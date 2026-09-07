"""뷰 매크로 SQL 템플릿 — `equity.duckdb` 의 테이블 매크로 본문 (DESIGN v1.2 §5 · 결정 1·6).

S06 이 내는 4개: `v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_firm_mktcap` + S21 후속(09-05, 전방
조정) 2개: `v_adj_price_fwd`·`v_adj_volume_fwd` + S17 1개: `v_consensus` + S21 본판 1개:
`v_fin_latest`. 본문은 하나의 템플릿이고
읽는 자리(`{price_daily}` 등)만 두 방식으로 채운다 —
  카탈로그: `render_macros(equity_root)` 가 커밋된 테이블의 MANIFEST 파티션 경로(**절대경로**,
            P1c)를 `read_parquet([...])` 로 넣어 `catalog.write_catalog` 에 준다.
  게이트  : `install_temp_macros(con, {...})` 가 같은 본문을 빌드 세션의 TEMP VIEW 이름(`out_pq` 등)
            위에 TEMP MACRO 로 올린다 — EG8 점프 게이트가 카탈로그 없이 뷰와 같은 식을 돈다.
두 경로가 한 템플릿을 쓰므로 게이트가 본 계산과 소비자가 보는 계산이 갈릴 수 없다.

as-of 규칙 (DESIGN §5): `v_cum_adj(as_of, lag_override := NULL)` —
  cum_*(d) = Π(계수 : d < apply_date ≤ as_of ∧ available_date ≤ cutoff ∧ factor_ok),
  base = as_of 에서 1. 축은 **apply_date**(계수를 가격에 적용하는 세션 — 감자는 정지 뒤 재개일,
  S06 2차)이지 명목 효력일이 아니다.
  cutoff = as_of 에서 `lag` 세션 전 거래일(trading_calendar 역산). lag 기본값은 컬럼군 세션 랙인데
  `dataset_profile`(S19)이 아직 없으므로 **가격 계열 0 세션** 을 본문 상수(FACTOR_LAG_SESSIONS)로
  둔다 — 근거: 계수의 available_date 는 min(공시 접수일, 효력일 다음 거래일) 이라 이미 '그날 알 수
  있었던 날' 이고(stage 가격류 lag_known=true, 공표 시각 미제공 → S04 와 같은 규약), 랙을 더 두면
  분할 당일
  조정가가 하루 늦게 붙어 EG8 점프가 생긴다. 소비자는 `lag_override` 로 세션 단위로 늘릴 수 있다.
  가격 행 자체는 `date ≤ as_of` 로 자른다(가격 랙 0 세션, PRICE_LAG_SESSIONS).
매개변수 이름이 `as_of` 인 이유: `asof` 는 duckdb 예약어(ASOF JOIN)라 매크로 인자로 못 쓴다.

전방 조정 (S21 후속, 사용자 결정 09-05 "전방 조정으로 바꾸는 쪽으로 가자" — FIELD_MAP
`price.adj_close`):
  `v_adj_price_fwd(as_of, lag_override := NULL)` —
  adj_close_fwd(d) = close(d) × Π(share_factor : factor_ok ∧ **같은 security_span 구간** ∧
                                                 apply_date ≤ d ∧ available_date ≤ d
                                                 ∧ available_date ≤ cutoff),
  즉 종목의 **첫 관측 수준을 고정**하고 사건마다 이후 가격을 누적 배수로 올린다(시총 불변 사건은
  share_factor = 1/price_factor 라 위 `v_adj_price` 의 역수 축과 같은 값). 삼성전자 2018-05-03 =
  2,650,000(원주가 그대로) · 05-04 = 51,900 × 50 = 2,595,000. 접는 세션 fold_date =
  greatest(apply_date, available_date): 적용 세션이 와도 아직 공개 전인 계수(회고 원천 —
  available = apply 다음 세션)는 공개 세션부터 접는다. 그래서 (ticker, date) 의 값은 as_of·창에
  무관한 **순수 함수**이고(as_of 는 "아직 공개되지 않은 사건을 접지 않는다" 는 필터 + 행 절단
  `date ≤ as_of` 로만 작용), 출력 `available_date` = greatest(date, 접힌 계수의 available_date)
  는 fold 규칙상 항상 date 다 — 워크벤치 포트 계약 `available_date ≤ as_of` 가 어떤 랙에서도 선다.
  `v_adj_volume_fwd` = volume_shr × Π price_factor(= ÷ Π share_factor), 같은 fold 축.
  기존 `v_adj_price`(base = as_of, 차트·EG8 용)는 그대로 둔다.

컨센서스 (S17, DESIGN §5 "관측점별 최초 관측, target_period 노출"):
  `v_consensus(as_of, lag_override := NULL)` — `consensus_daily` 를 `available_date <= cutoff` 로
  자르고 겹치는 달의 wise·v3 2행을 먼저 알 수 있었던 한 행으로 접는다. `obs_month` 로는 자르지
  않는다(DESIGN §4-6 "obs_month 날짜 축 금지"). `_asof/`·EG11·EG5c 대상이 아니다 — 그 표본 규약은
  키가 (as_of, ticker, **date**)인데(`catalog.sample_sql`·`eg5c_asof_invariance`) 이 뷰에는 일별
  date 축이 없고, `obs_month` 를 `date` 로 이름만 바꿔 실으면 금지한 날짜 축을 카탈로그 산출물에
  굽는 셈이 된다. 뷰 결과의 결정성은 S17 e2e 테스트가 같은 카탈로그를 read_only 연결 두 개로 열어
  직접 대조한다(GATES §9 S17 블록).
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from . import inputs

FACTOR_LAG_SESSIONS = 0     # 계수 available_date 컷오프 기본 랙(세션). 위 docstring 근거
PRICE_LAG_SESSIONS = 0      # 가격 행 컷오프 랙(세션) — 0 이라 `date <= as_of` 와 같다
CONSENSUS_LAG_SESSIONS = 0  # 컨센서스 available_date 컷오프 기본 랙(세션). 아래 v_consensus 근거
FIN_LAG_SESSIONS = 0        # 재무 available_date 컷오프 기본 랙(세션). 아래 v_fin_latest 근거
# TTM 창(4분기)의 period_end 폭 허용 범위(일) — 3분기 간격 ≈ 273일. 밖이면 분기가 빠진 것이다.
TTM_SPAN_MIN_DAYS, TTM_SPAN_MAX_DAYS = 240, 400

# 매크로 이름 → (시그니처, 읽는 테이블). 시그니처는 catalog._MACRO_NAME_RE 규약.
SIGNATURES: dict[str, str] = {
    "v_cum_adj": "v_cum_adj(as_of, lag_override := NULL)",
    "v_adj_price": "v_adj_price(as_of, lag_override := NULL)",
    "v_adj_volume": "v_adj_volume(as_of, lag_override := NULL)",
    "v_firm_mktcap": "v_firm_mktcap(d)",
    "v_adj_price_fwd": "v_adj_price_fwd(as_of, lag_override := NULL)",
    "v_adj_volume_fwd": "v_adj_volume_fwd(as_of, lag_override := NULL)",
    "v_consensus": "v_consensus(as_of, lag_override := NULL)",
    "v_fin_latest": "v_fin_latest(as_of, lag_override := NULL, vintage := 'restated')",
}
MACRO_INPUTS: dict[str, tuple[str, ...]] = {
    "v_cum_adj": ("price_daily", "adj_factor", "trading_calendar"),
    "v_adj_price": ("price_daily", "adj_factor", "trading_calendar"),
    "v_adj_volume": ("price_daily", "adj_factor", "trading_calendar"),
    "v_firm_mktcap": ("price_daily", "corp_ticker"),
    "v_adj_price_fwd": ("price_daily", "adj_factor", "trading_calendar", "security_span"),
    "v_adj_volume_fwd": ("price_daily", "adj_factor", "trading_calendar", "security_span"),
    "v_consensus": ("consensus_daily", "trading_calendar"),
    "v_fin_latest": ("fin_std", "disclosure_version", "trading_calendar"),
}
# 매크로가 다른 매크로를 부르는 경우 — 같은 카탈로그(또는 같은 세션)에 함께 있어야 한다.
MACRO_DEPENDS: dict[str, tuple[str, ...]] = {
    "v_adj_price": ("v_cum_adj",), "v_adj_volume": ("v_cum_adj",)}

# 전방 조정 공통 CTE — `v_adj_price_fwd`·`v_adj_volume_fwd` 가 같은 본문을 쓴다(매크로는 둘,
# 정의는 하나). 계수를 (ticker, span_seq, fold_date) 로 접고(같은 날 두 이벤트 = 곱) 앞에서부터
# 누적한 뒤 ASOF JOIN 으로 '이 날 이전 마지막 접는 세션' 의 누적값을 붙인다 — 행별 GROUP BY 없음.
#
# **누적은 `security_span` 구간 안에서만** 한다(S23, 2026-09-06). 재상장 2종(036220·101970)에서
# 이전 구간의 계수가 새 구간으로 넘어오면 새 구간의 수준이 통째로 틀어진다 — 전방 조정의 앵커는
# 그 구간의 첫 관측이지 폐지 전 옛 구간이 아니다. 구간 부여는 ASOF 이고, 계수 쪽만 **엄격
# 부등호**(`fold_date > first_date`)라 구간 첫날에 접히는 계수는 앵커와 같은 날이 되어 어떤 행에도
# 곱해지지 않는다(그래야 "구간 첫 행 누적 = 1" 이 선다). 구간이 없는 계수는 span_seq −1 로
# 밀어 어떤 가격 행(span_seq ≥ 0)과도 만나지 않게 한다.
# **표 `price_adj_daily`(S23)가 같은 규칙의 저장본**이고, 그 표의 EG3_price_adj_daily 가 매 빌드
# 기본 랙에서 두 산출의 동일성을 증명한다. 표를 읽는 얇은 매크로로 합치지 않은 이유는
# `lag_override` 다 — 기본값 밖 랙에서는 계수 컷오프를 다시 계산해야 하는데 표에는 랙 축이 없다.
_FWD_CTE = """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, {lag_factor})
),
vis AS (
    SELECT ticker, greatest(apply_date, available_date) AS fold_date,
           price_factor, share_factor, available_date
    FROM {adj_factor}
    WHERE factor_ok AND apply_date <= as_of AND available_date <= (SELECT cutoff FROM cut)
),
spn AS (
    SELECT f.ticker, coalesce(s.span_seq, -1) AS span_seq, f.fold_date,
           f.price_factor, f.share_factor, f.available_date
    FROM vis f
    ASOF LEFT JOIN {security_span} s ON s.ticker = f.ticker AND f.fold_date > s.first_date
),
fac AS (
    SELECT ticker, span_seq, fold_date,
           product(price_factor) AS pf, product(share_factor) AS sf,
           max(available_date) AS available_date
    FROM spn
    GROUP BY ticker, span_seq, fold_date
),
pre AS (
    SELECT ticker, span_seq, fold_date,
           product(pf) OVER w AS cum_price_factor,
           product(sf) OVER w AS cum_share_factor,
           max(available_date) OVER w AS available_date
    FROM fac
    WINDOW w AS (PARTITION BY ticker, span_seq ORDER BY fold_date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
px AS (
    SELECT p.*, coalesce(s.span_seq, 0) AS span_seq
    FROM (SELECT * FROM {price_daily} WHERE date <= as_of) p
    ASOF LEFT JOIN {security_span} s ON s.ticker = p.ticker AND p.date >= s.first_date
),
fwd AS (
    SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume_shr, p.price_kind,
           coalesce(c.cum_price_factor, 1) AS cum_price_factor,
           coalesce(c.cum_share_factor, 1) AS cum_share_factor,
           greatest(p.date, coalesce(c.available_date, p.date)) AS available_date
    FROM px p
    ASOF LEFT JOIN pre c
      ON c.ticker = p.ticker AND c.span_seq = p.span_seq AND p.date >= c.fold_date
)
"""

TEMPLATES: dict[str, str] = {
    # ticker·date 별 누적 계수. 계수는 (ticker, apply_date) 로 먼저 접어(같은 날 두 이벤트 = 곱,
    # 복합 성분) 뒤에서부터 누적한 뒤 ASOF JOIN 으로 '이 날 이후 첫 적용 세션' 의 누적값을
    # 붙인다 — 행별 GROUP BY 없음.
    "v_cum_adj": """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, {lag_factor})
),
fac AS (
    SELECT ticker, apply_date, product(price_factor) AS pf, product(share_factor) AS sf
    FROM {adj_factor}
    WHERE factor_ok AND apply_date <= as_of AND available_date <= (SELECT cutoff FROM cut)
    GROUP BY ticker, apply_date
),
suf AS (
    SELECT ticker, apply_date,
           product(pf) OVER w AS cum_price_factor,
           product(sf) OVER w AS cum_share_factor
    FROM fac
    WINDOW w AS (PARTITION BY ticker ORDER BY apply_date
                 ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)
)
SELECT p.ticker, p.date,
       coalesce(s.cum_price_factor, 1) AS cum_price_factor,
       coalesce(s.cum_share_factor, 1) AS cum_share_factor
