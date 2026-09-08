# Institutional Stock Screener & Fundamental Analysis Pipeline (Docker Edition)

A production-ready quantitative stock screening and fundamental analysis pipeline in Python (3.11+) backed by PostgreSQL 16, fully containerized with Docker & Docker Compose.

The engine discovers US equities directly from the Securities and Exchange Commission (SEC) EDGAR system, extracts fundamental and valuation metrics with client-side rate limiting and thread concurrency, persists data into PostgreSQL with conflict resolution, and scores companies using an institutional multi-factor financial model.

---

## Architecture & Project Structure

```text
stock_screener/
├── docker-compose.yml   # Multi-container orchestration (PostgreSQL 16 & App runner)
├── Dockerfile           # Production Python 3.12 container definition
├── Makefile             # Convenient command shortcuts for Docker workflows
├── requirements.txt     # Locked production dependencies
├── .env.example         # Environment template with SEC fair-access User-Agent
├── schema.sql           # Database DDL: companies, fundamentals, indexes & constraints
├── config.py            # Type-safe configuration via Pydantic Settings
├── db.py                # PostgreSQL connection pooling, migrations, batch upserts, SQL screen
├── universe.py          # SEC EDGAR common stock universe extraction & normalization
├── fetcher.py           # yfinance fundamentals extraction, rate limiting & ThreadPoolExecutor
├── screener.py          # Multi-factor quantitative screener, console table & CSV export
└── main.py              # Unified CLI entrypoint orchestrating pipeline subcommands
```

---

## Quantitative Multi-Factor Screening Model

### 1. Hard Exclusion Filters
Equities must strictly satisfy all institutional baseline criteria to pass the screen:
* **Market Capitalization:** $\ge \$500\text{M}$
* **30-Day Avg Dollar Volume ($P \times V$):** $\ge \$2,000,000$ (liquidity filter)
* **Valuation Ceiling:** $0 < \text{Forward P/E} < 30$
* **Profitability / Quality:** $\text{Return on Equity (ROE)} > 12\%$
* **Solvency / Leverage:** $\text{Debt-to-Equity} < 180$ ($1.8\times$)
* **Enterprise Multiple:** $0 < \text{EV / EBITDA} \le 22$

### 2. Factor Scoring Model
Passing equities are ranked by an institutional multi-factor composite score:

$$\text{Score} = (\text{ROE} \times 40) + (\text{Profit Margin} \times 30) + (\text{Revenue Growth} \times 20) - (\text{Forward P/E} \times 0.5)$$

* **Quality / Profitability:** Heavy weighting on ROE ($40\times$) and Net Margin ($30\times$).
* **Growth:** Rewarded via YoY Revenue Growth ($20\times$).
* **Valuation Penalty:** Discourages overvalued multiples via Forward P/E ($0.5\times$).

---

## Running with Docker (Recommended)

### 1. Configure Environment
Copy the default environment configuration:
```bash
cp .env.example .env
```

### 2. Run with Docker Compose (Single Command)
Execute the complete end-to-end pipeline (database startup -> migrations -> SEC universe sync -> data fetch -> multi-factor screen):
```bash
# Build images and run end-to-end workflow
docker compose up --build
```
The results table will print to the console, and `screened_results.csv` will be written directly to your host directory.

---

## Step-by-Step Execution via Docker

You can run individual pipeline stages via `docker compose run` or the provided `Makefile`:

### Using `docker compose`:
```bash
# Start PostgreSQL in background
docker compose up -d db

# 1. Apply schema DDL migrations
docker compose run --rm app init-db

# 2. Discover US common stock universe from SEC EDGAR (~9,800 clean stocks)
docker compose run --rm app sync-universe

# 3. Fetch fundamental & valuation metrics for stale tickers concurrently
docker compose run --rm app fetch --workers 8 --limit 100

# 4. Run quantitative multi-factor screen and export CSV
docker compose run --rm app screen --top 25

# 5. Run end-to-end orchestrated workflow
docker compose run --rm app run-all --workers 8 --limit 100 --top 25
```

### Using `make` shortcuts:
```bash
make up             # Spin up database
make init-db        # Run migrations
make sync-universe  # Sync SEC universe
make fetch          # Fetch fundamentals
make screen         # Screen and score equities
make run-all        # Run entire pipeline
make down           # Stop containers
make clean          # Stop containers and reset database volume
```

---

## Outputs & Persistence

1. **PostgreSQL Database:** Stored in a persistent Docker volume (`pgdata`), surviving container restarts.
2. **CSV Report:** Exported to `./screened_results.csv` on the host machine via mounted volume.
3. **Console Presentation:** Clean ASCII table with ranking, valuation multiples, quality ratios, dollar volume, and composite factor scores.
