-- v3 `quant.db` 호환 스키마 — 우리가 채우는 9표만 (플랜 `2026-09-24-v3-merge.md` §5 T1.1).
--
-- 원본: kael-system-v3 `backend/db/schema.py`(CREATE)와 `backend/db/migration_sql.py`(ALTER).
-- v3 `init_db.py:11-16` 은 SCHEMA_SQL 을 돌린 뒤 MIGRATION_SQL 을 순서대로 실행하므로 실제
-- 컬럼 순서는 **CREATE 순서 + ALTER 실행 순서**다. 여기서는 그 결과를 하나의 CREATE 로 합쳤고,
-- 마이그레이션으로 붙은 열은 `-- migration_sql.py:NN` 주석으로 표시했다.
-- 컬럼·타입·PK·WITHOUT ROWID 는 `tests/test_compat_schema.py` 가 리터럴로 고정한다.
--
-- 이 파일에 없는 v3 표(analyst_opinions·stock_info·major_shareholders·시장 6표·
-- pipeline_runs·column_units·research_reports·broker_reports 등)는 **우리가 채우지 않는다**
-- (플랜 §1-5 처분표). M4 제자리 upsert 때도 여기 9표만 건드린다.
--
-- 인덱스는 v3 것 중 조회 경로에 쓰는 넷만 옮겼다(schema.py:29·49·167·187).

-- ── stocks — schema.py:4-14 + migration_sql.py:71(delisted_date, CREATE 에 이미 있어 no-op) ──
-- v3 에서 유일하게 WITHOUT ROWID 가 아닌 표다.
CREATE TABLE IF NOT EXISTS stocks (
    stock_code TEXT(6) PRIMARY KEY,
    stock_name TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market IN ('KOSPI', 'KOSDAQ')),
    sector TEXT,
    market_cap INTEGER,
    listed_date TEXT,
    is_active INTEGER DEFAULT 1,
    delisted_date TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ── daily_prices — schema.py:16-27 (adj_close 는 migration_sql.py:8 과 중복 선언) ──────────