FROM (SELECT ticker, date FROM {price_daily} WHERE date <= as_of) p
ASOF LEFT JOIN suf s ON s.ticker = p.ticker AND p.date < s.apply_date
""",
    # 원주가 × 누적 가격계수 (FIELD_MAP price.adj_close). 원주가 컬럼은 그대로 함께 낸다(결정 6).
    "v_adj_price": """
SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume_shr, p.price_kind,
       c.cum_price_factor, c.cum_share_factor,
       p.open  * c.cum_price_factor AS adj_open,
       p.high  * c.cum_price_factor AS adj_high,
       p.low   * c.cum_price_factor AS adj_low,
       p.close * c.cum_price_factor AS adj_close
FROM {price_daily} p
JOIN v_cum_adj(as_of, lag_override := lag_override) c ON c.ticker = p.ticker AND c.date = p.date
""",
    # 원거래량 × 누적 주식수계수 — 곱셈이다(FX-2-010 · FX-N-006). 나눗셈이면 분할 전 거래량이
    # 1/2500 이 된다.
    "v_adj_volume": """
SELECT p.ticker, p.date, p.volume_shr, c.cum_share_factor,
       p.volume_shr * c.cum_share_factor AS adj_volume
FROM {price_daily} p
JOIN v_cum_adj(as_of, lag_override := lag_override) c ON c.ticker = p.ticker AND c.date = p.date
""",
    # 전방 조정가 = 원주가 × 그날까지 접힌 누적 주식수계수 (FIELD_MAP price.adj_close, S21 후속).
    # 곱셈이다 — 나눗셈이면 분할 뒤 가격이 1/2500 로 떨어진다(FX-N-006 류, test_equity_s06_views).
    "v_adj_price_fwd": _FWD_CTE + """
