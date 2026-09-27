"""Authenticated API for the local paper-trading assistant."""

from __future__ import annotations

import os
import logging
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.api.security import require_auth
from src.paper_trading.research import research_symbol
from src.paper_trading.scanner import nifty500_universe, scan_nifty500
from src.paper_trading.service import PaperTradingService
from src.paper_trading.monitor import PaperMonitoringService
from src.channels.bus.events import OutboundMessage

_paper_monitor: PaperMonitoringService | None = None
logger = logging.getLogger(__name__)


async def _paper_alert_sender(text: str) -> None:
    import sys
    from src.channels.targets import list_delivery_targets

    host = sys.modules.get("api_server")
    manager = getattr(host, "_channel_manager", None) if host else None
    if manager is None:
        logger.info("Alert stored locally; channel runtime is not running.")
        return
    ref = os.getenv("VIBE_TRADING_PAPER_ALERT_TARGET", "").strip()
    targets = list_delivery_targets()
    target = next((item for item in targets if item.ref == ref), None) if ref else (targets[0] if targets else None)
    if target is None:
        logger.info("Alert stored locally; no channel delivery target configured.")
        return
    adapter = manager.get_channel(target.channel)
    if adapter is None:
        logger.warning("Alert stored locally; configured channel %r is unavailable.", target.channel)
        return
    await adapter.send_with_receipt(
        OutboundMessage(channel=target.channel, chat_id=target.target, content=text)
    )


def get_paper_monitor() -> PaperMonitoringService:
    global _paper_monitor
    if _paper_monitor is None:
        _paper_monitor = PaperMonitoringService(sender=_paper_alert_sender)
    return _paper_monitor


async def start_paper_monitor() -> None:
    await get_paper_monitor().start()


async def stop_paper_monitor() -> None:
    if _paper_monitor is not None:
        await _paper_monitor.stop()


class PaperTradeRequest(BaseModel):
    symbol: str
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    reason: str = "manual paper trade"
    decision_id: str | None = None
    rationale: str | None = None


class PaperResetRequest(BaseModel):
    confirmation: str


class PaperDecisionRequest(BaseModel):
    symbol: str
    decision: str
    thesis: str
    technical_reasoning: str
    risks: list[str]
    evidence: list[dict[str, Any]]
    observed_price: float | None = None
    fundamental_reasoning: str | None = None
    news_reasoning: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    suggested_position_size: float | None = Field(default=None, ge=0)
    stop_loss: float | None = Field(default=None, gt=0)


def register_paper_trading_routes(app: FastAPI) -> None:
    def service() -> PaperTradingService:
        return PaperTradingService()

    def monitor() -> PaperMonitoringService:
        return get_paper_monitor()

    @app.get("/api/paper/portfolio", dependencies=[Depends(require_auth)])
    def paper_portfolio():
        return {"status": "ok", "portfolio": service().portfolio()}

    @app.get("/api/paper/trades", dependencies=[Depends(require_auth)])
    def paper_trades(limit: int = 100):
        return {"status": "ok", "trades": [service().store.as_dict(row) for row in service().store.trades(limit)]}

    @app.post("/api/paper/buy", dependencies=[Depends(require_auth)])
    def paper_buy(request: PaperTradeRequest):
        try:
            return {"status": "ok", "trade": service().trade("BUY", **request.model_dump())}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/paper/sell", dependencies=[Depends(require_auth)])
    def paper_sell(request: PaperTradeRequest):
        try:
            return {"status": "ok", "trade": service().trade("SELL", **request.model_dump())}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/paper/reset", dependencies=[Depends(require_auth)])
    def paper_reset(request: PaperResetRequest):
        try:
            return {"status": "ok", "portfolio": service().reset(request.confirmation)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/paper/refresh", dependencies=[Depends(require_auth)])
    def paper_refresh():
        try:
            return {"status": "ok", "portfolio": service().refresh_prices()}
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/paper/decisions", dependencies=[Depends(require_auth)])
    def paper_decisions(limit: int = 50):
        return {"status": "ok", "decisions": [service().store.as_dict(row) for row in service().store.decisions(limit)]}

    @app.post("/api/paper/decisions", dependencies=[Depends(require_auth)])
    def paper_decision(request: PaperDecisionRequest):
        try:
            return {"status": "ok", "decision": service().save_decision(request.model_dump())}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/paper/alerts", dependencies=[Depends(require_auth)])
    def paper_alerts(limit: int = 50):
        return {"status": "ok", "alerts": [service().store.as_dict(row) for row in service().store.alerts(limit)]}

    @app.post("/api/paper/monitor", dependencies=[Depends(require_auth)])
    def paper_monitor():
        try:
            return {"status": "ok", **service().monitor_once()}
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/paper/research/{symbol}", dependencies=[Depends(require_auth)])
    def paper_research(symbol: str):
        try:
            return research_symbol(symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/paper/monitor/status", dependencies=[Depends(require_auth)])
    def paper_monitor_status():
        return {"status": "ok", "monitor": monitor().status()}

    @app.put("/api/paper/monitor/config", dependencies=[Depends(require_auth)])
    async def paper_monitor_config(payload: dict[str, Any]):
        try:
            result = {"status": "ok", "config": monitor().save_config(
                enabled=bool(payload.get("enabled", False)),
                cadence_seconds=int(payload.get("cadence_seconds", 900)),
                movement_threshold=float(payload.get("movement_threshold", 0.05)),
            )}
            await monitor().stop()
            await monitor().start()
            return result
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/paper/scan/nifty500", dependencies=[Depends(require_auth)])
    def paper_scan_nifty500():
        try:
            paper = service()
            result = scan_nifty500(
                universe=nifty500_universe(),
                batch_size=int(os.getenv("VIBE_TRADING_NIFTY_BATCH_SIZE", "25")),
                portfolio_value=paper.portfolio()["total_value"],
                max_allocation=float(os.getenv("VIBE_TRADING_PAPER_MAX_ALLOCATION", "0.1")),
            )
            for decision in result["decisions"][:5]:
                paper.save_decision(decision)
            return result
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
