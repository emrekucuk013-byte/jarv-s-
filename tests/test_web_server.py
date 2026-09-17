import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from vyron.cli import build_runtime
from vyron.config import Config, DEFAULTS
from vyron.provider import FakeProvider, ModelReply, ToolCall
from vyron.web.server import WebApp, make_handler


@pytest.fixture
def server(tmp_path):
    data = json.loads(json.dumps(DEFAULTS))
    data["reminders"] = {"path": str(tmp_path / "r.json")}
    data["memory"] = {"path": str(tmp_path / "m.md")}
    data["drafts"] = {"dir": str(tmp_path / "d"), "outbox": str(tmp_path / "o")}
    data["heartbeat"] = {"enabled": False, "state": str(tmp_path / "hb.json"), "inbox": str(tmp_path / "inbox.json"), "checks": []}
    data["safety"] = {"require_confirmation": ["send_message"], "pause_file": str(tmp_path / "PAUSED")}
    data["audit"] = {"path": str(tmp_path / "audit.log")}
    data["web"] = {"confirm_timeout": 5}
    cfg = Config(data)
    provider = FakeProvider()
    rt = build_runtime(cfg, provider)
    app = WebApp(rt, cfg)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    yield httpd.server_address[1], provider, rt
    httpd.shutdown()


def req(port, method, path, body=None, headers=None, stream=False):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    h = {"Content-Type": "application/json"} if isinstance(body, dict) else {}
    h.update(headers or {})
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    if stream:
        return r, c
    out = r.read(); c.close()
    return r.status, (json.loads(out) if r.getheader("Content-Type", "").startswith("application/json") else out)


def events(resp):
    out = []
    for line in resp:
        if line.strip():
            out.append(json.loads(line))
    return out


def test_page_and_state(server):
    port, provider, rt = server
    status, body = req(port, "GET", "/")
    assert status == 200 and b"HOLD TO TALK" in body
    status, st = req(port, "GET", "/api/state")
    assert status == 200 and st["name"] == "Vyron" and st["reminders"] == [] and st["paused"] is False


def test_turn_streams_text_and_tools(server):
    port, provider, rt = server
    provider.script[:] = [ModelReply(text="", tool_calls=[ToolCall("t1", "add_reminder", {"text": "water plants"})]), "Added it."]
    r, c = req(port, "POST", "/api/turn", {"text": "remind me to water plants"}, stream=True)
    evs = events(r); c.close()
    kinds = [e["type"] for e in evs]
    assert "tool" in kinds and kinds[-1] == "done" and "".join(e.get("delta", "") for e in evs).strip() == "Added it."
    assert req(port, "GET", "/api/state")[1]["reminders"][0]["text"] == "water plants"


def test_confirmation_round_trip_over_the_web(server):
    port, provider, rt = server
    provider.script[:] = [ModelReply(text="", tool_calls=[ToolCall("t1", "send_message", {"to": "Sam", "subject": "hi", "body": "yo"})]), "Sent."]
    r, c = req(port, "POST", "/api/turn", {"text": "send it"}, stream=True)
    first = json.loads(next(l for l in r if l.strip()))
    assert first["type"] == "confirm" and "send_message" in first["description"]
    assert req(port, "POST", "/api/confirm", {"id": first["id"], "approved": True})[1]["ok"]
    rest = events(r); c.close()
    tool = next(e for e in rest if e["type"] == "tool")
    assert tool["ok"] and not tool["declined"]


def test_unanswered_confirmation_defaults_to_no(server):
    port, provider, rt = server
    rt_app_timeout = 5
    provider.script[:] = [ModelReply(text="", tool_calls=[ToolCall("t1", "send_message", {"to": "A", "subject": "b", "body": "c"})]), "Not sent."]
    t0 = time.time()
    r, c = req(port, "POST", "/api/turn", {"text": "send"}, stream=True)
    evs = events(r); c.close()
    tool = next(e for e in evs if e["type"] == "tool")
    assert tool["declined"] and time.time() - t0 >= rt_app_timeout - 0.5


def test_dismiss_memory_and_pause(server):
    port, provider, rt = server
    rt.inbox.add("test", "log", "something happened")
    assert len(req(port, "GET", "/api/state")[1]["notices"]) == 1
    assert req(port, "POST", "/api/dismiss", {"id": 0})[1]["dismissed"] == 1
    assert req(port, "POST", "/api/memory", {"facts": ["Likes tea.", ""]})[1]["count"] == 1
    assert req(port, "GET", "/api/state")[1]["memories"] == ["Likes tea."]
    assert req(port, "POST", "/api/pause", {})[1]["paused"] is True
    assert req(port, "POST", "/api/resume", {})[1]["paused"] is False


def test_stt_and_tts_without_keys_say_so(server):
    port, provider, rt = server
    assert req(port, "POST", "/api/stt", b"\x00\x01", headers={"Content-Type": "audio/wav"})[0] == 404
    assert req(port, "GET", "/api/tts?text=hi")[0] == 404
