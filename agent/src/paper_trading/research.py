"""Evidence-based Indian equity research helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.market_data import fetch_market_data
from src.paper_trading.service import normalize_symbol


def research_symbol(symbol: str, *, fetcher=fetch_market_data) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=120)
    raw = fetcher(codes=[normalized], source="auto", interval="1D", start_date=start.isoformat(), end_date=end.isoformat())
    rows = raw.get(normalized) or []
    if not rows:
        return {"status": "unavailable", "symbol": normalized, "missing": ["market_data"]}
    closes = [float(row["close"]) for row in rows if float(row.get("close") or 0) > 0]
    latest = closes[-1]
    previous = closes[-6] if len(closes) >= 6 else closes[0]
    sma20 = sum(closes[-20:]) / min(20, len(closes))
    return {
        "status": "ok", "symbol": normalized, "observed_at": datetime.now(timezone.utc).isoformat(),
        "market_data": {"price": latest, "period_return": latest / previous - 1, "sma20": sma20, "bars": len(closes)},
        "technical_analysis": {"trend": "above_sma20" if latest >= sma20 else "below_sma20"},
        "fundamentals": {"status": "not_available", "note": "No fundamental provider was queried."},
        "news": {"status": "not_available", "note": "No news provider was queried."},
        "ai_interpretation": "Evidence is limited to the returned market data; this is not investment advice.",
    }
