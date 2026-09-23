# MURMUR

**Your voice, your machine.** An open-source alternative to Wispr Flow: hold a key, speak, release — clean text lands wherever your cursor is. Speech-to-text runs locally with `faster-whisper`; the optional cleanup pass is bring-your-own-key or fully local via Ollama. No subscription, no audio leaving your box unless you choose it.

Push-to-talk dictation, frameless pill UI, per-app context-aware cleanup, searchable history, and text-to-speech read-aloud of any selected text.

Windows-first. macOS / Linux deferred to later phases.

## Stack

- **GUI**: PySide6 (LGPL — commercial-friendly)
- **STT**: `faster-whisper` Small.en int8, loaded once and kept resident in-process
- **VAD**: faster-whisper's built-in silero filter (`vad_filter=True`)
- **Audio**: `sounddevice` ringbuffer
- **Hotkey**: `keyboard` library (push-to-hold)
- **Paste**: clipboard set + `Ctrl+V` injection with previous-clipboard restore
- **Cleanup**: pluggable provider — `disabled` / `anthropic` (Claude Haiku 4.5, BYOK) / `ollama` (local)
- **Context**: Win32 `GetForegroundWindow` → process+title → category injected into cleanup prompt

## Quickstart

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m murmur
```

On first run, the `small.en` model (~244 MB) downloads to `%USERPROFILE%\.murmur\models\`.

## Use

1. Wait for the pill at the bottom-center to switch to dim-purple `READY` (model loaded).
2. Click into any text field anywhere on the system.
3. **Press and hold Right Alt**, speak, release.
4. The transcript pastes at the cursor.
5. **Hold Shift while releasing Right Alt** to skip cleanup for that utterance (raw Whisper output — use for code dictation, name spellings, exact phrasing).
6. **Select any text and tap F9** to hear it read aloud. Tap F9 again to stop. Uses the fully offline Windows SAPI voice by default; set `tts_voice` in config to a Microsoft Edge neural voice (see `edge-tts --list-voices`) for much better quality — **note that this sends the selected text to Microsoft's Edge TTS service**.

**Tray menu** (right-click the icon, or double-click to open History):
- Show History — searchable archive + stats
- Settings… — full settings UI (no JSON editing needed)
- Show/Hide Pill
- Quit

## Transcribe an audio file

MURMUR can also run headless on a recording (phone call, voice memo, meeting) without starting the tray app:

```powershell
.venv\Scripts\Activate.ps1
python -m murmur --file "C:\path	o\call.m4a"
```

Anything PyAV can decode works (`.m4a`, `.mp3`, `.wav`, `.flac`, `.ogg`, `.opus`, `.mp4`, ...). Segments stream to the terminal as they are decoded; the finished transcript prints to stdout. A 5-minute call takes roughly a minute on CPU with `small.en`.

| Flag | Effect |
| --- | --- |
| `--out notes.txt` | write the transcript to a file instead of stdout |
| `--copy` | put the transcript on the clipboard |
| `--archive` | save it to MURMUR's History |
| `--timestamps` / `-t` | one segment per line, prefixed with `[mm:ss]` |
| `--model medium.en` | better accuracy on low-quality audio (downloads ~1.5 GB on first use) |
| `--beam-size 5` | slower, more accurate decoding |
| `--no-vad` | disable the VAD filter if quiet speech is being dropped |
| `--cleanup` | run your configured LLM cleanup pass on the whole transcript |
| `--quiet` / `-q` | no progress output |

The tray app can keep running while you do this; file mode loads its own model copy and exits when done.

## Features

- **Resident model, no subprocess.** ~0.5–1.5s end-to-end on a Ryzen 3700X CPU.
- **History window:** searchable archive of every transcript with copy, delete, export to `.txt` / `.json`.
- **Stats tab:** total transcripts, words dictated, audio captured, today's count, average STT and cleanup latencies, top target apps, breakdown by category.
- **LLM cleanup pass (optional):** removes filler ("um", "uh", "like"), adds punctuation, fixes grammar without changing meaning, preserves proper nouns and code identifiers.
- **Per-app context:** the cleanup prompt knows whether the cursor is in a code editor, terminal, chat app, mail client, doc editor, or browser — and adapts tone accordingly (e.g. drops trailing periods in chat, preserves identifiers in code).
- **Custom vocabulary:** terms in the Dictionary tab are biased into Whisper's `initial_prompt` and the cleanup prompt so proper nouns survive transcription.
- **Hold-Shift-for-raw:** skip cleanup for a single utterance — handy for code or exact phrasing.
- **Read-aloud (F9):** speaks the current selection. Offline SAPI voice by default; opt into a Microsoft Edge neural voice via `tts_voice` in config (online — the selected text is sent to Microsoft; falls back to SAPI without network). Tap again to stop.
- **Single-instance guard:** double-launching just points you at the tray icon instead of spawning a duplicate.
- **No lone-Alt menu steal:** a phantom F24 is injected during the Alt hold so Windows never yanks focus to the menu bar when you release the push-to-talk key.
- **Sound cues (optional):** soft blips on start / stop / done / error.
- **Atomic archive writes:** tmp-file + `os.replace` — crash-safe history.
- **Pill never steals focus:** `WA_ShowWithoutActivating` + `WindowDoesNotAcceptFocus`, target caret stays put.

## Cleanup providers

In **Settings → Cleanup**:

| Provider | When to pick it | Cost |
|---|---|---|
| `disabled` | Privacy-only / no cleanup | Free, instant |
| `anthropic` | Best quality (Claude Haiku 4.5). BYOK. | ~$0.25/M input, $1.25/M output (fractions of a cent per utterance) |
| `ollama` | Local privacy, no API key. Needs `ollama serve` running locally. | Free, ~800–1500ms TTFT on CPU |

Set `ANTHROPIC_API_KEY` in Settings (stored locally at `~/.murmur/config.json` — plaintext, change permissions if needed).

## Files & data

- `%USERPROFILE%\.murmur\config.json`   — settings (now includes cleanup config + dictionary)
- `%USERPROFILE%\.murmur\archive.json`  — transcript history (atomic writes)
- `%USERPROFILE%\.murmur\models\`       — downloaded faster-whisper models
- `%USERPROFILE%\.murmur\murmur.log`    — app log

## Architecture notes

- **One resident process, one model load.** Unlike VoxMemo's per-utterance subprocess, MURMUR loads `WhisperModel` once at startup in a background `QThread` and reuses it. This is the entire latency fix.
- **Push-to-talk, not VAD-driven start.** End-of-utterance is the key-up event, not a VAD silence detection.
- **Window flags** are set in a single bitwise-OR'd call to avoid the qt.io setWindowFlags-overwriting bug.
- **Foreground capture happens before the pill paints**, so the cleanup prompt knows the correct target app.
- **Cleanup runs in the Transcriber QThread**, never on the GUI thread — paste happens regardless of LLM latency.
- **Settings updates** without restart: hotkey, modifier, paste, sounds, app-context apply immediately; model/compute changes trigger a background re-load with the pill briefly showing PROCESSING.

## Roadmap

- **Phase 3**: Moonshine v2 streaming with partial-result preview, MyChatArchive `capture_thought` "brain mode" hotkey, voice search via `search_brain`, Context Q&A with Kokoro TTS read-back, Linux X11.
- **macOS port**: pyobjc Accessibility shim, notarized `.app` build via $99/yr Apple Developer Program.

## License

[AGPL-3.0-or-later](LICENSE). Use it, fork it, ship it — but derivatives (including hosted/commercial ones) must publish their source under the same terms.
