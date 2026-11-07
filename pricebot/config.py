import os
from dataclasses import dataclass, field
from datetime import time
from urllib.parse import urlparse

from . import timeutil


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    allowed_user_ids: tuple[int, ...] = ()
    api_id: int | None = None
    api_hash: str | None = None
    caller_session_path: str = "data/caller.session"
    price_url: str = "https://api.gold-api.com/price/BTC"
    poll_interval_sec: float = 5
    timezone: str = "Asia/Kolkata"
    alert_window_start: time = time(5, 30)
    alert_window_end: time = time(23, 59)
    max_deviation_pct: float = 30
    max_active_alerts: int = 20
    msg_interval_sec: float = 5
    call_ring_sec: float = 30
    call_pause_sec: float = 5
    alarm_timeout_sec: float = 120
    healthcheck_url: str | None = None
    db_path: str = "data/alerts.db"
    request_timeout_sec: float = 10
    retry_backoff_sec: tuple[float, ...] = field(default=(5, 15, 30))
    down_after_failures: int = 3
    stale_after_sec: float = 15 * 60
    heartbeat_min_gap_sec: float = 60
    price_log_keep_days: int = 7

    @property
    def symbol(self) -> str:
        """Last path segment of PRICE_URL, e.g. .../price/BTC -> BTC."""
        return urlparse(self.price_url).path.rstrip("/").rsplit("/", 1)[-1].upper() or "PRICE"

    @property
    def source(self) -> str:
        """Host of PRICE_URL without a leading 'api.', e.g. api.gold-api.com -> gold-api.com."""
        host = urlparse(self.price_url).hostname or "unknown source"
        return host.removeprefix("api.")

    @property
    def authorized_ids(self) -> frozenset[int]:
        return frozenset((self.owner_id, *self.allowed_user_ids))

    @property
    def caller_enabled(self) -> bool:
        return self.api_id is not None and bool(self.api_hash)


def load_config(env=None) -> Config:
    env = os.environ if env is None else env

    def get(key, default=None):
        value = (env.get(key) or "").strip()
        return value if value else default

    def num(key, default, cast=float):
        raw = get(key)
        if raw is None:
            return default
        try:
            return cast(raw)
        except ValueError:
            raise ConfigError(f"{key} must be a number, got {raw!r}")

    token, owner = get("BOT_TOKEN"), get("OWNER_ID")
    if not token or not owner:
        raise ConfigError("BOT_TOKEN and OWNER_ID are required (see .env.example)")
    try:
        owner_id = int(owner)
    except ValueError:
        raise ConfigError(f"OWNER_ID must be a number, got {owner!r}")

    try:
        allowed = tuple(int(x) for x in (get("ALLOWED_USER_IDS") or "").replace(" ", "").split(",") if x)
    except ValueError:
        raise ConfigError("ALLOWED_USER_IDS must be comma-separated numeric Telegram user IDs")

    try:
        window = {
            "alert_window_start": timeutil.parse_hhmm(get("ALERT_WINDOW_START", "05:30")),
            "alert_window_end": timeutil.parse_hhmm(get("ALERT_WINDOW_END", "23:59")),
        }
    except ValueError:
        raise ConfigError("ALERT_WINDOW_START/ALERT_WINDOW_END must look like HH:MM, e.g. 05:30")
    if window["alert_window_start"] > window["alert_window_end"]:
        raise ConfigError("ALERT_WINDOW_START must not be later than ALERT_WINDOW_END")

    return Config(
        bot_token=token,
        owner_id=owner_id,
        allowed_user_ids=allowed,
        api_id=num("API_ID", None, int),
        api_hash=get("API_HASH"),
        caller_session_path=get("CALLER_SESSION_PATH", "data/caller.session"),
        price_url=get("PRICE_URL", "https://api.gold-api.com/price/BTC"),
        poll_interval_sec=num("POLL_INTERVAL_SEC", 5),
        timezone=get("TIMEZONE", "Asia/Kolkata"),
        **window,
        max_deviation_pct=num("MAX_DEVIATION_PCT", 30),
        max_active_alerts=num("MAX_ACTIVE_ALERTS", 20, int),
        msg_interval_sec=num("MSG_INTERVAL_SEC", 5),
        call_ring_sec=num("CALL_RING_SEC", 30),
        call_pause_sec=num("CALL_PAUSE_SEC", 5),
        alarm_timeout_sec=num("ALARM_TIMEOUT_SEC", 120),
        healthcheck_url=get("HEALTHCHECK_URL"),
        db_path=get("DB_PATH", "data/alerts.db"),
    )
