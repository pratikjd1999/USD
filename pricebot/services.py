from dataclasses import dataclass, field

from .alarm import AlarmEngine
from .config import Config
from .db import Database
from .feed import PriceFeed
from .health import HealthMonitor
from .watcher import PriceState


@dataclass
class Services:
    cfg: Config
    db: Database
    feed: PriceFeed
    prices: PriceState
    engine: AlarmEngine
    health: HealthMonitor
    caller: object | None = None
    tasks: list = field(default_factory=list)
