import asyncio
import logging
from datetime import datetime

from dotenv import load_dotenv
from telegram.ext import Application

from . import timeutil
from .alarm import AlarmEngine
from .bot import register_handlers
from .config import load_config
from .db import Database
from .feed import PriceFeed
from .health import HealthMonitor
from .notifier import TelegramNotifier
from .services import Services
from .watcher import PriceState, Watcher, maintenance_loop

log = logging.getLogger("pricebot")


class LocalTimeFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, timeutil.tz()).strftime("%Y-%m-%d %H:%M:%S %Z")


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(LocalTimeFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per poll is too noisy
    logging.getLogger("telegram").setLevel(logging.WARNING)


async def startup(app: Application) -> None:
    svc: Services = app.bot_data["svc"]
    notifier = svc.engine.notifier
    await app.bot.set_my_commands([("menu", "Open the alert menu"), ("start", "Open the alert menu")])
    if svc.cfg.caller_enabled:
        from .caller import Caller  # imported lazily so Telethon is only needed when calls are on
        caller = Caller(svc.cfg)
        try:
            await caller.start()
            svc.caller = svc.engine.caller = caller
        except Exception as exc:
            log.error("caller disabled: %s", exc)
            await notifier.send(f"⚠️ Caller account unavailable, so no voice calls: {exc}")
    else:
        log.info("API_ID/API_HASH not set: running with bot messages only (no calls)")

    watcher = Watcher(svc.cfg, svc.db, svc.feed, svc.prices, svc.engine, svc.health, notifier)
    svc.tasks += [asyncio.create_task(watcher.run()), asyncio.create_task(maintenance_loop(svc.cfg, svc.db))]
    await svc.engine.resume()
    await notifier.send(f"🟢 Price alert bot is up and running ({timeutil.fmt(timeutil.now())}). Send /menu.", everyone=True)


async def announce_offline(svc: Services) -> None:
    """Tell everyone the bot is stopping, so they know alerts won't fire until it is started again."""
    text = (f"🔴 Price alert bot is going offline ({timeutil.fmt(timeutil.now())}). "
            "Alerts will not fire until it is started again.")
    try:
        await asyncio.wait_for(svc.engine.notifier.send(text, everyone=True), timeout=10)
    except Exception as exc:  # never let the goodbye message block shutdown
        log.warning("could not send offline notice: %s", exc)


async def on_stop(app: Application) -> None:
    await announce_offline(app.bot_data["svc"])


async def shutdown(app: Application) -> None:
    svc: Services = app.bot_data["svc"]
    for task in svc.tasks:
        task.cancel()
    await asyncio.gather(*svc.tasks, return_exceptions=True)
    if svc.engine._task:
        svc.engine._task.cancel()
        await asyncio.gather(svc.engine._task, return_exceptions=True)
    if svc.caller:
        await svc.caller.stop()
    await svc.health.close()
    await svc.feed.close()
    svc.db.close()


def main() -> None:
    load_dotenv()
    cfg = load_config()
    timeutil.set_timezone(cfg.timezone)
    setup_logging()

    app = Application.builder().token(cfg.bot_token).post_init(startup).post_stop(on_stop).post_shutdown(shutdown).build()
    notifier = TelegramNotifier(app.bot, cfg.owner_id, cfg.allowed_user_ids)
    db = Database(cfg.db_path)
    engine = AlarmEngine(cfg, db, notifier)
    app.bot_data["svc"] = Services(
        cfg=cfg, db=db, feed=PriceFeed(cfg), prices=PriceState(), engine=engine,
        health=HealthMonitor(cfg, notifier),
    )
    register_handlers(app)
    log.info("starting (timezone %s, owner %s)", cfg.timezone, cfg.owner_id)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
