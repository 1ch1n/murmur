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
RESTORE_CLIPBOARD_DELAY_MS = 800


def paste_text(text: str, restore_previous: bool = True) -> None:
    """Set the clipboard to `text`, send Ctrl+V, optionally restore prior clipboard."""
    if not text:
        return

    cb = QApplication.clipboard()
    previous = cb.text() if restore_previous else None

    cb.setText(text)

    # small delay before injecting the keystroke
    time.sleep(PASTE_KEYSTROKE_DELAY_MS / 1000)
    try:
        keyboard.send("ctrl+v")
    except Exception as e:
        log.exception("failed to send paste keystroke: %s", e)

    if restore_previous and previous is not None:
        # Restore on the Qt event loop to avoid blocking the caller.
        QTimer.singleShot(
            RESTORE_CLIPBOARD_DELAY_MS,
            lambda: cb.setText(previous),
        )


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
