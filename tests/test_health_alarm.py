import asyncio

from pricebot import timeutil
from pricebot.alarm import AlarmEngine
from pricebot.health import HealthMonitor
from pricebot.models import Quote


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def q(updated):
    return Quote(81000.0, timeutil.parse(updated), timeutil.now())


async def test_feed_down_after_three_failures_then_recovery(cfg, notifier):
    h = HealthMonitor(cfg, notifier, ping=lambda u: None)
    await h.on_failure("e1")
    await h.on_failure("e2")
    assert notifier.sent == []
    await h.on_failure("e3")
    await h.on_failure("e4")  # only one warning
    assert len(notifier.sent) == 1 and "Price feed down since" in notifier.texts[0] and "IST" in notifier.texts[0]
    await h.on_success(q("2026-10-18T08:56:15Z"))
    assert "back" in notifier.texts[-1] and h.failures == 0


async def test_stale_feed_warning(cfg, notifier):
    clock = Clock()
    h = HealthMonitor(cfg, notifier, clock=clock, ping=lambda u: None)
    await h.on_success(q("2026-10-18T08:00:00Z"))
    clock.t = 14 * 60
    await h.on_success(q("2026-10-18T08:00:00Z"))
    assert notifier.sent == []
    clock.t = 15 * 60
    await h.on_success(q("2026-10-18T08:00:00Z"))
    await h.on_success(q("2026-10-18T08:00:00Z"))
    assert len(notifier.sent) == 1 and "stale" in notifier.texts[0]
    await h.on_success(q("2026-10-18T08:15:00Z"))
    assert "updating again" in notifier.texts[-1]


async def test_heartbeat_throttled_and_failure_safe(cfg, notifier):
    pings = []

    async def ping(url):
        pings.append(url)
        raise RuntimeError("network down")  # must not propagate

    clock = Clock()
    object.__setattr__(cfg, "heartbeat_min_gap_sec", 60)
    h = HealthMonitor(cfg, notifier, clock=clock, ping=ping)
    await h.on_success(q("2026-10-18T08:00:00Z"))
    clock.t = 10
    await h.on_success(q("2026-10-18T08:00:05Z"))
    clock.t = 61
    await h.on_success(q("2026-10-18T08:00:10Z"))
    await asyncio.sleep(0.01)
    assert pings == ["http://hc.test/ping"] * 2


class FakeCaller:
    def __init__(self, answers_on=None):
        self.rings, self.hangups, self.answers_on = 0, 0, answers_on

    async def ring(self, seconds):
        self.rings += 1
        await asyncio.sleep(seconds)
        return self.rings == self.answers_on

    async def hangup(self):
        self.hangups += 1


def triggered(db, *targets):
    out = []
    for t in targets:
        a = db.add_alert(t, "above", "BTC")
        db.trigger_alert(a.id, t + 1)
        out.append(db.get_alert(a.id))
    return out


async def test_stop_button_ends_everything(cfg, db, notifier):
    caller = FakeCaller()
    engine = AlarmEngine(cfg, db, notifier, caller)
    alerts = triggered(db, 100)
    await engine.start(alerts)
    await asyncio.sleep(0.1)
    assert engine.running and any(stop for _, stop in notifier.sent)
    assert engine.stop("button") and not engine.stop("button")
    await engine.wait()
    assert notifier.texts[-1] == "✅ Alarm stopped."
    assert caller.hangups >= 1
    done = db.get_alert(alerts[0].id)
    assert (done.status, done.stopped_by) == ("done", "button")
    count = len(notifier.sent)
    await asyncio.sleep(0.1)
    assert len(notifier.sent) == count  # message loop really stopped


async def test_answering_call_stops_alarm(cfg, db, notifier):
    engine = AlarmEngine(cfg, db, notifier, FakeCaller(answers_on=2))
    alerts = triggered(db, 100)
    await engine.start(alerts)
    await engine.wait()
    assert db.get_alert(alerts[0].id).stopped_by == "call_answered"


async def test_timeout_stops_alarm(cfg, db, notifier):
    engine = AlarmEngine(cfg, db, notifier, FakeCaller())
    alerts = triggered(db, 100)
    await engine.start(alerts)
    await engine.wait()
    assert notifier.texts[-1] == "⏰ Alarm timed out."
    assert db.get_alert(alerts[0].id).stopped_by == "timeout"
    assert not engine.running


async def test_works_without_caller_and_merges_new_hits(cfg, db, notifier):
    engine = AlarmEngine(cfg, db, notifier, caller=None)
    first, second = triggered(db, 100), triggered(db, 200)
    await engine.start(first)
    await engine.start(second)  # joins the running alarm
    assert "target $200.00" in notifier.texts[-1] and "target $100.00" in notifier.texts[-1]
    engine.stop("menu")
    await engine.wait()
    assert {db.get_alert(a.id).status for a in first + second} == {"done"}


async def test_resume_after_restart(cfg, db, notifier):
    alerts = triggered(db, 100)  # app died after marking triggered, before stop
    engine = AlarmEngine(cfg, db, notifier)
    await engine.resume()
    assert engine.running and "resuming" in notifier.texts[0].lower()
    engine.stop("button")
    await engine.wait()
    assert db.get_alert(alerts[0].id).status == "done"


async def test_failing_caller_does_not_stop_messages(cfg, db, notifier):
    class Broken(FakeCaller):
        async def ring(self, seconds):
            return False  # e.g. privacy settings: returns instantly

    engine = AlarmEngine(cfg, db, notifier, Broken())
    await engine.start(triggered(db, 100))
    await asyncio.sleep(0.15)
    assert len(notifier.sent) >= 3
    engine.stop("button")
    await engine.wait()
