"""
Main Command-Line Interface (CLI) for the Stock Screener Pipeline.

Provides subcommands to:
  - init-db: Execute database DDL migrations.
  - sync-universe: Discover and synchronize US equity universe from SEC EDGAR.
  - fetch: Concurrently pull fundamental and valuation metrics for stale tickers.
  - screen: Execute multi-factor screening and output ranked tables and CSV report.
  - run-all: Execute end-to-end pipeline (init -> sync -> fetch -> screen).
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

import db
import fetcher
import screener
import universe
from config import settings

logger = logging.getLogger("stock_screener")


def setup_logging(verbose: bool = False) -> None:
    """Configure structured logging output."""
    level = logging.DEBUG if verbose else logging.INFO
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Silence overly verbose external loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)


def cmd_init_db(args: argparse.Namespace) -> int:
    """Execute database migrations from schema.sql."""
    logger.info("Initializing database schema...")
    try:
        db.init_db(schema_path=getattr(args, "schema", None))
        print("[✓] Database schema initialized and migrated successfully.")
        return 0
    except Exception as exc:
        logger.error("Failed to initialize database: %s", exc)
        return 1


def cmd_sync_universe(args: argparse.Namespace) -> int:
    """Discover benchmark or SEC universe and persist to companies table."""
    source = getattr(args, "source", "sp500")
    logger.info("Synchronizing '%s' equity universe...", source)
    try:
        stocks = universe.fetch_universe(source=source)
        if not stocks:
            logger.warning("No equities were retrieved for source '%s'.", source)
            return 1

        total_saved = db.upsert_companies(stocks)
        print(f"[✓] Universe synchronization complete: {total_saved} companies recorded for '{source.upper()}'.")
        return 0
    except Exception as exc:
        logger.error("Failed during universe sync: %s", exc)
        return 1


def cmd_fetch(args: argparse.Namespace) -> int:
    """Fetch fundamentals for stale tickers with rate limiting and concurrency."""
    workers = args.workers or settings.WORKER_CONCURRENCY
    limit = args.limit
    max_age_days = args.max_age_days if args.max_age_days is not None else settings.DATA_MAX_AGE_DAYS
    raw_tickers = getattr(args, "tickers", None)
    tickers = None
    if raw_tickers:
        tickers = []
        for t in raw_tickers:
            for sub_t in t.split(","):
                clean_t = sub_t.strip().upper()
                if clean_t:
                    tickers.append(clean_t)
    uni = getattr(args, "universe", None)

    logger.info(
        "Starting fundamentals fetch (workers=%d, limit=%s, max_age_days=%d, universe=%s, tickers=%s)...",
        workers,
        str(limit),
        max_age_days,
        uni or "all",
        str(tickers) if tickers else "stale queue",
    )
    try:
        count = fetcher.fetch_and_persist_stale(
            limit=limit,
            max_workers=workers,
            max_age_days=max_age_days,
            tickers=tickers,
            universe=uni,
            show_progress=True,
        )
        print(f"[✓] Fundamentals fetch complete: {count} records cached.")
        return 0
    except Exception as exc:
        logger.error("Failed during fundamentals fetch: %s", exc)
        return 1


def cmd_screen(args: argparse.Namespace) -> int:
    """Run quantitative multi-factor screening and display/export results."""
    logger.info(
        "Running quantitative stock screening (strategy: %s, universe: %s, sector: %s)...",
        args.strategy,
        args.universe or "all",
        args.sector or "all",
    )
    try:
        results = screener.run_screen(
            strategy=args.strategy,
            universe=args.universe,
            sector=args.sector,
            min_market_cap=args.min_market_cap,
            max_market_cap=getattr(args, "max_market_cap", None),
            max_pe=args.max_pe,
            min_roe=args.min_roe,
            max_de=args.max_de,
            min_dollar_volume=args.min_dollar_volume,
            max_ev_ebitda=args.max_ev_ebitda,
            min_growth=args.min_growth,
            min_margin=args.min_margin,
            limit=args.top,
            output_csv=args.output_csv,
            print_table=True,
        )
        return 0
    except Exception as exc:
        logger.error("Failed during screening: %s", exc)
        return 1


def cmd_run_all(args: argparse.Namespace) -> int:
    """Orchestrate the end-to-end screening pipeline."""
    uni = getattr(args, "universe", "upcoming")
    strat = getattr(args, "strategy", "upcoming_breakouts")
    limit = args.limit
    workers = args.workers or settings.WORKER_CONCURRENCY

    print("=================================================================")
    print("      STOCK SCREENER & FUNDAMENTAL ANALYSIS PIPELINE            ")
    print(f"      Universe: {uni.upper()}  •  Strategy: {strat.upper()}     ")
    print("=================================================================\n")

    # Step 1: Initialize Database
    logger.info("[Step 1/4] Applying database schema migrations...")
    try:
        db.init_db()
    except Exception as exc:
        logger.error("Database migration failed: %s", exc)
        return 1

    # Step 2: Sync Universe
    logger.info("[Step 2/4] Synchronizing benchmark universe '%s'...", uni)
    try:
        stocks = universe.fetch_universe(source=uni)
        db.upsert_companies(stocks)
    except Exception as exc:
        logger.error("Universe sync failed: %s", exc)
        return 1

    # Step 3: Fetch Fundamentals
    logger.info("[Step 3/4] Fetching fundamentals (workers=%d, limit=%s)...", workers, str(limit))
    try:
        fetcher.fetch_and_persist_stale(
            limit=limit,
            max_workers=workers,
            max_age_days=settings.DATA_MAX_AGE_DAYS,
            universe=uni,
            show_progress=True,
        )
    except Exception as exc:
        logger.error("Fundamentals fetch failed: %s", exc)
        return 1

    # Step 4: Run Screen & Scoring
    logger.info("[Step 4/4] Executing '%s' multi-factor screen...", strat)
    try:
        screener.run_screen(
            strategy=strat,
            universe=uni,
            sector=getattr(args, "sector", None),
            max_market_cap=getattr(args, "max_market_cap", None),
            limit=args.top,
            output_csv=args.output_csv,
            print_table=True,
        )
    except Exception as exc:
        logger.error("Screen execution failed: %s", exc)
        return 1

    print("[✓] Pipeline execution finished successfully.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build and configure the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="stock_screener",
        description="Production-grade Institutional Stock Screening & Pattern Recognition Pipeline",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed debug logging output.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        help="Subcommand to execute.",
    )

    # Subcommand: init-db
    p_init = subparsers.add_parser(
        "init-db",
        help="Initialize or migrate PostgreSQL database schema.",
    )
    p_init.add_argument(
        "--schema",
        type=str,
        default=None,
        help="Path to SQL schema definition file (defaults to schema.sql).",
    )
    p_init.set_defaults(func=cmd_init_db)

    # Subcommand: sync-universe
    p_sync = subparsers.add_parser(
        "sync-universe",
        help="Fetch benchmark or SEC universe and populate database.",
    )
    p_sync.add_argument(
        "--source",
        type=str,
        choices=["sp500", "sp400", "upcoming", "nasdaq100", "dow30", "sec", "all"],
        default="upcoming",
        help="Benchmark universe source (default: upcoming).",
    )
    p_sync.set_defaults(func=cmd_sync_universe)

    # Subcommand: fetch
    p_fetch = subparsers.add_parser(
        "fetch",
        help="Concurrently fetch and cache latest fundamental metrics for stale tickers.",
    )
    p_fetch.add_argument(
        "--universe",
        type=str,
        default=None,
        help="Optional universe filter (e.g. upcoming, sp400, sp500, nasdaq100).",
    )
    p_fetch.add_argument(
        "--workers",
        type=int,
        default=settings.WORKER_CONCURRENCY,
        help=f"Number of parallel worker threads (default: {settings.WORKER_CONCURRENCY}).",
    )
    p_fetch.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of stale tickers to fetch in this run (e.g. 50).",
    )
    p_fetch.add_argument(
        "--max-age-days",
        type=int,
        default=settings.DATA_MAX_AGE_DAYS,
        help=f"Max age in days before cached data is stale (default: {settings.DATA_MAX_AGE_DAYS}).",
    )
    p_fetch.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Optional explicit list of tickers to fetch (e.g. --tickers APP PLTR CRWD).",
    )
    p_fetch.set_defaults(func=cmd_fetch)

    # Subcommand: screen
    p_screen = subparsers.add_parser(
        "screen",
        help="Screen and rank stocks using 4-pillar model & technical pattern recognition.",
    )
    p_screen.add_argument(
        "--strategy",
        type=str,
        choices=[
            "upcoming_breakouts",
            "minervini_trend",
            "balanced",
            "quality_compounders",
            "garp",
            "deep_value",
            "high_growth_momentum",
        ],
        default="upcoming_breakouts",
        help="Quantitative investment strategy preset (default: upcoming_breakouts).",
    )
    p_screen.add_argument(
        "--universe",
        type=str,
        default=None,
        help="Filter by benchmark universe (e.g. upcoming, sp400, sp500, nasdaq100).",
    )
    p_screen.add_argument(
        "--sector",
        type=str,
        default=None,
        help="Filter by economic sector (e.g. Technology, Healthcare, Financials).",
    )
    p_screen.add_argument(
        "--top",
        type=int,
        default=25,
        help="Number of top-ranked stocks to display (default: 25).",
    )
    p_screen.add_argument(
        "--output-csv",
        type=str,
        default="screened_results.csv",
        help="Path for exported CSV report (default: screened_results.csv).",
    )
    p_screen.add_argument(
        "--min-market-cap",
        type=int,
        default=screener.DEFAULT_MIN_MARKET_CAP,
        help=f"Minimum Market Cap in USD (default: {screener.DEFAULT_MIN_MARKET_CAP}).",
    )
    p_screen.add_argument(
        "--max-market-cap",
        type=int,
        default=None,
        help="Maximum Market Cap in USD (e.g. 35000000000 for mid-caps).",
    )
    p_screen.add_argument(
        "--max-pe",
        type=float,
        default=screener.DEFAULT_MAX_PE,
        help=f"Maximum Forward P/E ratio (default: {screener.DEFAULT_MAX_PE}).",
    )
    p_screen.add_argument(
        "--min-roe",
        type=float,
        default=screener.DEFAULT_MIN_ROE,
        help=f"Minimum Return on Equity (default: {screener.DEFAULT_MIN_ROE}).",
    )
    p_screen.add_argument(
        "--max-de",
        type=float,
        default=screener.DEFAULT_MAX_DE,
        help=f"Maximum Debt-to-Equity ratio (default: {screener.DEFAULT_MAX_DE}).",
    )
    p_screen.add_argument(
        "--min-dollar-volume",
        type=float,
        default=screener.DEFAULT_MIN_DOLLAR_VOLUME,
        help=f"Minimum 30-day Avg Dollar Volume in USD (default: {screener.DEFAULT_MIN_DOLLAR_VOLUME}).",
    )
    p_screen.add_argument(
        "--max-ev-ebitda",
        type=float,
        default=screener.DEFAULT_MAX_EV_EBITDA,
        help=f"Maximum EV/EBITDA multiple (default: {screener.DEFAULT_MAX_EV_EBITDA}).",
    )
    p_screen.add_argument(
        "--min-growth",
        type=float,
        default=0.0,
        help="Minimum YoY revenue growth rate (default: 0.0, discards contracting businesses).",
    )
    p_screen.add_argument(
        "--min-margin",
        type=float,
        default=0.03,
        help="Minimum operating/net margin (default: 0.03).",
    )
    p_screen.set_defaults(func=cmd_screen)

    # Subcommand: run-all
    p_all = subparsers.add_parser(
        "run-all",
        help="Orchestrate end-to-end pipeline: init-db -> sync-universe -> fetch -> screen.",
    )
    p_all.add_argument(
        "--universe",
        type=str,
        choices=["sp500", "nasdaq100", "dow30", "sec", "all"],
        default="sp500",
        help="Target equity universe (default: sp500).",
    )
    p_all.add_argument(
        "--strategy",
        type=str,
        choices=["balanced", "quality_compounders", "garp", "deep_value", "high_growth_momentum"],
        default="balanced",
        help="Quantitative investment strategy preset (default: balanced).",
    )
    p_all.add_argument(
        "--sector",
        type=str,
        default=None,
        help="Optional sector filter (e.g. Technology).",
    )
    p_all.add_argument(
        "--workers",
        type=int,
        default=settings.WORKER_CONCURRENCY,
        help=f"Number of parallel worker threads (default: {settings.WORKER_CONCURRENCY}).",
    )
    p_all.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Number of stale tickers to fetch during run-all (default: 50).",
    )
    p_all.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top-ranked stocks to output (default: 20).",
    )
    p_all.add_argument(
        "--output-csv",
        type=str,
        default="screened_results.csv",
        help="Path for exported CSV report (default: screened_results.csv).",
    )
    p_all.set_defaults(func=cmd_run_all)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Main application entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(verbose=args.verbose)

    exit_code = 1
    try:
        exit_code = args.func(args)
    except KeyboardInterrupt:
        print("\n[!] Execution interrupted by user.")
        exit_code = 130
    except Exception as exc:
        logger.exception("Unexpected execution failure: %s", exc)
        exit_code = 1
    finally:
        db.close_pool()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

