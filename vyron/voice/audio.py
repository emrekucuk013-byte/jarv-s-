"""Microphone capture and speaker playback via sounddevice (voice extra)."""

from __future__ import annotations

import io
import threading
import wave
from typing import Iterator


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _sd():
    try:
        import sounddevice
    except ImportError as e:
        raise RuntimeError("Voice needs the audio extras: pip install 'vyron[voice]'") from e
    except OSError as e:  # Linux without PortAudio; Mac/Windows wheels bundle it
        raise RuntimeError("PortAudio is missing. On Debian/Ubuntu: sudo apt install libportaudio2") from e
    return sounddevice


class Recorder:
    """Collects mono int16 audio between start() and stop()."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._frames: list[bytes] = []
        self._stream = None

    def start(self) -> None:
        sd = _sd()
        self._frames = []
        self._stream = sd.RawInputStream(samplerate=self.sample_rate, channels=1, dtype="int16", callback=self._cb)
        self._stream.start()

    def _cb(self, indata, frames, time, status) -> None:
        self._frames.append(bytes(indata))

    def stop(self) -> bytes:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return pcm_to_wav(b"".join(self._frames), self.sample_rate)


class SoundDevicePlayer:
    sample_rate = 16000

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._stop = threading.Event()

    def play(self, chunks: Iterator[bytes]) -> None:
        sd = _sd()
        self._stop.clear()
        carry = b""
        with sd.RawOutputStream(samplerate=self.sample_rate, channels=1, dtype="int16") as out:
            for chunk in chunks:
                if self._stop.is_set():
                    break
                data = carry + chunk
                usable = len(data) - (len(data) % 2)
                out.write(data[:usable])
                carry = data[usable:]

    def stop(self) -> None:
        self._stop.set()
