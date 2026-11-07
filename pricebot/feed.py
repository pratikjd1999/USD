import asyncio
import logging

import httpx

from . import timeutil
from .config import Config
from .models import Quote

log = logging.getLogger(__name__)


class FeedError(Exception):
    pass


class PriceFeed:
    def __init__(self, cfg: Config, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        # No keep-alive: the price API drops idle connections, and reusing a dead one fails with
        # "Server disconnected". One fresh connection every 5 s is cheap.
        self.client = client or httpx.AsyncClient(
            timeout=cfg.request_timeout_sec, limits=httpx.Limits(max_keepalive_connections=0)
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _once(self) -> Quote:
        resp = await self.client.get(self.cfg.price_url, timeout=self.cfg.request_timeout_sec)
        resp.raise_for_status()
        data = resp.json()
        price = float(data["price"])
        if price <= 0:
            raise ValueError(f"non-positive price {price}")
        return Quote(price=price, updated_at=timeutil.parse(data["updatedAt"]), fetched_at=timeutil.now())

    async def fetch(self, retry: bool = True) -> Quote:
        """Fetch the price; with retry, back off 5/15/30 s (configurable) before raising FeedError."""
        delays = self.cfg.retry_backoff_sec if retry else ()
        attempt = 0
        while True:
            try:
                return await self._once()
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
                if attempt >= len(delays):
                    raise FeedError(reason) from exc
                log.warning("price fetch failed (%s); retrying in %ss", reason, delays[attempt])
                await asyncio.sleep(delays[attempt])
                attempt += 1
