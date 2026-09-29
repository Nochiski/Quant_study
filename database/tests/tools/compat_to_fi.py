"""compat(v3 `quant.db` 스키마) 표 → `model.contracts.FactorInputs`(`fi_*`) 어댑터.

두 가지 용도다.
  ① 테스트 — 골든 픽스처(`tests/fixtures/model_golden/<D>/`, compat 표의 parquet 절단본)를 이식
     엔진이 읽는 `fi_*` 표로 바꾼다. v3 원본은 compat 표를 읽고 이식 엔진은 `fi_*` 를 읽으므로,
     이 변환이 v3 가 본 값과 1:1 이어야 골든 대조(G-M3 ②)가 선다.
  ② 규약 문서 — W1-b(`factor_inputs` 층)가 `fi_*` 를 채울 때 v3 동등성을 지키려면 어떤 의미로
     채워야 하는지를 표마다 적어 둔다(아래 `_MAP_*` 주석). 이 파일이 그 대응의 참조다.

v3 가 읽는 방식(원본 `backend/`, 읽기 전용 사본 기준):
  - 가격   `db/repositories/price_repo.py:51-60` —
           `trade_date BETWEEN D-550일 AND D ORDER BY trade_date`,
           값은 `adj_close if not None else close`(`scoring/factors/momentum.py:60-65`).
  - 수급   `db/repositories/flow_repo.py:57-79` —
           `trade_date <= D ORDER BY trade_date DESC LIMIT 20`.
  - 시총   `scoring/engine.py:245-255` — `stocks.market_cap`(억원, > 0 만).
  - 리비전 `db/repositories/consensus_revision_daily_repo.py:120-140`(종목 최신 base_date 1행) +
           `consensus_revision_compare_repo.py:148-158`(종목당 1행, 1w·1m·3m·1y 열).
  - 재무   `scoring/factors/quality.py:10-19`·`valuation.py:8-14` —
           `period_type='annual' AND data_type IS NULL ORDER BY period DESC LIMIT 2(1)`.

v2 가 읽는 방식(`scoring/v2_data_loader.py`, 가격·수급·시총은 위와 같은 표를 창만 달리 읽는다):
  - 가격   `:9-23` — `trade_date BETWEEN date(D,'-200 days') AND D`, 최근 130행,
           값은 `CAST(COALESCE(adj_close, close) AS INTEGER)`.
  - 수급   `:98-119` — `trade_date BETWEEN date(D,'-40 days') AND D`, 최근 20행, NULL → 0.
  - 시총   `:88-95` — `stocks.market_cap IS NOT NULL AND market_cap > 0`(= v2 유니버스).
  - 추정·확정 `:26-59` — `consensus_annual` 의 전년·당해·차년 12월기(`period_type='annual'`).

이 모듈은 테스트 도구라 duckdb 를 쓴다(엔진 본체는 표준 라이브러리만).
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

import duckdb
from model.contracts import FI_TABLES, FLOW_SUBJECTS, FactorInputs

Row = Mapping[str, object]

# compat 에서 읽는 표(골든 parquet 파일명과 같다). consensus_annual 은 v2 만 읽는다
# (→ fi_consensus_annual, v3 엔진은 읽지 않는다).
COMPAT_TABLES = ("stocks", "daily_prices", "investor_detail_flows", "financial_summary",
                 "consensus_revision_daily", "consensus_revision_compare", "consensus_annual")

# compat revision_compare 의 기간 접미사 → fi_consensus.horizon. `1y` 는 계약 horizon 에 없어 버린다
# (v3 엔진은 1w·1m·3m 만 읽는다 — `scoring/factors/revision.py:11-15`).
_COMPARE_HORIZONS = ("1w", "1m", "3m")
# fi_consensus 값 열 ← compat 열 이름(같다). `opinion` 은 계약에 자리가 없어 버린다(v3 점수 미사용).
_CONSENSUS_VALUES = ("revenue", "op", "ni", "eps", "bps", "per", "pbr", "roe")


def _d(v: object) -> date | None:
    """compat TEXT 날짜('YYYY-MM-DD') → DATE.

    계약 dtype 이 DATE 라 W1-b 가 duckdb 에서 받는 모양(datetime.date)과 맞춘다.
    """
    return None if v is None else date.fromisoformat(str(v)[:10])


def _f(v: object) -> float | None:
    """INTEGER 로 저장된 compat 값 → 계약 DOUBLE. 2^53 미만 정수라 변환이 정확하다(값 불변)."""
    return None if v is None else float(v)  # type: ignore[arg-type]


def _row(table: str, **values: object) -> dict[str, object]:
    """계약 컬럼 순서의 행. 넘기지 않은 열은 NULL — compat 에 원천이 없다는 뜻이다."""
    cols = FI_TABLES[table].column_names
    extra = set(values) - set(cols)
    if extra:
        raise KeyError(f"{table}: 계약 밖 열 {sorted(extra)}")
    return {c: values.get(c) for c in cols}


# ── fi_prices · fi_adj_prices ← daily_prices ────────────────────────────────────────────────
# 행 집합 = compat daily_prices 행 그대로(정지 세션 바 없음 — compat 도 없다).
# fi_adj_prices.adj_close = COALESCE(adj_close, close): v3 가 가격을 읽는 규칙 그 자체다
#   (`momentum.py:60-65`·`quality.py:124-125`). W1-b 는 수정가가 없는 칸을 NULL 로 둬도 된다 —
#   엔진이 같은 COALESCE 를 fi_prices.close 로 한 번 더 한다.
# adj_factor·adj_ok 는 compat 에 없다(NULL). v3 이식 엔진은 둘 다 읽지 않는다.
def _map_prices(daily_prices: Sequence[Row]) -> tuple[list[dict], list[dict]]:
    prices, adj = [], []
    for r in daily_prices:
        t, d = r["stock_code"], _d(r["trade_date"])
        prices.append(_row("fi_prices", ticker=t, date=d, open=r["open"], high=r["high"],
                           low=r["low"], close=r["close"], volume=r["volume"],
                           amount=r["amount"], price_source="krx"))
        a = r["adj_close"] if r["adj_close"] is not None else r["close"]
        adj.append(_row("fi_adj_prices", ticker=t, date=d, adj_close=_f(a)))
    return prices, adj


# ── fi_flows ← investor_detail_flows ────────────────────────────────────────────────────────
# 주체 12개 1:1(백만원, 순매수). compat 는 `round(원 / 1e6)` 정수 백만원이다 — W1-b 가 반올림 전
# 값을 실으면 compat 와 flow 원값이 어긋난다. compat 는 전 주체 NULL 인 칸에 행을 만들지 않는다
# (`compat/mappings.py` _INVESTOR_FLOWS_SQL) — v3 는 **행 개수**로 20개를 세므로(flow_repo LIMIT 20)
# W1-b 도 그런 칸에 행을 두면 안 된다.
def _map_flows(flows: Sequence[Row]) -> list[dict]:
    return [_row("fi_flows", ticker=r["stock_code"], date=_d(r["trade_date"]),
                 **{s: _f(r[s]) for s in FLOW_SUBJECTS})
            for r in flows]


# ── fi_universe ← stocks ────────────────────────────────────────────────────────────────────
# compat 는 `--model-universe estimates` 에서 추정치 없는 종목의 market_cap 을 NULL 로 내보내
# v3 의 `market_cap >= min_market_cap` 필터가 그 종목을 빼게 한다(`compat/quant_db.py:79-81`).
# 그래서 eligible = market_cap IS NOT NULL 이 compat 유니버스와 같은 뜻이다.
# (보통주·스팩·KOSPI/KOSDAQ 제한은 compat 가 행 자체를 거르므로 sec_type 은 NULL 로 둔다.)
# market_cap 은 compat 가 `round(원 / 1e8)` 정수 억원이다 — 시총 하한(1,000억) 경계와 flow 비율
# 분모가 이 값이라 W1-b 도 같은 반올림을 해야 v3 와 같다.
# sector 는 compat 에서 WICS 대분류 **이름**이다(sector_l1_name).
def _map_universe(stocks: Sequence[Row], score_date: date) -> list[dict]:
    out = []
    for r in stocks:
        cap = _f(r["market_cap"])
        ok = cap is not None
        out.append(_row(
            "fi_universe", ticker=r["stock_code"], date=score_date, name=r["stock_name"],
            market=r["market"], listed_date=_d(r["listed_date"]), market_cap=cap,
            mktcap_basis="krx", sector_l1_name=r["sector"], has_estimates=ok, eligible=ok,
            exclude_reason=None if ok else "compat market_cap NULL(추정치 유니버스 밖)"))
    return out


# ── fi_consensus ← consensus_revision_daily(cur) + consensus_revision_compare(1w·1m·3m) ────
# compat 는 두 표를 같은 WISE 판·같은 결산기(`as_of 이상 최소 target_period`)에서 만든다
# (`compat/mappings.py:180-212`). 그래서 cur 와 1w·1m·3m 이 같은 target_period 에 놓인다.
# daily 행은 종목당 1행(최신 base_date)이라 horizon=cur 로 옮기고 obs_date = base_date,
# fetched_date = collected_date. compare 행에는 관측일이 없다(NULL).
# compat 는 as_of 판에 그 결산기 셀이 하나라도 있으면 daily·compare 두 행을 **다** 만든다(값이
# NULL 이어도). v3 는 두 행이 모두 있어야 리비전을 계산하므로(`revision.py:30-33`) W1-b 도 그
# 결산기의 cur·1w·1m·3m 행을 값이 NULL 이어도 함께 실어야 같다.
def _map_consensus(daily: Sequence[Row], compare: Sequence[Row]) -> list[dict]:
    out = []
    for r in daily:
        out.append(_row("fi_consensus", ticker=r["stock_code"], target_period=r["target_period"],
                        horizon="cur", **{k: _f(r[k]) for k in _CONSENSUS_VALUES},
                        obs_date=_d(r["base_date"]), fetched_date=_d(r["collected_date"])))
    for r in compare:
        for h in _COMPARE_HORIZONS:
            out.append(_row("fi_consensus", ticker=r["stock_code"],
                            target_period=r["target_period"], horizon=h,
                            **{k: _f(r[f"{k}_{h}"]) for k in _CONSENSUS_VALUES}))
    return out


# ── fi_fin_summary ← financial_summary(data_type IS NULL 행만) ─────────────────────────────
# compat financial_summary 에는 WISE (E) 추정 슬롯이 `data_type='estimate'` 로 섞여 있고 v3 는
# `data_type IS NULL` 로 그것을 뺀다. 계약 fi_fin_summary 에는 data_type 열이 없으므로 **확정치만
# 싣는 표**로 정한다 — 추정치는 fi_consensus 몫이다. (추정 행을 싣으면 v3 이식 엔진이 그것을
# 확정 재무로 읽는다 → W1-b 는 (E) 슬롯을 싣지 않는다.)
# period_type 은 compat 규칙 그대로다: 결산월이 12 면 annual, 아니면 quarter(DQ-11 — 비12월 결산
# 사업보고서가 v3 창에서 빠지는 동작까지 v3_zscore@1.0 이 재현한다).
# 금액(revenue·op·ni·fcf·capex·gross_profit·total_assets)은 compat 에서 정수 억원(반올림)이다.
# accounting_standard → fs_basis. capex_basis·available_date 는 compat 에 없다(NULL).
_FIN_SAME = ("revenue", "op", "ni", "eps", "bps", "per", "pbr", "roe", "roa", "debt_ratio", "fcf",
             "capex", "op_margin", "ni_margin", "dividend_yield", "ev_ebitda", "yoy",
             "gross_profit", "total_assets")


def _map_fin(fin: Sequence[Row]) -> list[dict]:
    return [_row("fi_fin_summary", ticker=r["stock_code"], period=r["period"],
                 period_type=r["period_type"], **{k: _f(r[k]) for k in _FIN_SAME},
                 shares=r["shares"], fs_basis=r["accounting_standard"])
            for r in fin if r["data_type"] is None]


# ── fi_consensus_annual ← consensus_annual (v2 원천) ─────────────────────────────────────────
# v2 는 `consensus_annual` 의 전년·당해·차년 12월기 op·ni·per 를 읽는다(`v2_data_loader.py:26-59`,
# `period IN (Y-1/12, Y/12, Y+1/12) AND period_type='annual'`). compat 는 이 표를 stage
# `stg_consensus_annual`(WISE c1050001 T2Y) 종목별 최신 판 하나에서 만든다
# (`compat/mappings.py` _CONSENSUS_ANNUAL_SQL). 행을 1:1 로 옮긴다:
#   data_type  'estimate' → 'E' · 'actual' → 'A'(compat 는 period_kind 'E' 만 estimate, 그 밖은
#              actual 로 쓴다). 다른 값은 compat 규약 밖이라 거절한다.
#   period_type 은 계약에 없다 — compat 에서 결산월 12 ⇔ annual 이라 period 가 대신한다
#              (v2 엔진이 Y/12 기만 읽으므로 quarter 행(비12월 결산, DQ-11)은 실어도 읽히지 않는다).
#   fetched_date: consensus_annual 에 날짜 열이 없다(NULL).
# W1-b 규약: v3 의 fi_consensus(매트릭스)·fi_fin_summary(cF3002)와 같은 기·항목이어도 **다른 값**
# 이다(09-28 골든 당해 cur op 318 · ni 299 · per 618 종목, 전년 확정 op 564 · ni 595 종목) — 이 표는
# c1050001 값을 그대로 싣는다. 값이 전부 NULL 인 행도 v2 에게는 "그 종목이 있다"는 뜻이라(성장 0 ·
# 밸류 100 으로 갈린다 — 엔진 docstring) 빼지 않는다.
_ANNUAL_DATA_TYPE = {"estimate": "E", "actual": "A"}
_ANNUAL_VALUES = ("revenue", "op", "ni", "eps", "per")


def _map_consensus_annual(annual: Sequence[Row]) -> list[dict]:
    out = []
    for r in annual:
        dt = r["data_type"]
        if dt not in _ANNUAL_DATA_TYPE:
            raise ValueError(f"consensus_annual {r['stock_code']} {r['period']}: "
                             f"data_type {dt!r} ∉ {tuple(_ANNUAL_DATA_TYPE)}")
        out.append(_row("fi_consensus_annual", ticker=r["stock_code"], period=r["period"],
                        data_type=_ANNUAL_DATA_TYPE[dt],
                        **{k: _f(r[k]) for k in _ANNUAL_VALUES}))
    return out


def compat_to_fi(compat: Mapping[str, Sequence[Row]], score_date: str,
                 build_id: str = "compat") -> FactorInputs:
    """compat 표(표 이름 → 행 dict 목록) → FactorInputs(아침 확정판).

    v3·v2 이식 엔진이 이 한 판을 같이 읽는다(v2 추정·확정은 fi_consensus_annual 만).
    """
    missing = [t for t in COMPAT_TABLES if t not in compat]
    if missing:
        raise KeyError(f"compat 표 없음 {missing}")
    prices, adj = _map_prices(compat["daily_prices"])
    tables = {
        "fi_prices": prices,
        "fi_adj_prices": adj,
        "fi_flows": _map_flows(compat["investor_detail_flows"]),
        "fi_universe": _map_universe(compat["stocks"], date.fromisoformat(score_date)),
        "fi_consensus": _map_consensus(compat["consensus_revision_daily"],
                                       compat["consensus_revision_compare"]),
        "fi_consensus_annual": _map_consensus_annual(compat["consensus_annual"]),
        "fi_fin_summary": _map_fin(compat["financial_summary"]),
        # fi_credit: compat 에 원천이 없다(v4 전용 표) — 싣지 않는다.
    }
    return FactorInputs(date=score_date, basis="morning", build_id=build_id, tables=tables)


def read_parquet_rows(path: Path) -> list[dict[str, object]]:
    rel = duckdb.sql(f"SELECT * FROM read_parquet('{path.as_posix()}')")
    cols = rel.columns
    return [dict(zip(cols, t, strict=True)) for t in rel.fetchall()]


def load_golden(golden_dir: Path) -> FactorInputs:
    """골든 픽스처 디렉터리(`meta.json` + compat 표 parquet) → FactorInputs."""
    meta = json.loads((golden_dir / "meta.json").read_text(encoding="utf-8"))
    compat = {t: read_parquet_rows(golden_dir / f"{t}.parquet") for t in COMPAT_TABLES}
    return compat_to_fi(compat, meta["score_date"], build_id=f"golden_{meta['score_date']}")
