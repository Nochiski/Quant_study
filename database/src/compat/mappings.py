"""v3 `quant.db` 9표 ← equity/stage 매핑 선언 (플랜 `2026-09-24-v3-merge.md` §5 T1.2 2).

표마다 ① 원천(equity 판 또는 stage 판) ② duckdb SELECT ③ v3 컬럼 순서 ④ PK
⑤ `retire_when`(만료 조건) ⑥ `null_columns`(소스에 재료가 없어 NULL 로 두는 v3 열)을 선언한다.
SQL 은 `str.format` 자리를 쓴다 — 원천 표 실명 자리에는 `read_parquet([...])` 가, 나머지
`{date}`·`{from_date}`·`{snap_from}`·`{consensus_asof}`·`{asof_ym}`·`{exported_at}` 자리에는
`quant_db.export` 가 검증한 스칼라가 들어간다. **정규식에 `{n}` 수량자를 쓰지 않는다**
(`str.format` 이 자리로 읽는다 — `\\d\\d\\d\\d` 로 풀어 쓴다).

단위 환산 상수는 전부 `units.py` 가 정본이다(§1-4 GAP-3). 여기서는 그 상수를 SQL 에 박는다.

공통 규약
  · 날짜는 `CAST(… AS VARCHAR)` 로 'YYYY-MM-DD' 문자열을 만든다 — sqlite3 는 `date` 를
    못 바인딩한다.
  · 숫자는 전부 명시 CAST(BIGINT/DOUBLE) 한다 — duckdb DECIMAL 은 sqlite3 가 못 바인딩한다.
  · 결측은 결측이다. 소스에 없는 v3 열은 NULL 로 두고 `null_columns` 에 적는다(0 채움 금지).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import units
from .units import KRW_PER_EOK, KRW_PER_MN

EQUITY = "equity"
STAGE = "stage"


@dataclass(frozen=True)
class TableMapping:
    """v3 표 1개를 채우는 선언."""

    v3_table: str
    source_kind: str | None          # 'equity' | 'stage' | None(이번 태스크에서는 안 채움)
    sources: tuple[str, ...]         # 원천 표 실명(source_kind 루트 기준)
    columns: tuple[str, ...]         # v3 컬럼 순서 = SELECT 출력 순서
    pk: tuple[str, ...]
    sql: str
    retire_when: str
    null_columns: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""
    # 다른 루트의 원천 — (kind, 표). `financial_summary` 가 stage WISE 와 equity DART 를
    # 함께 읽는다(T1.5 · D-10). SQL 자리 이름은 `sources` 와 같은 규칙이다.
    cross_sources: tuple[tuple[str, str], ...] = ()


# ── daily_prices ────────────────────────────────────────────────────────────────────────────
# equity `price_daily`(OHLCV·거래대금) + `price_adj_daily.adj_close`(전방 조정).
# v3 `adj_close` 는 소급 조정이지만 모멘텀은 **비율**만 쓰므로 전방 조정과 결과가 같다
# (EQUITY_DESIGN §7 · 플랜 §1-4 GAP-4 — v3 쪽은 한 번 채워진 종목을 갱신하지 않아 최근 기업행위
#  종목에서 v3 가 틀리다. 우리 값이 정본이고 차이는 비교 도구가 `v3_defect` 로 분류한다).
# `price_daily.basis`('krx'|'evening')는 v3 스키마에 자리가 없다 — `_compat_meta.basis` 에만 남는다.
# ⚠ GAP-1: v3 `daily_prices` 는 open·high·low·close·volume 이 **NOT NULL** 이고(v3
#   `backend/db/schema.py:16-27`, 완화 ALTER 없음) 우리 저녁 잠정 T 행(`basis='evening'`)은
#   KRX 기본정보가 없어 open/high/low/value_krw 가 NULL 이다. 그대로 넣으면 표 트랜잭션이
#   통째로 깨지므로 **지금은 `basis='krx'` 행만 내보낸다**. 건너뛴 저녁 행 수는
#   `_compat_meta.n_evening_rows_skipped` 에 남는다.
#   → D-8 결정 뒤 evening 행 처리 추가(`docs/COMPAT_LAYER.md` §4).
#   M1 의 G-M2 비교는 확정판(morning, 전 행 krx)만 쓰므로 영향이 없다.
_DAILY_PRICES_SQL = f"""
SELECT
    p.ticker                                       AS stock_code,
    CAST(p.date AS VARCHAR)                        AS trade_date,
    CAST(p.open AS BIGINT)                         AS open,
    CAST(p.high AS BIGINT)                         AS high,
    CAST(p.low AS BIGINT)                          AS low,
    CAST(p.close AS BIGINT)                        AS close,
    CAST(p.volume_shr AS BIGINT)                   AS volume,
    CAST(round(p.value_krw / {KRW_PER_MN}.0) AS BIGINT)  AS amount,
    CAST(a.adj_close AS DOUBLE)                    AS adj_close
FROM {{price_daily}} p
LEFT JOIN {{price_adj_daily}} a ON a.ticker = p.ticker AND a.date = p.date
WHERE p.basis = 'krx'
  AND p.date >= DATE '{{from_date}}' AND p.date <= DATE '{{date}}'
