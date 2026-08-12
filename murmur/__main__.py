from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from murmur.app import MurmurApp


def _already_running() -> bool:
    """True if another `python -m murmur` process exists (e.g. the desktop
    shortcut was double-clicked while MURMUR is already in the tray)."""
    import psutil

    me = os.getpid()
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if p.info["pid"] == me:
                continue
            cmd = p.info.get("cmdline") or []
            if "-m" in cmd and "murmur" in cmd:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def main() -> int:
    if _already_running():
        print("MURMUR is already running (check the system tray).", file=sys.stderr)
        return 0

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("MURMUR")
    qt_app.setQuitOnLastWindowClosed(False)  # tray-resident
    qt_app.setStyle("Fusion")

    app = MurmurApp(qt_app)
    app.start()

    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
