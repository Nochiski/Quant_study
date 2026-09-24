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
# 종목당 1행 스냅샷. 유니버스는 `universe_daily` 의 as_of 이하 최신 세션 행(status listed·suspended,
# ETF 제외 — v3 `stocks` 는 키움 ka10099/KIS MST 종목표라 ETF 를 담지 않는다). 시총은 같은 창의
# `price_daily.mktcap_krw` 최신 값 → 억원. 이름·상장일·폐지일은 `security`.
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
WHERE u.rn = 1 AND u.sec_type <> 'etf'
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
# target_period: WISE 는 종목마다 3개(과거·당해·차기)를 준다. **as_of 이상인 것 중 최솟값**
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

# ── financial_summary ───────────────────────────────────────────────────────────────────────
# stage `stg_fin_wise`(WISE cF3002 재무제표 + cF4002 투자지표, 결정 D-5).
# wide → long: 한 행이 계정 1개 × 기간 슬롯 6개(`period_label_1..6` ↔ `val_1..6`)라
# 6-fold UNION ALL 로 펴고 accode 로 피벗한다.
# 기간 라벨은 '2021/12(IFRS연결)' / '2026/12(E)(IFRS연결)' 꼴이다 —
#   (E) 가 없으면 v3 `data_type` 은 **NULL** 이고 그 행만 scoring 의 quality·valuation 창
#   (`period_type='annual' AND data_type IS NULL`)에 든다.
# accode(절단본 `tests/fixtures/stage_slice/stg_fin_wise` 실측):
#   cF3002 200000 매출액(수익) · 200810 매출총이익 · 201370 영업이익 · 203170 당기순이익
#   cF4002 312000 EPS · 314000 BPS · 382100 PER · 382500 PBR · 331000 EV/EBITDA ·
#          431800 현금배당수익률 · 701250 보통주수정기말발행주식수
#   cF4002 는 같은 ACCODE 가 P_ACCODE 아래 한 번 더 나온다(EPS/BPS) — 값이 같지만 최상위
#   행(`p_accode IS NULL`)만 쓴다.
# ⚠ **없음(NULL)**: roe · roa · debt_ratio · fcf · capex · op_margin · ni_margin · yoy ·
#   total_assets. WISE cF3002 는 손익계산서 전용이고(절단본 accode 전수 200000~205590·
#   290010~294000) 재무상태표·현금흐름표 계정이 없다. v3 의 이 열들은 네이버 금융
#   '기업실적분석' HTML(`backend/clients/naver/_wisereport_parsers.py:36-49`)에서 왔다.
#   → v3 quality 팩터가 읽는 roa·debt_ratio·fcf·total_assets 가 비어 gpa·roa·fcf/자산·
#     부채비율 서브가 전부 결측이 된다(std_20d 만 남는다). **G-M2 ②의 선결 과제**로
#     오케스트레이터에 보고했다(D-5 재검토 또는 다른 원천 필요).
_FIN_SLOTS = "\n    UNION ALL\n    ".join(
    "SELECT ticker, ep, accode, p_accode, fs_basis, "
    f"period_label_{i} AS label, val_{i} AS val FROM cur" for i in range(1, 7))


def _fin_pick(ep: str, accode: str, top_only: bool = False) -> str:
    cond = f"ep = '{ep}' AND accode = '{accode}'"
    if top_only:
        cond += " AND p_accode IS NULL"
    return f"max(CASE WHEN {cond} THEN val END)"


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
    SELECT ticker, ep, accode, p_accode, fs_basis, val,
           regexp_extract(label, '(\\d\\d\\d\\d)[./](\\d\\d)', 1) AS yyyy,
           regexp_extract(label, '(\\d\\d\\d\\d)[./](\\d\\d)', 2) AS mm,
           regexp_matches(label, '\\(E\\)')                      AS is_est
    FROM slots
    WHERE label IS NOT NULL AND regexp_matches(label, '\\d\\d\\d\\d[./]\\d\\d')
)
SELECT ticker                                              AS stock_code,
       yyyy || '/' || mm                                   AS period,
       CASE WHEN mm = '12' THEN 'annual' ELSE 'quarter' END AS period_type,
       CAST(round({_fin_pick('cF3002', '200000', True)}) AS BIGINT) AS revenue,
       CAST(round({_fin_pick('cF3002', '201370', True)}) AS BIGINT) AS op,
       CAST(round({_fin_pick('cF3002', '203170', True)}) AS BIGINT) AS ni,
       CAST(round({_fin_pick('cF4002', '312000', True)}) AS BIGINT) AS eps,
       CAST(round({_fin_pick('cF4002', '314000', True)}) AS BIGINT) AS bps,
       CAST({_fin_pick('cF4002', '382100')} AS DOUBLE)              AS per,
       CAST({_fin_pick('cF4002', '382500')} AS DOUBLE)              AS pbr,
       CAST(NULL AS DOUBLE)                                         AS roe,
       CAST(NULL AS DOUBLE)                                         AS roa,
       CAST(NULL AS DOUBLE)                                         AS debt_ratio,
       CAST(NULL AS BIGINT)                                         AS fcf,
       CAST(NULL AS BIGINT)                                         AS capex,
       CAST(NULL AS DOUBLE)                                         AS op_margin,
       CAST(NULL AS DOUBLE)                                         AS ni_margin,
       CAST({_fin_pick('cF4002', '431800')} AS DOUBLE)              AS dividend_yield,
       CAST(round({_fin_pick('cF4002', '701250')}) AS BIGINT)       AS shares,
       CAST({_fin_pick('cF4002', '331000')} AS DOUBLE)              AS ev_ebitda,
       CAST(NULL AS DOUBLE)                                         AS yoy,
       CASE WHEN is_est THEN 'estimate' END                          AS data_type,
       max(CASE WHEN ep = 'cF3002' THEN fs_basis END)               AS accounting_standard,
       CAST(round({_fin_pick('cF3002', '200810', True)}) AS BIGINT) AS gross_profit,
       CAST(NULL AS BIGINT)                                         AS total_assets
FROM parsed
GROUP BY ticker, yyyy, mm, is_est
-- 같은 (종목, 기) 에 (E) 슬롯과 확정 슬롯이 둘 다 오면 v3 PK 가 하나뿐이라 뒤에 넣은 행이 남는다.
-- 확정치(data_type NULL)가 scoring 창이므로 그쪽을 마지막에 넣는다.
ORDER BY ticker, yyyy, mm, is_est DESC
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
        null_columns=("roe", "roa", "debt_ratio", "fcf", "capex", "op_margin", "ni_margin",
                      "yoy", "total_assets"),
        note="WISE cF3002 는 손익계산서 전용 — 재무상태표·현금흐름표 계정이 없다. "
             "v3 quality 팩터 입력(roa·debt_ratio·fcf·total_assets)이 비므로 D-5 재검토 필요",
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
