import asyncio
import logging
import time

from . import timeutil
from .alarm import AlarmEngine
from .config import Config
from .db import Database
from .feed import FeedError, PriceFeed
from .health import HealthMonitor
from .models import Alert, Quote, money

log = logging.getLogger(__name__)


def crossed(alert: Alert, price: float) -> bool:
    """An alert fires once the price is at or past its target in the alert's direction.

    This also covers a price that jumped over the target between two polls: the
    previous price was on the near side, the current one is already past it.
    """
    return price >= alert.target if alert.direction == "above" else price <= alert.target


class PriceState:
    def __init__(self):
        self.latest: Quote | None = None
        self.previous: Quote | None = None

    def update(self, quote: Quote) -> None:
        self.previous, self.latest = self.latest, quote


class Watcher:
    def __init__(self, cfg: Config, db: Database, feed: PriceFeed, state: PriceState,
                 engine: AlarmEngine, health: HealthMonitor, notifier):
        self.cfg, self.db, self.feed, self.state, self.engine, self.health = cfg, db, feed, state, engine, health
        self.notifier = notifier

    async def tick(self) -> None:
        try:
            quote = await self.feed.fetch()
        except FeedError as exc:
            log.error("price feed failed: %s", exc)
            self.db.log_price(None, None, False, str(exc))
            await self.health.on_failure(str(exc))
            return

        self.state.update(quote)
        self.db.log_price(quote.price, timeutil.iso(quote.updated_at), True)
        await self.health.on_success(quote)

        fired = [a.id for a in self.db.active_alerts() if crossed(a, quote.price) and self.db.trigger_alert(a.id, quote.price)]
        if not fired:
            return
        alerts = [self.db.get_alert(i) for i in fired]
        log.info("alerts triggered at %s: %s", quote.price, fired)
        if timeutil.in_window(timeutil.now(), self.cfg.alert_window_start, self.cfg.alert_window_end):
            await self.engine.start(alerts)
        else:  # outside the session: close the alerts and send one message, no alarm or call
            self.db.finish_alerts(fired, "outside_session")
            await self.notifier.send(self._outside_session_text(alerts), everyone=True)

    def _outside_session_text(self, alerts: list[Alert]) -> str:
        window = f"{self.cfg.alert_window_start:%H:%M}–{self.cfg.alert_window_end:%H:%M} {timeutil.now():%Z}"
        lines = [f"🔕 Alert #{a.id}: {a.symbol} hit {money(a.trigger_price)} (target {money(a.target)})" for a in alerts]
        return "\n".join(lines) + f"\nTriggered outside session time ({window}). No alarm was raised."

    async def run(self) -> None:
        log.info("watcher started, polling every %ss", self.cfg.poll_interval_sec)
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("watcher tick crashed; continuing")
            await asyncio.sleep(max(0.0, self.cfg.poll_interval_sec - (time.monotonic() - started)))


async def maintenance_loop(cfg: Config, db: Database) -> None:
    """Prune old price_log rows every day at 00:00 IST."""
    while True:
        await asyncio.sleep(timeutil.seconds_until_midnight() + 1)
        try:
            log.info("pruned %d price_log rows", db.prune_price_log(cfg.price_log_keep_days))
        except Exception:
            log.exception("price_log prune failed")
