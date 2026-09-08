-- ============================================================================
-- Stock Screener Database Schema (PostgreSQL 16+)
-- ============================================================================

-- Table: companies
-- Stores registered equities discovered from benchmark indices or SEC EDGAR
CREATE TABLE IF NOT EXISTS companies (
    ticker VARCHAR(10) PRIMARY KEY,
    name TEXT NOT NULL,
    cik VARCHAR(10) NOT NULL,
    sector TEXT,
    industry TEXT,
    exchange TEXT,
    universe VARCHAR(50) NOT NULL DEFAULT 'SEC',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Table: fundamentals
-- Stores point-in-time and trailing fundamental, quality, valuation, and momentum metrics
CREATE TABLE IF NOT EXISTS fundamentals (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL REFERENCES companies(ticker) ON DELETE CASCADE,
    fiscal_date DATE NOT NULL,
    filing_date DATE,
    market_cap BIGINT,
    pe_forward NUMERIC,
    trailing_pe NUMERIC,
    peg_ratio NUMERIC,
    price_to_book NUMERIC,
    ev_to_ebitda NUMERIC,
    roe NUMERIC,
    return_on_assets NUMERIC,
    debt_to_equity NUMERIC,
    profit_margin NUMERIC,
    operating_margin NUMERIC,
    gross_margin NUMERIC,
    revenue_growth NUMERIC,
    free_cash_flow BIGINT,
    current_price NUMERIC,
    volume BIGINT,
    price_return_6m NUMERIC,
    raw_payload JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fundamentals_ticker_fiscal_date UNIQUE (ticker, fiscal_date)
);

-- Migration guard for existing tables
ALTER TABLE companies ADD COLUMN IF NOT EXISTS exchange TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS universe VARCHAR(50) DEFAULT 'SEC';
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS trailing_pe NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS peg_ratio NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS price_to_book NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS return_on_assets NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS operating_margin NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS gross_margin NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS free_cash_flow BIGINT;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS price_return_6m NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS price_as_of DATE;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS technical_valid BOOLEAN;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS is_stage_2 BOOLEAN;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS stage_2_rules_passed SMALLINT;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS is_vcp BOOLEAN;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS is_breakout BOOLEAN;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS price_return_1m NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS price_return_3m NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS relative_return_1m NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS relative_return_3m NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS relative_return_6m NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS sma_50 NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS sma_200 NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS pattern_score NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS detected_patterns TEXT;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS dist_52w_high NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS rsi_14 NUMERIC;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS ud_volume_ratio NUMERIC;
COMMENT ON COLUMN fundamentals.fiscal_date IS 'Snapshot observation date; legacy name, not a fiscal period';
COMMENT ON COLUMN fundamentals.price_as_of IS 'Last completed source price session';
COMMENT ON COLUMN fundamentals.updated_at IS 'Fundamentals and technical snapshot fetch timestamp';

-- ============================================================================
-- Indexes for Performance & Analytical Screening Queries
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker 
    ON fundamentals (ticker);

CREATE INDEX IF NOT EXISTS idx_fundamentals_screening 
    ON fundamentals (pe_forward, roe, ev_to_ebitda);

CREATE INDEX IF NOT EXISTS idx_companies_universe
    ON companies (universe);

CREATE INDEX IF NOT EXISTS idx_companies_sector
    ON companies (sector);

CREATE INDEX IF NOT EXISTS idx_companies_updated_at 
    ON companies (updated_at);

CREATE INDEX IF NOT EXISTS idx_fundamentals_updated_at 
    ON fundamentals (updated_at);