SELECT ticker, date, open, high, low, close, volume_shr, price_kind,
       cum_price_factor, cum_share_factor, available_date,
       open  * cum_share_factor AS adj_open,
       high  * cum_share_factor AS adj_high,
       low   * cum_share_factor AS adj_low,
       close * cum_share_factor AS adj_close
FROM fwd
""",
    # 전방 조정 거래량 = 원거래량 × 누적 가격계수(= ÷ 누적 주식수계수) — 분할 뒤 거래량을 분할 전
    # 주식수 척도로 내린다.
    "v_adj_volume_fwd": _FWD_CTE + """
SELECT ticker, date, volume_shr, cum_price_factor, cum_share_factor, available_date,
       volume_shr * cum_price_factor AS adj_volume
FROM fwd
""",
    # 기업 시총 = 그날 가격 행이 있는(= 상장) 종류주 시총의 합. 그룹 키는 corp_ticker 의
    # common_ticker(KR7 isin8 그룹), 비KR7·ETF 는 자기 티커 단독. EG3-P05 는 isin8 축으로 독립
    # 재계산해 대조한다.
    "v_firm_mktcap": """
SELECT min(ct.corp_code)                        AS corp_code,
       coalesce(ct.common_ticker, ct.ticker)    AS firm_ticker,
       p.date                                   AS date,
       count(*)                                 AS n_leg,
       sum(p.mktcap_krw)                        AS firm_mktcap_krw
FROM {price_daily} p
JOIN {corp_ticker} ct ON ct.ticker = p.ticker
WHERE p.date = d AND p.mktcap_krw IS NOT NULL
GROUP BY coalesce(ct.common_ticker, ct.ticker), p.date
""",
    # 관측점별 최초 관측 (DESIGN §5, S17). `consensus_daily` 는 이미 관측점(월)마다 최초 관측
    # 한 행이므로 뷰가 하는 일은 둘뿐이다:
    #   ① PIT 절단 — `available_date <= cutoff`. **`obs_month` 로 자르지 않는다**(DESIGN §4-6
    #      "obs_month 날짜 축 금지"): wise 판본은 2026-09-01 에야 알 수 있는데 obs_month 는
    #      2025-08 이라 obs_month 로 자르면 1년치 look-ahead 가 열린다(EG-C ⑨).
    #   ② 원천 해소 — 겹치는 달의 같은 키(ticker, obs_month, target_period, metric)는 테이블에
    #      wise·v3 2행이다(FX-5-004). 소비자가 두 번 세지 않도록 **먼저 알 수 있었던 행**
    #      (min(available_date), 동률이면 src 사전순)만 남긴다. available_date 는 행마다 고정이라
    #      as_of 가 커져도 한 번 고른 행이 바뀌지 않는다(단조).
    # obs_month 는 자르지 않고 **그대로 노출**한다 — 여러 관측점이 보여야 리비전 팩터(G05)를
    # 팩터층이 계산할 수 있다. 12M forward 합성도 팩터층 몫이다(FIELD_MAP §2 consensus.*).
    # 랙 기본값 0 세션(CONSENSUS_LAG_SESSIONS): available_date 가 이미 '그날 알 수 있었던 날'
    # (wise fetched_date measured · v3 collected_date measured, stage lag_known=true)이다.
    # `dataset_profile`(S19)이 생기면 그 값으로 교체한다.
    "v_consensus": """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, {lag_consensus})
),
vis AS (
    SELECT c.*, row_number() OVER (
               PARTITION BY c.ticker, c.obs_month, c.target_period, c.metric
               ORDER BY c.available_date, c.src) AS rn
    FROM {consensus_daily} c
    WHERE c.available_date <= (SELECT cutoff FROM cut)
)
SELECT ticker, obs_month, target_period, metric, src, obs_date,
       est_mean, est_min, est_max, unit, coverage_degraded,
       available_date, available_basis
FROM vis
WHERE rn = 1
""",
    # 재무 판본 뷰 (DESIGN §5, S21 본판). 접는 축은 **판본과 재무제표 구분 둘뿐**이고 기간 축
    # (`period_end`)은 남긴다 — "latest" 는 vintage·fs_div 의 latest 다. as_of 로 기간을 하나로
    # 접지 않는 이유: 소비자(워크벤치 어댑터)는 세션마다 값이 필요한데 기간까지 접으면 세션 수만큼
    # 매크로를 다시 불러야 한다. 세션 축 절단은 소비자가 `available_date` 로 ASOF 조인한다
    # (`v_adj_price_fwd` 와 같은 규약 — as_of 는 컷오프·행 절단으로만 작용).
    #   ① 판본: `vintage` 인자 — 'restated' → `vintage_kind='api_restated'`(4A 가 내는 유일한 판),
    #      'pit' → `original`·`corrected`(4C=S14 대기라 현재는 빈 결과), 그 밖 값은 vintage_kind
    #      리터럴로 본다.
    #   ② 재무제표 구분: (corp_code, period_end, report_code) 당 **CFS 우선** 한 행 → `fs_div_used`.
    #      동률은 available_date 최신 → rcept_no 최신(정정 재제출).
    #   ③ TTM: 3개월 축(`report_code='11011'` 은 `<계정>_q4_derived`, 나머지는 원 계정 — 현금흐름은
    #      `_q`)의 4행 합인데 **4분기가 전부 보일 때만**이다. 조건 셋을 다 건다 — 창의 non-null 이
    #      4개 · 창의 period_end 폭이 3분기(240~400일) · 창 안 모든 행의 available_date 가 이 행의
    #      available_date 이하(정정 재제출로 옛 분기가 나중에 접수되면 그 행에서만 TTM 이 선다).
    #      하나라도 어긋나면 NULL 이다 — 부분합을 내면 분기 하나가 빠진 채 연간처럼 읽힌다.
    #   ④ `has_correction` = `disclosure_version.first_correction_dt <= cutoff`(DEFECT-E01 —
    #      정적 플래그가 아니라 기준일 판정이다). 링크가 없으면 FALSE.
    # 랙 기본값 0 세션(FIN_LAG_SESSIONS): `available_date` 가 DART 접수일이라 이미 '그날 알 수
    # 있었던 날' 이다. `dataset_profile`(S19)이 생기면 그 값으로 교체한다.
    "v_fin_latest": """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, {lag_fin})
),
vis AS (
    SELECT f.*
    FROM {fin_std} f
    WHERE f.available_date <= (SELECT cutoff FROM cut)
      AND CASE WHEN vintage = 'restated' THEN f.vintage_kind = 'api_restated'
               WHEN vintage = 'pit'      THEN f.vintage_kind IN ('original', 'corrected')
               ELSE f.vintage_kind = vintage END
),
pick AS (
    SELECT * FROM (
        SELECT v.*, row_number() OVER (
                   PARTITION BY v.corp_code, v.period_end, v.report_code
                   ORDER BY CASE WHEN v.fs_div = 'CFS' THEN 0 ELSE 1 END, v.fs_div,
                            v.available_date DESC, v.rcept_no DESC) AS rn
        FROM vis v)
    WHERE rn = 1
),
q AS (
    SELECT p.*,
           CASE WHEN p.report_code = '11011' THEN p.revenue_q4_derived
                ELSE p.revenue END                                   AS q_revenue,
           CASE WHEN p.report_code = '11011' THEN p.gross_profit_q4_derived
                ELSE p.gross_profit END                              AS q_gross_profit,
           CASE WHEN p.report_code = '11011' THEN p.op_profit_q4_derived
                ELSE p.op_profit END                                 AS q_op_profit,
           CASE WHEN p.report_code = '11011' THEN p.net_income_q4_derived
                ELSE p.net_income END                                AS q_net_income,
           p.cf_operating_q                                          AS q_cf_operating
    FROM pick p
),
ttm AS (
    SELECT q.*,
           count(*) OVER w                                           AS ttm_n_rows,
           max(q.available_date) OVER w                              AS ttm_max_available,
           min(q.period_end) OVER w                                  AS ttm_first_period_end,
           sum(q.q_revenue) OVER w                                   AS ttm_sum_revenue,
           count(q.q_revenue) OVER w                                 AS ttm_cnt_revenue,
           sum(q.q_gross_profit) OVER w                              AS ttm_sum_gross_profit,
           count(q.q_gross_profit) OVER w                            AS ttm_cnt_gross_profit,
           sum(q.q_op_profit) OVER w                                 AS ttm_sum_op_profit,
           count(q.q_op_profit) OVER w                               AS ttm_cnt_op_profit,
           sum(q.q_net_income) OVER w                                AS ttm_sum_net_income,
           count(q.q_net_income) OVER w                              AS ttm_cnt_net_income,
           sum(q.q_cf_operating) OVER w                              AS ttm_sum_cf_operating,
           count(q.q_cf_operating) OVER w                            AS ttm_cnt_cf_operating
    FROM q
    WINDOW w AS (PARTITION BY q.corp_code ORDER BY q.period_end, q.report_code
                 ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)
),
ok AS (
    SELECT t.*,
           (t.ttm_n_rows = 4
            AND t.ttm_max_available <= t.available_date
            AND date_diff('day', t.ttm_first_period_end, t.period_end)
                BETWEEN {ttm_span_min} AND {ttm_span_max}) AS ttm_window_ok
    FROM ttm t
)
SELECT o.corp_code, o.period_end, o.report_code, o.fs_div AS fs_div_used,
       o.bsns_year, o.rcept_no, o.period_start, o.currency,
       o.revenue, o.revenue_basis, o.gross_profit, o.op_profit, o.net_income,
       o.total_asset, o.total_liab, o.total_equity, o.cf_operating_ytd, o.cf_operating_q,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_revenue = 4
            THEN o.ttm_sum_revenue END                               AS ttm_revenue,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_gross_profit = 4
            THEN o.ttm_sum_gross_profit END                          AS ttm_gross_profit,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_op_profit = 4
            THEN o.ttm_sum_op_profit END                             AS ttm_op_profit,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_net_income = 4
            THEN o.ttm_sum_net_income END                            AS ttm_net_income,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_cf_operating = 4
            THEN o.ttm_sum_cf_operating END                          AS ttm_cf_operating,
       coalesce(d.first_correction_dt <= (SELECT cutoff FROM cut), FALSE) AS has_correction,
       o.available_date, o.available_basis
