"""factor_inputs 표별 SQL (플랜 `2026-09-24-v3-merge.md` M2 W1-b T2.2b · T2.11).

`build.py` 가 원천 판을 같은 이름의 TEMP VIEW(`price_daily`·`stg_fin_wise` …)로 올린 뒤 여기
함수가 돌려주는 `CREATE TEMP TABLE` 문을 순서대로 실행한다. 새 계산은 플랜 D-9 대로 셋뿐이다 —
① 창 자르기 ② 시총(주식수 × 종가) ③ 신선도 유예(T2.11). 나머지는 원천 값을 계약 단위로 옮긴다.

**단위·정수화는 compat(v3 미러)와 글자 그대로 같다**(`src/compat/mappings.py` 의 식을 옮겼다).
compat 가 나중에 이 층을 읽어 v3 `quant.db` 에 쓸 때 다시 반올림해도 값이 바뀌지 않아야 이식
엔진(이 층) = v3 엔진 원본(compat) 이 |Δ| ≤ 1e-9 로 맞는다(G-M3 ①). 그 규약의 참조는 v3 이식
갈래의 어댑터 `tests/tools/compat_to_fi.py` 다(오케스트레이터 09-29 지시 6항):
  · 시총 = round(주식수 × 종가 / 1e8) 정수 억원 · 수급 = round(원 / 1e6) 정수 백만원
  · 재무 금액 = 정수 억원 · 연간 판정 = 결산월 12(compat DQ-11 미러)
  · 컨센서스 = 고른 판의 결산기마다 cur·1w·1m·3m 네 행(값이 NULL 이어도)
  · eligible 에 시총 하한을 걸지 않는다(엔진이 spec.universe.min_market_cap 으로 건다)

임시 표 이름 규약: 산출 8표는 `_<표>`(예 `_fi_universe` — 접두 `_` 는 원천 뷰와 섞이지 않게), 보조는
`_calx`(거래일 번호)·`_dstar`(마지막 수집일)·`_cov`(종목별 신선도).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from model.contracts import FI_TABLES, TableContract, UniverseRule

# ── 창·상수 ──────────────────────────────────────────────────────────────────
PRICE_WINDOW_DAYS = 550          # fi_prices·fi_adj_prices — D 까지 550 달력일(v3 가격 이력 창)
FLOW_SESSIONS = 60               # fi_flows·fi_credit — D 까지 60 세션
FIN_ANNUAL_PERIODS = 2           # fi_fin_summary 연간 — v3 LIMIT 2
FIN_QUARTERS = 5                 # fi_fin_summary 분기 — v4 TTM 용
KRW_PER_MN = 1_000_000           # 원 → 백만원 (compat units.KRW_PER_MN 과 같다 — 테스트가 대조)
KRW_PER_EOK = 100_000_000        # 원 → 억원   (compat units.KRW_PER_EOK)
MARKETS_IN_LAYER = ("KOSPI", "KOSDAQ")    # fi_universe.market 어휘(계약 주석)
EXCLUDED_SEC_TYPES = ("etf",)             # 주식 유니버스 층 — ETF 는 싣지 않는다
LIVE_STATUSES = ("listed", "suspended")   # D 에 상장 중인 종목(compat stocks 와 같다)

# 신선도 상태 어휘(계약 fi_universe.coverage_state)
COVERAGE_STATES = ("fresh", "grace", "lapsed", "none")
# eligible=False 사유 어휘 — 앞에서부터 먼저 걸린 것 하나를 적는다
EXCLUDE_REASONS = ("sec_type", "market", "no_price", "estimates_lapsed", "estimates_none")

# 수급 주체 — 계약 FLOW_SUBJECTS(백만원) ← equity flow_daily(원). compat `units.FLOW_SUBJECTS` 와
# 같은 대응이다(`tests/test_factor_inputs.py` 가 두 선언을 대조한다). 이 층이 compat 을 import
# 하지 않는 이유: compat 이 나중에 이 층을 읽게 되면 import 순환이 된다.
FLOW_SOURCE: tuple[tuple[str, str], ...] = (
    ("individual", "ind_invsr_krw"),
    ("foreign_investor", "frgnr_invsr_krw"),
    ("institution_total", "orgn_krw"),
    ("financial_investment", "fnnc_invt_krw"),
    ("insurance", "insrnc_krw"),
    ("investment_trust", "invtrt_krw"),
    ("etc_financial", "etc_fnnc_krw"),
    ("bank", "bank_krw"),
    ("pension_fund", "penfnd_etc_krw"),
    ("private_equity", "samo_fund_krw"),
    ("nation", "natn_krw"),
    ("etc_corporation", "etc_corp_krw"),
)

# WISE c1050001 매트릭스 acc_cd(STAGE_DESIGN §4, compat `_ACC` 에서 투자의견 610100 을 뺀 8개)
CONSENSUS_ACC: tuple[tuple[str, str], ...] = (
    ("revenue", "121000"), ("op", "121500"), ("ni", "122710"), ("eps", "312000"),
    ("bps", "314000"), ("per", "382000"), ("pbr", "382400"), ("roe", "211500"),
)
CONSENSUS_ROUNDED = ("eps", "bps")      # compat revision_daily 가 BIGINT 로 싣는 둘
# stage lookback → 계약 horizon. 1y 는 계약 밖이라 싣지 않는다.
HORIZONS: tuple[tuple[str, str], ...] = (("current", "cur"), ("1w", "1w"), ("1m", "1m"),
                                         ("3m", "3m"))

ANNUAL_REPORT = "11011"                                 # 사업보고서
# audit_opinion.adt_opinion_class 중 D-13 '비적정'(equity S15 어휘: 적정·한정·의견거절·부적정·other)
AUDIT_ADVERSE = ("한정", "부적정", "의견거절")
# dividend_event.stock_knd 중 보통주 표기 — 연구 R-2(v4 하위 지표 IC)와 같다. 보통주 행에
# 값이 없으면 법인 축 '-' 행(종류를 나누지 않고 한 줄로 적은 공시)을 쓴다 — 09-28 로컬 판
# 실측 FY2025 배당 법인 1,261 중 155 곳이 '-' 행에만 dps 를 싣는다. 빼면 그 155 곳이 NULL →
# 엔진의 '무배당 0' 으로 조용히 떨어진다(연구 R-2 질의는 빼고 쟀다 — 보고서에 차이로 적는다).
DPS_STOCK_KINDS = ("보통주", "보통주식")
DPS_FALLBACK_KIND = "-"
QUARTER_REPORTS = ("11013", "11012", "11014", "11011")  # 1Q · 반기 · 3Q · 4Q(사업보고서 차감)


@dataclass(frozen=True)
class Params:
    """한 판의 스칼라. 전부 `build.py` 가 검증한 값이다(SQL 에 리터럴로 들어간다)."""

    d: str                  # 판 기준일 D (YYYY-MM-DD)
    fy: str                 # 당해 12월기 'YYYY12' — 추정치 신선도 판정 기준(compat 과 같다)
    price_from: str         # D − 550 달력일
    flow_from: str          # D 까지 60 세션의 첫 세션
    grace_days: int         # UniverseRule.coverage_grace_days
    credit_lag: int         # 신용잔고 실입수 랙(세션, equity FieldProfile)


def _lit_list(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def project(contract: TableContract, inner: str) -> str:
    """계약 열 순서·타입으로 투영하고 grain 으로 정렬한다 — FG0 이 이 모양을 다시 본다."""
    cols = ",\n       ".join(f'CAST("{c.name}" AS {c.dtype}) AS "{c.name}"'
                             for c in contract.columns)
    order = ", ".join(f'"{g}"' for g in contract.grain)
    return f"SELECT {cols}\nFROM (\n{inner}\n) AS _x\nORDER BY {order}"


def create(table: str, inner: str) -> str:
    """`_<표>` 임시 표 생성문(예: fi_universe → `_fi_universe`)."""
    return f"CREATE OR REPLACE TEMP TABLE _{table} AS\n" + project(FI_TABLES[table], inner)


# ── 달력 · 신선도(T2.11) ──────────────────────────────────────────────────────
def calendar_sql() -> str:
    """거래일 번호표 — 세션 간격(유예 나이·신용 랙)을 번호 차로 센다."""
    return ("CREATE OR REPLACE TEMP TABLE _calx AS\n"
            "SELECT date, row_number() OVER (ORDER BY date) AS idx\n"
            "FROM (SELECT DISTINCT date FROM trading_calendar WHERE date IS NOT NULL)")


def coverage_sqls(p: Params) -> list[str]:
    """종목별 신선도 `_cov`(T2.11 · 유예 규칙 = layer-fixes §3-2).

    D* = `stg_consensus_annual` 에서 fetched_date ≤ D 인 **마지막 수집일**(전 종목 공통).
    종목의 마지막 신선일 = 당해 12월기 추정(period_kind 'E', period = fy)의 op·ni 가 **둘 다** 있는
    fetched_date 의 최댓값(compat `ESTIMATE_TICKERS_SQL` 과 같은 판정).
      fresh  = 마지막 신선일 = D*               (나이 0)
      grace  = 나이 ≤ G 거래일                   (나이 = (마지막 신선일, D*] 안의 거래일 수)
      lapsed = 나이 > G
      none   = 신선일이 한 번도 없다 → `_cov` 에 행이 없고 유니버스에서 'none' 으로 채운다
    복귀(fresh 재진입)는 마지막 신선일이 D* 로 갱신되는 것이라 카운터가 저절로 0 이 된다.
    """
    dstar = ("CREATE OR REPLACE TEMP TABLE _dstar AS\n"
             "SELECT max(fetched_date) AS dstar FROM stg_consensus_annual\n"
             f"WHERE fetched_date <= DATE '{p.d}'")
    cov = f"""CREATE OR REPLACE TEMP TABLE _cov AS
