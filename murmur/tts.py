from __future__ import annotations

import logging

from PySide6.QtCore import QThread

from murmur.config import DATA_DIR

log = logging.getLogger(__name__)

# SAPI SpeechVoiceSpeakFlags
_SVSF_ASYNC = 1
_SVSF_PURGE_BEFORE_SPEAK = 2

_TTS_CACHE = DATA_DIR / "tts_last.mp3"


class SpeakerThread(QThread):
    """Speaks text off the GUI thread.

    Prefers the Edge neural voice from settings (needs internet); falls
    back to local SAPI when synthesis fails or no voice is configured.
    stop() flips a flag both paths poll, so a tap cuts speech short.
    """

    def __init__(self, text: str, rate: int = 0, voice: str = ""):
        super().__init__()
        self.text = text
        self.rate = rate
        self.voice = voice
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        if self.voice:
            try:
                self._speak_edge()
                return
            except Exception:
                log.exception("edge TTS failed, falling back to SAPI")
        self._speak_sapi()

    # --- Edge neural voice (online) --------------------------------------

    def _speak_edge(self) -> None:
        import asyncio

        import edge_tts
        import sounddevice as sd
        import soundfile as sf

        # Edge rate is a percentage; settings rate is SAPI-style -10..10.
        rate_pct = max(-50, min(100, self.rate * 10))
        communicate = edge_tts.Communicate(
            self.text, self.voice, rate=f"{rate_pct:+d}%"
        )
        asyncio.run(communicate.save(str(_TTS_CACHE)))
        if self._stop:
            return

        data, samplerate = sf.read(_TTS_CACHE, dtype="float32")
        sd.play(data, samplerate)
        while sd.get_stream().active:
            if self._stop:
                sd.stop()
                break
            self.msleep(100)

    # --- SAPI (offline fallback) ------------------------------------------

    def _speak_sapi(self) -> None:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voice.Rate = max(-10, min(10, self.rate))
            voice.Speak(self.text, _SVSF_ASYNC)
            while not voice.WaitUntilDone(100):
                if self._stop:
                    voice.Speak("", _SVSF_ASYNC | _SVSF_PURGE_BEFORE_SPEAK)
                    break
        except Exception:
            log.exception("SAPI TTS failed")
        finally:
            pythoncom.CoUninitialize()
