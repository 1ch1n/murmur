"""Read-aloud engine.

Text is split into sentences and spoken one at a time by a long-lived
worker thread that takes commands from a queue: play, pause, resume, stop,
seek, next, prev. Sentence granularity is what makes pause and jumping
instant. The Edge neural voice (online) is prefetched one sentence ahead;
Windows SAPI is the offline fallback and pauses natively.
"""
from __future__ import annotations

import io
import logging
import queue
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

# SAPI SpeechVoiceSpeakFlags
_SVSF_ASYNC = 1
_SVSF_PURGE_BEFORE_SPEAK = 2

Span = Tuple[int, int, str]   # start, end, text


def split_sentences(text: str) -> List[Span]:
    """Sentence spans over the original text. Boundaries: sentence-ending
    punctuation followed by whitespace, or a line break. Tiny fragments
    are merged into their neighbour so the reader never stutters."""
    if not text or not text.strip():
        return []
    spans: List[Span] = []
    pat = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+|\n+")
    pos = 0
    for m in pat.finditer(text):
        end = m.start()
        # keep closing quotes/brackets with the sentence
        while end < m.end() and text[end] in "\"'”’)]":
            end += 1
        chunk = text[pos:end]
        if chunk.strip():
            spans.append((pos + (len(chunk) - len(chunk.lstrip())), end, chunk.strip()))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        spans.append((pos + (len(tail) - len(tail.lstrip())), len(text), tail.strip()))

    # merge fragments shorter than 3 chars (e.g. "x", initials) backwards
    merged: List[Span] = []
    for s in spans:
        if merged and len(s[2]) < 3:
            a, _, ta = merged[-1]
            merged[-1] = (a, s[1], (ta + " " + s[2]).strip())
        else:
            merged.append(s)
    return merged