"""

# 같은 창에서 `basis='evening'` 이라 제외한 행 수 — `_compat_meta.n_evening_rows_skipped`.
# D-8 결정 전까지 "저녁 T 행이 v3 표에 없다" 는 사실을 숫자로 남긴다(조용한 실패 금지).
EVENING_SKIPPED_SQL = """
SELECT count(*) FROM {price_daily} p
WHERE p.basis = 'evening'
  AND p.date >= DATE '{from_date}' AND p.date <= DATE '{date}'
"""

# ── stocks ──────────────────────────────────────────────────────────────────────────────────
# 종목당 1행 스냅샷. 유니버스는 `universe_daily` 의 as_of 이하 최신 세션 행
# (status listed·suspended). 시총은 같은 창의 `price_daily.mktcap_krw` 최신 값 → 억원.
# 이름·상장일·폐지일은 `security`.
# **D-11 유니버스 미러**: v3 `stocks` 는 KOSPI·KOSDAQ 의 `sec_type IN ('common', 'spac')` 다.
#   09-23 그림자 실측 — v3 active 2,533 = common 2,413 + spac 117. 우리가 더 넣었던 236 은
#   preferred 114 · reit 23 · foreign 12 · dr 10 · fund 3 이고 v3 수집기(키움 ka10099 + KIS MST)가
#   애초에 담지 않는 종류다. 유니버스를 v3 와 같게 맞춰야 G-M2 ①의 티커 집합 차이가 선다.
# `sector` 는 WICS L1 명(`sector_snapshot`)을 넣는다 — v3 는 KRX 업종명이라 **값이 다르다**.
#   T1.4 소비자 감사에서 브리핑·리서치센터가 sector 를 표시용으로만 쓰는지 확인한 뒤
#   WICS 유지 / KRX 업종으로 교체를 확정한다(플랜 §5 T1.2 2).
# `updated_at` 은 v3 가 `datetime('now')` 로 채우던 자리 — 우리는 export 시각을 넣어
# 신선도를 남긴다.
_STOCKS_SQL = f"""
WITH uni AS (
    SELECT u.ticker, u.market, u.sec_type,
           row_number() OVER (PARTITION BY u.ticker ORDER BY u.date DESC) AS rn
    FROM {{universe_daily}} u
    WHERE u.status IN ('listed', 'suspended')
      AND u.date >= DATE '{{snap_from}}' AND u.date <= DATE '{{date}}'
),
cap AS (
    SELECT p.ticker, p.mktcap_krw,
           row_number() OVER (PARTITION BY p.ticker ORDER BY p.date DESC) AS rn
    FROM {{price_daily}} p
    WHERE p.mktcap_krw IS NOT NULL
      AND p.date >= DATE '{{snap_from}}' AND p.date <= DATE '{{date}}'
),
sect AS (
    SELECT s.ticker, s.wics_l1_nm,
           row_number() OVER (PARTITION BY s.ticker ORDER BY s.snapshot_date DESC) AS rn
    FROM {{sector_snapshot}} s
    WHERE s.snapshot_date <= DATE '{{date}}'
)
SELECT
    u.ticker                                              AS stock_code,
    v.name_current                                        AS stock_name,
    u.market                                              AS market,
    sect.wics_l1_nm                                       AS sector,
    CAST(round(cap.mktcap_krw / {KRW_PER_EOK}.0) AS BIGINT)
                                                          AS market_cap,
    CAST(v.list_date AS VARCHAR)                          AS listed_date,
    CASE WHEN v.delist_date IS NULL OR v.delist_date > DATE '{{date}}' THEN 1 ELSE 0 END
                                                          AS is_active,
    CAST(v.delist_date AS VARCHAR)                        AS delisted_date,
    '{{exported_at}}'                                     AS updated_at
FROM uni u
JOIN {{security}} v ON v.ticker = u.ticker
LEFT JOIN cap  ON cap.ticker = u.ticker AND cap.rn = 1
LEFT JOIN sect ON sect.ticker = u.ticker AND sect.rn = 1
WHERE u.rn = 1
  AND u.sec_type IN ('common', 'spac')          -- D-11
  AND u.market IN ('KOSPI', 'KOSDAQ')           -- v3 CHECK 제약과 같은 어휘
