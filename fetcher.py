"""
Institutional-Grade Fundamental & Valuation Data Fetcher.

Pulls stale candidate equities from PostgreSQL, fetches granular financial ratios,
profitability margins, cash flows, and 6-month momentum from yfinance, syncs
company sector profiles, and persists rows into the fundamentals table.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Sequence

import yfinance as yf
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)

import db
import patterns
from config import settings

logger = logging.getLogger(__name__)


def fetch_and_persist_stale(
    limit: int | None = None,
    max_workers: int | None = None,
    max_age_days: int = 30,
    tickers: Sequence[str] | None = None,
    universe: str | None = None,
    show_progress: bool = True,
) -> int:
    """
    Fetch fundamentals for stale or explicitly supplied tickers and persist them.

    Args:
        limit: Maximum number of tickers to process.
        max_workers: Concurrent yfinance workers.
        max_age_days: Cache age threshold for selecting stale tickers.
        tickers: Optional explicit ticker list.
        universe: Optional benchmark universe filter ('SP500', 'NASDAQ100', etc.).
        show_progress: Whether to display a rich terminal progress bar.

    Returns:
        Number of fundamental records persisted.
    """
    workers = max_workers or settings.WORKER_CONCURRENCY
    selected = db.get_stale_tickers(
        tickers=tickers,
        universe=universe,
        max_age_days=max_age_days,
    )
    if limit is not None:
        selected = selected[:limit]

    if not selected:
        logger.info("All target equities are up-to-date in cache.")
        return 0

    logger.info(
        "Ingesting fundamental data for %d tickers (workers=%d, universe=%s)...",
        len(selected),
        workers,
        universe or "all",
    )

    records: list[dict[str, Any]] = []
    failures = 0
    delay = 1.0 / settings.RATE_LIMIT_PER_SEC if settings.RATE_LIMIT_PER_SEC > 0 else 0.0

    def worker(ticker: str) -> dict[str, Any] | None:
        if delay:
            time.sleep(delay)
        return fetch_fundamentals(ticker)

    if show_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}[/]"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
        ) as progress:
            task_id = progress.add_task("Fetching Equities", total=len(selected))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(worker, ticker): ticker for ticker in selected}
                for future in as_completed(futures):
                    ticker = futures[future]
                    try:
                        record = future.result()
                    except Exception as exc:
                        logger.debug("Fetch failed for %s: %s", ticker, exc)
                        record = None

                    if record is None:
                        failures += 1
                    else:
                        records.append(record)
                    progress.advance(task_id)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker, ticker): ticker for ticker in selected}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    record = future.result()
                except Exception as exc:
                    logger.debug("Fetch failed for %s: %s", ticker, exc)
                    record = None

                if record is None:
                    failures += 1
                else:
                    records.append(record)

    persisted = db.upsert_fundamentals(records)
    logger.info("Fetch complete: %d persisted, %d failed/skipped.", persisted, failures)
    return persisted


def fetch_fundamentals(ticker: str) -> dict[str, Any] | None:
    """
    Fetch comprehensive fundamentals, balance sheet ratios, and 6M momentum from yfinance.
    Synchronizes company sector/industry profile into PostgreSQL.
    """
    ticker = ticker.upper().strip()
    stock = yf.Ticker(ticker)

    try:
        info: dict[str, Any] = stock.info or {}
    except Exception as exc:
        logger.debug("Could not retrieve info for %s: %s", ticker, exc)
        return None

    if not info or not isinstance(info, dict):
        return None

    # Sync company profile (Sector, Industry, Exchange) into companies table
    sector = info.get("sector")
    industry = info.get("industry")
    exchange = info.get("exchange")
    if sector or industry or exchange:
        try:
            db.update_company_profile(
                ticker=ticker,
                sector=sector,
                industry=industry,
                exchange=exchange,
            )
        except Exception as exc:
            logger.debug("Failed updating profile for %s: %s", ticker, exc)

    # Historical price data for current price, volume, momentum, and technical pattern recognition
    try:
        hist = stock.history(period="1y")
    except Exception:
        hist = None

    current_price = _safe_number(
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or (hist["Close"].dropna().iloc[-1] if hist is not None and not hist.empty and "Close" in hist else None)
    )
    volume = _safe_int(
        info.get("averageVolume")
        or info.get("averageVolume10days")
        or (hist["Volume"].dropna().tail(30).mean() if hist is not None and not hist.empty and "Volume" in hist else None)
    )

    # 6-Month Relative Price Momentum
    price_return_6m = None
    if hist is not None and not hist.empty and len(hist) >= 15 and "Close" in hist:
        close_series = hist["Close"].dropna()
        if len(close_series) >= 2:
            lookback_idx = min(126, len(close_series) - 1)
            p_start = close_series.iloc[-lookback_idx]
            p_end = close_series.iloc[-1]
            if p_start and p_start > 0:
                price_return_6m = _safe_number((p_end - p_start) / p_start)

    # Technical Pattern Recognition
    tech_patterns = patterns.analyze_technical_patterns(hist)

    fiscal_date = date.today()
    filing_date = _parse_date(info.get("mostRecentQuarter"))

    return {
        "ticker": ticker,
        "fiscal_date": fiscal_date,
        "filing_date": filing_date,
        "market_cap": _safe_int(info.get("marketCap")),
        "pe_forward": _safe_number(info.get("forwardPE") or info.get("trailingPE")),
        "trailing_pe": _safe_number(info.get("trailingPE")),
        "peg_ratio": _safe_number(info.get("pegRatio")),
        "price_to_book": _safe_number(info.get("priceToBook")),
        "ev_to_ebitda": _safe_number(info.get("enterpriseToEbitda")),
        "roe": _safe_number(info.get("returnOnEquity")),
        "return_on_assets": _safe_number(info.get("returnOnAssets")),
        "debt_to_equity": _safe_number(info.get("debtToEquity")),
        "profit_margin": _safe_number(info.get("profitMargins")),
        "operating_margin": _safe_number(info.get("operatingMargins")),
        "gross_margin": _safe_number(info.get("grossMargins")),
        "revenue_growth": _safe_number(info.get("revenueGrowth")),
        "free_cash_flow": _safe_int(info.get("freeCashflow")),
        "current_price": current_price,
        "volume": volume,
        "price_return_6m": price_return_6m,
        "pattern_score": tech_patterns["pattern_score"],
        "detected_patterns": tech_patterns["detected_patterns"],
        "dist_52w_high": tech_patterns["dist_52w_high"],
        "rsi_14": tech_patterns["rsi_14"],
        "ud_volume_ratio": tech_patterns["ud_volume_ratio"],
        "raw_payload": info,
    }


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _safe_int(value: Any) -> int | None:
    number = _safe_number(value)
    return int(number) if number is not None else None


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromtimestamp(int(value))
    except (TypeError, ValueError, OSError):
        return None

