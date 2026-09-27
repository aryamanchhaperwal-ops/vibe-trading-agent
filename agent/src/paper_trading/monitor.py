"""Scheduler-backed autonomous paper portfolio monitoring."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from src.live.runtime.scheduler import Job, Scheduler
from src.paper_trading.scanner import scan_nifty500
from src.paper_trading.service import PaperTradingService

logger = logging.getLogger(__name__)


class PaperMonitoringService:
    def __init__(
        self,
        paper: PaperTradingService | None = None,
        *,
        sender: Callable[[str], Awaitable[None]] | None = None,
        scanner: Callable[..., dict[str, Any]] = scan_nifty500,
    ) -> None:
        self.paper = paper or PaperTradingService()
        self.store = self.paper.store
        self.sender = sender
        self.scanner = scanner
        self.last_scan: str | None = None
        self.next_scan: str | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self._scheduler = Scheduler(self._on_job)

    def _cadence(self) -> int:
        configured = self.store.monitor_config()["cadence_seconds"]
        return max(60, int(configured or os.getenv("VIBE_TRADING_PAPER_MONITOR_CADENCE_SECONDS", "900")))

    @staticmethod
    def autonomous_execution_enabled() -> bool:
        """Return whether autonomous paper execution was explicitly opted in."""
        return os.getenv("VIBE_TRADING_PAPER_AUTONOMOUS_EXECUTION", "").strip().lower() in {
            "1", "true", "yes", "on",
        }

    async def start(self) -> None:
        config = self.store.monitor_config()
        if not config["enabled"]:
            await self.stop()
            return
        self._scheduler.add_job(Job("paper-monitor", int(time.time() * 1000) + 100, f"interval:{self._cadence()}000", {"kind": "paper-monitor"}))
        self.next_scan = datetime.now(timezone.utc).isoformat()
        self._scheduler.start()

    async def stop(self) -> None:
        self._scheduler.remove_job("paper-monitor")
        await self._scheduler.stop()

    async def _on_job(self, _: Job) -> None:
        try:
            portfolio_result = await asyncio.to_thread(
                self.paper.monitor_once,
                float(self.store.monitor_config()["movement_threshold"]),
            )
            scan = await asyncio.to_thread(
                self.scanner,
                portfolio_value=portfolio_result["portfolio"]["total_value"],
                max_allocation=float(os.getenv("VIBE_TRADING_PAPER_MAX_ALLOCATION", "0.1")),
                batch_size=int(os.getenv("VIBE_TRADING_NIFTY_BATCH_SIZE", "25")),
            )
            decisions = scan["decisions"]
            for decision in decisions[: int(os.getenv("VIBE_TRADING_PAPER_SCAN_TOP_N", "5"))]:
                self.paper.save_decision(decision)
            alerts = list(portfolio_result["alerts"]) + [
                {
                    "symbol": item["symbol"],
                    "event": "ai_buy_opportunity" if item["decision"] == "BUY" else "ai_sell_recommendation",
                    "message": f"{item['decision']} recommendation for {item['symbol']}: {item['thesis']}",
                }
                for item in decisions[: int(os.getenv("VIBE_TRADING_PAPER_SCAN_TOP_N", "5"))]
                if item["decision"] in {"BUY", "SELL"}
            ]
            cooldown = (datetime.now(timezone.utc) - timedelta(
                minutes=max(1, int(os.getenv("VIBE_TRADING_PAPER_ALERT_COOLDOWN_MINUTES", "60")))
            )).isoformat()
            persisted_alerts: list[dict[str, Any]] = []
            for alert in alerts:
                stored = self.store.create_alert(
                    symbol=alert["symbol"], event=alert["event"], message=alert["message"],
                    dedup_key=f"{alert['event']}:{alert['symbol']}",
                    timestamp=datetime.now(timezone.utc).isoformat(), since=cooldown,
                )
                if stored:
                    persisted_alerts.append(stored)
            alerts = persisted_alerts
            for alert in alerts:
                if self.sender:
                    try:
                        await self.sender(f"[Paper Trading Alert] {alert['event']}: {alert['message']}")
                    except Exception as exc:
                        logger.warning("paper alert stored locally but channel delivery failed: %s", exc)
                        alert["delivery_error"] = f"{type(exc).__name__}: {exc}"
            self.last_result = {
                "portfolio": portfolio_result["portfolio"],
                "scan": scan,
                "alerts": alerts,
            }
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("paper monitor scan failed")
        finally:
            self.last_scan = datetime.now(timezone.utc).isoformat()
            self.next_scan = datetime.fromtimestamp(time.time() + self._cadence(), timezone.utc).isoformat()

    def status(self) -> dict[str, Any]:
        config = self.store.monitor_config()
        return {
            **config,
            "running": self._scheduler._task is not None and not self._scheduler._task.done(),
            "last_scan": self.last_scan,
            "next_scan": self.next_scan,
            "last_error": self.last_error,
            "autonomous_execution_enabled": self.autonomous_execution_enabled(),
            "scanned": (self.last_result or {}).get("scan", {}).get("scanned", 0),
            "latest_opportunities": ((self.last_result or {}).get("scan", {}).get("decisions", [])[:5]),
            "batch_errors": ((self.last_result or {}).get("scan", {}).get("batch_errors", [])),
            "latest_alerts": [self.store.as_dict(row) for row in self.store.alerts(10)],
        }

    def save_config(self, *, enabled: bool, cadence_seconds: int, movement_threshold: float) -> dict[str, Any]:
        if cadence_seconds < 60:
            raise ValueError("cadence_seconds must be at least 60")
        if not 0 < movement_threshold < 1:
            raise ValueError("movement_threshold must be between 0 and 1")
        return self.store.save_monitor_config(
            enabled=enabled, cadence_seconds=cadence_seconds,
            movement_threshold=movement_threshold,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
