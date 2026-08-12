from __future__ import annotations

import logging

import keyboard
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class PushToTalkHotkey(QObject):
    """Global press-and-hold hotkey.

    Press the chosen key to start recording, release to stop.
    Uses the `keyboard` library hook (passive listener, does not suppress input).
    Callbacks fire on the keyboard library's listener thread, so they're
    marshalled through Qt signals to the GUI thread.
    """

    pressed = Signal()
    released = Signal(bool)   # bool = raw_modifier_held

    def __init__(self, key_name: str, raw_modifier: str = "shift"):
        super().__init__()
        self.key_name = key_name
        self.raw_modifier = raw_modifier
        self._down = False
        self._press_handle = None
        self._release_handle = None

    def start(self) -> None:
        log.info("registering push-to-talk hotkey: %s", self.key_name)
        self._press_handle = keyboard.on_press_key(
            self.key_name, self._on_press, suppress=False
        )
        self._release_handle = keyboard.on_release_key(
            self.key_name, self._on_release, suppress=False
        )

    def stop(self) -> None:
        if self._press_handle is not None:
            keyboard.unhook(self._press_handle)
            self._press_handle = None
        if self._release_handle is not None:
            keyboard.unhook(self._release_handle)
            self._release_handle = None

    # Alt released with no other key in between makes Windows move focus
    # to the focused app's menu bar — which yanks the caret out of the
    # textbox we're about to paste into. Injecting a dummy F24 while Alt
    # is held makes the press "not lone", so menu activation never fires.
    _ALT_NAMES = {"alt", "left alt", "right alt", "alt gr"}

    # `keyboard.on_press_key` fires repeatedly while the key is held;
    # gate on _down so we only emit pressed/released once per hold.
    def _on_press(self, _event) -> None:
        if self._down:
            return
        self._down = True
        if self.key_name in self._ALT_NAMES:
            try:
                keyboard.send("f24")
            except Exception:
                log.exception("failed to send menu-suppression key")
        self.pressed.emit()

    def _on_release(self, _event) -> None:
        if not self._down:
            return
        self._down = False
        raw_held = False
        if self.raw_modifier:
            try:
                raw_held = keyboard.is_pressed(self.raw_modifier)
            except Exception:
                raw_held = False
        self.released.emit(raw_held)


class TapHotkey(QObject):
    """Global tap hotkey — fires once per physical press.

    Gated on press/release like PushToTalkHotkey, because Windows key
    autorepeat delivers a stream of press events while the key is held.
    Same marshalling story too: the keyboard library calls back on its
    listener thread, and the Qt signal hops to the GUI thread.
    """

    triggered = Signal()

    def __init__(self, key_name: str):
        super().__init__()
        self.key_name = key_name
        self._down = False
        self._press_handle = None
        self._release_handle = None

    def start(self) -> None:
        log.info("registering tap hotkey: %s", self.key_name)
        self._press_handle = keyboard.on_press_key(
            self.key_name, self._on_press, suppress=False
        )
        self._release_handle = keyboard.on_release_key(
            self.key_name, self._on_release, suppress=False
        )

    def stop(self) -> None:
        if self._press_handle is not None:
            keyboard.unhook(self._press_handle)
            self._press_handle = None
        if self._release_handle is not None:
            keyboard.unhook(self._release_handle)
            self._release_handle = None

    def _on_press(self, _event) -> None:
        if self._down:
            return
        self._down = True
        self.triggered.emit()

    def _on_release(self, _event) -> None:
        self._down = False
