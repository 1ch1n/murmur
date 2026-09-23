from __future__ import annotations

from typing import Literal

from murmur.overlay.fonts import font_family


class C:
    """Palette. Near-black surfaces, warm off-white text, purple as the one accent."""

    BG = "#08080f"
    PANEL = "#0c0c16"
    SURFACE = "#10101c"
    CAPSULE = "#0e0e13"      # the widget and its panel
    INK = "#07070d"
    BORDER = "#1a1a28"
    BORDER_HI = "#252538"

    AMBER = "#ff6b35"
    AMBER_DIM = "#b34a25"
    TEAL = "#00d4aa"
    TEAL_DIM = "#009977"
    PURPLE = "#a855f7"       # the accent
    PURPLE_DIM = "#6d3aa1"
    RED = "#ff4d4d"          # recording lamp / error

    TEXT = "#c8c8d0"
    TEXT_WARM = "#ece7dc"    # readouts, highlights
    TEXT_DIM = "#5a5a6a"

    ACCENT = PURPLE
    ACCENT_DIM = PURPLE_DIM


QssKind = Literal["settings", "archive", "menu"]


def _base(ui: str, mono: str) -> str:
    return f"""
* {{ font-family: '{ui}', 'Segoe UI', sans-serif; }}

QMainWindow {{ background: {C.BG}; }}
QWidget {{ background: transparent; color: {C.TEXT}; }}

QLabel {{ color: {C.TEXT_DIM}; font-size: 11px; }}
QLabel#title {{ font-family: '{mono}', 'Consolas', monospace; color: {C.ACCENT}; font-size: 13px; letter-spacing: 3px; }}
QLabel#hint {{ color: {C.TEXT_DIM}; font-size: 11px; }}
QLabel#status {{ font-family: '{mono}', 'Consolas', monospace; color: {C.ACCENT}; font-size: 11px; }}
QLabel#empty {{ color: {C.TEXT_DIM}; font-size: 12px; }}

QTabWidget::pane {{ border: 1px solid {C.BORDER}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{
    font-family: '{mono}', 'Consolas', monospace;
    background: {C.SURFACE}; color: {C.TEXT_DIM}; padding: 7px 16px;
    font-size: 11px; letter-spacing: 1px; border: 1px solid {C.BORDER};
    border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{ background: {C.PANEL}; color: {C.TEXT_WARM}; border-color: {C.BORDER_HI}; }}
QTabBar::tab:hover {{ color: {C.TEXT_WARM}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 6px;
    color: {C.TEXT_WARM}; font-size: 12px; padding: 6px 9px;
    selection-background-color: {C.ACCENT_DIM};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {C.ACCENT_DIM};
}}

QPushButton {{
    background: {C.SURFACE}; border: 1px solid {C.BORDER}; border-radius: 6px;
    color: {C.TEXT}; font-size: 12px; font-weight: 500;
    padding: 7px 14px; min-width: 60px;
}}
QPushButton:hover {{ background: {C.BORDER}; border-color: {C.BORDER_HI}; color: {C.TEXT_WARM}; }}
QPushButton:pressed {{ background: {C.BORDER_HI}; }}
QPushButton#primary, QPushButton#accent {{ color: {C.TEXT_WARM}; background: {C.ACCENT_DIM}; border-color: {C.ACCENT_DIM}; }}
QPushButton#primary:hover, QPushButton#accent:hover {{ background: {C.ACCENT}; border-color: {C.ACCENT}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: {C.BG}; width: 6px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {C.BORDER_HI}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {C.ACCENT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QFrame#divider {{ background: {C.BORDER}; max-height: 1px; min-height: 1px; }}
"""


_SETTINGS_EXTRA = f"""
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {C.TEXT_DIM}; margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {C.PANEL}; border: 1px solid {C.BORDER}; color: {C.TEXT_WARM};
    selection-background-color: {C.ACCENT_DIM};
}}

QCheckBox {{ color: {C.TEXT_WARM}; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border: 1px solid {C.BORDER}; background: {C.PANEL};
    border-radius: 4px;
}}
QCheckBox::indicator:hover {{ border-color: {C.BORDER_HI}; }}
QCheckBox::indicator:checked {{ background: {C.ACCENT}; border-color: {C.ACCENT_DIM}; }}
"""


_ARCHIVE_EXTRA = f"""
QLineEdit {{ font-size: 13px; padding: 8px 11px; }}
QTextEdit {{ font-size: 13px; padding: 10px; line-height: 1.5; }}

QPushButton#small {{ min-width: 40px; padding: 5px 10px; font-size: 11px; }}
QPushButton#danger {{ color: {C.RED}; border-color: {C.BORDER}; }}
QPushButton#danger:hover {{ background: {C.RED}; color: {C.BG}; border-color: {C.RED}; }}

QFrame#card {{ background: {C.SURFACE}; border: 1px solid {C.BORDER}; border-radius: 8px; }}
QFrame#card:hover {{ border-color: {C.BORDER_HI}; background: {C.BORDER}; }}
QFrame#card[selected="true"] {{ border-color: {C.ACCENT_DIM}; background: {C.PANEL}; }}
"""


_MENU_EXTRA = f"""
QMenu {{
    background: {C.CAPSULE}; border: 1px solid {C.BORDER_HI}; border-radius: 8px; padding: 6px;
    color: {C.TEXT}; font-size: 12px;
}}
QMenu::item {{ padding: 7px 24px 7px 12px; background: transparent; border-radius: 5px; }}
QMenu::item:selected {{ background: {C.SURFACE}; color: {C.TEXT_WARM}; }}
QMenu::item:disabled {{ color: {C.TEXT_DIM}; }}
QMenu::separator {{ height: 1px; background: {C.BORDER}; margin: 5px 6px; }}
"""


def build_qss(kind: QssKind) -> str:
    """One stylesheet builder for every window. Font families resolve at call
    time so this works before and after register_fonts()."""
    qss = _base(font_family("ui"), font_family("mono"))
    if kind == "settings":
        return qss + _SETTINGS_EXTRA
    if kind == "archive":
        return qss + _ARCHIVE_EXTRA
    if kind == "menu":
        return qss + _MENU_EXTRA
    return qss
