"""
Database access and persistence layer for the Stock Screener.

Manages PostgreSQL connection pooling, schema migrations, batch upserts,
cache staleness detection, and pure SQL multi-factor screening queries.
"""

from __future__ import annotations

import atexit
import logging
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Generator, Sequence

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
    is_active: bool = True,
) -> None:
    """
    Insert or update a single company record in the companies table.

    Args:
        ticker: Stock ticker symbol (primary key).
        name: Official company legal or trade name.
        cik: 10-digit zero-padded SEC Central Index Key.
        sector: Economic sector name (optional).
        industry: Industry subcategory name (optional).
        is_active: Whether the equity is actively trading.
    """
    sql = """
        INSERT INTO companies (ticker, name, cik, sector, industry, is_active, updated_at)
        VALUES (%(ticker)s, %(name)s, %(cik)s, %(sector)s, %(industry)s, %(is_active)s, NOW())
        ON CONFLICT (ticker) DO UPDATE SET
            name = EXCLUDED.name,
            cik = EXCLUDED.cik,
            sector = COALESCE(EXCLUDED.sector, companies.sector),
            industry = COALESCE(EXCLUDED.industry, companies.industry),
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
                    "is_active": is_active,
                },
            )


def upsert_companies(records: list[dict[str, Any]], batch_size: int = 1000) -> int:
    """
    Bulk insert or update companies discovered from SEC EDGAR.

    Args:
        records: List of dictionaries with keys: ticker, name, cik, and optional sector, industry, is_active.
        batch_size: Number of records per executemany chunk.

    Returns:
        int: Total number of company records processed.
    """
    if not records:
        return 0

    sql = """
        INSERT INTO companies (ticker, name, cik, sector, industry, is_active, updated_at)
        VALUES (%(ticker)s, %(name)s, %(cik)s, %(sector)s, %(industry)s, %(is_active)s, NOW())
        ON CONFLICT (ticker) DO UPDATE SET
            name = EXCLUDED.name,
            cik = EXCLUDED.cik,
            sector = COALESCE(EXCLUDED.sector, companies.sector),
            industry = COALESCE(EXCLUDED.industry, companies.industry),
            is_active = EXCLUDED.is_active,
            updated_at = NOW();
    """

    sanitized_records = [
        {
            "ticker": r["ticker"].upper().strip(),
            "name": r["name"].strip(),
            "cik": str(r["cik"]).zfill(10),
            "sector": r.get("sector"),
            "industry": r.get("industry"),
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
                logger.debug("Upserted %d / %d companies...", total_inserted, len(sanitized_records))

    logger.info("Successfully upserted %d companies into PostgreSQL.", total_inserted)
    return total_inserted


def upsert_fundamentals(records: list[dict[str, Any]], batch_size: int = 500) -> int:
    """
    Bulk upsert fundamental financial data using executemany with conflict resolution.

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
            ev_to_ebitda,
            roe,
            debt_to_equity,
            profit_margin,
            revenue_growth,
            current_price,
            volume,
            raw_payload,
            updated_at
        ) VALUES (
            %(ticker)s,
            %(fiscal_date)s,
            %(filing_date)s,
            %(market_cap)s,
            %(pe_forward)s,
            %(ev_to_ebitda)s,
            %(roe)s,
            %(debt_to_equity)s,
            %(profit_margin)s,
            %(revenue_growth)s,
            %(current_price)s,
            %(volume)s,
            %(raw_payload)s,
            NOW()
        )
        ON CONFLICT (ticker, fiscal_date) DO UPDATE SET
            filing_date = EXCLUDED.filing_date,
            market_cap = EXCLUDED.market_cap,
            pe_forward = EXCLUDED.pe_forward,
            ev_to_ebitda = EXCLUDED.ev_to_ebitda,
            roe = EXCLUDED.roe,
            debt_to_equity = EXCLUDED.debt_to_equity,
            profit_margin = EXCLUDED.profit_margin,
            revenue_growth = EXCLUDED.revenue_growth,
            current_price = EXCLUDED.current_price,
            volume = EXCLUDED.volume,
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
            "ev_to_ebitda": r.get("ev_to_ebitda"),
            "roe": r.get("roe"),
            "debt_to_equity": r.get("debt_to_equity"),
            "profit_margin": r.get("profit_margin"),
            "revenue_growth": r.get("revenue_growth"),
            "current_price": r.get("current_price"),
            "volume": r.get("volume"),
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
    max_age_days: int = 30,
) -> list[str]:
    """
    Identify tickers that have no cached fundamentals or whose latest record is older than max_age_days.

    Args:
        tickers: Optional list of tickers to filter against. If None, checks all active companies.
        max_age_days: Number of days before cached data is deemed stale.

    Returns:
        list[str]: Sorted list of stale ticker symbols.
    """
    if tickers is not None and len(tickers) == 0:
        return []

    base_query = """
        SELECT c.ticker
        FROM companies c
        LEFT JOIN (
            SELECT ticker, MAX(updated_at) AS latest_update
            FROM fundamentals
            GROUP BY ticker
        ) f ON c.ticker = f.ticker
        WHERE c.is_active = TRUE
          {ticker_filter}
          AND (
              f.latest_update IS NULL
              OR f.latest_update < NOW() - (%(max_age_days)s || ' days')::INTERVAL
          )
        ORDER BY c.ticker ASC;
    """

    params: dict[str, Any] = {"max_age_days": max_age_days}

    if tickers:
        clean_tickers = [t.upper().strip() for t in tickers]
        query = base_query.format(ticker_filter="AND c.ticker = ANY(%(tickers)s)")
        params["tickers"] = clean_tickers
    else:
        query = base_query.format(ticker_filter="")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    stale_list = [row["ticker"] for row in rows]
    logger.info(
        "Identified %d stale tickers (cache threshold: %d days).",
        len(stale_list),
        max_age_days,
    )
    return stale_list


