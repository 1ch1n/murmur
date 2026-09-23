from __future__ import annotations

import logging
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QFileDialog

from murmur import sounds
from murmur.archive import Archive, Transcript
from murmur.audio.capture import AudioCapture
from murmur.cleanup.prompts import CleanupContext
from murmur.cleanup.providers import CleanupProvider, make_provider
from murmur.config import LOG_FILE, Settings, _ensure_dirs, load_settings, save_settings
from murmur.hotkeys.windows import PushToTalkHotkey, TapHotkey
from murmur.overlay.archive_window import ArchiveWindow
from murmur.overlay.pill import Pill, PillPrefs, PillState
from murmur.paste.clipboard import copy_selection, paste_text
from murmur.paste.foreground import ForegroundWindow, get_foreground_window
from murmur.stt.engine import WhisperEngine
from murmur.stt.file import AUDIO_EXTENSIONS
from murmur.stt.file_job import FileTranscriber
from murmur.tray import Tray
from murmur.tts import ReaderWorker, split_sentences

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


def hotkey_short(key: str) -> str:
    """'right alt' -> 'RALT', 'f9' -> 'F9', 'caps lock' -> 'CAPS'."""
    words = (key or "").lower().split()
    out = []
    for w in words:
        out.append({"right": "R", "left": "L", "windows": "WIN", "lock": "", "control": "CTRL"}.get(w, w.upper()))
    return "".join(out) or key.upper()


