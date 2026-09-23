from __future__ import annotations

from enum import Enum


class PillState(str, Enum):
    HIDDEN = "hidden"
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"