CREATE TABLE IF NOT EXISTS daily_prices (
    stock_code TEXT(6) NOT NULL,
    trade_date TEXT NOT NULL,
    open INTEGER NOT NULL,
    high INTEGER NOT NULL,
    low INTEGER NOT NULL,
    close INTEGER NOT NULL,
    volume INTEGER NOT NULL,
    amount INTEGER,
    adj_close REAL,
    PRIMARY KEY (stock_code, trade_date)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_dp_date_code ON daily_prices(trade_date, stock_code);

-- ── investor_detail_flows — schema.py:31-47 ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS investor_detail_flows (
    stock_code TEXT(6) NOT NULL,
    trade_date TEXT NOT NULL,
    individual INTEGER DEFAULT 0,
    foreign_investor INTEGER DEFAULT 0,
    institution_total INTEGER DEFAULT 0,
    financial_investment INTEGER DEFAULT 0,
    insurance INTEGER DEFAULT 0,
    investment_trust INTEGER DEFAULT 0,
    etc_financial INTEGER DEFAULT 0,
    bank INTEGER DEFAULT 0,
    pension_fund INTEGER DEFAULT 0,
    private_equity INTEGER DEFAULT 0,
    nation INTEGER DEFAULT 0,
    etc_corporation INTEGER DEFAULT 0,
    PRIMARY KEY (stock_code, trade_date)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_idf_date ON investor_detail_flows(trade_date, stock_code);

-- ── consensus_annual — schema.py:51-68 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS consensus_annual (
    stock_code TEXT(6) NOT NULL,
    period TEXT NOT NULL,
    period_type TEXT NOT NULL CHECK(period_type IN ('annual', 'quarter')),
    data_type TEXT,
    revenue REAL,
    yoy REAL,
    op REAL,
    ni REAL,
    eps INTEGER,
    bps INTEGER,
    per REAL,
    pbr REAL,
    roe REAL,
    ev_ebitda REAL,
    accounting_standard TEXT,
    PRIMARY KEY (stock_code, period, period_type)
) WITHOUT ROWID;

-- ── consensus_revision_daily — schema.py:70-84 + migration_sql.py:30 ──────────────────────
CREATE TABLE IF NOT EXISTS consensus_revision_daily (
    stock_code TEXT(6) NOT NULL,
    base_date TEXT NOT NULL,
    target_period TEXT,
    opinion REAL,
    revenue REAL,
    op REAL,
    ni REAL,
    eps INTEGER,
    per REAL,
    bps INTEGER,
    pbr REAL,
    roe REAL,
    collected_date TEXT,                       -- migration_sql.py:30
    PRIMARY KEY (stock_code, base_date)
) WITHOUT ROWID;

-- ── consensus_revision_compare — schema.py:86-98 ──────────────────────────────────────────
-- 종목당 1행 스냅샷(PK = stock_code). 9지표 × (1w, 1m, 3m, 1y) = 36열.
-- v3 DDL 에 WITHOUT ROWID 가 **없다**(stocks 와 함께 둘뿐) — 원문 그대로 둔다.
CREATE TABLE IF NOT EXISTS consensus_revision_compare (
    stock_code TEXT(6) PRIMARY KEY,
    target_period TEXT,
    opinion_1w REAL, opinion_1m REAL, opinion_3m REAL, opinion_1y REAL,
    revenue_1w REAL, revenue_1m REAL, revenue_3m REAL, revenue_1y REAL,
    op_1w REAL, op_1m REAL, op_3m REAL, op_1y REAL,
    ni_1w REAL, ni_1m REAL, ni_3m REAL, ni_1y REAL,
    eps_1w INTEGER, eps_1m INTEGER, eps_3m INTEGER, eps_1y INTEGER,
    per_1w REAL, per_1m REAL, per_3m REAL, per_1y REAL,
    bps_1w INTEGER, bps_1m INTEGER, bps_3m INTEGER, bps_1y INTEGER,
    pbr_1w REAL, pbr_1m REAL, pbr_3m REAL, pbr_1y REAL,
    roe_1w REAL, roe_1m REAL, roe_3m REAL, roe_1y REAL
);

-- ── financial_summary — schema.py:100-121 + migration_sql.py:4-7, 18-19 ───────────────────
-- v3 스코어링이 읽는 창: `period_type='annual' AND data_type IS NULL` 최신 2행(quality) /
-- 1행(valuation) — `backend/scoring/factors/quality.py:10-19`·`valuation.py:8-14`.
CREATE TABLE IF NOT EXISTS financial_summary (
    stock_code TEXT(6) NOT NULL,
    period TEXT NOT NULL,
    period_type TEXT NOT NULL CHECK(period_type IN ('annual', 'quarter')),
    revenue INTEGER,
    op INTEGER,
    ni INTEGER,
    eps INTEGER,
    bps INTEGER,
    per REAL,
    pbr REAL,
    roe REAL,
    roa REAL,
    debt_ratio REAL,
    fcf INTEGER,
    capex INTEGER,
    op_margin REAL,
    ni_margin REAL,
    dividend_yield REAL,
    shares INTEGER,
    ev_ebitda REAL,                            -- migration_sql.py:4
    yoy REAL,                                  -- migration_sql.py:5
    data_type TEXT,                            -- migration_sql.py:6
    accounting_standard TEXT,                  -- migration_sql.py:7
    gross_profit INTEGER,                      -- migration_sql.py:18
    total_assets INTEGER,                      -- migration_sql.py:19
    PRIMARY KEY (stock_code, period, period_type)
) WITHOUT ROWID;

-- ── score_history — schema.py:155-165 + migration_sql.py:9-15, 32-65 (48열) ────────────────
-- 이번 태스크(T1.2)는 DDL 만 만든다. 행은 T2.7 이 model 판에서 채운다.
-- 6열(growth_score·sentiment_score·volatility_score·size_score·foreign_score·
-- shareholder_score)은 v3 에서도 항상 NULL 이다(플랜 §8-1).
CREATE TABLE IF NOT EXISTS score_history (
    stock_code TEXT(6) NOT NULL,
    score_date TEXT NOT NULL,
    momentum_score REAL,
    revision_score REAL,
    flow_score REAL,
    valuation_score REAL,
    composite_score REAL NOT NULL,
    rank INTEGER,
    quality_score REAL,                        -- migration_sql.py:9
    growth_score REAL,                         -- migration_sql.py:10
    sentiment_score REAL,                      -- migration_sql.py:11
    volatility_score REAL,                     -- migration_sql.py:12
    size_score REAL,                           -- migration_sql.py:13
    foreign_score REAL,                        -- migration_sql.py:14
    shareholder_score REAL,                    -- migration_sql.py:15
    r1m REAL,                                  -- migration_sql.py:32
    r3m REAL,                                  -- migration_sql.py:33
    r6m REAL,                                  -- migration_sql.py:34
    r9m REAL,                                  -- migration_sql.py:35
    r12m REAL,                                 -- migration_sql.py:36
    op_change_1w REAL,                         -- migration_sql.py:37
    ni_change_1w REAL,                         -- migration_sql.py:38
    op_change_1m REAL,                         -- migration_sql.py:39
    ni_change_1m REAL,                         -- migration_sql.py:40
    op_change_3m REAL,                         -- migration_sql.py:41
    ni_change_3m REAL,                         -- migration_sql.py:42
    flow_inst_5d REAL,                         -- migration_sql.py:43
    flow_inst_20d REAL,                        -- migration_sql.py:44
    flow_for_5d REAL,                          -- migration_sql.py:45
    flow_for_20d REAL,                         -- migration_sql.py:46
    flow_pe_5d REAL,                           -- migration_sql.py:47
    flow_pe_20d REAL,                          -- migration_sql.py:48
    qual_gpa REAL,                             -- migration_sql.py:49
    qual_roa REAL,                             -- migration_sql.py:50
    qual_fcf_assets REAL,                      -- migration_sql.py:51
    qual_debt_ratio REAL,                      -- migration_sql.py:52
    qual_gpa_change REAL,                      -- migration_sql.py:53
    qual_std_20d REAL,                         -- migration_sql.py:54
    val_per REAL,                              -- migration_sql.py:55
    val_pbr REAL,                              -- migration_sql.py:56
    val_ev_ebitda REAL,                        -- migration_sql.py:57
    val_dividend_yield REAL,                   -- migration_sql.py:58
    op_1w_flag TEXT,                           -- migration_sql.py:60
    ni_1w_flag TEXT,                           -- migration_sql.py:61
    op_1m_flag TEXT,                           -- migration_sql.py:62
    ni_1m_flag TEXT,                           -- migration_sql.py:63
    op_3m_flag TEXT,                           -- migration_sql.py:64
    ni_3m_flag TEXT,                           -- migration_sql.py:65
    PRIMARY KEY (stock_code, score_date)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_sh_date_score ON score_history(score_date, composite_score DESC);

-- ── score_history_v2 — schema.py:169-185 (= migration_sql.py:20-29 과 같은 DDL, 21열) ──────
CREATE TABLE IF NOT EXISTS score_history_v2 (
    stock_code TEXT(6) NOT NULL,
    score_date TEXT NOT NULL,
    momentum_score REAL,
    growth_score REAL,
    flow_score REAL,
    value_score REAL,
    total_score REAL NOT NULL,
    rank INTEGER,
    r1m REAL, r3m REAL, r6m REAL,
    op_yoy_cur REAL, op_yoy_next REAL,
    ni_yoy_cur REAL, ni_yoy_next REAL,
    inst_5d REAL, inst_20d REAL,
    frgn_5d REAL, frgn_20d REAL,
    per_cur REAL, per_next REAL,
    PRIMARY KEY (stock_code, score_date)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_shv2_date_score ON score_history_v2(score_date, total_score DESC);
