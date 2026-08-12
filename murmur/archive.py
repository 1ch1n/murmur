from __future__ import annotations

import json
import logging
import tempfile
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Iterable, List, Optional

from murmur.config import DATA_DIR, _ensure_dirs

log = logging.getLogger(__name__)

ARCHIVE_FILE = DATA_DIR / "archive.json"


@dataclass
class Transcript:
    id: str
    timestamp: str                          # ISO 8601
    text: str
    target_app: Optional[str] = None        # process name
    target_category: Optional[str] = None   # code / chat / mail / doc / terminal / browser / other
    raw_text: Optional[str] = None          # original Whisper output if cleanup was applied
    cleanup_provider: Optional[str] = None  # which provider produced text
    audio_duration_s: Optional[float] = None
    transcribe_ms: Optional[int] = None
    cleanup_ms: Optional[int] = None
    word_count: int = 0

    def __post_init__(self) -> None:
        if not self.word_count:
            self.word_count = len(self.text.split())


class Archive:
    """JSON-backed transcript store. Append-only by default; supports delete."""

    def __init__(self, path: Path = ARCHIVE_FILE):
        self.path = path
        self._lock = Lock()
        self._items: List[Transcript] = []
        self._load()

    def _load(self) -> None:
        _ensure_dirs()
        if not self.path.exists():
            self._items = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = [
                Transcript(**{k: v for k, v in t.items() if k in Transcript.__dataclass_fields__})
                for t in raw
            ]
        except Exception:
            log.exception("failed to load archive; starting empty")
            self._items = []

    def _save(self) -> None:
        _ensure_dirs()
        payload = json.dumps([asdict(t) for t in self._items], indent=2, ensure_ascii=False)
        # Atomic write: tmp file in same dir, then replace.
        fd, tmp = tempfile.mkstemp(prefix="archive_", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # --- public API -----------------------------------------------------

    def add(
        self,
        text: str,
        *,
        target_app: Optional[str] = None,
        target_category: Optional[str] = None,
        raw_text: Optional[str] = None,
        cleanup_provider: Optional[str] = None,
        audio_duration_s: Optional[float] = None,
        transcribe_ms: Optional[int] = None,
        cleanup_ms: Optional[int] = None,
    ) -> Transcript:
        text = text.strip()
        with self._lock:
            t = Transcript(
                id=datetime.now().strftime("%Y%m%d%H%M%S%f"),
                timestamp=datetime.now().isoformat(timespec="seconds"),
                text=text,
                target_app=target_app,
                target_category=target_category,
                raw_text=raw_text,
                cleanup_provider=cleanup_provider,
                audio_duration_s=audio_duration_s,
                transcribe_ms=transcribe_ms,
                cleanup_ms=cleanup_ms,
            )
            self._items.append(t)
            self._save()
        log.info("archived transcript id=%s words=%d", t.id, t.word_count)
        return t

    def delete(self, transcript_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [t for t in self._items if t.id != transcript_id]
            if len(self._items) == before:
                return False
            self._save()
        return True

    def clear(self) -> None:
        with self._lock:
            self._items = []
            self._save()

    def all(self) -> List[Transcript]:
        """Return a snapshot of all transcripts, newest first."""
        with self._lock:
            return list(reversed(self._items))

    def search(self, query: str) -> List[Transcript]:
        q = query.strip().lower()
        if not q:
            return self.all()
        with self._lock:
            return [t for t in reversed(self._items) if q in t.text.lower()]

    def __len__(self) -> int:
        return len(self._items)
