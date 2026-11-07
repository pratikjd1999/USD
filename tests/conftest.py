import pytest

from pricebot.config import Config
from pricebot.db import Database


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[str, bool]] = []
        self.broadcast: list[bool] = []  # the `everyone` flag of each message, in order

    async def send(self, text, *, stop_button=False, everyone=False):
        self.sent.append((text, stop_button))
        self.broadcast.append(everyone)

    @property
    def texts(self):
        return [t for t, _ in self.sent]


@pytest.fixture
def cfg():
    return Config(
        bot_token="x", owner_id=1, msg_interval_sec=0.02, call_ring_sec=0.05, call_pause_sec=0.02,
        alarm_timeout_sec=0.3, retry_backoff_sec=(0.01, 0.01, 0.01), healthcheck_url="http://hc.test/ping",
        heartbeat_min_gap_sec=0.0,
    )


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def notifier():
    return FakeNotifier()


def set_clock(monkeypatch, local: str) -> None:
    """Freeze pricebot.timeutil.now() at e.g. '2026-10-18 02:15' (IST)."""
    from datetime import datetime

    from pricebot import timeutil

    fixed = datetime.fromisoformat(local).replace(tzinfo=timeutil.tz())
    monkeypatch.setattr(timeutil, "now", lambda: fixed)


@pytest.fixture(autouse=True)
def midday(monkeypatch):
    """Default to 12:00 IST (inside the alert window) so tests don't depend on when they run."""
    set_clock(monkeypatch, "2026-10-18 12:00")
