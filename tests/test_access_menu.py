from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from pricebot import timeutil
from pricebot.alarm import AlarmEngine
from pricebot.bot import guard, menu_text
from pricebot.config import ConfigError, Config, load_config
from pricebot.feed import FeedError
from pricebot.health import HealthMonitor
from pricebot.models import Quote
from pricebot.notifier import TelegramNotifier
from pricebot.services import Services
from pricebot.watcher import PriceState


def test_allowed_ids_parsed():
    cfg = load_config({"BOT_TOKEN": "t", "OWNER_ID": "1", "ALLOWED_USER_IDS": "1018649296, 7"})
    assert cfg.allowed_user_ids == (1018649296, 7)
    assert cfg.authorized_ids == {1, 1018649296, 7}
    assert load_config({"BOT_TOKEN": "t", "OWNER_ID": "1"}).authorized_ids == {1}
    with pytest.raises(ConfigError):
        load_config({"BOT_TOKEN": "t", "OWNER_ID": "1", "ALLOWED_USER_IDS": "@sharu357"})


def ctx(cfg, svc=None):
    svc = svc or SimpleNamespace(cfg=cfg)
    return SimpleNamespace(application=SimpleNamespace(bot_data={"svc": svc}))


async def test_guard_allows_owner_and_allowed_blocks_others():
    cfg = Config(bot_token="t", owner_id=1, allowed_user_ids=(1018649296,))
    for uid in (1, 1018649296):
        await guard(SimpleNamespace(effective_user=SimpleNamespace(id=uid, username="x")), ctx(cfg))
    for user in (SimpleNamespace(id=999, username="x"), None):
        with pytest.raises(ApplicationHandlerStop):
            await guard(SimpleNamespace(effective_user=user), ctx(cfg))


class FakeFeed:
    def __init__(self, quote=None):
        self.quote = quote

    async def fetch(self, retry=True):
        if self.quote is None:
            raise FeedError("down")
        return self.quote


def services(cfg, db, notifier, feed, latest=None):
    prices = PriceState()
    if latest:
        prices.update(latest)
    return Services(cfg, db, feed, prices, AlarmEngine(cfg, db, notifier), HealthMonitor(cfg, notifier))


async def test_menu_shows_current_price_in_ist(cfg, db, notifier):
    q = Quote(81635.82, timeutil.parse("2026-10-18T09:00:46Z"), timeutil.now())
    text = await menu_text(services(cfg, db, notifier, FakeFeed(), latest=q))
    assert "BTC: $81,635.82" in text and "IST" in text


async def test_menu_fetches_when_no_price_yet_and_survives_feed_error(cfg, db, notifier):
    q = Quote(100.0, timeutil.now(), timeutil.now())
    assert "$100.00" in await menu_text(services(cfg, db, notifier, FakeFeed(q)))
    assert "unavailable" in await menu_text(services(cfg, db, notifier, FakeFeed(None)))


async def test_notifier_alarm_goes_to_everyone_others_to_owner_only():
    sent = []

    class Bot:
        async def send_message(self, chat_id, text, reply_markup=None):
            if chat_id == 3:
                raise RuntimeError("bot can't message users who haven't pressed Start")
            sent.append(chat_id)

    n = TelegramNotifier(Bot(), 1, (2, 3))
    await n.send("health warning")
    assert sent == [1]
    sent.clear()
    await n.send("alarm", stop_button=True, everyone=True)  # chat 3 fails but must not break chat 2
    assert sent == [1, 2]


async def test_offline_notice_goes_to_everyone(cfg, db, notifier):
    from pricebot.main import announce_offline

    svc = services(cfg, db, notifier, FakeFeed())
    await announce_offline(svc)
    assert len(notifier.sent) == 1 and "going offline" in notifier.texts[0] and "IST" in notifier.texts[0]
    assert notifier.sent[0][1] is False  # no stop button
    assert notifier.broadcast == [True]


async def test_offline_notice_failure_does_not_raise(cfg, db):
    from pricebot.main import announce_offline

    class Broken:
        async def send(self, text, *, stop_button=False, everyone=False):
            raise RuntimeError("network down")

    await announce_offline(services(cfg, db, Broken(), FakeFeed()))


@pytest.mark.parametrize("url,symbol,source", [
    ("https://api.gold-api.com/price/BTC", "BTC", "gold-api.com"),
    ("https://api.gold-api.com/price/xau/", "XAU", "gold-api.com"),
    ("https://example.org/quote/ETH?x=1", "ETH", "example.org"),
])
def test_symbol_and_source_come_from_price_url(url, symbol, source):
    cfg = load_config({"BOT_TOKEN": "t", "OWNER_ID": "1", "PRICE_URL": url})
    assert (cfg.symbol, cfg.source) == (symbol, source)


async def test_status_and_menu_use_symbol_from_url(cfg, db, notifier):
    from dataclasses import replace

    from pricebot.bot import status_text

    xau = replace(cfg, price_url="https://api.gold-api.com/price/XAU")
    q = Quote(2400.5, timeutil.parse("2026-10-18T09:00:46Z"), timeutil.now())
    svc = services(xau, db, notifier, FakeFeed(), latest=q)
    status = status_text(svc)
    assert "XAU: $2,400.50" in status and "Source: gold-api.com" in status and "BTC" not in status
    assert (await menu_text(svc)).startswith("Price Alert Bot\n\nXAU: $2,400.50")
