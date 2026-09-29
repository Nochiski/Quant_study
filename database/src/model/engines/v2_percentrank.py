"""v2 4팩터 PERCENTRANK 엔진 이식 (플랜 `docs/plans/2026-09-24-v3-merge.md` M2 W1-d · T2.4).

v3 저장소의 `backend/scoring/v2_engine.py`·`v2_data_loader.py`·`v2_factors/{momentum,growth,flow,
value,percentrank}.py` 를 **숫자 하나 바꾸지 않고** 옮겼다(G-M3 ①: v2 원본과 |Δ| ≤ 1e-9 · rank
동일). 원본 파일:줄은 함수마다 적는다. 다른 점은 입력뿐이다 — v2 는 compat sqlite 를 질의하고,
여기서는 `fi_*` 표(`FactorInputs`)만 읽는다. 표준 라이브러리만 쓰고, 순회는 전부 정렬된 순서라
입력 행 순서와 무관하게 같은 결과를 낸다.

흐름(`v2_engine.py:154-177`):
  유니버스(적격 ∧ 시총 > 0) → 팩터 4개: 하위 지표 원값 → 지표마다 PERCENTRANK.INC(0~1)
  → 결측(NaN)은 0 → Σ w·백분위 × 100 → 종합 = Σ 팩터가중 × 팩터점수(점수가 없는 팩터는 0.0)
  → `(−종합, 종목코드)` 정렬 · rank 1…N.

의도적으로 재현한 v2 동작(고치면 v2_percentrank@1.0 이 아니다 — 수정은 @1.1 에서):
  - 유니버스에 시총 하한도, D 가격 행 요건도 없다. D 가격이 없는 종목도 창 안의 옛 가격으로
    모멘텀을 받는다(`v2_engine.py:155-159`·`57-59`, `v2_data_loader.py:88-95`).
  - 가격은 COALESCE(수정종가, 종가)를 **정수로 절사**한 값이다(`v2_data_loader.py:13`).
  - 모멘텀 창은 달력일 200일 안의 **최근 130행**에서 21·63·126 행 전(`v2_data_loader.py:15-23`,
    `momentum.py:7-19`). 수급 창은 달력일 40일 안의 최근 20행에서 뒤 5·20행(`:104-118`,
    `flow.py:8-24`).
  - 결측 하위 지표는 백분위 0 으로 더한다 — 밸류는 w·(1 − 0) 이라 **만점**이 된다(`value.py:33-37`).
    그래서 추정·확정 행이 하나라도 있는데 PER 이 없거나 0 이하인 종목은 밸류 100, 행이 아예 없는
    종목은 밸류·성장 0.0 이다(`v2_engine.py:110-112` 의 `.get(code, 0.0)`).
  - 수급 행이 없는 종목도 순매수 0 으로 백분위에 들어간다(`v2_engine.py:71-72`). NULL 칸은 0.
  - YoY(%): 전년 < 0 → 당해 > 0 전환만 NaN. 흑자 → 적자는 음수, 적자 축소(−10 → −5)는 −50%
    (`growth.py:9-17`).
  - 결산기는 기준일 연도 Y 의 **12월기 고정**(전년 Y−1/12 · 당해 Y/12 · 차년 Y+1/12,
    `v2_data_loader.py:27-36`). 비12월 결산 종목은 성장·밸류 입력이 없다.
  - 추정·확정은 `fi_consensus_annual`(WISE c1050001 T2Y — v2 원본이 읽은 compat `consensus_annual`
    과 같은 원천)만 읽는다. v3 가 읽는 fi_consensus·fi_fin_summary 는 같은 기·항목이어도 값이
    달라 읽지 않는다. 전년·당해 값은 추정(E)이 있으면 E, 없으면 확정(A). 차년은 E 만
    (`v2_data_loader.py:45-58` + compat 의 "같은 기 E 우선 한 행").

v2 와 다른 점(맞출 원본 출력이 없는 곳만):
  - GAP-7 동점 순서: v2 는 `set` 순회 순서(PYTHONHASHSEED)대로 안정 정렬해 실행마다 다를 수
    있다(`v2_engine.py:97`·`121`). 여기서는 `(−종합, 종목코드)`로 고정한다.
  - 기준 종가 0(절사로 0 이 되는 0 < 수정종가 < 1 포함): v2 는 ZeroDivisionError 로 실행 전체가
    죽는다(`momentum.py:18`). 여기서는 그 수익률만 결측(NaN)으로 둔다.
  - stale guard(D 가격 행 < 2,000 이면 중단, `v2_engine.py:143-152`)는 판 빌드 게이트(W2) 몫이다.
"""
from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from model.contracts import (
    V2_SCORE_COLUMNS,
    EngineResult,
    FactorInputs,
    ModelSpec,
    UniverseRule,
)

