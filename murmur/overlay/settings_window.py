from __future__ import annotations

import logging
from dataclasses import asdict, replace
from typing import Optional

import sounddevice as sd
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from murmur.config import Settings
from murmur.overlay.styles import build_qss

log = logging.getLogger(__name__)


MODELS = ["tiny.en", "base.en", "small.en", "medium.en"]
COMPUTE_TYPES = ["int8", "int8_float16", "float16", "float32"]
DEVICES = ["cpu", "cuda"]
PROVIDERS = ["disabled", "anthropic", "ollama"]
MODIFIERS = ["", "shift", "ctrl", "alt"]
COMMON_KEYS = [
    "right alt", "right ctrl", "right shift", "right windows",
    "left alt", "left ctrl",
    "caps lock", "scroll lock", "pause",
    "f9", "f10", "f11", "f12",
    "`",
]


class SettingsWindow(QMainWindow):
    """Tabbed settings editor. Applies changes via signal, never edits config.json directly."""

    applied = Signal(Settings)
    reset_position_requested = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.setWindowTitle("MURMUR: SETTINGS")
        self.resize(640, 600)
        self.setStyleSheet(build_qss("settings"))
        self._settings = settings
        self._build()
        self.refresh_from(settings)

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        title = QLabel("MURMUR  ·  SETTINGS")
        title.setObjectName("title")
        root.addWidget(title)

        div = QFrame()
        div.setObjectName("divider")
        root.addWidget(div)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "GENERAL")
        tabs.addTab(self._build_stt_tab(), "STT")
        tabs.addTab(self._build_cleanup_tab(), "CLEANUP")
        tabs.addTab(self._build_dict_tab(), "DICTIONARY")
        tabs.addTab(self._build_widget_tab(), "WIDGET")
        root.addWidget(tabs, 1)

        # action row
        actions = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("status")
        actions.addWidget(self.status)
        actions.addStretch()

        revert_btn = QPushButton("REVERT")
        revert_btn.clicked.connect(lambda: self.refresh_from(self._settings))
        actions.addWidget(revert_btn)

        apply_btn = QPushButton("APPLY")
        apply_btn.setObjectName("primary")
        apply_btn.clicked.connect(self._apply)
        actions.addWidget(apply_btn)
        root.addLayout(actions)

        QShortcut(QKeySequence("Ctrl+S"), self, self._apply)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)

    # --- tabs -----------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(10)

        self.f_ptt = QComboBox()
        self.f_ptt.setEditable(True)
        self.f_ptt.addItems(COMMON_KEYS)
        form.addRow(QLabel("PUSH-TO-TALK KEY"), self.f_ptt)

        self.f_raw_mod = QComboBox()
        for m in MODIFIERS:
            self.f_raw_mod.addItem(m or "<none>", userData=m)
        form.addRow(QLabel("HOLD-WHILE-RELEASE FOR RAW"), self.f_raw_mod)

        self.f_input = QComboBox()
        # Populated in refresh_from with the live device list.
        form.addRow(QLabel("INPUT DEVICE"), self.f_input)

        self.f_paste = QCheckBox("Auto-paste at cursor")
        form.addRow(QLabel(""), self.f_paste)

        self.f_sounds = QCheckBox("Play sound cues on start / stop / done")
        form.addRow(QLabel(""), self.f_sounds)

        self.f_app_ctx = QCheckBox("Use foreground-app context in cleanup")
        form.addRow(QLabel(""), self.f_app_ctx)

        hint = QLabel(
            "MURMUR runs in the system tray. Hold the push-to-talk key, speak, release. "
            "Click the floating widget for History, Settings, file transcription, or Quit. "
            "Drag it anywhere; the position is remembered."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(QLabel(""), hint)

        return w

    def _build_stt_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(10)

        self.f_model = QComboBox()
        self.f_model.addItems(MODELS)
        form.addRow(QLabel("WHISPER MODEL"), self.f_model)

        self.f_compute = QComboBox()
        self.f_compute.addItems(COMPUTE_TYPES)
        form.addRow(QLabel("COMPUTE TYPE"), self.f_compute)

        self.f_device = QComboBox()
        self.f_device.addItems(DEVICES)
        form.addRow(QLabel("DEVICE"), self.f_device)

        self.f_beam = QSpinBox()
        self.f_beam.setRange(1, 5)
        form.addRow(QLabel("BEAM SIZE"), self.f_beam)

        self.f_vad = QCheckBox("Silero VAD filter (trim silence)")
        form.addRow(QLabel(""), self.f_vad)

        self.f_min_ms = QSpinBox()
        self.f_min_ms.setRange(0, 2000)
        self.f_min_ms.setSuffix(" ms")
        form.addRow(QLabel("MIN UTTERANCE"), self.f_min_ms)

        hint = QLabel(
            "Changing the model triggers a re-download/load (~244 MB for small.en). "
            "int8 is the recommended default on CPU. Beam 1 is greedy/fastest; "
            "increase only if accuracy on hard utterances matters more than latency."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(QLabel(""), hint)

        return w

    def _build_cleanup_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(10)

        self.f_provider = QComboBox()
        self.f_provider.addItems(PROVIDERS)
        form.addRow(QLabel("PROVIDER"), self.f_provider)

        self.f_api_key = QLineEdit()
        self.f_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.f_api_key.setPlaceholderText("sk-ant-…")
        form.addRow(QLabel("ANTHROPIC API KEY"), self.f_api_key)

        self.f_anthropic_model = QLineEdit()
        form.addRow(QLabel("ANTHROPIC MODEL"), self.f_anthropic_model)

        self.f_ollama_model = QLineEdit()
        form.addRow(QLabel("OLLAMA MODEL"), self.f_ollama_model)

        self.f_ollama_url = QLineEdit()
        form.addRow(QLabel("OLLAMA URL"), self.f_ollama_url)

        self.f_timeout = QDoubleSpinBox()
        self.f_timeout.setRange(1.0, 60.0)
        self.f_timeout.setSingleStep(0.5)
        self.f_timeout.setSuffix(" s")
        form.addRow(QLabel("TIMEOUT"), self.f_timeout)

        hint = QLabel(
            "Cleanup removes filler, adds punctuation, and lightly fixes grammar without "
            "changing meaning. Hold the raw-modifier (e.g. Shift) while releasing PTT to "
            "skip cleanup for one utterance, useful for code dictation. "
            "API key is stored locally in plaintext at ~/.murmur/config.json."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(QLabel(""), hint)

        return w

    def _build_dict_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        layout.addWidget(QLabel("CUSTOM VOCABULARY"))
        self.f_dict = QPlainTextEdit()
        self.f_dict.setPlaceholderText(
            "One term per line. Examples:\n"
            "MURMUR\nfaster-whisper\nMyChatArchive\nChasko"
        )
        layout.addWidget(self.f_dict, 1)

        hint = QLabel(
            "These terms are injected as a bias hint into Whisper's initial_prompt "
            "and into the cleanup prompt so they survive transcription unchanged. "
            "Best for proper nouns, product names, code identifiers, and unusual terms."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        return w

    def _build_widget_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(10)

        self.f_scale = QComboBox()
        for label, key in (("Small", "s"), ("Medium", "m"), ("Large", "l")):
            self.f_scale.addItem(label, userData=key)
        form.addRow(QLabel("SIZE"), self.f_scale)

        self.f_opacity = QSpinBox()
        self.f_opacity.setRange(40, 100)
        self.f_opacity.setSingleStep(5)
        self.f_opacity.setSuffix(" %")
        form.addRow(QLabel("OPACITY"), self.f_opacity)

        self.f_wave = QCheckBox("Show the voice line while idle")
        form.addRow(QLabel(""), self.f_wave)

        reset_btn = QPushButton("RESET POSITION")
        reset_btn.clicked.connect(self.reset_position_requested.emit)
        form.addRow(QLabel("POSITION"), reset_btn)

        hint = QLabel(
            "Drag the widget anywhere on any monitor; the spot is saved. "
            "Click it to open the menu, right-click for the same menu, Esc to close. "
            "Reset puts it back at the bottom-center of the primary display."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(QLabel(""), hint)

        return w

    # --- state ----------------------------------------------------------

    def refresh_from(self, s: Settings) -> None:
        """Populate fields from `s`. Also re-queries input devices."""
        # The live settings become the baseline for the next APPLY, so fields
        # the app changes outside this dialog (widget position) survive.
        self._settings = s

        # Devices
        self.f_input.clear()
        self.f_input.addItem("Default", userData=None)
        try:
            devs = sd.query_devices()
        except Exception:
            devs = []
        for i, d in enumerate(devs):
            if d.get("max_input_channels", 0) > 0:
                self.f_input.addItem(f"{i}: {d['name'][:40]}", userData=i)
        idx = 0
        for i in range(self.f_input.count()):
            if self.f_input.itemData(i) == s.input_device:
                idx = i
                break
        self.f_input.setCurrentIndex(idx)

        # General
        self._set_combo_text(self.f_ptt, s.push_to_talk_key)
        idx_mod = 0
        for i in range(self.f_raw_mod.count()):
            if self.f_raw_mod.itemData(i) == s.raw_modifier:
                idx_mod = i
                break
        self.f_raw_mod.setCurrentIndex(idx_mod)
        self.f_paste.setChecked(s.paste_on_done)
        self.f_sounds.setChecked(s.play_sound_cues)
        self.f_app_ctx.setChecked(s.use_app_context)

        # STT
        self._set_combo_text(self.f_model, s.model)
        self._set_combo_text(self.f_compute, s.compute_type)
        self._set_combo_text(self.f_device, s.device)
        self.f_beam.setValue(s.beam_size)
        self.f_vad.setChecked(s.vad_filter)
        self.f_min_ms.setValue(s.min_utterance_ms)

        # Cleanup
        self._set_combo_text(self.f_provider, s.cleanup_provider)
        self.f_api_key.setText(s.anthropic_api_key)
        self.f_anthropic_model.setText(s.anthropic_model)
        self.f_ollama_model.setText(s.ollama_model)
        self.f_ollama_url.setText(s.ollama_base_url)
        self.f_timeout.setValue(s.cleanup_timeout_s)

        # Dictionary
        self.f_dict.setPlainText("\n".join(s.dictionary))

        # Widget
        idx_scale = 1
        for i in range(self.f_scale.count()):
            if self.f_scale.itemData(i) == (s.pill_scale or "m").lower():
                idx_scale = i
                break
        self.f_scale.setCurrentIndex(idx_scale)
        self.f_opacity.setValue(int(round(max(0.4, min(1.0, s.pill_opacity)) * 100)))
        self.f_wave.setChecked(s.pill_show_waveform)

        self.status.setText("")

    def _set_combo_text(self, cb: QComboBox, value: str) -> None:
        idx = cb.findText(value)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        else:
            cb.setEditText(value)

    def _collect(self) -> Settings:
        dict_lines = [line.strip() for line in self.f_dict.toPlainText().splitlines()]
        dictionary = [line for line in dict_lines if line]

        return replace(
            self._settings,
            push_to_talk_key=self.f_ptt.currentText().strip().lower(),
            raw_modifier=self.f_raw_mod.currentData() or "",
            input_device=self.f_input.currentData(),
            paste_on_done=self.f_paste.isChecked(),
            play_sound_cues=self.f_sounds.isChecked(),
            use_app_context=self.f_app_ctx.isChecked(),
            model=self.f_model.currentText().strip(),
            compute_type=self.f_compute.currentText().strip(),
            device=self.f_device.currentText().strip(),
            beam_size=self.f_beam.value(),
            vad_filter=self.f_vad.isChecked(),
            min_utterance_ms=self.f_min_ms.value(),
            cleanup_provider=self.f_provider.currentText().strip().lower(),
            anthropic_api_key=self.f_api_key.text(),
            anthropic_model=self.f_anthropic_model.text().strip() or "claude-haiku-4-5",
            ollama_model=self.f_ollama_model.text().strip() or "llama3.2:3b",
            ollama_base_url=self.f_ollama_url.text().strip() or "http://localhost:11434",
            cleanup_timeout_s=float(self.f_timeout.value()),
            dictionary=dictionary,
            pill_scale=self.f_scale.currentData() or "m",
            pill_opacity=self.f_opacity.value() / 100.0,
            pill_show_waveform=self.f_wave.isChecked(),
        )

    def _apply(self) -> None:
        new_settings = self._collect()
        self._settings = new_settings
        self.status.setText("APPLIED")
        self.applied.emit(new_settings)

    def closeEvent(self, e) -> None:
        e.ignore()
        self.hide()