def transcript_meta(t: Transcript) -> str:
    try:
        stamp = datetime.fromisoformat(t.timestamp).strftime("%H:%M")
    except Exception:
        stamp = ""
    words = t.word_count or len((t.text or "").split())
    return f"{stamp} · {words} words" if stamp else f"{words} words"


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
        # which app the user was targeting BEFORE the widget/tray potentially
        # changes the foreground window.
        self._foreground: Optional[ForegroundWindow] = None

        self.pill = Pill(PillPrefs.from_settings(self.settings))
        self.pill.set_caption(self._caption())
        self.history = ArchiveWindow(
            self.archive,
            hotkey_label=self.settings.push_to_talk_key,
        )

        # Settings and reader windows are created lazily.
        self.settings_window = None
        self.reader_window = None

        # Read-aloud engine: one worker thread for the app's lifetime.
        self.reader = ReaderWorker(rate=self.settings.tts_rate, voice=self.settings.tts_voice)
        self._reader_n = 0
        self.reader.sentence_started.connect(self._on_reader_sentence)
        self.reader.state_changed.connect(self._on_reader_state)
        self.reader.error.connect(lambda m: log.warning("reader: %s", m))

        self.hotkey = PushToTalkHotkey(
            self.settings.push_to_talk_key,
            raw_modifier=self.settings.raw_modifier,
        )
        self.read_hotkey = TapHotkey(self.settings.read_aloud_key)
        self.tray = Tray(hotkey_label=self.settings.push_to_talk_key.upper())

        self._capture: Optional[AudioCapture] = None
        self._transcriber: Optional[Transcriber] = None
        self._file_job: Optional[FileTranscriber] = None
        self._model_ready = False

        # Wire signals
        self.hotkey.pressed.connect(self._on_hotkey_press)
        self.hotkey.released.connect(self._on_hotkey_release)
        self.read_hotkey.triggered.connect(self._on_read_aloud)

        self.tray.quit_requested.connect(self._quit)
        self.tray.toggle_pill_requested.connect(self._toggle_pill)
        self.tray.show_history_requested.connect(self._show_history)
        self.tray.show_settings_requested.connect(self._show_settings)
        self.tray.transcribe_file_requested.connect(self._transcribe_file)
        self.tray.read_aloud_requested.connect(self._show_reader)

        self.pill.history_requested.connect(self._show_history)
        self.pill.settings_requested.connect(self._show_settings)
        self.pill.transcribe_file_requested.connect(self._transcribe_file)
        self.pill.read_aloud_requested.connect(self._show_reader)
        self.pill.quit_requested.connect(self._quit)
        self.pill.copy_requested.connect(self._copy_text)
        self.pill.waveform_toggled.connect(self._on_waveform_toggled)
        self.pill.position_changed.connect(self._on_pill_moved)

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
        latest = self.archive.all()[:1]
        if latest:
            self.pill.set_last_transcript(latest[0].text, transcript_meta(latest[0]))

        self.pill.set_state(PillState.PROCESSING)
        self.pill.set_readout("Loading")

        self._loader = ModelLoader(self.engine)
        self._loader.ready.connect(self._on_model_ready)
        self._loader.failed.connect(self._on_model_failed)
        self._loader.start()

        self.reader.start()
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
        try:
            self.reader.quit_worker()
            self.reader.wait(800)
        except Exception:
            pass
        if self._capture is not None:
            self._capture.stop()
        self.qt_app.quit()

    def _caption(self) -> str:
        return hotkey_short(self.settings.push_to_talk_key)

    def _toggle_pill(self) -> None:
        if self.pill.isVisible():
            self.pill.set_state(PillState.HIDDEN)
        else:
            self.pill.set_state(PillState.IDLE if self._model_ready else PillState.PROCESSING)

    def _show_history(self) -> None:
        self.pill.collapse_panel(restore_focus=False)
        self.history.open_window()

    def _show_settings(self) -> None:
        self.pill.collapse_panel(restore_focus=False)
        if self.settings_window is None:
            # lazy import to avoid bootstrap order issues
            from murmur.overlay.settings_window import SettingsWindow

            self.settings_window = SettingsWindow(self.settings)
            self.settings_window.applied.connect(self._on_settings_applied)
            self.settings_window.reset_position_requested.connect(self._reset_pill_position)
        self.settings_window.refresh_from(self.settings)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _on_settings_applied(self, new_settings: Settings) -> None:
        log.info("settings updated")
        old = self.settings

        # The dialog never owns the widget position or the reader voice;
        # carry the live values over.
        new_settings = replace(
            new_settings,
            pill_x=old.pill_x, pill_y=old.pill_y, pill_screen=old.pill_screen,
            tts_rate=old.tts_rate, tts_voice=old.tts_voice,
        )
        self.settings = new_settings
        save_settings(new_settings)

        # Re-register the push-to-talk hotkey if the trigger or modifier changed.
        if old.push_to_talk_key != new_settings.push_to_talk_key or old.raw_modifier != new_settings.raw_modifier:
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

        # Same for the read-aloud tap key.
        if old.read_aloud_key != new_settings.read_aloud_key:
            try:
                self.read_hotkey.stop()
            except Exception:
                pass
            self.read_hotkey = TapHotkey(new_settings.read_aloud_key)
            self.read_hotkey.triggered.connect(self._on_read_aloud)
            self.read_hotkey.start()

        # Rebuild the cleanup provider if anything it reads changed.
        provider_fields = (
            "cleanup_provider", "anthropic_api_key", "anthropic_model",
            "ollama_model", "ollama_base_url", "cleanup_timeout_s",
        )
        if any(getattr(old, f) != getattr(new_settings, f) for f in provider_fields):
            self.cleanup_provider = self._build_provider()

        # Model, compute type or device: reload the model.
        if (
            old.model != new_settings.model
            or old.compute_type != new_settings.compute_type
            or old.device != new_settings.device
        ):
            self._model_ready = False
            self.engine = WhisperEngine(new_settings)
            self._loader = ModelLoader(self.engine)
            self._loader.ready.connect(self._on_model_ready)
            self._loader.failed.connect(self._on_model_failed)
            self.pill.set_state(PillState.PROCESSING)
            self.pill.set_readout("Loading")
            self._loader.start()
        else:
            # Engine kept; update the in-engine settings so beam/vad etc apply.
            self.engine.settings = new_settings

        if old.tts_rate != new_settings.tts_rate or old.tts_voice != new_settings.tts_voice:
            self.reader.set_voice(new_settings.tts_rate, new_settings.tts_voice)

        # Widget prefs apply live.
        new_prefs = PillPrefs.from_settings(new_settings)
        if new_prefs != self.pill.prefs:
            self.pill.apply_prefs(new_prefs)
        self.pill.set_caption(self._caption())

    # --- widget position / prefs -------------------------------------------

    def _on_pill_moved(self, x: int, y: int, screen: str) -> None:
        self.settings = replace(self.settings, pill_x=x, pill_y=y, pill_screen=screen)
        save_settings(self.settings)
        log.info("widget moved to (%d, %d) on %s", x, y, screen)

    def _reset_pill_position(self) -> None:
        self.settings = replace(self.settings, pill_x=None, pill_y=None, pill_screen="")
        save_settings(self.settings)
        self.pill.reset_position()

    def _on_waveform_toggled(self, on: bool) -> None:
        self.settings = replace(self.settings, pill_show_waveform=bool(on))
        save_settings(self.settings)
        self.pill.apply_prefs(PillPrefs.from_settings(self.settings))

    def _copy_text(self, text: str) -> None:
        if text:
            QApplication.clipboard().setText(text)
            self.pill.set_readout("Copied", 1200)

    # --- read aloud --------------------------------------------------------

    def _show_reader(self) -> None:
        self.pill.collapse_panel(restore_focus=False)
        if self.reader_window is None:
            from murmur.overlay.reader_window import ReaderWindow

            w = ReaderWindow(rate=self.settings.tts_rate, voice=self.settings.tts_voice)
            w.voice_changed.connect(self._on_reader_voice)
            w.play_requested.connect(self._reader_play)
            w.toggle_requested.connect(self.reader.toggle)
            w.stop_requested.connect(self.reader.stop)
            w.seek_requested.connect(self.reader.seek)
            w.next_requested.connect(self.reader.next)
            w.prev_requested.connect(self.reader.prev)
            w.rate_changed.connect(self._on_reader_rate)
            self.reader_window = w
        self.reader_window.open_window()

    def _reader_play(self, sentences: list, start: int) -> None:
        self._reader_n = len(sentences)
        self.reader.load(sentences)
        self.reader.play(start)

    def _on_reader_rate(self, rate: int) -> None:
        self.settings = replace(self.settings, tts_rate=int(rate))
        save_settings(self.settings)
        self.reader.set_voice(self.settings.tts_rate, self.settings.tts_voice)

    def _on_reader_voice(self, voice: str) -> None:
        self.settings = replace(self.settings, tts_voice=voice or "")
        save_settings(self.settings)
        self.reader.set_voice(self.settings.tts_rate, self.settings.tts_voice)
        log.info("read-aloud voice: %s", voice or "windows default")

    def _on_reader_sentence(self, idx: int) -> None:
        if self.reader_window is not None:
            self.reader_window.on_sentence(idx)
        if self._reader_n:
            self.pill.set_readout(f"{idx + 1}/{self._reader_n}")

    def _on_reader_state(self, state: str) -> None:
        if self.reader_window is not None:
            self.reader_window.on_state(state)
        if state == "stopped":
            self.pill.set_readout(None)
        elif state == "paused":
            self.pill.set_readout("Paused")

    # --- state transitions ---------------------------------------------

    def _build_provider(self) -> CleanupProvider:
        return make_provider(
            self.settings.cleanup_provider,
            anthropic_api_key=self.settings.anthropic_api_key,
            anthropic_model=self.settings.anthropic_model,
            ollama_model=self.settings.ollama_model,
            ollama_base_url=self.settings.ollama_base_url,
            timeout=self.settings.cleanup_timeout_s,
        )

    def _on_model_ready(self) -> None:
        log.info("model ready")
        self._model_ready = True
        self.pill.set_readout(None)
        self.pill.set_state(PillState.IDLE)

    def _on_model_failed(self, msg: str) -> None:
        log.error("model load failed: %s", msg)
        self.pill.set_readout(None)
        self.pill.set_state(PillState.ERROR)
        if self.settings.play_sound_cues:
            sounds.play_error()

    def _on_read_aloud(self) -> None:
        """F9: read the selection. While something is being read, F9 is
        pause / resume. Use the reader window or Stop to end it."""
        if self.reader.is_active:
            log.info("read-aloud: %s", "resume" if self.reader.is_paused else "pause")
            self.reader.toggle()
            return

        text = copy_selection()
        if not text.strip():
            log.info("read-aloud: no text selected")
            self.pill.set_readout("Select text", 1200)
            return

        spans = split_sentences(text)
        if not spans:
            return
        log.info("read-aloud: %d sentences, %d chars (voice=%s)",
                 len(spans), len(text), self.settings.tts_voice or "sapi")
        if self.reader_window is not None:
            self.reader_window.set_text(text)
        self._reader_play([s[2] for s in spans], 0)

    def _busy(self) -> bool:
        return (
            (self._capture is not None and self._capture.isRunning())
            or (self._transcriber is not None and self._transcriber.isRunning())
            or (self._file_job is not None and self._file_job.isRunning())
        )

    def _on_hotkey_press(self) -> None:
        if not self._model_ready:
            log.info("hotkey pressed but model not ready yet; ignoring")
            return
        if self._busy():
            if self._file_job is not None and self._file_job.isRunning():
                self.pill.set_readout("Busy", 900)
            return

        # If the panel is open, put focus back where the user had it so the
        # foreground capture and the paste both land in the right app.
        self.pill.collapse_panel(restore_focus=True)

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
        min_samples = 16000 * max(0, self.settings.min_utterance_ms) / 1000
        if audio.size < min_samples:
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

        t = self.archive.add(
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
        self.pill.set_last_transcript(t.text, transcript_meta(t))

        self.pill.set_state(PillState.DONE)
        self.pill.set_readout(f"{t.word_count} words", 1100)
        if self.settings.play_sound_cues:
            sounds.play_done()
        QTimer.singleShot(800, lambda: self.pill.set_state(PillState.IDLE))

    def _on_transcribe_failed(self, msg: str) -> None:
        log.error("transcribe failed: %s", msg)
        self.pill.set_state(PillState.ERROR)
        if self.settings.play_sound_cues:
            sounds.play_error()
        QTimer.singleShot(1500, lambda: self.pill.set_state(PillState.IDLE))

    # --- file transcription --------------------------------------------

    def _transcribe_file(self) -> None:
        self.pill.collapse_panel(restore_focus=False)
        if not self._model_ready:
            self.pill.set_readout("Loading", 900)
            return
        if self._busy():
            self.pill.set_readout("Busy", 900)
            return

        exts = " ".join(f"*{e}" for e in AUDIO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(
            None,
            "Transcribe audio or video",
            str(Path.home() / "Downloads") if (Path.home() / "Downloads").is_dir() else str(Path.home()),
            f"Audio / Video ({exts});;All files (*)",
        )
        if not path:
            return

        log.info("file transcription: %s", path)
        self._file_job = FileTranscriber(self.engine, Path(path))
        self._file_job.progress.connect(self._on_file_progress)
        self._file_job.done.connect(self._on_file_done)
        self._file_job.failed.connect(self._on_file_failed)
        self.pill.set_state(PillState.PROCESSING)
        self.pill.set_progress(0, "")
        self._file_job.start()

    def _on_file_progress(self, pct: int, tail: str) -> None:
        self.pill.set_progress(pct, tail)

    def _on_file_done(self, payload: dict) -> None:
        text = (payload.get("text") or "").strip()
        name = payload.get("source_name") or "file"
        self.pill.set_progress(None)
        if not text:
            log.info("file transcription: no speech in %s", name)
            self.pill.set_readout("No speech", 1500)
            self.pill.set_state(PillState.IDLE)
            return

        QApplication.clipboard().setText(text)
        t = self.archive.add(
            text,
            target_app=name,
            target_category="file",
            audio_duration_s=payload.get("duration_s"),
            transcribe_ms=payload.get("transcribe_ms"),
        )
        self.history.on_new_transcript()
        self.pill.set_last_transcript(t.text, transcript_meta(t))
        log.info("file transcription done: %s -> %d words in %d ms",
                 name, t.word_count, payload.get("transcribe_ms") or 0)

        self.pill.set_state(PillState.DONE)
        self.pill.set_readout("Copied", 1400)
        if self.settings.play_sound_cues:
            sounds.play_done()
        QTimer.singleShot(1200, lambda: self.pill.set_state(PillState.IDLE))

    def _on_file_failed(self, msg: str) -> None:
        log.error("file transcription failed: %s", msg)
        self.pill.set_progress(None)
        self.pill.set_state(PillState.ERROR)
        if self.settings.play_sound_cues:
            sounds.play_error()
        QTimer.singleShot(1500, lambda: self.pill.set_state(PillState.IDLE))
