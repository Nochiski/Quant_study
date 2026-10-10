"""장 마감 판 T 행 — v3 소비자가 T 저녁에 읽을 가격·수급 행
(컷오버 QL-D · N-42 Q3 · T-2 · T-32 · T-33).

`--basis evening` 의 equity 판은 직전 거래일 D' 까지다(D' 연구 확정판 `m_` — 그날 저녁 판 `e_` 도
T 는 캘린더 밖). v3 소비자(07:00 브리핑·위키·uni)는 T 저녁에 T 행이 필요하므로 `daily_prices`·
`investor_detail_flows` 의 T 행을 **원장 두 개**에서 만든다.

대상 종목 — D' `universe_daily` 이월 ∩ `V3_STOCK_FILTER`(PR-4 와 같은 전제, 장 마감 수집 PR-1 의
  대상과 같은 술어). 상태 변화는 다음 날 아침 확정판에서 본다. **T 당일 신규 상장 종목은 빠진다.**
원천 — 종목마다 하나. 가격·수급을 섞지 않고, 두 표가 같은 선택(임시 표 `PICK_TABLE`)을 쓴다.
  ① `postclose`    `data/raw/postclose.db` 의 T 행 중 `price_valid='1'` · 종가 > 0 · 거래량 있음
                   (`daily.kw_daily.ka10060_postclose_price_usable_sql` — fi 장 마감 판 PR-5 와 공유)
                   (16:00 전 응답 — 종가 = KRX 공식 종가(정규장, T-33), 수급 = 15:40 확정, N-35)
  ② `kiwoom_2105`  그 밖(행 없음 · `price_valid` 가 '0'·NULL · 종가 빈·0 이하·거래량 빈 행 — PR-1 리뷰 s4)은
                   키움 원장 `data/raw/kiwoom.db` 의 같은 TR 표 T 행(21:05 저녁 수집, 애프터마켓
                   포함 — 지금 v3 와 같은 뜻). 21:05 전에 돌면 '행 없음'으로 남고 다시 돌면
                   채워진다.
파싱 — stage 규칙 객체의 열 규칙 그대로(`stage.build._cast_expr` — 부호·쉼표·단위 스케일을 두 곳에
  적지 않는다, P4). 수급은 원문 백만원 → 원(stage ×1e6) → v3 백만원(`mappings._FLOW_SELECT`).
v3 NOT NULL 채움(T-32 = D2-9 (c)) — v3 T 행은 ka10081 이 늘 시·고·저·거래대금을 줘서 같은 상황이
  없다. open·high·low = 종가, amount = 종가 × 거래량 ÷ 1e6(백만원, 근사 — NULL 이면 07:00 브리핑
  거래대금 상위가 TypeError, COMPAT_LAYER §4-1). 다음 날 아침 KRX 행으로 날짜 단위 교체된다.
adj_close — T 종가 그대로(QL-E · T-40). v3 기준(KRX 기준가 사슬 K)에서 장 마감 판의 최신 행은 T 이고, 그날
  기준가 = D' KRX 종가면 T 의 단계가 1 이라 K(T) = K(D') 다 — 창 안 D' 이하 행(`mappings` daily_prices 는 D' 까지의
  사슬로 짓는다)과 같은 기준이고, 직전 행 덮어쓰기(T-41)도 값이 같다. 그날 기준가가 D' KRX 종가와 다르면(T-6 첫
  조건 `daily.kw_daily.ka10060_base_price_differs_sql`) T 에 사건 단계가 있다 → NULL. 그 종목의 D' 이하 행은
  D' 기준으로 두고, 다음 날 아침 KRX 반영(`--basis morning --date T`)이 T 단계로 창 전체(adj_close·덮어쓰기 행)와
  창 밖 adj_close 를 다시 쓴다.
이 채움들은 v3 외부 계약 때문이고 equity·모델 입력으로는 돌아가지 않는다. 만료 = D2-9 (a)(저녁
ka10081) 또는 v3 소비자 직독 전환.

`compat.quant_db` 만 이 모듈을 부른다 — 그것도 장 마감 판을 내보내는 순간에(함수 안 import). stage
빌더·규칙 모듈을 끌어오므로 장 마감 수집기(`daily.postclose` → `compat.mappings`)의 import 에 실리면
안 된다(16:00 창).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
from daily import calendar as daily_calendar
from daily.kw_daily import (
    ka10060_base_price_differs_sql,
    ka10060_postclose_price_usable_sql,
)
from stage.build import _cast_expr
from stage.model import KIND_NUMERIC, TableRule
from stage.rules_kiwoom import STG_FLOW_DAILY_KIWOOM, STG_FLOW_POSTCLOSE_KIWOOM

from .mappings import _FLOW_ANY, _FLOW_SELECT, V3_STOCK_FILTER, TableMapping
from .quant_db import CompatEmptyError, CompatError
from .units import KRW_PER_MN

# 행 원천 열 — v3 표에 넣기 전에 떼어 `TableResult.t_rows` 에 남긴다
SOURCE_COL = "t_source"
SOURCE_POSTCLOSE = "postclose"            # T-30 과 같은 이름
# 21:05 저녁 키움 원장 — `--basis evening` 과 뜻이 겹치지 않는 이름
SOURCE_KIWOOM_2105 = "kiwoom_2105"
LEDGER_TABLE = STG_FLOW_DAILY_KIWOOM.sources[0].table    # 두 원장의 같은 TR 표 이름(ka10060)
PICK_TABLE = "_t_pick"
# 원장별 파싱 규칙 — 장 마감 원장은 PR-2 stage 규칙(키움 원장 규칙과 같은 ka10060 열 객체 +
# `price_valid` BOOLEAN), 21:05 원장은 연구 stage 규칙이다
POSTCLOSE_RULE: TableRule = STG_FLOW_POSTCLOSE_KIWOOM
KIWOOM_RULE: TableRule = STG_FLOW_DAILY_KIWOOM
# (ATTACH 별칭, CLI 인자) — 원천 순서와 같다
_LEDGERS = (("t_pc", "--postclose-db"), ("t_kw", "--kiwoom-db"))


def _parsed(rule: TableRule) -> str:
    """원장 원문 수치 열(종가·전일대비·거래량·수급 13)을 stage 규칙대로 읽는 SELECT 목록
    (별칭 `r`)."""
    return ",\n           ".join(_cast_expr(c, 'r."' + c.src + '"') + " AS " + c.name
                                  for c in rule.columns if c.kind == KIND_NUMERIC)


if _parsed(POSTCLOSE_RULE).replace("\n", "") != _parsed(KIWOOM_RULE).replace("\n", ""):
    # 두 원장을 UNION 하므로 열 이름·타입이 같아야 한다 — 규칙이 갈라지면 import 에서 멈춘다
    raise RuntimeError("장 마감 원장과 키움 원장의 ka10060 열 규칙이 다르다 — T 행을 합칠 수 없다")

UNIVERSE_SQL = f"""
SELECT DISTINCT u.ticker
FROM {{universe_daily}} u
WHERE u.date = DATE '{{d_prime}}' AND {V3_STOCK_FILTER}
"""

# 판 이음매 — 판의 마지막 세션(≤ T)이 D' 여야 T 를 얹는다(PR-4 MD-SEAM 과 같은 전제)
SEAM_SQL = """
SELECT max(u.date) FROM {universe_daily} u WHERE u.date <= DATE '{t_iso}'
"""

# 장 마감 원장 `price_valid` — PR-2 열 규칙(BOOLEAN)대로 읽는다. '0'·NULL 은 거짓/NULL 이라 ② 로
# 넘어간다
_PRICE_VALID = _cast_expr(POSTCLOSE_RULE.column("price_valid"), 'r."price_valid"')

# ① 을 고르는 술어 — 가격을 쓸 수 있는 장 마감 행(fi 장 마감 판 T 가격 행과 같은 정의, P4)
_PRICE_USABLE = ka10060_postclose_price_usable_sql("s.price_ok", "s.close_krw", "s.volume_shr")

# 종목마다 원천 하나 — ① postclose(`_PRICE_USABLE`) → ② kiwoom_2105.
# 두 표가 이 임시 표를 같이 쓴다.
PICK_SQL = f"""
CREATE OR REPLACE TEMP TABLE {PICK_TABLE} AS
WITH uni AS ({UNIVERSE_SQL}),
raw AS (
    SELECT '{SOURCE_POSTCLOSE}' AS {SOURCE_COL}, 0 AS prio, CAST(r.ticker AS VARCHAR) AS ticker,
           {_PRICE_VALID} AS price_ok,
           {_parsed(POSTCLOSE_RULE)}
    FROM {{postclose}} r
    WHERE r.dt = '{{t_ymd}}'
    UNION ALL
    SELECT '{SOURCE_KIWOOM_2105}' AS {SOURCE_COL}, 1 AS prio, CAST(r.ticker AS VARCHAR) AS ticker,
           TRUE AS price_ok,
           {_parsed(KIWOOM_RULE)}
    FROM {{kiwoom}} r
    WHERE r.dt = '{{t_ymd}}'
)
SELECT s.* EXCLUDE (prio, price_ok)
FROM raw s JOIN uni u ON u.ticker = s.ticker
WHERE s.prio = 1 OR {_PRICE_USABLE}
QUALIFY row_number() OVER (PARTITION BY s.ticker ORDER BY s.prio) = 1
"""

_BASE_DIFFERS = ka10060_base_price_differs_sql("t.close_krw", "t.pred_pre_krw", "v.prev_close")

DAILY_PRICES_SQL = f"""
WITH prev AS (
    SELECT p.ticker, p.close AS prev_close
    FROM {{price_daily}} p
    WHERE p.date = DATE '{{d_prime}}' AND p.basis = 'krx'
)
SELECT
    t.ticker                                       AS stock_code,
    '{{t_iso}}'                                    AS trade_date,
    CAST(t.close_krw AS BIGINT)                    AS open,
    CAST(t.close_krw AS BIGINT)                    AS high,
    CAST(t.close_krw AS BIGINT)                    AS low,
    CAST(t.close_krw AS BIGINT)                    AS close,
    CAST(t.volume_shr AS BIGINT)                   AS volume,
    CAST(round(CAST(t.close_krw AS DOUBLE) * CAST(t.volume_shr AS DOUBLE) / {KRW_PER_MN}.0)
         AS BIGINT)                                AS amount,
    CAST(CASE WHEN NOT {_BASE_DIFFERS}
              THEN CAST(t.close_krw AS DOUBLE)
         END AS DOUBLE)                            AS adj_close,
    t.{SOURCE_COL}
