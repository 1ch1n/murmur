from __future__ import annotations

import logging
from threading import Thread

import numpy as np

log = logging.getLogger(__name__)


_SR = 44100
_DURATION = 0.07  # 70ms blips, short enough not to delay paste


def _tone(freq: float, duration: float = _DURATION, gain: float = 0.18) -> np.ndarray:
    n = int(_SR * duration)
    t = np.arange(n, dtype=np.float32) / _SR
    wave = np.sin(2 * np.pi * freq * t).astype(np.float32)
    # soft envelope so it doesn't click
    attack = int(n * 0.1)
    release = int(n * 0.5)
    env = np.ones(n, dtype=np.float32)
    if attack > 0:
        env[:attack] = np.linspace(0.0, 1.0, attack, dtype=np.float32)
    if release > 0:
        env[-release:] = np.linspace(1.0, 0.0, release, dtype=np.float32)
    return (wave * env * gain).astype(np.float32)


_START_BLIP = _tone(880.0)   # A5, listening
_STOP_BLIP = _tone(523.25)   # C5, processing
_DONE_BLIP = np.concatenate([_tone(523.25, 0.04), _tone(880.0, 0.05)])
_ERROR_BLIP = _tone(220.0, 0.18)


def _play(buf: np.ndarray) -> None:
    """Play `buf` async in a daemon thread so we never block paste."""
    def _run() -> None:
        try:
            import sounddevice as sd

            sd.play(buf, samplerate=_SR, blocking=False)
        except Exception as e:
            log.debug("sound playback failed: %s", e)

    Thread(target=_run, daemon=True).start()


def play_start() -> None:
    _play(_START_BLIP)


def play_stop() -> None:
    _play(_STOP_BLIP)


def play_done() -> None:
    _play(_DONE_BLIP)


def play_error() -> None:
    _play(_ERROR_BLIP)
