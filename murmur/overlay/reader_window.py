"""Read-aloud window: paste text, play, pause, jump by sentence."""
from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
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
from murmur.tts import Span, split_sentences

log = logging.getLogger(__name__)


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

    def __init__(self, rate: int = 0):
        super().__init__()
        self.setWindowTitle("MURMUR: READ ALOUD")
        self.resize(640, 480)
        self.setStyleSheet(build_qss("archive"))
        self._spans: List[Span] = []
        self._loaded_text = ""
        self._current = -1
        self._state = "stopped"
        self._build(rate)

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
        self.rate.setToolTip("SAPI-style rate: -10 slow, 0 normal, 10 fast")
        self.rate.valueChanged.connect(self.rate_changed.emit)
        controls.addWidget(self.rate)
        root.addLayout(controls)

        hint = QLabel(
            "Ctrl+Space play/pause   ·   Ctrl+Left / Ctrl+Right previous / next sentence   ·   Esc closes"
        )
        hint.setObjectName("hint")
        root.addWidget(hint)

        QShortcut(QKeySequence("Ctrl+Space"), self, self._on_play_clicked)
        QShortcut(QKeySequence("Ctrl+Left"), self, self.prev_requested.emit)
        QShortcut(QKeySequence("Ctrl+Right"), self, self.next_requested.emit)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)
        self._update_buttons()

    # --- public: called by the app ----------------------------------------

    def open_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.text.setFocus()

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
            # keep the sentence in view without stealing the caret
            view = QTextCursor(self.text.document())
            view.setPosition(a)
            self.text.setTextCursor(view) if not self.text.hasFocus() else None
        self.text.setExtraSelections(selections)

    def closeEvent(self, e) -> None:
        e.ignore()
        self.hide()