FROM ok o
LEFT JOIN (SELECT rcept_no, first_correction_dt FROM {disclosure_version}) d
       ON d.rcept_no = o.rcept_no
""",
}


def render_body(name: str, sources: dict[str, str], template: str | None = None) -> str:
    """템플릿의 읽는 자리를 `sources[table]`(관계식 문자열)로 채운다."""
    body = template if template is not None else TEMPLATES[name]
    fill = {t: sources[t] for t in MACRO_INPUTS[name]}
    return body.format(lag_factor=FACTOR_LAG_SESSIONS, lag_price=PRICE_LAG_SESSIONS,
                       lag_consensus=CONSENSUS_LAG_SESSIONS, lag_fin=FIN_LAG_SESSIONS,
                       ttm_span_min=TTM_SPAN_MIN_DAYS, ttm_span_max=TTM_SPAN_MAX_DAYS,
                       **fill).strip()


def parquet_source(equity_root: Path, table: str) -> str:
    """커밋된 equity 테이블의 current_build 파티션을 읽는 `read_parquet([...])` (절대경로).

    MANIFEST 의 `partitions[].path` 만 쓴다 — 디렉토리 glob 은 `_reject/`·구버전 `v=` 를 함께 문다.
    `hive_partitioning=false`: `year=`·`v=` 축은 테이블 컬럼이 아니다(파일 안에 없다).
    """
    pb = inputs.resolve(equity_root, table)
    globs = ", ".join(f"'{Path(g).resolve()}'" for g in pb.globs)
    return f"read_parquet([{globs}], hive_partitioning=false)"


def render_macros(equity_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """카탈로그용 `{시그니처: 본문}` 과 `{매크로: 건너뛴 이유}`.

    입력 테이블이 아직 커밋되지 않은 매크로(또는 그것에 의존하는 매크로)는 만들지 않고 이유를
    남긴다 — 깨진 매크로를 카탈로그에 싣지 않는다(카탈로그 없는 상태 = 소비 불가, GATES §9 §8-6).
    """
    resolved: dict[str, str] = {}
    missing: dict[str, str] = {}
    for table in sorted({t for ts in MACRO_INPUTS.values() for t in ts}):
        try:
            resolved[table] = parquet_source(equity_root, table)
        except FileNotFoundError as e:
            missing[table] = str(e)
    macros: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for name in SIGNATURES:
        absent = [t for t in MACRO_INPUTS[name] if t not in resolved]
        dep_skipped = [d for d in MACRO_DEPENDS.get(name, ()) if d in skipped]
        if absent or dep_skipped:
            skipped[name] = (f"not_built: inputs={absent}" if absent
                             else f"depends_on_skipped: {dep_skipped}")
            continue
        macros[SIGNATURES[name]] = render_body(name, resolved)
    return macros, skipped


def install_temp_macros(con: duckdb.DuckDBPyConnection, sources: dict[str, str],
                        overrides: dict[str, str] | None = None) -> list[str]:
    """`sources` 로 채울 수 있는 매크로를 TEMP MACRO 로 세션에 올린다. 만든 이름을 돌려준다.

    `overrides[name]` 은 그 매크로의 템플릿을 바꿔 끼운다 — 부정 픽스처(FX-N-006 나눗셈)용.
    """
    made: list[str] = []
    for name, sig in SIGNATURES.items():
        if any(t not in sources for t in MACRO_INPUTS[name]):
            continue
        if any(d not in made for d in MACRO_DEPENDS.get(name, ())):
            continue
        body = render_body(name, sources, (overrides or {}).get(name))
        con.execute(f"CREATE OR REPLACE TEMP MACRO {sig} AS TABLE {body}")
        made.append(name)
    return made


__all__ = ["CONSENSUS_LAG_SESSIONS", "FACTOR_LAG_SESSIONS", "FIN_LAG_SESSIONS", "MACRO_DEPENDS",
           "MACRO_INPUTS", "PRICE_LAG_SESSIONS", "SIGNATURES", "TEMPLATES", "TTM_SPAN_MAX_DAYS",
           "TTM_SPAN_MIN_DAYS", "install_temp_macros", "parquet_source", "render_body",
           "render_macros"]
