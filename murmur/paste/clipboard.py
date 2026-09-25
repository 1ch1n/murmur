from __future__ import annotations

import logging
import time

import keyboard
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

# Delay between writing the clipboard and sending Ctrl+V. Some apps lag at
# reading the clipboard after a programmatic write, so we give them a beat.
PASTE_KEYSTROKE_DELAY_MS = 60
# How long to keep polling the clipboard after sending Ctrl+C. Copy is
# handled by the target app, which can be slow to publish the data.
COPY_READBACK_TIMEOUT_MS = 700
COPY_POLL_INTERVAL_MS = 50
# Delay before restoring the previous clipboard contents. Long enough that
# the paste target has consumed our text, short enough not to interfere with
# normal copy operations downstream.
RESTORE_CLIPBOARD_DELAY_MS = 1500


def _set_clipboard_direct(text: str) -> bool:
    """Put `text` on the system clipboard as rendered data.

    Qt's setText hands the clipboard a live object that the pasting app has
    to fetch back from this process, which only works while our event loop
    is free. Writing through the Win32 API stores the bytes in the system
    itself, so the paste target never depends on us.
    """
    try:
        import win32clipboard

        for attempt in range(5):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                time.sleep(0.02)   # another app briefly owns it
        else:
            return False
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        log.exception("direct clipboard write failed; using Qt")
        return False


def _read_clipboard_text() -> str:
    try:
        return QApplication.clipboard().text() or ""
    except Exception:
        return ""


def _release_stuck_modifiers() -> None:
    """After a long push-to-talk hold the keyboard hook's idea of which
    modifiers are down can lag the OS. Release them all before Ctrl+V so
    the keystroke is a clean Ctrl+V and nothing else."""
    for name in ("right alt", "left alt", "alt", "right ctrl", "left ctrl", "ctrl",
                 "right shift", "left shift", "shift", "left windows", "right windows"):
        try:
            if keyboard.is_pressed(name):
                keyboard.release(name)
        except Exception:
            pass


def paste_text(text: str, restore_previous: bool = True) -> None:
    """Set the clipboard to `text`, send Ctrl+V, optionally restore the prior
    clipboard once the paste has certainly been consumed."""
    if not text:
        return

    previous = _read_clipboard_text() if restore_previous else None

    if not _set_clipboard_direct(text):
        QApplication.clipboard().setText(text)

    try:
        from murmur.paste.foreground import get_foreground_window

        fg = get_foreground_window()
        log.info("paste -> %s (%d chars)", fg.process if fg else "unknown", len(text))
    except Exception:
        pass

    time.sleep(PASTE_KEYSTROKE_DELAY_MS / 1000)
    _release_stuck_modifiers()
    try:
        keyboard.send("ctrl+v")
    except Exception as e:
        log.exception("failed to send paste keystroke: %s", e)

    if restore_previous and previous is not None and previous != text:
        def _restore() -> None:
            # Only put the old content back if nobody has copied anything
            # new in the meantime (including a paste that is still being read).
            if _read_clipboard_text() == text:
                if not _set_clipboard_direct(previous):
                    QApplication.clipboard().setText(previous)

        QTimer.singleShot(RESTORE_CLIPBOARD_DELAY_MS, _restore)


def copy_selection() -> str:
    """Copy the current selection via Ctrl+C and return it, restoring the prior clipboard."""
    cb = QApplication.clipboard()
    previous = cb.text()
    cb.clear()

    try:
        keyboard.send("ctrl+c")
    except Exception as e:
        log.exception("failed to send copy keystroke: %s", e)
        if previous:
            cb.setText(previous)
        return ""

    text = ""
    waited = 0
    while waited < COPY_READBACK_TIMEOUT_MS:
        time.sleep(COPY_POLL_INTERVAL_MS / 1000)
        waited += COPY_POLL_INTERVAL_MS
        text = cb.text()
        if text:
            break

    if previous:
        if text:
            QTimer.singleShot(
                RESTORE_CLIPBOARD_DELAY_MS,
                lambda: cb.setText(previous),
            )
        else:
            # Nothing was copied, put the user's clipboard back right away.
            cb.setText(previous)
    return text