Row = Mapping[str, Any]     # fi_* 행 — 값 타입은 계약(contracts.FI_TABLES)이 정한다
NAN = float("nan")

FACTORS = ("momentum", "growth", "flow", "value")            # v2_engine.py:166
# 팩터별 하위 지표 = 출력 원값 열(`v2_repo.py:7-14`). spec 의 sub_weights 는 이 안에서만 고른다.
SUB_KEYS = {
    "momentum": ("r1m", "r3m", "r6m"),                        # momentum.py:7
    "growth": ("op_yoy_cur", "op_yoy_next", "ni_yoy_cur", "ni_yoy_next"),  # v2_data_loader.py:67-72
    "flow": ("inst_5d", "inst_20d", "frgn_5d", "frgn_20d"),   # flow.py:8
    "value": ("per_cur", "per_next"),                         # v2_data_loader.py:81-84
}
FISCAL_MONTH = "12"                                           # v2_data_loader.py:27-29


def _rows(inputs: FactorInputs, name: str) -> Sequence[Row]:
    return inputs.rows(name)


def _iso(v: object) -> str:
    """DATE(date·datetime·'YYYY-MM-DD…') → 'YYYY-MM-DD'. v2 는 날짜를 TEXT 로 비교한다."""
    return str(v)[:10]


def _out(v: float | None) -> float | None:
    """NaN → None. v2 는 NaN 을 sqlite REAL 로 넘기고 sqlite 는 NaN 을 NULL 로 저장한다."""
    return None if v is None or math.isnan(v) else v


# ── 공용: PERCENTRANK · 가중합 ───────────────────────────────────────────────
def _percentrank_all(values: Mapping[str, float]) -> dict[str, float]:
    """v2 `percentrank.py:17-31` — 엑셀 PERCENTRANK.INC = (값보다 작은 개수)/(n − 1).

    동점은 가장 낮은 자리(bisect_left). NaN 은 NaN 으로 두고 n 에서 뺀다. 유효값이 2개 미만이면
    0.0. 정렬된 값만 보므로 입력 순서와 무관하다.
    """
    sorted_v = sorted(v for v in values.values() if not math.isnan(v))
    n = len(sorted_v)
    out: dict[str, float] = {}
    for code, v in values.items():
        if math.isnan(v):
            out[code] = NAN
        elif n < 2:
            out[code] = 0.0
        else:
            out[code] = bisect.bisect_left(sorted_v, v) / (n - 1)
    return out


def _factor_scores(raw: Mapping[str, Mapping[str, float]], sub_weights: Mapping[str, float],
                   *, invert: bool = False) -> dict[str, float]:
    """v2 `compute_{momentum,growth,flow}_scores`(`momentum.py:22-42`·`growth.py:20-40`·
    `flow.py:27-47`)와 `compute_value_scores`(`value.py:25-39`, `invert` — w·(1 − 백분위)).

    하위 지표마다 백분위 → NaN 은 0.0 → `sub_weights` 키 순서로 0.0 에서부터 더한 뒤 × 100.
    """
    pr_by_sub = {sub: _percentrank_all({c: raw[c][sub] for c in raw}) for sub in sub_weights}
    scores: dict[str, float] = {}
    for code in raw:
        total = 0.0
        for sub, w in sub_weights.items():
            pr = pr_by_sub[sub].get(code, 0.0)
            if math.isnan(pr):
                pr = 0.0
            if invert:
                total += w * (1 - pr)
            else:
                total += w * pr
        scores[code] = total * 100
    return scores


# ── 유니버스 · 가격 ──────────────────────────────────────────────────────────
def _universe(inputs: FactorInputs) -> dict[str, float]:
    """v2 `load_market_caps`(`v2_data_loader.py:88-95`) — {종목: 시총(억원)}, 시총 > 0.

    compat 는 추정치 없는 종목의 market_cap 을 NULL 로 내보내 v2 유니버스에서 뺀다
    (`compat/quant_db.py:79-81`) — fi 에서는 `eligible` ∧ market_cap > 0 이 같은 뜻이다.
    """
    caps: dict[str, float] = {}
    for r in _rows(inputs, "fi_universe"):
        cap = r["market_cap"]
        if r["eligible"] and cap is not None and cap > 0:
            caps[r["ticker"]] = cap
    return caps


