from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from murmur.overlay.styles import C


class Waveform(QWidget):
    """Compact VU-meter style bars for the pill. Forked from VoxMemo."""

    def __init__(self, num_bars: int = 18):
        super().__init__()
        self.num_bars = num_bars
        self.levels = np.zeros(num_bars)
        self.peaks = np.zeros(num_bars)
        self.is_active = False

        # Smoothing
        self.attack = 0.5
        self.decay = 0.82
        self.peak_decay = 0.94

        # Idle phase animation
        self.phase = 0.0
        self.bar_color = QColor(C.AMBER)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)  # ~30fps

    def set_samples(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        chunk_size = max(1, samples.size // self.num_bars)
        for i in range(self.num_bars):
            start = i * chunk_size
            end = min(start + chunk_size, samples.size)
            if start >= samples.size:
                break
            chunk = samples[start:end]
            rms = float(np.sqrt(np.mean(chunk * chunk)))
            if rms > self.levels[i]:
                self.levels[i] = self.levels[i] * (1 - self.attack) + rms * self.attack
            else:
                self.levels[i] *= self.decay
            if self.levels[i] > self.peaks[i]:
                self.peaks[i] = self.levels[i]
            else:
                self.peaks[i] *= self.peak_decay

    def set_active(self, active: bool, color_hex: str | None = None) -> None:
        self.is_active = active
        if color_hex:
            self.bar_color = QColor(color_hex)
        if not active:
            self.levels[:] = 0
            self.peaks[:] = 0

    def clear(self) -> None:
        self.levels[:] = 0
        self.peaks[:] = 0
        self.update()

    def _tick(self) -> None:
        self.phase += 0.07
        if not self.is_active:
            self.levels *= 0.88
            self.peaks *= 0.92
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cy = h // 2

        margin = 4
        total_w = w - margin * 2
        gap = 2
        bar_w = max(2, (total_w - gap * (self.num_bars - 1)) // self.num_bars)

        peak_color = QColor(self.bar_color)
        peak_color.setAlpha(110)

        max_level = max(float(np.max(self.levels)), 0.001)

        for i in range(self.num_bars):
            x = margin + i * (bar_w + gap)

            if self.is_active or np.max(self.levels) > 0.001:
                level = self.levels[i] / max_level
                bar_h = int(level * (h // 2 - 2))
                bar_h = max(1, min(bar_h, h // 2 - 1))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(self.bar_color))
                p.drawRoundedRect(x, cy - bar_h, bar_w, bar_h * 2, 1, 1)
                if self.peaks[i] > 0.01:
                    peak_h = int((self.peaks[i] / max_level) * (h // 2 - 2))
                    peak_h = max(1, min(peak_h, h // 2 - 1))
                    p.setBrush(QBrush(peak_color))
                    p.drawRect(x, cy - peak_h - 1, bar_w, 1)
                    p.drawRect(x, cy + peak_h, bar_w, 1)
            else:
                # idle shimmer
                wave = np.sin(self.phase + i * 0.4) * 0.3 + 0.4
                bar_h = max(1, int(wave * 3))
                idle = QColor(C.TEXT_DIM)
                idle.setAlpha(120)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(idle))
                p.drawRoundedRect(x, cy - bar_h, bar_w, bar_h * 2, 1, 1)