WITH fresh AS (
    SELECT ticker, max(fetched_date) AS last_fresh
    FROM stg_consensus_annual
    WHERE fetched_date <= DATE '{p.d}'
      AND period_kind = 'E' AND period = '{p.fy}'
      AND op IS NOT NULL AND ni IS NOT NULL
    GROUP BY ticker
),
aged AS (
    SELECT f.ticker, f.last_fresh, s.dstar,
           (SELECT count(*) FROM _calx c
            WHERE c.date > f.last_fresh AND c.date <= s.dstar) AS age
    FROM fresh f CROSS JOIN _dstar s
)
SELECT ticker, last_fresh,
       CASE WHEN last_fresh = dstar THEN 'fresh'
            WHEN age <= {p.grace_days} THEN 'grace'
            ELSE 'lapsed' END                AS coverage_state,
       CAST(age AS INTEGER)                  AS coverage_age_days
FROM aged"""
    return [dstar, cov]


# ── fi_universe ──────────────────────────────────────────────────────────────
def _exclude_case(rule: UniverseRule) -> str:
    """기본 UniverseRule 판정 — 먼저 걸린 사유 하나(EXCLUDE_REASONS 순서).

    `min_market_cap` 은 여기서 보지 않는다 — 엔진이 `spec.universe.min_market_cap` 으로 건다
    (오케스트레이터 09-29 · 이식 엔진 `v3_zscore._universe`).
    """
    parts = [f"WHEN sec_type NOT IN ({_lit_list(rule.sec_types)}) THEN 'sec_type'",
             f"WHEN market NOT IN ({_lit_list(rule.markets)}) THEN 'market'",
             "WHEN close IS NULL THEN 'no_price'"]
    if rule.require_estimates:
        parts += ["WHEN coverage_state = 'lapsed' THEN 'estimates_lapsed'",
                  "WHEN coverage_state = 'none' THEN 'estimates_none'"]
    return "CASE " + "\n                 ".join(parts) + " ELSE NULL END"


def universe_sql(p: Params, rule: UniverseRule) -> str:
    """D 한 날의 종목 속성 + 신선도 + 기본 규칙 판정.

    종목 = `universe_daily` 의 D 행 중 상장 중(listed·suspended) · KOSPI/KOSDAQ · ETF 제외.
    sec_type 은 거르지 않는다(preferred 등도 싣고 eligible 로 가른다 — 다른 spec 이 재판정).
    시총 = round(D 의 KRX 상장주식수 × KRX 종가 / 1e8) 정수 억원(FG3 · compat `stocks.market_cap`
    과 같은 반올림 — 1,000억 하한 경계와 수급 분모가 이 값이다). 곱은 DECIMAL 로 해 정확하다.
    업종 = D 이하 최신 WICS 스냅샷.
    추정기관 수 = equity `coverage_daily.analyst_count` — 신선·유예 종목은 **마지막 신선일**
    값(점수에 쓰는 컨센서스와 같은 판), 그 밖은 D 이하 최신 값.
    D-13 적격성 재료(계약 09-29, **eligible 에는 쓰지 않는다** — v4 가 spec 으로 건다):
      adv20         = `universe_daily.adv20_krw` / 1e8 — [D−19, D] 20 세션 거래대금 평균(억원).
                      값 있는 세션이 20 미만이면 NULL(equity 정의 그대로).
      is_admin      = `universe_daily.admin_state`(관리종목·KOSDAQ 투자주의환기 소속부,
                      모르면 NULL)
      is_halted     = `universe_daily.halt_state`(D 에 매매거래정지)
      audit_adverse = 의견이 실린 가장 최근 사업보고서(11011, available ≤ D) 감사의견
                      **당기 행**의 분류가 한정·부적정·의견거절. 한 접수에 당기·전기·전전기 3행이
                      실리므로 라벨에 '전' 이 없는 행 → '당' 이 있는 행 우선 → 기수 큰 순 → 분류 순
                      으로 하나(메모리 equity-adapter-pick-nondeterminism). 의견 원문이 없는 행
                      (09-28 로컬 판 FY2025 '-' 라벨 414건)은 건너뛰어 직전 알려진 의견을 쓴다.
                      적정 → false, other → NULL.
      filing_late   = 가장 최근 정기보고서 원본(정정 제외, available ≤ D)의 접수일 > 실효 기한.
                      `disclosure_version.legal_deadline` 은 기말 + 90/45 역일이라 주말·휴장 보정이
                      없다 → 기한이 휴장일이면 **다음 거래일**로 민다(정상 제출이 +1~+2 일로 '지연'
                      되는 오판 방지). 아직 제출하지 않은 보고서(미제출)는 이 열이 보지 못한다.
    """
    inner = f"""WITH u AS (
    SELECT ticker, market, sec_type, halt_state, admin_state, adv20_krw FROM universe_daily
    WHERE date = DATE '{p.d}' AND status IN ({_lit_list(LIVE_STATUSES)})
      AND market IN ({_lit_list(MARKETS_IN_LAYER)})
      AND sec_type NOT IN ({_lit_list(EXCLUDED_SEC_TYPES)})
),
px AS (
    SELECT ticker, close, shares_out FROM price_daily
    WHERE date = DATE '{p.d}' AND basis = 'krx' AND close IS NOT NULL
),
sect AS (
    SELECT ticker, wics_l1_cd, wics_l1_nm, wics_l2_cd, wics_l2_nm,
           row_number() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
    FROM sector_snapshot
    WHERE snapshot_date <= DATE '{p.d}' AND available_date <= DATE '{p.d}'
),
aud AS (
    SELECT s.ticker, a.adt_opinion_class,
           row_number() OVER (
               PARTITION BY s.ticker
               ORDER BY a.bsns_year DESC, a.available_date DESC,
                        CASE WHEN a.bsns_year_label LIKE '%당%' THEN 0 ELSE 1 END,
                        TRY_CAST(regexp_extract(a.bsns_year_label, '(\\d+)', 1) AS INTEGER)
                            DESC NULLS LAST,
                        a.adt_opinion_class, a.rcept_no DESC) AS rn
    FROM audit_opinion a JOIN security s ON s.corp_code = a.corp_code
    WHERE a.reprt_code = '{ANNUAL_REPORT}' AND a.available_date <= DATE '{p.d}'
      AND coalesce(a.bsns_year_label, '') NOT LIKE '%전%'
      AND a.adt_opinion IS NOT NULL
      AND s.ticker IN (SELECT ticker FROM u)
),
filing AS (
    SELECT s.ticker, v.rcept_dt, v.rcept_no, v.legal_deadline
    FROM disclosure_version v JOIN security s ON s.corp_code = v.corp_code
    WHERE NOT coalesce(v.is_correction, false) AND v.available_date <= DATE '{p.d}'
      AND v.legal_deadline IS NOT NULL AND s.ticker IN (SELECT ticker FROM u)
),
fil AS (
    SELECT f.ticker, f.rcept_dt, c.date AS deadline_eff,
           row_number() OVER (PARTITION BY f.ticker ORDER BY f.rcept_dt DESC, f.rcept_no DESC)
               AS rn
    FROM filing f ASOF LEFT JOIN _calx c ON c.date >= f.legal_deadline
),
base AS (
    SELECT u.ticker, u.market, u.sec_type,
           coalesce(s.name_abbrv_current, s.name_current) AS name,  -- 약명(시장 호칭) 우선
           s.list_date,
           u.halt_state, u.admin_state, u.adv20_krw,
           px.close, px.shares_out,
           CAST(round(CAST(px.shares_out AS DECIMAL(38, 0)) * CAST(px.close AS DECIMAL(38, 0))
                      / {KRW_PER_EOK}.0) AS BIGINT)      AS market_cap,
           coalesce(v.coverage_state, 'none')            AS coverage_state,
           v.coverage_age_days,
           CASE WHEN v.coverage_state IN ('fresh', 'grace') THEN v.last_fresh
                ELSE DATE '{p.d}' END                    AS analyst_asof
    FROM u
    LEFT JOIN security s USING (ticker)
    LEFT JOIN px USING (ticker)
    LEFT JOIN _cov v USING (ticker)
),
na AS (
    SELECT ticker, date, analyst_count FROM coverage_daily WHERE date <= DATE '{p.d}'
),
judged AS (
    SELECT b.*, n.analyst_count,
           {_exclude_case(rule)} AS reason
    FROM base b
    ASOF LEFT JOIN na n ON n.ticker = b.ticker AND n.date <= b.analyst_asof
)
SELECT j.ticker, DATE '{p.d}' AS date, j.name, j.market, j.sec_type,
       j.list_date                               AS listed_date,
       j.shares_out                              AS shares,
       j.market_cap,
       'krx'                                     AS mktcap_basis,
       t.wics_l1_cd AS sector_l1, t.wics_l1_nm AS sector_l1_name,
       t.wics_l2_cd AS sector_l2, t.wics_l2_nm AS sector_l2_name,
       j.coverage_state IN ('fresh', 'grace')    AS has_estimates,
       j.coverage_state,
       j.coverage_age_days,
       j.analyst_count                           AS n_analysts,
       j.adv20_krw / {KRW_PER_EOK}.0             AS adv20,
       j.admin_state                             AS is_admin,
       j.halt_state                              AS is_halted,
       CASE WHEN a.adt_opinion_class IN ({_lit_list(AUDIT_ADVERSE)}) THEN true
            WHEN a.adt_opinion_class = '적정' THEN false END AS audit_adverse,
       CASE WHEN f.deadline_eff IS NOT NULL THEN f.rcept_dt > f.deadline_eff END
                                                 AS filing_late,
       j.reason IS NULL                          AS eligible,
       j.reason                                  AS exclude_reason
