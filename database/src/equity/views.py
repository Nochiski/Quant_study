"""뷰 매크로 SQL 템플릿 — `equity.duckdb` 의 테이블 매크로 본문 (DESIGN v1.2 §5 · 결정 1·6).

S06 이 내는 4개: `v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_firm_mktcap` + S21 후속(09-05, 전방
조정) 2개: `v_adj_price_fwd`·`v_adj_volume_fwd` + S17 1개: `v_consensus`. 본문은 하나의 템플릿이고
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
  adj_close_fwd(d) = close(d) × Π(share_factor : factor_ok ∧ apply_date ≤ d ∧ available_date ≤ d
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

# 매크로 이름 → (시그니처, 읽는 테이블). 시그니처는 catalog._MACRO_NAME_RE 규약.
SIGNATURES: dict[str, str] = {
    "v_cum_adj": "v_cum_adj(as_of, lag_override := NULL)",
    "v_adj_price": "v_adj_price(as_of, lag_override := NULL)",
    "v_adj_volume": "v_adj_volume(as_of, lag_override := NULL)",
    "v_firm_mktcap": "v_firm_mktcap(d)",
    "v_adj_price_fwd": "v_adj_price_fwd(as_of, lag_override := NULL)",
    "v_adj_volume_fwd": "v_adj_volume_fwd(as_of, lag_override := NULL)",
    "v_consensus": "v_consensus(as_of, lag_override := NULL)",
}
MACRO_INPUTS: dict[str, tuple[str, ...]] = {
    "v_cum_adj": ("price_daily", "adj_factor", "trading_calendar"),
    "v_adj_price": ("price_daily", "adj_factor", "trading_calendar"),
    "v_adj_volume": ("price_daily", "adj_factor", "trading_calendar"),
    "v_firm_mktcap": ("price_daily", "corp_ticker"),
    "v_adj_price_fwd": ("price_daily", "adj_factor", "trading_calendar"),
    "v_adj_volume_fwd": ("price_daily", "adj_factor", "trading_calendar"),
    "v_consensus": ("consensus_daily", "trading_calendar"),
}
# 매크로가 다른 매크로를 부르는 경우 — 같은 카탈로그(또는 같은 세션)에 함께 있어야 한다.
MACRO_DEPENDS: dict[str, tuple[str, ...]] = {
    "v_adj_price": ("v_cum_adj",), "v_adj_volume": ("v_cum_adj",)}

# 전방 조정 공통 CTE — `v_adj_price_fwd`·`v_adj_volume_fwd` 가 같은 본문을 쓴다(매크로는 둘,
# 정의는 하나). 계수를 (ticker, fold_date) 로 접고(같은 날 두 이벤트 = 곱) 앞에서부터 누적한 뒤
# ASOF JOIN 으로 '이 날 이전 마지막 접는 세션' 의 누적값을 붙인다 — 행별 GROUP BY 없음.
_FWD_CTE = """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, {lag_factor})
),
fac AS (
    SELECT ticker, greatest(apply_date, available_date) AS fold_date,
           product(price_factor) AS pf, product(share_factor) AS sf,
           max(available_date) AS available_date
    FROM {adj_factor}
    WHERE factor_ok AND apply_date <= as_of AND available_date <= (SELECT cutoff FROM cut)
    GROUP BY ticker, greatest(apply_date, available_date)
),
pre AS (
    SELECT ticker, fold_date,
           product(pf) OVER w AS cum_price_factor,
           product(sf) OVER w AS cum_share_factor,
           max(available_date) OVER w AS available_date
    FROM fac
    WINDOW w AS (PARTITION BY ticker ORDER BY fold_date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
fwd AS (
    SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume_shr, p.price_kind,
           coalesce(c.cum_price_factor, 1) AS cum_price_factor,
           coalesce(c.cum_share_factor, 1) AS cum_share_factor,
           greatest(p.date, coalesce(c.available_date, p.date)) AS available_date
    FROM (SELECT * FROM {price_daily} WHERE date <= as_of) p
    ASOF LEFT JOIN pre c ON c.ticker = p.ticker AND p.date >= c.fold_date
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
}


def render_body(name: str, sources: dict[str, str], template: str | None = None) -> str:
    """템플릿의 읽는 자리를 `sources[table]`(관계식 문자열)로 채운다."""
    body = template if template is not None else TEMPLATES[name]
    fill = {t: sources[t] for t in MACRO_INPUTS[name]}
    return body.format(lag_factor=FACTOR_LAG_SESSIONS, lag_price=PRICE_LAG_SESSIONS,
                       lag_consensus=CONSENSUS_LAG_SESSIONS, **fill).strip()


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


__all__ = ["CONSENSUS_LAG_SESSIONS", "FACTOR_LAG_SESSIONS", "MACRO_DEPENDS", "MACRO_INPUTS",
           "PRICE_LAG_SESSIONS", "SIGNATURES", "TEMPLATES", "install_temp_macros",
           "parquet_source", "render_body", "render_macros"]
