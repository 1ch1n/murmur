from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from murmur.archive import Transcript
from murmur.overlay.styles import C


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def _fmt_ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


class StatTile(QFrame):
    """One stat: large number + small label."""

    def __init__(self, label: str, value: str, accent: str = C.AMBER):
        super().__init__()
        self.accent = accent
        self.setObjectName("tile")
        self.setStyleSheet(f"""
            QFrame#tile {{
                background: {C.SURFACE};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
            }}
            QFrame#tile:hover {{ border-color: {accent}; }}
        """)
        self.setMinimumHeight(74)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)

        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet(
            f"color: {accent}; font-size: 22px; font-weight: 300; letter-spacing: 1px;"
        )
        lay.addWidget(self.value_lbl)

        self.label_lbl = QLabel(label.upper())
        self.label_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; font-size: 9px; letter-spacing: 3px;"
        )
        lay.addWidget(self.label_lbl)

    def set_value(self, value: str) -> None:
        self.value_lbl.setText(value)


class _Bar(QFrame):
    """Horizontal bar visualizing one item in a top-N list."""

    def __init__(self, label: str, count: int, fraction: float, color: str = C.PURPLE):
        super().__init__()
        self.setMinimumHeight(28)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(8)

        name = QLabel(label)
        name.setFixedWidth(160)
        name.setStyleSheet(f"color: {C.TEXT}; font-size: 11px; letter-spacing: 0;")
        lay.addWidget(name)

        # bar container
        bar_track = QFrame()
        bar_track.setStyleSheet(f"background: {C.PANEL}; border-radius: 2px;")
        bar_track.setFixedHeight(14)
        bar_track_lay = QHBoxLayout(bar_track)
        bar_track_lay.setContentsMargins(0, 0, 0, 0)
        fill = QFrame()
        width_pct = max(2, int(fraction * 100))
        fill.setStyleSheet(f"background: {color}; border-radius: 2px;")
        bar_track_lay.addWidget(fill, width_pct)
        spacer = QFrame()
        spacer.setStyleSheet("background: transparent;")
        bar_track_lay.addWidget(spacer, max(0, 100 - width_pct))

        lay.addWidget(bar_track, 1)

        cnt = QLabel(_fmt_int(count))
        cnt.setFixedWidth(50)
        cnt.setAlignment(Qt.AlignmentFlag.AlignRight)
        cnt.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 10px; letter-spacing: 1px;")
        lay.addWidget(cnt)


class StatsView(QWidget):
    """Aggregate stats panel for the archive."""

    def __init__(self):
        super().__init__()
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(12)

        # tiles row
        tiles = QHBoxLayout()
        tiles.setSpacing(10)

        self.t_total = StatTile("transcripts", "0", accent=C.AMBER)
        tiles.addWidget(self.t_total)

        self.t_words = StatTile("words dictated", "0", accent=C.TEAL)
        tiles.addWidget(self.t_words)

        self.t_audio = StatTile("audio captured", "0s", accent=C.PURPLE)
        tiles.addWidget(self.t_audio)

        self.t_today = StatTile("today", "0", accent=C.AMBER)
        tiles.addWidget(self.t_today)

        self.t_avg_stt = StatTile("avg stt", "n/a", accent=C.TEAL)
        tiles.addWidget(self.t_avg_stt)

        self.t_avg_clean = StatTile("avg cleanup", "n/a", accent=C.PURPLE)
        tiles.addWidget(self.t_avg_clean)

        root.addLayout(tiles)

        # body: two columns of top-N lists
        body = QHBoxLayout()
        body.setSpacing(16)

        # Top apps
        left = QVBoxLayout()
        left.addWidget(self._section_header("TOP TARGET APPS"))
        self.apps_container = QWidget()
        self.apps_layout = QVBoxLayout(self.apps_container)
        self.apps_layout.setContentsMargins(0, 6, 0, 0)
        self.apps_layout.setSpacing(2)
        self.apps_layout.addStretch()
        left.addWidget(self.apps_container, 1)

        left_w = QWidget()
        left_w.setLayout(left)
        body.addWidget(left_w, 1)

        # Top categories
        right = QVBoxLayout()
        right.addWidget(self._section_header("BY CATEGORY"))
        self.cat_container = QWidget()
        self.cat_layout = QVBoxLayout(self.cat_container)
        self.cat_layout.setContentsMargins(0, 6, 0, 0)
        self.cat_layout.setSpacing(2)
        self.cat_layout.addStretch()
        right.addWidget(self.cat_container, 1)

        right_w = QWidget()
        right_w.setLayout(right)
        body.addWidget(right_w, 1)

        root.addLayout(body, 1)

    def _section_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; font-size: 10px; letter-spacing: 3px;"
        )
        return lbl

    # --- update ---------------------------------------------------------

    def refresh(self, items: Iterable[Transcript]) -> None:
        items = list(items)

        total = len(items)
        words = sum(t.word_count for t in items)
        audio_s = sum(t.audio_duration_s or 0 for t in items)
        today = sum(1 for t in items if _is_today(t.timestamp))

        stt_values = [t.transcribe_ms for t in items if t.transcribe_ms]
        cleanup_values = [t.cleanup_ms for t in items if t.cleanup_ms]
        avg_stt = (sum(stt_values) / len(stt_values)) if stt_values else 0
        avg_cleanup = (sum(cleanup_values) / len(cleanup_values)) if cleanup_values else 0

        self.t_total.set_value(_fmt_int(total))
        self.t_words.set_value(_fmt_int(words))
        self.t_audio.set_value(_fmt_duration(audio_s))
        self.t_today.set_value(_fmt_int(today))
        self.t_avg_stt.set_value(_fmt_ms(avg_stt) if avg_stt else "n/a")
        self.t_avg_clean.set_value(_fmt_ms(avg_cleanup) if avg_cleanup else "n/a")

        # Top apps
        apps_counter = Counter(t.target_app for t in items if t.target_app)
        self._fill_bars(self.apps_layout, apps_counter.most_common(8), color=C.PURPLE)

        # Categories
        cats_counter = Counter(t.target_category or "other" for t in items)
        self._fill_bars(self.cat_layout, cats_counter.most_common(8), color=C.TEAL)

    def _fill_bars(self, layout: QVBoxLayout, pairs, color: str) -> None:
        # strip existing bars (preserve trailing stretch)
        while layout.count() > 1:
            item = layout.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()

        if not pairs:
            empty = QLabel("NO DATA YET")
            empty.setStyleSheet(
                f"color: {C.TEXT_DIM}; font-size: 10px; letter-spacing: 3px; padding: 20px 0;"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.insertWidget(0, empty)
            return

        max_count = max(c for _, c in pairs)
        for label, count in pairs:
            frac = count / max_count if max_count else 0
            layout.insertWidget(
                layout.count() - 1,
                _Bar(str(label), count, frac, color=color),
            )


def _is_today(timestamp: str) -> bool:
    try:
        dt = datetime.fromisoformat(timestamp)
    except Exception:
        return False
    return dt.date() == datetime.now().date()
