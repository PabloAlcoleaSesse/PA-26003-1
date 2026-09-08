-- ============================================================================
-- Stock Screener Database Schema (PostgreSQL 16+)
-- ============================================================================

-- Table: companies
-- Stores registered equities discovered from the SEC EDGAR system
CREATE TABLE IF NOT EXISTS companies (
    ticker VARCHAR(10) PRIMARY KEY,
    name TEXT NOT NULL,
    cik VARCHAR(10) NOT NULL,
    sector TEXT,
    industry TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Table: fundamentals
-- Stores point-in-time and trailing fundamental/valuation metrics
CREATE TABLE IF NOT EXISTS fundamentals (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
    fiscal_date DATE NOT NULL,
    filing_date DATE,
    market_cap BIGINT,
    pe_forward NUMERIC,
    ev_to_ebitda NUMERIC,
    roe NUMERIC,
    debt_to_equity NUMERIC,
    profit_margin NUMERIC,
    revenue_growth NUMERIC,
    current_price NUMERIC,
    volume BIGINT,
    raw_payload JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fundamentals_ticker_fiscal_date UNIQUE (ticker, fiscal_date)
);

-- ============================================================================
-- Indexes for Performance & Analytical Screening Queries
-- ============================================================================

-- B-Tree index on foreign key ticker for fast joins and lookups
CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker 
    ON fundamentals (ticker);

-- Composite multi-factor index optimizing valuation and quality screening filters
CREATE INDEX IF NOT EXISTS idx_fundamentals_screening 
    ON fundamentals (pe_forward, roe, ev_to_ebitda);

-- Timestamp indexes for cache staleness invalidation queries
CREATE INDEX IF NOT EXISTS idx_companies_updated_at 
    ON companies (updated_at);

CREATE INDEX IF NOT EXISTS idx_fundamentals_updated_at 
    ON fundamentals (updated_at);