FROM judged j
LEFT JOIN sect t ON t.ticker = j.ticker AND t.rn = 1
LEFT JOIN aud a ON a.ticker = j.ticker AND a.rn = 1
LEFT JOIN fil f ON f.ticker = j.ticker AND f.rn = 1"""
    return create("fi_universe", inner)


_IN_UNIVERSE = "ticker IN (SELECT ticker FROM _fi_universe)"


# ── 가격 · 수정주가 ──────────────────────────────────────────────────────────
def prices_sql(p: Params) -> str:
    """KRX 원주가 550 달력일. 거래대금은 원(계약) — compat daily_prices 의 백만원과 다르다."""
    inner = f"""SELECT ticker, date, open, high, low, close,
       volume_shr AS volume, value_krw AS amount, 'krx' AS price_source
FROM price_daily
WHERE basis = 'krx' AND date >= DATE '{p.price_from}' AND date <= DATE '{p.d}'
  AND {_IN_UNIVERSE}"""
    return create("fi_prices", inner)


def adj_prices_sql(p: Params) -> str:
    """전방 조정 종가 + 누적계수 + 미해결 사건 **계단 표식**(DQ-1, 오케스트레이터 09-29).

    `adj_ok` 는 창 안 미해결 사건(`adj_factor.factor_ok = false`, 적용일 ∈ (창 시작, D],
    available ≤ D)의 **적용일마다 뒤집힌다** — 창 첫 구간 = True, 첫 사건 적용일부터 False, 둘째
    사건부터 다시 True …(같은 날 사건 여럿은 한 번). 그래서 창 안에서 값이 바뀌면 그 창이 사건을
    넘는다(엔진 `v4_rank._Series.crosses_event`), 값이 한결같으면 척도가 이어진다. 사건이 하나면
    '사건 전 True · 사건부터 False' 다. 창 밖 옛 사건은 창 안 비율을 깨지 않으므로 세지 않는다
    (`price_adj_daily.n_unadjusted_events` 는 구간 누적이라 2011년 사건 하나로 영구 표시가 된다).
    """
    inner = f"""WITH bad AS (
    SELECT DISTINCT ticker, apply_date FROM adj_factor
    WHERE NOT factor_ok
      AND apply_date > DATE '{p.price_from}' AND apply_date <= DATE '{p.d}'
      AND available_date <= DATE '{p.d}'
)
SELECT a.ticker, a.date, a.adj_close, a.cum_share_factor AS adj_factor,
       (SELECT count(*) FROM bad b
        WHERE b.ticker = a.ticker AND b.apply_date <= a.date) % 2 = 0 AS adj_ok
