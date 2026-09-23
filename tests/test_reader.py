"""Read-aloud engine: command handling with a fake speaker (no audio)."""
from __future__ import annotations

import time

import pytest


class FakeReader:
    """Drive ReaderWorker's command handling without a thread or audio."""

    def __init__(self):
        from murmur.tts import ReaderWorker

        self.w = ReaderWorker(rate=0, voice="")
        self.events: list = []
        self.w.sentence_started.connect(lambda i: self.events.append(("sentence", i)))
        self.w.state_changed.connect(lambda s: self.events.append(("state", s)))
        self.w.finished_all.connect(lambda: self.events.append(("finished",)))

    def handle(self, name, *args):
        return self.w._handle((name, args))


def test_play_pause_resume_seek_stop(qapp):
    r = FakeReader()
    w = r.w
    r.handle("load", ["One.", "Two.", "Three."])
    assert not w.is_active

    assert r.handle("play", 0) is True
    assert w.is_playing and w.idx == 0
    assert ("state", "playing") in r.events

    assert r.handle("pause") is True
    assert w.is_paused
    assert r.handle("pause") is False          # idempotent
    assert r.handle("resume") is True
    assert w.is_playing

    assert r.handle("next") is True and w.idx == 1
    assert r.handle("next") is True and w.idx == 2
    assert r.handle("next") is True and w.idx == 2   # clamps at the end
    assert r.handle("prev") is True and w.idx == 1
    assert r.handle("seek", 0) is True and w.idx == 0
    assert r.handle("seek", 99) is True and w.idx == 2

    assert r.handle("stop") is True
    assert not w.is_active and w.idx == 0
    assert r.events[-1] == ("state", "stopped")

    # toggle from stopped starts; from playing pauses; from paused resumes
    assert r.handle("toggle") is True and w.is_playing
    assert r.handle("toggle") is True and w.is_paused
    assert r.handle("toggle") is True and w.is_playing

    # loading new text while playing stops playback
    r.handle("load", ["New."])
    assert not w.is_active and w.sentences == ["New."]


def test_worker_loop_advances_through_sentences(qapp):
    """Run the real thread with _speak stubbed out: it must emit each
    sentence in order, then finished_all, then go idle."""
    from PySide6.QtTest import QTest

    r = FakeReader()
    w = r.w
    spoken = []

    def fake_speak(idx):
        spoken.append(w.sentences[idx])
        time.sleep(0.01)
        return w._drain()

    w._speak = fake_speak
    w.start()
    try:
        w.load(["A.", "B.", "C."])
        w.play(0)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and ("finished",) not in r.events:
            QTest.qWait(20)
        assert spoken == ["A.", "B.", "C."]
        assert [e for e in r.events if e[0] == "sentence"] == [("sentence", 0), ("sentence", 1), ("sentence", 2)]
        assert r.events[-1] == ("finished",) or r.events[-2] == ("finished",)
        assert not w.is_active
    finally:
        w.quit_worker()
        w.wait(1000)
