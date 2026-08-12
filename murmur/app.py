from __future__ import annotations

import logging
import sys
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from murmur import sounds
from murmur.archive import Archive
from murmur.audio.capture import AudioCapture
from murmur.cleanup.prompts import CleanupContext
from murmur.cleanup.providers import CleanupProvider, make_provider
from murmur.config import LOG_FILE, Settings, _ensure_dirs, load_settings, save_settings
from murmur.hotkeys.windows import PushToTalkHotkey, TapHotkey
from murmur.overlay.archive_window import ArchiveWindow
from murmur.overlay.pill import Pill, PillState
from murmur.paste.clipboard import copy_selection, paste_text
from murmur.paste.foreground import ForegroundWindow, get_foreground_window
from murmur.stt.engine import WhisperEngine
from murmur.tray import Tray
from murmur.tts import SpeakerThread

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    _ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


class ModelLoader(QThread):
    """Loads the Whisper model off the GUI thread."""
    ready = Signal()
    failed = Signal(str)

    def __init__(self, engine: WhisperEngine):
        super().__init__()
        self.engine = engine

    def run(self) -> None:
        try:
            self.engine.load()
            self.ready.emit()
        except Exception as e:
            log.exception("model load failed")
            self.failed.emit(str(e))


class Transcriber(QThread):
    """Runs transcription + (optional) cleanup off the GUI thread.

    Emits `done(payload)` with a dict of:
      raw_text, final_text, transcribe_ms, cleanup_ms, audio_duration_s, target_app,
      target_category, cleanup_provider
    """

    done = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        engine: WhisperEngine,
        cleanup_provider: CleanupProvider,
        audio: np.ndarray,
        context: CleanupContext,
        raw_mode: bool,
    ):
        super().__init__()
        self.engine = engine
        self.cleanup_provider = cleanup_provider
        self.audio = audio
        self.context = context
        self.raw_mode = raw_mode

    def run(self) -> None:
        try:
            audio_dur = self.audio.size / 16000

            t0 = time.perf_counter()
            raw = self.engine.transcribe(self.audio)
            transcribe_ms = int((time.perf_counter() - t0) * 1000)

            cleanup_ms = 0
            provider_name: Optional[str] = None
            final = raw

            if (
                not self.raw_mode
                and raw.strip()
                and self.cleanup_provider.name != "disabled"
            ):
                t1 = time.perf_counter()
                cleaned = self.cleanup_provider.cleanup(raw, self.context)
                cleanup_ms = int((time.perf_counter() - t1) * 1000)
                provider_name = self.cleanup_provider.name
                if cleaned.strip():
                    final = cleaned

            self.done.emit({
                "raw_text": raw,
                "final_text": final,
                "transcribe_ms": transcribe_ms,
                "cleanup_ms": cleanup_ms,
                "audio_duration_s": audio_dur,
                "target_app": self.context.target_app,
                "target_category": self.context.target_category,
                "cleanup_provider": provider_name,
                "raw_mode": self.raw_mode,
            })
        except Exception as e:
            log.exception("transcribe pipeline failed")
            self.failed.emit(str(e))


