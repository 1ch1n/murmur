from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import json

APP_NAME = "MURMUR"
APP_VERSION = "0.3.0"

DATA_DIR = Path.home() / ".murmur"
MODELS_DIR = DATA_DIR / "models"
CONFIG_FILE = DATA_DIR / "config.json"
LOG_FILE = DATA_DIR / "murmur.log"

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SIZE = 1600  # 100ms blocks


@dataclass
class Settings:
    # STT
    model: str = "small.en"        # tiny.en | base.en | small.en | medium.en
    compute_type: str = "int8"     # int8 | int8_float16 | float16 | float32
    device: str = "cpu"            # cpu | cuda
    beam_size: int = 1             # 1 = greedy (fastest); 5 = highest accuracy
    vad_filter: bool = True

    # Audio
    input_device: int | None = None  # None = system default
    min_utterance_ms: int = 250

    # Hotkey
    push_to_talk_key: str = "right alt"  # keyboard lib name
    paste_on_done: bool = True
    play_sound_cues: bool = False
    raw_modifier: str = "shift"    # hold this while releasing PTT for raw (no cleanup)

    # Read-aloud (tap to speak the current selection, tap again to stop).
    # Must NOT be a modifier key: the keyboard lib confuses left/right
    # variants, and the handler itself sends Ctrl+C — a modifier trigger
    # re-fires on normal copy/paste chords.
    read_aloud_key: str = "f9"
    tts_rate: int = 0              # -10 (slow) .. 10 (fast)
    # Edge neural voice name (see `edge-tts --list-voices`). NOTE: setting
    # this sends the text being read to Microsoft's Edge TTS service.
    # "" (default) = fully offline Windows SAPI voice.
    tts_voice: str = ""

    # Cleanup
    cleanup_provider: str = "disabled"   # disabled | anthropic | ollama
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"
    ollama_model: str = "llama3.2:3b"
    ollama_base_url: str = "http://localhost:11434"
    cleanup_timeout_s: float = 10.0
    use_app_context: bool = True

    # Vocabulary
    dictionary: list[str] = field(default_factory=list)  # proper nouns / tech terms

    # UI
    pill_width: int = 280
    pill_height: int = 56
    pill_bottom_margin: int = 80


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    _ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return Settings(**{k: v for k, v in raw.items() if k in Settings.__dataclass_fields__})
        except Exception:
            # Don't silently destroy the user's settings (API key, dictionary):
            # preserve the unreadable file and reset from defaults.
            import logging

            backup = CONFIG_FILE.with_name("config.json.bak")
            try:
                CONFIG_FILE.replace(backup)
            except OSError:
                backup = None
            logging.getLogger(__name__).exception(
                "config.json unreadable; backed up to %s and reset to defaults", backup
            )
    s = Settings()
    save_settings(s)
    return s


def save_settings(s: Settings) -> None:
    _ensure_dirs()
    CONFIG_FILE.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