FROM price_adj_daily a
WHERE a.basis = 'krx' AND a.date >= DATE '{p.price_from}' AND a.date <= DATE '{p.d}'
  AND a.{_IN_UNIVERSE}"""
    return create("fi_adj_prices", inner)


# ── 수급 · 신용 ──────────────────────────────────────────────────────────────
def flows_sql(p: Params) -> str:
    """12주체 순매수 60 세션(백만원 정수화 — compat investor_detail_flows 식 그대로).

    셀당 원천이 둘이면 키움 우선(compat 과 같은 결정 규칙), 전 주체 NULL 셀은 싣지 않는다.
    """
    any_col = ", ".join(f"f.{src}" for _, src in FLOW_SOURCE)
    sel = ",\n       ".join(f"CAST(round(f.{src} / {KRW_PER_MN}.0) AS BIGINT) AS {dst}"
                           for dst, src in FLOW_SOURCE)
    inner = f"""WITH picked AS (
    SELECT f.*, row_number() OVER (
               PARTITION BY f.ticker, f.date
               ORDER BY CASE WHEN f.src = 'kiwoom' THEN 0 ELSE 1 END, f.src) AS rn
    FROM flow_daily f
    WHERE f.date >= DATE '{p.flow_from}' AND f.date <= DATE '{p.d}'
      AND f.{_IN_UNIVERSE}
      AND coalesce({any_col}) IS NOT NULL
)
SELECT f.ticker, f.date,
       {sel}
FROM picked f
WHERE f.rn = 1"""
    return create("fi_flows", inner)


def credit_sql(p: Params) -> str:
    """신용융자 잔고(주)·잔고율(%) 60 세션. `available_date` = 그 날 + 실입수 랙 세션.

    KIS 신용잔고는 T+3 아침에야 들어온다(equity FieldProfile `credit.margin_balance`) —
    `available_date > D` 인 행은 싣지 않는다(look-ahead 차단, 감사 09-19 E01).
    """
    inner = f"""SELECT c.ticker, c.date,
       c.whol_loan_rmnd_stcn_shr AS credit_balance,
       c.whol_loan_rmnd_rate_pct AS credit_ratio,
       a.date                    AS available_date
FROM credit_daily c
JOIN _calx k ON k.date = c.date
JOIN _calx a ON a.idx = k.idx + {p.credit_lag}
WHERE c.date >= DATE '{p.flow_from}' AND c.date <= DATE '{p.d}'
  AND a.date <= DATE '{p.d}'
  AND c.{_IN_UNIVERSE}
  AND (c.whol_loan_rmnd_stcn_shr IS NOT NULL OR c.whol_loan_rmnd_rate_pct IS NOT NULL)"""
    return create("fi_credit", inner)


# ── 컨센서스 ────────────────────────────────────────────────────────────────
def consensus_sql(p: Params) -> str:
    """WISE 매트릭스(c1050001 T4) — 신선·유예 종목의 **마지막 신선일** 판 × 결산기 3 × horizon 4.

    판에 있는 (종목, 결산기)마다 cur·1w·1m·3m 네 행을 **값이 NULL 이어도** 만든다 — compat 는
    그 결산기 셀이 하나라도 있으면 revision_daily·compare 두 행을 다 만들고 v3 는 둘이 다 있어야
    리비전을 계산한다(어댑터 `compat_to_fi._map_consensus`). `obs_date` 는 cur 행만 WISE
    기준일(그 결산기 행들의 max(base_date) — compat revision_daily.base_date 와 같은 식),
    1w/1m/3m 의 관측일은 WISE 가 주지 않는다(NULL).
    ⚠ 한시 예외(compat GAP-6 과 같다): equity 에 op·ni 컨센서스 표가 없어(B-23) stage
    `stg_consensus_matrix` 를 직독한다. 만료 = W1-a S17b `consensus_revision` 승격.
    """
    lb_in = _lit_list(tuple(src for src, _ in HORIZONS))
    h_values = ", ".join(f"('{src}', '{dst}')" for src, dst in HORIZONS)
    vals = []
    for name, code in CONSENSUS_ACC:
        pick = f"max(CASE WHEN acc_cd = '{code}' THEN value END)"
        if name in CONSENSUS_ROUNDED:
            vals.append(f"CAST({pick} AS BIGINT) AS {name}")
        else:
            vals.append(f"CAST({pick} AS DOUBLE) AS {name}")
    val_sel = ",\n           ".join(vals)
    names = ", ".join(f"v.{n}" for n, _ in CONSENSUS_ACC)
    inner = f"""WITH snap AS (
    SELECT m.* FROM stg_consensus_matrix m
    JOIN _cov c ON c.ticker = m.ticker AND c.last_fresh = m.fetched_date
    WHERE c.coverage_state IN ('fresh', 'grace') AND m.{_IN_UNIVERSE}
),
periods AS (
    SELECT ticker, target_period, fetched_date, max(base_date) AS base_date
    FROM snap GROUP BY ticker, target_period, fetched_date
),
h AS (
    SELECT * FROM (VALUES {h_values}) AS t(lookback, horizon)
),
vals AS (
    SELECT ticker, target_period, lookback,
           {val_sel}
    FROM snap WHERE lookback IN ({lb_in})
    GROUP BY ticker, target_period, lookback
)
SELECT p.ticker,
       substr(p.target_period, 1, 4) || '/' || substr(p.target_period, 5, 2) AS target_period,
       h.horizon,
       {names},
       u.n_analysts,
       CASE WHEN h.horizon = 'cur' THEN p.base_date END AS obs_date,
       p.fetched_date