"""

# ── investor_detail_flows ───────────────────────────────────────────────────────────────────
# equity `flow_daily` 13주체 → v3 12열(원 → 백만원). `natfor_krw`(내외국인)는 v3 에 자리가 없어
# 버린다. `orgn_krw`(기관계)는 기관 7주체 합과 **다르다**(FIELD_MAP GAP-03) — v3
# `institution_total` 도 같은 성격의 합계 컬럼이라 그대로 나른다. 재계산하지 않는다.
# `flow_daily` grain 은 (date, ticker, src) 라 셀 하나에 두 원천 행이 올 수 있다(실측 겹침 0).
# v3 PK 는 (stock_code, trade_date) 하나뿐이므로 키움 우선으로 **결정적으로** 하나를 고른다.
# 미측정 셀(전 주체 NULL)은 v3 에 행을 만들지 않는다 — v3 는 수집한 행만 가진다.
# ⚠ 저녁 판에는 T 행이 아예 없다 — `flow_daily` 격자가 T-1 까지라 T 원장 행이 `off_grid` 로
#   격리된다. daily_prices 의 저녁 T 행과 같은 D-8 범위이며 여기서는 손대지 않는다.
_FLOW_COLS = units.FLOW_SUBJECTS        # 주체 대응의 정본은 units.py 다(중복 선언 금지)
_FLOW_SELECT = ",\n       ".join(
    f"CAST(round(f.{src} / {KRW_PER_MN}.0) AS BIGINT) AS {dst}" for dst, src in _FLOW_COLS)
_FLOW_ANY = ", ".join(f"f.{src}" for _, src in _FLOW_COLS)
_INVESTOR_FLOWS_SQL = f"""
WITH picked AS (
    SELECT f.*, row_number() OVER (
               PARTITION BY f.ticker, f.date
               ORDER BY CASE WHEN f.src = 'kiwoom' THEN 0 ELSE 1 END, f.src) AS rn
    FROM {{flow_daily}} f
    WHERE f.date >= DATE '{{from_date}}' AND f.date <= DATE '{{date}}'
      AND coalesce({_FLOW_ANY}) IS NOT NULL
)
SELECT f.ticker                   AS stock_code,
       CAST(f.date AS VARCHAR)    AS trade_date,
       {_FLOW_SELECT}
FROM picked f
WHERE f.rn = 1
"""

# ── 컨센서스 리비전 (한시 예외) ──────────────────────────────────────────────────────────────
# equity `consensus_daily` 에 영업이익·순이익이 없어(B-23 · 플랜 §1-4 GAP-6) stage
# `stg_consensus_matrix` 를 **직독**한다. equity 층을 건너뛰는 유일한 매핑이다.
#   만료: M2 T2.6 이 equity `consensus_revision`(S17b)을 올리면 그 표로 바꾸고 이 예외를 없앤다.
# 좌표: (ticker, fetched_date, target_period, acc_cd, lookback_idx).
# acc_cd 9종(STAGE_DESIGN §4 WISE 실측 09-02):
#   610100 투자의견 · 121000 매출액 · 121500 영업이익 · 122710 순이익 · 312000 EPS ·
#   382000 PER · 314000 BPS · 382400 PBR · 211500 ROE
# lookback: VAL1=current · VAL2=1w · VAL3=1m · VAL4=3m · VAL5=1y (`parsers.py:268`).
# as-of: `fetched_date <= {consensus_asof}` 의 종목별 **최신 fetched_date** 한 판.
#   G-M2 에서 v3 09-23 점수의 리비전 입력이 09-22 자료였으므로 `--consensus-asof` 로 맞춘다.
# target_period: WISE 는 종목마다 3개(당해·차기·차차기 — 09-23 실측)를 준다.
#   **as_of 이상인 것 중 최솟값**
#   = 아직 끝나지 않은 가장 가까운 결산기를 고른다(비12월 결산 202605·202903 실재).
#   v3 가 네이버에서 어떤 기를 골랐는지는 문서화돼 있지 않다 — T1.3 서버 대조의 확인 대상이다.
_ACC: tuple[tuple[str, str], ...] = (
    ("opinion", "610100"), ("revenue", "121000"), ("op", "121500"), ("ni", "122710"),
    ("eps", "312000"), ("per", "382000"), ("bps", "314000"), ("pbr", "382400"),
    ("roe", "211500"),
)
_ACC_INT = {"eps", "bps"}          # v3 DDL 이 INTEGER 인 둘
_LOOKBACKS = ("1w", "1m", "3m", "1y")

_MATRIX_PICK = """
WITH cur AS (
    SELECT m.ticker, m.fetched_date, m.target_period, m.target_label, m.base_date,
           m.acc_cd, m.lookback, m.value
    FROM {stg_consensus_matrix} m
    WHERE m.fetched_date <= DATE '{consensus_asof}'
),
latest AS (
    SELECT ticker, max(fetched_date) AS fetched_date FROM cur GROUP BY ticker
),
snap AS (
    SELECT c.* FROM cur c
    JOIN latest l ON l.ticker = c.ticker AND l.fetched_date = c.fetched_date
),
per AS (
    SELECT ticker, min(target_period) AS target_period
    FROM snap WHERE target_period >= '{asof_ym}' GROUP BY ticker
),
sel AS (
    SELECT s.* FROM snap s JOIN per p ON p.ticker = s.ticker
                                     AND p.target_period = s.target_period
)
"""

_REVISION_DAILY_SQL = _MATRIX_PICK + """
SELECT ticker                                 AS stock_code,
       CAST(max(base_date) AS VARCHAR)        AS base_date,
       max(target_label)                      AS target_period,
