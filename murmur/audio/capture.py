from __future__ import annotations

import logging
from typing import List

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QThread, Signal

from murmur.config import BLOCK_SIZE, CHANNELS, SAMPLE_RATE

log = logging.getLogger(__name__)


class AudioCapture(QThread):
    """Background thread that records audio into an in-memory buffer.

    Emits `samples` for live waveform updates and `done(np.ndarray)` when stopped.
    """

    samples = Signal(np.ndarray)
    done = Signal(np.ndarray)
    error = Signal(str)

    def __init__(self, device: int | None = None):
        super().__init__()
        self.device = device
        self._running = False
        self._chunks: List[np.ndarray] = []

    def run(self) -> None:
        self._running = True
        self._chunks = []
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                device=self.device,
                blocksize=BLOCK_SIZE,
                dtype="float32",
            ) as stream:
                while self._running:
                    data, _ = stream.read(BLOCK_SIZE)
                    if data.size == 0:
                        continue
                    chunk = data.copy().flatten()
                    self._chunks.append(chunk)
                    self.samples.emit(chunk)
        except Exception as e:
            log.exception("audio capture failed")
            self.error.emit(str(e))
            return

        if self._chunks:
            audio = np.concatenate(self._chunks).astype(np.float32, copy=False)
        else:
            audio = np.zeros(0, dtype=np.float32)
        self.done.emit(audio)

    def stop(self) -> None:
        self._running = False
