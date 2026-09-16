import json
from datetime import datetime, timedelta

from vyron.config import Config, DEFAULTS
from vyron.heartbeat import Heartbeat, Inbox
from vyron.heartbeat.checks import parse_interval
from vyron.heartbeat.loop import in_quiet_hours


def make_config(tmp_path, checks, **hb):
    data = json.loads(json.dumps(DEFAULTS))
    data["heartbeat"] = {"tick_seconds": 1, "state": str(tmp_path / "hb.json"), "checks": checks, **hb}
    data["reminders"] = {"path": str(tmp_path / "reminders.json")}
    return Config(data)


class FakeClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def run_tick_sync(hb: Heartbeat, now):
    """Drive one tick and wait for check threads, so tests are deterministic."""
    hb.clock.t = now
    hb.tick()
    import time
    for _ in range(100):
        with hb._lock:
            if not hb._running:
                break
        time.sleep(0.01)
    hb.deliver(now)


def test_intervals():
    assert parse_interval("30s") == timedelta(seconds=30)
    assert parse_interval("5m") == timedelta(minutes=5)
    assert parse_interval("2h") == timedelta(hours=2)
    assert parse_interval(7) == timedelta(seconds=7)


def test_quiet_hours_wrap_midnight():
    assert in_quiet_hours(datetime(2026, 1, 1, 23, 30), "22:00", "07:00")
    assert in_quiet_hours(datetime(2026, 1, 1, 6, 59), "22:00", "07:00")
    assert not in_quiet_hours(datetime(2026, 1, 1, 12, 0), "22:00", "07:00")
    assert not in_quiet_hours(datetime(2026, 1, 1, 12, 0), None, None)


def test_file_watch_surfaces_once_and_is_held_until_dismissed(tmp_path):
    trigger = tmp_path / "trigger.txt"
    cfg = make_config(tmp_path, [{"name": "t", "check": "file_watch", "every": "10s", "path": str(trigger), "level": "log"}])
    inbox = Inbox(tmp_path / "inbox.json")
    announced = []
    hb = Heartbeat(cfg, inbox, announce=announced.append, clock=FakeClock(datetime(2026, 1, 1, 12, 0)))
    now = datetime(2026, 1, 1, 12, 0)
    run_tick_sync(hb, now)
    assert inbox.pending() == []            # quiet by default: nothing happened, nothing surfaced

    trigger.write_text("deploy finished")   # trigger the condition on purpose
    run_tick_sync(hb, now + timedelta(seconds=11))
    run_tick_sync(hb, now + timedelta(seconds=22))
    pending = inbox.pending()
    assert len(pending) == 1 and "deploy finished" in pending[0].text   # once, not twice
    assert announced == []                  # log level: waits in the calm log, no interruption

    # "close the interface and reopen": a fresh Inbox from disk still holds it
    again = Inbox(tmp_path / "inbox.json")
    assert [n.text for n in again.pending()] == [pending[0].text]
    assert again.dismiss(pending[0].id) and again.pending() == []
    assert Inbox(tmp_path / "inbox.json").pending() == []   # dismissal persisted


def test_interrupt_level_respects_quiet_hours(tmp_path):
    (tmp_path / "reminders.json").write_text(json.dumps(
        [{"id": 1, "text": "call mum", "due": "2026-01-01T23:00", "done": False}]))
    cfg = make_config(tmp_path, [{"name": "due", "check": "due_reminders", "every": "1m", "level": "interrupt"}],
                      quiet_hours_start="22:00", quiet_hours_end="07:00")
    inbox = Inbox(tmp_path / "inbox.json")
    announced = []
    hb = Heartbeat(cfg, inbox, announce=announced.append, clock=FakeClock(datetime(2026, 1, 1, 23, 5)))
    run_tick_sync(hb, datetime(2026, 1, 1, 23, 5))
    assert len(inbox.pending()) == 1 and announced == []   # held: it's late
    run_tick_sync(hb, datetime(2026, 1, 2, 7, 1))
    assert [n.text for n in announced] == ["Reminder due: call mum"]   # delivered when quiet hours end
    run_tick_sync(hb, datetime(2026, 1, 2, 7, 2))
    assert len(announced) == 1              # announced once only


def test_schedule_survives_restart(tmp_path):
    cfg = make_config(tmp_path, [{"name": "t", "check": "file_watch", "every": "1h", "path": str(tmp_path / "x")}])
    inbox = Inbox(tmp_path / "inbox.json")
    t0 = datetime(2026, 1, 1, 12, 0)
    hb = Heartbeat(cfg, inbox, clock=FakeClock(t0))
    run_tick_sync(hb, t0)
    assert hb.next_due["t"] == t0 + timedelta(hours=1)

    hb2 = Heartbeat(cfg, inbox, clock=FakeClock(t0 + timedelta(minutes=5)))   # restart 5 minutes later
    assert hb2.next_due["t"] == t0 + timedelta(hours=1)
    ran = []
    hb2.audit = lambda kind, data: ran.append(kind)
    run_tick_sync(hb2, t0 + timedelta(minutes=5))
    assert "check_ran" not in ran           # did not refire on boot
    run_tick_sync(hb2, t0 + timedelta(hours=1, minutes=1))
    assert "check_ran" in ran


def test_overlapping_runs_are_skipped_and_failures_logged(tmp_path):
    import threading
    from vyron.heartbeat import checks
    gate = threading.Event()

    @checks.check("slow")
    def slow(settings, ctx):
        gate.wait(2)
        return []

    @checks.check("broken")
    def broken(settings, ctx):
        raise RuntimeError("boom")

    cfg = make_config(tmp_path, [{"name": "s", "check": "slow", "every": "1s"}, {"name": "b", "check": "broken", "every": "1s"}])
    events = []
    hb = Heartbeat(cfg, Inbox(tmp_path / "inbox.json"), clock=FakeClock(datetime(2026, 1, 1)),
                   audit=lambda kind, data: events.append((kind, data)))
    hb.tick()
    hb.clock.t = datetime(2026, 1, 1, 0, 0, 5)
    hb.tick()
    gate.set()
    run_tick_sync(hb, datetime(2026, 1, 1, 0, 0, 6))
    kinds = [k for k, _ in events]
    assert "check_skipped_overlap" in kinds
    assert any(k == "check_failed" and d["check"] == "b" and "boom" in d["error"] for k, d in events)
    checks.CHECKS.pop("slow"); checks.CHECKS.pop("broken")


def test_ask_agent_check_is_quiet_on_nothing(tmp_path):
    cfg = make_config(tmp_path, [{"name": "look", "check": "ask_agent", "every": "1h", "prompt": "Anything due?", "level": "log"}])
    inbox = Inbox(tmp_path / "inbox.json")
    replies = iter(["NOTHING", "Two reminders are overdue."])
    hb = Heartbeat(cfg, inbox, run_agent=lambda p: next(replies), clock=FakeClock(datetime(2026, 1, 1, 9)))
    run_tick_sync(hb, datetime(2026, 1, 1, 9))
    assert inbox.pending() == []
    run_tick_sync(hb, datetime(2026, 1, 1, 10, 1))
    assert [n.text for n in inbox.pending()] == ["Two reminders are overdue."]
