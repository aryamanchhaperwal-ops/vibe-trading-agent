"""Batched, evidence-based NIFTY 500 paper-opportunity scanner."""

from __future__ import annotations

import csv
import io
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from src.market_data import fetch_market_data
from src.paper_trading.service import normalize_symbol

_NIFTY_500_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"


def nifty500_universe(
    *,
    opener: Callable[..., Any] | None = None,
    configured_path: str | None = None,
) -> list[str]:
    """Load the current constituent list from a local override or NSE index CSV."""
    path = configured_path or os.getenv("VIBE_TRADING_NIFTY500_FILE")
    if path:
        with open(path, encoding="utf-8", newline="") as handle:
            rows = csv.DictReader(handle)
            values = [str(row.get("Symbol") or row.get("symbol") or "").strip() for row in rows]
    else:
        request = urllib.request.Request(_NIFTY_500_URL, headers={"User-Agent": "Vibe-Trading/1.0"})
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            values = [
                str(row.get("Symbol") or "").strip()
                for row in csv.DictReader(io.TextIOWrapper(response, encoding="utf-8-sig"))
            ]
    symbols = []
    for value in values:
        if value:
            try:
                symbols.append(normalize_symbol(value))
            except ValueError:
                continue
    if not symbols:
        raise RuntimeError("NIFTY 500 constituent source returned no valid symbols")
    return list(dict.fromkeys(symbols))


def _decision(symbol: str, rows: list[dict[str, Any]], portfolio_value: float, max_allocation: float) -> dict[str, Any]:
    closes = [float(row.get("close") or 0) for row in rows if float(row.get("close") or 0) > 0]
    if not closes:
        return {
            "symbol": symbol,
            "decision": "NO ACTION",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "thesis": "No action because usable market data was unavailable.",
            "technical_reasoning": "No positive closing prices were returned.",
            "fundamental_reasoning": "Not available from the selected public data path.",
            "news_reasoning": "Not available from the selected public data path.",
            "risks": ["Market data unavailable"],
            "confidence": 0.0,
            "suggested_position_size": 0.0,
            "evidence": [{"source": "market_data", "bars": 0}],
        }
    latest = closes[-1]
    prior = closes[-6] if len(closes) >= 6 else closes[0]
    sma = sum(closes[-20:]) / min(20, len(closes))
    period_return = latest / prior - 1 if prior else 0
    if latest > sma and period_return > 0.02:
        decision = "BUY"
    elif latest < sma and period_return < -0.05:
        decision = "SELL"
    else:
        decision = "HOLD"
    return {
        "symbol": symbol, "decision": decision, "timestamp": datetime.now(timezone.utc).isoformat(),
        "observed_price": latest, "thesis": f"{decision} based on available daily price trend.",
        "technical_reasoning": f"Price {latest:.2f}; SMA20 {sma:.2f}; 5-session return {period_return:.2%}.",
        "fundamental_reasoning": "Not available from the selected public data path.",
        "news_reasoning": "Not available from the selected public data path.",
        "risks": ["Technical-only signal", "No fundamentals or news were available"],
        "confidence": min(0.95, max(0.1, abs(period_return) * 4)),
        "suggested_position_size": portfolio_value * max_allocation,
        "evidence": [{"source": "market_data", "bars": len(closes), "sma20": sma, "period_return": period_return}],
    }


def scan_nifty500(
    *,
    universe: Iterable[str] | None = None,
    batch_size: int = 25,
    fetcher: Callable[..., dict[str, Any]] = fetch_market_data,
    portfolio_value: float = 0.0,
    max_allocation: float = 0.1,
) -> dict[str, Any]:
    """Fetch manageable batches and return ranked structured decisions."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    raw_symbols = list(universe) if universe is not None else nifty500_universe()
    symbols: list[str] = []
    for value in raw_symbols:
        try:
            symbol = normalize_symbol(value)
        except ValueError:
            continue
        if symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        raise RuntimeError("NIFTY 500 universe contains no valid symbols")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=60)
    decisions: list[dict[str, Any]] = []
    batches = 0
    batch_errors: list[dict[str, Any]] = []
    for offset in range(0, len(symbols), batch_size):
        batch = symbols[offset : offset + batch_size]
        batches += 1
        try:
            raw = fetcher(codes=batch, source="auto", interval="1D", start_date=start.isoformat(), end_date=end.isoformat())
        except Exception as exc:
            batch_errors.append({
                "symbols": batch,
                "error": f"{type(exc).__name__}: {exc}",
            })
            raw = {}
        for symbol in batch:
            decisions.append(_decision(symbol, raw.get(symbol) or [], portfolio_value, max_allocation))
    decisions.sort(key=lambda item: (item["decision"] == "BUY", item.get("confidence", 0)), reverse=True)
    return {
        "status": "partial" if batch_errors else "ok",
        "universe": "NIFTY 500",
        "scanned": len(symbols),
        "batches": batches,
        "batch_size": batch_size,
        "batch_errors": batch_errors,
        "decisions": decisions,
    }