""" + ",\n".join(
    f"       CAST(max(CASE WHEN acc_cd = '{code}' AND lookback = 'current' THEN value END) AS "
    f"{'BIGINT' if name in _ACC_INT else 'DOUBLE'}) AS {name}" for name, code in _ACC
) + """,
       CAST(max(fetched_date) AS VARCHAR)     AS collected_date
FROM sel
GROUP BY ticker
"""

_REVISION_COMPARE_SQL = _MATRIX_PICK + """
SELECT ticker                                 AS stock_code,
       max(target_label)                      AS target_period,
""" + ",\n".join(
    f"       CAST(max(CASE WHEN acc_cd = '{code}' AND lookback = '{h}' THEN value END) AS "
    f"{'BIGINT' if name in _ACC_INT else 'DOUBLE'}) AS {name}_{h}"
    for name, code in _ACC for h in _LOOKBACKS
) + """
FROM sel
GROUP BY ticker
"""

# ── consensus_annual ────────────────────────────────────────────────────────────────────────
# stage `stg_consensus_annual`(c1050001 T2Y). 종목별 최신 fetched_date 한 판.
# v3 형식: period 'YYYY/MM' · period_type 는 **월이 12 면 annual, 아니면 quarter**
#   (v3 `_wisereport_parsers.py:89-98` 의 규칙 그대로 — 비12월 결산은 v3 에서도 quarter 로 들어가
#    scoring 의 `period_type='annual'` 창에서 빠진다. 같은 동작을 재현해야 비교가 선다).
# data_type: period_kind 'E' → 'estimate', 'A' → 'actual'
#   (v3 `v2_data_loader.py:50-57` 이 'estimate' 만 문자열로 본다).
# 같은 period 에 A·E 가 둘 다 오면 E 를 고른다(v2 의 'estimate 우선'과 같은 방향).
_CONSENSUS_ANNUAL_SQL = """
WITH latest AS (
    SELECT ticker, max(fetched_date) AS fetched_date
    FROM {stg_consensus_annual}
    WHERE fetched_date <= DATE '{consensus_asof}'
    GROUP BY ticker
),
sel AS (
    SELECT a.*, row_number() OVER (
             PARTITION BY a.ticker, a.period
             ORDER BY CASE WHEN a.period_kind = 'E' THEN 0 ELSE 1 END, a.period_label) AS rn
    FROM {stg_consensus_annual} a
    JOIN latest l ON l.ticker = a.ticker AND l.fetched_date = a.fetched_date
    WHERE a.period IS NOT NULL
)
SELECT ticker                                              AS stock_code,
       substr(period, 1, 4) || '/' || substr(period, 5, 2) AS period,
       CASE WHEN substr(period, 5, 2) = '12' THEN 'annual' ELSE 'quarter' END AS period_type,
       CASE WHEN period_kind = 'E' THEN 'estimate' ELSE 'actual' END          AS data_type,
       CAST(revenue AS DOUBLE)                             AS revenue,
       CAST(yoy_pct AS DOUBLE)                             AS yoy,
       CAST(op AS DOUBLE)                                  AS op,
       CAST(ni AS DOUBLE)                                  AS ni,
       CAST(round(eps) AS BIGINT)                          AS eps,
       CAST(round(bps) AS BIGINT)                          AS bps,
       CAST(per AS DOUBLE)                                 AS per,
       CAST(pbr AS DOUBLE)                                 AS pbr,
       CAST(roe_pct AS DOUBLE)                             AS roe,
       CAST(ev_ebitda AS DOUBLE)                           AS ev_ebitda,
       fs_basis                                            AS accounting_standard
