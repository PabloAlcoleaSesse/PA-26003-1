"""Forward outcome measurements for recorded screens, not a historical backtest."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

HORIZONS = (5, 20, 60)
FIELDS = ["run_id", "ticker", "eligible", "setup_status", "horizon_sessions", "status",
          "entry_date", "exit_date", "gross_return", "net_return",
          "benchmark_return", "excess_return", "max_drawdown", "cost_bps"]


def measure_outcomes(stock: pd.DataFrame, benchmark: pd.DataFrame, signal_date: date,
                     *, cost_bps: float = 10.0) -> list[dict]:
    """Enter at the next session open; measure completed common session closes.

    SPY defines the session calendar. Missing stock bars make the observation
    unavailable rather than silently shortening the requested horizon.
    """
    if not np.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("cost_bps must be a finite non-negative number")

    def normalize(frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty or not {"Open", "Close"}.issubset(frame.columns):
            return pd.DataFrame(columns=["Open", "Close"])
        frame = frame[["Open", "Close"]].copy()
        frame.index = pd.Index(pd.to_datetime(frame.index).date)
        frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
        return frame.loc[frame.index > signal_date]

    stock, benchmark = normalize(stock), normalize(benchmark)
    results = []
    for horizon in HORIZONS:
        row = {"horizon_sessions": horizon, "status": "pending", "cost_bps": cost_bps}
        if benchmark.empty:
            row["status"] = "benchmark_unavailable"
        elif len(benchmark) >= horizon:
            sessions = benchmark.index[:horizon]
            bars = stock.reindex(sessions)
            ref = benchmark.loc[sessions]
            values = np.concatenate((bars.to_numpy(dtype=float).ravel(), ref.to_numpy(dtype=float).ravel()))
            if not np.isfinite(values).all() or (values <= 0).any():
                row["status"] = "price_data_unavailable"
            else:
                entry = float(bars.iloc[0]["Open"])
                gross = float(bars.iloc[-1]["Close"]) / entry - 1
                benchmark_return = float(ref.iloc[-1]["Close"]) / float(ref.iloc[0]["Open"]) - 1
                wealth = np.r_[1.0, bars["Close"].to_numpy(dtype=float) / entry]
                drawdown = wealth / np.maximum.accumulate(wealth) - 1
                row.update(status="complete", entry_date=sessions[0], exit_date=sessions[-1],
                           gross_return=gross, net_return=gross - cost_bps / 10_000,
                           benchmark_return=benchmark_return - cost_bps / 10_000,
                           excess_return=gross - benchmark_return,
                           max_drawdown=float(drawdown.min()))
        results.append(row)
    return results


def evaluate_run(run_path: str | Path, output_csv: str | Path, *, cost_bps: float = 10.0) -> list[dict]:
    """Download adjusted prices to measure an immutable run's forward outcomes."""
    if not np.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("cost_bps must be a finite non-negative number")
    run = json.loads(Path(run_path).read_text(encoding="utf-8"))
    created_at = datetime.fromisoformat(run["created_at"])
    if created_at.tzinfo is None:
        raise ValueError("Run created_at must include a timezone")
    signal_date = created_at.astimezone(ZoneInfo("America/New_York")).date()
    # Exclude today's session even if run during trading or before provider finalization.
    today = datetime.now(ZoneInfo("America/New_York")).date()
    start = signal_date + timedelta(days=1)

    def history(ticker: str) -> pd.DataFrame:
        if start >= today:
            return pd.DataFrame()
        try:
            frame = yf.Ticker(ticker).history(start=start.isoformat(), end=today.isoformat(), auto_adjust=True)
            if not frame.empty:
                frame = frame.loc[pd.to_datetime(frame.index).date < today]
            return frame
        except Exception:
            return pd.DataFrame()

    selections = run["selected"]
    benchmark = history("SPY") if selections else pd.DataFrame()
    rows = []
    for selection in selections:
        ticker = selection["ticker"]
        observations = measure_outcomes(history(ticker), benchmark, signal_date, cost_bps=cost_bps)
        for observation in observations:
            if start >= today:
                observation["status"] = "pending"
            rows.append(dict(run_id=run["run_id"], ticker=ticker,
                             eligible=selection.get("eligible", ""),
                             setup_status=selection.get("setup_status", ""), **observation))

    path = Path(output_csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows
