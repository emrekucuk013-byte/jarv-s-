import json
import threading
import time

import httpx
import pytest

from vyron.agent import Agent
from vyron.config import load_config
from vyron.provider import FakeProvider
from vyron.voice.sentences import SentenceChunker
from vyron.voice.session import VoiceSession
from vyron.voice.stt import DeepgramTranscriber, TranscribeError
from vyron.voice.tts import ElevenLabsSpeaker, SpeechQueue


def test_sentence_chunker_streams_whole_sentences():
    c = SentenceChunker()
    out = []
    for delta in ["Hello there", ". How are", " you today? I'm Dr. Who", " and I'm fine.\nBye"]:
        out += c.feed(delta)
    out += c.flush()
    assert out == ["Hello there.", "How are you today?", "I'm Dr. Who and I'm fine.", "Bye"]


def test_deepgram_seam_sends_wav_and_returns_transcript():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content
        return httpx.Response(200, json={"results": {"channels": [{"alternatives": [{"transcript": " what's on my list "}]}]}})

    t = DeepgramTranscriber("dg-key", transport=httpx.MockTransport(handler))
    assert t.transcribe(b"RIFFwav") == "what's on my list"
    assert seen["auth"] == "Token dg-key" and "model=nova-3" in seen["url"] and seen["body"] == b"RIFFwav"


def test_deepgram_failure_is_plain_language():
    t = DeepgramTranscriber("k", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(TranscribeError, match="500"):
        t.transcribe(b"x")


class FakePlayer:
    sample_rate = 16000

    def __init__(self, delay=0.0):
        self.played, self.stopped, self.delay = [], False, delay

    def play(self, chunks):
        self.stopped = False
        for c in chunks:
            if self.stopped:
                break
            self.played.append(c)
            time.sleep(self.delay)

    def stop(self):
        self.stopped = True


def test_elevenlabs_seam_streams_pcm_into_player():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"\x00\x01" * 3000)

    player = FakePlayer()
    s = ElevenLabsSpeaker("el-key", "voice123", player, transport=httpx.MockTransport(handler))
    s.speak("Hello.")
    assert "voice123/stream" in seen["url"] and "pcm_16000" in seen["url"]
    assert seen["body"]["text"] == "Hello." and b"".join(player.played) == b"\x00\x01" * 3000


class FakeSpeaker:
    def __init__(self):
        self.spoken, self.stops = [], 0
        self.slow = threading.Event()

    def speak(self, text):
        self.spoken.append(text)
        if self.slow.is_set():
            time.sleep(0.2)

    def stop(self):
        self.stops += 1


class FakeRecorder:
    def start(self):
        self.on = True

    def stop(self):
        self.on = False
        return b"wav"


class FakeTranscriber:
    def __init__(self, text):
        self.text = text

    def transcribe(self, wav):
        return self.text


def make_session(reply, heard="what's on my list", speaker=None):
    agent = Agent(load_config(), FakeProvider([reply]))
    speaker = speaker or FakeSpeaker()
    out = []
    session = VoiceSession(agent, FakeTranscriber(heard), SpeechQueue(speaker), FakeRecorder(), out=out.append)
    return session, speaker, out


def test_spoken_turn_uses_the_same_brain_and_speaks_sentences():
    session, speaker, out = make_session("Your list has one item. Buy brackets.")
    session.key_down()
    session.key_up()
    time.sleep(0.3)
    session.speech.wait(2)
    text = "".join(out)
    assert "you (heard)> what's on my list" in text  # transcript shown next to the reply
    assert "[thinking…]" in text
    assert speaker.spoken == ["Your list has one item.", "Buy brackets."]
    assert session.agent.history[0]["content"] == "what's on my list"


def test_new_turn_interrupts_speech():
    speaker = FakeSpeaker()
    session, _, _ = make_session("One. Two. Three. Four.", speaker=speaker)
    speaker.slow.set()
    session.key_down(); session.key_up()
    time.sleep(0.15)  # first sentence is being spoken
    session.key_down()  # user cuts in
    assert speaker.stops >= 1
    session.speech.wait(2)
    assert len(speaker.spoken) < 4  # the rest was dropped