FROM sel
WHERE rn = 1
"""

# ── financial_summary (두 원천 합성 — T1.5 · D-10) ──────────────────────────────────────────
# ① stage `stg_fin_wise`(WISE cF3002 재무제표 + cF4002 투자지표, 결정 D-5)
#      → revenue · op · ni · eps · bps · per · pbr · ev_ebitda · dividend_yield · shares ·
#        gross_profit · accounting_standard
# ② equity `fin_std`(DART 표준계정 PIT, S12) — **v3 quality 팩터 입력**
#      → total_assets · roa · debt_ratio · fcf · capex · roe
#   4일 그림자 실측에서 quality 상관이 0.48 뿐이었던 원인이 ②의 부재였다(나머지 팩터는
#   momentum 0.997 · flow 0.996 · revision 0.985 · valuation 0.977). WISE cF3002 는
#   손익계산서 전용이라 재무상태표·현금흐름표 계정이 없다.
#
# 정의(단위는 v3 `financial_summary` 기준 — 금액 억원 · 비율 %):
#   total_assets = total_asset / 1e8                              (원 → 억원)
#   roa          = net_income / total_asset   × 100               (%)
#   debt_ratio   = total_liab / total_equity  × 100               (%)
#   roe          = net_income / total_equity  × 100               (%)
#   capex        = abs(capex_ytd) / 1e8                           (원 → 억원)
#   fcf          = (cf_operating_ytd − abs(capex_ytd)) / 1e8      (원 → 억원)
#   `capex_ytd`(유형자산 취득, 연초누계)는 DART 제출사마다 유출을 음수로 적기도 양수로 적기도
#   한다. CAPEX 는 **취득액(크기)** 이므로 `abs()` 로 부호를 지우고 FCF 에서 뺀다 — 부호 규약을
#   실측으로 확인하기 전까지의 보수적 선택이며, 서버 대조(v3 네이버 CAPEX)로 검증할 항목이다.
#
# 조인·PIT:
#   `fin_std` grain 은 (corp_code, period_end, report_code, fs_div, vintage_kind) 라
#   `security.corp_code` 로 티커에 붙인다(보통주·우선주가 같은 corp_code 를 공유하면 둘 다
#   같은 재무를 받는다 — v3 네이버도 그렇다).
#   연간 = `report_code='11011'`(사업보고서). `period` 는 `period_end` 의 'YYYY/MM' 이라
#   비12월 결산도 자기 결산월로 들어간다(v3 규칙대로 월이 12 가 아니면 period_type='quarter').
#   PIT — `available_date <= {date}` 인 판본만 본다. 같은 (corp_code, period_end) 에 여러
#   판본이면 **연결(CFS) 우선**(v3·WISE 가 IFRS연결 기준이다) → 그 안에서 available_date 최신
#   (정정 공시 반영) → rcept_no 최신.
#   `gross_profit` 은 WISE 값 우선, 없으면 DART(원 → 억원). v3 원천에 가까운 쪽을 남긴다.
#   WISE 확정 행과 DART 행은 (ticker, period) FULL OUTER JOIN 이다 — 한쪽에만 있는 기도
#   행으로 남긴다(DART 만 있는 종목은 quality 입력이라도 채워진다).
# ⚠ 여전히 **없음(NULL)**: op_margin · ni_margin · yoy. 계산은 팩터층 몫이라 두지 않는다.
_FIN_SLOTS = "\n    UNION ALL\n    ".join(
    "SELECT ticker, ep, accode, p_accode, acc_nm, fs_basis, "
    f"period_label_{i} AS label, val_{i} AS val FROM cur" for i in range(1, 7))


def _fin_pick(ep: str, accode: str, top_only: bool = False,
              acc_nm: str | None = None) -> str:
    """accode 피벗 한 칸. `acc_nm` 을 주면 계정명까지 맞는 행만 센다.

    R1 — WISE 는 업종에 따라 **같은 accode 에 다른 계정**을 싣는다. 절단본 실측:
    003540(대신증권)의 cF3002 `200000` 은 '순이자이익' 이고 `200810`·`203170` 은
    하위 계정('단기매매금융자산매매이익'·'자산재평가이익')이다. accode 만 보면 금융업의
    순이자이익이 v3 `financial_summary.revenue` 로 들어가 밸류·퀄리티가 오염된다.
    계정명이 다르면 값을 만들지 않는다(NULL) — 잘못 채우느니 비운다(원칙 ④).
    """
    # DQ-6(2026-09-26 실측): 금융업 템플릿은 같은 계정을 **다른 accode** 에 싣는다 — 당기순이익이
    # 제조업 203170 · 증권 203730 · 은행 202550 · 보험 203250 · 신탁 202290. accode 를 고정하면
    # 은행·증권·보험 33종목의 순이익이 NULL 이 된다. 그래서 `acc_nm` 이 주어지면 **계정명 + 최상위
    # (p_accode IS NULL)** 로 고르고 accode 는 문서용 힌트로만 남긴다(R1 의 반대 방향 오염도 막힌다:
    # 이름이 다르면 안 센다). 계정명이 없으면(cF4002 지표) 종전대로 accode 로 고른다.
    if acc_nm is not None:
        cond = f"ep = '{ep}' AND acc_nm = '{acc_nm}' AND p_accode IS NULL"
    else:
        cond = f"ep = '{ep}' AND accode = '{accode}'"
        if top_only:
            cond += " AND p_accode IS NULL"
    return f"max(CASE WHEN {cond} THEN val END)"


# WISE 확정·추정 두 갈래가 함께 쓰는 v3 컬럼(DART 와 무관한 자리)
_W_COLS = """       CAST(round(w_revenue) AS BIGINT)                       AS revenue,
       CAST(round(w_op) AS BIGINT)                            AS op,
       CAST(round(w_ni) AS BIGINT)                            AS ni,
       CAST(round(w_eps) AS BIGINT)                           AS eps,
       CAST(round(w_bps) AS BIGINT)                           AS bps,
       CAST(w_per AS DOUBLE)                                  AS per,
       CAST(w_pbr AS DOUBLE)                                  AS pbr,"""
_W_TAIL = """       CAST(NULL AS DOUBLE)                                   AS op_margin,
       CAST(NULL AS DOUBLE)                                   AS ni_margin,
       CAST(w_dividend_yield AS DOUBLE)                       AS dividend_yield,
       CAST(round(w_shares) AS BIGINT)                        AS shares,
       CAST(w_ev_ebitda AS DOUBLE)                            AS ev_ebitda,
       CAST(NULL AS DOUBLE)                                   AS yoy,"""

# DQ-11(2026-09-26, 기록만): 아래 `period_type` 은 **v3 미러**다 — 월(mm)이 12 면 annual, 아니면
# quarter. 그래서 비12월 결산 12종목의 사업보고서가 `quarter` 로 들어간다. v3 scoring 의
# `period_type='annual'` 창이 그 종목을 빼는 동작까지 그대로 재현해야 그림자 비교가 서므로 여기서
# 고치지 않는다. 결산월을 옳게 보는 판정은 **모델 층이 `freq` 로** 한다(그림자 컷오버 뒤).
_FINANCIAL_SUMMARY_SQL = f"""
WITH latest AS (
    SELECT ticker, max(fetched_date) AS fetched_date
    FROM {{stg_fin_wise}}
    WHERE fetched_date <= DATE '{{consensus_asof}}'
    GROUP BY ticker
),
cur AS (
    SELECT w.* FROM {{stg_fin_wise}} w
    JOIN latest l ON l.ticker = w.ticker AND l.fetched_date = w.fetched_date
),
slots AS (
    {_FIN_SLOTS}
),
parsed AS (
    SELECT ticker, ep, accode, p_accode, acc_nm, fs_basis, val,
           regexp_extract(label, '(\\d\\d\\d\\d)[./](\\d\\d)', 1) AS yyyy,
           regexp_extract(label, '(\\d\\d\\d\\d)[./](\\d\\d)', 2) AS mm,
           regexp_matches(label, '\\(E\\)')                      AS is_est
    FROM slots
    WHERE label IS NOT NULL AND regexp_matches(label, '\\d\\d\\d\\d[./]\\d\\d')
),
wise AS (
    SELECT ticker,
           yyyy || '/' || mm                                   AS period,
           CASE WHEN mm = '12' THEN 'annual' ELSE 'quarter' END AS period_type,
           is_est,
           {_fin_pick('cF3002', '200000', True, '매출액(수익)')}  AS w_revenue,
           {_fin_pick('cF3002', '201370', True, '영업이익')}      AS w_op,
           {_fin_pick('cF3002', '203170', True, '당기순이익')}    AS w_ni,
           {_fin_pick('cF4002', '312000', True)}                  AS w_eps,
           {_fin_pick('cF4002', '314000', True)}                  AS w_bps,
           {_fin_pick('cF4002', '382100')}                        AS w_per,
           {_fin_pick('cF4002', '382500')}                        AS w_pbr,
           {_fin_pick('cF4002', '331000')}                        AS w_ev_ebitda,
           {_fin_pick('cF4002', '431800')}                        AS w_dividend_yield,
           {_fin_pick('cF4002', '701250')}                        AS w_shares,
           {_fin_pick('cF3002', '200810', True, '매출총이익')}    AS w_gross_profit,
           max(CASE WHEN ep = 'cF3002' THEN fs_basis END)         AS w_fs_basis
    FROM parsed
    GROUP BY ticker, yyyy, mm, is_est
),
fin AS (
    SELECT f.corp_code, f.period_end, f.total_asset, f.total_liab, f.total_equity,
           f.net_income, f.cf_operating_ytd, f.capex_ytd, f.gross_profit,
           row_number() OVER (
               PARTITION BY f.corp_code, f.period_end
               ORDER BY CASE WHEN f.fs_div = 'CFS' THEN 0 ELSE 1 END,
                        f.available_date DESC, f.rcept_no DESC) AS rn
    FROM {{fin_std}} f
    WHERE f.report_code = '11011'
      AND f.available_date <= DATE '{{date}}'
),
dart AS (
    SELECT s.ticker,
           strftime(f.period_end, '%Y/%m')                      AS period,
           CASE WHEN strftime(f.period_end, '%m') = '12' THEN 'annual' ELSE 'quarter' END
                                                                AS period_type,
           f.total_asset  AS d_total_asset,
           f.total_liab   AS d_total_liab,
           f.total_equity AS d_total_equity,
           f.net_income   AS d_net_income,
           f.cf_operating_ytd AS d_cf_op,
           f.capex_ytd    AS d_capex,
           f.gross_profit AS d_gross_profit
    FROM fin f
    JOIN {{security}} s ON s.corp_code = f.corp_code
    WHERE f.rn = 1
),
act AS (
    SELECT coalesce(w.ticker, d.ticker)           AS ticker,
           coalesce(w.period, d.period)           AS period,
           coalesce(w.period_type, d.period_type) AS period_type,
           w.w_revenue, w.w_op, w.w_ni, w.w_eps, w.w_bps, w.w_per, w.w_pbr,
           w.w_ev_ebitda, w.w_dividend_yield, w.w_shares, w.w_gross_profit, w.w_fs_basis,
           d.d_total_asset, d.d_total_liab, d.d_total_equity, d.d_net_income,
           d.d_cf_op, d.d_capex, d.d_gross_profit
    FROM (SELECT * FROM wise WHERE NOT is_est) w
    FULL OUTER JOIN dart d ON d.ticker = w.ticker AND d.period = w.period
)
SELECT ticker                                                 AS stock_code,
       period, period_type,
{_W_COLS}
       CAST(d_net_income / nullif(d_total_equity, 0) * 100 AS DOUBLE)  AS roe,
       CAST(d_net_income / nullif(d_total_asset, 0) * 100 AS DOUBLE)   AS roa,
       CAST(d_total_liab / nullif(d_total_equity, 0) * 100 AS DOUBLE)  AS debt_ratio,
       CAST(round((d_cf_op - abs(d_capex)) / {KRW_PER_EOK}.0) AS BIGINT) AS fcf,
       CAST(round(abs(d_capex) / {KRW_PER_EOK}.0) AS BIGINT)           AS capex,
{_W_TAIL}
       CAST(NULL AS VARCHAR)                                  AS data_type,
       w_fs_basis                                             AS accounting_standard,
       CAST(round(coalesce(w_gross_profit, d_gross_profit / {KRW_PER_EOK}.0)) AS BIGINT)
                                                              AS gross_profit,
       CAST(round(d_total_asset / {KRW_PER_EOK}.0) AS BIGINT)  AS total_assets