class MurmurApp(QObject):
    """Wiring + state machine.

    hotkey down  -> capture audio
    hotkey up    -> transcribe -> (cleanup unless raw_mode) -> paste -> archive
    """

    MIN_SAMPLES = 16000 * 0.25  # 250ms minimum utterance
    settings_changed = Signal()

    def __init__(self, qt_app: QApplication):
        super().__init__()
        _configure_logging()

        self.qt_app = qt_app
        self.settings: Settings = load_settings()

        self.engine = WhisperEngine(self.settings)
        self.archive = Archive()
        self.cleanup_provider: CleanupProvider = self._build_provider()

        # Captured at the moment LISTENING begins so the cleanup prompt knows
        # which app the user was targeting BEFORE the pill/tray potentially
        # changes the foreground window.
        self._foreground: Optional[ForegroundWindow] = None

        self.pill = Pill(
            width=self.settings.pill_width,
            height=self.settings.pill_height,
            bottom_margin=self.settings.pill_bottom_margin,
        )
        self.history = ArchiveWindow(
            self.archive,
            hotkey_label=self.settings.push_to_talk_key,
        )

        # Settings window is created lazily; importing here would create a circular
        # import (settings_window imports MurmurApp's settings_changed signal indirectly).
        self.settings_window = None

        self.hotkey = PushToTalkHotkey(
            self.settings.push_to_talk_key,
            raw_modifier=self.settings.raw_modifier,
        )
        self.read_hotkey = TapHotkey(self.settings.read_aloud_key)
        self.tray = Tray(hotkey_label=self.settings.push_to_talk_key.upper())

        self._capture: Optional[AudioCapture] = None
        self._transcriber: Optional[Transcriber] = None
        self._speaker: Optional[SpeakerThread] = None
        self._model_ready = False

        # Wire signals
        self.hotkey.pressed.connect(self._on_hotkey_press)
        self.hotkey.released.connect(self._on_hotkey_release)
        self.read_hotkey.triggered.connect(self._on_read_aloud)
        self.tray.quit_requested.connect(self._quit)
        self.tray.toggle_pill_requested.connect(self._toggle_pill)
        self.tray.show_history_requested.connect(self._show_history)
        self.tray.show_settings_requested.connect(self._show_settings)

    # --- lifecycle ------------------------------------------------------

    def start(self) -> None:
        log.info("MURMUR starting")
        log.info(
            "settings: model=%s compute=%s ptt=%s cleanup=%s",
            self.settings.model,
            self.settings.compute_type,
            self.settings.push_to_talk_key,
            self.settings.cleanup_provider,
        )
        self.pill.set_state(PillState.IDLE)

        self._loader = ModelLoader(self.engine)
        self._loader.ready.connect(self._on_model_ready)
        self._loader.failed.connect(self._on_model_failed)
        self._loader.start()

        self.hotkey.start()
        self.read_hotkey.start()

    def _quit(self) -> None:
        log.info("MURMUR quitting")
        try:
            self.hotkey.stop()
        except Exception:
            pass
        try:
            self.read_hotkey.stop()
        except Exception:
            pass
        if self._speaker is not None and self._speaker.isRunning():
            self._speaker.stop()
        if self._capture is not None:
            self._capture.stop()
        self.qt_app.quit()

    def _toggle_pill(self) -> None:
        if self.pill.isVisible():
            self.pill.hide()
        else:
            self.pill.set_state(PillState.IDLE)

    def _show_history(self) -> None:
        self.history.open_window()

    def _show_settings(self) -> None:
        if self.settings_window is None:
            # lazy import to avoid bootstrap order issues
            from murmur.overlay.settings_window import SettingsWindow

            self.settings_window = SettingsWindow(self.settings)
            self.settings_window.applied.connect(self._on_settings_applied)
        self.settings_window.refresh_from(self.settings)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _on_settings_applied(self, new_settings: Settings) -> None:
        log.info("settings updated")
        old_key = self.settings.push_to_talk_key
        old_modifier = self.settings.raw_modifier
        old_provider = self.settings.cleanup_provider
        old_provider_key = self.settings.anthropic_api_key
        old_model = self.settings.model
        old_compute = self.settings.compute_type

        self.settings = new_settings
        save_settings(new_settings)

        # Re-register hotkey if the trigger or modifier changed.
        if old_key != new_settings.push_to_talk_key or old_modifier != new_settings.raw_modifier:
            try:
                self.hotkey.stop()
            except Exception:
                pass
            self.hotkey = PushToTalkHotkey(
                new_settings.push_to_talk_key,
                raw_modifier=new_settings.raw_modifier,
            )
            self.hotkey.pressed.connect(self._on_hotkey_press)
            self.hotkey.released.connect(self._on_hotkey_release)
            self.hotkey.start()

        # Rebuild cleanup provider on relevant changes.
        if (
            old_provider != new_settings.cleanup_provider
            or old_provider_key != new_settings.anthropic_api_key
        ):
            self.cleanup_provider = self._build_provider()

        # If model or compute type changed, reload the model.
        if old_model != new_settings.model or old_compute != new_settings.compute_type:
            self._model_ready = False
            self.engine = WhisperEngine(new_settings)
            self._loader = ModelLoader(self.engine)
            self._loader.ready.connect(self._on_model_ready)
            self._loader.failed.connect(self._on_model_failed)
            self.pill.set_state(PillState.PROCESSING)
            self._loader.start()
        else:
            # Engine kept; update the in-engine settings so beam/vad etc apply.
            self.engine.settings = new_settings

    # --- state transitions ---------------------------------------------

    def _build_provider(self) -> CleanupProvider:
        return make_provider(
            self.settings.cleanup_provider,
            anthropic_api_key=self.settings.anthropic_api_key,
            anthropic_model=self.settings.anthropic_model,
            ollama_model=self.settings.ollama_model,
            ollama_base_url=self.settings.ollama_base_url,
        )

    def _on_model_ready(self) -> None:
        log.info("model ready")
        self._model_ready = True
        self.pill.set_state(PillState.IDLE)

    def _on_model_failed(self, msg: str) -> None:
        log.error("model load failed: %s", msg)
        self.pill.set_state(PillState.ERROR)
        if self.settings.play_sound_cues:
            sounds.play_error()

    def _on_read_aloud(self) -> None:
        # Toggle: a tap while speaking stops the speech.
        if self._speaker is not None and self._speaker.isRunning():
            log.info("read-aloud: stopping speech")
            self._speaker.stop()
            return

        text = copy_selection()
        if not text.strip():
            log.info("read-aloud: no text selected")
            return

        log.info("read-aloud: speaking %d chars (voice=%s)", len(text), self.settings.tts_voice or "sapi")
        self._speaker = SpeakerThread(
            text,
            rate=self.settings.tts_rate,
            voice=self.settings.tts_voice,
        )
        self._speaker.start()

    def _on_hotkey_press(self) -> None:
        if not self._model_ready:
            log.info("hotkey pressed but model not ready yet — ignoring")
            return
        if self._capture is not None and self._capture.isRunning():
            return
        if self._transcriber is not None and self._transcriber.isRunning():
            return

        # Capture foreground before our pill paints over it (paranoia — pill
        # doesn't take focus, but window class queries can still be racy).
        self._foreground = get_foreground_window() if self.settings.use_app_context else None
        if self._foreground:
            log.info("foreground: %s (%s)", self._foreground.process, self._foreground.category)

        log.info("hotkey down -> LISTENING")
        self.pill.set_state(PillState.LISTENING)
        if self.settings.play_sound_cues:
            sounds.play_start()

        self._capture = AudioCapture(device=self.settings.input_device)
        self._capture.samples.connect(self.pill.push_samples)
        self._capture.done.connect(self._on_capture_done)
        self._capture.error.connect(self._on_capture_error)
        self._capture.start()

    def _on_hotkey_release(self, raw_mode: bool) -> None:
        if self._capture is None or not self._capture.isRunning():
            return
        log.info("hotkey up (raw_mode=%s) -> stopping capture", raw_mode)
        self._raw_mode_pending = raw_mode
        self._capture.stop()
        self.pill.set_state(PillState.PROCESSING)
        if self.settings.play_sound_cues:
            sounds.play_stop()

    def _on_capture_done(self, audio: np.ndarray) -> None:
        log.info("captured %d samples (%.2fs)", audio.size, audio.size / 16000)
        if audio.size < self.MIN_SAMPLES:
            log.info("utterance too short, skipping")
            self.pill.set_state(PillState.IDLE)
            return

        ctx = CleanupContext(
            target_app=self._foreground.process if self._foreground else None,
            target_category=self._foreground.category if self._foreground else "other",
            window_title=self._foreground.title if self._foreground else None,
            dictionary=tuple(self.settings.dictionary),
        )

        self._transcriber = Transcriber(
            self.engine,
            self.cleanup_provider,
            audio,
            ctx,
            raw_mode=getattr(self, "_raw_mode_pending", False),
        )
        self._transcriber.done.connect(self._on_transcribe_done)
        self._transcriber.failed.connect(self._on_transcribe_failed)
        self._transcriber.start()

    def _on_capture_error(self, msg: str) -> None:
        log.error("capture error: %s", msg)
        self.pill.set_state(PillState.ERROR)
        if self.settings.play_sound_cues:
            sounds.play_error()
        QTimer.singleShot(1500, lambda: self.pill.set_state(PillState.IDLE))

    def _on_transcribe_done(self, payload: dict) -> None:
        final = (payload.get("final_text") or "").strip()
        raw = (payload.get("raw_text") or "").strip()
        log.info(
            "result: stt=%dms cleanup=%dms provider=%s raw_mode=%s -> %d chars",
            payload.get("transcribe_ms") or 0,
            payload.get("cleanup_ms") or 0,
            payload.get("cleanup_provider"),
            payload.get("raw_mode"),
            len(final),
        )
        if not final:
            self.pill.set_state(PillState.IDLE)
            return

        if self.settings.paste_on_done:
            paste_text(final)

        self.archive.add(
            final,
            target_app=payload.get("target_app"),
            target_category=payload.get("target_category"),
            raw_text=raw if raw and raw != final else None,
            cleanup_provider=payload.get("cleanup_provider"),
            audio_duration_s=payload.get("audio_duration_s"),
            transcribe_ms=payload.get("transcribe_ms"),
            cleanup_ms=payload.get("cleanup_ms"),
        )
        self.history.on_new_transcript()

        self.pill.set_state(PillState.DONE)
        if self.settings.play_sound_cues:
            sounds.play_done()
        QTimer.singleShot(700, lambda: self.pill.set_state(PillState.IDLE))

    def _on_transcribe_failed(self, msg: str) -> None:
        log.error("transcribe failed: %s", msg)
        self.pill.set_state(PillState.ERROR)
        if self.settings.play_sound_cues:
            sounds.play_error()
        QTimer.singleShot(1500, lambda: self.pill.set_state(PillState.IDLE))
