from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from murmur.archive import Archive, Transcript
from murmur.overlay.stats_view import StatsView
from murmur.overlay.styles import C, build_qss

log = logging.getLogger(__name__)


class TranscriptCard(QFrame):
    clicked = Signal(Transcript)
    copy_clicked = Signal(str)
    delete_clicked = Signal(str)

    def __init__(self, t: Transcript):
        super().__init__()
        self.transcript = t
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(70)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)

        try:
            dt = datetime.fromisoformat(self.transcript.timestamp)
            date_str = dt.strftime("%b %d  %H:%M")
        except Exception:
            date_str = self.transcript.timestamp[:16]

        date_lbl = QLabel(date_str.upper())
        date_lbl.setStyleSheet(f"color: {C.PURPLE}; font-size: 9px; letter-spacing: 2px;")
        top.addWidget(date_lbl)

        words_lbl = QLabel(f"{self.transcript.word_count}W")
        words_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 9px; letter-spacing: 1px;")
        top.addWidget(words_lbl)

        top.addStretch()

        copy_btn = QPushButton("COPY")
        copy_btn.setObjectName("small")
        copy_btn.setFixedWidth(50)
        copy_btn.clicked.connect(lambda: self.copy_clicked.emit(self.transcript.text))
        top.addWidget(copy_btn)

        del_btn = QPushButton("×")
        del_btn.setObjectName("small")
        del_btn.setFixedWidth(26)
        del_btn.setStyleSheet(f"color: {C.TEXT_DIM}; border-color: {C.BORDER};")
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(self.transcript.id))
        top.addWidget(del_btn)

        layout.addLayout(top)

        preview = self.transcript.text[:110].replace("\n", " ")
        if len(self.transcript.text) > 110:
            preview += "…"
        preview_lbl = QLabel(preview)
        preview_lbl.setStyleSheet(f"color: {C.TEXT}; font-size: 11px; letter-spacing: 0;")
        preview_lbl.setWordWrap(True)
        layout.addWidget(preview_lbl)

    def set_selected(self, sel: bool) -> None:
        self.setProperty("selected", "true" if sel else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.transcript)