FROM act

UNION ALL

-- (E) 슬롯 — 추정치에는 DART 확정 재무가 없다. v3 도 data_type='estimate' 로 갈라 두고
-- scoring 창(`data_type IS NULL`)에서 뺀다.
SELECT ticker                                                 AS stock_code,
       period, period_type,
{_W_COLS}
       CAST(NULL AS DOUBLE)                                   AS roe,
       CAST(NULL AS DOUBLE)                                   AS roa,
       CAST(NULL AS DOUBLE)                                   AS debt_ratio,
       CAST(NULL AS BIGINT)                                   AS fcf,
       CAST(NULL AS BIGINT)                                   AS capex,
{_W_TAIL}
       'estimate'                                             AS data_type,
       w_fs_basis                                             AS accounting_standard,
       CAST(round(w_gross_profit) AS BIGINT)                  AS gross_profit,
       CAST(NULL AS BIGINT)                                   AS total_assets
FROM wise
WHERE is_est
-- 같은 (종목, 기) 에 (E) 슬롯과 확정 슬롯이 둘 다 오면 v3 PK 가 하나뿐이라 뒤에 넣은 행이 남는다.
-- 확정치(data_type NULL)가 scoring 창이므로 NULLS LAST 로 그쪽을 마지막에 넣는다.
ORDER BY stock_code, period, data_type NULLS LAST
"""

# ── 모델 유니버스(사용자 결정 09-24) ────────────────────────────────────────────────────────
# "v3 유니버스는 추정치 데이터가 있는 종목만". v3 엔진 원본은 `stocks.market_cap >= min_market_cap`
# 으로만 유니버스를 자르므로(v3 `backend/scoring/engine.py:71-78`, v2 는 `market_cap > 0`),
# **추정치가 없는 종목의 `market_cap` 을 NULL 로 내보내면** v3 코드를 한 줄도 고치지 않고 같은
# 효과를 낸다. 이름·시장 열은 남으므로 브리핑·뉴스·unitelegram 의 조인은 깨지지 않는다.
# 판정: as_of 이하 최신 `stg_consensus_annual` 판에서 **당해 12월기 추정(period_kind='E')의
# op·ni 가 둘 다 있는** 종목.
ESTIMATE_TICKERS_SQL = """
WITH latest AS (
    SELECT ticker, max(fetched_date) AS fetched_date
    FROM {stg_consensus_annual}
    WHERE fetched_date <= DATE '{consensus_asof}'
    GROUP BY ticker
)
SELECT DISTINCT a.ticker
FROM {stg_consensus_annual} a
JOIN latest l ON l.ticker = a.ticker AND l.fetched_date = a.fetched_date
WHERE a.period_kind = 'E' AND a.period = '{asof_fy}'
  AND a.op IS NOT NULL AND a.ni IS NOT NULL
