-- v3 날짜별 사본 소형 픽스처 — 컷오버 트랙 QL-I 되돌리기 리허설(`tests/test_v3_restore.py`).
-- 만든 방법: 원본 v3 로컬 사본(`data/quant.db`, 마지막 거래일 2026-08-07)을 읽기 전용(immutable)으로 열어
--   ① 표·색인 DDL 을 sqlite_master 그대로(sqlite_sequence 는 AUTOINCREMENT 가 만든다)
--   ② 행: compat 9표 = 3종목(005930·000660·035720) × 마지막 6거래일(07-31~08-07) 등 그대로,
--      9표 밖(v3 가 계속 쓰는 표) = 시장 표 마지막 2~3거래일 · stock_info·analyst_opinions 마지막 스냅샷 ·
--      column_units 의 daily_prices 행. 리포트·대주주·pipeline_runs 는 싣지 않는다(본문·인명·경로) — 테스트가
--      컷오버 기간 v3 쓰기로 합성 행을 넣는다.
-- 실물 v3 stocks 는 delisted_date 가 마이그레이션으로 끝에 붙어 있다(열 순서가 compat v3_schema.sql 과 다르다).
CREATE TABLE analyst_opinions (
    stock_code TEXT(6) NOT NULL,
    snapshot_date TEXT NOT NULL,
    opinion_score REAL,
    target_price INTEGER,
    estimated_eps INTEGER,
    estimated_per REAL,
    analyst_count INTEGER,
    PRIMARY KEY (stock_code, snapshot_date)
) WITHOUT ROWID;
CREATE TABLE broker_reports (
    source        TEXT NOT NULL,
    report_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    broker        TEXT NOT NULL,
    report_date   TEXT NOT NULL,
    stock_name    TEXT,
    stock_code    TEXT(6),
    target_price  INTEGER,
    opinion       TEXT,
    analyst       TEXT,
    pdf_url       TEXT,
    pdf_path      TEXT,
    pdf_text      TEXT,
    pdf_text_len  INTEGER,
    parse_status  TEXT,
    content_hash  TEXT,
    collected_at  TEXT NOT NULL,
    PRIMARY KEY (source, report_id)
) WITHOUT ROWID;
CREATE TABLE column_units (
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    unit TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (table_name, column_name)
) WITHOUT ROWID;
CREATE TABLE consensus_annual (
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
CREATE TABLE consensus_revision_compare (
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
CREATE TABLE consensus_revision_daily (
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
    roe REAL, collected_date TEXT,
    PRIMARY KEY (stock_code, base_date)
) WITHOUT ROWID;
CREATE TABLE daily_prices (
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
CREATE TABLE financial_summary (
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
    shares INTEGER, ev_ebitda REAL, yoy REAL, data_type TEXT, accounting_standard TEXT, gross_profit INTEGER, total_assets INTEGER,
    PRIMARY KEY (stock_code, period, period_type)
) WITHOUT ROWID;
CREATE TABLE investor_detail_flows (
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
CREATE TABLE macro_data (
    indicator  TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    value      REAL NOT NULL,
    PRIMARY KEY (indicator, trade_date)
) WITHOUT ROWID;
CREATE TABLE major_shareholders (
    stock_code TEXT(6) NOT NULL,
    snapshot_date TEXT NOT NULL,
    shareholder_name TEXT NOT NULL,
    shares INTEGER,
    ownership_pct REAL,
    PRIMARY KEY (stock_code, snapshot_date, shareholder_name)
) WITHOUT ROWID;
CREATE TABLE market_breadth (
    index_code  TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    advancing   INTEGER NOT NULL,
    declining   INTEGER NOT NULL,
    unchanged   INTEGER NOT NULL,
    limit_up    INTEGER DEFAULT 0,
    limit_down  INTEGER DEFAULT 0,
    PRIMARY KEY (index_code, trade_date)
) WITHOUT ROWID;
CREATE TABLE market_indices (
    index_code  TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        INTEGER NOT NULL,
    high        INTEGER NOT NULL,
    low         INTEGER NOT NULL,
    close       INTEGER NOT NULL,
    volume      INTEGER,
    PRIMARY KEY (index_code, trade_date)
) WITHOUT ROWID;
CREATE TABLE market_inflection (
    trade_date        TEXT PRIMARY KEY,
    probability       REAL NOT NULL,
    direction         INTEGER NOT NULL,
    d2_value          REAL,
    confirmed_prob    REAL,
    confirmed_dir     INTEGER,
    consecutive_days  INTEGER NOT NULL DEFAULT 0,
    alert_level       TEXT,
    cluster_strength  REAL,
    cluster_grade     TEXT,
    updated_at        TEXT NOT NULL
, confirmed_at TEXT) WITHOUT ROWID;
CREATE TABLE market_investor_flows (
    index_code       TEXT NOT NULL,
    trade_date       TEXT NOT NULL,
    securities       INTEGER,
    insurance        INTEGER,
    investment_trust INTEGER,
    bank             INTEGER,
    pension_fund     INTEGER,
    private_equity   INTEGER,
    foreign_investor INTEGER,
    individual       INTEGER,
    institution_total INTEGER,
    PRIMARY KEY (index_code, trade_date)
) WITHOUT ROWID;
CREATE TABLE market_regime (
    trade_date      TEXT PRIMARY KEY,
    regime_label    TEXT NOT NULL,
    composite_score REAL,
    sub_scores      TEXT,
    flow_regime     TEXT,
    flow_z          REAL,
    vol_regime      TEXT,
    vkospi          REAL,
    macro_regime    TEXT,
    usd_krw         REAL,
    spread_10y_3y   REAL,
    theme_regime    TEXT,
    updated_at      TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE pipeline_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT CHECK(status IN ('running', 'success', 'failed')),
    error_message TEXT,
    rows_affected INTEGER DEFAULT 0
);
CREATE TABLE program_trading (
    index_code  TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    arb_net     INTEGER,
    non_arb_net INTEGER,
    total_net   INTEGER,
    PRIMARY KEY (index_code, trade_date)
) WITHOUT ROWID;
CREATE TABLE research_reports (
    category      TEXT NOT NULL,
    nid           INTEGER NOT NULL,
    title         TEXT NOT NULL,
    broker        TEXT,
    report_date   TEXT NOT NULL,
    views         INTEGER,
    stock_name    TEXT,
    stock_code    TEXT(6),
    target_price  INTEGER,
    opinion       TEXT,
    summary       TEXT,
    pdf_url       TEXT,
    pdf_path      TEXT,
    pdf_text      TEXT,
    pdf_text_len  INTEGER,
    parse_status  TEXT,
    content_hash  TEXT,
    collected_at  TEXT NOT NULL, sector TEXT,
    PRIMARY KEY (category, nid)
) WITHOUT ROWID;
CREATE TABLE score_history (
    stock_code TEXT(6) NOT NULL,
    score_date TEXT NOT NULL,
    momentum_score REAL,
    revision_score REAL,
    flow_score REAL,
    valuation_score REAL,
    composite_score REAL NOT NULL,
    rank INTEGER, quality_score REAL, growth_score REAL, sentiment_score REAL, volatility_score REAL, size_score REAL, foreign_score REAL, shareholder_score REAL, r1m REAL, r3m REAL, r6m REAL, r9m REAL, r12m REAL, op_change_1w REAL, ni_change_1w REAL, op_change_1m REAL, ni_change_1m REAL, op_change_3m REAL, ni_change_3m REAL, flow_inst_5d REAL, flow_inst_20d REAL, flow_for_5d REAL, flow_for_20d REAL, flow_pe_5d REAL, flow_pe_20d REAL, qual_gpa REAL, qual_roa REAL, qual_fcf_assets REAL, qual_debt_ratio REAL, qual_gpa_change REAL, qual_std_20d REAL, val_per REAL, val_pbr REAL, val_ev_ebitda REAL, val_dividend_yield REAL, op_1w_flag TEXT, ni_1w_flag TEXT, op_1m_flag TEXT, ni_1m_flag TEXT, op_3m_flag TEXT, ni_3m_flag TEXT,
    PRIMARY KEY (stock_code, score_date)
) WITHOUT ROWID;
CREATE TABLE score_history_v2 (
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
CREATE TABLE sector_daily (
    sector_code TEXT NOT NULL,
    sector_name TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    close       INTEGER,
    change_rate REAL,
    volume      INTEGER,
    advancing   INTEGER,
    declining   INTEGER,
    PRIMARY KEY (sector_code, trade_date)
) WITHOUT ROWID;
CREATE TABLE stock_info (
    stock_code TEXT(6) NOT NULL,
    snapshot_date TEXT NOT NULL,
    market_cap INTEGER,
    foreign_ownership_pct REAL,
    beta REAL,
    floating_ratio REAL,
    high_52w INTEGER,
    low_52w INTEGER,
    PRIMARY KEY (stock_code, snapshot_date)
) WITHOUT ROWID;
CREATE TABLE stocks (
    stock_code TEXT(6) PRIMARY KEY,
    stock_name TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market IN ('KOSPI', 'KOSDAQ')),
    sector TEXT,
    market_cap INTEGER,
    listed_date TEXT,
    is_active INTEGER DEFAULT 1,
    updated_at TEXT DEFAULT (datetime('now'))
, delisted_date TEXT);
CREATE INDEX idx_broker_reports_date ON broker_reports(report_date DESC);
CREATE INDEX idx_broker_reports_stock ON broker_reports(stock_code, report_date DESC);
CREATE INDEX idx_dp_date_code ON daily_prices(trade_date, stock_code);
CREATE INDEX idx_idf_date ON investor_detail_flows(trade_date, stock_code);
CREATE INDEX idx_mi_date ON market_indices(trade_date);
CREATE INDEX idx_research_date ON research_reports(report_date);
CREATE INDEX idx_research_stock ON research_reports(stock_code);
CREATE INDEX idx_sh_date_score ON score_history(score_date, composite_score DESC);
CREATE INDEX idx_shv2_date_score ON score_history_v2(score_date, total_score DESC);
INSERT INTO stocks (stock_code, stock_name, market, sector, market_cap, listed_date, is_active, updated_at, delisted_date) VALUES ('000660', 'SK하이닉스', 'KOSPI', '전기/전자', 10387601, '1996-12-26', 1, '2026-08-07 11:05:23', NULL);
INSERT INTO stocks (stock_code, stock_name, market, sector, market_cap, listed_date, is_active, updated_at, delisted_date) VALUES ('005930', '삼성전자', 'KOSPI', '전기/전자', 13504904, '1975-06-11', 1, '2026-08-07 11:05:23', NULL);
INSERT INTO stocks (stock_code, stock_name, market, sector, market_cap, listed_date, is_active, updated_at, delisted_date) VALUES ('035720', '카카오', 'KOSPI', 'IT 서비스', 176749, '2017-07-10', 1, '2026-08-07 11:05:23', NULL);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('000660', '2026-07-31', 1697000, 1718000, 1586000, 1718000, 10499619, 17548832, 1718000.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('000660', '2026-08-03', 1642000, 1645000, 1562000, 1567000, 5432833, 8641702, 1567000.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('000660', '2026-08-04', 1620000, 1630000, 1483000, 1577000, 5391210, 8323856, 1577000.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('000660', '2026-08-05', 1693000, 1701000, 1647000, 1668000, 3945674, 6621651, 1668000.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('000660', '2026-08-06', 1600000, 1606000, 1481000, 1495000, 5498295, 8368491, 1495000.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('000660', '2026-08-07', 1521000, 1542000, 1409000, 1422000, 5002539, 7254602, 1422000.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('005930', '2026-07-31', 257000, 267000, 243000, 262500, 58478873, 14924242, 262500.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('005930', '2026-08-03', 248000, 249500, 238000, 239500, 27825493, 6736161, 239500.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('005930', '2026-08-04', 244500, 244500, 228000, 240000, 29433821, 6922845, 240000.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('005930', '2026-08-05', 254000, 254500, 244000, 246000, 22577128, 5610076, 246000.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('005930', '2026-08-06', 241500, 246000, 228000, 230500, 26101823, 6091289, 230500.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('005930', '2026-08-07', 235000, 239500, 229000, 231000, 20546010, 4791581, 231000.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('035720', '2026-07-31', 37050, 37600, 35750, 37400, 3029760, 111804, 37400.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('035720', '2026-08-03', 36650, 37500, 35900, 37200, 1702786, 62658, 37200.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('035720', '2026-08-04', 37600, 38000, 36700, 37900, 2281800, 85510, 37900.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('035720', '2026-08-05', 38100, 39350, 38000, 38150, 2573345, 99170, 38150.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('035720', '2026-08-06', 38350, 38400, 36750, 38300, 2375197, 89549, 38300.0);
INSERT INTO daily_prices (stock_code, trade_date, open, high, low, close, volume, amount, adj_close) VALUES ('035720', '2026-08-07', 38300, 40600, 37800, 39900, 3984335, 157850, 39900.0);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('000660', '2026-07-31', -4122568, 3666286, 504452, -380294, 5366, 649872, 6442, 756, 108679, 113632, 0, -30409);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('000660', '2026-08-03', 2174506, -1768492, -456003, -441333, 23411, -178833, 1230, 5815, 78505, 55202, 0, 42256);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('000660', '2026-08-04', 569273, -635491, 30975, 260081, 23219, -52547, -308, -620, -10806, -188042, 0, 35790);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('000660', '2026-08-05', -614670, 659558, -43095, 19180, 5787, 27377, -870, 1361, -29792, -66138, 0, 77);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('000660', '2026-08-06', 2039284, -1693585, -422181, 153053, 11785, -545510, -1907, -646, 51083, -90038, 0, 59996);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('000660', '2026-08-07', 271106, -69812, 47584, 285621, 6490, -168004, -456, 390, -39774, -36684, 0, -252036);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('005930', '2026-07-31', -2958633, 2113789, 935049, 116000, -42816, 442896, 2923, 12379, 70860, 332808, 0, -76352);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('005930', '2026-08-03', 2097252, -952097, -1219196, -864274, 12527, -309733, 1999, 1563, -32644, -28634, 0, 67464);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('005930', '2026-08-04', 689254, 201284, -926889, -377331, 9592, -106500, 90, -7187, -34379, -411175, 0, 34437);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('005930', '2026-08-05', -60218, 577807, -517404, 125535, -15747, 2887, 1780, 1751, -56408, -577201, 0, 446);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('005930', '2026-08-06', 970899, -726837, -277668, 89300, 12474, -259431, -2117, -1222, -108882, -7788, 0, 29943);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('005930', '2026-08-07', 143022, -398687, 8288, 27197, 2301, 12317, 898, 561, -102710, 67724, 0, 248135);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('035720', '2026-07-31', -14116, 17998, -3673, -5017, -38, -550, 19, 0, 2450, -537, 0, -93);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('035720', '2026-08-03', 1106, 7549, -8096, -8763, 80, 35, 0, 0, -179, 732, 0, -642);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('035720', '2026-08-04', -1323, 2091, -884, -2030, 145, -1116, 0, 0, 780, 1338, 0, 173);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('035720', '2026-08-05', 9392, 719, -10132, -1599, -59, -920, 0, 0, -2356, -5197, 0, 3);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('035720', '2026-08-06', -1466, 1440, 6, 354, 204, -752, 2, 0, -417, 615, 0, 38);
INSERT INTO investor_detail_flows (stock_code, trade_date, individual, foreign_investor, institution_total, financial_investment, insurance, investment_trust, etc_financial, bank, pension_fund, private_equity, nation, etc_corporation) VALUES ('035720', '2026-08-07', -34920, 17962, 15463, 5902, 348, 908, -1, 0, 5962, 2345, 0, 1756);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('000660', '2026-07-31', 2.1575436471248097, 0.6123988287757138, -0.04282188159878653, -0.19082303360234934, 0.8647237406922993, 70, 0.6138767760213449, NULL, NULL, NULL, NULL, NULL, NULL, -0.2144490169181527, 0.18728403593642018, 1.069879518072289, 1.9671848013816926, 5.519924098671726, -0.018754627902806586, 0.08634323168682759, -0.0007761535629198073, 0.11425346162724148, 0.07916419815528802, 0.20961532331818883, 0.2044920185955874, 0.09554569497553718, -0.0816563755815902, -0.41973443685701967, 0.03176848441086071, 0.03587809233553939, 0.33326595032471606, 29.02, 0.14680902652183864, 45.95, 0.25498088573803995, 0.1039206345514505, 11.04, 3.79, NULL, 0.46, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('000660', '2026-08-03', 1.0761759453690727, 0.44475950874548353, -0.3980748979988226, -0.1890329560879769, 0.42177178290481365, 275, 0.6400942187900899, NULL, NULL, NULL, NULL, NULL, NULL, -0.35381443298969073, -0.021236727045596503, 0.7276736493936052, 1.642495784148398, 5.061895551257253, -0.020449485117621912, 0.09231377664603965, -0.002502064118533272, 0.12037740127592239, 0.07730021053381803, 0.2162633719832585, 0.1484761481687264, -0.1555421311517658, -0.14746940524503976, -0.46453209910354976, 0.03684265011708497, 0.03848162130688755, 0.33326595032471606, 29.02, 0.14680902652183864, 45.95, 0.25498088573803995, 0.10165348679505584, 11.04, 3.79, NULL, 0.46, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('000660', '2026-08-04', 0.8625451957311705, 0.3772609100467613, -0.327422097222368, -0.18965400733622345, 0.35426282218776756, 285, 0.6677081063248395, NULL, NULL, NULL, NULL, NULL, NULL, -0.32693128467776356, -0.046553808948004836, 0.7522222222222222, 1.7189655172413794, 5.019083969465649, 0.0012900679428265343, 0.00788869226963797, 0.0012900679428265343, 0.00788869226963797, 0.04053170395578743, 0.1705832441286458, 0.09614131762828818, -0.03129593966595963, 0.0528647688145651, -0.5107987810621044, 0.013397466029332809, 0.026929569053109562, 0.33326595032471606, 29.02, 0.14680902652183864, 45.95, 0.25498088573803995, 0.10170005690997139, 11.04, 3.79, NULL, 0.46, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('000660', '2026-08-05', 1.175719791064513, 0.2858050511241747, -0.16517077931997162, -0.19000577152244388, 0.4522963715042346, 201, 0.6587365186386703, NULL, NULL, NULL, NULL, NULL, NULL, -0.2421626533393912, -0.010676156583629894, 0.9809976247030879, 1.7524752475247525, 5.502923976608187, 0.0012900679428265343, 0.00788869226963797, 0.0012900679428265343, 0.00788869226963797, 0.04053170395578743, 0.1705832441286458, -0.003816370696385679, -0.028443086374593925, 0.20566832939216043, -0.33297397299364373, -0.0018723614775454911, 0.0016093248099057393, 0.33326595032471606, 29.02, 0.14680902652183864, 45.95, 0.25498088573803995, 0.10234343417070826, 11.04, 3.79, NULL, 0.46, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('000660', '2026-08-06', 0.30589979904129166, 0.24273103996440892, -0.5362333694382949, -0.18874871792251224, 0.10004647068064637, 533, 0.615787646588464, NULL, NULL, NULL, NULL, NULL, NULL, -0.2798651252408478, -0.2047872340425532, 0.7818831942789034, 1.4151857835218093, 4.599250936329588, 0.0012900679428265343, 0.00788869226963797, 0.0012900679428265343, 0.00788869226963797, 0.04053170395578743, 0.1705832441286458, -0.03533164646999902, -0.020114989102049737, 0.020902747503150163, -0.5424014644999144, -0.016059539627873664, -0.016559408640033052, 0.33326595032471606, 29.02, 0.14680902652183864, 45.95, 0.25498088573803995, 0.104020062026488, 11.04, 3.79, NULL, 0.46, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('000660', '2026-08-07', -0.1587177632255789, 0.21373148496744002, -0.9948248085159973, -0.18881943392600234, -0.1388967461738313, 809, 0.6244604239941005, NULL, NULL, NULL, NULL, NULL, NULL, -0.3494967978042086, -0.22506811989100817, 0.6031567080045096, 1.3047001620745542, 4.286245353159852, 0.0012900679428265343, 0.00788869226963797, 0.0012900679428265343, 0.00788869226963797, 0.04053170395578743, 0.1705832441286458, -0.08112749036086388, -0.10272872437052598, -0.3376931786270959, -0.5341882115033105, -0.03135468911445482, -0.032499515528176336, 0.33326595032471606, 29.02, 0.14680902652183864, 45.95, 0.25498088573803995, 0.10319228262676429, 11.04, 3.79, NULL, 0.46, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('005930', '2026-07-31', 2.3021365427016565, 0.24285078728387857, 0.11014051007042219, 0.09689372023104702, 0.7917539202022343, 86, -0.034597528306152855, NULL, NULL, NULL, NULL, NULL, NULL, -0.08216783216783216, 0.12903225806451613, 0.745345744680851, 1.6093439363817097, 2.755364806866953, -0.0006338038459290432, -0.000620052032967492, 0.050964832896024374, 0.0432987256557862, 0.1957853885769325, 0.20840249627559254, 0.14107605515557606, 0.14269336403570304, -0.052102237640016626, -0.22314600982466273, 0.08963136239506633, 0.10343954421863878, 0.2317174893168103, 8.36, 0.0666611281822253, 29.94, 0.04301898581041234, 0.08786725511807005, 18.27, 1.87, NULL, 1.39, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('005930', '2026-08-03', 1.0750096493450385, 0.3888184363319029, -0.16581441300069358, 0.0999753985509006, 0.413243039505473, 283, -0.027400434525607472, NULL, NULL, NULL, NULL, NULL, NULL, -0.22617124394184168, -0.09962406015037593, 0.4298507462686567, 1.4143145161290323, 2.4811046511627906, 0.020582316742232577, 0.02710334179131426, 0.07327637066310892, 0.07224045248006783, 0.22117140533359558, 0.24192429984053515, 0.05861323767731334, -0.02397328293423213, -0.06533592699300814, -0.2849164720314913, 0.09670881042251814, 0.10880429475075307, 0.2317174893168103, 8.36, 0.0666611281822253, 29.94, 0.04301898581041234, 0.0876305367727755, 18.27, 1.87, NULL, 1.39, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('005930', '2026-08-04', 0.6411101828871569, 0.11407355067169565, -0.37119925551783667, 0.10269811476718417, 0.1632409313813808, 486, 0.006558509405739358, NULL, NULL, NULL, NULL, NULL, NULL, -0.24528301886792453, -0.11602209944751381, 0.41927853341218213, 1.4514811031664965, 2.404255319148936, 0.0, 0.0, 0.0, 0.0, 0.11223890596505656, 0.11207755380888576, 0.0027287300775158327, -0.0903971037417035, 0.055906004025780213, -0.22429916066979644, 0.02479732656150433, 0.07457621368692578, 0.2317174893168103, 8.36, 0.0666611281822253, 29.94, 0.04301898581041234, 0.08725319825868653, 18.27, 1.87, NULL, 1.39, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('005930', '2026-08-05', 0.819221009782556, 0.11198655613633218, -0.47109519489559076, 0.10263906867417888, 0.19790683144703844, 429, 0.024996937830723102, NULL, NULL, NULL, NULL, NULL, NULL, -0.16891891891891891, -0.08379888268156424, 0.544256120527307, 1.445328031809145, 2.426183844011142, 0.0, 0.0, 0.0, 0.0, 0.11223890596505656, 0.11207755380888576, -0.12275323506824055, -0.08609827181422133, 0.14525271270827908, -0.05205688143628304, -0.041876337841215784, 0.047074558236443236, 0.2317174893168103, 8.36, 0.0666611281822253, 29.94, 0.04301898581041234, 0.08646093484555001, 18.27, 1.87, NULL, 1.39, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('005930', '2026-08-06', 0.33890473218414896, 0.13320780333218646, -0.5283625305075343, 0.10362821055330156, 0.04715961067806401, 588, 0.008355350693400978, NULL, NULL, NULL, NULL, NULL, NULL, -0.16936936936936936, -0.19264448336252188, 0.45334174022698615, 1.2270531400966183, 2.2464788732394365, 0.0, 0.0, 0.0, 0.0, 0.11223890596505656, 0.11207755380888576, -0.14886886531521396, -0.13354154063708287, 0.09008426444336134, -0.044631763076453626, -0.05135105692688276, 0.06305184631979763, 0.2317174893168103, 8.36, 0.0666611281822253, 29.94, 0.04301898581041234, 0.08647852905467872, 18.27, 1.87, NULL, 1.39, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('005930', '2026-08-07', 0.3388782239245911, 0.12160828537133571, -0.7932757188661753, 0.10349257305926764, -0.0094510875968945, 683, 0.007088460816357909, NULL, NULL, NULL, NULL, NULL, NULL, -0.16906474820143885, -0.17204301075268819, 0.38822115384615385, 1.2405431619786615, 2.2489451476793247, 0.0, 0.0, 0.0, 0.0, 0.11223890596505656, 0.11207755380888576, -0.2171706663001825, -0.15570092168000602, -0.09615247912906304, -0.09193512223411585, -0.07086862668553587, 0.07305257408716123, 0.2317174893168103, 8.36, 0.0666611281822253, 29.94, 0.04301898581041234, 0.08648028567189026, 18.27, 1.87, NULL, 1.39, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('035720', '2026-07-31', 0.3259458630969113, 0.04921968294147166, 0.9699227771700597, -0.2238931302257613, 0.36112759619238016, 333, 0.7698268996942945, NULL, NULL, NULL, NULL, NULL, NULL, 0.06099290780141844, -0.2059447983014862, -0.3595890410958904, -0.40350877192982454, -0.3162705667276051, 0.0, 0.0, 0.008086826984464779, 0.005545826036193812, 0.031352057478772045, 0.04982477525521865, 0.15407876867360798, 0.398207333635129, 0.30907499622755397, 0.6331250943111514, 0.024113475177304965, 0.008196770786177757, 0.2915075494448144, 1.93, 0.03331473716414419, 82.49, -0.044630713143285795, 0.026907322670393342, 54.13, 2.35, NULL, 0.12, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('035720', '2026-08-03', 0.2994195268550166, 0.030152325084118033, 0.9380045373982564, -0.22212154299894016, 0.34166080761881895, 345, 0.7740049885732129, NULL, NULL, NULL, NULL, NULL, NULL, 0.04788732394366197, -0.19654427645788336, -0.3716216216216216, -0.3871499176276771, -0.34507042253521125, 0.0, 0.0, 0.008086826984464779, 0.005545826036193812, 0.031352057478772045, 0.04982477525521865, 0.07607910722196264, 0.3306592066218012, 0.35143729253773004, 0.6836803427413237, 0.02105723076176201, 0.014752198265660935, 0.2915075494448144, 1.93, 0.03331473716414419, 82.49, -0.044630713143285795, 0.026958932463896388, 54.13, 2.35, NULL, 0.12, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('035720', '2026-08-04', 0.21407925350688645, -0.02177936998138701, 0.722338390408156, -0.2166070477945987, 0.25981816176878103, 382, 0.7932122340895986, NULL, NULL, NULL, NULL, NULL, NULL, 0.06460674157303371, -0.16243093922651933, -0.35213675213675216, -0.3964968152866242, -0.40408805031446543, 0.0, 0.0, 0.0, 0.0, 0.0, -0.0018832391713747645, 0.04395139674787063, 0.33049615819882067, 0.3571624277800941, 0.6543629757579368, 0.011775567335755554, 0.02176425040204896, 0.2915075494448144, 1.93, 0.03331473716414419, 82.49, -0.044630713143285795, 0.027199407472503132, 54.13, 2.35, NULL, 0.12, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('035720', '2026-08-05', 0.08576884717497052, 0.0024955266988184554, 0.16407269498918042, -0.21829965482520214, 0.11731596102383017, 533, 0.7985207534637758, NULL, NULL, NULL, NULL, NULL, NULL, 0.07768361581920905, -0.17065217391304346, -0.3445017182130584, -0.38764044943820225, -0.40203761755485895, 0.0, 0.0, 0.0, 0.0, 0.0, -0.0018832391713747645, -0.0947235749747037, 0.22843600773978237, 0.24174985354769613, 0.6634082261815298, -0.020035858624709316, -0.008313757048941698, 0.2915075494448144, 1.93, 0.03331473716414419, 82.49, -0.044630713143285795, 0.02712128662930025, 54.13, 2.35, NULL, 0.12, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('035720', '2026-08-06', 0.02583326269435834, 0.0034062903145312172, 0.043066297430661704, -0.21759041511938124, 0.07589699857421468, 561, 0.8027091469735359, NULL, NULL, NULL, NULL, NULL, NULL, 0.10533910533910534, -0.14317673378076062, -0.3185053380782918, -0.40249609984399376, -0.40342679127725856, 0.0, 0.0, 0.0, 0.0, 0.0, -0.0018832391713747645, -0.13426106022562506, 0.2079251688651554, 0.17562565571548136, 0.6684938289068855, -0.017971024743313177, 0.0012966957833810753, 0.2915075494448144, 1.93, 0.03331473716414419, 82.49, -0.044630713143285795, 0.026462529982667012, 54.13, 2.35, NULL, 0.12, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history (stock_code, score_date, momentum_score, revision_score, flow_score, valuation_score, composite_score, rank, quality_score, growth_score, sentiment_score, volatility_score, size_score, foreign_score, shareholder_score, r1m, r3m, r6m, r9m, r12m, op_change_1w, ni_change_1w, op_change_1m, ni_change_1m, op_change_3m, ni_change_3m, flow_inst_5d, flow_inst_20d, flow_for_5d, flow_for_20d, flow_pe_5d, flow_pe_20d, qual_gpa, qual_roa, qual_fcf_assets, qual_debt_ratio, qual_gpa_change, qual_std_20d, val_per, val_pbr, val_ev_ebitda, val_dividend_yield, op_1w_flag, ni_1w_flag, op_1m_flag, ni_1m_flag, op_3m_flag, ni_3m_flag) VALUES ('035720', '2026-08-07', 0.43714010969892847, -0.22732587940608015, 0.5169292764909449, -0.2176343022373131, 0.2245770797232536, 443, 0.8001038556094143, NULL, NULL, NULL, NULL, NULL, NULL, 0.18573551263001487, -0.08381171067738231, -0.3060869565217391, -0.37460815047021945, -0.37362637362637363, 0.010344099641123074, -0.02888243831640058, 0.010344099641123074, -0.02888243831640058, 0.010344099641123074, -0.030711284948573083, -0.020611149143700956, 0.28231560008826073, 0.16838001912316336, 0.7360324528002987, -0.0009448426865215645, 0.018738436992571387, 0.2915075494448144, 1.93, 0.03331473716414419, 82.49, -0.044630713143285795, 0.02636976210370847, 54.13, 2.35, NULL, 0.12, NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('000660', '2026-07-31', 62.299440230055694, 80.19364702998516, 52.39826155669696, 96.63125793578814, 69.52029971226456, 10, -32.89062499999999, 32.15384615384615, 123.98956975228161, 464.52790643286744, 47.2482807700094, 474.7303834709288, 29.53521341451055, 20.44920185955874, 9.554569497553718, -8.16563755815902, -41.973443685701966, 3.87, 3.02);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('000660', '2026-08-03', 61.38755489855685, 80.18624805097163, 36.29541864139021, 92.1477892290075, 64.77008687065542, 37, -28.349336991312303, 21.191028615622585, 112.9076086956522, 463.55261329415214, 47.500122635415345, 477.889219509456, 28.82364526776331, 14.847614816872639, -15.55421311517658, -14.746940524503977, -46.45320991035498, 5.01, 3.93);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('000660', '2026-08-04', 59.66373794657432, 79.66088736897991, 45.21327014218009, 94.01572791278674, 66.48532228993895, 29, -34.96907216494846, 22.628304821150856, 97.125, 464.2797616929259, 47.42786044568403, 482.4479156109059, 27.753053603630097, 9.614131762828817, -3.129593966595963, 5.28647688145651, -51.079878106210444, 4.53, 3.58);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('000660', '2026-08-05', 59.67769459824947, 79.80702262349915, 37.006319115323855, 94.27991398579634, 64.51533747511014, 53, -28.809218950064018, 15.272978576364892, 98.33531510107017, 464.2797616929259, 47.42786044568403, 482.4479156109059, 27.753053603630097, -0.3816370696385679, -2.8443086374593927, 20.56683293921604, -33.29739729936437, 4.53, 3.58);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('000660', '2026-08-06', 54.03730079815556, 79.84224523258415, 25.398894154818326, 94.38284824570277, 59.94398443412598, 107, -32.07632894139027, -6.620861961274205, 73.63530778164925, 464.2797616929259, 47.42786044568403, 482.4479156109059, 27.753053603630097, -3.533164646999902, -2.0114989102049736, 2.0902747503150163, -54.24014644999144, 4.53, 3.58);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('000660', '2026-08-07', 49.95857925224697, 79.66584599724136, 11.248025276461297, 94.54485311712625, 55.13711150553652, 230, -31.502890173410403, -14.026602176541713, 56.43564356435644, 464.2797616929259, 47.42786044568403, 482.4479156109059, 27.753053603630097, -8.112749036086388, -10.272872437052598, -33.76931786270959, -53.418821150331055, 4.53, 3.58);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('005930', '2026-07-31', 70.91658801030223, 83.35362680271905, 54.78467009087318, 94.7754171957192, 73.62245502633255, 4, -16.534181240063596, 18.243243243243246, 72.58382642998026, 778.416230801781, 41.383341510036445, 602.8116877717971, 40.4451135302786, 14.107605515557605, 14.269336403570303, -5.210223764001663, -22.314600982466274, 4.47, 3.17);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('005930', '2026-08-03', 66.17554132536543, 83.0337952724421, 40.11058451816746, 91.21673588678665, 68.06381046118489, 15, -16.25874125874126, 5.973451327433632, 57.462195923734384, 797.0646807817703, 40.38601832303061, 622.3082147337067, 39.7566472750829, 5.861323767731334, -2.397328293423213, -6.533592699300813, -28.491647203149128, 5.48, 3.91);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('005930', '2026-08-04', 60.49333263633362, 83.01347756830803, 39.53001579778831, 90.80967522143992, 66.16618841139896, 31, -22.45557350565428, 8.843537414965997, 50.47021943573669, 797.0646807817703, 40.38601832303061, 622.3082147337067, 39.7566472750829, 0.2728730077515833, -9.03971037417035, 5.590600402578021, -22.429916066979644, 5.48, 3.91);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('005930', '2026-08-05', 59.35332108484248, 83.0720210881466, 30.497630331753555, 90.82986730045553, 63.58859801928799, 59, -22.64150943396226, 5.8064516129032295, 51.47783251231528, 797.0646807817703, 40.38601832303061, 622.3082147337067, 39.7566472750829, -12.275323506824055, -8.609827181422133, 14.525271270827908, -5.205688143628303, 5.48, 3.91);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('005930', '2026-08-06', 51.78793044167735, 83.12972896182869, 29.06398104265403, 90.94437511944511, 60.99221704175126, 94, -22.12837837837838, -13.345864661654138, 43.4349719975109, 797.0646807817703, 40.38601832303061, 622.3082147337067, 39.7566472750829, -14.886886531521396, -13.354154063708288, 9.008426444336134, -4.463176307645362, 5.48, 3.91);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('005930', '2026-08-07', 51.073219258542466, 82.87042425705216, 16.22037914691943, 91.10172712301001, 57.49188176656185, 159, -16.75675675675675, -14.917127071823199, 43.92523364485981, 797.0646807817703, 40.38601832303061, 622.3082147337067, 39.7566472750829, -21.71706663001825, -15.5700921680006, -9.615247912906304, -9.193512223411584, 5.48, 3.91);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('035720', '2026-07-31', 67.25227509294336, 44.68554981206855, 85.97392335045436, 21.725353709414115, 59.481641170662, 102, 7.471264367816088, -23.43909928352098, -38.68852459016393, 29.41505928637782, 15.755195963562274, 40.19003438523674, 17.696148152448398, 15.407876867360798, 39.8207333635129, 30.907499622755395, 63.31250943111514, 21.75, 18.49);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('035720', '2026-08-03', 64.83345841628277, 44.91068611968314, 85.4304897314376, 21.69452562853578, 58.69585266248691, 109, 5.531914893617018, -23.140495867768596, -39.80582524271845, 29.41505928637782, 15.755195963562274, 40.19003438523674, 17.696148152448398, 7.607910722196263, 33.06592066218012, 35.143729253773, 68.36803427413237, 21.75, 18.49);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('035720', '2026-08-04', 64.56598720736054, 45.064383637728035, 82.4960505529226, 21.65710878946173, 57.9320539525898, 135, 6.760563380281681, -19.87315010570825, -39.067524115755624, 29.41505928637782, 15.755195963562274, 40.19003438523674, 17.696148152448398, 4.395139674787063, 33.049615819882064, 35.71624277800941, 65.43629757579367, 21.75, 18.49);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('035720', '2026-08-05', 63.56343112716134, 45.00806289639111, 58.91390205371248, 21.869252751605693, 51.73725214047398, 313, 7.162921348314599, -19.002123142250525, -38.26860841423948, 29.41505928637782, 15.755195963562274, 40.19003438523674, 17.696148152448398, -9.47235749747037, 22.84360077397824, 24.174985354769614, 66.34082261815298, 21.75, 18.49);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('035720', '2026-08-06', 63.95314363462897, 45.213992733665506, 58.11216429699842, 21.795779793823705, 51.7184596008036, 323, 8.192090395480234, -17.278617710583156, -38.02588996763754, 29.41505928637782, 15.755195963562274, 40.19003438523674, 17.696148152448398, -13.426106022562506, 20.792516886515543, 17.562565571548134, 66.84938289068855, 21.75, 18.49);
INSERT INTO score_history_v2 (stock_code, score_date, momentum_score, growth_score, flow_score, value_score, total_score, rank, r1m, r3m, r6m, op_yoy_cur, op_yoy_next, ni_yoy_cur, ni_yoy_next, inst_5d, inst_20d, frgn_5d, frgn_20d, per_cur, per_next) VALUES ('035720', '2026-08-07', 68.49363622322245, 45.53902546635105, 64.37598736176936, 17.78810016468899, 54.35955663710084, 255, 15.15151515151516, -11.8232044198895, -35.016286644951144, 30.763346265231405, 15.116376248380758, 36.132983377077885, 21.931607580558364, -2.0611149143700955, 28.231560008826072, 16.838001912316336, 73.60324528002987, 25.35, 20.8);
INSERT INTO consensus_revision_daily (stock_code, base_date, target_period, opinion, revenue, op, ni, eps, per, bps, pbr, roe, collected_date) VALUES ('000660', '2026-08-04', '2026/12', 4.0, 3456048.0, 2663757.0, 2499825.0, 345914, 4.56, 514257, 3.07, 101.22, '2026-08-05');
INSERT INTO consensus_revision_daily (stock_code, base_date, target_period, opinion, revenue, op, ni, eps, per, bps, pbr, roe, collected_date) VALUES ('000660', '2026-08-05', '2026/12', 4.0, 3456048.0, 2663757.0, 2499825.0, 345914, 4.82, 514257, 3.24, 101.22, '2026-08-06');
INSERT INTO consensus_revision_daily (stock_code, base_date, target_period, opinion, revenue, op, ni, eps, per, bps, pbr, roe, collected_date) VALUES ('000660', '2026-08-06', '2026/12', 4.0, 3456048.0, 2663757.0, 2499825.0, 345914, 4.32, 514257, 2.91, 101.22, '2026-08-07');
INSERT INTO consensus_revision_daily (stock_code, base_date, target_period, opinion, revenue, op, ni, eps, per, bps, pbr, roe, collected_date) VALUES ('005930', '2026-08-04', '2026/12', 4.04, 7378931.0, 3911296.0, 3197005.0, 47929, 5.01, 109524, 2.19, 55.9, '2026-08-05');
INSERT INTO consensus_revision_daily (stock_code, base_date, target_period, opinion, revenue, op, ni, eps, per, bps, pbr, roe, collected_date) VALUES ('005930', '2026-08-05', '2026/12', 4.04, 7378931.0, 3911296.0, 3197005.0, 47929, 5.13, 109524, 2.25, 55.9, '2026-08-06');
INSERT INTO consensus_revision_daily (stock_code, base_date, target_period, opinion, revenue, op, ni, eps, per, bps, pbr, roe, collected_date) VALUES ('005930', '2026-08-06', '2026/12', 4.04, 7378931.0, 3911296.0, 3197005.0, 47929, 4.81, 109524, 2.1, 55.9, '2026-08-07');
INSERT INTO consensus_revision_daily (stock_code, base_date, target_period, opinion, revenue, op, ni, eps, per, bps, pbr, roe, collected_date) VALUES ('035720', '2026-08-04', '2026/12', 4.0, 82507.0, 9474.0, 6890.0, 1556, 24.36, 27059, 1.4, 5.93, '2026-08-05');
INSERT INTO consensus_revision_daily (stock_code, base_date, target_period, opinion, revenue, op, ni, eps, per, bps, pbr, roe, collected_date) VALUES ('035720', '2026-08-05', '2026/12', 4.0, 82507.0, 9474.0, 6890.0, 1556, 24.52, 27059, 1.41, 5.93, '2026-08-06');
INSERT INTO consensus_revision_daily (stock_code, base_date, target_period, opinion, revenue, op, ni, eps, per, bps, pbr, roe, collected_date) VALUES ('035720', '2026-08-06', '2026/12', 4.0, 82562.0, 9572.0, 6691.0, 1511, 25.35, 26965, 1.42, 5.77, '2026-08-07');
INSERT INTO consensus_revision_compare (stock_code, target_period, opinion_1w, opinion_1m, opinion_3m, opinion_1y, revenue_1w, revenue_1m, revenue_3m, revenue_1y, op_1w, op_1m, op_3m, op_1y, ni_1w, ni_1m, ni_3m, ni_1y, eps_1w, eps_1m, eps_3m, eps_1y, per_1w, per_1m, per_3m, per_1y, bps_1w, bps_1m, bps_3m, bps_1y, pbr_1w, pbr_1m, pbr_3m, pbr_1y, roe_1w, roe_1m, roe_3m, roe_1y) VALUES ('000660', '2026/12', NULL, NULL, NULL, NULL, 3460728.0, 3460728.0, 3360275.0, 972389.0, 2660325.0, 2660325.0, 2559996.0, 417017.0, 2480259.0, 2480259.0, 2135538.0, 333094.0, 343207, 343207, 295506, 46092, 5.01, 5.01, 7.89, 5.84, 511307, 511307, 458391, 187112, 3.36, 3.36, 5.09, 1.44, 100.87, 100.87, 94.21, 27.82);
INSERT INTO consensus_revision_compare (stock_code, target_period, opinion_1w, opinion_1m, opinion_3m, opinion_1y, revenue_1w, revenue_1m, revenue_3m, revenue_1y, op_1w, op_1m, op_3m, op_1y, ni_1w, ni_1m, ni_3m, ni_1y, eps_1w, eps_1m, eps_3m, eps_1y, per_1w, per_1m, per_3m, per_1y, bps_1w, bps_1m, bps_3m, bps_1y, pbr_1w, pbr_1m, pbr_3m, pbr_1y, roe_1w, roe_1m, roe_3m, roe_1y) VALUES ('005930', '2026/12', NULL, NULL, NULL, NULL, 7378931.0, 7378931.0, 6846861.0, 3317192.0, 3911296.0, 3911296.0, 3516597.0, 387968.0, 3197005.0, 3197005.0, 2874804.0, 359941.0, 47929, 47929, 43098, 5396, 5.48, 5.48, 7.36, 12.92, 109524, 109524, 105485, 67292, 2.4, 2.4, 3.01, 1.04, 55.9, 55.9, 51.46, 8.42);
INSERT INTO consensus_revision_compare (stock_code, target_period, opinion_1w, opinion_1m, opinion_3m, opinion_1y, revenue_1w, revenue_1m, revenue_3m, revenue_1y, op_1w, op_1m, op_3m, op_1y, ni_1w, ni_1m, ni_3m, ni_1y, eps_1w, eps_1m, eps_3m, eps_1y, per_1w, per_1m, per_3m, per_1y, bps_1w, bps_1m, bps_3m, bps_1y, pbr_1w, pbr_1m, pbr_3m, pbr_1y, roe_1w, roe_1m, roe_3m, roe_1y) VALUES ('035720', '2026/12', NULL, NULL, NULL, NULL, 82507.0, 82507.0, 83584.0, 88255.0, 9474.0, 9474.0, 9474.0, 8115.0, 6890.0, 6890.0, 6903.0, 6248.0, 1556, 1556, 1559, 1411, 24.04, 24.04, 26.91, 44.3, 27059, 27059, 26989, 25848, 1.38, 1.38, 1.55, 2.42, 5.93, 5.93, 5.95, 5.64);
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('000660', '2025/12', 'annual', 'actual', 971466.8, 46.76, 472063.2, 429192.9, 58955, 174539, 11.04, 3.73, 44.15, 7.59, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('000660', '2026/12', 'annual', 'estimate', 3456048.3, 255.76, 2663757.1, 2499825.1, 345914, 514257, 4.53, 3.05, 101.22, 3.39, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('000660', '2027/12', 'annual', 'estimate', 5017410.7, 45.18, 3927120.1, 3193602.9, 437185, 941670, 3.58, 1.66, 60.42, 1.74, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('000660', '2028/12', 'annual', 'estimate', 5458393.2, 8.79, 4023514.7, 3314470.6, 453731, 1371230, 3.45, 1.14, 39.47, 1.18, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('005930', '2025/12', 'annual', 'actual', 3336059.4, 10.88, 436010.5, 442609.6, 6564, 63997, 18.27, 1.87, 10.85, 7.53, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('005930', '2026/12', 'annual', 'estimate', 7378930.5, 121.19, 3911296.2, 3197005.5, 47929, 109524, 5.48, 2.4, 55.9, 3.15, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('005930', '2027/12', 'annual', 'estimate', 9458758.0, 28.19, 5490913.0, 4468027.7, 67202, 170240, 3.91, 1.54, 48.63, 1.79, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('005930', '2028/12', 'annual', 'estimate', 10102507.0, 6.81, 5563316.4, 4586704.8, 68987, 234304, 3.81, 1.12, 34.52, 1.34, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('035720', '2025/12', 'annual', 'actual', 80991.5, 2.99, 7320.4, 4914.9, 1110, 25625, 54.13, 2.35, 4.59, 13.18, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('035720', '2026/12', 'annual', 'estimate', 82561.5, 1.94, 9572.4, 6690.8, 1511, 26965, 25.35, 1.42, 5.77, 5.82, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('035720', '2027/12', 'annual', 'estimate', 88497.6, 7.19, 11019.4, 8158.2, 1842, 28864, 20.8, 1.33, 6.61, 5.11, 'IFRS연결');
INSERT INTO consensus_annual (stock_code, period, period_type, data_type, revenue, yoy, op, ni, eps, bps, per, pbr, roe, ev_ebitda, accounting_standard) VALUES ('035720', '2028/12', 'annual', 'estimate', 94899.6, 7.23, 12374.8, 9295.0, 2098, 31038, 18.25, 1.23, 7.02, 4.18, 'IFRS연결');
INSERT INTO financial_summary (stock_code, period, period_type, revenue, op, ni, eps, bps, per, pbr, roe, roa, debt_ratio, fcf, capex, op_margin, ni_margin, dividend_yield, shares, ev_ebitda, yoy, data_type, accounting_standard, gross_profit, total_assets) VALUES ('000660', '2024/12', 'annual', 661930, 234673, 197969, 27182, 107256, 6.4, 1.62, 31.06, 17.98, 62.15, 138504, 159455, 35.45, 29.91, 1.27, 728002365, NULL, NULL, NULL, NULL, 318281, 1198552);
INSERT INTO financial_summary (stock_code, period, period_type, revenue, op, ni, eps, bps, per, pbr, roe, roa, debt_ratio, fcf, capex, op_margin, ni_margin, dividend_yield, shares, ev_ebitda, yoy, data_type, accounting_standard, gross_profit, total_assets) VALUES ('000660', '2025/12', 'annual', 971467, 472063, 429479, 58955, 171751, 11.04, 3.79, 44.15, 29.02, 45.95, 258542, 275189, 48.59, 44.21, 0.46, 728002365, NULL, NULL, NULL, NULL, 586907, 1761077);
INSERT INTO financial_summary (stock_code, period, period_type, revenue, op, ni, eps, bps, per, pbr, roe, roa, debt_ratio, fcf, capex, op_margin, ni_margin, dividend_yield, shares, ev_ebitda, yoy, data_type, accounting_standard, gross_profit, total_assets) VALUES ('000660', '2026/12', 'annual', 3456048, 2663757, 2506086, 345914, 514257, 4.32, 2.91, 101.22, 79.51, 21.31, 1626650, 455624, 77.08, 72.51, 0.62, NULL, 3.21, NULL, 'estimate', '연결', 2915725, 4542739);
INSERT INTO financial_summary (stock_code, period, period_type, revenue, op, ni, eps, bps, per, pbr, roe, roa, debt_ratio, fcf, capex, op_margin, ni_margin, dividend_yield, shares, ev_ebitda, yoy, data_type, accounting_standard, gross_profit, total_assets) VALUES ('005930', '2024/12', 'annual', 3008709, 327260, 344514, 4950, 57981, 10.75, 0.92, 9.03, 7.1, 27.93, 215763, 514064, 10.88, 11.45, 2.72, 5969782550, NULL, NULL, NULL, NULL, 1143086, 5145319);
INSERT INTO financial_summary (stock_code, period, period_type, revenue, op, ni, eps, bps, per, pbr, roe, roa, debt_ratio, fcf, capex, op_margin, ni_margin, dividend_yield, shares, ev_ebitda, yoy, data_type, accounting_standard, gross_profit, total_assets) VALUES ('005930', '2025/12', 'annual', 3336059, 436011, 452068, 6564, 63997, 18.27, 1.87, 10.85, 8.36, 29.94, 377930, 475222, 13.07, 13.55, 1.39, 5919637922, NULL, NULL, NULL, NULL, 1313704, 5669421);
INSERT INTO financial_summary (stock_code, period, period_type, revenue, op, ni, eps, bps, per, pbr, roe, roa, debt_ratio, fcf, capex, op_margin, ni_margin, dividend_yield, shares, ev_ebitda, yoy, data_type, accounting_standard, gross_profit, total_assets) VALUES ('005930', '2026/12', 'annual', 7378931, 3911296, 3268572, 47929, 109524, 4.81, 2.1, 55.9, 43.68, 26.39, 2454844, 720508, 53.01, 44.3, 3.34, NULL, 2.69, NULL, 'estimate', '연결', 5290577, 9295533);
INSERT INTO financial_summary (stock_code, period, period_type, revenue, op, ni, eps, bps, per, pbr, roe, roa, debt_ratio, fcf, capex, op_margin, ni_margin, dividend_yield, shares, ev_ebitda, yoy, data_type, accounting_standard, gross_profit, total_assets) VALUES ('035720', '2024/12', 'annual', 78640, 4953, -1619, 124, 23100, 306.86, 1.65, 0.56, -0.64, 84.85, 8708, 3796, 6.3, -2.06, 0.18, 443662117, NULL, NULL, NULL, NULL, 78640, 257730);
INSERT INTO financial_summary (stock_code, period, period_type, revenue, op, ni, eps, bps, per, pbr, roe, roa, debt_ratio, fcf, capex, op_margin, ni_margin, dividend_yield, shares, ev_ebitda, yoy, data_type, accounting_standard, gross_profit, total_assets) VALUES ('035720', '2025/12', 'annual', 80991, 7320, 5180, 1110, 25625, 54.13, 2.35, 4.59, 1.93, 82.49, 9256, 4792, 9.04, 6.4, 0.12, 442495220, NULL, NULL, NULL, NULL, 80991, 277835);
INSERT INTO financial_summary (stock_code, period, period_type, revenue, op, ni, eps, bps, per, pbr, roe, roa, debt_ratio, fcf, capex, op_margin, ni_margin, dividend_yield, shares, ev_ebitda, yoy, data_type, accounting_standard, gross_profit, total_assets) VALUES ('035720', '2026/12', 'annual', 82562, 9572, 7924, 1511, 26965, 25.35, 1.42, 5.77, 2.79, 83.03, 8860, 4804, 11.59, 9.6, 0.2, NULL, 5.82, NULL, 'estimate', '연결', 82575, 290419);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('001', '2026-08-05', 660348, 667466, 654027, 659826, 338500);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('001', '2026-08-06', 647875, 655094, 623832, 629638, 284501);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('001', '2026-08-07', 636507, 641560, 615873, 625877, 299377);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('101', '2026-08-05', 79493, 80349, 78332, 79959, 587546);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('101', '2026-08-06', 79744, 81354, 77908, 80167, 554382);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('101', '2026-08-07', 80762, 81480, 77660, 79881, 509894);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('201', '2026-08-05', 104240, 105319, 102911, 103859, 148096);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('201', '2026-08-06', 101693, 102869, 97574, 98292, 149108);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('201', '2026-08-07', 99475, 100461, 96079, 97473, 145454);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('603', '2026-08-05', 7875, 7903, 7851, 7855, 0);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('603', '2026-08-06', 7696, 7730, 7667, 7717, 0);
INSERT INTO market_indices (index_code, trade_date, open, high, low, close, volume) VALUES ('603', '2026-08-07', 7648, 7648, 7558, 7559, 0);
INSERT INTO market_breadth (index_code, trade_date, advancing, declining, unchanged, limit_up, limit_down) VALUES ('001', '2026-08-06', 490, 381, 44, 3, 0);
INSERT INTO market_breadth (index_code, trade_date, advancing, declining, unchanged, limit_up, limit_down) VALUES ('001', '2026-08-07', 554, 322, 36, 3, 0);
INSERT INTO market_breadth (index_code, trade_date, advancing, declining, unchanged, limit_up, limit_down) VALUES ('101', '2026-08-06', 736, 893, 95, 7, 0);
INSERT INTO market_breadth (index_code, trade_date, advancing, declining, unchanged, limit_up, limit_down) VALUES ('101', '2026-08-07', 808, 829, 84, 6, 0);
INSERT INTO market_investor_flows (index_code, trade_date, securities, insurance, investment_trust, bank, pension_fund, private_equity, foreign_investor, individual, institution_total) VALUES ('001', '2026-08-06', 6459, 479, -7798, -113, 471, -1398, -38661, 39257, -1723);
INSERT INTO market_investor_flows (index_code, trade_date, securities, insurance, investment_trust, bank, pension_fund, private_equity, foreign_investor, individual, institution_total) VALUES ('001', '2026-08-07', 8648, 525, -369, -78, -1165, 111, -9493, 1337, 7825);
INSERT INTO market_investor_flows (index_code, trade_date, securities, insurance, investment_trust, bank, pension_fund, private_equity, foreign_investor, individual, institution_total) VALUES ('101', '2026-08-06', 1303, 32, -17, 1, 40, 307, -2974, 1350, 1638);
INSERT INTO market_investor_flows (index_code, trade_date, securities, insurance, investment_trust, bank, pension_fund, private_equity, foreign_investor, individual, institution_total) VALUES ('101', '2026-08-07', -1444, 44, 208, 0, -5, 83, -2934, 3860, -1117);
INSERT INTO market_regime (trade_date, regime_label, composite_score, sub_scores, flow_regime, flow_z, vol_regime, vkospi, macro_regime, usd_krw, spread_10y_3y, theme_regime, updated_at) VALUES ('2026-08-06', 'NEUTRAL', -0.0475, '{"signals": {"price_zscore": -0.9102, "ma_cross": 1.0, "ad_ratio": 0.1251, "new_high_low": 0.0, "foreign_z": -0.8355, "institution_z": -0.103, "contrarian": 1.0, "vkospi_level": -0.6667, "vkospi_change": 0.4798, "program_z": -0.8092, "divergence": 0.8662}, "categories": {"momentum": -0.1461, "breadth": 0.0751, "flow": -0.0651, "volatility": -0.0934, "program_divergence": 0.0285}}', '강한 순매도', -0.8355, 'EXTREME', 77.17, 'GOLDILOCKS', 1424.8, 0.453, 'MIXED', '2026-08-06T13:16:36');
INSERT INTO market_regime (trade_date, regime_label, composite_score, sub_scores, flow_regime, flow_z, vol_regime, vkospi, macro_regime, usd_krw, spread_10y_3y, theme_regime, updated_at) VALUES ('2026-08-07', 'NEUTRAL', 0.0302, '{"signals": {"price_zscore": -0.9078, "ma_cross": 1.0, "ad_ratio": 0.2648, "new_high_low": 0.0, "foreign_z": -0.257, "institution_z": 0.407, "contrarian": 1.0, "vkospi_level": -0.6429, "vkospi_change": 0.4771, "program_z": -0.2853, "divergence": -0.1525}, "categories": {"momentum": -0.1447, "breadth": 0.1589, "flow": 0.3193, "volatility": -0.0829, "program_divergence": -0.2189}}', '순매도', -0.257, 'EXTREME', 75.59, 'GOLDILOCKS', 1418.8, 0.462, 'MIXED', '2026-08-07T13:14:38');
INSERT INTO market_inflection (trade_date, probability, direction, d2_value, confirmed_prob, confirmed_dir, consecutive_days, alert_level, cluster_strength, cluster_grade, updated_at, confirmed_at) VALUES ('2026-08-06', 0.9997, -1, -0.0547843914, NULL, NULL, 4, '강화', 0.9998, '강', '2026-08-06T13:16:54', NULL);
INSERT INTO market_inflection (trade_date, probability, direction, d2_value, confirmed_prob, confirmed_dir, consecutive_days, alert_level, cluster_strength, cluster_grade, updated_at, confirmed_at) VALUES ('2026-08-07', 0.998, 1, 0.0343520574, NULL, NULL, 1, '감지', 0.998, '강', '2026-08-07T13:14:50', NULL);
INSERT INTO program_trading (index_code, trade_date, arb_net, non_arb_net, total_net) VALUES ('001', '2026-08-06', 175313, -2301006, -2125693);
INSERT INTO program_trading (index_code, trade_date, arb_net, non_arb_net, total_net) VALUES ('001', '2026-08-07', 87719, -739068, -651349);
INSERT INTO sector_daily (sector_code, sector_name, trade_date, close, change_rate, volume, advancing, declining) VALUES ('001', '종합(KOSPI)', '2026-08-06', -6296, -4.58, 284501, 490, 381);
INSERT INTO sector_daily (sector_code, sector_name, trade_date, close, change_rate, volume, advancing, declining) VALUES ('001', '종합(KOSPI)', '2026-08-07', -6258, -0.6, 299377, 554, 322);
INSERT INTO macro_data (indicator, trade_date, value) VALUES ('KTB_10Y', '2026-08-06', 4.195);
INSERT INTO macro_data (indicator, trade_date, value) VALUES ('KTB_10Y', '2026-08-07', 4.208);
INSERT INTO macro_data (indicator, trade_date, value) VALUES ('KTB_3Y', '2026-08-06', 3.742);
INSERT INTO macro_data (indicator, trade_date, value) VALUES ('KTB_3Y', '2026-08-07', 3.746);
INSERT INTO macro_data (indicator, trade_date, value) VALUES ('USD_KRW', '2026-08-06', 1424.8);
INSERT INTO macro_data (indicator, trade_date, value) VALUES ('USD_KRW', '2026-08-07', 1418.8);
INSERT INTO stock_info (stock_code, snapshot_date, market_cap, foreign_ownership_pct, beta, floating_ratio, high_52w, low_52w) VALUES ('000660', '2026-08-07', 10920861, 51.03, 1.8, 75.06, 2987000, 245000);
INSERT INTO stock_info (stock_code, snapshot_date, market_cap, foreign_ownership_pct, beta, floating_ratio, high_52w, low_52w) VALUES ('005930', '2026-08-07', 13475672, 46.68, 1.23, 75.59, 374500, 67500);
INSERT INTO stock_info (stock_code, snapshot_date, market_cap, foreign_ownership_pct, beta, floating_ratio, high_52w, low_52w) VALUES ('035720', '2026-08-07', 169662, 28.94, 0.34, 75.69, 69700, 32250);
INSERT INTO analyst_opinions (stock_code, snapshot_date, opinion_score, target_price, estimated_eps, estimated_per, analyst_count) VALUES ('000660', '2026-08-07', 4.0, 3322083, 345914, 4.32, 24);
INSERT INTO analyst_opinions (stock_code, snapshot_date, opinion_score, target_price, estimated_eps, estimated_per, analyst_count) VALUES ('005930', '2026-08-07', 4.04, 493542, 47929, 4.81, 24);
INSERT INTO analyst_opinions (stock_code, snapshot_date, opinion_score, target_price, estimated_eps, estimated_per, analyst_count) VALUES ('035720', '2026-08-07', 4.0, 63000, 1511, 25.35, 16);
INSERT INTO column_units (table_name, column_name, unit, source) VALUES ('daily_prices', 'adj_close', '원', 'kiwoom');
INSERT INTO column_units (table_name, column_name, unit, source) VALUES ('daily_prices', 'amount', '백만원', 'kiwoom');
INSERT INTO column_units (table_name, column_name, unit, source) VALUES ('daily_prices', 'close', '원', 'kiwoom');
INSERT INTO column_units (table_name, column_name, unit, source) VALUES ('daily_prices', 'high', '원', 'kiwoom');
INSERT INTO column_units (table_name, column_name, unit, source) VALUES ('daily_prices', 'low', '원', 'kiwoom');
INSERT INTO column_units (table_name, column_name, unit, source) VALUES ('daily_prices', 'open', '원', 'kiwoom');
INSERT INTO column_units (table_name, column_name, unit, source) VALUES ('daily_prices', 'volume', '주', 'kiwoom');
