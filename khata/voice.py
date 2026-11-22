"""Voice notes -> text. Provider sits behind a small interface so it can be swapped.

The default provider speaks the OpenAI-compatible /audio/transcriptions API, which OpenAI
and Groq (Whisper) both serve. Set KHATA_STT_URL / KHATA_STT_MODEL to pick one.
Audio is written to a temp dir only for conversion and is always deleted afterwards.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from .guard import PaidGuard

log = logging.getLogger("khata.voice")

MAX_BYTES = 10 * 1024 * 1024
FFMPEG_TIMEOUT = 20
STT_TIMEOUT = 30


class VoiceError(Exception):
    """Message is safe to show the user."""


class Transcriber(Protocol):
    def transcribe(self, wav_path: Path) -> str: ...


# Whisper's prompt biases spelling toward words a khata actually uses (max 224 tokens).
STT_PROMPT = ("Khata entries in Hindi, Punjabi, Hinglish or English. Ramesh ko 500 rupaye udhaar. "
              "Sita ne 200 diye. रमेश को पाँच सौ रुपये उधार। ਸੀਤਾ ਨੇ 300 ਜਮ੍ਹਾ ਕਰਾਏ। dedh sau, dhai hazaar.")


URDU_SCRIPT = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")


class OpenAICompatTranscriber:
    """Defaults to Groq's free tier (whisper-large-v3). Works with any OpenAI-compatible STT."""

    def __init__(self, key: str, url: str | None = None, model: str | None = None):
        self.key = key
        self.url = url or os.environ.get("KHATA_STT_URL", "https://api.groq.com/openai/v1/audio/transcriptions")
        self.model = model or os.environ.get("KHATA_STT_MODEL", "whisper-large-v3")

    def transcribe(self, wav_path: Path) -> str:
        forced = os.environ.get("KHATA_STT_LANGUAGE", "").strip() or None
        text = self._post(wav_path, forced)
        # Spoken Hindi and Urdu are nearly identical, so Whisper sometimes writes Hindi in Urdu
        # script. The extractor reads Devanagari, so ask again with the language pinned to Hindi.
        if not forced and URDU_SCRIPT.search(text):
            log.info("STT returned Urdu script; retrying with language=hi")
            text = self._post(wav_path, "hi")
        return text

    def _post(self, wav_path: Path, language: str | None) -> str:
        import httpx
        data = {"model": self.model, "prompt": STT_PROMPT, "temperature": "0", "response_format": "json"}
        if language:
            data["language"] = language
        with wav_path.open("rb") as f:
            r = httpx.post(self.url, headers={"Authorization": f"Bearer {self.key}"},
                           data=data, files={"file": (wav_path.name, f)}, timeout=STT_TIMEOUT)
        if r.status_code == 429:
            raise VoiceError("Voice is busy right now (free-tier limit). Please type the entry, or try in a minute.")
        if r.status_code != 200:
            try:
                reason = str((r.json().get("error") or {}).get("message", ""))[:200]
            except Exception:
                reason = ""
            log.warning("STT error %s (%s): %s", r.status_code, wav_path.suffix, reason)
            raise VoiceError("Voice service error. Please type the entry instead.")
        return (r.json().get("text") or "").strip()


def to_wav(src: Path, dst: Path) -> Path:
    """Convert any audio to 16 kHz mono wav. Without ffmpeg, pass the original file through."""
    if not shutil.which("ffmpeg"):
        return src
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
                        "-ac", "1", "-ar", "16000", str(dst)],
                       check=True, timeout=FFMPEG_TIMEOUT, capture_output=True)
    except subprocess.TimeoutExpired:
        raise VoiceError("That voice note took too long to process. Try a shorter one.")
    except subprocess.CalledProcessError:
        raise VoiceError("I couldn't read that audio file.")
    return dst


class VoicePipeline:
    def __init__(self, guard: PaidGuard, transcriber: Transcriber | None = None):
        self.guard = guard
        self.transcriber = transcriber
        self.last_tmpdir: str | None = None   # exposed for tests that check deletion

    def transcribe(self, audio: bytes, suffix: str = ".ogg") -> str:
        if not audio:
            raise VoiceError("Empty voice note.")
        if len(audio) > MAX_BYTES:
            raise VoiceError("Voice note is too large (10 MB max).")
        if not self.guard.take():
            raise VoiceError("Voice is off in demo mode. Please type the entry, e.g. 'Ramesh ko 500 udhaar'.")
        tr = self.transcriber or OpenAICompatTranscriber(self.guard.key)
        # .oga (Telegram voice notes) is Ogg audio, but Groq only accepts the .ogg extension.
        suffix = {".oga": ".ogg"}.get(suffix, suffix)
        suffix = suffix if suffix in (".ogg", ".opus", ".webm", ".mp3", ".m4a", ".wav", ".mp4") else ".bin"
        tmp = tempfile.mkdtemp(prefix="khata-voice-")
        self.last_tmpdir = tmp
        try:
            src = Path(tmp) / f"in{suffix}"
            src.write_bytes(audio)
            wav = to_wav(src, Path(tmp) / "out.wav")
            try:
                text = tr.transcribe(wav)
            except VoiceError:
                raise
            except Exception:
                raise VoiceError("Voice service timed out. Please type the entry instead.")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not text:
            raise VoiceError("I couldn't hear anything in that note.")
        return text
