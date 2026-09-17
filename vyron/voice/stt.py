"""Speech-to-text seam: give me audio, get back text."""

from __future__ import annotations

from typing import Protocol


class TranscribeError(Exception):
    pass


class Transcriber(Protocol):
    def transcribe(self, wav_bytes: bytes) -> str: ...


class DeepgramTranscriber:
    """Deepgram pre-recorded transcription of one push-to-talk clip.

    A clip is short and complete the moment the key is released, so a single
    request is the simplest reliable path; a streaming socket can replace
    this later without touching anything else.
    """

    URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, model: str = "nova-3", language: str = "en", transport=None):
        import httpx

        self._client = httpx.Client(timeout=30, transport=transport)
        self._headers = {"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"}
        self._params = {"model": model, "language": language, "smart_format": "true"}

    def transcribe(self, wav_bytes: bytes, content_type: str = "audio/wav") -> str:
        import httpx

        headers = {**self._headers, "Content-Type": content_type or "audio/wav"}
        try:
            r = self._client.post(self.URL, params=self._params, headers=headers, content=wav_bytes)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise TranscribeError(f"Deepgram returned {e.response.status_code}.") from e
        except httpx.HTTPError as e:
            raise TranscribeError("Couldn't reach Deepgram.") from e
        try:
            return r.json()["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        except (KeyError, IndexError, ValueError) as e:
            raise TranscribeError("Deepgram sent an unexpected reply.") from e
