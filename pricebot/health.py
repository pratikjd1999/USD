import asyncio
import logging
import time

import httpx

from . import timeutil
from .config import Config
from .models import Quote

log = logging.getLogger(__name__)


class HealthMonitor:
    """Feed-down and stale-feed warnings plus the Healthchecks.io heartbeat."""

    def __init__(self, cfg: Config, notifier, clock=time.monotonic, ping=None):
        self.cfg = cfg
        self.notifier = notifier
        self.clock = clock
        self._ping = ping or self._http_ping
        self._http: httpx.AsyncClient | None = None
        self.failures = 0
        self.down_since = None
        self.down_notified = False
        self.last_source_update = None
        self.last_change_at = clock()
        self.stale_notified = False
        self.last_ping_at: float | None = None

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()

    async def on_failure(self, error: str) -> None:
        if self.failures == 0:
            self.down_since = timeutil.now()
        self.failures += 1
        if self.failures >= self.cfg.down_after_failures and not self.down_notified:
            self.down_notified = True
            await self.notifier.send(f"⚠️ Price feed down since {timeutil.fmt_hm(self.down_since)}\n{error}")

    async def on_success(self, quote: Quote) -> None:
        if self.down_notified:
            await self.notifier.send("✅ Price feed is back.")
        self.failures, self.down_since, self.down_notified = 0, None, False

        now = self.clock()
        if quote.updated_at != self.last_source_update:
            if self.stale_notified:
                await self.notifier.send("✅ Price feed is updating again.")
            self.last_source_update, self.last_change_at, self.stale_notified = quote.updated_at, now, False
        elif now - self.last_change_at >= self.cfg.stale_after_sec and not self.stale_notified:
            self.stale_notified = True
            await self.notifier.send(
                f"⚠️ Price feed looks stale: no new data since {timeutil.fmt(self.last_source_update)}."
            )

        if self.last_ping_at is None or now - self.last_ping_at >= self.cfg.heartbeat_min_gap_sec:
            self.last_ping_at = now
            asyncio.create_task(self._safe_ping())

    async def _safe_ping(self) -> None:
        if not self.cfg.healthcheck_url:
            return
        try:
            await self._ping(self.cfg.healthcheck_url)
        except Exception as exc:  # a failed heartbeat must never affect the watcher
            log.warning("healthcheck ping failed: %s", exc)

    async def _http_ping(self, url: str) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10)
        (await self._http.get(url)).raise_for_status()
