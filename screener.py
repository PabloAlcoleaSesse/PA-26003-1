"""
Quantitative Stock Screener and Multi-Factor Ranking Engine.

Applies institutional-grade fundamental filters and computes normalized multi-factor
quality, growth, valuation, and momentum composite scores.
Formats results into rich CLI tables and exports CSV reports.
"""

from __future__ import annotations

import csv
from collections import Counter
import logging
import json
import hashlib
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console
from rich import box
from rich.table import Table

import db

logger = logging.getLogger(__name__)
console = Console()

STRATEGY_ALIASES = {"upcoming": "upcoming_breakouts", "breakout": "upcoming_breakouts", "minervini": "minervini_trend"}
TREND_STRATEGIES = {"upcoming_breakouts", "minervini_trend", "high_growth_momentum"}


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _eligibility(row: dict[str, Any], strategy: str, as_of: date,
                 max_price_age_days: int, min_pattern_score: float | None) -> list[str]:
    reasons = []
    try:
        price_date = date.fromisoformat(str(row.get("price_as_of"))[:10])
        age = (as_of - price_date).days
        if age < 0:
            reasons.append("price_date_in_future")
        elif age > max_price_age_days:
            reasons.append("stale_price")
    except (ValueError, TypeError):
        reasons.append("missing_price_date")
    if not _finite(row.get("current_price")) or float(row["current_price"]) <= 0:
        reasons.append("invalid_price")
    if strategy in TREND_STRATEGIES:
        if row.get("technical_valid") not in (True, 1):
            reasons.append("technical_data_unavailable")
        if row.get("is_stage_2") not in (True, 1):
            rules_passed = row.get("stage_2_rules_passed", 0) or 0
            if rules_passed < 5:
                reasons.append("uptrend_not_confirmed")
        for period in ("1m", "3m", "6m"):
            value = row.get(f"price_return_{period}")
            if not _finite(value):
                reasons.append(f"missing_return_{period}")
            elif period != "1m" and float(value) <= 0:
                reasons.append(f"nonpositive_return_{period}")
        relative = row.get("relative_return_3m")
        if not _finite(relative):
            reasons.append("benchmark_comparison_unavailable")
        elif float(relative) < -0.03:
            reasons.append("underperforming_benchmark_3m")
        if strategy == "upcoming_breakouts" and not (
            row.get("is_vcp") in (True, 1) or row.get("is_breakout") in (True, 1)
        ):
            reasons.append("no_breakout_setup")
    if min_pattern_score is not None:
        pattern = row.get("pattern_score")
        if not _finite(pattern):
            reasons.append("pattern_score_unavailable")
        elif float(pattern) < min_pattern_score:
            reasons.append("pattern_score_below_minimum")
    return reasons


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, Decimal)) and not math.isfinite(value):
        return None
    return value


