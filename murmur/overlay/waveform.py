"""The voice line: a scrolling oscilloscope trace of the microphone.

Audio arrives in 100 ms blocks (10 Hz). Each block is reduced to a handful
of signed peaks which queue up; the widget ticks at ~60 Hz and scrolls the
line left at a constant rate, pulling one queued point per step, so the
trace moves continuously and shows the real shape of speech: silence is a
flat line, words are jagged bursts.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Deque, List

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from murmur.overlay.draw import dpx, hairline, snap_c, with_alpha
from murmur.overlay.styles import C

SAMPLE_RATE = 16000
POINTS_PER_SEC = 72.0        # scroll speed: one second of speech spans ~72 points
DB_FLOOR = -50.0
DB_CEIL = -6.0


def _db_to_unit(db: float) -> float:
    return max(0.0, min(1.0, (db - DB_FLOOR) / (DB_CEIL - DB_FLOOR)))


def rms_db(x: np.ndarray) -> float:
    if x.size == 0:
        return DB_FLOOR
    r = float(np.sqrt(np.mean(x.astype(np.float32) ** 2)))
    return 20 * math.log10(r + 1e-9)


class Trace:
    def __init__(self, n_points: int = 72):
        self.n = n_points
        self.buf = np.zeros(n_points, dtype=np.float32)   # newest at the end
        self.pending: Deque[float] = deque()
        self.scroll = 0.0
        self.level = 0.0
        self._quiet_s = 0.0

    # --- model -----------------------------------------------------------

    def push(self, samples: np.ndarray) -> None:
        x = np.asarray(samples, dtype=np.float32).ravel()
        if x.size == 0:
            return
        self.level = _db_to_unit(rms_db(x))
        n_new = max(1, int(round(x.size / SAMPLE_RATE * POINTS_PER_SEC)))
        for part in np.array_split(x, n_new):
            if part.size == 0:
                continue
            i = int(np.argmax(np.abs(part)))
            peak = float(part[i])
            # soft compression so quiet speech still reads, loud never clips
            self.pending.append(math.tanh(3.2 * peak))
        self._quiet_s = 0.0

    def clear(self) -> None:
        """Stop feeding; the line scrolls to flat on its own."""
        self.pending.clear()
        self.level = 0.0

    def reset(self) -> None:
        self.clear()
        self.buf[:] = 0.0
        self.scroll = 0.0

    def tick(self, dt: float) -> None:
        if dt <= 0:
            return
        # catch up if audio blocks arrived faster than we scrolled
        rate = POINTS_PER_SEC * (1.0 + min(2.0, len(self.pending) / 14.0))
        self.scroll += dt * rate
        while self.scroll >= 1.0:
            self.scroll -= 1.0
            v = self.pending.popleft() if self.pending else 0.0
            self.buf = np.roll(self.buf, -1)
            self.buf[-1] = v
        self._quiet_s += dt
        if self._quiet_s > 0.4:
            self.level = 0.0

    def active(self) -> bool:
        return bool(self.pending) or bool(np.abs(self.buf).max() > 0.004)

    # --- painter ---------------------------------------------------------

    def paint(self, p: QPainter, rect: QRectF, dpr: float, color: QColor, alive: bool) -> None:
        """Draw the line. When nothing is happening it is a single crisp
        hairline; while alive it is an antialiased polyline of the buffer."""
        p.save()
        cy = rect.center().y()
        if not alive and not self.active():
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            y = snap_c(cy, dpr)
            p.setPen(hairline(color, dpr, 1))
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.restore()
            return

        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        n = self.n
        spacing = rect.width() / (n - 1)
        amp = rect.height() / 2
        pts: List[QPointF] = []
        for i in range(n):
            x = rect.left() + (i - self.scroll) * spacing
            pts.append(QPointF(x, cy - float(self.buf[i]) * amp))
        # one extra point past the right edge keeps the scroll seamless
        pts.append(QPointF(rect.left() + (n - self.scroll) * spacing, cy))
        pen = QPen(color, dpx(max(1, int(1.25 * dpr)), dpr))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setClipRect(rect.adjusted(-dpx(1, dpr), -amp, dpx(1, dpr), amp))
        p.drawPolyline(pts)
        p.restore()


# Back-compat name used by the smoke test and older callers.
Waveform = Trace
