"""
Stock Universe Discovery and Benchmark Index Ingestion.

Supports high-conviction institutional benchmark universes:
  - S&P 500 (Core ~500 US large-cap equities with GICS Sector & Sub-Industry)
  - Nasdaq 100 (Top 100 innovative non-financial growth leaders)
  - Dow Jones Industrial Average (30 blue-chip market titans)
  - Cleaned SEC EDGAR (broad market, scrubbed of SPAC units, warrants, rights, and preferreds)
"""

from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any

import requests

from config import settings

logger = logging.getLogger(__name__)

SEC_URL = "https://www.sec.gov/files/company_tickers.json"
SP500_CSV_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
)

# Benchmark Top Curated Lists for High Resilience
DOW30_TICKERS = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK",
    "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT",
]

NASDAQ100_TICKERS = [
    "AAPL", "ABNB", "ADBE", "ADI", "ADP", "AMAT", "AMD", "AMGN", "AMZN", "ANSS",
    "ASML", "AVGO", "AZN", "BIIB", "BKNG", "BKR", "CDNS", "CDW", "CEG", "CHTR",
    "CMCSA", "COST", "CPRT", "CRWD", "CSCO", "CSGP", "CSX", "CTAS", "CTSH", "DASH",
    "DLTR", "DXCM", "EA", "EXC", "FAST", "FTNT", "GEHC", "GILD", "GOOG", "GOOGL",
    "HON", "IDXX", "ILMN", "INTC", "INTU", "ISRG", "KDP", "KHC", "KLAC", "LIN",
    "LRCX", "LULU", "MAR", "MCHP", "MDB", "MDLZ", "MELI", "META", "MNST", "MRVL",
    "MSFT", "MU", "NFLX", "NVDA", "NXPI", "ODFL", "ON", "ORLY", "PANW", "PAYX",
    "PCAR", "PDD", "PEP", "PYPL", "QCOM", "REGN", "ROP", "ROST", "SBUX", "SNPS",
    "TEAM", "TMUS", "TSLA", "TTD", "TTWO", "TXN", "VRSK", "VRTX", "WBD", "WDAY",
    "XEL", "ZS",
]


def fetch_sp500_universe() -> list[dict[str, Any]]:
    """
    Fetch S&P 500 constituents with official GICS sector and industry classification.

    Returns:
        List of company dicts ready for database insertion.
    """
    logger.info("Fetching S&P 500 constituents from verified benchmark feed...")
    response = requests.get(SP500_CSV_URL, timeout=settings.REQUEST_TIMEOUT)
    response.raise_for_status()

    reader = csv.DictReader(io.StringIO(response.text))
    records: list[dict[str, Any]] = []

    for row in reader:
        raw_sym = row.get("Symbol", "").strip().upper()
        # Normalize BRK.B -> BRK-B for Yahoo Finance compatibility
        ticker = raw_sym.replace(".", "-")
        if not ticker:
            continue

        records.append(
            {
                "ticker": ticker,
                "name": row.get("Security", ticker).strip(),
                "cik": str(row.get("CIK", "0")).zfill(10),
                "sector": row.get("GICS Sector", "").strip() or None,
                "industry": row.get("GICS Sub-Industry", "").strip() or None,
                "universe": "SP500",
                "is_active": True,
            }
        )

    logger.info("Successfully ingested %d S&P 500 companies with GICS classifications.", len(records))
    return records


def fetch_nasdaq100_universe() -> list[dict[str, Any]]:
    """
    Fetch the Nasdaq 100 growth & technology innovators universe.

    Returns:
        List of company dicts.
    """
    logger.info("Building Nasdaq 100 universe (%d constituents)...", len(NASDAQ100_TICKERS))
    records: list[dict[str, Any]] = []
    for ticker in sorted(NASDAQ100_TICKERS):
        records.append(
            {
                "ticker": ticker,
                "name": ticker,
                "cik": "0000000000",
                "sector": None,  # Will be enriched during fundamentals fetch
                "industry": None,
                "universe": "NASDAQ100",
                "is_active": True,
            }
        )
    return records


def fetch_dow30_universe() -> list[dict[str, Any]]:
    """
    Fetch the Dow Jones Industrial Average mega-cap universe.

    Returns:
        List of company dicts.
    """
    logger.info("Building Dow Jones 30 universe...")
    records: list[dict[str, Any]] = []
    for ticker in sorted(DOW30_TICKERS):
        records.append(
            {
                "ticker": ticker,
                "name": ticker,
                "cik": "0000000000",
                "sector": None,
                "industry": None,
                "universe": "DOW30",
                "is_active": True,
            }
        )
    return records


