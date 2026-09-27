import asyncio
from pathlib import Path

import pytest

from src.paper_trading.monitor import PaperMonitoringService
from src.paper_trading.service import PaperTradingService, normalize_symbol
from src.paper_trading.store import PaperTradingStore


def service(tmp_path: Path) -> PaperTradingService:
    return PaperTradingService(
        PaperTradingStore(tmp_path / "paper.sqlite3"),
        price_fetcher=lambda symbol: 100.0,
        starting_cash=1000,
    )


def test_indian_symbol_normalization():
    assert normalize_symbol("TCS") == "TCS.NS"
    assert normalize_symbol("500325.BO") == "500325.BO"


def test_buy_sell_persist_and_calculate_pnl(tmp_path: Path):
    account = service(tmp_path)
    bought = account.trade("BUY", "TCS", 5, price=100)
    assert bought["portfolio"]["cash"] == pytest.approx(499.5)
    sold = account.trade("SELL", "TCS", 2, price=110)
    assert sold["portfolio"]["positions"][0]["quantity"] == 3
    assert sold["portfolio"]["realized_pnl"] == pytest.approx(19.58)
    reloaded = PaperTradingService(PaperTradingStore(tmp_path / "paper.sqlite3"), price_fetcher=lambda _: 100)
    assert reloaded.portfolio()["positions"][0]["quantity"] == 3
    assert len(reloaded.store.trades()) == 2


def test_reset_requires_explicit_confirmation(tmp_path: Path):
    account = service(tmp_path)
    with pytest.raises(ValueError):
        account.reset("yes")
    account.trade("BUY", "INFY", 1, price=100)
    account.reset("RESET PAPER PORTFOLIO")
    assert account.portfolio()["positions"] == []
    assert account.portfolio()["cash"] == 1000


def _monitor(tmp_path: Path, sender=None) -> PaperMonitoringService:
    paper = PaperTradingService(
        PaperTradingStore(tmp_path / "paper.sqlite3"),
        price_fetcher=lambda _: 100.0,
        starting_cash=1000,
    )
    scanner = lambda **_: {
        "status": "ok",
        "scanned": 1,
        "batches": 1,
        "batch_errors": [],
        "decisions": [{
            "symbol": "TCS.NS",
            "decision": "BUY",
            "thesis": "positive trend",
            "technical_reasoning": "price above trend",
            "risks": ["technical-only"],
            "evidence": [{"source": "test"}],
            "confidence": 0.8,
        }],
    }
    return PaperMonitoringService(paper, sender=sender, scanner=scanner)


def test_monitoring_without_channel_stores_alerts_locally(tmp_path: Path):
    monitor = _monitor(tmp_path)
    asyncio.run(monitor._on_job(None))
    assert monitor.last_error is None
    assert monitor.status()["latest_alerts"]


def test_monitoring_delivers_when_channel_is_configured(tmp_path: Path):
    delivered: list[str] = []

    async def sender(message: str) -> None:
        delivered.append(message)

    monitor = _monitor(tmp_path, sender=sender)
    asyncio.run(monitor._on_job(None))
    assert monitor.last_error is None
    assert delivered and "ai_buy_opportunity" in delivered[0]


def test_unavailable_channel_does_not_fail_monitoring(tmp_path: Path):
    async def sender(_: str) -> None:
        raise RuntimeError("channel unavailable")

    monitor = _monitor(tmp_path, sender=sender)
    asyncio.run(monitor._on_job(None))
    assert monitor.last_error is None
    assert monitor.status()["latest_alerts"]
    assert monitor.last_result["alerts"][0]["delivery_error"]


def test_monitoring_is_recommendation_only_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VIBE_TRADING_PAPER_AUTONOMOUS_EXECUTION", raising=False)
    monitor = _monitor(tmp_path)
    calls: list[tuple[object, ...]] = []

    def unexpected_trade(*args: object, **kwargs: object) -> object:
        calls.append(args)
        raise AssertionError("monitoring must not execute paper trades")

    monkeypatch.setattr(monitor.paper, "trade", unexpected_trade)
    asyncio.run(monitor._on_job(None))
    assert not calls
    assert monitor.status()["autonomous_execution_enabled"] is False