FROM {PICK_TABLE} t
LEFT JOIN prev v ON v.ticker = t.ticker
ORDER BY t.ticker
"""

# 미측정(전 주체 NULL) 행은 만들지 않는다 — `mappings._INVESTOR_FLOWS_SQL` 과 같은 규칙
INVESTOR_FLOWS_SQL = f"""
SELECT f.ticker                   AS stock_code,
       '{{t_iso}}'                AS trade_date,
       {_FLOW_SELECT},
       f.{SOURCE_COL}
FROM {PICK_TABLE} f
WHERE coalesce({_FLOW_ANY}) IS NOT NULL
ORDER BY f.ticker
"""

# v3 표 → T 행 SQL. 열 순서는 표 매핑의 `columns` + `SOURCE_COL` 이다(`build` 가 확인)
TABLE_SQL: dict[str, str] = {"daily_prices": DAILY_PRICES_SQL,
                             "investor_detail_flows": INVESTOR_FLOWS_SQL}


@dataclass(frozen=True)
class TPart:
    """표 하나의 T 행(v3 열 순서)과 원천 기록(`TableResult.t_rows`)."""

    rows: list[tuple]
    info: dict[str, object]


def dprime(t: date, calendar_dir: Path | None) -> date:
    """T 가 `daily.calendar` 거래일인지 보고 직전 거래일 D' 를 준다(PR-4 `_evening_dprime` 과
    같은 판정)."""
    try:
        cal = daily_calendar.load() if calendar_dir is None else daily_calendar.load(calendar_dir)
        trading = cal.is_trading_day(t)
        out = cal.prev_trading_day(t)
    except (daily_calendar.CalendarUnavailable, KeyError) as e:
        raise CompatError(f"장 마감 판: daily.calendar 를 읽지 못했다"
                          f"(calendar_dir={calendar_dir}): {e}") from e
    if not trading:
        raise CompatError(f"장 마감 판 T={t.isoformat()} 는 daily.calendar 거래일이 아니다 — "
                          "T 행을 만들지 않는다")
    return out


def ledgers(postclose_db: Path | None, kiwoom_db: Path | None) -> tuple[Path, Path]:
    """T 행 원천 원장 두 파일. 빠졌거나 없으면 멈춘다 — 경로 실수를 '그날 행 없음'으로 읽지
    않는다(P1). 과거 T 재생처럼 장 마감 원장이 아직 없던 날은 `daily.postclose.connect` 로 세운 빈
    원장을 준다.
    """
    out: list[Path] = []
    for (_, flag), path in zip(_LEDGERS, (postclose_db, kiwoom_db), strict=True):
        if path is None:
            raise CompatError(f"--basis evening 의 daily_prices·investor_detail_flows 는 T 행 "
                              f"원천 원장이 필요하다 — {flag} 가 없다(QL-D)")
        if not Path(path).is_file():
            raise CompatError(f"T 행 원장 파일이 없다: {flag} {path}")
        out.append(Path(path))
    return out[0], out[1]


def check_seam(duck: duckdb.DuckDBPyConnection, universe_daily: str,
               params: dict[str, str]) -> None:
    """판 `universe_daily` 의 T 이하 마지막 날이 D' 인가. 아니면 쓰기 전에 멈춘다 — T 가 이미 있으면
    (아침 확정판) KRX 확정 행을 원장 값으로 덮게 되고, D' 보다 앞이면 D' 행 없이 T 가 붙는다."""
    row = duck.execute(SEAM_SQL.format(universe_daily=universe_daily, **params)).fetchone()
    last = None if row is None or row[0] is None else row[0].isoformat()
    if last == params["d_prime"]:
        return
    why = ("판에 이미 T 가 있다(아침 확정판) — --basis morning 으로 내보낸다"
           if last == params["date"]
           else "판이 D' 까지 오지 않았다 — D' 행 없이 T 를 얹지 않는다")
    raise CompatError(f"장 마감 판 이음매 불일치: T={params['date']} 의 직전 거래일 "
                      f"D'={params['d_prime']}(daily.calendar) ≠ 판 universe_daily 의 T 이하 "
                      f"마지막 날 {last} — {why}")


def build(duck: duckdb.DuckDBPyConnection, selected: list[TableMapping],
          exprs: dict[str, str], params: dict[str, str],
          paths: tuple[Path, Path]) -> dict[str, TPart]:
    """표마다 T 행 — 쓰기 전에 전부 만든다(한 표라도 막히면 아무 표도 쓰지 않는다).

    `exprs` 는 equity 표 → `read_parquet(...)` 관계식. 원천 선택은 임시 표 하나로 한 번만 한다(두
    표가 같은 종목 원천을 쓴다). 멈추는 경우: 원장에 그 TR 표가 없다 · 표의 T 행이 0(07:00 브리핑이
    D' 를 T 로 읽게 된다 — COMPAT_LAYER §4-1 DEFECT-C02).
    """
    universe = {str(r[0]) for r in duck.execute(
        UNIVERSE_SQL.format(universe_daily=exprs["universe_daily"], **params)).fetchall()}
    try:
        for (alias, flag), path in zip(_LEDGERS, paths, strict=True):
            lit = str(path.resolve()).replace("'", "''")
            duck.execute(f"ATTACH '{lit}' AS {alias} (TYPE sqlite, READ_ONLY)")
            n = duck.execute("SELECT count(*) FROM duckdb_tables() WHERE database_name = ? "
                             "AND table_name = ?", [alias, LEDGER_TABLE]).fetchone()
            if not n or not n[0]:
                raise CompatError(f"T 행 원장에 {LEDGER_TABLE} 표가 없다: {flag} {path}")
        duck.execute(PICK_SQL.format(universe_daily=exprs["universe_daily"],
                                     postclose=f't_pc."{LEDGER_TABLE}"',
                                     kiwoom=f't_kw."{LEDGER_TABLE}"', **params))
    finally:
        for alias, _ in _LEDGERS:
            duck.execute(f"DETACH DATABASE IF EXISTS {alias}")
    out: dict[str, TPart] = {}
    for m in selected:
        if m.v3_table not in TABLE_SQL:
            continue
        cur = duck.execute(TABLE_SQL[m.v3_table].format(
            **{t: exprs[t] for t in m.sources}, **params))
        cols = [d[0] for d in (cur.description or [])]
        if cols != [*m.columns, SOURCE_COL]:
            raise CompatError(f"T 행 SELECT 컬럼이 선언과 다르다: table={m.v3_table} got={cols}")
        got = cur.fetchall()
        if not got:
            raise CompatEmptyError(
                f"T 행 0: table={m.v3_table} T={params['date']} — postclose·21:05 원장에 그날 "
                f"행이 없다(D'={params['d_prime']} 를 T 로 읽게 두지 않는다, 기존 행은 그대로)")
        out[m.v3_table] = TPart([r[:-1] for r in got], _info(got, universe, params, paths))
    return out


def _info(got: list[tuple], universe: set[str], params: dict[str, str],
          paths: tuple[Path, Path]) -> dict[str, object]:
    """원천 기록 — 원천별 행 수, 21:05 원장으로 대체한 종목, 행 없는 종목과 그 비율(기록형,
    상한 없음).
    postclose 종목은 나머지 전부라 행마다 원천이 남는다."""
    source = {str(r[0]): str(r[-1]) for r in got}
    kiwoom = sorted(t for t, s in source.items() if s == SOURCE_KIWOOM_2105)
    missing = sorted(universe - set(source))
    return {"date": params["date"], "d_prime": params["d_prime"], "n_universe": len(universe),
            SOURCE_POSTCLOSE: len(source) - len(kiwoom), SOURCE_KIWOOM_2105: len(kiwoom),
            "missing": len(missing),
            "missing_ratio": round(len(missing) / len(universe), 6) if universe else None,
            f"{SOURCE_KIWOOM_2105}_tickers": kiwoom, "missing_tickers": missing,
            "ledgers": {SOURCE_POSTCLOSE: str(paths[0]), SOURCE_KIWOOM_2105: str(paths[1])}}
