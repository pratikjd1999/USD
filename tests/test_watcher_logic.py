import httpx
import pytest

from pricebot import timeutil
from pricebot.alarm import AlarmEngine
from pricebot.feed import FeedError, PriceFeed
from pricebot.health import HealthMonitor
from pricebot.models import Alert, Quote
from pricebot.watcher import PriceState, Watcher, crossed


def alert(direction, target):
    return Alert(id=1, symbol="BTC", target=target, direction=direction, status="active", created_at="")


def test_crossed_above():
    assert not crossed(alert("above", 100), 99.99)
    assert crossed(alert("above", 100), 100)  # touching counts
    assert crossed(alert("above", 100), 150)  # jumped far past between two polls


def test_crossed_below():
    assert not crossed(alert("below", 100), 100.01)
    assert crossed(alert("below", 100), 100)
    assert crossed(alert("below", 100), 50)


def test_db_trigger_only_once(db):
    a = db.add_alert(100, "above", "BTC")
    assert db.trigger_alert(a.id, 101)
    assert not db.trigger_alert(a.id, 102)
    assert db.get_alert(a.id).trigger_price == 101


def test_db_lifecycle_and_delete(db):
    a, b = db.add_alert(100, "above", "BTC"), db.add_alert(50, "below", "BTC")
    assert db.count_active() == 2
    assert db.delete_alert(b.id) and not db.delete_alert(b.id)
    assert [x.id for x in db.active_alerts()] == [a.id]
    db.trigger_alert(a.id, 101)
    assert [x.id for x in db.pending_triggered()] == [a.id]
    db.finish_alerts([a.id], "button")
    done = db.get_alert(a.id)
    assert (done.status, done.stopped_by) == ("done", "button") and done.stopped_at


def test_timestamps_are_ist(db):
    a = db.add_alert(100, "above", "BTC")
    assert a.created_at.endswith("+05:30")
    assert timeutil.fmt(timeutil.parse("2026-10-18T08:56:15Z")) == "18 Oct 2026, 14:26:15 IST"


def quote(price, updated="2026-10-18T08:56:15Z"):
    return Quote(price, timeutil.parse(updated), timeutil.now())


class FakeEngine:
    def __init__(self):
        self.started = []

    async def start(self, alerts):
        self.started.append(alerts)


class FakeFeed:
    def __init__(self, *results):
        self.results = list(results)

    async def fetch(self, retry=True):
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


async def test_tick_fires_each_alert_once_and_batches(cfg, db, notifier):
    db.add_alert(100, "above", "BTC")
    db.add_alert(105, "above", "BTC")
    db.add_alert(90, "below", "BTC")
    engine = FakeEngine()
    w = Watcher(cfg, db, FakeFeed(quote(99), quote(106), quote(107)), PriceState(), engine, HealthMonitor(cfg, notifier, ping=lambda u: None), notifier)
    await w.tick()
    assert engine.started == []
    await w.tick()  # jumps over both 100 and 105 in one poll -> one alarm with both
    assert len(engine.started) == 1 and sorted(a.target for a in engine.started[0]) == [100, 105]
    assert all(a.status == "triggered" and a.trigger_price == 106 for a in engine.started[0])
    await w.tick()  # already triggered: must not fire again
    assert len(engine.started) == 1
    assert [a.target for a in db.active_alerts()] == [90]


async def test_tick_failure_logs_and_counts(cfg, db, notifier):
    health = HealthMonitor(cfg, notifier, ping=lambda u: None)
    w = Watcher(cfg, db, FakeFeed(FeedError("boom")), PriceState(), FakeEngine(), health, notifier)
    await w.tick()
    assert health.failures == 1
    row = db.conn.execute("SELECT ok, error FROM price_log").fetchone()
    assert (row["ok"], row["error"]) == (0, "boom")


async def test_feed_retries_then_succeeds(cfg):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"price": 81000.5, "updatedAt": "2026-10-18T08:56:15Z"})

    feed = PriceFeed(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    q = await feed.fetch()
    assert q.price == 81000.5 and len(calls) == 3
    assert q.updated_at.utcoffset().total_seconds() == 5.5 * 3600


async def test_feed_gives_up_after_three_retries(cfg):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    feed = PriceFeed(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(FeedError):
        await feed.fetch()
    assert len(calls) == 4  # first try + 3 retries
    calls.clear()
    with pytest.raises(FeedError):
        await feed.fetch(retry=False)
    assert len(calls) == 1


async def test_feed_rejects_garbage(cfg):
    feed = PriceFeed(cfg, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"oops": 1}))))
    with pytest.raises(FeedError):
        await feed.fetch(retry=False)


# ---- alert session window (05:30-23:59 IST) ----
from datetime import time as dtime  # noqa: E402

from conftest import set_clock  # noqa: E402


@pytest.mark.parametrize("local,inside", [
    ("2026-10-18 00:00", False), ("2026-10-18 02:15", False), ("2026-10-18 05:29", False),
    ("2026-10-18 05:30", True), ("2026-10-18 12:00", True), ("2026-10-18 23:59", True),
])
def test_in_window(local, inside):
    from datetime import datetime
    at = datetime.fromisoformat(local).replace(tzinfo=timeutil.tz())
    assert timeutil.in_window(at, dtime(5, 30), dtime(23, 59)) is inside


def test_in_window_includes_last_second_of_end_minute():
    from datetime import datetime
    at = datetime.fromisoformat("2026-10-18 23:59:59").replace(tzinfo=timeutil.tz())
    assert timeutil.in_window(at, dtime(5, 30), dtime(23, 59))


async def test_hit_outside_session_sends_one_message_and_no_alarm(cfg, db, notifier, monkeypatch):
    set_clock(monkeypatch, "2026-10-18 02:15")
    a, b = db.add_alert(100, "above", "BTC"), db.add_alert(105, "above", "BTC")
    engine = FakeEngine()
    w = Watcher(cfg, db, FakeFeed(quote(106), quote(107)), PriceState(), engine, HealthMonitor(cfg, notifier, ping=lambda u: None), notifier)
    await w.tick()
    assert engine.started == []  # no alarm, no call
    assert len(notifier.sent) == 1  # one message for both alerts
    text = notifier.texts[0]
    assert f"#{a.id}" in text and f"#{b.id}" in text and "$106.00" in text and "outside session time" in text
    assert "05:30–23:59 IST" in text
    for alert_id in (a.id, b.id):
        done = db.get_alert(alert_id)
        assert (done.status, done.stopped_by, done.trigger_price) == ("done", "outside_session", 106)
    await w.tick()  # alerts are consumed: nothing more is sent
    assert len(notifier.sent) == 1


async def test_hit_inside_session_still_rings(cfg, db, notifier, monkeypatch):
    set_clock(monkeypatch, "2026-10-18 05:30")
    db.add_alert(100, "above", "BTC")
    engine = FakeEngine()
    w = Watcher(cfg, db, FakeFeed(quote(101)), PriceState(), engine, HealthMonitor(cfg, notifier, ping=lambda u: None), notifier)
    await w.tick()
    assert len(engine.started) == 1 and notifier.sent == []
