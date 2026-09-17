"""Text-to-speech seam: give me text, play it aloud."""

from __future__ import annotations

import queue
import threading
from typing import Iterator, Protocol


class SpeakError(Exception):
    pass


class Player(Protocol):
    """Plays raw 16-bit mono PCM chunks. ``stop`` must interrupt an in-progress play."""

    sample_rate: int

    def play(self, chunks: Iterator[bytes]) -> None: ...
    def stop(self) -> None: ...


class Speaker(Protocol):
    def speak(self, text: str) -> None: ...
    def stop(self) -> None: ...


class ElevenLabsSpeaker:
    """Streams ElevenLabs audio straight into the player so speech starts before synthesis ends."""

    URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

    def __init__(self, api_key: str, voice_id: str, player: Player, model_id: str = "eleven_turbo_v2_5", transport=None):
        import httpx

        self._client = httpx.Client(timeout=60, transport=transport)
        self._headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
        self.voice_id = voice_id
        self.model_id = model_id
        self.player = player

    def speak(self, text: str) -> None:
        import httpx

        if not text.strip():
            return
        url = self.URL.format(voice_id=self.voice_id)
        params = {"output_format": f"pcm_{self.player.sample_rate}"}
        body = {"text": text, "model_id": self.model_id}
        try:
            with self._client.stream("POST", url, params=params, headers=self._headers, json=body) as r:
                if r.status_code >= 400:
                    raise SpeakError(f"ElevenLabs returned {r.status_code}.")
                self.player.play(r.iter_bytes(chunk_size=4096))
        except httpx.HTTPError as e:
            raise SpeakError("Couldn't reach ElevenLabs.") from e

    def stop(self) -> None:
        self.player.stop()


class SpeechQueue:
    """Speaks sentences in order on a background thread; ``cancel`` drops the rest and cuts the current one."""

    def __init__(self, speaker: Speaker, on_error=None):
        self.speaker = speaker
        self.on_error = on_error
        self._q: queue.Queue[str | None] = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self._generation = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def say(self, text: str) -> None:
        if text.strip():
            self._idle.clear()
            self._q.put(text)

    def cancel(self) -> None:
        self._generation += 1
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self.speaker.stop()

    def wait(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    @property
    def speaking(self) -> bool:
        return not self._idle.is_set()

    def _run(self) -> None:
        while True:
            text = self._q.get()
            if text is None:
                return
            gen = self._generation
            try:
                if gen == self._generation:
                    self.speaker.speak(text)
            except Exception as e:  # noqa: BLE001
                if self.on_error:
                    self.on_error(e)
            finally:
                if self._q.empty():
                    self._idle.set()


class SystemSpeaker:
    """The operating system's built-in voice. No key, no network; less natural than ElevenLabs.

    Mac: `say`. Linux: `espeak` (or `spd-say`). Windows: PowerShell's SAPI voice.
    """

    def __init__(self, voice: str | None = None, rate: int | None = None):
        import platform
        self.os = platform.system()
        self.voice = voice
        self.rate = rate
        self._proc = None

    def _command(self, text: str) -> list[str]:
        if self.os == "Darwin":
            cmd = ["say"]
            if self.voice: cmd += ["-v", self.voice]
            if self.rate: cmd += ["-r", str(self.rate)]
            return cmd + [text]
        if self.os == "Windows":
            safe = text.replace("'", "''")
            return ["powershell", "-NoProfile", "-Command",
                    f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak('{safe}')"]
        import shutil
        if shutil.which("espeak"):
            return ["espeak", text]
        if shutil.which("spd-say"):
            return ["spd-say", "-w", text]
        raise SpeakError("No system voice found. Install espeak, or set an ElevenLabs key.")

    def speak(self, text: str) -> None:
        import subprocess
        if not text.strip():
            return
        try:
            self._proc = subprocess.Popen(self._command(text), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._proc.wait()
        except FileNotFoundError as e:
            raise SpeakError("The system voice command isn't available on this machine.") from e
        finally:
            self._proc = None

    def stop(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
