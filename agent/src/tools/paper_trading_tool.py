"""Conversational paper-trading and Indian-equity research tool."""

from __future__ import annotations

import json
from typing import Any

from src.agent.tools import BaseTool
from src.paper_trading.research import research_symbol
from src.paper_trading.service import PaperTradingService


class PaperTradingTool(BaseTool):
    name = "paper_trading"
    description = (
        "Use the local persistent paper portfolio. Supports portfolio/balance/history "
        "queries, validated BUY/SELL trades, explicit reset confirmation, stored "
        "auditable decisions, price refresh, and evidence-based Indian stock research. "
        "Never places real broker orders."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["portfolio", "balance", "history", "buy", "sell", "reset", "research", "refresh", "monitor"],
            },
            "symbol": {"type": "string"},
            "quantity": {"type": "number"},
            "price": {"type": "number"},
            "reason": {"type": "string"},
            "rationale": {"type": "string"},
            "confirmation": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["action"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        action = str(kwargs.get("action") or "").strip().lower()
        service = PaperTradingService()
        if action in {"portfolio", "balance"}:
            payload = {"status": "ok", "portfolio": service.portfolio()}
        elif action == "history":
            payload = {"status": "ok", "trades": [service.store.as_dict(row) for row in service.store.trades(kwargs.get("limit", 100))]}
        elif action in {"buy", "sell"}:
            payload = {"status": "ok", "trade": service.trade(
                action.upper(), kwargs.get("symbol", ""), kwargs.get("quantity", 0),
                price=kwargs.get("price"), reason=kwargs.get("reason", "AI paper trade"),
                rationale=kwargs.get("rationale"),
            )}
        elif action == "reset":
            payload = {"status": "ok", "portfolio": service.reset(str(kwargs.get("confirmation") or ""))}
        elif action == "refresh":
            payload = {"status": "ok", "portfolio": service.refresh_prices()}
        elif action == "monitor":
            payload = {"status": "ok", **service.monitor_once()}
        elif action == "research":
            payload = research_symbol(str(kwargs.get("symbol") or ""))
        else:
            raise ValueError(f"unsupported paper_trading action {action!r}")
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)