FROM periods p
CROSS JOIN h
LEFT JOIN vals v ON v.ticker = p.ticker AND v.target_period = p.target_period
                AND v.lookback = h.lookback
JOIN _fi_universe u ON u.ticker = p.ticker"""
    return create("fi_consensus", inner)


def consensus_annual_sql(p: Params) -> str:
    """v2 전용 연간 컨센서스(WISE c1050001 T2Y) — 전년·당해·차년 12월기의 추정(E)·확정(A).

    계약 `fi_consensus_annual`(W1-d 09-29): v2 원천은 v3 가 읽는 매트릭스·cF3002 와 같은 기·
    항목이어도 값이 달라 표를 나눴다. compat `consensus_annual` 과 같은 판(종목별 fetched_date ≤ D
    최신 한 판)·같은 값(eps 정수화)이되, compat 은 한 기에 E 하나만 남기고 이 표는 E·A 를 둘 다
    싣는다(grain 에 data_type — v2 의 E 우선은 엔진이 한다). 같은 (기, 종류)가 한 판에 둘이면
    compat 과 같이 period_label 순 첫 행. 값이 전부 NULL 인 행도 싣는다(v2 에게는 '그 종목이
    있다' 는 뜻 — 어댑터).
    신선도 유예(T2.11)는 걸지 않는다 — 계약이 '최신 ≤ D 한 판' 이고 eligible 이 이미 거른다.
    """
    y = int(p.d[:4])
    periods = _lit_list(tuple(f"{yy}12" for yy in (y - 1, y, y + 1)))
    inner = f"""WITH latest AS (
    SELECT ticker, max(fetched_date) AS fetched_date
    FROM stg_consensus_annual
    WHERE fetched_date <= DATE '{p.d}' AND {_IN_UNIVERSE}
    GROUP BY ticker
),
typed AS (
    -- compat 과 같은 판정: 'E' 만 추정, 그 밖은 확정
    SELECT a.*, CASE WHEN a.period_kind = 'E' THEN 'E' ELSE 'A' END AS data_type
    FROM stg_consensus_annual a
    JOIN latest l ON l.ticker = a.ticker AND l.fetched_date = a.fetched_date
    WHERE a.period IN ({periods})
),
sel AS (
    SELECT t.*, row_number() OVER (
             PARTITION BY t.ticker, t.period, t.data_type ORDER BY t.period_label) AS rn
    FROM typed t
)
SELECT ticker,
       substr(period, 1, 4) || '/' || substr(period, 5, 2) AS period,
       data_type,
       CAST(revenue AS DOUBLE)                             AS revenue,
       CAST(op AS DOUBLE)                                  AS op,
       CAST(ni AS DOUBLE)                                  AS ni,
       CAST(round(eps) AS BIGINT)                          AS eps,
       CAST(per AS DOUBLE)                                 AS per,
       fetched_date
FROM sel
WHERE rn = 1"""
    return create("fi_consensus_annual", inner)


# ── 재무 요약(compat financial_summary 이식 · DQ-6 · DQ-8 · DQ-11) ─────────────
_FIN_SLOTS = "\n    UNION ALL\n    ".join(
    "SELECT ticker, fetched_date, ep, accode, p_accode, acc_nm, fs_basis, "
    f"period_label_{i} AS label, val_{i} AS val FROM cur" for i in range(1, 7))


# WISE 분기 손익(stg_fin_wise_q pkey Q:IS, 플랜 2026-09-30 T-Q4) — 6칸을 행으로. 기간·추정·기준은 stage 파생 열.
_FIN_Q_SLOTS = "\n    UNION ALL\n    ".join(
    "SELECT ticker, fetched_date, acc_nm, "
    f"period_{i} AS period, is_est_{i} AS is_est, basis_{i} AS basis, val_{i} AS val FROM wqcur"
    for i in range(1, 7))
# 분기 매출 계정(최상위 행, 10-01 실측) — 일반 '매출액(수익)' · 보험 '영업수익'(총액) → gross,
# 은행·증권·금융지주 '순영업이익'(순액) → net. 영업이익은 발표기준 우선(네이버·DART 와 같은 정의, DQ-12).
WISE_Q_REVENUE_GROSS: tuple[str, ...] = ("매출액(수익)", "영업수익")
WISE_Q_REVENUE_NET = "순영업이익"
# 지주사 — 별도 재무는 사실상 자회사 배당이라 연결이 없는 분기를 별도로 대체하지 않는다(10-01 사용자).
# DART 업종 KSIC 64992(지주회사) ∪ (649* ∧ 이름 '홀딩스'·'지주' 또는 효성·HDC). 서버 실측 98곳.
HOLDING_KSIC = "64992"
HOLDING_NAMES_649: tuple[str, ...] = ("효성", "HDC")


def _fin_pick(ep: str, accode: str, top_only: bool = False, acc_nm: str | None = None) -> str:
    """compat `mappings._fin_pick` 과 같은 규칙(R1·DQ-6): 계정명이 주어지면 계정명 + 최상위로,
    아니면 accode 로 고른다. 금융업 템플릿의 accode 차이를 계정명이 흡수한다."""
    if acc_nm is not None:
        cond = f"ep = '{ep}' AND acc_nm = '{acc_nm}' AND p_accode IS NULL"
    else:
        cond = f"ep = '{ep}' AND accode = '{accode}'"
        if top_only:
            cond += " AND p_accode IS NULL"
    return f"max(CASE WHEN {cond} THEN val END)"


def fin_summary_sql(p: Params) -> str:
    """연간 2기(WISE 손익·지표 + DART fin_std 자산·현금흐름) + 분기 5기(DART).

    연간 행 = compat 과 같은 값·같은 판정: 결산월이 12 인 기만 annual(DQ-11 v3 미러 — 오케스트레이터
    09-29 지시. v3_zscore@1.0 은 비12월 결산 사업보고서를 연간 창에서 빼는 동작까지 재현해야 한다).
    compat `_FINANCIAL_SUMMARY_SQL` 과 다른 점(전부 의도):
      · WISE 판은 신선·유예 종목만 쓴다(T2.11 — 커버가 끊긴 종목의 옛 재무 행이 조용히 남지 않게).
        그 밖 종목은 WISE 열이 NULL 이고 DART 열만 남는다.
      · 비12월 결산의 연간 확정치는 **싣지 않는다**. compat 은 그것을 period_type='quarter' 로
        두지만(v3 는 읽지 않는다) 이 표의 quarter 는 3개월 분기(v4 TTM)라 12개월 값이 섞이면
        TTM 이 틀어지고, 같은 결산월의 DART 4Q 분기 행과 키가 겹친다. v3 가 읽는 연간 창은 같다.
      · (E) 슬롯은 싣지 않는다 — 추정치는 fi_consensus 몫이다(어댑터 `_map_fin` 과 같은 규약).
      · 분기 행을 더한다(DART 1Q·반기·3Q 는 3개월 값, 4Q 는 사업보고서 − 1~3Q 파생). WISE 분기
        슬롯(val_q*)은 기간 라벨이 없어 못 쓴다. 분기 capex·FCF 는 싣지 않는다 — `_q` 가 부호가
        섞인 누계의 차라 크기가 틀어진다(절단본 003540: 1Q −7.2억 → 반기 +13.7억).
      · `fs_basis` = WISE 라벨, WISE 가 없는 행은 'DART:' || fs_div. `capex_basis` 는 DART 그대로.
      · `dps`(보통주 주당배당금, 원) = equity `dividend_event`(사업보고서 11011 · stock_knd
        보통주/보통주식, 값이 없으면 법인 축 '-' · available_date ≤ D, 결산기 = stlm_dt 의
        'YYYY/MM') — v4 DY0 재료(계약 09-29 추가). 모르면 NULL(무배당 = 0 은 엔진이 정한다).
        분기 행 NULL.
      · `available_date` = 행을 이룬 원천들의 max(WISE fetched_date, DART·배당 available_date).
    op_margin·ni_margin·yoy 는 compat 과 같이 NULL(계산은 엔진 몫).
    """
    eok = f"{KRW_PER_EOK}.0"
    inner = f"""WITH wsnap AS (
    SELECT w.ticker, max(w.fetched_date) AS fetched_date
    FROM stg_fin_wise w
    WHERE w.fetched_date <= DATE '{p.d}'
      AND w.ticker IN (SELECT ticker FROM _cov WHERE coverage_state IN ('fresh', 'grace'))
      AND w.{_IN_UNIVERSE}
    GROUP BY w.ticker
),
cur AS (
    SELECT w.* FROM stg_fin_wise w
    JOIN wsnap l ON l.ticker = w.ticker AND l.fetched_date = w.fetched_date
),
slots AS (
    {_FIN_SLOTS}
),
parsed AS (
    SELECT ticker, fetched_date, ep, accode, p_accode, acc_nm, fs_basis, val,
           regexp_extract(label, '(\\d\\d\\d\\d)[./](\\d\\d)', 1) AS yyyy,
           regexp_extract(label, '(\\d\\d\\d\\d)[./](\\d\\d)', 2) AS mm,
           regexp_matches(label, '\\(E\\)')                      AS is_est
    FROM slots
    WHERE label IS NOT NULL AND regexp_matches(label, '\\d\\d\\d\\d[./]\\d\\d')
),
wise AS (
    SELECT ticker,
           yyyy || '/' || mm                                      AS period,
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
           max(CASE WHEN ep = 'cF3002' THEN fs_basis END)         AS w_fs_basis,
           max(fetched_date)                                      AS w_fetched
    FROM parsed
    WHERE NOT is_est
    GROUP BY ticker, yyyy, mm
),
wqsnap AS (
    -- 종목별 D 이전 최신 WISE 분기 손익 스냅샷(신선·유예 종목만 — 연간 wsnap 과 같은 규약)
    SELECT q.ticker, max(q.fetched_date) AS fetched_date
    FROM stg_fin_wise_q q
    WHERE q.pkey = 'Q:IS' AND q.fetched_date <= DATE '{p.d}'
      AND q.ticker IN (SELECT ticker FROM _cov WHERE coverage_state IN ('fresh', 'grace'))
      AND q.{_IN_UNIVERSE}
    GROUP BY q.ticker
),
wqcur AS (
    SELECT q.* FROM stg_fin_wise_q q
    JOIN wqsnap l ON l.ticker = q.ticker AND l.fetched_date = q.fetched_date
    WHERE q.pkey = 'Q:IS' AND q.p_accode IS NULL           -- 최상위 계정만(DQ-6)
),
wqslots AS (
    {_FIN_Q_SLOTS}
),
wq AS (
    SELECT ticker,
           substr(period, 1, 4) || '/' || substr(period, 5, 2)  AS period,
           coalesce({" ".join(f"max(val) FILTER (WHERE acc_nm = '{n}')," for n in WISE_Q_REVENUE_GROSS)}
                    max(val) FILTER (WHERE acc_nm = '{WISE_Q_REVENUE_NET}'))  AS q_revenue,
           CASE WHEN max(val) FILTER (WHERE acc_nm IN ({", ".join(f"'{n}'" for n in WISE_Q_REVENUE_GROSS)}))
                     IS NOT NULL THEN 'gross'
                WHEN max(val) FILTER (WHERE acc_nm = '{WISE_Q_REVENUE_NET}') IS NOT NULL THEN 'net'
           END                                                   AS q_revenue_basis,
           coalesce(max(val) FILTER (WHERE acc_nm = '영업이익(발표기준)'),
                    max(val) FILTER (WHERE acc_nm = '영업이익'))  AS q_op,
           max(val) FILTER (WHERE acc_nm = '당기순이익')         AS q_ni,
           max(basis)                                            AS q_basis,
           max(fetched_date)                                     AS q_fetched,
           row_number() OVER (PARTITION BY ticker ORDER BY period DESC) AS k
    FROM wqslots
    WHERE period IS NOT NULL AND is_est IS NOT TRUE
    GROUP BY ticker, period
),
fin AS (
    SELECT f.*,
           row_number() OVER (
               PARTITION BY f.corp_code, f.period_end, f.report_code
               ORDER BY CASE WHEN f.fs_div = 'CFS' THEN 0 ELSE 1 END,
                        f.available_date DESC, f.rcept_no DESC) AS rn
    FROM fin_std f
    WHERE f.report_code IN ({_lit_list(QUARTER_REPORTS)})
      AND f.available_date <= DATE '{p.d}'
),
dsec AS (
    SELECT ticker, corp_code FROM security
    WHERE corp_code IS NOT NULL AND {_IN_UNIVERSE}
),
hold AS (
    SELECT DISTINCT s.ticker FROM dsec s JOIN corp c ON c.corp_code = s.corp_code
    WHERE c.induty_code = '{HOLDING_KSIC}'
       OR (c.induty_code LIKE '649%' AND (c.corp_name LIKE '%홀딩스%' OR c.corp_name LIKE '%지주%'
           OR c.corp_name IN ({", ".join(f"'{n}'" for n in HOLDING_NAMES_649)})))
),
div AS (
    SELECT s.ticker,
           coalesce(strftime(e.stlm_dt, '%Y/%m'), e.bsns_year || '/12') AS period,
           e.dps_krw        AS dv_dps,
           e.available_date AS dv_available,
           row_number() OVER (
               PARTITION BY s.ticker, coalesce(strftime(e.stlm_dt, '%Y/%m'), e.bsns_year || '/12')
               ORDER BY e.dps_krw IS NULL,
                        CASE WHEN trim(e.stock_knd) = '{DPS_FALLBACK_KIND}' THEN 1 ELSE 0 END,
                        e.available_date DESC, e.rcept_no DESC) AS rn
    FROM dividend_event e JOIN dsec s ON s.corp_code = e.corp_code
    WHERE e.reprt_code = '{ANNUAL_REPORT}'
      AND trim(e.stock_knd) IN ({_lit_list(DPS_STOCK_KINDS + (DPS_FALLBACK_KIND,))})
      AND e.available_date <= DATE '{p.d}'
),
dart AS (
    SELECT s.ticker,
           strftime(f.period_end, '%Y/%m') AS period,
           f.total_asset      AS d_total_asset,
           f.total_liab       AS d_total_liab,
           f.total_equity     AS d_total_equity,
           f.net_income       AS d_net_income,
           f.cf_operating_ytd AS d_cf_op,
           f.capex_ytd        AS d_capex,
           f.gross_profit     AS d_gross_profit,
           f.capex_basis      AS d_capex_basis,
           f.fs_div           AS d_fs_div,
           f.available_date   AS d_available
    FROM fin f JOIN dsec s ON s.corp_code = f.corp_code
    WHERE f.rn = 1 AND f.report_code = '{ANNUAL_REPORT}'
),
act AS (
    SELECT coalesce(w.ticker, d.ticker) AS ticker,
           coalesce(w.period, d.period) AS period,
           w.w_revenue, w.w_op, w.w_ni, w.w_eps, w.w_bps, w.w_per, w.w_pbr,
           w.w_ev_ebitda, w.w_dividend_yield, w.w_shares, w.w_gross_profit, w.w_fs_basis,
           w.w_fetched,
           d.d_total_asset, d.d_total_liab, d.d_total_equity, d.d_net_income,
           d.d_cf_op, d.d_capex, d.d_gross_profit, d.d_capex_basis, d.d_fs_div, d.d_available
    FROM wise w
    FULL OUTER JOIN dart d ON d.ticker = w.ticker AND d.period = w.period
),
annual AS (
    SELECT a.*, v.dv_dps, v.dv_available,
           row_number() OVER (PARTITION BY a.ticker ORDER BY a.period DESC) AS k
    FROM act a
    LEFT JOIN div v ON v.ticker = a.ticker AND v.period = a.period AND v.rn = 1
    WHERE substr(a.period, 6, 2) = '12'          -- compat period_type 규칙(DQ-11 v3 미러)
),
qtr AS (
    SELECT s.ticker,
           strftime(f.period_end, '%Y/%m') AS period,
           CASE WHEN f.report_code = '{ANNUAL_REPORT}' THEN
                    CASE WHEN f.q4_derived_available_date <= DATE '{p.d}'
                         THEN f.revenue_q4_derived END
                ELSE f.revenue END      AS q_revenue,
           CASE WHEN f.report_code = '{ANNUAL_REPORT}' THEN
                    CASE WHEN f.q4_derived_available_date <= DATE '{p.d}'
                         THEN f.op_profit_q4_derived END
                ELSE f.op_profit END    AS q_op,
           CASE WHEN f.report_code = '{ANNUAL_REPORT}' THEN
                    CASE WHEN f.q4_derived_available_date <= DATE '{p.d}'
                         THEN f.net_income_q4_derived END
                ELSE f.net_income END   AS q_ni,
           CASE WHEN f.report_code = '{ANNUAL_REPORT}' THEN
                    CASE WHEN f.q4_derived_available_date <= DATE '{p.d}'
                         THEN f.gross_profit_q4_derived END
                ELSE f.gross_profit END AS q_gross_profit,
           f.total_asset, f.total_liab, f.total_equity, f.capex_basis, f.fs_div,
           f.available_date,
           row_number() OVER (PARTITION BY s.ticker ORDER BY f.period_end DESC,
                              f.report_code) AS k
    FROM fin f JOIN dsec s ON s.corp_code = f.corp_code
    WHERE f.rn = 1
)
SELECT ticker, period, 'annual' AS period_type,
       CAST(round(w_revenue) AS BIGINT)                                 AS revenue,
       CAST(round(w_op) AS BIGINT)                                      AS op,
       CAST(round(w_ni) AS BIGINT)                                      AS ni,
       CAST(round(w_eps) AS BIGINT)                                     AS eps,
       CAST(round(w_bps) AS BIGINT)                                     AS bps,
       CAST(w_per AS DOUBLE)                                            AS per,
       CAST(w_pbr AS DOUBLE)                                            AS pbr,
       CAST(d_net_income / nullif(d_total_equity, 0) * 100 AS DOUBLE)   AS roe,
       CAST(d_net_income / nullif(d_total_asset, 0) * 100 AS DOUBLE)    AS roa,
       CAST(d_total_liab / nullif(d_total_equity, 0) * 100 AS DOUBLE)   AS debt_ratio,
       CAST(round((d_cf_op - abs(d_capex)) / {eok}) AS BIGINT)          AS fcf,
       CAST(round(abs(d_capex) / {eok}) AS BIGINT)                      AS capex,
       CAST(NULL AS DOUBLE)                                             AS op_margin,
       CAST(NULL AS DOUBLE)                                             AS ni_margin,
       CAST(w_dividend_yield AS DOUBLE)                                 AS dividend_yield,
       CAST(dv_dps AS DOUBLE)                                           AS dps,
       CAST(round(w_shares) AS BIGINT)                                  AS shares,
       CAST(w_ev_ebitda AS DOUBLE)                                      AS ev_ebitda,
       CAST(NULL AS DOUBLE)                                             AS yoy,
       CAST(round(coalesce(w_gross_profit, d_gross_profit / {eok})) AS BIGINT) AS gross_profit,
       CAST(round(d_total_asset / {eok}) AS BIGINT)                     AS total_assets,
       coalesce(w_fs_basis, 'DART:' || d_fs_div)                        AS fs_basis,
       d_capex_basis                                                    AS capex_basis,
       CAST(NULL AS VARCHAR)                                            AS revenue_basis,
       greatest(w_fetched, d_available, dv_available)                   AS available_date
FROM annual
WHERE k <= {FIN_ANNUAL_PERIODS}

UNION ALL

SELECT ticker, period, 'quarter' AS period_type,
       CAST(round(q_revenue / {eok}) AS BIGINT)                         AS revenue,
       CAST(round(q_op / {eok}) AS BIGINT)                              AS op,
       CAST(round(q_ni / {eok}) AS BIGINT)                              AS ni,
       NULL AS eps, NULL AS bps, NULL AS per, NULL AS pbr, NULL AS roe, NULL AS roa,
       CAST(total_liab / nullif(total_equity, 0) * 100 AS DOUBLE)       AS debt_ratio,
       NULL AS fcf, NULL AS capex, NULL AS op_margin, NULL AS ni_margin,
       NULL AS dividend_yield, NULL AS dps, NULL AS shares, NULL AS ev_ebitda, NULL AS yoy,
       CAST(round(q_gross_profit / {eok}) AS BIGINT)                    AS gross_profit,
       CAST(round(total_asset / {eok}) AS BIGINT)                       AS total_assets,
       'DART:' || fs_div                                                AS fs_basis,
       capex_basis,
       CAST(NULL AS VARCHAR)                                            AS revenue_basis,
       available_date
FROM qtr
WHERE k <= {FIN_QUARTERS} AND ticker NOT IN (SELECT ticker FROM wqsnap)

UNION ALL

SELECT w.ticker, w.period, 'quarter' AS period_type,
       CASE WHEN h.ticker IS NULL OR w.q_basis LIKE '%연결%'
            THEN CAST(round(w.q_revenue) AS BIGINT) END                 AS revenue,
       CASE WHEN h.ticker IS NULL OR w.q_basis LIKE '%연결%'
            THEN CAST(round(w.q_op) AS BIGINT) END                      AS op,
       CASE WHEN h.ticker IS NULL OR w.q_basis LIKE '%연결%'
            THEN CAST(round(w.q_ni) AS BIGINT) END                      AS ni,
       NULL AS eps, NULL AS bps, NULL AS per, NULL AS pbr, NULL AS roe, NULL AS roa,
       NULL AS debt_ratio,
       NULL AS fcf, NULL AS capex, NULL AS op_margin, NULL AS ni_margin,
       NULL AS dividend_yield, NULL AS dps, NULL AS shares, NULL AS ev_ebitda, NULL AS yoy,
       NULL AS gross_profit, NULL AS total_assets,
       -- 지주사의 연결 아닌 분기는 값을 비우고 표식을 남긴다(엔진 flag 지주사_별도제외)
       'WISE:' || coalesce(w.q_basis, '?')
           || CASE WHEN h.ticker IS NOT NULL AND coalesce(w.q_basis, '') NOT LIKE '%연결%'
                   THEN '|지주사제외' ELSE '' END                       AS fs_basis,
       CAST(NULL AS VARCHAR)                                            AS capex_basis,
       w.q_revenue_basis                                                AS revenue_basis,
       w.q_fetched                                                      AS available_date
FROM wq w
LEFT JOIN hold h ON h.ticker = w.ticker
WHERE w.k <= {FIN_QUARTERS}"""
    return create("fi_fin_summary", inner)


# ── 판 순서 ──────────────────────────────────────────────────────────────────
# 표 → 그 표를 굽는 SQL 이 **직접** 읽는 원천(판 기록용). `_fi_universe` 제한은 fi_universe 의
# 원천으로 따로 남으므로 여기에 되풀이하지 않는다.
TABLE_SOURCES: Mapping[str, tuple[str, ...]] = {
    "fi_universe": ("universe_daily", "security", "price_daily", "sector_snapshot",
                    "coverage_daily", "audit_opinion", "disclosure_version",
                    "stg_consensus_annual", "trading_calendar"),
    "fi_prices": ("price_daily",),
    "fi_adj_prices": ("price_adj_daily", "adj_factor"),
    "fi_flows": ("flow_daily", "trading_calendar"),
    "fi_credit": ("credit_daily", "trading_calendar"),
    "fi_consensus": ("stg_consensus_matrix", "stg_consensus_annual", "coverage_daily",
                     "trading_calendar"),
    "fi_consensus_annual": ("stg_consensus_annual",),
    "fi_fin_summary": ("stg_fin_wise", "stg_fin_wise_q", "fin_std", "dividend_event", "security",
                       "corp", "stg_consensus_annual", "trading_calendar"),
}


def table_sqls(p: Params, rule: UniverseRule) -> list[tuple[str, str]]:
    """(표, SQL) — 실행 순서. fi_universe 가 먼저여야 나머지가 그 종목으로 자른다."""
    return [("fi_universe", universe_sql(p, rule)),
            ("fi_prices", prices_sql(p)),
            ("fi_adj_prices", adj_prices_sql(p)),
            ("fi_flows", flows_sql(p)),
            ("fi_credit", credit_sql(p)),
            ("fi_consensus", consensus_sql(p)),
            ("fi_consensus_annual", consensus_annual_sql(p)),
            ("fi_fin_summary", fin_summary_sql(p))]


# 판 manifest `gaps` — 원천이 없거나 의도적으로 비운 열(조용한 결측 금지 · V2-7).
GAPS: tuple[dict[str, str], ...] = (
    {"table": "fi_prices", "column": "price_source",
     "reason": "아침판만 구현 — 전 행 'krx'. 저녁 T 오버레이(evening_snapshot)는 W1-a 뒤"},
    {"table": "fi_universe", "column": "mktcap_basis",
     "reason": "아침판만 구현 — 전 행 'krx'. 저녁 't1_shares_x_t_close'(B-24)는 W1-a 뒤"},
    {"table": "fi_universe", "column": "filing_late",
     "reason": "가장 최근 제출한 정기보고서만 판정 — 기한이 지났는데 아직 안 낸 보고서(미제출)는 "
               "보지 못한다(기대 보고서 목록이 층에 없다)"},
    {"table": "fi_universe", "column": "is_admin",
     "reason": "equity universe_daily.admin_state 그대로 — KOSDAQ 투자주의환기 소속부 포함, "
               "판정 재료가 없으면 NULL"},
    {"table": "fi_consensus", "column": "*",
     "reason": "한시 예외: equity 에 op·ni 컨센서스 표가 없어(B-23) stage stg_consensus_matrix "
               "직독. 만료 = W1-a S17b consensus_revision"},
    {"table": "fi_consensus", "column": "obs_date",
     "reason": "1w/1m/3m 행은 NULL — WISE 매트릭스는 lookback 관측일을 주지 않는다(cur 만 "
               "base_date)"},
    {"table": "fi_fin_summary", "column": "annual(비12월 결산)",
     "reason": "compat DQ-11 미러 — 결산월 12 만 annual. 비12월 결산 연간 확정치(전 시장 12종목, "
               "09-23 유니버스 0)는 싣지 않는다(quarter 로 두면 3개월 분기 행과 섞인다)"},
    {"table": "fi_fin_summary", "column": "op_margin,ni_margin,yoy",
     "reason": "compat 과 같이 NULL — 계산은 엔진 몫(원천 WISE·DART 에 직접 열이 없다)"},
    {"table": "fi_fin_summary", "column": "quarter:eps,bps,per,pbr,ev_ebitda,dividend_yield,"
                                          "dps,shares,roe,roa,fcf,capex",
     "reason": "분기 행은 DART 만 — WISE 분기 슬롯은 기간 라벨이 없고, 분기 ROA 연율화는 정의 "
               "전, 분기 capex·FCF 는 부호 혼재 누계 차라 싣지 않는다"},
)
