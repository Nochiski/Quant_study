"""뷰 매크로 SQL 템플릿 — `equity.duckdb` 의 테이블 매크로 본문 (DESIGN v1.2 §5 · 결정 1·6).

S06 이 내는 4개: `v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_firm_mktcap`. 본문은 하나의 템플릿이고
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
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from . import inputs

FACTOR_LAG_SESSIONS = 0     # 계수 available_date 컷오프 기본 랙(세션). 위 docstring 근거
PRICE_LAG_SESSIONS = 0      # 가격 행 컷오프 랙(세션) — 0 이라 `date <= as_of` 와 같다

# 매크로 이름 → (시그니처, 읽는 테이블). 시그니처는 catalog._MACRO_NAME_RE 규약.
SIGNATURES: dict[str, str] = {
    "v_cum_adj": "v_cum_adj(as_of, lag_override := NULL)",
    "v_adj_price": "v_adj_price(as_of, lag_override := NULL)",
    "v_adj_volume": "v_adj_volume(as_of, lag_override := NULL)",
    "v_firm_mktcap": "v_firm_mktcap(d)",
}
MACRO_INPUTS: dict[str, tuple[str, ...]] = {
    "v_cum_adj": ("price_daily", "adj_factor", "trading_calendar"),
    "v_adj_price": ("price_daily", "adj_factor", "trading_calendar"),
    "v_adj_volume": ("price_daily", "adj_factor", "trading_calendar"),
    "v_firm_mktcap": ("price_daily", "corp_ticker"),
}
# 매크로가 다른 매크로를 부르는 경우 — 같은 카탈로그(또는 같은 세션)에 함께 있어야 한다.
MACRO_DEPENDS: dict[str, tuple[str, ...]] = {
    "v_adj_price": ("v_cum_adj",), "v_adj_volume": ("v_cum_adj",)}

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
}


def render_body(name: str, sources: dict[str, str], template: str | None = None) -> str:
    """템플릿의 읽는 자리를 `sources[table]`(관계식 문자열)로 채운다."""
    body = template if template is not None else TEMPLATES[name]
    fill = {t: sources[t] for t in MACRO_INPUTS[name]}
    return body.format(lag_factor=FACTOR_LAG_SESSIONS, lag_price=PRICE_LAG_SESSIONS,
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


__all__ = ["FACTOR_LAG_SESSIONS", "MACRO_DEPENDS", "MACRO_INPUTS", "PRICE_LAG_SESSIONS",
           "SIGNATURES", "TEMPLATES", "install_temp_macros", "parquet_source", "render_body",
           "render_macros"]
