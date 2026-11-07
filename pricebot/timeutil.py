"""All times in the app are Indian Standard Time (Asia/Kolkata) unless TIMEZONE says otherwise."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_tz = ZoneInfo("Asia/Kolkata")


def set_timezone(name: str) -> None:
    global _tz
    _tz = ZoneInfo(name)


def tz() -> ZoneInfo:
    return _tz


def now() -> datetime:
    return datetime.now(_tz)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).astimezone(_tz).isoformat(timespec="seconds")


def parse(value: str) -> datetime:
    """Parse an ISO 8601 string (a trailing Z means UTC) into an aware datetime in local time."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz)


def fmt(dt: datetime) -> str:
    """18 Oct 2026, 14:30:05 IST"""
    return dt.astimezone(_tz).strftime("%d %b %Y, %H:%M:%S %Z")


def fmt_hm(dt: datetime) -> str:
    """14:30 IST"""
    return dt.astimezone(_tz).strftime("%H:%M %Z")


def seconds_until_midnight(at: datetime | None = None) -> float:
    at = (at or now()).astimezone(_tz)
    midnight = (at + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - at).total_seconds()


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value.strip(), "%H:%M").time()


def in_window(at: datetime, start: time, end: time) -> bool:
    """True if `at` is inside [start, end] at minute precision, so end=23:59 includes 23:59:59."""
    return start <= at.astimezone(_tz).time().replace(second=0, microsecond=0) <= end
