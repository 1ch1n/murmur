from __future__ import annotations

from enum import Enum

import numpy as np
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property, QPoint
from PySide6.QtGui import QColor, QPainter, QPen, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from murmur.overlay.styles import C, PILL_STYLE
from murmur.overlay.waveform import Waveform


class PillState(str, Enum):
    HIDDEN = "hidden"
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


_STATE_COLOR = {
    PillState.IDLE: C.PURPLE_DIM,
    PillState.LISTENING: C.AMBER,
    PillState.PROCESSING: C.TEAL,
    PillState.DONE: C.PURPLE,
    PillState.ERROR: C.RED,
}

_STATE_LABEL = {
    PillState.IDLE: "READY",
    PillState.LISTENING: "REC",
    PillState.PROCESSING: "···",
    PillState.DONE: "✓",
    PillState.ERROR: "ERR",
}


class Pill(QWidget):
    """Frameless, always-on-top bottom-center dictation pill.

    Window flags follow the qt.io recommended pattern:
        Tool | FramelessWindowHint | WindowStaysOnTopHint
    Set in a single bitwise-OR'd call to avoid the well-known
    setWindowFlags-overwriting bug.
    """

    def __init__(self, width: int = 280, height: int = 56, bottom_margin: int = 80):
        super().__init__(None)
        self._pill_w = width
        self._pill_h = height
        self._bottom_margin = bottom_margin

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)

        self.setFixedSize(self._pill_w, self._pill_h)
        self.setStyleSheet(PILL_STYLE)

        self.state = PillState.IDLE

        # layout: [status label] [waveform]
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 8, 18, 8)
        root.setSpacing(12)

        self.status = QLabel(_STATE_LABEL[PillState.IDLE])
        self.status.setObjectName("status")
        self.status.setFixedWidth(36)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.status)

        self.waveform = Waveform(num_bars=18)
        root.addWidget(self.waveform, 1)

        self._anchor_bottom_center()

    def _anchor_bottom_center(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + geo.height() - self.height() - self._bottom_margin
        self.move(x, y)

    # --- public API -----------------------------------------------------

    def set_state(self, state: PillState) -> None:
        self.state = state
        if state == PillState.HIDDEN:
            self.hide()
            return

        self.status.setText(_STATE_LABEL.get(state, ""))
        color = _STATE_COLOR.get(state, C.TEXT_DIM)
        self.status.setStyleSheet(
            f"color: {color}; font-size: 10px; letter-spacing: 2px; font-weight: 600;"
        )

        active = state == PillState.LISTENING
        self.waveform.set_active(active, color_hex=color)

        if not self.isVisible():
            self._anchor_bottom_center()
            self.show()
        self.update()

    def push_samples(self, samples: np.ndarray) -> None:
        if self.state == PillState.LISTENING:
            self.waveform.set_samples(samples)

    # --- painting -------------------------------------------------------

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Pill background
        bg = QColor(C.BG)
        bg.setAlpha(235)
        p.setBrush(bg)

        # State-colored border
        border_color = QColor(_STATE_COLOR.get(self.state, C.BORDER))
        p.setPen(QPen(border_color, 1))
        p.drawRoundedRect(0, 0, self.width() - 1, self.height() - 1, 3, 3)
