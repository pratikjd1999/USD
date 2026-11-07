import re
from decimal import Decimal


class ValidationError(Exception):
    """Message is safe to show to the user."""


_NUMBER = re.compile(r"\d+(\.\d+)?")


def parse_price(text: str) -> Decimal:
    s = text.strip().replace(",", "")
    if s.startswith("$"):
        s = s[1:].strip()
    if not _NUMBER.fullmatch(s):
        raise ValidationError("That doesn't look like a price. Send a number like 85000 or $85,000.50.")
    value = Decimal(s)
    if value <= 0:
        raise ValidationError("The price must be greater than zero.")
    if value != value.quantize(Decimal("0.01")):
        raise ValidationError("Use at most 2 decimal places.")
    return value


def validate_target(text: str, current: float, active_count: int, max_deviation_pct: float, max_alerts: int) -> float:
    if active_count >= max_alerts:
        raise ValidationError(f"You already have {max_alerts} active alerts. Delete one first.")
    target = float(parse_price(text))
    if abs(target - current) < 0.005:
        raise ValidationError("That is the current price. Pick a different target.")
    deviation = abs(target - current) / current * 100
    if deviation > max_deviation_pct:
        low, high = current * (1 - max_deviation_pct / 100), current * (1 + max_deviation_pct / 100)
        raise ValidationError(
            f"That is {deviation:.0f}% away from the current price, which looks like a typo. "
            f"Targets must be within {max_deviation_pct:g}% (${low:,.2f} to ${high:,.2f})."
        )
    return target
