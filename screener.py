"""
Quantitative Stock Screener and Multi-Factor Ranking Engine.

Applies institutional-grade fundamental filters and computes multi-factor quality
and valuation scores. Formats results into clean CLI tables and exports CSV reports.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from tabulate import tabulate

import db

logger = logging.getLogger(__name__)

# Institutional Screening Filter Default Thresholds
DEFAULT_MIN_MARKET_CAP = 500_000_000        # $500M minimum capitalization
DEFAULT_MIN_DOLLAR_VOLUME = 2_000_000.0     # $2M daily trading liquidity
DEFAULT_MAX_PE = 30.0                       # 0 < Forward P/E < 30
DEFAULT_MIN_ROE = 0.12                      # Return on Equity > 12% (0.12)
DEFAULT_MAX_DE = 180.0                      # Debt-to-Equity < 180 (1.8x)
DEFAULT_MAX_EV_EBITDA = 22.0                # EV/EBITDA between 0 and 22


def calculate_factor_score(
    roe: float | None,
    profit_margin: float | None,
    revenue_growth: float | None,
    pe_forward: float | None,
) -> float:
    """
    Calculate the multi-factor quantitative composite score.

    Mathematical Model:
        Score = (ROE * 40) + (Profit Margin * 30) + (Revenue Growth * 20) - (Forward P/E * 0.5)

    Args:
        roe: Return on Equity (decimal, e.g. 0.20 for 20%).
        profit_margin: Net profit margin (decimal, e.g. 0.15 for 15%).
        revenue_growth: YoY revenue growth rate (decimal, e.g. 0.10 for 10%).
        pe_forward: Forward Price-to-Earnings ratio.

    Returns:
        float: Computed multi-factor score rounded to 4 decimal places.
    """
    roe_val = float(roe) if roe is not None else 0.0
    margin_val = float(profit_margin) if profit_margin is not None else 0.0
    growth_val = float(revenue_growth) if revenue_growth is not None else 0.0
    pe_val = float(pe_forward) if pe_forward is not None else 0.0

    score = (roe_val * 40.0) + (margin_val * 30.0) + (growth_val * 20.0) - (pe_val * 0.5)
    return round(score, 4)


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
    min_market_cap: int = DEFAULT_MIN_MARKET_CAP,
    max_pe: float = DEFAULT_MAX_PE,
    min_roe: float = DEFAULT_MIN_ROE,
    max_de: float = DEFAULT_MAX_DE,
    min_dollar_volume: float = DEFAULT_MIN_DOLLAR_VOLUME,
    max_ev_ebitda: float = DEFAULT_MAX_EV_EBITDA,
    limit: int = 25,
    output_csv: str | Path | None = "screened_results.csv",
    print_table: bool = True,
) -> list[dict[str, Any]]:
    """
    Execute the quantitative fundamental screen, output ranked results to console, and write CSV.

    Args:
        min_market_cap: Minimum market cap in USD.
        max_pe: Maximum forward P/E ratio.
        min_roe: Minimum Return on Equity (as decimal).
        max_de: Maximum Debt/Equity ratio.
        min_dollar_volume: Minimum daily dollar volume.
        max_ev_ebitda: Maximum EV/EBITDA multiple.
        limit: Number of top-ranked results to return.
        output_csv: Path to output CSV file, or None to skip file export.
        print_table: Whether to print the formatted table to standard output.

    Returns:
        list[dict[str, Any]]: Screened and scored stock records.
    """
    logger.info(
        "Executing quantitative multi-factor screen (filters: Cap >= $%s, PE < %.1f, ROE > %.1f%%, D/E < %.1f, $Vol >= $%s)...",
        f"{min_market_cap:n}",
        max_pe,
        min_roe * 100.0,
        max_de,
        f"{min_dollar_volume:n}",
    )

    results = db.query_screened_stocks(
        min_market_cap=min_market_cap,
        max_pe=max_pe,
        min_roe=min_roe,
        max_de=max_de,
        min_dollar_volume=min_dollar_volume,
        max_ev_ebitda=max_ev_ebitda,
        limit=limit,
    )

    if not results:
        logger.warning("No equities matched the specified screening criteria.")
        if print_table:
            print("\n[!] No stocks matched the current screening criteria.\n")
        return []

    # Assign ranks and recalculate factor score if needed
    for rank, row in enumerate(results, start=1):
        row["rank"] = rank
        if "factor_score" not in row or row["factor_score"] is None:
            row["factor_score"] = calculate_factor_score(
                roe=row.get("roe"),
                profit_margin=row.get("profit_margin"),
                revenue_growth=row.get("revenue_growth"),
                pe_forward=row.get("pe_forward"),
            )

    # Print formatted table to console
    if print_table:
        table_rows = []
        for r in results:
            table_rows.append([
                r["rank"],
                r["ticker"],
                (r["name"][:22] + "..") if len(r.get("name", "")) > 24 else r.get("name", ""),
                (r["sector"][:14]) if r.get("sector") else "N/A",
                f"${float(r['current_price']):.2f}" if r.get("current_price") else "N/A",
                _format_currency_compact(r.get("market_cap")),
                _format_ratio(r.get("pe_forward")),
                _format_ratio(r.get("ev_to_ebitda")),
                _format_pct(r.get("roe")),
                _format_ratio(r.get("debt_to_equity")),
                _format_pct(r.get("profit_margin")),
                _format_pct(r.get("revenue_growth")),
                _format_currency_compact(r.get("dollar_volume")),
                f"{float(r['factor_score']):.2f}",
            ])

        headers = [
            "Rank", "Ticker", "Company", "Sector", "Price",
            "Market Cap", "Fwd P/E", "EV/EBITDA", "ROE",
            "D/E", "Margin", "Rev Grw", "$ Volume", "Score"
        ]

        title = f"\n=== TOP {len(results)} SCREENED EQUITIES (MULTI-FACTOR RANKING) ==="
        print(title)
        print(tabulate(table_rows, headers=headers, tablefmt="github", stralign="left", numalign="right"))
        print(f"Factor Scoring Model: Score = (ROE * 40) + (Margin * 30) + (Growth * 20) - (Fwd P/E * 0.5)\n")

    # Export to CSV
    if output_csv:
        csv_path = Path(output_csv)
        fieldnames = [
            "rank",
            "ticker",
            "name",
            "sector",
            "industry",
            "factor_score",
            "current_price",
            "market_cap",
            "pe_forward",
            "ev_to_ebitda",
            "roe",
            "debt_to_equity",
            "profit_margin",
            "revenue_growth",
            "volume",
            "dollar_volume",
            "fiscal_date",
        ]

        with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in results:
                writer.writerow(row)

        logger.info("Screened results successfully exported to: %s", csv_path.resolve())
        print(f"[✓] Screen report written to: {csv_path.resolve()}")

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_screen(limit=10)
