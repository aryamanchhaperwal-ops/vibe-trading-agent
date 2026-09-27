"""Validated persistent paper-trading operations.

This module deliberately has no broker connector or order-placement dependency.
Prices come from the public market-data fallback chain and all mutations are
local SQLite transactions.
"""

from __future__ import annotations

import os
import uuid
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.market_data import fetch_market_data
from src.paper_trading.store import PaperTradingStore

_SYMBOL_RE = re.compile(r"^[A-Z0-9&.-]{1,24}(?:\.(?:NS|BO|SH|SZ|BJ))?$")
_DEFAULT_STARTING_CASH = 1_000_000.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper().replace(" ", "")
    if value in {"NIFTY", "NIFTY50"}:
        return "^NSEI"
    if value == "NIFTY500":
        return "^CRSLDX"
    if "." not in value and value not in {"^NSEI", "^CRSLDX"}:
        value += ".NS"
    if not _SYMBOL_RE.fullmatch(value) and value not in {"^NSEI", "^CRSLDX"}:
        raise ValueError("invalid Indian equity symbol")
    return value


class PaperTradingService:
    def __init__(
        self,
        store: PaperTradingStore | None = None,
        *,
        price_fetcher: Callable[[str], float] | None = None,
        starting_cash: float | None = None,
    ) -> None:
        self.store = store or PaperTradingStore()
        self._price_fetcher = price_fetcher or self._fetch_price
        self.starting_cash = float(
            starting_cash
            if starting_cash is not None
            else os.getenv("VIBE_TRADING_PAPER_STARTING_CASH", _DEFAULT_STARTING_CASH)
        )
        if self.starting_cash <= 0:
            raise ValueError("starting paper cash must be positive")
        self._ensure_account()

    def _ensure_account(self) -> None:
        if self.store.account() is not None:
            return
        now = _now()
        with self.store._connect() as db:
            db.execute(
                "INSERT INTO paper_account VALUES (1, ?, ?, 0, ?, ?)",
                (self.starting_cash, self.starting_cash, now, now),
            )

    @staticmethod
    def _fetch_price(symbol: str) -> float:
        raw = fetch_market_data(
            codes=[symbol], source="auto", interval="1D",
            start_date=(datetime.now(timezone.utc).date()).isoformat(),
            end_date=datetime.now(timezone.utc).date().isoformat(),
        )
        rows = raw.get(symbol) or []
        if not rows:
            raise ValueError(f"no current market price available for {symbol}")
        price = float(rows[-1].get("close") or 0)
        if price <= 0:
            raise ValueError(f"invalid market price returned for {symbol}")
        return price

    def _price(self, symbol: str, price: float | None) -> float:
        value = float(price) if price is not None else float(self._price_fetcher(symbol))
        if value <= 0:
            raise ValueError("price must be positive")
        return value

    def portfolio(self) -> dict[str, Any]:
        account = self.store.account()
        assert account is not None
        positions = []
        invested = 0.0
        market_value = 0.0
        for row in self.store.positions():
            item = self.store.as_dict(row)
            item["market_value"] = item["quantity"] * item["current_price"]
            item["unrealized_pnl"] = item["quantity"] * (item["current_price"] - item["average_price"])
            item["pnl_percent"] = item["unrealized_pnl"] / (item["quantity"] * item["average_price"])
            invested += item["quantity"] * item["average_price"]
            market_value += item["market_value"]
            positions.append(item)
        total = float(account["cash"]) + market_value
        return {
            "starting_cash": account["starting_cash"],
            "cash": account["cash"],
            "invested_value": invested,
            "market_value": market_value,
            "total_value": total,
            "realized_pnl": account["realized_pnl"],
            "unrealized_pnl": market_value - invested,
            "total_return": (total - account["starting_cash"]) / account["starting_cash"],
            "positions": positions,
            "updated_at": account["updated_at"],
        }

    def trade(
        self, side: str, symbol: str, quantity: float, *,
        price: float | None = None, reason: str = "manual paper trade",
        decision_id: str | None = None, rationale: str | None = None,
    ) -> dict[str, Any]:
        side = str(side).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        symbol = normalize_symbol(symbol)
        quantity = float(quantity)
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        price = self._price(symbol, price)
        fee_rate = max(0.0, float(os.getenv("VIBE_TRADING_PAPER_FEE_RATE", "0.001")))
        value, fees = quantity * price, quantity * price * fee_rate
        now = _now()
        with self.store._connect() as db:
            account = db.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
            position = db.execute("SELECT * FROM paper_positions WHERE symbol = ?", (symbol,)).fetchone()
            current_qty = float(position["quantity"]) if position else 0.0
            realized = 0.0
            if side == "BUY":
                if float(account["cash"]) < value + fees:
                    raise ValueError("insufficient virtual cash")
                new_qty = current_qty + quantity
                average = ((current_qty * float(position["average_price"]) if position else 0) + value + fees) / new_qty
                db.execute("UPDATE paper_account SET cash = cash - ?, updated_at = ? WHERE id = 1", (value + fees, now))
            else:
                if current_qty < quantity:
                    raise ValueError("insufficient paper position")
                average = float(position["average_price"])
                realized = quantity * (price - average) - fees
                new_qty = current_qty - quantity
                db.execute(
                    "UPDATE paper_account SET cash = cash + ?, realized_pnl = realized_pnl + ?, updated_at = ? WHERE id = 1",
                    (value - fees, realized, now),
                )
            if new_qty:
                db.execute(
                    "INSERT INTO paper_positions VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity, average_price=excluded.average_price, current_price=excluded.current_price, updated_at=excluded.updated_at",
                    (symbol, new_qty, average, price, now),
                )
            else:
                db.execute("DELETE FROM paper_positions WHERE symbol = ?", (symbol,))
            trade_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade_id, now, symbol, side, quantity, price, fees, value, realized, reason, decision_id, rationale),
            )
        return {"trade_id": trade_id, "side": side, "symbol": symbol, "quantity": quantity, "price": price, "fees": fees, "realized_pnl": realized, "portfolio": self.portfolio()}

    def refresh_prices(self) -> dict[str, Any]:
        for row in self.store.positions():
            price = self._price(row["symbol"], None)
            with self.store._connect() as db:
                db.execute("UPDATE paper_positions SET current_price = ?, updated_at = ? WHERE symbol = ?", (price, _now(), row["symbol"]))
        return self.portfolio()

    def reset(self, confirmation: str) -> dict[str, Any]:
        if confirmation.strip() != "RESET PAPER PORTFOLIO":
            raise ValueError("explicit confirmation 'RESET PAPER PORTFOLIO' is required")
        now = _now()
        with self.store._connect() as db:
            db.execute("DELETE FROM paper_positions")
            db.execute("DELETE FROM paper_trades")
            db.execute("DELETE FROM paper_decisions")
            db.execute("UPDATE paper_account SET cash = starting_cash, realized_pnl = 0, updated_at = ? WHERE id = 1", (now,))
        return self.portfolio()

    def save_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        required = ("symbol", "decision", "thesis", "technical_reasoning", "risks", "evidence")
        missing = [key for key in required if not decision.get(key)]
        if missing:
            raise ValueError("decision missing: " + ", ".join(missing))
        record = dict(decision)
        record["id"] = str(decision.get("id") or uuid.uuid4().hex)
        record["timestamp"] = str(decision.get("timestamp") or _now())
        with self.store._connect() as db:
            db.execute(
                "INSERT INTO paper_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record["id"], record["timestamp"], normalize_symbol(record["symbol"]),
                 str(record["decision"]).upper(), record.get("observed_price"),
                 record["thesis"], record["technical_reasoning"], record.get("fundamental_reasoning"),
                 record.get("news_reasoning"), __import__("json").dumps(record["risks"]),
                 record.get("confidence"), record.get("suggested_position_size"), record.get("stop_loss"),
                 __import__("json").dumps(record["evidence"])),
            )
        return record

    def monitor_once(self, movement_threshold: float = 0.05) -> dict[str, Any]:
        """Refresh holdings and emit at most one alert per symbol per scan."""
        if not 0 < float(movement_threshold) < 1:
            raise ValueError("movement threshold must be between 0 and 1")
        alerts: list[dict[str, Any]] = []
        cooldown_minutes = max(1, int(os.getenv("VIBE_TRADING_PAPER_ALERT_COOLDOWN_MINUTES", "60")))
        since = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).isoformat()
        for row in self.store.positions():
            previous = float(row["current_price"])
            current = self._price(row["symbol"], None)
            change = (current - previous) / previous
            with self.store._connect() as db:
                db.execute(
                    "UPDATE paper_positions SET current_price = ?, updated_at = ? WHERE symbol = ?",
                    (current, _now(), row["symbol"]),
                )
            if abs(change) >= movement_threshold:
                alert = {
                    "symbol": row["symbol"],
                    "event": "significant_price_movement",
                    "message": f"{row['symbol']} moved {change:.2%} since the last scan.",
                }
                dedup_key = f"price:{row['symbol']}:{'up' if change > 0 else 'down'}"
                stored_alert = self.store.create_alert(
                    symbol=alert["symbol"], event=alert["event"], message=alert["message"],
                    dedup_key=dedup_key, timestamp=_now(), since=since,
                )
                if stored_alert:
                    alerts.append(stored_alert)
            stop_loss = float(os.getenv("VIBE_TRADING_PAPER_STOP_LOSS", "0"))
            take_profit = float(os.getenv("VIBE_TRADING_PAPER_TAKE_PROFIT", "0"))
            pnl_ratio = (current - float(row["average_price"])) / float(row["average_price"])
            trigger = (
                ("stop_loss", pnl_ratio <= -stop_loss) if stop_loss > 0 else None
            ) or (
                ("take_profit", pnl_ratio >= take_profit) if take_profit > 0 else None
            )
            if trigger and trigger[1]:
                event, message = trigger[0], f"{row['symbol']} reached {pnl_ratio:.2%}; user review required."
                stored_alert = self.store.create_alert(
                    symbol=row["symbol"], event=event, message=message,
                    dedup_key=f"{event}:{row['symbol']}", timestamp=_now(), since=since,
                )
                if stored_alert:
                    alerts.append(stored_alert)
        return {"scanned": len(self.store.positions()), "alerts": alerts, "portfolio": self.portfolio()}