def _closes(inputs: FactorInputs, universe: Mapping[str, float], d: str, days: int,
            rows_n: int) -> dict[str, list[int]]:
    """v2 `load_closes`(`v2_data_loader.py:9-23`): [D − days 달력일, D] 날짜 오름차순 최근
    rows_n 행.

    값 = `CAST(COALESCE(adj_close, close) AS INTEGER)` — fi_adj_prices 가 없거나 NULL 이면
    fi_prices.close, 정수로 **절사**(sqlite CAST 와 Python int 둘 다 0 쪽으로 자른다).
    """
    start = (date.fromisoformat(d) - timedelta(days=days)).isoformat()
    adj = {(r["ticker"], _iso(r["date"])): r["adj_close"]
           for r in _rows(inputs, "fi_adj_prices") if r["ticker"] in universe}
    by_code: dict[str, list[tuple[str, int]]] = {}
    for r in _rows(inputs, "fi_prices"):
        t = r["ticker"]
        if t not in universe:
            continue
        day = _iso(r["date"])
        if start <= day <= d:
            a = adj.get((t, day))
            by_code.setdefault(t, []).append((day, int(a if a is not None else r["close"])))
    return {c: [px for _, px in sorted(v)][-rows_n:] for c, v in by_code.items()}


def _momentum_raw(closes: Sequence[int], windows: Mapping[str, int]) -> dict[str, float]:
    """v2 `momentum.py:10-19` — (종가[−1] / 종가[−(n+1)] − 1) × 100(%), 행이 n+1 개 미만이면 NaN.

    기준 종가 0 은 v2 에서 ZeroDivisionError(실행 중단)다 — 여기서는 NaN(모듈 docstring).
    """
    n = len(closes)
    out: dict[str, float] = {}
    for key, w in windows.items():
        if n < w + 1 or closes[-(w + 1)] == 0:
            out[key] = NAN
        else:
            out[key] = (closes[-1] / closes[-(w + 1)] - 1) * 100
    return out


# ── 성장 · 밸류 ──────────────────────────────────────────────────────────────
def _yoy(current: float | None, previous: float | None) -> float:
    """v2 `growth.py:9-17` — YoY(%). 결측·전년 0·적자→흑자 전환은 NaN."""
    if current is None or previous is None:
        return NAN
    if previous == 0:
        return NAN
    if previous < 0 and current > 0:
        return NAN
    return (current / previous - 1) * 100


def _year_rows(inputs: FactorInputs, universe: Mapping[str, float],
               year: int) -> dict[str, tuple[Row | None, Row | None, Row | None]]:
    """v2 `load_consensus_for_year`(`v2_data_loader.py:26-59`) — 종목 → (전년, 당해, 차년) 행.

    `fi_consensus_annual` 의 data_type E(추정)·A(확정). v2 SQL 의 `period_type='annual'` 은
    compat 에서 결산월 12 와 같은 뜻이라 Y/12 기만 고르는 것으로 대신한다.
    전년·당해는 E 가 있으면 E, 없으면 A. 차년은 E 만(v2 `:54` — 백테스트 때 A 혼입 방지).
    세 기 중 어디든(차년 A 포함, 값이 전부 NULL 이어도) 행이 있는 종목만 담는다 — v2 의
    `by_code` 가 SQL 행이 있는 종목으로 만들어지기 때문이다(`:38-43`).
    """
    prev, cur, nxt = (f"{y}/{FISCAL_MONTH}" for y in (year - 1, year, year + 1))
    periods = (prev, cur, nxt)
    est: dict[tuple[str, str], Row] = {}
    act: dict[tuple[str, str], Row] = {}
    for r in _rows(inputs, "fi_consensus_annual"):
        if r["ticker"] not in universe or r["period"] not in periods:
            continue
        if r["data_type"] not in ("E", "A"):
            raise ValueError(f"fi_consensus_annual {r['ticker']} {r['period']}: "
                             f"data_type {r['data_type']!r} ∉ ('E', 'A')")
        (est if r["data_type"] == "E" else act)[(r["ticker"], r["period"])] = r

    def pick(t: str, p: str) -> Row | None:
        e = est.get((t, p))
        return e if e is not None else act.get((t, p))

    members = sorted({t for t, _ in est} | {t for t, _ in act})
    return {t: (pick(t, prev), pick(t, cur), est.get((t, nxt))) for t in members}


def _v(row: Row | None, key: str) -> float | None:
    return None if row is None else row[key]


