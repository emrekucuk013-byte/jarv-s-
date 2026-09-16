"""Push-to-talk: hold a key, speak, release. The brain in the middle is untouched."""

from __future__ import annotations

import sys
import threading
from typing import Callable

from ..agent import Agent
from .sentences import SentenceChunker
from .stt import TranscribeError, Transcriber
from .tts import SpeechQueue


class VoiceSession:
    """Wires recorder -> transcriber -> Agent.run_turn -> speech queue.

    ``recorder`` needs start()/stop()->wav bytes. Everything is injectable so
    the whole turn can be tested without a microphone.
    """

    def __init__(self, agent: Agent, transcriber: Transcriber, speech: SpeechQueue, recorder,
                 out: Callable[[str], None] | None = None, speak_replies: bool = True):
        self.agent = agent
        self.transcriber = transcriber
        self.speech = speech
        self.recorder = recorder
        self.out = out or (lambda s: (sys.stdout.write(s), sys.stdout.flush()))
        self.speak_replies = speak_replies
        self._turn_lock = threading.Lock()
        self._recording = False

    # -- key events ---------------------------------------------------------

    def key_down(self) -> None:
        if self._recording:
            return
        # Interrupt: a new turn cuts the assistant off and it listens instead.
        self.speech.cancel()
        self._recording = True
        self.recorder.start()
        self.out("\n[listening…] ")

    def key_up(self) -> None:
        if not self._recording:
            return
        self._recording = False
        wav = self.recorder.stop()
        self.out("[thinking…] ")
        threading.Thread(target=self.spoken_turn, args=(wav,), daemon=True).start()

    # -- one spoken turn ----------------------------------------------------

    def spoken_turn(self, wav: bytes) -> None:
        with self._turn_lock:
            try:
                text = self.transcriber.transcribe(wav)
            except TranscribeError as e:
                self.out(f"\n(couldn't transcribe: {e})\n")
                return
            if not text:
                self.out("\n(heard nothing)\n")
                return
            self.out(f"\nyou (heard)> {text}\n{self.agent.name.lower()}> ")
            self.text_turn(text)

    def text_turn(self, text: str) -> None:
        chunker = SentenceChunker()

        def on_text(delta: str) -> None:
            self.out(delta)
            if self.speak_replies:
                for sentence in chunker.feed(delta):
                    self.speech.say(sentence)

        result = self.agent.run_turn(text, on_text=on_text, on_tool=self._on_tool)
        if self.speak_replies:
            for sentence in chunker.flush():
                self.speech.say(sentence)
        if result.error:
            self.out(f"\n(couldn't get a reply: {result.error})")
            if self.speak_replies:
                self.speech.say("Sorry, I couldn't get a reply just now.")
        self.out("\n")

    def _on_tool(self, event: dict) -> None:
        mark = "!" if event["is_error"] else "*"
        self.out(f"\n  [{mark} {event['tool']}] ")


def run_push_to_talk(session: VoiceSession, key_name: str) -> None:
    """Block on a global hotkey listener until Ctrl-C."""
    try:
        from pynput import keyboard
    except ImportError as e:
        raise RuntimeError("Voice needs the audio extras: pip install 'vyron[voice]'") from e

    target = getattr(keyboard.Key, key_name, None) or keyboard.KeyCode.from_char(key_name)

    def on_press(key):
        if key == target:
            session.key_down()

    def on_release(key):
        if key == target:
            session.key_up()

    print(f"Hold [{key_name}] to talk, release to send. Ctrl-C to quit.")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            pass
