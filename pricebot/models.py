from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Quote:
    price: float
    updated_at: datetime  # source timestamp, local time
    fetched_at: datetime


@dataclass(frozen=True)
class Alert:
    id: int
    symbol: str
    target: float
    direction: str  # "above" | "below"
    status: str  # active | triggered | done | deleted
    created_at: str
    triggered_at: str | None = None
    trigger_price: float | None = None
    stopped_at: str | None = None
    stopped_by: str | None = None

    @property
    def phrase(self) -> str:
        return "rises to" if self.direction == "above" else "falls to"


def money(value: float) -> str:
    return f"${value:,.2f}"
