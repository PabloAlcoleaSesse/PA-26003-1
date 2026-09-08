"""
Database access and persistence layer for the Stock Screener.

Manages PostgreSQL connection pooling, schema migrations, batch upserts,
cache staleness detection, and pure SQL multi-factor screening queries.
"""

from __future__ import annotations

import atexit
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Generator, Sequence
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from config import settings

logger = logging.getLogger(__name__)

# Connection pool singleton and thread safety lock
_pool: ConnectionPool | None = None
_pool_lock = Lock()


def get_pool() -> ConnectionPool:
    """
    Retrieve or lazily initialize the thread-safe connection pool.

    Returns:
        ConnectionPool: Active psycopg_pool instance configured with application settings.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                logger.info(
                    "Initializing PostgreSQL connection pool (min=%d, max=%d) -> %s:%d/%s",
                    settings.DB_MIN_POOL_SIZE,
                    settings.DB_MAX_POOL_SIZE,
                    settings.DB_HOST,
                    settings.DB_PORT,
                    settings.DB_NAME,
                )
                _pool = ConnectionPool(
                    conninfo=settings.conn_string,
                    min_size=settings.DB_MIN_POOL_SIZE,
                    max_size=settings.DB_MAX_POOL_SIZE,
                    timeout=10,
                    reconnect_timeout=10,
                    open=True,
                    kwargs={"row_factory": dict_row, "autocommit": False},
                )
    return _pool


def close_pool() -> None:
    """Close all connections and terminate the connection pool."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            logger.info("Closing PostgreSQL connection pool...")
            _pool.close()
            _pool = None


atexit.register(close_pool)



@contextmanager
def get_connection() -> Generator[psycopg.Connection, None, None]:
    """
    Context manager yielding a pooled database connection with automatic transaction handling.

    Yields:
        psycopg.Connection: Active database connection configured with dict_row factory.
    """
    pool = get_pool()
    with pool.connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db(schema_path: str | Path | None = None) -> None:
    """
    Execute DDL statements from schema.sql to initialize or migrate the database schema.

    Args:
        schema_path: Optional explicit path to schema.sql. Defaults to local directory.
    """
    if schema_path is None:
        potential_paths = [
            Path(__file__).parent / "schema.sql",
            Path("schema.sql"),
        ]
        target_path = next((p for p in potential_paths if p.exists()), None)
        if target_path is None:
            raise FileNotFoundError("Could not find 'schema.sql' in project path.")
    else:
        target_path = Path(schema_path)
        if not target_path.exists():
            raise FileNotFoundError(f"Schema file not found at: {target_path}")

    logger.info("Applying database schema from %s...", target_path.resolve())
    sql_content = target_path.read_text(encoding="utf-8")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_content)
    logger.info("Database schema initialized successfully.")


def upsert_company(
    ticker: str,
    name: str,
    cik: str,
    sector: str | None = None,
    industry: str | None = None,
    exchange: str | None = None,
    universe: str = "SEC",
    is_active: bool = True,
) -> None:
    """Insert or update a single company record in the companies table."""
    sql = """
        INSERT INTO companies (ticker, name, cik, sector, industry, exchange, universe, is_active, updated_at)
        VALUES (%(ticker)s, %(name)s, %(cik)s, %(sector)s, %(industry)s, %(exchange)s, %(universe)s, %(is_active)s, NOW())
        ON CONFLICT (ticker) DO UPDATE SET
            name = EXCLUDED.name,
            cik = EXCLUDED.cik,
            sector = COALESCE(EXCLUDED.sector, companies.sector),
            industry = COALESCE(EXCLUDED.industry, companies.industry),
            exchange = COALESCE(EXCLUDED.exchange, companies.exchange),
            universe = CASE WHEN companies.universe = 'SEC' AND EXCLUDED.universe != 'SEC' THEN EXCLUDED.universe ELSE companies.universe END,
            is_active = EXCLUDED.is_active,
            updated_at = NOW();
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "ticker": ticker.upper().strip(),
                    "name": name.strip(),
                    "cik": cik.strip(),
                    "sector": sector,
                    "industry": industry,
                    "exchange": exchange,
                    "universe": universe,
                    "is_active": is_active,
                },
            )


def update_company_profile(
    ticker: str,
    sector: str | None = None,
    industry: str | None = None,
    exchange: str | None = None,
) -> None:
    """Update company sector and industry classifications discovered during fetching."""
    sql = """
        UPDATE companies
        SET sector = COALESCE(%(sector)s, sector),
            industry = COALESCE(%(industry)s, industry),
            exchange = COALESCE(%(exchange)s, exchange),
            updated_at = NOW()
        WHERE ticker = %(ticker)s;
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "ticker": ticker.upper().strip(),
                    "sector": sector,
                    "industry": industry,
                    "exchange": exchange,
                },
            )


