"""One clock for the whole widget.

A single 16 ms QTimer drives the needle spring, the waveform interpolation,
the state crossfade and the hover lerp. It stops itself when everything has
settled, so an idle widget costs nothing, and restarts on any input.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QElapsedTimer, QObject, Qt, QTimer

from murmur.overlay.draw import ease_in_cubic, ease_out_cubic

FRAME_MS = 16


class Blend:
    """A 0..1 value that eases toward 1 over `duration_s` after restart()."""

    def __init__(self, duration_s: float, ease_in: bool = False):
        self.duration = duration_s
        self.t = 1.0
        self._ease_in = ease_in

    def restart(self) -> None:
        self.t = 0.0

    def finish(self) -> None:
        self.t = 1.0

    def step(self, dt: float) -> None:
        if self.t < 1.0:
            self.t = min(1.0, self.t + dt / self.duration)

    @property
    def value(self) -> float:
        return ease_in_cubic(self.t) if self._ease_in else ease_out_cubic(self.t)

    def done(self) -> bool:
        return self.t >= 1.0


class Lerp:
    """A value that slides toward a target at a fixed rate (for hover)."""

    def __init__(self, value: float = 0.0, speed: float = 8.0):
        self.value = value
        self.target = value
        self.speed = speed

    def step(self, dt: float) -> None:
        d = self.target - self.value
        if abs(d) < 0.002:
            self.value = self.target
            return
        self.value += d * min(1.0, dt * self.speed)

    def done(self) -> bool:
        return self.value == self.target


class Animator(QObject):
    """Calls `on_tick(dt)` at ~60 Hz while `is_busy()` is True."""

    def __init__(self, on_tick: Callable[[float], None], is_busy: Callable[[], bool], parent=None):
        super().__init__(parent)
        self._on_tick = on_tick
        self._is_busy = is_busy
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()
        self._last_ns = 0

    def wake(self) -> None:
        if not self._timer.isActive():
            self._clock.restart()
            self._last_ns = 0
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def running(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        now = self._clock.nsecsElapsed()
        dt = (now - self._last_ns) / 1e9 if self._last_ns else FRAME_MS / 1000
        self._last_ns = now
        self._on_tick(dt)
        if not self._is_busy():
            self._timer.stop()