def query_screened_stocks(
    min_market_cap: int = 500_000_000,
    max_pe: float = 30.0,
    min_roe: float = 0.12,
    max_de: float = 180.0,
    min_dollar_volume: float = 2_000_000.0,
    max_ev_ebitda: float = 22.0,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """
    Execute the multi-factor fundamental quantitative screening query in pure SQL.

    Filtering Rules Applied in SQL:
      1. Market Cap >= $500,000,000 (min_market_cap)
      2. Avg Dollar Volume (Current Price * Volume) >= $2,000,000 (min_dollar_volume)
      3. 0 < Forward P/E < 30 (max_pe)
      4. Return on Equity (ROE) > 12% (min_roe)
      5. Debt-to-Equity < 180 (1.8x) (max_de)
      6. EV / EBITDA between 0 and 22 (max_ev_ebitda)

    Factor Scoring Formula:
      Score = (ROE * 40) + (Profit Margin * 30) + (Revenue Growth * 20) - (Forward P/E * 0.5)

    Args:
        min_market_cap: Minimum market capitalization in USD.
        max_pe: Maximum forward P/E ratio ceiling.
        min_roe: Minimum Return on Equity (expressed as decimal, e.g. 0.12 for 12%).
        max_de: Maximum Debt-to-Equity ratio.
        min_dollar_volume: Minimum daily dollar trading volume.
        max_ev_ebitda: Maximum EV/EBITDA ratio ceiling.
        limit: Maximum number of screened stocks to return.

    Returns:
        list[dict[str, Any]]: Ranked list of matching equities ordered by factor score descending.
    """
    # Normalize ROE if supplied as percentage (> 1.0)
    effective_min_roe = min_roe / 100.0 if min_roe > 1.0 else min_roe

    # Normalize Debt/Equity if supplied as decimal (< 5.0)
    effective_max_de = max_de * 100.0 if 0 < max_de < 5.0 else max_de

    sql = """
        WITH latest_fundamentals AS (
            SELECT DISTINCT ON (f.ticker)
                f.id,
                f.ticker,
                f.fiscal_date,
                f.filing_date,
                f.market_cap,
                f.pe_forward,
                f.ev_to_ebitda,
                f.roe,
                f.debt_to_equity,
                f.profit_margin,
                f.revenue_growth,
                f.current_price,
                f.volume,
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
            lf.market_cap,
            lf.pe_forward,
            lf.ev_to_ebitda,
            lf.roe,
            lf.debt_to_equity,
            lf.profit_margin,
            lf.revenue_growth,
            lf.current_price,
            lf.volume,
            lf.dollar_volume,
            lf.fiscal_date,
            ROUND(
                (COALESCE(lf.roe, 0) * 40.0)
                + (COALESCE(lf.profit_margin, 0) * 30.0)
                + (COALESCE(lf.revenue_growth, 0) * 20.0)
                - (COALESCE(lf.pe_forward, 0) * 0.5),
                4
            ) AS factor_score
        FROM latest_fundamentals lf
        JOIN companies c ON c.ticker = lf.ticker
        WHERE c.is_active = TRUE
          AND lf.market_cap >= %(min_market_cap)s
          AND lf.dollar_volume >= %(min_dollar_volume)s
          AND lf.pe_forward > 0 AND lf.pe_forward < %(max_pe)s
          AND lf.roe > %(min_roe)s
          AND lf.debt_to_equity >= 0 AND lf.debt_to_equity < %(max_de)s
          AND lf.ev_to_ebitda > 0 AND lf.ev_to_ebitda <= %(max_ev_ebitda)s
        ORDER BY factor_score DESC
        LIMIT %(limit)s;
    """

    params = {
        "min_market_cap": min_market_cap,
        "min_dollar_volume": min_dollar_volume,
        "max_pe": max_pe,
        "min_roe": effective_min_roe,
        "max_de": effective_max_de,
        "max_ev_ebitda": max_ev_ebitda,
        "limit": limit,
    }

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    logger.info("Screening query returned %d passing equities.", len(rows))
    return list(rows)