def upsert_companies(records: list[dict[str, Any]], batch_size: int = 1000) -> int:
    """
    Bulk insert or update companies discovered from benchmarks or SEC EDGAR.

    Args:
        records: List of dictionaries with keys: ticker, name, cik, universe, sector, industry.
        batch_size: Number of records per executemany chunk.

    Returns:
        int: Total number of company records processed.
    """
    if not records:
        return 0

    sql = """
        INSERT INTO companies (ticker, name, cik, sector, industry, exchange, universe, is_active, updated_at)
        VALUES (%(ticker)s, %(name)s, %(cik)s, %(sector)s, %(industry)s, %(exchange)s, %(universe)s, %(is_active)s, NOW())
        ON CONFLICT (ticker) DO UPDATE SET
            name = EXCLUDED.name,
            cik = EXCLUDED.cik,
            sector = COALESCE(EXCLUDED.sector, companies.sector),
            industry = COALESCE(EXCLUDED.industry, companies.industry),
            exchange = COALESCE(EXCLUDED.exchange, companies.exchange),
            universe = CASE WHEN companies.universe = 'SEC' AND EXCLUDED.universe != 'SEC' THEN EXCLUDED.universe ELSE companies.universe END,
            is_active = EXCLUDED.is_active,
            updated_at = NOW();
    """

    sanitized_records = [
        {
            "ticker": r["ticker"].upper().strip(),
            "name": r["name"].strip(),
            "cik": str(r.get("cik", "0")).zfill(10),
            "sector": r.get("sector"),
            "industry": r.get("industry"),
            "exchange": r.get("exchange"),
            "universe": r.get("universe", "SEC"),
            "is_active": r.get("is_active", True),
        }
        for r in records
    ]

    total_inserted = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for i in range(0, len(sanitized_records), batch_size):
                chunk = sanitized_records[i : i + batch_size]
                cur.executemany(sql, chunk)
                total_inserted += len(chunk)

    logger.info("Successfully upserted %d companies into PostgreSQL.", total_inserted)
    return total_inserted