class ArchiveWindow(QMainWindow):
    """History browser. Persists across show/hide; safe to open from tray repeatedly."""

    def __init__(self, archive: Archive, hotkey_label: str = ""):
        super().__init__()
        self.archive = archive
        self.hotkey_label = hotkey_label
        self._selected_card: Optional[TranscriptCard] = None
        self._cards: list[TranscriptCard] = []

        self.setWindowTitle("MURMUR: HISTORY")
        self.resize(820, 600)
        self.setStyleSheet(build_qss("archive"))
        self._build()
        self._refresh()

    # --- ui scaffolding -------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        # header
        header = QHBoxLayout()
        title = QLabel("MURMUR  ·  HISTORY")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self.count_lbl = QLabel("0")
        self.count_lbl.setObjectName("status")
        header.addWidget(self.count_lbl)
        if self.hotkey_label:
            tip = QLabel(f"  HOLD {self.hotkey_label.upper()} TO DICTATE")
            tip.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 9px; letter-spacing: 2px;")
            header.addWidget(tip)
        root.addLayout(header)

        # search + actions
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("SEARCH TRANSCRIPTS…")
        self.search.textChanged.connect(self._refresh)
        bar.addWidget(self.search, 1)

        export_btn = QPushButton("EXPORT")
        export_btn.clicked.connect(self._export)
        bar.addWidget(export_btn)

        clear_btn = QPushButton("CLEAR ALL")
        clear_btn.setObjectName("danger")
        clear_btn.clicked.connect(self._clear_all)
        bar.addWidget(clear_btn)
        root.addLayout(bar)

        divider = QFrame()
        divider.setObjectName("divider")
        root.addWidget(divider)

        # tabs: HISTORY (list+detail) and STATS
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_history_tab(), "HISTORY")
        self.stats_view = StatsView()
        self.tabs.addTab(self.stats_view, "STATS")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)

        # status row
        self.status = QLabel("READY")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        # shortcuts
        QShortcut(QKeySequence("Ctrl+F"), self, self.search.setFocus)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.hide)

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        body = QHBoxLayout(page)
        body.setContentsMargins(0, 8, 0, 0)
        body.setSpacing(12)

        # left list
        left = QVBoxLayout()
        left.setSpacing(0)
        list_label = QLabel("TRANSCRIPTS")
        left.addWidget(list_label)

        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setMinimumWidth(340)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 6, 0, 6)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch()
        self.list_scroll.setWidget(self.list_container)
        left.addWidget(self.list_scroll, 1)

        left_w = QWidget()
        left_w.setLayout(left)
        body.addWidget(left_w, 0)

        # right detail
        right = QVBoxLayout()
        right.setSpacing(8)

        detail_header = QHBoxLayout()
        self.detail_label = QLabel("DETAIL")
        detail_header.addWidget(self.detail_label)
        detail_header.addStretch()

        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 9px; letter-spacing: 2px;")
        detail_header.addWidget(self.detail_meta)
        right.addLayout(detail_header)

        self.detail = QTextEdit()
        self.detail.setPlaceholderText("Select a transcript to view…")
        self.detail.setReadOnly(False)  # let user edit/copy snippets freely
        right.addWidget(self.detail, 1)

        detail_actions = QHBoxLayout()
        detail_actions.addStretch()
        self.copy_detail_btn = QPushButton("COPY")
        self.copy_detail_btn.setObjectName("accent")
        self.copy_detail_btn.clicked.connect(self._copy_detail)
        self.copy_detail_btn.setEnabled(False)
        detail_actions.addWidget(self.copy_detail_btn)

        self.delete_detail_btn = QPushButton("DELETE")
        self.delete_detail_btn.setObjectName("danger")
        self.delete_detail_btn.clicked.connect(self._delete_detail)
        self.delete_detail_btn.setEnabled(False)
        detail_actions.addWidget(self.delete_detail_btn)
        right.addLayout(detail_actions)

        right_w = QWidget()
        right_w.setLayout(right)
        body.addWidget(right_w, 1)

        return page

    def _on_tab_changed(self, index: int) -> None:
        # Tab 1 = STATS, refresh aggregates whenever it's shown.
        if index == 1:
            self.stats_view.refresh(self.archive.all())

    # --- state ----------------------------------------------------------

    def _refresh(self) -> None:
        q = self.search.text().strip()
        items = self.archive.search(q) if q else self.archive.all()

        # nuke existing cards (but keep the trailing stretch)
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

        self._cards = []
        self._selected_card = None

        if not items:
            empty = QLabel("NO TRANSCRIPTS YET" if not q else "NO MATCHES")
            empty.setObjectName("empty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                f"color: {C.TEXT_DIM}; font-size: 11px; letter-spacing: 3px; padding: 40px;"
            )
            self.list_layout.insertWidget(0, empty)
        else:
            for t in items:
                card = TranscriptCard(t)
                card.clicked.connect(self._on_card_clicked)
                card.copy_clicked.connect(self._copy_text)
                card.delete_clicked.connect(self._delete_transcript)
                self.list_layout.insertWidget(self.list_layout.count() - 1, card)
                self._cards.append(card)

        total = len(self.archive)
        if q:
            self.count_lbl.setText(f"{len(items)} / {total}")
        else:
            self.count_lbl.setText(f"{total}")

        if not items:
            self.detail.clear()
            self.detail_meta.setText("")
            self.copy_detail_btn.setEnabled(False)
            self.delete_detail_btn.setEnabled(False)

    def _on_card_clicked(self, t: Transcript) -> None:
        # find & mark selected
        for c in self._cards:
            sel = c.transcript.id == t.id
            c.set_selected(sel)
            if sel:
                self._selected_card = c

        self.detail.setPlainText(t.text)
        try:
            dt = datetime.fromisoformat(t.timestamp)
            date_str = dt.strftime("%b %d %Y  %H:%M:%S").upper()
        except Exception:
            date_str = t.timestamp.upper()
        self.detail_meta.setText(f"{date_str}  ·  {t.word_count}W  ·  {t.id}")
        self.copy_detail_btn.setEnabled(True)
        self.delete_detail_btn.setEnabled(True)

    def _copy_detail(self) -> None:
        text = self.detail.toPlainText().strip() or (
            self._selected_card.transcript.text if self._selected_card else ""
        )
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._flash_status("COPIED")

    def _delete_detail(self) -> None:
        if not self._selected_card:
            return
        self._delete_transcript(self._selected_card.transcript.id, confirm=True)

    def _copy_text(self, text: str) -> None:
        if not text:
            return
        QApplication.clipboard().setText(text)
        self._flash_status("COPIED")

    def _delete_transcript(self, transcript_id: str, confirm: bool = True) -> None:
        if confirm:
            r = QMessageBox.question(self, "Delete", "Delete this transcript?")
            if r != QMessageBox.StandardButton.Yes:
                return
        if self.archive.delete(transcript_id):
            self._flash_status("DELETED")
            self._refresh()

    def _clear_all(self) -> None:
        if len(self.archive) == 0:
            return
        r = QMessageBox.question(
            self,
            "Clear",
            f"Delete all {len(self.archive)} transcripts? This cannot be undone.",
        )
        if r == QMessageBox.StandardButton.Yes:
            self.archive.clear()
            self._flash_status("CLEARED")
            self._refresh()

    def _export(self) -> None:
        items = self.archive.all()
        if not items:
            self._flash_status("NOTHING TO EXPORT")
            return
        default_name = datetime.now().strftime("murmur_export_%Y%m%d_%H%M%S.txt")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export transcripts", default_name, "Text (*.txt);;JSON (*.json)"
        )
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                import json as _json
                from dataclasses import asdict
                payload = _json.dumps([asdict(t) for t in items], indent=2, ensure_ascii=False)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(payload)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    for t in items:
                        f.write(f"--- {t.timestamp}  ({t.word_count}w) ---\n{t.text}\n\n")
            self._flash_status(f"EXPORTED {len(items)}")
        except Exception as e:
            log.exception("export failed")
            self._flash_status(f"EXPORT FAILED: {e!s}"[:50], error=True)

    def _flash_status(self, msg: str, error: bool = False) -> None:
        self.status.setText(msg.upper())
        self.status.setStyleSheet(
            f"color: {C.RED if error else C.PURPLE}; font-size: 10px; letter-spacing: 1px;"
        )
        QTimer.singleShot(1800, lambda: self.status.setText("READY"))

    # --- public ---------------------------------------------------------

    def open_window(self) -> None:
        self._refresh()
        if self.tabs.currentIndex() == 1:
            self.stats_view.refresh(self.archive.all())
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def on_new_transcript(self) -> None:
        """Called externally after a new transcript is archived."""
        if self.isVisible():
            self._refresh()
            if self.tabs.currentIndex() == 1:
                self.stats_view.refresh(self.archive.all())

    # close = hide, not quit
    def closeEvent(self, e) -> None:
        e.ignore()
        self.hide()
