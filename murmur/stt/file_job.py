"""Background file transcription on the resident engine."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from murmur.stt.engine import WhisperEngine
from murmur.stt.file import decode_file, duration_s

log = logging.getLogger(__name__)


class FileTranscriber(QThread):
    """Decode a file, transcribe it, and report progress per segment.

    Reuses the app's WhisperEngine; the model is never loaded twice.
    """

    progress = Signal(int, str)      # percent, latest segment text
    done = Signal(dict)              # text, source_name, duration_s, transcribe_ms
    failed = Signal(str)

    def __init__(self, engine: WhisperEngine, path: Path):
        super().__init__()
        self.engine = engine
        self.path = Path(path)

    def run(self) -> None:
        try:
            audio = decode_file(self.path)
            total = duration_s(audio)
            if total < 0.1:
                self.failed.emit(f"{self.path.name} contains no audio")
                return

            def on_seg(seg) -> None:
                pct = min(100, int(100 * seg.end / total)) if total > 0 else 0
                self.progress.emit(pct, seg.text)

            t0 = time.perf_counter()
            text = self.engine.transcribe(audio, on_segment=on_seg)
            ms = int((time.perf_counter() - t0) * 1000)
            self.done.emit({
                "text": text,
                "source_name": self.path.name,
                "duration_s": round(total, 2),
                "transcribe_ms": ms,
            })
        except FileNotFoundError as e:
            self.failed.emit(str(e))
        except Exception as e:
            log.exception("file transcription failed: %s", self.path)
            self.failed.emit(f"could not transcribe {self.path.name}: {e}")
