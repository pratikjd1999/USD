import os
import sqlite3
from datetime import timedelta

from . import timeutil
from .models import Alert

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    target        REAL NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('above','below')),
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','triggered','done','deleted')),
    created_at    TEXT NOT NULL,
    triggered_at  TEXT,
    trigger_price REAL,
    stopped_at    TEXT,
    stopped_by    TEXT
);
CREATE TABLE IF NOT EXISTS price_log (
    ts                TEXT NOT NULL,
    price             REAL,
    source_updated_at TEXT,
    ok                INTEGER NOT NULL,
    error             TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_log_ts ON price_log (ts);
"""


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _alert(row: sqlite3.Row) -> Alert:
        return Alert(**{k: row[k] for k in row.keys()})

    def add_alert(self, target: float, direction: str, symbol: str) -> Alert:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO alerts (symbol, target, direction, created_at) VALUES (?,?,?,?)",
                (symbol, target, direction, timeutil.iso()),
            )
        return self.get_alert(cur.lastrowid)

    def get_alert(self, alert_id: int) -> Alert | None:
        row = self.conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        return self._alert(row) if row else None

    def active_alerts(self) -> list[Alert]:
        rows = self.conn.execute("SELECT * FROM alerts WHERE status='active' ORDER BY id").fetchall()
        return [self._alert(r) for r in rows]

    def count_active(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM alerts WHERE status='active'").fetchone()[0]

    def delete_alert(self, alert_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("UPDATE alerts SET status='deleted' WHERE id=? AND status='active'", (alert_id,))
        return cur.rowcount == 1

    def trigger_alert(self, alert_id: int, price: float) -> bool:
        """active -> triggered. Returns False if the alert was no longer active, so it can only fire once."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE alerts SET status='triggered', triggered_at=?, trigger_price=? WHERE id=? AND status='active'",
                (timeutil.iso(), price, alert_id),
            )
        return cur.rowcount == 1

    def pending_triggered(self) -> list[Alert]:
        rows = self.conn.execute("SELECT * FROM alerts WHERE status='triggered' ORDER BY id").fetchall()
        return [self._alert(r) for r in rows]

    def finish_alerts(self, ids: list[int], stopped_by: str) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE alerts SET status='done', stopped_at=?, stopped_by=? WHERE id=? AND status='triggered'",
                [(timeutil.iso(), stopped_by, i) for i in ids],
            )

    def log_price(self, price: float | None, source_updated_at: str | None, ok: bool, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO price_log (ts, price, source_updated_at, ok, error) VALUES (?,?,?,?,?)",
                (timeutil.iso(), price, source_updated_at, int(ok), error),
            )

    def prune_price_log(self, keep_days: int) -> int:
        cutoff = timeutil.iso(timeutil.now() - timedelta(days=keep_days))
        with self.conn:
            cur = self.conn.execute("DELETE FROM price_log WHERE ts < ?", (cutoff,))
        return cur.rowcount
