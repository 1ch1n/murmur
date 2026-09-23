"""Import smoke test, verifies the package wires together without launching the GUI."""
from __future__ import annotations


def test_imports() -> None:
    from murmur import __version__
    from murmur.config import Settings, load_settings
    from murmur.stt.engine import WhisperEngine
    from murmur.audio.capture import AudioCapture
    from murmur.hotkeys.windows import PushToTalkHotkey
    from murmur.paste.clipboard import paste_text
    from murmur.paste.foreground import get_foreground_window, ForegroundWindow
    from murmur.cleanup.prompts import CleanupContext, build_prompt
    from murmur.cleanup.providers import make_provider, DisabledProvider, AnthropicProvider, OllamaProvider
    from murmur.overlay.pill import Pill, PillState
    from murmur.overlay.waveform import Waveform
    from murmur.overlay.styles import C
    from murmur.overlay.archive_window import ArchiveWindow, TranscriptCard
    from murmur.overlay.settings_window import SettingsWindow
    from murmur.overlay.stats_view import StatsView
    from murmur.archive import Archive, Transcript
    from murmur import sounds
    from murmur.tray import Tray
    from murmur.app import MurmurApp

    assert __version__
    assert Settings().model == "small.en"
    assert Settings().cleanup_provider == "disabled"
    assert Settings().raw_modifier == "shift"
    assert PillState.IDLE.value == "idle"
    assert C.AMBER.startswith("#")


def test_cleanup_prompt() -> None:
    from murmur.cleanup.prompts import CleanupContext, build_prompt

    ctx = CleanupContext(
        target_app="code.exe",
        target_category="code",
        dictionary=("MURMUR", "faster-whisper"),
    )
    prompt = build_prompt("um so I want to like, write a function", ctx)
    assert "code editor" in prompt.lower()
    assert "MURMUR" in prompt
    assert "faster-whisper" in prompt
    assert "code.exe" in prompt
    assert "Raw transcript:" in prompt


def test_cleanup_disabled_passthrough() -> None:
    from murmur.cleanup.prompts import CleanupContext
    from murmur.cleanup.providers import make_provider

    p = make_provider("disabled")
    assert p.name == "disabled"
    out = p.cleanup("hello world", CleanupContext())
    assert out == "hello world"


def test_archive_roundtrip(tmp_path) -> None:
    from murmur.archive import Archive

    a = Archive(path=tmp_path / "arch.json")
    assert len(a) == 0

    t1 = a.add("hello world this is a test")
    t2 = a.add("second transcript here")
    assert len(a) == 2
    assert t1.word_count == 6
    assert t2.word_count == 3

    # newest first
    items = a.all()
    assert items[0].id == t2.id
    assert items[1].id == t1.id

    # search
    found = a.search("second")
    assert len(found) == 1
    assert found[0].id == t2.id

    # case-insensitive
    assert len(a.search("HELLO")) == 1

    # persistence across instances
    b = Archive(path=tmp_path / "arch.json")
    assert len(b) == 2

    # delete
    assert a.delete(t1.id) is True
    assert a.delete("nonexistent") is False
    assert len(a) == 1

    a.clear()
    assert len(a) == 0