def _growth_raw(rows: Mapping[str, tuple[Row | None, ...]]) -> dict[str, dict[str, float]]:
    """v2 `load_growth_raw`(`v2_data_loader.py:62-73`)."""
    out = {}
    for t, (prev, cur, nxt) in rows.items():
        out[t] = {
            "op_yoy_cur": _yoy(_v(cur, "op"), _v(prev, "op")),
            "op_yoy_next": _yoy(_v(nxt, "op"), _v(cur, "op")),
            "ni_yoy_cur": _yoy(_v(cur, "ni"), _v(prev, "ni")),
            "ni_yoy_next": _yoy(_v(nxt, "ni"), _v(cur, "ni")),
        }
    return out


def _per_raw(rows: Mapping[str, tuple[Row | None, ...]]) -> dict[str, dict[str, float]]:
    """v2 `load_forward_per`(`v2_data_loader.py:76-85`) — 당해·차년 PER, 결측은 NaN(부호 그대로)."""
    out = {}
    for t, (_, cur, nxt) in rows.items():
        pc, pn = _v(cur, "per"), _v(nxt, "per")
        out[t] = {"per_cur": NAN if pc is None else pc, "per_next": NAN if pn is None else pn}
    return out


def _value_scores(per_raw: Mapping[str, Mapping[str, float]],
                  sub_weights: Mapping[str, float]) -> dict[str, float]:
    """v2 `value.py:8-39` — PER ≤ 0 은 NaN 으로 바꾼 뒤 역방향 백분위 가중합."""
    cleaned: dict[str, dict[str, float]] = {}
    for code, raw in per_raw.items():
        cleaned[code] = {}
        for sub in sub_weights:
            v = raw.get(sub, NAN)
            if not math.isnan(v) and v <= 0:
                v = NAN
            cleaned[code][sub] = v
    return _factor_scores(cleaned, sub_weights, invert=True)


# ── 수급 ─────────────────────────────────────────────────────────────────────
def _flows(inputs: FactorInputs, universe: Mapping[str, float], d: str, days: int, rows_n: int,
           subjects: Sequence[str]) -> dict[str, dict[str, list[float]]]:
    """v2 `load_flow_data`(`v2_data_loader.py:98-119`): [D − days 달력일, D] 날짜 오름차순 최근
    rows_n 행, 주체별 순매수(백만원) 목록. NULL 은 0."""
    start = (date.fromisoformat(d) - timedelta(days=days)).isoformat()
    by_code: dict[str, list[Row]] = {}
    for r in _rows(inputs, "fi_flows"):
        if r["ticker"] in universe and start <= _iso(r["date"]) <= d:
            by_code.setdefault(r["ticker"], []).append(r)
    out = {}
    for code, rows in by_code.items():
        rows = sorted(rows, key=lambda r: _iso(r["date"]))
        out[code] = {s: [r[s] or 0 for r in rows][-rows_n:] for s in subjects}
    return out


