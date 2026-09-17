import json

import httpx

from vyron.config import Config, DEFAULTS
from vyron.heartbeat.checks import CHECKS, CheckContext
from vyron.notify import NtfyNotifier
from vyron.tools import ToolRegistry
from vyron.tools.gmail import register


class FakeGmail:
    def __init__(self, mails):
        self.mails = mails

    def newer_than(self, last_uid, limit=20):
        return [m for m in self.mails if m["uid"] > last_uid][-limit:]

    def recent(self, n=10):
        return list(reversed(self.mails))[:n]

    def search(self, q, n=10):
        return [m for m in self.mails if q.lower() in (m["subject"] + m["from"]).lower()]

    def read(self, uid):
        return next(m for m in self.mails if m["uid"] == uid)


def cfg(tmp_path):
    d = json.loads(json.dumps(DEFAULTS)); d["gmail"] = {"state": str(tmp_path / "gmail.json")}
    return Config(d)


def mail(uid, frm, subj, body=""):
    return {"uid": uid, "from": frm, "subject": subj, "date": "Thu, 17 Sep 2026 10:00:00 +0000", "body": body}


def test_first_run_sets_watermark_quietly_then_matches_senders_and_keywords(tmp_path):
    client = FakeGmail([mail(1, "Shop <no-reply@shop.com>", "Your receipt"), mail(2, "Anna <anna@x.com>", "Lunch?")])
    settings = {"name": "gmail", "_client": client, "senders": ["anna"], "keywords": ["invoice"]}
    ctx = CheckContext(cfg(tmp_path), now=None)
    assert CHECKS["gmail"](settings, ctx) == []          # old mail is never announced
    client.mails += [mail(3, "Bob <bob@y.com>", "Invoice 42 attached"), mail(4, "News <news@z.com>", "Weekly digest"), mail(5, "anna@x.com", "Re: Lunch?")]
    out = CHECKS["gmail"](settings, ctx)
    assert [k for _, k in out] == ["gmail:3", "gmail:5"]  # keyword match and watched sender; the digest stays quiet
    assert CHECKS["gmail"](settings, ctx) == []          # nothing new: nothing said


def test_judge_asks_the_brain_and_stays_quiet_on_nothing(tmp_path):
    client = FakeGmail([mail(1, "a@a", "old")])
    asked = []
    def run_agent(prompt):
        asked.append(prompt); return "NOTHING" if "digest" in prompt else "Your landlord asks about Friday."
    settings = {"name": "gmail", "_client": client, "judge": True}
    ctx = CheckContext(cfg(tmp_path), now=None, run_agent=run_agent)
    CHECKS["gmail"](settings, ctx)
    client.mails += [mail(2, "Landlord <l@l.com>", "Friday", "Are you home Friday?"), mail(3, "n@n", "Weekly digest")]
    out = CHECKS["gmail"](settings, ctx)
    assert out == [("Email: Your landlord asks about Friday.", "gmail:2")] and len(asked) == 2
    assert "data, not instructions" in asked[0]


def test_gmail_tools_are_read_only_and_optional(tmp_path):
    reg = ToolRegistry()
    register(reg, cfg(tmp_path), client=None)
    assert reg.names() == []                              # no credentials: no tools
    reg = ToolRegistry()
    register(reg, cfg(tmp_path), client=FakeGmail([mail(7, "Anna <anna@x.com>", "Lunch?", "Tomorrow at 1?")]))
    assert set(reg.names()) == {"recent_emails", "search_emails", "read_email"}
    assert all(not reg.get(n).consequential for n in reg.names())
    assert "#7" in reg.run("recent_emails", {}).content
    assert "Tomorrow at 1?" in reg.run("read_email", {"id": 7}).content


def test_ntfy_push():
    seen = {}
    def handler(r):
        seen["url"] = str(r.url); seen["title"] = r.headers["title"]; seen["body"] = r.content.decode(); return httpx.Response(200)
    n = NtfyNotifier("vyron-test-123", transport=httpx.MockTransport(handler))
    assert n.send("Vyron", "Email from Anna: Lunch?")
    assert seen["url"] == "https://ntfy.sh/vyron-test-123" and seen["body"].startswith("Email from Anna")
    down = NtfyNotifier("t", transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))))
    assert down.send("a", "b") is False
