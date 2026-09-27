"""Read-only, evidence-grounded stock analysis for the dashboard."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from src.market_data import fetch_market_data
from src.paper_trading.service import PaperTradingService
from src.providers.chat import ChatLLM
from src.tools.stock_news_tool import StockNewsTool

logger = logging.getLogger(__name__)

_SYMBOL_RE = re.compile(r"^[A-Z0-9&.-]{1,24}(?:\.(?:NS|BO|US|HK|TO|V|L|SH|SZ|BJ|KS|KQ))?$")
_INDIAN_HINTS = {"TCS", "INFY", "INFOSYS", "RELIANCE", "HDFCBANK", "ICICIBANK", "ITC", "SBIN", "WIPRO"}
_QUERY_STOP_WORDS = {
    "A", "AN", "AND", "ANALYZE", "ANALYSIS", "AVOID", "BEAR", "BUY", "CASE", "COMPARE",
    "COMPANY", "FIND", "FOR", "GIVE", "HOLD", "INTERESTING", "INVESTMENT", "IS", "LONG",
    "ME", "OF", "ON", "RISKS", "STOCK", "THE", "THIS", "TO", "WHAT", "WHY", "WITH",
}
_KNOWN_SUFFIXES = (".NS", ".BO", ".US", ".HK", ".TO", ".V", ".L", ".SH", ".SZ", ".BJ", ".KS", ".KQ")


def normalize_analyst_symbol(value: str) -> str:
    """Resolve common bare tickers without changing explicit exchange symbols."""
    symbol = str(value or "").strip().upper().replace(" ", "")
    if not symbol:
        raise ValueError("a stock ticker is required")
    if symbol in {"NIFTY", "NIFTY50"}:
        return "^NSEI"
    if symbol.endswith(_KNOWN_SUFFIXES) or symbol.startswith("^"):
        result = symbol
    elif symbol in _INDIAN_HINTS:
        result = f"{symbol}.NS"
    else:
        result = f"{symbol}.US"
    if not _SYMBOL_RE.fullmatch(result) and result not in {"^NSEI", "^CRSLDX"}:
        raise ValueError(f"invalid ticker: {value}")
    return result


def _rows(symbol: str) -> list[dict[str, Any]]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=365)
    raw = fetch_market_data(
        codes=[symbol],
        source="auto",
        interval="1D",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        max_rows=260,
        include_provenance=True,
    )
    value = raw.get(symbol) or []
    if isinstance(value, dict):
        value = value.get("data") or []
    return [row for row in value if isinstance(row, dict)]


def _fundamentals(symbol: str) -> dict[str, Any]:
    """Use yfinance's public metadata when installed; missing fields stay absent."""
    try:
        import yfinance as yf  # type: ignore

        info = yf.Ticker(symbol.replace(".US", "")).info
        fields = (
            "longName", "sector", "industry", "marketCap", "trailingPE",
            "forwardPE", "priceToBook", "returnOnEquity", "profitMargins",
            "revenueGrowth", "earningsGrowth", "debtToEquity", "dividendYield",
        )
        return {
            key: info[key]
            for key in fields
            if info.get(key) is not None
        }
    except Exception as exc:
        logger.info("fundamentals unavailable for %s: %s", symbol, exc)
        return {}


def _news(symbol: str) -> list[dict[str, Any]]:
    try:
        result = json.loads(StockNewsTool().execute(code=symbol, scope="stock", limit=8))
        return (result.get("data") or {}).get("articles") or []
    except Exception as exc:
        logger.info("news unavailable for %s: %s", symbol, exc)
        return []


def _evidence(symbol: str) -> dict[str, Any]:
    rows = _rows(symbol)
    closes = [float(row["close"]) for row in rows if row.get("close") not in (None, "") and float(row["close"]) > 0]
    if not closes:
        return {"symbol": symbol, "status": "unavailable", "missing": ["market_data"]}
    latest = closes[-1]
    prior = closes[-22] if len(closes) >= 22 else closes[0]
    sma20 = sum(closes[-20:]) / min(20, len(closes))
    fundamentals = _fundamentals(symbol)
    news = _news(symbol)
    return {
        "symbol": symbol,
        "status": "ok",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "market_data": {
            "price": latest,
            "price_change_approx_1m": latest / prior - 1 if prior else None,
            "sma20": sma20,
            "trend": "above_sma20" if latest >= sma20 else "below_sma20",
            "bars": len(closes),
            "last_bar": rows[-1].get("date") or rows[-1].get("timestamp"),
        },
        "fundamentals": fundamentals,
        "news": news,
        "missing": [
            label for label, value in (("fundamentals", fundamentals), ("news", news))
            if not value
        ],
    }


def _paper_context(symbols: list[str]) -> list[dict[str, Any]]:
    try:
        positions = PaperTradingService().portfolio().get("positions", [])
        return [item for item in positions if item.get("symbol") in symbols]
    except Exception as exc:
        logger.info("paper context unavailable: %s", exc)
        return []


def stock_analyst_model_name() -> str:
    """Return the configured local model used for analyst synthesis."""
    return os.getenv("VIBE_TRADING_STOCK_ANALYST_MODEL", "").strip() or "qwen2.5-coder:7b"


def analyze_stock_request(*, query: str, symbols: list[str] | None = None) -> dict[str, Any]:
    requested = symbols or [
        value for value in re.findall(
            r"\b[A-Z][A-Z0-9&.-]{1,23}(?:\.(?:NS|BO|US|HK|TO|V|L|SH|SZ|BJ|KS|KQ))?\b",
            query.upper(),
        )
        if value not in _QUERY_STOP_WORDS
    ]
    if not requested:
        raise ValueError("enter a ticker or a request containing one")
    resolved: list[str] = []
    for value in requested[:4]:
        ticker = normalize_analyst_symbol(value)
        if ticker not in resolved:
            resolved.append(ticker)
    evidence = [_evidence(symbol) for symbol in resolved]
    prompt = (
        "You are Vibe-Trading's local stock research analyst. Use ONLY the JSON evidence below. "
        "Never invent numbers, news, citations, or unavailable metrics. Clearly label facts versus interpretation. "
        "This is not guaranteed financial advice. Return concise Markdown (under 450 words) with these headings: "
        "Assessment, Reasons, Company / ticker, Current/recent price, Bull case, Bear case, Key fundamentals, "
        "Valuation, Technical/price trend context, Recent important news, Major risks, What could change the thesis, "
        "Final AI assessment. Use short bullets. For multiple stocks, include a compact comparison table and explain which appears stronger and why. "
        "The final assessment must be BUY, HOLD, AVOID, or INSUFFICIENT DATA. "
        f"User request: {query}\nEvidence: {json.dumps(evidence, ensure_ascii=False)}"
    )
    model_name = stock_analyst_model_name()
    # Keep this focused request independent from the global agent retry budget.
    # A stalled local Ollama request must become a visible analyst error, not a
    # sequence of multi-minute retries.
    llm = ChatLLM(
        model_name=model_name,
        request_timeout=45,
        max_retries=0,
        max_tokens=500,
    )
    try:
        response = llm.chat([
            {"role": "system", "content": "You are a careful, evidence-grounded financial research assistant."},
            {"role": "user", "content": prompt},
        ], timeout=45)
        analysis = response.content.strip()
    finally:
        llm.close()
    return {
        "status": "ok",
        "query": query,
        "symbols": resolved,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence": evidence,
        "paper_positions": _paper_context(resolved),
        "analysis": analysis,
        "model": model_name,
        "disclaimer": "Research support only; not guaranteed financial advice. Stock Analyst never places trades automatically.",
    }
