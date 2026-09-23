"""File-transcription CLI: decoding + arg parsing, no model load."""
from __future__ import annotations

import numpy as np


def test_decode_file_resamples_to_16k_mono(tmp_path) -> None:
    import soundfile as sf

    from murmur.stt.file import decode_file, duration_s, fmt_ts

    sr = 44100
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    stereo = np.stack([np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 660 * t)], axis=1)
    wav = tmp_path / "tone.wav"
    sf.write(wav, stereo.astype(np.float32), sr)

    audio = decode_file(wav)
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert abs(duration_s(audio) - 2.0) < 0.05
    assert fmt_ts(65) == "01:05"
    assert fmt_ts(3661) == "1:01:01"


def test_decode_missing_file_raises(tmp_path) -> None:
    import pytest

    from murmur.stt.file import decode_file

    with pytest.raises(FileNotFoundError):
        decode_file(tmp_path / "nope.m4a")


def test_cli_parser_and_overrides() -> None:
    from murmur.cli import _apply_overrides, build_parser
    from murmur.config import Settings

    args = build_parser().parse_args(
        ["--file", "call.m4a", "--model", "medium.en", "--beam-size", "5", "--no-vad", "-t"]
    )
    assert args.file == "call.m4a" and args.timestamps
    s = _apply_overrides(Settings(), args)
    assert (s.model, s.beam_size, s.vad_filter) == ("medium.en", 5, False)
    assert Settings().model == "small.en"  # original untouched

    assert build_parser().parse_args([]).file is None


def test_instance_guard_ignores_file_mode() -> None:
    from murmur.__main__ import _is_murmur_cmdline

    assert _is_murmur_cmdline(["python", "-m", "murmur"])
    assert not _is_murmur_cmdline(["python", "-m", "murmur", "--file", "x.wav"])
    assert not _is_murmur_cmdline(["murmur.exe", "-f", "x.wav"])
