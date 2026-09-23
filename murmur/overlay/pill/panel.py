"""The panel that slides out of the capsule on click.

A separate frameless tool window that does take focus (the user just
clicked us, so Windows grants it). That gives Esc, keyboard rows and
click-outside for free. The foreground window from before it opened is
restored on Esc or a second click on the capsule, never when the user
chose another window themselves.
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from murmur.overlay.draw import dpx, fill_rect, font, hairline, snap_c, with_alpha
from murmur.overlay.pill.bezel import paint_card
from murmur.overlay.pill.motion import Animator, Blend, Lerp
from murmur.overlay.styles import C

log = logging.getLogger(__name__)


# --- foreground window helpers (Windows only, best effort) -----------------

def get_foreground_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    try:
        import win32gui

        return int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        return 0


def restore_foreground(hwnd: int) -> None:
    if not hwnd or sys.platform != "win32":
        return
    try:
        import win32gui

        if win32gui.IsWindow(hwnd):
            win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass  # Windows refuses when the user has since moved on. Fine.


# --- rows ------------------------------------------------------------------

@dataclass
class Row:
    key: str
    label: str
    rect: QRectF = field(default_factory=QRectF)
    enabled: bool = True


def wrap_lines(text: str, fm: QFontMetricsF, width: float, max_lines: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    cur = ""
    i = 0
    while i < len(words):
        w = words[i]
        trial = (cur + " " + w).strip()
        if fm.horizontalAdvance(trial) <= width or not cur:
            cur = trial
            i += 1
        else:
            lines.append(cur)
            cur = ""
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines and cur:
        lines.append(cur)
    if i < len(words) or (len(lines) == max_lines and cur):
        rest = " ".join(words[i:]) if i < len(words) else ""
        lines[-1] = fm.elidedText((lines[-1] + " " + rest).strip(), Qt.TextElideMode.ElideRight, width)
    return lines


class PillPanel(QWidget):
    history_requested = Signal()
    settings_requested = Signal()
    transcribe_file_requested = Signal()
    read_aloud_requested = Signal()
    quit_requested = Signal()
    copy_requested = Signal(str)

    GAP = 8
    MARGIN = 10   # transparent margin for the shadow, in k units

    def __init__(self, k: float = 1.0):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self.k = k
        self._above = True
        self._anchor = QRect()
        self._opening = True
        self._blend = Blend(0.18)
        self._hover_row: Optional[int] = None
        self._hover = Lerp(0.0, speed=14.0)
        self._prev_hwnd = 0
        self._closed_at = 0.0
        self._pressed_row: Optional[int] = None

        self._transcript = ""
        self._transcript_meta = ""
        self._progress: Optional[tuple[int, str]] = None
        self._flash_until = 0.0
        self._flash_key = ""
        self._content_h = 0.0

        self.rows: List[Row] = []
        self._rebuild_rows()

        self._anim = Animator(self._tick, self._busy, self)

    # --- content ---------------------------------------------------------

    def set_transcript(self, text: str, meta: str) -> None:
        self._transcript = (text or "").strip()
        self._transcript_meta = meta or ""
        self._rebuild_rows()
        self.update()

    def set_progress(self, pct: Optional[int], tail: str = "") -> None:
        self._progress = None if pct is None else (pct, tail or "")
        self._rebuild_rows()
        self.update()

    def _rebuild_rows(self) -> None:
        rows = []
        if self._transcript:
            rows.append(Row("copy", "Copy last transcript"))
        rows += [
            Row("history", "History"),
            Row("read", "Read aloud…"),
            Row("file", "Transcribe file…", enabled=self._progress is None),
            Row("settings", "Settings"),
            Row("quit", "Quit"),
        ]
        self.rows = rows
        self._layout_rows()

    # --- geometry --------------------------------------------------------

    def _m(self) -> dict:
        k = self.k
        return {
            "margin": self.MARGIN * k,
            "pad": 14 * k,
            "header_h": 16 * k,
            "line_h": 17 * k,
            "row_h": 30 * k,
            "div": 12 * k,
            "prog_h": 34 * k,
        }

    def _card(self) -> QRectF:
        m = self._m()["margin"]
        return QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)

    def _preview_lines(self, width: float) -> List[str]:
        if not self._transcript:
            return []
        fm = QFontMetricsF(font("ui", 12.5 * self.k))
        return wrap_lines(self._transcript, fm, width, 3)

    def _layout_rows(self) -> None:
        m = self._m()
        card_w = (self.width() or 240) - 2 * m["margin"]
        inner_w = card_w - 2 * m["pad"]
        y = m["margin"] + m["pad"]
        if self._transcript:
            y += m["header_h"] + 4 * self.k
            y += len(self._preview_lines(inner_w)) * m["line_h"] + 6 * self.k
        if self._progress is not None:
            y += m["prog_h"]
        if self._transcript or self._progress is not None:
            y += m["div"]
        for r in self.rows:
            r.rect = QRectF(m["margin"] + m["pad"] - 6 * self.k, y, inner_w + 12 * self.k, m["row_h"])
            y += m["row_h"]
        self._content_h = y + m["pad"] - 4 * self.k + m["margin"]

    def preferred_height(self) -> int:
        self._layout_rows()
        return int(round(self._content_h))

    # --- open / close ----------------------------------------------------

    def is_open(self) -> bool:
        return self.isVisible() and self._opening

    def open_at(self, anchor: QRect, avail: QRect, k: float) -> None:
        """`anchor` is the capsule rect in global coords."""
        if time.monotonic() - self._closed_at < 0.2:
            return
        self.k = k
        self._anchor = QRect(anchor)
        self._rebuild_rows()
        m = self._m()
        margin = int(round(m["margin"]))
        w = anchor.width() + 2 * margin
        self.resize(w, 10)
        h = self.preferred_height()
        gap = int(self.GAP * k)
        card_h = h - 2 * margin
        if anchor.top() - card_h - gap >= avail.top():
            self._above = True
            y = anchor.top() - gap - card_h - margin
        else:
            self._above = False
            y = anchor.bottom() + 1 + gap - margin
        x = anchor.left() - margin
        x = max(avail.left() - margin, min(x, avail.right() - w + 1 + margin))
        self.setGeometry(x, y, w, h)
        self._layout_rows()

        self._prev_hwnd = get_foreground_hwnd()
        self._opening = True
        self._blend = Blend(0.18)
        self._blend.restart()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self._anim.wake()

    def collapse(self, restore_focus: bool) -> None:
        if not self.isVisible() or not self._opening:
            return
        self._opening = False
        self._blend = Blend(0.12, ease_in=True)
        self._blend.restart()
        self._closed_at = time.monotonic()
        hwnd, self._prev_hwnd = self._prev_hwnd, 0
        self._anim.wake()
        if restore_focus:
            restore_foreground(hwnd)

    # --- animation -------------------------------------------------------

    def _busy(self) -> bool:
        return (not self._blend.done()) or (not self._hover.done()) or self._flash_until > time.monotonic()

    def _tick(self, dt: float) -> None:
        self._blend.step(dt)
        self._hover.step(dt)
        if self._opening:
            self.setWindowOpacity(self._blend.value)
        else:
            self.setWindowOpacity(1.0 - self._blend.value)
            if self._blend.done():
                self.hide()
                self._hover_row = None
        self.update()

    # --- events ----------------------------------------------------------

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.WindowDeactivate and self.isVisible() and self._opening:
            if QApplication.activeModalWidget() is None:
                self.collapse(restore_focus=False)
        return super().event(e)

    def keyPressEvent(self, e) -> None:
        key = e.key()
        if key == Qt.Key.Key_Escape:
            self.collapse(restore_focus=True)
            return
        enabled = [i for i, r in enumerate(self.rows) if r.enabled]
        if not enabled:
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Tab):
            nxt = enabled[0] if self._hover_row not in enabled else enabled[(enabled.index(self._hover_row) + 1) % len(enabled)]
            self._set_hover(nxt)
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Backtab):
            prv = enabled[-1] if self._hover_row not in enabled else enabled[(enabled.index(self._hover_row) - 1) % len(enabled)]
            self._set_hover(prv)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self._hover_row is not None:
                self._activate(self._hover_row)
            return
        super().keyPressEvent(e)

    def _row_at(self, pos: QPointF) -> Optional[int]:
        for i, r in enumerate(self.rows):
            if r.rect.contains(pos):
                return i
        return None

    def _set_hover(self, idx: Optional[int]) -> None:
        if idx != self._hover_row:
            self._hover_row = idx
            self._hover.value = 0.0
            self._hover.target = 1.0 if idx is not None else 0.0
            self.setCursor(Qt.CursorShape.PointingHandCursor if idx is not None and self.rows[idx].enabled
                           else Qt.CursorShape.ArrowCursor)
            self._anim.wake()
            self.update()

    def mouseMoveEvent(self, e) -> None:
        self._set_hover(self._row_at(e.position()))

    def leaveEvent(self, e) -> None:
        self._set_hover(None)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed_row = self._row_at(e.position())

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        idx = self._row_at(e.position())
        if idx is not None and idx == self._pressed_row:
            self._activate(idx)
        self._pressed_row = None

    def _activate(self, idx: int) -> None:
        row = self.rows[idx]
        if not row.enabled:
            return
        key = row.key
        if key == "copy":
            self.copy_requested.emit(self._transcript)
            self._flash_key = "copy"
            self._flash_until = time.monotonic() + 1.2
            self._anim.wake()
            QTimer.singleShot(1250, self.update)
            return
        if key == "history":
            self.collapse(restore_focus=False)
            self.history_requested.emit()
        elif key == "settings":
            self.collapse(restore_focus=False)
            self.settings_requested.emit()
        elif key == "file":
            self.collapse(restore_focus=False)
            self.transcribe_file_requested.emit()
        elif key == "read":
            self.collapse(restore_focus=False)
            self.read_aloud_requested.emit()
        elif key == "quit":
            self.quit_requested.emit()

    # --- painting --------------------------------------------------------

    def paintEvent(self, _e) -> None:
        dpr = self.devicePixelRatioF()
        k = self.k
        m = self._m()
        card = self._card()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # slide out of the capsule edge
        t = self._blend.value if self._opening else 1.0 - self._blend.value
        slide = (1.0 - t) * 8 * k
        p.translate(0, slide if self._above else -slide)

        paint_card(p, card, 12 * k, dpr, k, rim_alpha=24)

        pad = m["pad"]
        x0 = card.left() + pad
        inner_w = card.width() - 2 * pad
        y = card.top() + pad
        one = dpx(1, dpr)

        if self._transcript:
            f_h = font("ui_medium", 11 * k)
            p.setFont(f_h)
            p.setPen(QColor(C.TEXT_DIM))
            p.drawText(QPointF(x0, y + 10 * k), "Last transcript")
            if self._transcript_meta:
                f_m = font("mono", 10 * k)
                fm_m = QFontMetricsF(f_m)
                mw = fm_m.horizontalAdvance(self._transcript_meta)
                p.setFont(f_m)
                p.drawText(QPointF(card.right() - pad - mw, y + 10 * k), self._transcript_meta)
            y += m["header_h"] + 4 * k
            f_p = font("ui", 12.5 * k)
            p.setFont(f_p)
            p.setPen(with_alpha(C.TEXT_WARM, 225))
            for line in self._preview_lines(inner_w):
                p.drawText(QPointF(x0, y + 12 * k), line)
                y += m["line_h"]
            y += 6 * k

        if self._progress is not None:
            pct, tail = self._progress
            f_pr = font("mono", 10.5 * k)
            p.setFont(f_pr)
            p.setPen(with_alpha(C.TEXT_WARM, 220))
            p.drawText(QPointF(x0, y + 11 * k), f"Transcribing  {pct:d}%")
            bar_y = y + 17 * k
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C.BORDER_HI))
            p.drawRect(fill_rect(x0, bar_y, inner_w, 2 * one, dpr))
            p.setBrush(QColor(C.PURPLE))
            p.drawRect(fill_rect(x0, bar_y, inner_w * pct / 100.0, 2 * one, dpr))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if tail:
                f_t = font("ui", 11 * k)
                fm_t = QFontMetricsF(f_t)
                p.setFont(f_t)
                p.setPen(QColor(C.TEXT_DIM))
                p.drawText(QPointF(x0, y + 30 * k),
                           fm_t.elidedText(tail, Qt.TextElideMode.ElideLeft, inner_w))
            y += m["prog_h"]

        if self._transcript or self._progress is not None:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(hairline(with_alpha(C.TEXT_WARM, 18), dpr))
            yy = snap_c(y + m["div"] / 2, dpr)
            p.drawLine(QPointF(x0, yy), QPointF(card.right() - pad, yy))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        f_r = font("ui_medium", 13 * k)
        p.setFont(f_r)
        now = time.monotonic()
        for i, r in enumerate(self.rows):
            hovered = (i == self._hover_row)
            if hovered and r.enabled:
                a = self._hover.value
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(with_alpha(C.TEXT_WARM, int(16 * a)))
                path = QPainterPath()
                path.addRoundedRect(r.rect.adjusted(0, 2 * k, 0, -2 * k), 7 * k, 7 * k)
                p.drawPath(path)
            label = r.label
            color = with_alpha(C.TEXT_WARM, 255 if hovered and r.enabled else 205)
            if not r.enabled:
                color = QColor(C.TEXT_DIM)
            if r.key == self._flash_key and now < self._flash_until:
                label = "Copied"
                color = QColor(C.PURPLE)
            p.setPen(color)
            p.drawText(QPointF(x0, r.rect.center().y() + 4.5 * k), label)
        p.end()
