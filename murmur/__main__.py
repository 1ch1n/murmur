from __future__ import annotations

import os
import sys



def _is_murmur_cmdline(cmd: list[str]) -> bool:
    """Match `python -m murmur` (adjacent tokens) or the pip-installed
    `murmur` console script — not unrelated processes like
    `python -m pip install murmur`."""
    if not cmd:
        return False
    # Headless file transcriptions (`murmur --file ...`) are not the tray app.
    if any(a in ("--file", "-f") or a.startswith("--file=") for a in cmd[1:]):
        return False
    exe = os.path.basename(cmd[0]).lower()
    if exe in ("murmur.exe", "murmur"):
        return True
    return any(a == "-m" and b == "murmur" for a, b in zip(cmd, cmd[1:]))


def _already_running() -> bool:
    """True if another MURMUR process exists (e.g. the desktop shortcut was
    double-clicked while MURMUR is already in the tray)."""
    import psutil

    me = os.getpid()
    # A wrapper that launched us (`cmd /c start ... python -m murmur`, a
    # .bat, a PowerShell Start-Process) carries our own arguments in ITS
    # command line. Ignore ancestors so they never count as a running copy.
    try:
        skip = {me, *(a.pid for a in psutil.Process(me).parents())}
    except psutil.Error:
        skip = {me}
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if p.info["pid"] in skip:
                continue
            if _is_murmur_cmdline(p.info.get("cmdline") or []):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def main() -> int:
    from murmur.cli import build_parser, transcribe_file_cmd

    args = build_parser().parse_args()
    if args.file:
        return transcribe_file_cmd(args)

    if _already_running():
        print("MURMUR is already running (check the system tray).", file=sys.stderr)
        return 0

    from PySide6.QtWidgets import QApplication

    from murmur.app import MurmurApp

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("MURMUR")
    qt_app.setQuitOnLastWindowClosed(False)  # tray-resident
    qt_app.setStyle("Fusion")

    app = MurmurApp(qt_app)
    app.start()

    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