def fetch_us_stock_universe() -> list[dict[str, Any]]:
    """
    Pull full US equity universe from SEC EDGAR and scrub out SPAC warrants,
    units, rights, preferred shares, and OTC noise.

    Returns:
        List of cleaned common stock company records.
    """
    headers = {"User-Agent": settings.SEC_USER_AGENT}
    logger.info("Querying SEC EDGAR company registry...")
    response = requests.get(SEC_URL, headers=headers, timeout=settings.REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    # Regex pattern matching derivative tickers:
    # - 5-letter tickers ending with U (unit), W/WS (warrant), R (right), P/PR (preferred)
    # - Tickers with punctuation dots or hyphens
    junk_pattern = re.compile(r"^[A-Z]{4,5}[UWR]$|^[A-Z]{4,5}WS$|^[A-Z]{4,5}PR$")

    records: list[dict[str, Any]] = []
    seen = set()

    for item in data.values():
        ticker = item["ticker"].upper().strip()

        if "." in ticker or "-" in ticker:
            continue
        if len(ticker) > 5 or junk_pattern.match(ticker):
            continue
        if ticker in seen:
            continue

        seen.add(ticker)
        records.append(
            {
                "ticker": ticker,
                "name": item.get("title", ticker).strip(),
                "cik": str(item["cik_str"]).zfill(10),
                "sector": None,
                "industry": None,
                "universe": "SEC",
                "is_active": True,
            }
        )

    logger.info("SEC universe sanitized: %d clean common stock candidates retained.", len(records))
    return records


# High-Growth Emerging Market Leaders & Innovative Compounders ($1B - $35B Sweet Spot)
UPCOMING_GROWTH_TICKERS = [
    "APP", "PLTR", "CRWD", "NET", "IOT", "AXON", "CLS", "VRT", "HOOD", "NU",
    "DKNG", "ALAB", "CELH", "DUOL", "TOST", "MDB", "FSLR", "ELF", "SYM", "HIMS",
    "CAVA", "WING", "ONON", "DECK", "TMDX", "FUTU", "RDDT", "CART", "ASTS", "RKLB",
    "POWI", "S", "TEM", "SOFI", "ARM", "RBLX", "PATH", "BILL", "DOCN", "CFLT",
    "GTLB", "ESTC", "FOUR", "FRSH", "BRZE", "PCOR", "KVYO", "SMAR", "ALTR", "PRCT",
]


def fetch_upcoming_universe() -> list[dict[str, Any]]:
    """
    Fetch curated basket of emerging high-growth compounders and mid-cap leaders.

    Returns:
        List of company dicts.
    """
    logger.info("Building Upcoming Growth Leaders universe (%d candidates)...", len(UPCOMING_GROWTH_TICKERS))
    records: list[dict[str, Any]] = []
    for ticker in sorted(UPCOMING_GROWTH_TICKERS):
        records.append(
            {
                "ticker": ticker,
                "name": ticker,
                "cik": "0000000000",
                "sector": None,
                "industry": None,
                "universe": "UPCOMING",
                "is_active": True,
            }
        )
    return records


def fetch_sp400_universe() -> list[dict[str, Any]]:
    """
    Fetch S&P MidCap 400 constituents (~$2B to $25B emerging corporate leaders).

    Returns:
        List of company dicts.
    """
    logger.info("Extracting S&P MidCap 400 constituents...")
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
    headers = {"User-Agent": settings.SEC_USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=settings.REQUEST_TIMEOUT)
        response.raise_for_status()
        html = response.text

        pattern = re.compile(
            r'<td[^>]*><a rel=\"mw:ExtLink nofollow\"[^>]*>([A-Za-z0-9\.\-]+)</a></td>\s*'
            r'<td[^>]*><a rel=\"mw:WikiLink\"[^>]*>([^<]+)</a></td>\s*'
            r'<td[^>]*>([^<]+)</td>\s*'
            r'<td[^>]*>([^<]+)</td>',
            re.DOTALL,
        )
        matches = pattern.findall(html)
        records: list[dict[str, Any]] = []
        for sym, name, sec, ind in matches:
            ticker = sym.replace(".", "-").strip().upper()
            if not ticker:
                continue
            records.append(
                {
                    "ticker": ticker,
                    "name": name.strip(),
                    "cik": "0000000000",
                    "sector": sec.strip() or None,
                    "industry": ind.strip() or None,
                    "universe": "SP400",
                    "is_active": True,
                }
            )

        if records:
            logger.info("Successfully ingested %d S&P MidCap 400 companies.", len(records))
            return records
    except Exception as exc:
        logger.warning("Failed fetching S&P 400 from Wikipedia: %s. Falling back to upcoming list.", exc)

    return fetch_upcoming_universe()


def fetch_universe(source: str = "sp500") -> list[dict[str, Any]]:
    """
    Universal factory function to ingest benchmark equity universes.

    Args:
        source: 'sp500', 'sp400', 'upcoming', 'nasdaq100', 'dow30', or 'sec' / 'all'.

    Returns:
        List of company dicts ready for persistence in PostgreSQL.
    """
    src = source.lower().strip()
    if src in ("sp500", "s&p500", "sp_500"):
        return fetch_sp500_universe()
    if src in ("sp400", "midcap", "s&p400", "mid"):
        return fetch_sp400_universe()
    if src in ("upcoming", "growth", "breakout", "emerging"):
        return fetch_upcoming_universe()
    if src in ("nasdaq100", "nasdaq", "ndx", "qqq"):
        return fetch_nasdaq100_universe()
    if src in ("dow", "dow30", "djia"):
        return fetch_dow30_universe()
    if src in ("sec", "all", "broad"):
        return fetch_us_stock_universe()

    logger.warning("Unrecognized universe source '%s'. Defaulting to S&P 500.", source)
    return fetch_sp500_universe()


def fetch_equity_universe() -> list[str]:
    """Legacy helper returning ticker symbols only."""
    return [r["ticker"] for r in fetch_us_stock_universe()]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sp = fetch_sp500_universe()
    print(f"Discovered {len(sp)} S&P 500 equities with GICS sector mapping.")

