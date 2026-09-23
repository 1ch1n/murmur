"""The capsule widget: one custom-painted, always-on-top, never-focused
surface. A lamp, a voice line, a readout. Drag to move, click to open the
panel, right-click for a menu."""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, replace
from typing import List, Optional

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from murmur.overlay.draw import dpx, fill_rect, font, hairline, mix, snap, snap_c, stroke_rect, with_alpha
from murmur.overlay.pill.bezel import BezelCache, capsule_path
from murmur.overlay.pill.motion import Animator, Blend, Lerp
from murmur.overlay.pill.panel import PillPanel, get_foreground_hwnd, restore_foreground
from murmur.overlay.pill.prefs import PillPrefs
from murmur.overlay.pill.state import PillState
from murmur.overlay.pill.theme import Layout
from murmur.overlay.styles import C, build_qss
from murmur.overlay.waveform import Trace

log = logging.getLogger(__name__)


# --- position resolution (pure, unit-testable) ------------------------------

@dataclass(frozen=True)
class ScreenInfo:
    name: str
    avail: QRect


def clamp_into(rect: QRect, bounds: QRect) -> QRect:
    x = max(bounds.left(), min(rect.left(), bounds.right() - rect.width() + 1))
    y = max(bounds.top(), min(rect.top(), bounds.bottom() - rect.height() + 1))
    return QRect(x, y, rect.width(), rect.height())


def bottom_center(avail: QRect, size: QSize, bottom_margin: int) -> QPoint:
    x = avail.x() + (avail.width() - size.width()) // 2
    y = avail.y() + avail.height() - size.height() - bottom_margin
    return clamp_into(QRect(QPoint(x, y), size), avail).topLeft()


def resolve_position(prefs: PillPrefs, screens: List[ScreenInfo], primary: ScreenInfo, size: QSize) -> QPoint:
    """Saved screen by name, else any screen containing the saved point,
    else bottom-center of the primary screen. Always clamped on-screen."""
    if prefs.x is not None and prefs.y is not None:
        pt = QPoint(int(prefs.x), int(prefs.y))
        target = next((s for s in screens if s.name == prefs.screen), None)
        if target is None:
            target = next((s for s in screens if s.avail.contains(pt)), None)
        if target is not None:
            return clamp_into(QRect(pt, size), target.avail).topLeft()
    return bottom_center(primary.avail, size, prefs.bottom_margin)


def _live_screens() -> tuple[List[ScreenInfo], ScreenInfo]:
    screens = [ScreenInfo(s.name(), s.availableGeometry()) for s in QGuiApplication.screens()]
    prim = QGuiApplication.primaryScreen()
    primary = ScreenInfo(prim.name(), prim.availableGeometry()) if prim else (screens[0] if screens else ScreenInfo("", QRect(0, 0, 1920, 1080)))
    if not screens:
        screens = [primary]
    return screens, primary


# --- state visuals ------------------------------------------------------------

def line_color(state: PillState) -> QColor:
    return {
        PillState.IDLE: with_alpha(C.TEXT_WARM, 70),
        PillState.LISTENING: with_alpha(C.TEXT_WARM, 235),
        PillState.PROCESSING: with_alpha(C.TEXT_WARM, 70),
        PillState.DONE: with_alpha(C.PURPLE, 255),
        PillState.ERROR: with_alpha(C.RED, 255),
    }.get(state, with_alpha(C.TEXT_WARM, 70))


def lamp_color(state: PillState) -> QColor:
    if state in (PillState.LISTENING, PillState.ERROR):
        return QColor(C.RED)
    return QColor(C.PURPLE)


