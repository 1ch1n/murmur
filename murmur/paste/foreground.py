from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForegroundWindow:
    process: str    # e.g. "Code.exe", "slack.exe", "chrome.exe"
    title: str      # window title
    category: str   # mapped category: code / chat / mail / doc / terminal / browser / other


# Process-name -> category. Lowercased exe basename match.
_CATEGORY_MAP: dict[str, str] = {
    # editors
    "code.exe": "code",
    "cursor.exe": "code",
    "windsurf.exe": "code",
    "devenv.exe": "code",
    "rider64.exe": "code",
    "pycharm64.exe": "code",
    "idea64.exe": "code",
    "webstorm64.exe": "code",
    "sublime_text.exe": "code",
    "notepad++.exe": "code",
    # terminals
    "windowsterminal.exe": "terminal",
    "wt.exe": "terminal",
    "cmd.exe": "terminal",
    "powershell.exe": "terminal",
    "pwsh.exe": "terminal",
    "conemu64.exe": "terminal",
    "alacritty.exe": "terminal",
    # chat
    "slack.exe": "chat",
    "discord.exe": "chat",
    "teams.exe": "chat",
    "ms-teams.exe": "chat",
    "telegram.exe": "chat",
    "whatsapp.exe": "chat",
    "signal.exe": "chat",
    "zoom.exe": "chat",
    # mail
    "outlook.exe": "mail",
    "thunderbird.exe": "mail",
    "mailspring.exe": "mail",
    # docs / notes
    "winword.exe": "doc",
    "notion.exe": "doc",
    "obsidian.exe": "doc",
    "logseq.exe": "doc",
    "evernote.exe": "doc",
    "onenote.exe": "doc",
    # browsers
    "chrome.exe": "browser",
    "firefox.exe": "browser",
    "msedge.exe": "browser",
    "brave.exe": "browser",
    "arc.exe": "browser",
    "vivaldi.exe": "browser",
    "opera.exe": "browser",
}


def _categorize(process: str) -> str:
    return _CATEGORY_MAP.get(process.lower(), "other")


def get_foreground_window() -> Optional[ForegroundWindow]:
    """Return the active window on Windows. None if unavailable / not supported."""
    try:
        import win32gui
        import win32process
    except ImportError:
        log.debug("pywin32 not available; foreground detection disabled")
        return None

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = _process_name(pid) or ""
        return ForegroundWindow(process=process, title=title, category=_categorize(process))
    except Exception:
        log.exception("failed to query foreground window")
        return None


def _process_name(pid: int) -> Optional[str]:
    """Return the basename of the executable for `pid`, lowercased."""
    if pid <= 0:
        return None

    # Try psutil if available (cleanest).
    try:
        import psutil  # type: ignore

        return psutil.Process(pid).name().lower()
    except Exception:
        pass

    # Fall back to win32 directly.
    try:
        import win32api
        import win32con
        import win32process

        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION
            if hasattr(win32con, "PROCESS_QUERY_LIMITED_INFORMATION")
            else win32con.PROCESS_QUERY_INFORMATION,
            False,
            pid,
        )
        try:
            path = win32process.GetModuleFileNameEx(handle, 0)
        finally:
            win32api.CloseHandle(handle)
        return os.path.basename(path).lower()
    except Exception:
        return None