def _implementation_fingerprint() -> str:
    digest = hashlib.sha256()
    for name in ("db.py", "fetcher.py", "patterns.py", "screener.py"):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((Path(__file__).parent / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()

# Institutional Screening Filter Default Thresholds
DEFAULT_MIN_MARKET_CAP = 500_000_000        # $500M minimum capitalization
DEFAULT_MIN_DOLLAR_VOLUME = 2_000_000.0     # $2M daily trading liquidity
DEFAULT_MAX_PE = 45.0                       # 0 < Forward P/E < 45
DEFAULT_MIN_ROE = 0.10                      # Return on Equity > 10% (0.10)
DEFAULT_MAX_DE = 200.0                      # Debt-to-Equity < 200 (2.0x)
DEFAULT_MAX_EV_EBITDA = 30.0                # EV/EBITDA between 0 and 30

# Strategy Factor Pillar Weighting Profiles
STRATEGY_PROFILES: dict[str, dict[str, float]] = {
    "balanced": {
        "quality": 0.30,
        "growth": 0.25,
        "valuation": 0.25,
        "momentum": 0.20,
        "pattern": 0.00,
    },
    "upcoming_breakouts": {
        "pattern": 0.35,
        "growth": 0.25,
        "quality": 0.20,
        "momentum": 0.10,
        "valuation": 0.10,
    },
    "minervini_trend": {
        "pattern": 0.45,
        "growth": 0.30,
        "momentum": 0.15,
        "quality": 0.10,
        "valuation": 0.00,
    },
    "quality_compounders": {
        "quality": 0.50,
        "growth": 0.20,
        "valuation": 0.15,
        "momentum": 0.15,
        "pattern": 0.00,
    },
    "garp": {
        "growth": 0.40,
        "valuation": 0.30,
        "quality": 0.20,
        "momentum": 0.10,
        "pattern": 0.00,
    },
    "deep_value": {
        "valuation": 0.50,
        "quality": 0.25,
        "momentum": 0.15,
        "growth": 0.10,
        "pattern": 0.00,
    },
    "high_growth_momentum": {
        "growth": 0.40,
        "momentum": 0.30,
        "pattern": 0.20,
        "quality": 0.10,
        "valuation": 0.00,
    },
}


def score_quality(row: dict[str, Any]) -> float:
    """Compute Quality Pillar Score (0 - 100) from ROE, Operating Margin, and Debt/Equity."""
    score = 0.0

    # ROE (40% weight within Quality)
    roe = float(row.get("roe") or 0.0)
    if roe >= 0.30:
        score += 40.0
    elif roe >= 0.20:
        score += 35.0
    elif roe >= 0.15:
        score += 28.0
    elif roe >= 0.10:
        score += 18.0
    elif roe > 0.0:
        score += 8.0

    # Operating Margin / Profit Margin (35% weight)
    op_margin = float(row.get("operating_margin") or row.get("profit_margin") or 0.0)
    if op_margin >= 0.30:
        score += 35.0
    elif op_margin >= 0.20:
        score += 30.0
    elif op_margin >= 0.12:
        score += 22.0
    elif op_margin >= 0.06:
        score += 12.0
    elif op_margin > 0:
        score += 5.0

    # Debt-to-Equity Financial Safety (25% weight)
    de = row.get("debt_to_equity")
    if de is None:
        score += 18.0  # Neutral
    else:
        de_val = float(de)
        if de_val <= 40.0:
            score += 25.0
        elif de_val <= 80.0:
            score += 20.0
        elif de_val <= 120.0:
            score += 14.0
        elif de_val <= 180.0:
            score += 6.0

    return min(100.0, max(0.0, score))


def score_growth(row: dict[str, Any]) -> float:
    """Compute Growth Pillar Score (0 - 100). Penalizes contracting revenues."""
    growth = float(row.get("revenue_growth") or 0.0)

    # Hard penalty for revenue contraction
    if growth < -0.05:
        return 5.0
    if growth < 0.0:
        return 15.0

    if growth >= 0.35:
        return 100.0
    if growth >= 0.25:
        return 88.0
    if growth >= 0.15:
        return 74.0
    if growth >= 0.08:
        return 58.0
    if growth >= 0.03:
        return 40.0
    return 25.0


def score_valuation(row: dict[str, Any]) -> float:
    """Compute Valuation Pillar Score (0 - 100). Curves P/E, PEG, and EV/EBITDA."""
    score = 0.0
    pe = float(row.get("pe_forward") or 0.0)

    # Forward P/E (50% weight) - Sweet spot is 8 to 22
    if 8.0 <= pe <= 18.0:
        score += 50.0
    elif 18.0 < pe <= 26.0:
        score += 40.0
    elif 5.0 <= pe < 8.0:
        score += 35.0  # Low P/E caution
    elif 26.0 < pe <= 35.0:
        score += 25.0
    elif 35.0 < pe <= 50.0:
        score += 10.0
    else:
        score += 3.0

    # PEG Ratio (25% weight)
    peg = row.get("peg_ratio")
    if peg is not None:
        peg_val = float(peg)
        if 0.0 < peg_val <= 1.2:
            score += 25.0
        elif 1.2 < peg_val <= 1.8:
            score += 18.0
        elif 1.8 < peg_val <= 2.5:
            score += 10.0
        else:
            score += 4.0
    else:
        score += 14.0  # Neutral fallback

    # EV / EBITDA (25% weight)
    ev_ebitda = row.get("ev_to_ebitda")
    sector = (row.get("sector") or "").lower()
    if "financial" in sector or "bank" in sector or "insurance" in sector:
        # EV/EBITDA is not meaningful for Financials -> Award neutral/fair score
        score += 20.0
    elif ev_ebitda is not None:
        ev_val = float(ev_ebitda)
        if 4.0 <= ev_val <= 12.0:
            score += 25.0
        elif 12.0 < ev_val <= 18.0:
            score += 18.0
        elif 18.0 < ev_val <= 25.0:
            score += 10.0
        else:
            score += 3.0
    else:
        score += 12.0

    return min(100.0, max(0.0, score))


def score_momentum(row: dict[str, Any]) -> float:
    """Compute Momentum Pillar Score (0 - 100) based on 6-month price performance."""
    mom = row.get("price_return_6m")
    if mom is None:
        return 50.0  # Neutral fallback

    mom_val = float(mom)
    if mom_val >= 0.30:
        return 100.0
    if mom_val >= 0.18:
        return 85.0
    if mom_val >= 0.08:
        return 70.0
    if mom_val >= 0.0:
        return 55.0
    if mom_val >= -0.10:
        return 35.0
    return 15.0


def calculate_composite_score(row: dict[str, Any], strategy: str = "balanced") -> dict[str, float]:
    """
    Calculate normalized 4-pillar scores and the overall composite multi-factor score.
    Includes technical pattern recognition score for breakout and trend strategies.

    Returns:
        Dict with keys: quality, growth, valuation, momentum, pattern, composite.
    """
    canonical = STRATEGY_ALIASES.get(strategy.lower().strip(), strategy.lower().strip())
    weights = STRATEGY_PROFILES.get(canonical, STRATEGY_PROFILES["balanced"])

    q_score = score_quality(row)
    g_score = score_growth(row)
    v_score = score_valuation(row)
    m_score = score_momentum(row)
    p_score = float(row["pattern_score"]) if _finite(row.get("pattern_score")) else 0.0

    composite = (
        (q_score * weights.get("quality", 0.0))
        + (g_score * weights.get("growth", 0.0))
        + (v_score * weights.get("valuation", 0.0))
        + (m_score * weights.get("momentum", 0.0))
        + (p_score * weights.get("pattern", 0.0))
    )

    return {
        "quality": round(q_score, 1),
        "growth": round(g_score, 1),
        "valuation": round(v_score, 1),
        "momentum": round(m_score, 1),
        "pattern": round(p_score, 1),
        "composite": round(composite, 2),
    }


def _format_currency_compact(value: float | int | None) -> str:
    """Format large currency numbers into compact strings ($B, $M, $K)."""
    if value is None:
        return "N/A"
    f_val = float(value)
    abs_val = abs(f_val)
    if abs_val >= 1_000_000_000_000:
        return f"${f_val / 1_000_000_000_000:.2f}T"
    if abs_val >= 1_000_000_000:
        return f"${f_val / 1_000_000_000:.2f}B"
    if abs_val >= 1_000_000:
        return f"${f_val / 1_000_000:.2f}M"
    if abs_val >= 1_000:
        return f"${f_val / 1_000:.2f}K"
    return f"${f_val:.2f}"


def _format_pct(value: float | None) -> str:
    """Format decimal as percentage string."""
    if value is None:
        return "N/A"
    return f"{float(value) * 100.0:+.1f}%"


def _format_ratio(value: float | None) -> str:
    """Format ratio string."""
    if value is None:
        return "N/A"
    return f"{float(value):.2f}"


def run_screen(
    strategy: str = "balanced",
    universe: str | None = None,
    sector: str | None = None,
    min_market_cap: int = DEFAULT_MIN_MARKET_CAP,
    max_market_cap: int | None = None,
    max_pe: float = DEFAULT_MAX_PE,
    min_roe: float = DEFAULT_MIN_ROE,
    max_de: float = DEFAULT_MAX_DE,
    min_dollar_volume: float = DEFAULT_MIN_DOLLAR_VOLUME,
    max_ev_ebitda: float = DEFAULT_MAX_EV_EBITDA,
    min_growth: float | None = 0.0,
    min_margin: float | None = 0.03,
    min_pattern_score: float | None = None,
    limit: int = 25,
    output_csv: str | Path | None = "screened_results.csv",
    print_table: bool = True,
    include_watchlist: bool = False,
    max_price_age_days: int = 5,
    as_of: date | None = None,
    audit_dir: str | Path | None = "runs",
) -> list[dict[str, Any]]:
    """
    Execute institutional multi-factor quantitative equity screening with pattern recognition.

    Args:
        strategy: 'balanced', 'upcoming_breakouts', 'minervini_trend', 'quality_compounders', 'garp', 'deep_value', 'high_growth_momentum'.
        universe: Optional benchmark filter ('SP500', 'SP400', 'UPCOMING', etc.).
        sector: Optional sector filter ('Technology', 'Healthcare', etc.).
        min_market_cap: Minimum market cap in USD.
        max_market_cap: Maximum market cap in USD (for targeting emerging mid-caps).
        max_pe: Maximum forward P/E ratio.
        min_roe: Minimum Return on Equity.
        max_de: Maximum Debt/Equity ratio.
        min_dollar_volume: Minimum daily dollar volume.
        max_ev_ebitda: Maximum EV/EBITDA multiple.
        min_growth: Minimum YoY revenue growth (excludes shrinking companies).
        min_margin: Minimum profit or operating margin.
        min_pattern_score: Minimum technical pattern score (0 - 100).
        limit: Top results to display.
        output_csv: Path to export CSV.
        print_table: Whether to print the formatted table.

    Returns:
        List of screened and scored stock records.
    """
    # as_of is a testing clock, not a historical backtest: fundamentals are current.
    screen_date = as_of or datetime.now(timezone.utc).date()
    if max_price_age_days < 0 or limit < 0:
        raise ValueError("max_price_age_days and limit must be nonnegative")
    # Adjust thresholds based on strategy presets
    strat = strategy.lower().strip()
    strat = STRATEGY_ALIASES.get(strat, strat)
    if strat not in STRATEGY_PROFILES:
        raise ValueError(f"Unknown strategy: {strategy}")
    if strat in ("upcoming_breakouts", "upcoming", "breakout"):
        # Sweet spot for upcoming stocks: $1B to $80B mid/growth-caps with accelerating growth and technical setups
        max_market_cap = max_market_cap or 80_000_000_000
        min_growth = max(min_growth or 0.0, 0.12)
        # Reinvesting growth compounders may have higher PE multiples and lower initial ROE
        if max_pe is None or max_pe <= DEFAULT_MAX_PE:
            max_pe = 85.0
        if min_roe is None or min_roe >= DEFAULT_MIN_ROE:
            min_roe = 0.04
        min_pattern_score = max(min_pattern_score or 0.0, 50.0)
    elif strat in ("minervini_trend", "minervini"):
        min_pattern_score = max(min_pattern_score or 0.0, 65.0)
        min_growth = max(min_growth or 0.0, 0.10)
        if max_pe is None or max_pe <= DEFAULT_MAX_PE:
            max_pe = 75.0
        if min_roe is None or min_roe >= DEFAULT_MIN_ROE:
            min_roe = 0.05
    elif strat == "quality_compounders":
        min_roe = max(min_roe, 0.14)
        min_margin = max(min_margin or 0.0, 0.12)
        min_growth = max(min_growth or 0.0, 0.02)
    elif strat == "garp":
        min_growth = max(min_growth or 0.0, 0.10)
        max_pe = min(max_pe, 38.0)
    elif strat == "deep_value":
        max_pe = min(max_pe, 20.0)
        max_ev_ebitda = min(max_ev_ebitda, 14.0)
    elif strat == "high_growth_momentum":
        min_growth = max(min_growth or 0.0, 0.15)
        min_pattern_score = max(min_pattern_score or 0.0, 50.0)
        if max_pe is None or max_pe <= DEFAULT_MAX_PE:
            max_pe = 70.0

    raw_candidates = db.query_screened_stocks(
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        max_pe=max_pe,
        min_roe=min_roe,
        max_de=max_de,
        min_dollar_volume=min_dollar_volume,
        max_ev_ebitda=max_ev_ebitda,
        min_growth=min_growth,
        min_margin=min_margin,
        min_pattern_score=None,
        universe=universe,
        sector=sector,
        limit=None,
    )

    # Score each candidate across all pillars
    scored_candidates = []
    for original in raw_candidates:
        row = dict(original)
        row["inputs"] = dict(original)
        reasons = _eligibility(row, strat, screen_date, max_price_age_days, min_pattern_score)
        row["eligibility_reasons"] = reasons
        row["eligible"] = not reasons
        row["setup_status"] = "Watchlist"
        if not reasons and row.get("technical_valid") in (True, 1) and row.get("is_stage_2") in (True, 1):
            if row.get("is_breakout") in (True, 1):
                row["setup_status"] = "Confirmed breakout"
            elif row.get("is_vcp") in (True, 1):
                row["setup_status"] = "Setup forming"
        scores = calculate_composite_score(row, strategy=strat)
        row["quality_score"] = scores["quality"]
        row["growth_score"] = scores["growth"]
        row["valuation_score"] = scores["valuation"]
        row["momentum_score"] = scores["momentum"]
        row["pattern_score"] = scores["pattern"]
        row["factor_score"] = scores["composite"]
        scored_candidates.append(row)

    # Sort by composite factor score descending
    scored_candidates.sort(key=lambda r: r["factor_score"], reverse=True)
    selectable = [row for row in scored_candidates if row["eligible"] or include_watchlist]
    selectable.sort(key=lambda r: (r["eligible"], r["factor_score"]), reverse=True)
    results = selectable[:limit]
    watchlist = [dict(row, rank=rank) for rank, row in enumerate(
        (row for row in scored_candidates if not row["eligible"]), start=1
    )][:limit]
    rejection_counts = dict(Counter(reason for row in scored_candidates
                                    for reason in row["eligibility_reasons"]))
    eligible_count = sum(row["eligible"] for row in scored_candidates)
    logger.info("Screened %d candidates: %d eligible, %d rejected; %d rows selected.",
                len(scored_candidates), eligible_count,
                len(scored_candidates) - eligible_count, len(results))

    # Assign ranks
    for rank, row in enumerate(results, start=1):
        row["rank"] = rank

    if audit_dir is not None:
        now = datetime.now(timezone.utc)
        run_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid4().hex
        manifest = {
            "run_id": run_id, "created_at": now.isoformat(), "as_of": screen_date.isoformat(),
            "strategy": strat,
            "implementation_sha256": _implementation_fingerprint(),
            "config": {
                "universe": universe, "sector": sector, "min_market_cap": min_market_cap,
                "max_market_cap": max_market_cap, "max_pe": max_pe, "min_roe": min_roe,
                "max_de": max_de, "min_dollar_volume": min_dollar_volume,
                "max_ev_ebitda": max_ev_ebitda, "min_growth": min_growth,
                "min_margin": min_margin, "min_pattern_score": min_pattern_score,
                "limit": limit, "include_watchlist": include_watchlist,
                "max_price_age_days": max_price_age_days, "weights": STRATEGY_PROFILES[strat],
            },
            "audit_scope": "Post-fundamental-filter candidate pool; SQL fundamental rejections are not recorded. Current fundamentals, not a historical backtest.",
            "candidates": scored_candidates, "selected": results,
            "watchlist": watchlist, "rejection_counts": rejection_counts,
        }
        directory = Path(audit_dir)
        directory.mkdir(parents=True, exist_ok=True)
        manifest_path = directory / f"{run_id}.json"
        with manifest_path.open("x", encoding="utf-8") as handle:
            json.dump(_json_safe(manifest), handle, indent=2, default=_json_default, allow_nan=False)
        logger.info("Screening run record saved to: %s", manifest_path.resolve())

    # Display Rich Table
    if print_table:
        console.print(f"{len(raw_candidates)} passed fundamental filters; "
                      f"{eligible_count} passed entry checks.")
        if results:
            _display_rich_screen_results(results, strategy=strat, universe=universe, sector=sector)
        elif not raw_candidates:
            console.print("No fundamental candidates. Check universe coverage and fundamental thresholds.")
        elif limit == 0:
            console.print("No rows requested (--top 0).")
        else:
            console.print("No eligible entries. Research watchlist below; all rows failed entry checks.")
            table = Table(title="Research watchlist", box=box.SIMPLE_HEAD)
            table.add_column("Ticker", no_wrap=True)
            table.add_column("6M", justify="right", no_wrap=True)
            table.add_column("Entry checks failed", overflow="fold")
            for row in watchlist:
                table.add_row(row["ticker"], _format_pct(row.get("price_return_6m")),
                              "; ".join(reason.replace("_", " ") for reason in row["eligibility_reasons"]))
            console.print(table)
        if rejection_counts:
            console.print("Failed checks (a stock can fail several): " + "; ".join(
                f"{reason.replace('_', ' ')}: {count}"
                for reason, count in sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0]))
            ))

    # Export to CSV
    if output_csv:
        _export_to_csv(results, output_csv)
        output_path = Path(output_csv)
        _export_to_csv(watchlist, output_path.with_name(output_path.stem + ".watchlist.csv"))

    return results