def fmt_timer(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


# --- the widget ---------------------------------------------------------------

class Pill(QWidget):
    history_requested = Signal()
    settings_requested = Signal()
    transcribe_file_requested = Signal()
    read_aloud_requested = Signal()
    quit_requested = Signal()
    copy_requested = Signal(str)
    waveform_toggled = Signal(bool)
    position_changed = Signal(int, int, str)

    SAVE_DEBOUNCE_MS = 400

    def __init__(self, prefs: Optional[PillPrefs] = None):
        super().__init__(None)
        self.prefs = prefs or PillPrefs()
        self.state = PillState.HIDDEN
        self._prev_state = PillState.HIDDEN
        self._hint = ""
        self._readout_override: Optional[str] = None
        self._file_pct: Optional[int] = None
        self._proc_t = 0.0
        self._rec_started = 0.0
        self._last_text = ""
        self._last_meta = ""

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        w, h = self.prefs.size
        self.setFixedSize(w, h)
        self.setWindowOpacity(self.prefs.opacity)

        self._layout_cache: Optional[Layout] = None
        self._bezel = BezelCache()
        self.trace = Trace(n_points=72)
        self._blend = Blend(0.26)
        self._hover = Lerp(0.0, speed=9.0)
        self._anim = Animator(self._tick, self._busy, self)

        # drag / click
        self._press: Optional[QPoint] = None
        self._grab = QPoint()
        self._dragging = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._emit_position)

        self._panel: Optional[PillPanel] = None
        self._override_timer = QTimer(self)
        self._override_timer.setSingleShot(True)
        self._override_timer.timeout.connect(self._clear_override)

        self.move(self._resolve())
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(lambda _s: self._ensure_on_screen())
            app.screenRemoved.connect(lambda _s: self._ensure_on_screen())
            app.primaryScreenChanged.connect(lambda _s: self._ensure_on_screen())

    # Back-compat for callers that still refer to the bars.
    @property
    def waveform(self) -> Trace:
        return self.trace

    # --- public API used by app.py -----------------------------------------

    def set_state(self, state: PillState) -> None:
        if state == PillState.HIDDEN:
            self.collapse_panel(restore_focus=False)
            self.hide()
            return
        if state != self.state:
            self._prev_state = self.state if self.state != PillState.HIDDEN else state
            self.state = state
            self._blend.restart()
            if state == PillState.LISTENING:
                self._rec_started = time.monotonic()
            else:
                self.trace.clear()
            if state == PillState.PROCESSING:
                self._proc_t = 0.0
            else:
                self._file_pct = None
        if not self.isVisible():
            self._ensure_on_screen()
            self.show()
        self._anim.wake()
        self.update()

    def push_samples(self, samples: np.ndarray) -> None:
        if self.state == PillState.LISTENING:
            self.trace.push(samples)
            self._anim.wake()

    def set_caption(self, text: str) -> None:
        """The hotkey hint shown on hover and in the tooltip."""
        self._hint = text
        self.setToolTip(f"Hold {text} to dictate. Click for menu. Drag to move." if text else "")
        self.update()

    def set_readout(self, text: Optional[str], ms: int = 0) -> None:
        """Temporarily replace the readout (COPIED, BUSY, 12 words...)."""
        self._readout_override = text
        self._override_timer.stop()
        if text and ms > 0:
            self._override_timer.start(ms)
        self.update()

    def _clear_override(self) -> None:
        self._readout_override = None
        self.update()

    def set_progress(self, pct: Optional[int], tail: str = "") -> None:
        self._file_pct = pct
        if self._panel is not None:
            self._panel.set_progress(pct, tail)
        self._anim.wake()
        self.update()

    def set_last_transcript(self, text: str, meta: str = "") -> None:
        self._last_text = text
        self._last_meta = meta
        if self._panel is not None:
            self._panel.set_transcript(text, meta)

    def apply_prefs(self, prefs: PillPrefs) -> None:
        old = self.prefs
        self.prefs = prefs
        if prefs.size != old.size:
            w, h = prefs.size
            self.setFixedSize(w, h)
            self._layout_cache = None
        self.setWindowOpacity(prefs.opacity)
        self._bezel.invalidate()
        self._ensure_on_screen()
        self.update()

    def reset_position(self) -> None:
        self.prefs = replace(self.prefs, x=None, y=None, screen="")
        self.collapse_panel(restore_focus=False)
        self.move(self._resolve())

    def collapse_panel(self, restore_focus: bool = False) -> None:
        if self._panel is not None and self._panel.isVisible():
            self._panel.collapse(restore_focus=restore_focus)

    def panel_open(self) -> bool:
        return self._panel is not None and self._panel.is_open()

    def toggle_panel(self) -> None:
        if self.panel_open():
            self.collapse_panel(restore_focus=True)
            return
        panel = self._ensure_panel()
        screen = self.screen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        L = self._layout()
        cap = L.capsule.toRect().translated(self.pos())
        panel.open_at(cap, avail, L.k)

    # --- internals -----------------------------------------------------------

    def _ensure_panel(self) -> PillPanel:
        if self._panel is None:
            panel = PillPanel(k=self._layout().k)
            panel.history_requested.connect(self.history_requested)
            panel.settings_requested.connect(self.settings_requested)
            panel.transcribe_file_requested.connect(self.transcribe_file_requested)
            panel.read_aloud_requested.connect(self.read_aloud_requested)
            panel.quit_requested.connect(self.quit_requested)
            panel.copy_requested.connect(self.copy_requested)
            panel.set_transcript(self._last_text, self._last_meta)
            panel.set_progress(self._file_pct)
            self._panel = panel
        return self._panel

    def _layout(self) -> Layout:
        dpr = self.devicePixelRatioF()
        L = self._layout_cache
        if L is None or L.dpr != dpr or L.w != self.width() or L.h != self.height():
            L = Layout.compute(self.width(), self.height(), dpr)
            self._layout_cache = L
        return L

    def _resolve(self) -> QPoint:
        screens, primary = _live_screens()
        return resolve_position(self.prefs, screens, primary, self.size())

    def _ensure_on_screen(self) -> None:
        self.move(self._resolve())

    def _current_screen_name(self) -> str:
        s = self.screen()
        return s.name() if s else ""

    def _emit_position(self) -> None:
        pos = self.pos()
        name = self._current_screen_name()
        self.prefs = replace(self.prefs, x=pos.x(), y=pos.y(), screen=name)
        self.position_changed.emit(pos.x(), pos.y(), name)

    # --- animation ----------------------------------------------------------

    def _busy(self) -> bool:
        return (
            not self._blend.done()
            or not self._hover.done()
            or self.trace.active()
            or self.state in (PillState.LISTENING, PillState.PROCESSING)
        )

    def _tick(self, dt: float) -> None:
        self._blend.step(dt)
        self._hover.step(dt)
        self.trace.tick(dt)
        if self.state == PillState.PROCESSING:
            self._proc_t += dt
        self.update()

    # --- events --------------------------------------------------------------

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.DevicePixelRatioChange:
            self._layout_cache = None
            self._bezel.invalidate()
        return super().event(e)

    def enterEvent(self, e) -> None:
        self._hover.target = 1.0
        self._anim.wake()

    def leaveEvent(self, e) -> None:
        self._hover.target = 0.0
        self._anim.wake()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._grab = self._press - self.pos()
            self._dragging = False

    def mouseMoveEvent(self, e) -> None:
        if self._press is None:
            return
        g = e.globalPosition().toPoint()
        if not self._dragging:
            if (g - self._press).manhattanLength() < QApplication.startDragDistance():
                return
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.collapse_panel(restore_focus=False)
        screen = QGuiApplication.screenAt(g) or self.screen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        self.move(clamp_into(QRect(g - self._grab, self.size()), avail).topLeft())

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        was_drag = self._dragging
        self._press = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if was_drag:
            self._save_timer.start()
        else:
            self.toggle_panel()

    def contextMenuEvent(self, e) -> None:
        self.collapse_panel(restore_focus=False)
        menu = QMenu()
        menu.setStyleSheet(build_qss("menu"))
        actions = [
            ("Copy last transcript", lambda: self.copy_requested.emit(self._last_text)),
            ("History", self.history_requested.emit),
            ("Read aloud…", self.read_aloud_requested.emit),
            ("Transcribe file…", self.transcribe_file_requested.emit),
            ("Settings…", self.settings_requested.emit),
            None,
            ("Quit", self.quit_requested.emit),
        ]
        for item in actions:
            if item is None:
                menu.addSeparator()
                continue
            label, fn = item
            act = QAction(label, menu)
            if label == "Copy last transcript" and not self._last_text:
                act.setEnabled(False)
            act.triggered.connect(fn)
            menu.addAction(act)
        prev = get_foreground_hwnd()
        menu.exec(e.globalPos())
        restore_foreground(prev)

    # --- painting ------------------------------------------------------------

    def _readout_text(self) -> tuple[str, QColor]:
        if self._readout_override:
            return self._readout_override, QColor(C.PURPLE)
        st = self.state
        if st == PillState.LISTENING:
            return fmt_timer(time.monotonic() - self._rec_started), with_alpha(C.TEXT_WARM, 220)
        if st == PillState.PROCESSING:
            if self._file_pct is not None:
                return f"{self._file_pct:d}%", with_alpha(C.TEXT_WARM, 220)
            return "", QColor(C.TEXT_DIM)
        if st == PillState.ERROR:
            return "Error", QColor(C.RED)
        if st == PillState.IDLE and self._hint:
            return self._hint, with_alpha(C.TEXT_DIM, int(255 * self._hover.value))
        return "", QColor(C.TEXT_DIM)

    def paintEvent(self, _e) -> None:
        L = self._layout()
        dpr = L.dpr
        k = L.k
        t = self._blend.value
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        p.drawPixmap(0, 0, self._bezel.ensure(L))

        # hover: rim brightens
        hv = self._hover.value
        if hv > 0.01:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(hairline(with_alpha(C.TEXT_WARM, int(28 + 50 * hv)), dpr, 1))
            p.drawRoundedRect(
                stroke_rect(L.capsule.x(), L.capsule.y(), L.capsule.width(), L.capsule.height(), dpr, 1),
                L.radius, L.radius,
            )

        # lamp
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        lamp = mix(lamp_color(self._prev_state), lamp_color(self.state), t)
        p.setBrush(lamp)
        p.drawEllipse(L.lamp, L.lamp_r, L.lamp_r)

        # voice line
        color = mix(line_color(self._prev_state), line_color(self.state), t)
        show_idle_line = self.prefs.show_waveform or self.state != PillState.IDLE
        alive = self.state == PillState.LISTENING
        if show_idle_line or self.trace.active():
            self.trace.paint(p, L.trace, dpr, color, alive)

        if self.state == PillState.PROCESSING:
            self._paint_processing(p, L)

        # readout, right-aligned, tabular mono so nothing jitters
        text, tcolor = self._readout_text()
        if text:
            f = font("mono", 11.5 * k)
            fm = QFontMetricsF(f)
            p.setFont(f)
            p.setPen(tcolor)
            tw = fm.horizontalAdvance(text)
            y = L.readout.center().y() + fm.capHeight() / 2
            p.drawText(QPointF(L.readout.right() - tw, y), text)
        p.end()

    def _paint_processing(self, p: QPainter, L: Layout) -> None:
        """Processing: a soft highlight travelling along the flat line.
        File job: the line fills purple from the left."""
        dpr = L.dpr
        r = L.trace
        y = snap_c(r.center().y(), dpr)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self._file_pct is not None:
            w = r.width() * max(0, min(100, self._file_pct)) / 100.0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C.PURPLE))
            p.drawRect(fill_rect(r.left(), y - dpx(1, dpr), w, dpx(2, dpr), dpr))
        else:
            period = 1.4
            u = (self._proc_t % period) / period
            span = r.width() * 0.32
            cx = r.left() - span / 2 + u * (r.width() + span)
            grad = QLinearGradient(QPointF(cx - span / 2, 0), QPointF(cx + span / 2, 0))
            grad.setColorAt(0.0, with_alpha(C.PURPLE, 0))
            grad.setColorAt(0.5, with_alpha(C.PURPLE, 255))
            grad.setColorAt(1.0, with_alpha(C.PURPLE, 0))
            pen = QPen(grad, dpx(2, dpr))
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            x0 = max(r.left(), cx - span / 2)
            x1 = min(r.right(), cx + span / 2)
            if x1 > x0:
                p.drawLine(QPointF(x0, y), QPointF(x1, y))
        p.restore()
