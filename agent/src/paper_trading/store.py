"""SQLite storage for the paper portfolio and its audit trail."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src.config.paths import get_runtime_root


class PaperTradingStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or get_runtime_root() / "paper_trading" / "paper.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    starting_cash REAL NOT NULL,
                    cash REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL NOT NULL CHECK (quantity >= 0),
                    average_price REAL NOT NULL CHECK (average_price > 0),
                    current_price REAL NOT NULL CHECK (current_price > 0),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                    quantity REAL NOT NULL CHECK (quantity > 0),
                    price REAL NOT NULL CHECK (price > 0),
                    fees REAL NOT NULL DEFAULT 0,
                    value REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    decision_id TEXT,
                    rationale TEXT
                );
                CREATE TABLE IF NOT EXISTS paper_decisions (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    observed_price REAL,
                    thesis TEXT NOT NULL,
                    technical_reasoning TEXT NOT NULL,
                    fundamental_reasoning TEXT,
                    news_reasoning TEXT,
                    risks TEXT NOT NULL,
                    confidence REAL,
                    suggested_position_size REAL,
                    stop_loss REAL,
                    evidence TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_paper_trades_timestamp
                    ON paper_trades(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_paper_decisions_timestamp
                    ON paper_decisions(timestamp DESC);
                CREATE TABLE IF NOT EXISTS paper_monitor_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    cadence_seconds INTEGER NOT NULL DEFAULT 900,
                    movement_threshold REAL NOT NULL DEFAULT 0.05,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_alerts (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    event TEXT NOT NULL,
                    message TEXT NOT NULL,
                    dedup_key TEXT,
                    acknowledged INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_paper_alerts_dedup
                    ON paper_alerts(dedup_key, timestamp DESC);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(paper_alerts)")}
            if "dedup_key" not in columns:
                db.execute("ALTER TABLE paper_alerts ADD COLUMN dedup_key TEXT")

    def account(self) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()

    def positions(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT * FROM paper_positions ORDER BY symbol").fetchall()

    def trades(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM paper_trades ORDER BY timestamp DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()

    def decisions(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM paper_decisions ORDER BY timestamp DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()

    def alerts(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM paper_alerts ORDER BY timestamp DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()

    def recent_alert_exists(self, dedup_key: str, since: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM paper_alerts WHERE dedup_key = ? AND timestamp >= ? LIMIT 1",
                (dedup_key, since),
            ).fetchone() is not None

    def create_alert(
        self, *, symbol: str, event: str, message: str, dedup_key: str,
        timestamp: str, since: str,
    ) -> dict[str, Any] | None:
        with self._connect() as db:
            if db.execute(
                "SELECT 1 FROM paper_alerts WHERE dedup_key = ? AND timestamp >= ? LIMIT 1",
                (dedup_key, since),
            ).fetchone():
                return None
            alert = {
                "id": uuid.uuid4().hex,
                "timestamp": timestamp,
                "symbol": symbol,
                "event": event,
                "message": message,
                "dedup_key": dedup_key,
            }
            db.execute(
                "INSERT INTO paper_alerts (id, timestamp, symbol, event, message, dedup_key) VALUES (?, ?, ?, ?, ?, ?)",
                tuple(alert.values()),
            )
            return alert

    def monitor_config(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM paper_monitor_config WHERE id = 1").fetchone()
        if row is None:
            return {"enabled": False, "cadence_seconds": 900, "movement_threshold": 0.05, "updated_at": None}
        return {
            "enabled": bool(row["enabled"]),
            "cadence_seconds": row["cadence_seconds"],
            "movement_threshold": row["movement_threshold"],
            "updated_at": row["updated_at"],
        }

    def save_monitor_config(self, *, enabled: bool, cadence_seconds: int, movement_threshold: float, updated_at: str) -> dict[str, Any]:
        with self._connect() as db:
            db.execute(
                "INSERT INTO paper_monitor_config VALUES (1, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, cadence_seconds=excluded.cadence_seconds, movement_threshold=excluded.movement_threshold, updated_at=excluded.updated_at",
                (int(enabled), cadence_seconds, movement_threshold, updated_at),
            )
        return self.monitor_config()

    @staticmethod
    def as_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("evidence", "risks"):
            if isinstance(result.get(key), str):
                result[key] = json.loads(result[key])
        return result