def _display_rich_screen_results(
    results: list[dict[str, Any]],
    strategy: str,
    universe: str | None,
    sector: str | None,
) -> None:
    """Render a compact table with explicit entry status and complete price dates."""
    table = Table(
        title=f"[bold cyan]{strategy.upper()}[/] | [bold magenta]{universe or 'ALL'}[/]",
        header_style="bold bright_white",
        show_lines=False,
        box=box.SIMPLE_HEAD,
        padding=(0, 0),
    )

    table.add_column("#", justify="right", style="bold")
    table.add_column("Ticker", justify="left", style="bold yellow")
    table.add_column("Price", justify="right")
    table.add_column("As of", justify="left", width=10, min_width=10, max_width=10, no_wrap=True)
    table.add_column("Status", justify="left", no_wrap=True)
    table.add_column("3M", justify="right")
    table.add_column("3M-SPY", justify="right")
    table.add_column("Score", justify="right", style="bold")

    for r in results:
        # Score color coding
        sc = float(r["factor_score"])
        if sc >= 75.0:
            score_str = f"[bold green]{sc:.1f}[/]"
        elif sc >= 62.0:
            score_str = f"[bold yellow]{sc:.1f}[/]"
        else:
            score_str = f"[white]{sc:.1f}[/]"

        table.add_row(
            str(r["rank"]),
            r["ticker"],
            f"${float(r['current_price']):.2f}" if r.get("current_price") else "N/A",
            str(r.get("price_as_of") or "N/A")[:10],
            r["setup_status"] if r["eligible"] else "Excluded",
            _format_pct(r.get("price_return_3m")),
            _format_pct(r.get("relative_return_3m")),
            score_str,
        )

    console.print()
    console.print(table)
    console.print()


