"""Headless file transcription: `murmur --file call.m4a`.

Runs without the tray/overlay. Loads the model once, decodes the file with
PyAV, streams segments to stderr as progress, and writes the transcript to
stdout (or --out / --copy / --archive).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

from murmur.config import Settings, load_settings

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="murmur",
        description="MURMUR - push-to-talk dictation. With no arguments, starts the tray app.",
    )
    p.add_argument("--file", "-f", metavar="AUDIO",
                   help="transcribe this audio/video file instead of starting the tray app")
    p.add_argument("--out", "-o", metavar="TXT",
                   help="write the transcript to this file (default: print to stdout)")
    p.add_argument("--copy", action="store_true",
                   help="also put the transcript on the clipboard")
    p.add_argument("--archive", action="store_true",
                   help="also save the transcript to MURMUR's History")
    p.add_argument("--timestamps", "-t", action="store_true",
                   help="one segment per line, prefixed with [mm:ss]")
    p.add_argument("--cleanup", action="store_true",
                   help="run the configured LLM cleanup pass on the result (off by default)")
    p.add_argument("--model", metavar="NAME",
                   help="override the Whisper model for this run, e.g. medium.en")
    p.add_argument("--beam-size", type=int, metavar="N",
                   help="override beam size (1 = fastest, 5 = most accurate)")
    p.add_argument("--no-vad", action="store_true",
                   help="disable the VAD filter (try this if speech is being dropped)")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="no progress output on stderr")
    return p


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    changes = {}
    if args.model:
        changes["model"] = args.model
    if args.beam_size:
        changes["beam_size"] = args.beam_size
    if args.no_vad:
        changes["vad_filter"] = False
    return replace(settings, **changes) if changes else settings


def _set_clipboard(text: str) -> None:
    if sys.platform == "win32":
        import win32clipboard  # pywin32

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])
    app.clipboard().setText(text)
    app.processEvents()


def transcribe_file_cmd(args: argparse.Namespace) -> int:
    from murmur.stt.engine import Segment, WhisperEngine
    from murmur.stt.file import decode_file, duration_s, fmt_ts

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    err = (lambda *a: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr, flush=True))

    src = Path(args.file)
    try:
        audio = decode_file(src)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # PyAV raises a wide range here
        print(f"could not decode {src.name}: {e}", file=sys.stderr)
        return 2

    total = duration_s(audio)
    if total < 0.1:
        print(f"{src.name} contains no audio", file=sys.stderr)
        return 2

    settings = _apply_overrides(load_settings(), args)
    err(f"{src.name}: {fmt_ts(total)} of audio, model={settings.model} beam={settings.beam_size}")

    engine = WhisperEngine(settings)
    t0 = time.perf_counter()
    engine.load()
    err(f"model loaded in {time.perf_counter() - t0:.1f}s")

    segments: list[Segment] = []

    def on_seg(seg: Segment) -> None:
        segments.append(seg)
        pct = min(100, int(100 * seg.end / total))
        err(f"[{fmt_ts(seg.start)}] {pct:3d}%  {seg.text}")

    t1 = time.perf_counter()
    raw = engine.transcribe(audio, on_segment=on_seg)
    transcribe_ms = int((time.perf_counter() - t1) * 1000)
    err(f"transcribed in {transcribe_ms / 1000:.1f}s ({total / max(transcribe_ms / 1000, 1e-3):.1f}x realtime)")

    if not raw:
        print(f"no speech detected in {src.name}", file=sys.stderr)
        return 1

    final = raw
    cleanup_ms = 0
    provider_name: Optional[str] = None
    if args.cleanup:
        from murmur.cleanup.prompts import CleanupContext
        from murmur.cleanup.providers import make_provider

        provider = make_provider(
            settings.cleanup_provider,
            anthropic_api_key=settings.anthropic_api_key,
            anthropic_model=settings.anthropic_model,
            ollama_model=settings.ollama_model,
            ollama_base_url=settings.ollama_base_url,
            # long files need far more than the dictation timeout
            timeout=max(settings.cleanup_timeout_s, 30.0 + total / 5),
        )
        if provider.name == "disabled":
            err("--cleanup requested but cleanup_provider is 'disabled' in config; skipping")
        else:
            ctx = CleanupContext(target_app=src.name, target_category="doc",
                                 dictionary=tuple(settings.dictionary))
            t2 = time.perf_counter()
            cleaned = provider.cleanup(raw, ctx)
            cleanup_ms = int((time.perf_counter() - t2) * 1000)
            if cleaned.strip():
                final = cleaned.strip()
                provider_name = provider.name
            err(f"cleanup ({provider.name}) in {cleanup_ms / 1000:.1f}s")

    if args.timestamps and provider_name is None:
        output = "\n".join(f"[{fmt_ts(s.start)}] {s.text}" for s in segments)
    else:
        if args.timestamps:
            err("--timestamps ignored: cleanup rewrites the text, so segment boundaries no longer apply")
        output = final

    if args.out:
        out = Path(args.out)
        out.write_text(output + "\n", encoding="utf-8")
        err(f"wrote {out}")
    else:
        print(output)

    if args.copy:
        try:
            _set_clipboard(output)
            err("copied to clipboard")
        except Exception as e:
            err(f"clipboard failed: {e}")

    if args.archive:
        from murmur.archive import Archive

        Archive().add(
            final,
            target_app=src.name,
            target_category="file",
            raw_text=raw if provider_name else None,
            cleanup_provider=provider_name,
            audio_duration_s=round(total, 2),
            transcribe_ms=transcribe_ms,
            cleanup_ms=cleanup_ms,
        )
        err("saved to History")

    return 0
