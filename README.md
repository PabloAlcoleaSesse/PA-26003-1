# Institutional Stock Screener & Fundamental Analysis Pipeline (Docker Edition)

A production-ready quantitative equity screening and fundamental analysis pipeline in Python (3.11+) backed by PostgreSQL 16, fully containerized with Docker & Docker Compose.

The engine discovers benchmark US equities (**S&P 500**, **Nasdaq 100**, **Dow 30**, or sanitized **SEC EDGAR**), extracts granular fundamental and valuation metrics with client-side rate limiting and thread concurrency, persists data into PostgreSQL, and scores companies using an institutional **4-pillar multi-factor model** with curated investment strategy presets.

---

## Architecture & Project Structure

```text
stock_screener/
├── docker-compose.yml   # Multi-container orchestration (PostgreSQL 16 & App runner)
├── Dockerfile           # Production Python 3.12 container definition
├── Makefile             # Convenient command shortcuts for Docker workflows
├── requirements.txt     # Production dependencies (psycopg, rich, yfinance, pydantic)
├── .env.example         # Environment template with SEC fair-access User-Agent
├── schema.sql           # Database DDL: companies, fundamentals, indexes & migrations
├── config.py            # Type-safe configuration via Pydantic Settings
├── db.py                # Connection pool, migrations, profile updates, SQL queries
├── universe.py          # Benchmark universes (Upcoming Leaders, S&P 400 MidCap, S&P 500, Nasdaq 100, Dow 30, SEC)
├── patterns.py          # Technical chart pattern recognition (Minervini Stage 2, VCP, Breakout, Accumulation)
├── fetcher.py           # yfinance fundamentals extraction, 1Y OHLCV, 6M momentum & pattern analysis
├── screener.py          # Multi-factor quantitative engine, strategy presets & rich CLI badges
└── main.py              # Unified CLI entrypoint orchestrating pipeline subcommands
```

---

## Technical Pattern Recognition Engine

In addition to fundamental pillars, the pipeline integrates quantitative price-action and institutional volume analysis ([patterns.py](patterns.py)) on 1-year daily OHLCV data:

1. **Mark Minervini Stage 2 Trend Template (U.S. Investing Championship setup):**
   * Current Price > 50-day SMA > 150-day SMA > 200-day SMA.
   * 200-day moving average sloping upwards over the past 20 trading days.
   * Within 22% of 52-week high and $\ge 20\%$ off 52-week low.
2. **Volatility Contraction Pattern (VCP):**
   * Coiling price ranges with diminishing amplitude near 52-week highs, indicating institutional supply absorption.
3. **Consolidation Resistance Breakout:**
   * Price eclipsing 20-to-50-day resistance on $\ge 1.20\times$ 50-day average trading volume.
4. **Institutional Accumulation / Distribution (Up/Down Volume Ratio):**
   * Ratio of volume on up-days vs. down-days over the past 20 sessions $\ge 1.20\times$, signaling institutional buying.
5. **RSI(14) Momentum Sweet Spot:**
   * 14-day Relative Strength Index between 50 and 72 (strong bullish momentum without being overextended).

---

## Institutional Multi-Factor Quantitative Model

Instead of simplistic unscaled linear additions that reward value traps and contracting companies, the engine groups financial metrics into **normalized pillars (0 to 100 scale)**:

1. **Quality Pillar (Q):**
   * Return on Equity (ROE $> 15\%$)
   * Operating Margin & Net Profit Margin
   * Balance Sheet Safety (Debt-to-Equity $< 100$)
2. **Growth Pillar (G):**
   * YoY Revenue Growth
   * *Anti-Trap Guardrail:* Hard disqualifier / heavy penalty on contracting top-lines ($< 0\%$).
3. **Valuation Pillar (V):**
   * Forward P/E (curves 8x–22x highest; penalizes hyper-expensive multiples while avoiding distressed value traps)
   * PEG Ratio (Peter Lynch metric: PEG $< 1.5$ rewarded)
   * EV / EBITDA multiple (automatically adapted/neutralized for Financials)
4. **Momentum & Cash Flow (M):**
   * 6-Month Relative Price Momentum (prevents catching "falling knives")
   * Free Cash Flow (FCF) generation
5. **Technical Pattern Score (P):**
   * 0 to 100 score synthesized from Stage 2 trends, VCP coiling, breakouts, and accumulation.

### Curated Strategy Presets

* `upcoming_breakouts` (Emerging High-Growth Leaders): 35% Pattern, 30% Growth, 20% Quality, 15% Valuation. Filters for $1B–$80B mid-caps with accelerating growth ($\ge 12\%$) and confirmed technical setups.
* `minervini_trend` (Trend Template & VCP): 45% Pattern, 25% Growth, 15% Quality, 15% Valuation. Demands high pattern scores ($\ge 65.0$) and upward-trending moving averages.
* `balanced` (Default): 30% Quality, 25% Growth, 25% Valuation, 20% Momentum.
* `quality_compounders` (Buffett / Terry Smith style): 50% Quality, 20% Growth/FCF, 15% Valuation, 15% Momentum.
* `garp` (Peter Lynch Growth At A Reasonable Price): 40% Growth, 30% Valuation/PEG, 20% Quality, 10% Momentum.
* `deep_value`: 50% Valuation, 25% Quality, 15% Momentum, 10% Growth.
* `high_growth_momentum`: 45% Growth, 35% Momentum, 15% Quality, 5% Valuation.

---

## Running with Docker (Recommended)

### 1. Configure Environment
```bash
cp .env.example .env
```

### 2. Run with Docker Compose (Single Command)
```bash
# Build images and run end-to-end workflow (S&P 500 benchmark)
docker compose up --build
```

---

## Step-by-Step Execution via Docker / Make

```bash
# Start PostgreSQL in background
make up

# 1. Apply schema DDL migrations
make init-db

# 2. Discover benchmark universe (S&P 500 with GICS Sector classifications)
make sync-universe

# 3. Fetch fundamental & valuation metrics for stale tickers concurrently
make fetch

# 4. Run quantitative multi-factor screen (Top 20 ranked equities)
make screen

# 5. Run end-to-end orchestrated workflow
make run-all
```

### Direct CLI Examples:
```bash
# Screen upcoming high-growth compounders with technical pattern setups
python3 main.py screen --strategy upcoming_breakouts --top 15

# Screen Minervini Stage 2 Trend Template & VCP setups
python3 main.py screen --strategy minervini_trend --top 15

# Screen traditional value/growth strategies
python3 main.py screen --strategy quality_compounders --top 15
python3 main.py screen --strategy garp --top 15

# Screen by Economic Sector
python3 main.py screen --sector Technology --top 10
python3 main.py screen --sector Healthcare --top 10

# End-to-end pipeline run (Upcoming universe)
python3 main.py run-all --universe upcoming --strategy upcoming_breakouts --workers 8 --limit 50 --top 20
```

---

## Outputs & Persistence

1. **PostgreSQL Database:** Stored in persistent Docker volume (`pgdata`) or local Postgres instance.
2. **Terminal Presentation:** Rich color-coded CLI table displaying Rank, Ticker, Company, Sector, Valuation, Quality, Growth, 6M Momentum, and Composite Pillar breakdown (`Q / G / V / M`).
3. **CSV Report:** Exported to `./screened_results.csv` with full numerical metrics.

## Governance

This project includes the following public-project governance documents:

- [License (MIT)](LICENSE)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Contributing Guide](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)

Community templates are available under [`.github/`](.github/) for issues and pull requests.

---

## Disclaimer

This software is for educational and research purposes only and does not
constitute financial advice.