def _flow_raw(flows: Mapping[str, Sequence[float]], mcap: float,
              windows: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    """v2 `flow.py:12-24` — 뒤쪽 n 행 순매수 합(백만원) ÷ 시총(억원) × 100. 행이 없으면 합 0."""
    if not mcap or mcap <= 0:
        return {k: NAN for k in windows}
    out = {}
    for key, win in windows.items():
        vals = flows.get(win["subject"], [])
        n = win["rows"]
        recent = vals[-n:] if len(vals) >= n else vals
        out[key] = sum(recent) / mcap * 100
    return out


# ── 엔진 ─────────────────────────────────────────────────────────────────────
def _check(spec: ModelSpec, inputs: FactorInputs) -> None:
    if spec.engine != V2PercentRankEngine.name:
        raise ValueError(f"{spec.spec_id}: engine {spec.engine!r} ≠ v2_percentrank")
    if tuple(spec.buckets) != FACTORS:
        raise ValueError(f"{spec.spec_id}: buckets 는 {FACTORS} 순서여야 한다 — "
                         f"{tuple(spec.buckets)}")
    # v2 유니버스 = 적격 ∧ 시총 > 0 뿐이다. 다른 규칙(시총 하한 등)은 이 엔진이 판정하지 않으므로
    # 거절한다(조용히 무시하지 않는다).
    if spec.universe != UniverseRule():
        raise ValueError(f"{spec.spec_id}: v2_percentrank 는 기본 유니버스 규칙만 지원"
                         f"(min_market_cap 등 없음) — {spec.universe}")
    p: Mapping[str, Any] = spec.params
    errs = []
    for f in FACTORS:
        sub = (p.get(f) or {}).get("sub_weights") or {}
        if not sub or set(sub) - set(SUB_KEYS[f]):
            errs.append(f"params.{f}.sub_weights {sorted(sub)} ⊄ {SUB_KEYS[f]}(또는 비었다)")
    mom, flow = p.get("momentum") or {}, p.get("flow") or {}
    if set(mom.get("windows") or {}) != set(SUB_KEYS["momentum"]):
        errs.append(f"params.momentum.windows 키는 {SUB_KEYS['momentum']}")
    if set(flow.get("windows") or {}) != set(SUB_KEYS["flow"]):
        errs.append(f"params.flow.windows 키는 {SUB_KEYS['flow']}")
    for f, k in (("momentum", "price_days"), ("momentum", "price_rows"),
                 ("flow", "flow_days"), ("flow", "flow_rows")):
        if not isinstance((p.get(f) or {}).get(k), int):
            errs.append(f"params.{f}.{k} 는 정수")
    if errs:
        raise ValueError(f"{spec.spec_id}: 설정 오류 {errs}")
    bad = inputs.check(V2PercentRankEngine.name)
    if bad:
        raise ValueError(f"factor_inputs {inputs.build_id} 계약 불일치: {bad}")


class V2PercentRankEngine:
    """`contracts.Engine` 구현 — v2 `score_history_v2` 21열을 낸다(지표 긴 표는 비움)."""

    name = "v2_percentrank"

    def output_columns(self, spec: ModelSpec) -> tuple[str, ...]:
        return V2_SCORE_COLUMNS

    def run(self, spec: ModelSpec, inputs: FactorInputs) -> EngineResult:
        _check(spec, inputs)
        p: Mapping[str, Any] = spec.params      # 구조는 config/models/v2_percentrank.toml
        mp, fp = p["momentum"], p["flow"]
        d = inputs.date
        caps = _universe(inputs)
        codes = sorted(caps)

        # 모멘텀(`v2_engine.py:55-60`) — 창 안에 가격 행이 있는 종목만 원값을 갖는다
        closes = _closes(inputs, caps, d, mp["price_days"], mp["price_rows"])
        mom_raw = {c: _momentum_raw(closes[c], mp["windows"]) for c in codes if c in closes}
        mom = _factor_scores(mom_raw, mp["sub_weights"])

        # 성장·밸류(`v2_engine.py:62-79`) — 전년·당해·차년 12월기 행이 있는 종목만
        year_rows = _year_rows(inputs, caps, int(d[:4]))
        growth_raw = _growth_raw(year_rows)
        growth = _factor_scores(growth_raw, p["growth"]["sub_weights"])
        per_raw = _per_raw(year_rows)
        value = _value_scores(per_raw, p["value"]["sub_weights"])

        # 수급(`v2_engine.py:68-73`) — 유니버스 전부(행이 없으면 순매수 0)
        subjects = sorted({w["subject"] for w in fp["windows"].values()})
        flows = _flows(inputs, caps, d, fp["flow_days"], fp["flow_rows"], subjects)
        flow_raw = {c: _flow_raw(flows.get(c, {}), caps[c], fp["windows"]) for c in codes}
        flow = _factor_scores(flow_raw, fp["sub_weights"])

        score_map = {"momentum": mom, "growth": growth, "flow": flow, "value": value}
        out: list[dict[str, Any]] = []
        for code in codes:                                   # v2_engine.py:97-120
            total = sum(w * score_map[f].get(code, 0.0) for f, w in spec.buckets.items())
            mr, gr = mom_raw.get(code, {}), growth_raw.get(code, {})
            fr, pr = flow_raw[code], per_raw.get(code, {})
            row: dict[str, Any] = dict.fromkeys(V2_SCORE_COLUMNS)
            row.update(
                stock_code=code, score_date=d,
                momentum_score=mom.get(code, 0.0), growth_score=growth.get(code, 0.0),
                flow_score=flow.get(code, 0.0), value_score=value.get(code, 0.0),
                total_score=total,
                **{k: _out(mr.get(k)) for k in SUB_KEYS["momentum"]},
                **{k: _out(gr.get(k)) for k in SUB_KEYS["growth"]},
                **{k: _out(fr.get(k)) for k in SUB_KEYS["flow"]},
                **{k: _out(pr.get(k)) for k in SUB_KEYS["value"]},
            )
            out.append(row)

        # v2_engine.py:121-123 — 종합 내림차순. 동점은 종목코드 오름차순으로 고정(GAP-7)
        out.sort(key=lambda r: (-r["total_score"], r["stock_code"]))
        for i, row in enumerate(out, start=1):
            row["rank"] = i
        return EngineResult(scores=out, indicators=[])


ENGINE = V2PercentRankEngine()