"""

# ── 선언 ────────────────────────────────────────────────────────────────────────────────────
MAPPINGS: tuple[TableMapping, ...] = (
    TableMapping(
        v3_table="daily_prices",
        source_kind=EQUITY,
        sources=("price_daily", "price_adj_daily"),
        columns=("stock_code", "trade_date", "open", "high", "low", "close", "volume",
                 "amount", "adj_close"),
        pk=("stock_code", "trade_date"),
        sql=_DAILY_PRICES_SQL,
        retire_when="브리핑 kr_market·리서치센터 S1/S2/S11·가설 store·unitelegram 이 "
                    "equity price_daily 직독으로 옮겨진 뒤",
        note="basis='krx' 행만 내보낸다 — 저녁 잠정 T 행은 v3 NOT NULL(open·high·low)을 "
             "못 채운다(GAP-1). D-8 결정 뒤 처리 추가(docs/COMPAT_LAYER.md §4)",
    ),
    TableMapping(
        v3_table="stocks",
        source_kind=EQUITY,
        sources=("universe_daily", "security", "price_daily", "sector_snapshot"),
        columns=("stock_code", "stock_name", "market", "sector", "market_cap", "listed_date",
                 "is_active", "delisted_date", "updated_at"),
        pk=("stock_code",),
        sql=_STOCKS_SQL,
        retire_when="브리핑·리서치센터·뉴스 preview/naver_ir·api health·unitelegram kael_db 가 "
                    "equity security/universe_daily 직독으로 옮겨진 뒤",
        note="sector 는 WICS L1 명 — v3 는 KRX 업종명이라 값이 다르다(T1.4 확인 대상)",
    ),
    TableMapping(
        v3_table="investor_detail_flows",
        source_kind=EQUITY,
        sources=("flow_daily",),
        columns=("stock_code", "trade_date") + tuple(c for c, _ in _FLOW_COLS),
        pk=("stock_code", "trade_date"),
        sql=_INVESTOR_FLOWS_SQL,
        retire_when="리서치센터 S1/S11·drilldown·가설·unitelegram 이 equity flow_daily "
                    "직독으로 옮겨진 뒤",
        note="natfor_krw(내외국인)는 v3 에 자리가 없어 버린다. orgn 은 7주체 합과 다르다(GAP-03)",
    ),
    TableMapping(
        v3_table="consensus_revision_daily",
        source_kind=STAGE,
        sources=("stg_consensus_matrix",),
        columns=("stock_code", "base_date", "target_period") + tuple(n for n, _ in _ACC) +
                ("collected_date",),
        pk=("stock_code", "base_date"),
        sql=_REVISION_DAILY_SQL,
        retire_when="만료: M2 T2.6 equity consensus_revision(S17b) 승격 시 — "
                    "그때 이 stage 직독 예외를 없앤다(B-23 종결)",
        note="한시 예외: equity 층을 건너뛰고 stage 를 직독하는 유일한 매핑(GAP-6)",
    ),
    TableMapping(
        v3_table="consensus_revision_compare",
        source_kind=STAGE,
        sources=("stg_consensus_matrix",),
        columns=("stock_code", "target_period") +
                tuple(f"{n}_{h}" for n, _ in _ACC for h in _LOOKBACKS),
        pk=("stock_code",),
        sql=_REVISION_COMPARE_SQL,
        retire_when="만료: M2 T2.6 equity consensus_revision(S17b) 승격 시 — "
                    "그때 이 stage 직독 예외를 없앤다(B-23 종결)",
        note="한시 예외: 위와 같은 좌표에서 lookback 1w/1m/3m/1y 축만 뽑는다",
    ),
    TableMapping(
        v3_table="consensus_annual",
        source_kind=STAGE,
        sources=("stg_consensus_annual",),
        columns=("stock_code", "period", "period_type", "data_type", "revenue", "yoy", "op",
                 "ni", "eps", "bps", "per", "pbr", "roe", "ev_ebitda", "accounting_standard"),
        pk=("stock_code", "period", "period_type"),
        sql=_CONSENSUS_ANNUAL_SQL,
        retire_when="v2 엔진이 model 층 입력(equity consensus)으로 옮겨진 뒤 — "
                    "consensus_annual 소비자는 scoring 뿐이다(플랜 §8-3)",
    ),
    TableMapping(
        v3_table="financial_summary",
        source_kind=STAGE,
        sources=("stg_fin_wise",),
        columns=("stock_code", "period", "period_type", "revenue", "op", "ni", "eps", "bps",
                 "per", "pbr", "roe", "roa", "debt_ratio", "fcf", "capex", "op_margin",
                 "ni_margin", "dividend_yield", "shares", "ev_ebitda", "yoy", "data_type",
                 "accounting_standard", "gross_profit", "total_assets"),
        pk=("stock_code", "period", "period_type"),
        sql=_FINANCIAL_SUMMARY_SQL,
        retire_when="v3 엔진이 model 층으로 은퇴한 뒤(D-4) — financial_summary 소비자는 "
                    "scoring 뿐이다(플랜 §8-3)",
        null_columns=("op_margin", "ni_margin", "yoy"),
        note="WISE(손익·투자지표) + DART fin_std(재무상태표·현금흐름) 합성 — T1.5/D-10. "
             "quality 입력 roa·debt_ratio·fcf·total_assets 는 DART 쪽에서 온다",
        cross_sources=((EQUITY, "fin_std"), (EQUITY, "security")),
    ),
    TableMapping(
        v3_table="score_history",
        source_kind=None,
        sources=(),
        columns=(),
        pk=("stock_code", "score_date"),
        sql="",
        retire_when="브리핑 상위 8·export·api health·unitelegram get_signal_insights 가 "
                    "model 판 직독으로 옮겨진 뒤",
        note="T2.7 에서 model 판을 원천으로 연결한다. 이번 태스크는 DDL 만.",
    ),
    TableMapping(
        v3_table="score_history_v2",
        source_kind=None,
        sources=(),
        columns=(),
        pk=("stock_code", "score_date"),
        sql="",
        retire_when="리서치센터 S6·export 가 model 판 직독으로 옮겨진 뒤",
        note="T2.7 에서 model 판을 원천으로 연결한다. 이번 태스크는 DDL 만.",
    ),
)

BY_TABLE: dict[str, TableMapping] = {m.v3_table: m for m in MAPPINGS}