def _export_to_csv(results: list[dict[str, Any]], output_csv: str | Path) -> None:
    """Export ranked results to CSV with comprehensive factor and technical pattern metrics."""
    csv_path = Path(output_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank",
        "eligible",
        "setup_status",
        "eligibility_reasons",
        "price_as_of",
        "technical_valid",
        "ticker",
        "name",
        "sector",
        "industry",
        "universe",
        "factor_score",
        "pattern_score",
        "detected_patterns",
        "dist_52w_high",
        "rsi_14",
        "ud_volume_ratio",
        "quality_score",
        "growth_score",
        "valuation_score",
        "momentum_score",
        "current_price",
        "market_cap",
        "pe_forward",
        "trailing_pe",
        "peg_ratio",
        "price_to_book",
        "ev_to_ebitda",
        "roe",
        "return_on_assets",
        "debt_to_equity",
        "profit_margin",
        "operating_margin",
        "gross_margin",
        "revenue_growth",
        "price_return_6m",
        "price_return_1m",
        "price_return_3m",
        "relative_return_1m",
        "relative_return_3m",
        "relative_return_6m",
        "free_cash_flow",
        "volume",
        "dollar_volume",
        "fiscal_date",
    ]

    with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow(row)

    logger.info("Screened results exported to: %s", csv_path.resolve())
    console.print(f"[bold green]✓[/] Screen report saved to: [cyan]{csv_path.resolve()}[/]")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_screen(strategy="upcoming_breakouts", limit=10)