class ReaderWorker(QThread):
    """Speaks sentences off the GUI thread under queue control."""

    sentence_started = Signal(int)       # index of the sentence now speaking
    state_changed = Signal(str)          # playing | paused | stopped
    finished_all = Signal()
    error = Signal(str)

    def __init__(self, rate: int = 0, voice: str = ""):
        super().__init__()
        self.rate = rate
        self.voice = voice
        self.sentences: List[str] = []
        self.idx = 0
        self._cmd: "queue.Queue[tuple]" = queue.Queue()
        self._playing = False
        self._paused = False
        self._quit = False
        self._gen = 0                     # bumps on load; invalidates prefetch
        self._cache: Dict[Tuple[int, int], Tuple] = {}
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._prefetch: Optional[Tuple[int, int, Future]] = None
        self._sapi = None
        self._sapi_paused = False

    # --- commands (GUI thread) --------------------------------------------

    def command(self, name: str, *args) -> None:
        self._cmd.put((name, args))

    def load(self, sentences: List[str]) -> None:
        self.command("load", list(sentences))

    def play(self, idx: Optional[int] = None) -> None:
        self.command("play", idx)

    def pause(self) -> None:
        self.command("pause")

    def resume(self) -> None:
        self.command("resume")

    def toggle(self) -> None:
        self.command("toggle")

    def stop(self) -> None:
        self.command("stop")

    def seek(self, idx: int) -> None:
        self.command("seek", int(idx))

    def next(self) -> None:
        self.command("next")

    def prev(self) -> None:
        self.command("prev")

    def set_voice(self, rate: int, voice: str) -> None:
        self.command("voice", int(rate), voice or "")

    def quit_worker(self) -> None:
        self.command("quit")

    @property
    def is_playing(self) -> bool:
        return self._playing and not self._paused

    @property
    def is_paused(self) -> bool:
        return self._playing and self._paused

    @property
    def is_active(self) -> bool:
        return self._playing

    # --- worker loop ------------------------------------------------------

    def run(self) -> None:
        try:
            import pythoncom

            pythoncom.CoInitialize()
        except Exception:
            pythoncom = None
        try:
            while not self._quit:
                if not self._playing or self._paused:
                    self._handle(self._cmd.get())
                    continue
                if self.idx >= len(self.sentences):
                    self._playing = False
                    self.idx = 0
                    self.state_changed.emit("stopped")
                    self.finished_all.emit()
                    continue
                self.sentence_started.emit(self.idx)
                interrupted = self._speak(self.idx)
                if not interrupted and self._playing and not self._paused:
                    self.idx += 1
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)
            if pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def _handle(self, cmd: tuple) -> bool:
        """Apply a command. Returns True when playback of the current
        sentence must be interrupted."""
        name, args = cmd
        if name == "quit":
            self._quit = True
            self._playing = False
            return True
        if name == "load":
            self.sentences = args[0]
            self.idx = 0
            self._gen += 1
            self._cache.clear()
            self._prefetch = None
            was = self._playing
            self._playing = False
            self._paused = False
            if was:
                self.state_changed.emit("stopped")
            return True
        if name == "voice":
            self.rate, self.voice = args[0], args[1]
            self._cache.clear()
            self._prefetch = None
            return False
        if name == "play":
            if args and args[0] is not None:
                self.idx = max(0, min(len(self.sentences), int(args[0])))
            if not self.sentences:
                return False
            if self._playing and not self._paused and (not args or args[0] is None):
                return False
            self._playing = True
            self._paused = False
            self.state_changed.emit("playing")
            return True
        if name == "pause":
            if self._playing and not self._paused:
                self._paused = True
                self.state_changed.emit("paused")
                return True
            return False
        if name == "resume":
            if self._playing and self._paused:
                self._paused = False
                self.state_changed.emit("playing")
                return True
            return False
        if name == "toggle":
            if not self._playing:
                return self._handle(("play", (None,)))
            if self._paused:
                return self._handle(("resume", ()))
            return self._handle(("pause", ()))
        if name == "stop":
            if self._playing:
                self._playing = False
                self._paused = False
                self.idx = 0
                self.state_changed.emit("stopped")
                return True
            return False
        if name in ("seek", "next", "prev"):
            if not self.sentences:
                return False
            if name == "seek":
                self.idx = max(0, min(len(self.sentences) - 1, args[0]))
            elif name == "next":
                self.idx = min(len(self.sentences) - 1, self.idx + 1)
            else:
                self.idx = max(0, self.idx - 1)
            self._resume_offset = 0
            if not self._playing:
                self._playing = True
                self._paused = False
                self.state_changed.emit("playing")
            elif self._paused:
                self.sentence_started.emit(self.idx)
            return True
        return False

    def _drain(self) -> bool:
        """Apply any queued commands without blocking. True if interrupted."""
        interrupted = False
        while True:
            try:
                cmd = self._cmd.get_nowait()
            except queue.Empty:
                return interrupted
            if self._handle(cmd):
                interrupted = True

    # --- speaking ---------------------------------------------------------

    def _speak(self, idx: int) -> bool:
        text = self.sentences[idx]
        if self.voice:
            try:
                return self._speak_edge(idx, text)
            except Exception:
                log.exception("edge TTS failed, falling back to SAPI")
                self.error.emit("Edge voice unavailable, using the offline voice")
        return self._speak_sapi(idx, text)

    # Edge neural voice (online), with one-sentence prefetch.
    def _synth_edge(self, gen: int, idx: int, text: str):
        import asyncio

        import edge_tts
        import soundfile as sf

        rate_pct = max(-50, min(100, self.rate * 10))

        async def collect() -> bytes:
            buf = io.BytesIO()
            comm = edge_tts.Communicate(text, self.voice, rate=f"{rate_pct:+d}%")
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        data = asyncio.run(collect())
        audio, sr = sf.read(io.BytesIO(data), dtype="float32")
        return audio, sr

    def _get_edge_audio(self, idx: int):
        key = (self._gen, idx)
        if key in self._cache:
            return self._cache.pop(key)
        if self._prefetch is not None and self._prefetch[0] == self._gen and self._prefetch[1] == idx:
            fut = self._prefetch[2]
            self._prefetch = None
            return fut.result()
        return self._synth_edge(self._gen, idx, self.sentences[idx])

    def _queue_prefetch(self, idx: int) -> None:
        if idx < len(self.sentences) and (self._prefetch is None or self._prefetch[1] != idx):
            gen = self._gen
            self._prefetch = (gen, idx, self._pool.submit(self._synth_edge, gen, idx, self.sentences[idx]))

    def _speak_edge(self, idx: int, text: str) -> bool:
        import sounddevice as sd

        # synthesis may take a moment; let commands interrupt it
        fut = self._pool.submit(self._synth_edge, self._gen, idx, text) if not (
            self._prefetch and self._prefetch[0] == self._gen and self._prefetch[1] == idx
        ) else self._prefetch[2]
        self._prefetch = None
        while not fut.done():
            if self._drain():
                return True
            time.sleep(0.03)
        audio, sr = fut.result()
        self._queue_prefetch(idx + 1)

        offset = getattr(self, "_resume_offset", 0) if getattr(self, "_resume_idx", None) == idx else 0
        self._resume_idx = idx
        offset = min(offset, len(audio))
        sd.play(audio[offset:], sr)
        started = time.monotonic()
        try:
            while True:
                stream = sd.get_stream()
                if stream is None or not stream.active:
                    break
                if self._drain():
                    sd.stop()
                    if self._playing and self._paused and self.idx == idx:
                        self._resume_offset = offset + int((time.monotonic() - started) * sr)
                    else:
                        self._resume_offset = 0
                    return True
                time.sleep(0.03)
        finally:
            pass
        self._resume_offset = 0
        return False

    # SAPI (offline), native pause/resume.
    def _speak_sapi(self, idx: int, text: str) -> bool:
        try:
            import win32com.client

            if self._sapi is None:
                self._sapi = win32com.client.Dispatch("SAPI.SpVoice")
            voice = self._sapi
            voice.Rate = max(-10, min(10, self.rate))
            voice.Speak(text, _SVSF_ASYNC)
            while not voice.WaitUntilDone(40):
                if not self._drain():
                    continue
                if self._playing and self._paused and self.idx == idx:
                    # True pause: SAPI holds its place mid-sentence. Block on
                    # the queue until we are resumed, stopped, or moved.
                    voice.Pause()
                    while self._playing and self._paused and not self._quit:
                        self._handle(self._cmd.get())
                    voice.Resume()
                    if self._playing and not self._paused and self.idx == idx:
                        continue        # resumed in place, keep polling
                voice.Speak("", _SVSF_ASYNC | _SVSF_PURGE_BEFORE_SPEAK)
                return True
            return False
        except Exception:
            log.exception("SAPI TTS failed")
            self.error.emit("Text-to-speech failed")
            self._playing = False
            self.state_changed.emit("stopped")
            return True
