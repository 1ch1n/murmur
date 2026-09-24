"""Phrase committer and the always-open capture, without a microphone."""
from __future__ import annotations

import numpy as np

SR = 16000


def test_find_commit_rules():
    from murmur.stt.streaming import Commit, find_commit

    def vad_from(spans):
        return lambda _a: spans

    # too short to bother
    assert find_commit(np.zeros(SR), vad_from([(0, SR)])) is None

    # speech then a pause: commit through the pause with padding
    since = np.zeros(4 * SR, dtype=np.float32)
    c = find_commit(since, vad_from([(int(0.2 * SR), int(3.0 * SR))]))
    assert isinstance(c, Commit) and c.has_speech
    assert int(3.0 * SR) < c.cut <= int(3.2 * SR)

    # still talking at the end, but an earlier gap exists after 2 s of audio
    c = find_commit(since, vad_from([(0, int(2.5 * SR)), (int(3.2 * SR), int(4.0 * SR))]))
    assert c is not None and c.has_speech and abs(c.cut - int(2.65 * SR)) < SR * 0.05

    # still talking, no usable gap: wait
    assert find_commit(since, vad_from([(0, int(4.0 * SR))])) is None

    # pure silence for a while: advance without transcribing
    c = find_commit(np.zeros(5 * SR, dtype=np.float32), vad_from([]))
    assert c is not None and not c.has_speech and c.cut == 5 * SR


def test_silero_spans_on_real_signal():
    from murmur.stt.streaming import silero_spans

    # 0.5 s silence, 1.5 s of noisy "speech", 1 s silence
    rng = np.random.default_rng(0)
    a = np.concatenate([
        np.zeros(int(0.5 * SR)),
        rng.standard_normal(int(1.5 * SR)) * 0.3,
        np.zeros(SR),
    ]).astype(np.float32)
    spans = silero_spans(a)
    # Silero may or may not call white noise speech; we only require a
    # well-formed, ordered, in-range result.
    assert all(0 <= s < e <= a.size for s, e in spans)
    assert spans == sorted(spans)


def test_capture_preroll_and_end(qapp):
    from murmur.audio.capture import AudioCapture

    cap = AudioCapture(device=None, preroll_ms=300, block=1600, auto_open=False)
    got = []
    cap.samples.connect(lambda c: got.append(c.size))

    # feed 1 s of ring audio (value 1.0) before the key press
    for _ in range(10):
        cap._on_audio(np.full((1600, 1), 1.0, dtype=np.float32), 1600, None, None)
    assert not cap.recording
    assert cap.begin()          # no stream to open in tests: begin() must not need one
    # 5 blocks of value 2.0 while held
    for _ in range(5):
        cap._on_audio(np.full((1600, 1), 2.0, dtype=np.float32), 1600, None, None)
    audio = cap.end()
    assert not cap.recording
    # 300 ms pre-roll (4800 samples of 1.0) + 5 x 1600 of 2.0
    assert audio.size == 4800 + 8000
    assert np.all(audio[:4800] == 1.0) and np.all(audio[4800:] == 2.0)
    qapp.processEvents()
    assert got == [1600] * 5     # samples signal only while recording
    assert cap.end().size == 0   # idempotent
