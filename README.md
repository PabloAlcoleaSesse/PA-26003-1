# Stock Screening and Outcome Tracking

Python equity screening with PostgreSQL, Yahoo Finance fundamentals and adjusted
daily prices, explicit technical eligibility, and recorded screening runs.
Scores rank candidates; they are not probabilities of profit or validated return forecasts.

## Daily Workflow

```bash
cp .env.example .env
docker compose up -d db
docker compose build app
make run-all
```

`run-all` migrates the database, synchronizes the upcoming universe, refreshes the
entire stale universe, screens it, and writes a CSV plus an immutable run JSON.
`--limit` is an optional fetch cap; it no longer defaults to 50. A capped fetch
does not restrict screening to the fetched tickers.

Direct CLI with dependencies from `requirements.txt` installed:

```bash
python3 main.py run-all --universe upcoming --strategy upcoming_breakouts --max-age-days 0 --top 20
python3 main.py screen --universe upcoming --strategy upcoming_breakouts --include-watchlist
python3 main.py screen --universe upcoming --strategy minervini_trend
```

Existing installations must run `init-db` (or `run-all`) and refresh data before
screening: old snapshots lack the new validity flags and price dates and will be
excluded. `--max-age-days 0` forces refresh. Normal cache age defaults to one day.

## Data and Freshness

- Prices use two years of adjusted OHLCV history. The current New York calendar
  date is always excluded, including after market close. Run the next morning
  to include the preceding session; same-evening runs deliberately lag one session.
- `price_as_of` records the actual completed source session; `updated_at` records
  snapshot fetch time. Missing final prices never inherit a fresh timestamp.
- Prices and fundamentals still refresh together. Separate fetch cadences are a
  future optimization; the old shared 30-day technical cache is no longer the default.
- Screening rejects missing, future, or stale price dates. The default tolerance
  is five calendar days to accommodate weekends/holidays; adjust with
  `--max-price-age-days`. It is a calendar tolerance, not an exchange-calendar guarantee.
- Stale selection uses the preceding weekday and retries invalid technical data;
  exchange holidays and short-history stocks can therefore cause repeat fetches.
- SPY is fetched once per batch. Relative returns are stock return minus SPY return
  over matching 21/63/126-session endpoints. Missing or invalid aligned data yields
  unavailable metrics, not neutral benchmark strength.
- `fiscal_date` is a legacy name for the snapshot observation date, not a fiscal
  period. `filing_date` currently contains Yahoo's most recent quarter date, not
  a verified publication date. These snapshots cannot reconstruct past information availability.

## Eligibility Before Ranking

Fundamental filters select the candidate pool. The whole matching pool is scored;
it is no longer truncated to the largest 200 companies before ranking. EV/EBITDA
limits are enforced, with Financials/Financial Services exempted.

For `upcoming_breakouts`, `minervini_trend`, and `high_growth_momentum`, eligibility
requires valid technical history, confirmed Stage 2, positive three- and six-month
returns, and positive three-month excess return over SPY. The one-month return must
be available but can be flat or negative during consolidation.

`upcoming_breakouts` additionally requires VCP or a confirmed breakout. Existing
strategy weights remain unchanged; eligibility prevents strong fundamentals from
compensating for a failed entry requirement. Thresholds are hypotheses for validation.

| State | Meaning |
| --- | --- |
| Watchlist | No confirmed VCP/breakout, or entry requirements failed; inspect `eligible` and reasons |
| Setup forming | Eligible Stage 2 stock with a qualifying contraction |
| Confirmed breakout | Eligible Stage 2 stock closed above prior resistance with qualifying volume |

By default failed candidates are excluded. `--include-watchlist` appends them after
eligible candidates, within `--top`, and marks them excluded. Non-trend fundamental
strategies and Stage 2-only candidates can be eligible while labeled Watchlist;
eligibility and setup state describe different properties.

Technical analysis requires 252 complete, finite, aligned OHLCV rows. Stage 2 uses
full 50/150/200-session averages and an upward 200-session average. Breakout requires
a close strictly above the prior 25-session high, with at least 1.2 times the prior
50-session average volume (excluding the signal session). VCP requires an uptrend,
contracting ranges and lower recent volume. Accumulation compares up/down volume
within the same last 20 sessions. Missing technical scores remain unavailable;
zero scores are preserved. Recently listed equities with insufficient history are excluded.

Other strategies remain available: `balanced`, `quality_compounders`, `garp`, and
`deep_value`. All strategies require usable, recent prices; trend-specific gates
do not apply to these fundamental strategies.

## Run Records and Outcomes

Each screen creates `runs/<timestamp>_<uuid>.json` (change with `--audit-dir`),
containing configuration, weights, original candidate inputs, rejection reasons,
selected rows, a UTC creation timestamp, and a SHA256 fingerprint of the screening,
fetching, pattern, and database source files. The audit covers the pool *after*
fundamental SQL filters; it does not record stocks rejected by those filters.
CSV includes setup state, eligibility, reasons, source date, and absolute/relative returns.
An empty screen writes a header-only CSV, replacing any previous result rows.
Every CSV export also writes a separate `<name>.watchlist.csv` with the top rejected
candidates and their reasons (or just its header when none were rejected). When
there are no eligible entries, the terminal shows this research watchlist and a
summary of failed checks instead of an empty table. Watchlist rows remain ineligible;
they are not silently added to the run's selected entries or evaluated as trades.
`--include-watchlist` still explicitly includes these rows in the main output.

Measure a saved run later:

```bash
python3 main.py evaluate --run runs/RUN_FILE.json --output-csv outcomes.csv --cost-bps 10
```

The evaluator measures 5/20/60-session outcomes from the first session open after
the run's New York creation date. It uses adjusted prices, SPY-defined sessions,
and completed daily closes. Outputs include gross and net return, benchmark net
return, excess return, and maximum close-to-close drawdown including entry.
It applies the configurable round-trip cost equally to the stock and benchmark;
excess return therefore equals the gross return difference. Drawdown is gross,
not intraday. Watchlist selections retain their `eligible` flag in the outcome CSV.

Unelapsed horizons are pending; missing benchmark/stock data is explicitly marked
unavailable. Delistings and missing data must be investigated rather than dropped
from aggregate performance. This is forward observation tracking, not a portfolio
simulator or a historical backtest. No automatic buys, sells, or scheduling are added.

Before changing weights, compare saved versions on unseen periods with realistic
costs and historical universe membership. A historical fundamental backtest needs
point-in-time fundamentals and actual publication timestamps, which this data
source and current snapshots do not supply.

## Project and Verification

`main.py` owns CLI orchestration; `universe.py` discovers tickers; `fetcher.py`
retrieves snapshots; `patterns.py` computes signals; `db.py` and `schema.sql`
persist/query them; `screener.py` handles eligibility/ranking/audits; `outcomes.py`
measures subsequent observations. `config.py` loads environment settings.

```bash
make test
```

Tests use deterministic price fixtures and mocked providers/database calls. They
cover flat/downward prices, true/false breakouts, missing history, volume windows,
aligned benchmark returns, stale records, full-pool ranking, run records, CLI
forwarding, and forward outcome timing. They establish calculation behavior, not
investment performance.
