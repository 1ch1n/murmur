"""Read-aloud window: paste text, play, pause, jump by sentence, pick a voice."""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from murmur.overlay.styles import C, build_qss
from murmur.tts import (
    Span,
    edge_label,
    fetch_edge_voices,
    list_sapi_voices,
    load_cached_edge_voices,
    split_sentences,
)

log = logging.getLogger(__name__)


class _EdgeVoiceFetch(QObject):
    """Fetch the Edge voice list on a plain Python thread and report back
    on the GUI thread. asyncio + edge-tts stalls inside a QThread on
    Windows, so this deliberately avoids QThread."""

    done = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._future: Optional[Future] = None
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        if self._future is not None and not self._future.done():
            return
        self._future = self._pool.submit(fetch_edge_voices)
        self._timer.start()

    def isRunning(self) -> bool:
        return self._future is not None and not self._future.done()

    def _poll(self) -> None:
        if self._future is None or not self._future.done():
            return
        self._timer.stop()
        try:
            voices = self._future.result()
        except Exception:
            log.warning("edge voice list unavailable (offline?)")
            voices = []
        self.done.emit(voices)


class ReaderWindow(QMainWindow):
    """Owns no audio. Emits intents; the app drives the ReaderWorker and
    calls back into `on_sentence` / `on_state` so the window stays in sync
    whether playback was started here or from the F9 hotkey."""

    play_requested = Signal(list, int)     # sentences, start index
    toggle_requested = Signal()
    stop_requested = Signal()
    seek_requested = Signal(int)
    next_requested = Signal()
    prev_requested = Signal()
    rate_changed = Signal(int)
    voice_changed = Signal(str)

    def __init__(self, rate: int = 0, voice: str = ""):
        super().__init__()
        self.setWindowTitle("MURMUR: READ ALOUD")
        self.resize(680, 520)
        self.setStyleSheet(build_qss("archive"))
        self._spans: List[Span] = []
        self._loaded_text = ""
        self._current = -1
        self._state = "stopped"
        self._voice = voice or ""
        self._edge_voices: List[dict] = load_cached_edge_voices()
        self._fetch: Optional[_EdgeVoiceFetch] = None
        self._build(rate)
        self._populate_voices()

    # --- ui ---------------------------------------------------------------

    def _build(self, rate: int) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("MURMUR  ·  READ ALOUD")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self.status = QLabel("")
        self.status.setObjectName("status")
        header.addWidget(self.status)
        root.addLayout(header)

        div = QFrame()
        div.setObjectName("divider")
        root.addWidget(div)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "Paste or type anything here, then press Play. "
            "Click into a sentence and press Play to start from there. "
            "F9 anywhere on the desktop reads the current selection."
        )
        self.text.textChanged.connect(self._on_text_changed)
        root.addWidget(self.text, 1)

        # voice row
        voice_row = QHBoxLayout()
        voice_row.setSpacing(8)
        voice_row.addWidget(QLabel("Voice"))
        self.voice = QComboBox()
        self.voice.setMinimumWidth(300)
        self.voice.currentIndexChanged.connect(self._on_voice_index)
        voice_row.addWidget(self.voice, 1)
        self.all_langs = QCheckBox("All languages")
        self.all_langs.toggled.connect(lambda _c: self._populate_voices())
        voice_row.addWidget(self.all_langs)
        self.refresh_btn = QPushButton("Refresh online voices")
        self.refresh_btn.clicked.connect(self._refresh_edge)
        voice_row.addWidget(self.refresh_btn)
        root.addLayout(voice_row)

        # transport row
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.prev_btn = QPushButton("Previous")
        self.prev_btn.clicked.connect(self.prev_requested.emit)
        controls.addWidget(self.prev_btn)

        self.play_btn = QPushButton("Play")
        self.play_btn.setObjectName("primary")
        self.play_btn.clicked.connect(self._on_play_clicked)
        controls.addWidget(self.play_btn)

        self.next_btn = QPushButton("Next")
        self.next_btn.clicked.connect(self.next_requested.emit)
        controls.addWidget(self.next_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        controls.addWidget(self.stop_btn)

        controls.addStretch()

        controls.addWidget(QLabel("Speed"))
        self.rate = QSpinBox()
        self.rate.setRange(-10, 10)
        self.rate.setValue(rate)
        self.rate.setToolTip("-10 slow, 0 normal, 10 fast")
        self.rate.valueChanged.connect(self.rate_changed.emit)
        controls.addWidget(self.rate)
        root.addLayout(controls)

        hint = QLabel(
            "Ctrl+Space play/pause   ·   Ctrl+Left / Ctrl+Right previous / next sentence   ·   Esc closes   ·   "
            "Windows voices work offline; Edge voices send the text to Microsoft."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        QShortcut(QKeySequence("Ctrl+Space"), self, self._on_play_clicked)
        QShortcut(QKeySequence("Ctrl+Left"), self, self.prev_requested.emit)
        QShortcut(QKeySequence("Ctrl+Right"), self, self.next_requested.emit)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)
        self._update_buttons()

    # --- voices -----------------------------------------------------------

    def _populate_voices(self) -> None:
        self.voice.blockSignals(True)
        self.voice.clear()
        self.voice.addItem("Windows default (offline)", userData="")
        for value, label in list_sapi_voices():
            self.voice.addItem(f"{label}  (offline)", userData=value)
        show_all = self.all_langs.isChecked()
        edge = [v for v in self._edge_voices if show_all or v["locale"].lower().startswith("en")]
        if edge:
            self.voice.insertSeparator(self.voice.count())
        for v in edge:
            self.voice.addItem(edge_label(v["short_name"], v.get("gender", "")), userData=v["short_name"])
        # keep the configured voice selectable even if it is not in the list
        if self._voice and self.voice.findData(self._voice) < 0:
            self.voice.addItem(self._voice, userData=self._voice)
        idx = self.voice.findData(self._voice)
        self.voice.setCurrentIndex(max(0, idx))
        self.voice.blockSignals(False)
        if not self._edge_voices:
            self.refresh_btn.setText("Load online voices")

    def _on_voice_index(self, _i: int) -> None:
        value = self.voice.currentData()
        if value is None or value == self._voice:
            return
        self._voice = value
        self.voice_changed.emit(value)

    def _refresh_edge(self) -> None:
        if self._fetch is not None and self._fetch.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Loading…")
        if self._fetch is None:
            self._fetch = _EdgeVoiceFetch(self)
            self._fetch.done.connect(self._on_edge_fetched)
        self._fetch.start()

    def _on_edge_fetched(self, voices: list) -> None:
        self.refresh_btn.setEnabled(True)
        if voices:
            self._edge_voices = voices
            self.refresh_btn.setText("Refresh online voices")
            self._populate_voices()
            self.status.setText(f"{len(voices)} online voices")
        else:
            self.refresh_btn.setText("Load online voices")
            self.status.setText("online voices unavailable")

    def set_voice(self, voice: str) -> None:
        self._voice = voice or ""
        self._populate_voices()

    # --- public: called by the app ----------------------------------------

    def open_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.text.setFocus()
        if not self._edge_voices:
            self._refresh_edge()

    def set_text(self, text: str) -> None:
        """Load text (from F9 selection) without starting playback."""
        self.text.blockSignals(True)
        self.text.setPlainText(text)
        self.text.blockSignals(False)
        self._reload_spans()

    def on_sentence(self, idx: int) -> None:
        self._current = idx
        self._highlight(idx)
        if self._spans:
            self.status.setText(f"{idx + 1} / {len(self._spans)}")

    def on_state(self, state: str) -> None:
        self._state = state
        if state == "stopped":
            self._current = -1
            self._highlight(-1)
            self.status.setText("")
        elif state == "paused":
            self.status.setText(f"paused  {self._current + 1} / {len(self._spans)}" if self._spans else "paused")
        self._update_buttons()

    def sentences(self) -> List[str]:
        return [s[2] for s in self._spans]

    # --- internals --------------------------------------------------------

    def _on_text_changed(self) -> None:
        self._loaded_text = ""       # forces a reload on next Play

    def _reload_spans(self) -> None:
        txt = self.text.toPlainText()
        self._spans = split_sentences(txt)
        self._loaded_text = txt

    def _sentence_at_cursor(self) -> int:
        pos = self.text.textCursor().position()
        for i, (a, b, _) in enumerate(self._spans):
            if a <= pos <= b:
                return i
        return 0

    def _on_play_clicked(self) -> None:
        txt = self.text.toPlainText()
        if txt != self._loaded_text or not self._spans:
            self._reload_spans()
            if not self._spans:
                self.status.setText("nothing to read")
                return
            self.play_requested.emit(self.sentences(), self._sentence_at_cursor())
            return
        if self._state == "stopped":
            self.play_requested.emit(self.sentences(), self._sentence_at_cursor())
        else:
            self.toggle_requested.emit()

    def _update_buttons(self) -> None:
        self.play_btn.setText("Pause" if self._state == "playing" else ("Resume" if self._state == "paused" else "Play"))
        active = self._state != "stopped"
        self.stop_btn.setEnabled(active)
        self.prev_btn.setEnabled(bool(self._spans))
        self.next_btn.setEnabled(bool(self._spans))

    def _highlight(self, idx: int) -> None:
        selections = []
        if 0 <= idx < len(self._spans):
            a, b, _ = self._spans[idx]
            cur = QTextCursor(self.text.document())
            cur.setPosition(a)
            cur.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            bg = QColor(C.PURPLE_DIM)
            bg.setAlpha(110)
            fmt.setBackground(bg)
            fmt.setForeground(QColor(C.TEXT_WARM))
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format = fmt
            selections.append(sel)
            if not self.text.hasFocus():
                view = QTextCursor(self.text.document())
                view.setPosition(a)
                self.text.setTextCursor(view)
        self.text.setExtraSelections(selections)

    def closeEvent(self, e) -> None:
        e.ignore()
        self.hide()