def upsert_fundamentals(records: list[dict[str, Any]], batch_size: int = 500) -> int:
    """
    Bulk upsert fundamental financial data including quality, valuation, and momentum.

    Args:
        records: List of dictionaries representing fundamentals rows.
        batch_size: Batch size for database transmission.

    Returns:
        int: Total number of records upserted.
    """
    if not records:
        return 0

    sql = """
        INSERT INTO fundamentals (
            ticker,
            fiscal_date,
            filing_date,
            market_cap,
            pe_forward,
            trailing_pe,
            peg_ratio,
            price_to_book,
            ev_to_ebitda,
            roe,
            return_on_assets,
            debt_to_equity,
            profit_margin,
            operating_margin,
            gross_margin,
            revenue_growth,
            free_cash_flow,
            current_price,
            volume,
            price_return_6m,
            price_as_of,
            technical_valid,
            is_stage_2,
            stage_2_rules_passed,
            is_vcp,
            is_breakout,
            price_return_1m,
            price_return_3m,
            relative_return_1m,
            relative_return_3m,
            relative_return_6m,
            sma_50,
            sma_200,
            pattern_score,
            detected_patterns,
            dist_52w_high,
            rsi_14,
            ud_volume_ratio,
            raw_payload,
            updated_at
        ) VALUES (
            %(ticker)s,
            %(fiscal_date)s,
            %(filing_date)s,
            %(market_cap)s,
            %(pe_forward)s,
            %(trailing_pe)s,
            %(peg_ratio)s,
            %(price_to_book)s,
            %(ev_to_ebitda)s,
            %(roe)s,
            %(return_on_assets)s,
            %(debt_to_equity)s,
            %(profit_margin)s,
            %(operating_margin)s,
            %(gross_margin)s,
            %(revenue_growth)s,
            %(free_cash_flow)s,
            %(current_price)s,
            %(volume)s,
            %(price_return_6m)s,
            %(price_as_of)s,
            %(technical_valid)s,
            %(is_stage_2)s,
            %(stage_2_rules_passed)s,
            %(is_vcp)s,
            %(is_breakout)s,
            %(price_return_1m)s,
            %(price_return_3m)s,
            %(relative_return_1m)s,
            %(relative_return_3m)s,
            %(relative_return_6m)s,
            %(sma_50)s,
            %(sma_200)s,
            %(pattern_score)s,
            %(detected_patterns)s,
            %(dist_52w_high)s,
            %(rsi_14)s,
            %(ud_volume_ratio)s,
            %(raw_payload)s,
            NOW()
        )
        ON CONFLICT (ticker, fiscal_date) DO UPDATE SET
            filing_date = EXCLUDED.filing_date,
            market_cap = EXCLUDED.market_cap,
            pe_forward = EXCLUDED.pe_forward,
            trailing_pe = EXCLUDED.trailing_pe,
            peg_ratio = EXCLUDED.peg_ratio,
            price_to_book = EXCLUDED.price_to_book,
            ev_to_ebitda = EXCLUDED.ev_to_ebitda,
            roe = EXCLUDED.roe,
            return_on_assets = EXCLUDED.return_on_assets,
            debt_to_equity = EXCLUDED.debt_to_equity,
            profit_margin = EXCLUDED.profit_margin,
            operating_margin = EXCLUDED.operating_margin,
            gross_margin = EXCLUDED.gross_margin,
            revenue_growth = EXCLUDED.revenue_growth,
            free_cash_flow = EXCLUDED.free_cash_flow,
            current_price = EXCLUDED.current_price,
            volume = EXCLUDED.volume,
            price_return_6m = EXCLUDED.price_return_6m,
            price_as_of = EXCLUDED.price_as_of,
            technical_valid = EXCLUDED.technical_valid,
            is_stage_2 = EXCLUDED.is_stage_2,
            stage_2_rules_passed = EXCLUDED.stage_2_rules_passed,
            is_vcp = EXCLUDED.is_vcp,
            is_breakout = EXCLUDED.is_breakout,
            price_return_1m = EXCLUDED.price_return_1m,
            price_return_3m = EXCLUDED.price_return_3m,
            relative_return_1m = EXCLUDED.relative_return_1m,
            relative_return_3m = EXCLUDED.relative_return_3m,
            relative_return_6m = EXCLUDED.relative_return_6m,
            sma_50 = EXCLUDED.sma_50,
            sma_200 = EXCLUDED.sma_200,
            pattern_score = EXCLUDED.pattern_score,
            detected_patterns = EXCLUDED.detected_patterns,
            dist_52w_high = EXCLUDED.dist_52w_high,
            rsi_14 = EXCLUDED.rsi_14,
            ud_volume_ratio = EXCLUDED.ud_volume_ratio,
            raw_payload = EXCLUDED.raw_payload,
            updated_at = NOW();
    """

    sanitized = []
    for r in records:
        payload = r.get("raw_payload")
        if payload is not None and not isinstance(payload, Jsonb):
            payload = Jsonb(payload)

        sanitized.append({
            "ticker": r["ticker"].upper().strip(),
            "fiscal_date": r["fiscal_date"],
            "filing_date": r.get("filing_date"),
            "market_cap": r.get("market_cap"),
            "pe_forward": r.get("pe_forward"),
            "trailing_pe": r.get("trailing_pe"),
            "peg_ratio": r.get("peg_ratio"),
            "price_to_book": r.get("price_to_book"),
            "ev_to_ebitda": r.get("ev_to_ebitda"),
            "roe": r.get("roe"),
            "return_on_assets": r.get("return_on_assets"),
            "debt_to_equity": r.get("debt_to_equity"),
            "profit_margin": r.get("profit_margin"),
            "operating_margin": r.get("operating_margin"),
            "gross_margin": r.get("gross_margin"),
            "revenue_growth": r.get("revenue_growth"),
            "free_cash_flow": r.get("free_cash_flow"),
            "current_price": r.get("current_price"),
            "volume": r.get("volume"),
            "price_return_6m": r.get("price_return_6m"),
            "pattern_score": r.get("pattern_score"),
            "price_as_of": r.get("price_as_of"),
            "technical_valid": r.get("technical_valid"),
            "is_stage_2": r.get("is_stage_2"),
            "stage_2_rules_passed": r.get("stage_2_rules_passed"),
            "is_vcp": r.get("is_vcp"),
            "is_breakout": r.get("is_breakout"),
            "price_return_1m": r.get("price_return_1m"),
            "price_return_3m": r.get("price_return_3m"),
            "relative_return_1m": r.get("relative_return_1m"),
            "relative_return_3m": r.get("relative_return_3m"),
            "relative_return_6m": r.get("relative_return_6m"),
            "sma_50": r.get("sma_50"),
            "sma_200": r.get("sma_200"),
            "detected_patterns": r.get("detected_patterns", "Neutral"),
            "dist_52w_high": r.get("dist_52w_high"),
            "rsi_14": r.get("rsi_14"),
            "ud_volume_ratio": r.get("ud_volume_ratio"),
            "raw_payload": payload,
        })

    total_inserted = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for i in range(0, len(sanitized), batch_size):
                chunk = sanitized[i : i + batch_size]
                cur.executemany(sql, chunk)
                total_inserted += len(chunk)

    logger.info("Successfully persisted %d fundamental records.", total_inserted)
    return total_inserted


