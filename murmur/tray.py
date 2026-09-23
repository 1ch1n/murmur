from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QBrush, Qt
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from murmur.overlay.styles import C, build_qss


def _build_icon() -> QIcon:
    """Tray glyph: a purple needle on a dark disc, matching the widget."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(C.SURFACE)))
    p.drawEllipse(3, 3, 26, 26)
    p.setPen(QColor(C.BORDER_HI))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(3, 3, 26, 26)
    p.setPen(QColor(C.PURPLE))
    p.drawLine(16, 21, 23, 9)
    p.setBrush(QBrush(QColor(C.PURPLE)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(13, 18, 6, 6)
    p.end()
    return QIcon(pm)


class Tray(QObject):
    quit_requested = Signal()
    toggle_pill_requested = Signal()
    show_history_requested = Signal()
    show_settings_requested = Signal()
    transcribe_file_requested = Signal()
    read_aloud_requested = Signal()

    def __init__(self, hotkey_label: str):
        super().__init__()
        self.tray = QSystemTrayIcon(_build_icon())
        self.tray.setToolTip(f"MURMUR: hold {hotkey_label} to dictate")

        menu = QMenu()
        menu.setStyleSheet(build_qss("menu"))

        history = QAction("Show History", menu)
        history.triggered.connect(self.show_history_requested.emit)
        menu.addAction(history)

        settings = QAction("Settings…", menu)
        settings.triggered.connect(self.show_settings_requested.emit)
        menu.addAction(settings)

        read = QAction("Read Aloud…", menu)
        read.triggered.connect(self.read_aloud_requested.emit)
        menu.addAction(read)

        transcribe = QAction("Transcribe File…", menu)
        transcribe.triggered.connect(self.transcribe_file_requested.emit)
        menu.addAction(transcribe)

        toggle = QAction("Show/Hide Widget", menu)
        toggle.triggered.connect(self.toggle_pill_requested.emit)
        menu.addAction(toggle)

        menu.addSeparator()

        info = QAction(f"Hotkey: {hotkey_label}", menu)
        info.setEnabled(False)
        menu.addAction(info)

        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _on_activated(self, reason) -> None:
        # QSystemTrayIcon.ActivationReason: DoubleClick = 2
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_history_requested.emit()
