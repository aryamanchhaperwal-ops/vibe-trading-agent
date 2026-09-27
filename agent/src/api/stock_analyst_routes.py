"""Authenticated read-only Stock Analyst endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.api.security import require_auth
from src.stock_analyst import analyze_stock_request, stock_analyst_model_name

logger = logging.getLogger(__name__)
_ANALYSIS_TIMEOUT_SECONDS = 60


class StockAnalysisRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    symbols: list[str] | None = Field(default=None, max_length=4)


def register_stock_analyst_routes(app: FastAPI) -> None:
    @app.post("/api/stock-analyst/analyze", dependencies=[Depends(require_auth)])
    async def analyze(request: StockAnalysisRequest):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    analyze_stock_request,
                    query=request.query.strip(),
                    symbols=request.symbols,
                ),
                timeout=_ANALYSIS_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            logger.error(
                "Stock Analyst timed out after %ss for query %r",
                _ANALYSIS_TIMEOUT_SECONDS,
                request.query,
            )
            raise HTTPException(
                status_code=504,
                detail=(
                    "Stock analysis timed out while waiting for local Ollama or "
                    "a public data source. Check that Ollama is running and the "
                    f"{stock_analyst_model_name()} model is available, then retry."
                ),
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            if "timed out" in str(exc).lower() or "timeout" in type(exc).__name__.lower():
                raise HTTPException(
                    status_code=504,
                    detail=(
                        "Local Ollama did not finish this analysis in time. "
                        f"Check that {stock_analyst_model_name()} is loaded and responding, "
                        "then retry."
                    ),
                ) from exc
            raise HTTPException(status_code=503, detail=f"stock analysis unavailable: {exc}") from exc
