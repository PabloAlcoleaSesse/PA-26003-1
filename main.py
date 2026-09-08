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
        print("[✓] Database schema initialized and ready.")
        return 0
    except Exception as exc:
        logger.error("Failed to initialize database: %s", exc)
        return 1


def cmd_sync_universe(args: argparse.Namespace) -> int:
    """Discover all US common stocks from SEC EDGAR and persist to companies table."""
    logger.info("Beginning SEC EDGAR stock universe synchronization...")
    try:
        stocks = universe.fetch_us_stock_universe()
        if not stocks:
            logger.warning("No equities were retrieved from SEC EDGAR.")
            return 1

        total_saved = db.upsert_companies(stocks)
        print(f"[✓] Universe synchronization complete: {total_saved} companies recorded.")
        return 0
    except Exception as exc:
        logger.error("Failed during universe sync: %s", exc)
        return 1


def cmd_fetch(args: argparse.Namespace) -> int:
    """Fetch fundamentals for stale tickers with rate limiting and concurrency."""
    workers = args.workers or settings.WORKER_CONCURRENCY
    limit = args.limit
    max_age_days = args.max_age_days or settings.DATA_MAX_AGE_DAYS
    tickers = getattr(args, "tickers", None)

    logger.info(
        "Starting fundamentals fetch (workers=%d, limit=%s, max_age_days=%d, tickers=%s)...",
        workers,
        str(limit),
        max_age_days,
        str(tickers) if tickers else "all stale",
    )
    try:
        count = fetcher.fetch_and_persist_stale(
            limit=limit,
            max_workers=workers,
            max_age_days=max_age_days,
            tickers=tickers,
        )
        print(f"[✓] Fundamentals fetch complete: {count} records cached.")
        return 0
    except Exception as exc:
        logger.error("Failed during fundamentals fetch: %s", exc)
        return 1


def cmd_screen(args: argparse.Namespace) -> int:
    """Run quantitative multi-factor screening and display/export results."""
    logger.info("Running quantitative stock screening...")
    try:
        results = screener.run_screen(
            min_market_cap=args.min_market_cap,
            max_pe=args.max_pe,
            min_roe=args.min_roe,
            max_de=args.max_de,
            min_dollar_volume=args.min_dollar_volume,
            max_ev_ebitda=args.max_ev_ebitda,
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
    print("=================================================================")
    print("      STOCK SCREENER & FUNDAMENTAL ANALYSIS PIPELINE            ")
    print("=================================================================\n")

    # Step 1: Initialize Database
    logger.info("[Step 1/4] Applying database schema migrations...")
    try:
        db.init_db()
    except Exception as exc:
        logger.error("Database migration failed: %s", exc)
        return 1

    # Step 2: Sync Universe from SEC
    logger.info("[Step 2/4] Synchronizing SEC EDGAR universe...")
    try:
        stocks = universe.fetch_us_stock_universe()
        db.upsert_companies(stocks)
    except Exception as exc:
        logger.error("Universe sync failed: %s", exc)
        return 1

    # Step 3: Fetch Fundamentals
    workers = args.workers or settings.WORKER_CONCURRENCY
    limit = args.limit
    logger.info("[Step 3/4] Fetching fundamentals (workers=%d, limit=%s)...", workers, str(limit))
    try:
        fetcher.fetch_and_persist_stale(
            limit=limit,
            max_workers=workers,
            max_age_days=settings.DATA_MAX_AGE_DAYS,
        )
    except Exception as exc:
        logger.error("Fundamentals fetch failed: %s", exc)
        return 1

    # Step 4: Run Screen & Scoring
    logger.info("[Step 4/4] Executing multi-factor screen...")
    try:
        screener.run_screen(
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
        description="Production-grade Stock Screening & Fundamental Analysis Pipeline",
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
        help="Fetch US common stock universe from SEC EDGAR and populate database.",
    )
    p_sync.set_defaults(func=cmd_sync_universe)

    # Subcommand: fetch
    p_fetch = subparsers.add_parser(
        "fetch",
        help="Concurrently fetch and cache latest fundamental metrics for stale tickers.",
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
        help="Maximum number of stale tickers to fetch in this run (e.g. 100).",
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
        help="Optional explicit list of tickers to fetch (e.g. --tickers AAPL MSFT NVDA).",
    )
    p_fetch.set_defaults(func=cmd_fetch)

    # Subcommand: screen
    p_screen = subparsers.add_parser(
        "screen",
        help="Screen and rank cached stocks against institutional multi-factor criteria.",
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
    p_screen.set_defaults(func=cmd_screen)

    # Subcommand: run-all
    p_all = subparsers.add_parser(
        "run-all",
        help="Orchestrate end-to-end pipeline: init-db -> sync-universe -> fetch -> screen.",
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
        default=100,
        help="Number of stale tickers to fetch during run-all (default: 100).",
    )
    p_all.add_argument(
        "--top",
        type=int,
        default=25,
        help="Number of top-ranked stocks to output (default: 25).",
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