def get_stale_tickers(
    tickers: Sequence[str] | None = None,
    universe: str | None = None,
    max_age_days: int | None = None,
) -> list[str]:
    """
    Identify tickers requiring fundamental data fetching.
    Prioritizes benchmark universes (S&P 500, Nasdaq 100) first to avoid alphabetical bias.

    Args:
        tickers: Explicit tickers list.
        universe: Optional universe filter (e.g. 'SP500', 'NASDAQ100').
        max_age_days: Cache freshness threshold.

    Returns:
        List of tickers ordered by benchmark priority.
    """
    if tickers is not None and len(tickers) == 0:
        return []

    max_age_days = max_age_days if max_age_days is not None else settings.DATA_MAX_AGE_DAYS
    latest_session = datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    while latest_session.weekday() >= 5:
        latest_session -= timedelta(days=1)
    filters = ["c.is_active = TRUE"]
    params: dict[str, Any] = {"max_age_days": max_age_days, "latest_session": latest_session}

    if universe:
        if universe.upper().strip() == "UPCOMING":
            from universe import UPCOMING_GROWTH_TICKERS
            filters.append("(c.universe = 'UPCOMING' OR c.ticker = ANY(%(upcoming_tickers)s))")
            params["upcoming_tickers"] = list(UPCOMING_GROWTH_TICKERS)
        else:
            filters.append("c.universe = %(universe)s")
            params["universe"] = universe.upper().strip()

    if tickers:
        clean_tickers = [t.upper().strip() for t in tickers]
        filters.append("c.ticker = ANY(%(tickers)s)")
        params["tickers"] = clean_tickers

    where_clause = " AND ".join(filters)

    query = f"""
        SELECT c.ticker
        FROM companies c
        LEFT JOIN (
            SELECT DISTINCT ON (ticker) ticker, updated_at AS latest_update,
                   price_as_of, technical_valid
            FROM fundamentals
            ORDER BY ticker, fiscal_date DESC, updated_at DESC
        ) f ON c.ticker = f.ticker
        WHERE {where_clause}
          AND (
              f.latest_update IS NULL
              OR f.latest_update < NOW() - (%(max_age_days)s || ' days')::INTERVAL
              OR f.price_as_of IS NULL
              OR f.price_as_of < %(latest_session)s
              OR f.technical_valid IS NOT TRUE
          )
        ORDER BY
            CASE
                WHEN c.universe = 'SP500' THEN 1
                WHEN c.universe = 'NASDAQ100' THEN 2
                WHEN c.universe = 'DOW30' THEN 3
                ELSE 4
            END,
            c.ticker ASC;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    stale_list = [row["ticker"] for row in rows]
    logger.info(
        "Identified %d stale tickers (universe: %s, cache threshold: %d days).",
        len(stale_list),
        universe or "all",
        max_age_days,
    )
    return stale_list


def query_screened_stocks(
    min_market_cap: int = 500_000_000,
    max_market_cap: int | None = None,
    max_pe: float = 40.0,
    min_roe: float = 0.10,
    max_de: float = 200.0,
    min_dollar_volume: float = 2_000_000.0,
    max_ev_ebitda: float = 30.0,
    min_growth: float | None = 0.0,
    min_margin: float | None = 0.03,
    min_pattern_score: float | None = None,
    universe: str | None = None,
    sector: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve candidate stocks with enriched fundamentals & technical pattern metrics.

    Args:
        min_market_cap: Minimum market cap in USD.
        max_market_cap: Maximum market cap in USD (for targeting emerging mid-caps).
        max_pe: Maximum forward P/E.
        min_roe: Minimum Return on Equity.
        max_de: Maximum Debt/Equity ratio.
        min_dollar_volume: Minimum daily dollar volume.
        max_ev_ebitda: Maximum EV/EBITDA multiple.
        min_growth: Minimum YoY revenue growth.
        min_margin: Minimum operating or net margin.
        min_pattern_score: Minimum technical pattern score (0 - 100).
        universe: Benchmark universe filter ('SP500', 'SP400', 'UPCOMING', etc.).
        sector: Economic sector filter ('Technology', 'Healthcare', etc.).
        limit: Max candidates to retrieve.

    Returns:
        List of matching equity records.
    """
    where_clauses = [
        "c.is_active = TRUE",
        "lf.market_cap >= %(min_market_cap)s",
        "lf.dollar_volume >= %(min_dollar_volume)s",
    ]

    params: dict[str, Any] = {
        "min_market_cap": min_market_cap,
        "min_dollar_volume": min_dollar_volume,
        "limit": limit,
    }

    if max_pe is not None:
        where_clauses.append("lf.pe_forward > 0 AND lf.pe_forward <= %(max_pe)s")
        params["max_pe"] = max_pe

    if max_ev_ebitda is not None:
        where_clauses.append("(c.sector IN ('Financials', 'Financial Services') OR (lf.ev_to_ebitda > 0 AND lf.ev_to_ebitda <= %(max_ev_ebitda)s))")
        params["max_ev_ebitda"] = max_ev_ebitda

    if min_roe is not None:
        effective_min_roe = min_roe / 100.0 if min_roe > 1.0 else min_roe
        where_clauses.append("lf.roe >= %(min_roe)s")
        params["min_roe"] = effective_min_roe

    if max_de is not None:
        effective_max_de = max_de * 100.0 if 0 < max_de < 5.0 else max_de
        where_clauses.append("(lf.debt_to_equity IS NULL OR (lf.debt_to_equity >= 0 AND lf.debt_to_equity <= %(max_de)s))")
        params["max_de"] = effective_max_de

    if max_market_cap is not None:
        where_clauses.append("lf.market_cap <= %(max_market_cap)s")
        params["max_market_cap"] = max_market_cap

    if min_pattern_score is not None:
        where_clauses.append("lf.pattern_score IS NOT NULL AND lf.pattern_score >= %(min_pattern_score)s")
        params["min_pattern_score"] = min_pattern_score

    if universe:
        if universe.upper().strip() == "UPCOMING":
            from universe import UPCOMING_GROWTH_TICKERS
            where_clauses.append("(c.universe = 'UPCOMING' OR c.ticker = ANY(%(upcoming_tickers)s))")
            params["upcoming_tickers"] = list(UPCOMING_GROWTH_TICKERS)
        else:
            where_clauses.append("c.universe = %(universe)s")
            params["universe"] = universe.upper().strip()

    if sector:
        where_clauses.append("LOWER(c.sector) LIKE LOWER(%(sector)s)")
        params["sector"] = f"%{sector.strip()}%"

    if min_growth is not None:
        where_clauses.append("lf.revenue_growth >= %(min_growth)s")
        params["min_growth"] = min_growth

    if min_margin is not None:
        where_clauses.append("(lf.profit_margin >= %(min_margin)s OR lf.operating_margin >= %(min_margin)s)")
        params["min_margin"] = min_margin

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        WITH latest_fundamentals AS (
            SELECT DISTINCT ON (f.ticker)
                f.id,
                f.ticker,
                f.fiscal_date,
                f.filing_date,
                f.market_cap,
                f.pe_forward,
                f.trailing_pe,
                f.peg_ratio,
                f.price_to_book,
                f.ev_to_ebitda,
                f.roe,
                f.return_on_assets,
                f.debt_to_equity,
                f.profit_margin,
                f.operating_margin,
                f.gross_margin,
                f.revenue_growth,
                f.free_cash_flow,
                f.current_price,
                f.volume,
                f.price_return_6m,
                f.price_as_of,
                f.technical_valid,
                f.is_stage_2,
                f.stage_2_rules_passed,
                f.is_vcp,
                f.is_breakout,
                f.price_return_1m,
                f.price_return_3m,
                f.relative_return_1m,
                f.relative_return_3m,
                f.relative_return_6m,
                f.sma_50,
                f.sma_200,
                f.pattern_score,
                f.detected_patterns,
                f.dist_52w_high,
                f.rsi_14,
                f.ud_volume_ratio,
                (f.current_price * f.volume) AS dollar_volume,
                f.updated_at
            FROM fundamentals f
            ORDER BY f.ticker, f.fiscal_date DESC, f.updated_at DESC
        )
        SELECT
            c.ticker,
            c.name,
            c.sector,
            c.industry,
            c.universe,
            lf.market_cap,
            lf.pe_forward,
            lf.trailing_pe,
            lf.peg_ratio,
            lf.price_to_book,
            lf.ev_to_ebitda,
            lf.roe,
            lf.return_on_assets,
            lf.debt_to_equity,
            lf.profit_margin,
            lf.operating_margin,
            lf.gross_margin,
            lf.revenue_growth,
            lf.free_cash_flow,
            lf.current_price,
            lf.volume,
            lf.dollar_volume,
            lf.price_return_6m,
            lf.price_as_of,
            lf.technical_valid,
            lf.is_stage_2,
            lf.stage_2_rules_passed,
            lf.is_vcp,
            lf.is_breakout,
            lf.price_return_1m,
            lf.price_return_3m,
            lf.relative_return_1m,
            lf.relative_return_3m,
            lf.relative_return_6m,
            lf.sma_50,
            lf.sma_200,
            lf.updated_at,
            lf.pattern_score,
            lf.detected_patterns,
            lf.dist_52w_high,
            lf.rsi_14,
            lf.ud_volume_ratio,
            lf.fiscal_date
        FROM latest_fundamentals lf
        JOIN companies c ON c.ticker = lf.ticker
        WHERE {where_sql}
        ORDER BY lf.market_cap DESC
        LIMIT %(limit)s;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    logger.info("Screening query candidate pool: %d equities retrieved.", len(rows))
    return list(rows)
