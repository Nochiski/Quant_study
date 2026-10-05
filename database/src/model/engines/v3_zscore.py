"""v3 5팩터 z-score 엔진 이식 (플랜 `docs/plans/2026-09-24-v3-merge.md` M2 W1-c · T2.3).

v3 `backend/scoring/engine.py`·`factors/{momentum,revision,flow,quality,valuation,ranking}.py` 를
**숫자 하나 바꾸지 않고** 옮겼다(G-M3 ①: v3 원본과 |Δ| ≤ 1e-9 · rank 동일). 원본 파일:줄은 함수마다
적는다. 다른 점은 입력뿐이다 — v3 는 compat sqlite 를 질의하고, 여기서는 `fi_*` 표(`FactorInputs`)만
읽는다. 표준 라이브러리만 쓰고, 순회는 전부 정렬된 순서라 입력 행 순서와 무관하게 같은 결과를 낸다.

흐름(v3 `engine.py:60-158`):
  유니버스(D 가격 있음 · 적격 · 시총 ≥ 하한) → 팩터 5개 원점수(하위 지표 z → 비례 재정규화 합)
  → 팩터마다 2단계 z(`_normalize_factor_scores`) → 있는 팩터만 비례 재정규화한 종합점수
  → `(−종합, 종목코드)` 정렬 · rank 1…N.

의도적으로 재현한 v3 동작(고치면 v3_zscore@1.0 이 아니다 — 수정은 @1.1 에서):
  - 모멘텀은 지표가 하나도 없는 종목도 원점수 0.0 으로 2단계 정규화에 넣는다(`momentum.py:22-24`).
    다른 팩터는 그런 종목을 뺀다.
  - 리비전: 비교값이 없거나 0 이면 변화율 0.0(`revision.py:78-79`) — 결측이 "변화 없음"으로 섞인다.
  - 수급 창은 달력일·세션이 아니라 **D 이하 최근 행 20개**(`flow_repo.py:61-67`)에서 앞 5·20행.
    z 는 순매수/(시총×100,000) 로, 저장 원값은 순매수/시총 으로 계산한다(`flow.py:64-68`).
  - 퀄리티는 연간 확정 재무 행이 하나도 없으면 가격만으로 되는 std_20d 도 만들지 않는다
    (`quality.py:78-79`). 하위 지표가 두 종목 미만이면 그 지표 z 는 0.0(`quality.py:44-47`).
  - `adj_ok`(DQ-1 미해결 구간)은 읽지 않는다 — v3 에 그 개념이 없다.
"""
from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from model.contracts import (
    V3_SCORE_COLUMNS,
    EngineResult,
    FactorInputs,
    ModelSpec,
    UniverseRule,
)
from model.engines._common import weighted_available, z_score_winsorized

Row = Mapping[str, Any]     # fi_* 행 — 값 타입은 계약(contracts.FI_TABLES)이 정한다

FACTORS = ("momentum", "revision", "flow", "quality", "valuation")   # engine.py:222-229
HISTORY_DAYS = 550                   # engine.py:237 — D 포함 550 달력일 전부터
FLOW_RECORDS = 20                    # engine.py:181 — get_flows(code, 20, D)
LOOKBACKS = {"r1m": 20, "r3m": 60, "r6m": 120, "r9m": 180, "r12m": 240}   # momentum.py:8(세션)
FLOW_WINDOWS = {                     # flow.py:6-16 — 지표 → (주체 열, 앞에서 몇 행)
    "inst_5d": ("institution_total", 5), "inst_20d": ("institution_total", 20),
    "for_5d": ("foreign_investor", 5), "for_20d": ("foreign_investor", 20),
    "pe_5d": ("private_equity", 5), "pe_20d": ("private_equity", 20),
}
REVISION_PERIODS = ("1w", "1m", "3m")                # revision.py:11-15(_PERIOD_FIELDS 순서)
QUALITY_REVERSE = frozenset({"debt_ratio", "std_20d"})           # quality.py:21
VALUATION_REVERSE = frozenset({"per", "pbr", "ev_ebitda"})       # valuation.py:17
STD_MIN_PRICES, STD_WINDOW, STD_MIN_RETURNS = 10, 21, 5          # quality.py:118-130


def _rows(inputs: FactorInputs, name: str) -> Sequence[Row]:
    return inputs.rows(name)


def _iso(v: object) -> str:
    """DATE(date·datetime·'YYYY-MM-DD…') → 'YYYY-MM-DD'. v3 는 날짜를 TEXT 로 비교한다."""
    return str(v)[:10]


# ── 유니버스 · 가격 이력 ─────────────────────────────────────────────────────
def _universe(spec: ModelSpec, inputs: FactorInputs, d: str) -> tuple[list[str], dict[str, float]]:
    """v3 `engine.py:66-78` + `_load_market_caps`(245-255).

    D 에 가격 행이 있고 `fi_universe.eligible` 인 종목 중 시총 ≥ `universe.min_market_cap`.
    `universe.min_analysts` 가 있으면 추정기관수(n_analysts)가 그보다 작은 종목도 뺀다 —
    NULL(모름)은 빼지 않는다(원본 v3 에는 없는 scope 규칙, 2026-10-05).
    시총 맵은 > 0 인 값만 담는다(수급 분모). 반환 종목은 정렬 순서.
    """
    on_d = {r["ticker"] for r in _rows(inputs, "fi_prices") if _iso(r["date"]) == d}
    uni = {r["ticker"]: r for r in _rows(inputs, "fi_universe")}
    min_cap = spec.universe.min_market_cap or 0
    min_an = spec.universe.min_analysts
    codes: list[str] = []
    caps: dict[str, float] = {}
    for t in sorted(on_d):
        u = uni.get(t)
        if u is None or not u["eligible"]:
            continue
        cap = u["market_cap"]
        if cap is not None and cap > 0:
            caps[t] = cap
        if min_cap > 0 and caps.get(t, 0) < min_cap:
            continue
        n_an = u.get("n_analysts")
        if min_an is not None and n_an is not None and n_an < min_an:
            continue
        codes.append(t)
    return codes, caps


def _price_histories(inputs: FactorInputs, codes: Sequence[str], d: str) -> dict[str, list[float]]:
    """v3 `engine.py:232-242` + `price_repo.py:51-60`: [D−550일, D] 날짜 오름차순.

    값 = `adj_close if not None else close`(`momentum.py:60-65`) — fi_adj_prices 에 없거나 NULL
    이면 fi_prices.close. 행 집합은 fi_prices(compat daily_prices 와 같은 자리).
    """
    start = (date.fromisoformat(d) - timedelta(days=HISTORY_DAYS)).isoformat()
    want = set(codes)
    adj = {(r["ticker"], _iso(r["date"])): r["adj_close"]
           for r in _rows(inputs, "fi_adj_prices") if r["ticker"] in want}
    rows: dict[str, list[tuple[str, float]]] = {c: [] for c in codes}
    for r in _rows(inputs, "fi_prices"):
        t = r["ticker"]
        if t not in want:
            continue
        day = _iso(r["date"])
        if start <= day <= d:
            a = adj.get((t, day))
            rows[t].append((day, a if a is not None else r["close"]))
    return {c: [px for _, px in sorted(v, key=lambda x: x[0])] for c, v in rows.items()}


# ── 모멘텀 ───────────────────────────────────────────────────────────────────
def _period_return(history: Sequence[float], lookback: int) -> float | None:
    """v3 `momentum.py:55-68` — lookback 세션 전 대비 수익률. 행이 lookback 이하이면 None."""
    if len(history) <= lookback:
        return None
    current, previous = history[-1], history[-lookback - 1]
    if previous == 0:
        return None
    return (current - previous) / previous


def _momentum(histories: Mapping[str, Sequence[float]], sub_weights: Mapping[str, float]):
    """v3 `momentum.py:11-52`."""
    raw: dict[str, dict[str, float]] = {c: {} for c in histories}
    scored: dict[str, dict[str, float]] = {c: {} for c in histories}
    for name, lookback in LOOKBACKS.items():
        codes, vals = [], []
        for code, history in histories.items():
            r = _period_return(history, lookback)
            if r is None:
                continue
            raw[code][name] = r
            codes.append(code)
            vals.append(r)
        for code, z in zip(codes, z_score_winsorized(vals), strict=True):
            scored[code][name] = z
    scores = {}
    for code in histories:
        s = weighted_available(scored[code], sub_weights)
        scores[code] = 0.0 if s is None else s          # momentum.py:22-28 — 지표 없으면 0.0
    return scores, raw


# ── 리비전 ───────────────────────────────────────────────────────────────────
def _change(current: float | None, previous: float | None) -> tuple[float, str | None]:
    """v3 `revision.py:75-87` — 변화율과 흑전·적전·적확·적축 표식."""
    if current is None or previous in (None, 0):
        return 0.0, None
    flag: str | None = None
    if previous < 0 and current > 0:
        flag = "흑전"
    elif previous > 0 and current < 0:
        flag = "적전"
    elif previous < 0 and current < 0:
        flag = "적확" if abs(current) > abs(previous) else "적축"
    return (current - previous) / abs(previous), flag


def _consensus_pair(rows: Sequence[Row], asof_ym: str) -> tuple[Row, dict[str, Row]] | None:
    """종목 하나의 fi_consensus 행 → (현재 행, {horizon: 과거 행}). 없으면 None.

    결산기 = `asof_ym`(D 의 'YYYY/MM') 이상 중 최소 — compat 가 v3 표를 만들 때 고르는 규칙
    (`compat/mappings.py:180-212`, "아직 끝나지 않은 가장 가까운 결산기"). v3 는 현재 행
    (revision_daily)과 비교 행(revision_compare)이 둘 다 있어야 계산한다(`revision.py:30-33`).
    비교 행 = 그 결산기의 1w·1m·3m 중 하나라도.
    """
    periods = [r["target_period"] for r in rows
               if r["target_period"] is not None and r["target_period"] >= asof_ym]
    if not periods:
        return None
    tp = min(periods)
    by_h = {r["horizon"]: r for r in rows if r["target_period"] == tp}
    current = by_h.get("cur")
    compare = {h: by_h[h] for h in REVISION_PERIODS if h in by_h}
    if current is None or not compare:
        return None
    return current, compare


def _revision(inputs: FactorInputs, codes: Sequence[str], d: str,
              metrics: Mapping[str, float], periods: Mapping[str, float]):
    """v3 `revision.py:18-72`."""
    op_w, ni_w = metrics["operating_profit"], metrics["net_income"]
    asof_ym = f"{d[:4]}/{d[5:7]}"
    want = set(codes)
    by_code: dict[str, list[Row]] = {}
    for r in _rows(inputs, "fi_consensus"):
        if r["ticker"] in want:
            by_code.setdefault(r["ticker"], []).append(r)

    period_raws: dict[str, dict[str, float]] = {p: {} for p in periods}
    raw: dict[str, dict[str, object]] = {}
    for code in codes:
        pair = _consensus_pair(by_code.get(code, ()), asof_ym)
        if pair is None:
            continue
        current, compare = pair
        code_raw: dict[str, object] = {}
        for p in REVISION_PERIODS:
            if p not in periods:
                continue
            prev = compare.get(p, {})
            op_change, op_flag = _change(current["op"], prev.get("op"))
            ni_change, ni_flag = _change(current["ni"], prev.get("ni"))
            code_raw[f"op_change_{p}"] = op_change
            code_raw[f"ni_change_{p}"] = ni_change
            if op_flag:
                code_raw[f"op_{p}_flag"] = op_flag
            if ni_flag:
                code_raw[f"ni_{p}_flag"] = ni_flag
            period_raws[p][code] = op_change * op_w + ni_change * ni_w
        if code_raw:
            raw[code] = code_raw

    scored: dict[str, dict[str, float]] = {}
    for p, vals in period_raws.items():
        if not vals:
            continue
        cs = list(vals)
        for code, z in zip(cs, z_score_winsorized([vals[c] for c in cs]), strict=True):
            scored.setdefault(code, {})[p] = z
    scores = {}
    for code, subs in scored.items():
        s = weighted_available(subs, periods)
        if s is not None:
            scores[code] = s
    return scores, raw


# ── 수급 ─────────────────────────────────────────────────────────────────────
def _flow(inputs: FactorInputs, codes: Sequence[str], caps: Mapping[str, float], d: str,
          sub_weights: Mapping[str, float]):
    """v3 `flow.py:19-73` + `flow_repo.py:57-79`(D 이하 최근 20행, 날짜 내림차순)."""
    want = set(codes)
    by_code: dict[str, list[Row]] = {}
    for r in _rows(inputs, "fi_flows"):
        if r["ticker"] in want and _iso(r["date"]) <= d:
            by_code.setdefault(r["ticker"], []).append(r)
    recent = {c: sorted(v, key=lambda r: _iso(r["date"]), reverse=True)[:FLOW_RECORDS]
              for c, v in by_code.items()}

    valid = [c for c in codes if c in caps and caps[c] > 0]          # flow.py:26
    raw: dict[str, dict[str, float]] = {c: {} for c in valid}
    scored: dict[str, dict[str, float]] = {c: {} for c in valid}
    for name, (field, window) in FLOW_WINDOWS.items():
        cs, vals = [], []
        for code in valid:
            flows = recent.get(code)
            if not flows:
                continue
            net = sum(r[field] or 0 for r in flows[:window])
            # 순매수(백만원) / 시총(억원)×100,000 — z 는 이 비율로, 저장 원값은 순매수/시총(%)
            raw[code][name] = net / caps[code]
            cs.append(code)
            vals.append(net / (caps[code] * 100_000))
        for code, z in zip(cs, z_score_winsorized(vals), strict=True):
            scored[code][name] = z
    scores = {}
    for code in valid:
        s = weighted_available(scored[code], sub_weights)
        if s is not None:
            scores[code] = s
    return scores, raw


# ── 퀄리티 · 밸류에이션 ──────────────────────────────────────────────────────
def _annual_rows(inputs: FactorInputs, codes: Sequence[str]) -> dict[str, list[Row]]:
    """v3 `quality.py:10-19`: `period_type='annual' AND data_type IS NULL ORDER BY period DESC`.

    fi_fin_summary 는 확정치만 싣는 표라(추정은 fi_consensus) `data_type IS NULL` 은 표 규약이
    대신한다. 종목·기(period)가 grain 이라 기 내림차순이 곧 v3 순서다.
    """
    want = set(codes)
    by_code: dict[str, list[Row]] = {}
    for r in _rows(inputs, "fi_fin_summary"):
        if r["ticker"] in want and r["period_type"] == "annual":
            by_code.setdefault(r["ticker"], []).append(r)
    return {c: sorted(v, key=lambda r: r["period"], reverse=True)
            for c, v in by_code.items()}


def _return_std(prices: Sequence[float]) -> float | None:
    """v3 `quality.py:118-131` — 최근 21행(20 수익률)의 일간 수익률 표본 표준편차."""
    if len(prices) < STD_MIN_PRICES:
        return None
    recent = prices[-STD_WINDOW:] if len(prices) >= STD_WINDOW else prices
    returns = []
    for i in range(1, len(recent)):
        prev_price, cur_price = recent[i - 1], recent[i]
        if prev_price <= 0:
            continue
        returns.append((cur_price - prev_price) / prev_price)
    if len(returns) < STD_MIN_RETURNS:
        return None
    return statistics.stdev(returns)


def _quality_raw(annual: Mapping[str, Sequence[Row]], codes: Sequence[str],
                 histories: Mapping[str, Sequence[float]]) -> dict[str, dict[str, float]]:
    """v3 `quality.py:66-115` — 최신 연간 2기에서 gpa·roa·fcf_assets·debt_ratio·gpa_change,
    가격 이력에서 std_20d."""
    raw: dict[str, dict[str, float]] = {}
    for code in codes:
        rows = annual.get(code, [])[:2]
        if not rows:
            continue
        m: dict[str, float] = {}
        latest = rows[0]
        gp, ta = latest["gross_profit"], latest["total_assets"]
        if gp is not None and ta is not None and ta > 0:
            m["gpa"] = gp / ta
        if latest["roa"] is not None:
            m["roa"] = latest["roa"]
        fcf = latest["fcf"]
        if fcf is not None and ta is not None and ta > 0:
            m["fcf_assets"] = fcf / ta
        if latest["debt_ratio"] is not None:
            m["debt_ratio"] = latest["debt_ratio"]
        if len(rows) >= 2 and "gpa" in m:
            prev_gp, prev_ta = rows[1]["gross_profit"], rows[1]["total_assets"]
            if prev_gp is not None and prev_ta is not None and prev_ta > 0:
                prev_gpa = prev_gp / prev_ta
                if prev_gpa != 0:
                    m["gpa_change"] = (m["gpa"] - prev_gpa) / abs(prev_gpa)
        std = _return_std(histories.get(code, []))
        if std is not None:
            m["std_20d"] = std
        if m:
            raw[code] = m
    return raw


def _valuation_raw(annual: Mapping[str, Sequence[Row]],
                   codes: Sequence[str]) -> dict[str, dict[str, float]]:
    """v3 `valuation.py:61-88` — 최신 연간 1기. per·pbr·ev_ebitda 는 양수만, 배당수익률은 있으면."""
    raw: dict[str, dict[str, float]] = {}
    for code in codes:
        rows = annual.get(code)
        if not rows:
            continue
        row, m = rows[0], {}
        for k in ("per", "pbr", "ev_ebitda"):
            v = row[k]
            if v is not None and v > 0:
                m[k] = v
        if row["dividend_yield"] is not None:
            m["dividend_yield"] = row["dividend_yield"]
        if m:
            raw[code] = m
    return raw


def _score_subs(raw: Mapping[str, Mapping[str, float]], sub_weights: Mapping[str, float],
                reverse: frozenset[str] | set[str]) -> dict[str, float]:
    """v3 `quality.py:34-63`·`valuation.py:29-58` — 하위 지표마다 z(두 종목 미만이면 0.0,
    `reverse` 는 부호 반전) → 비례 재정규화 합."""
    scored: dict[str, dict[str, float]] = {c: {} for c in raw}
    for sub in sub_weights:
        codes = [c for c in raw if sub in raw[c]]
        vals = [raw[c][sub] for c in codes]
        if len(vals) < 2:
            for c in codes:
                scored[c][sub] = 0.0
            continue
        for c, z in zip(codes, z_score_winsorized(vals), strict=True):
            scored[c][sub] = -z if sub in reverse else z
    out = {}
    for c, subs in scored.items():
        s = weighted_available(subs, sub_weights)
        if s is not None:
            out[c] = s
    return out


# ── 엔진 ─────────────────────────────────────────────────────────────────────
def _check(spec: ModelSpec, inputs: FactorInputs) -> None:
    if spec.engine != V3ZScoreEngine.name:
        raise ValueError(f"{spec.spec_id}: engine {spec.engine!r} ≠ v3_zscore")
    if tuple(spec.buckets) != FACTORS:
        raise ValueError(f"{spec.spec_id}: buckets 는 {FACTORS} 순서여야 한다 — "
                         f"{tuple(spec.buckets)}")
    # 시총 하한·추정기관수 하한 밖의 유니버스 조건은 fi_universe.eligible(기본 규칙 판정)을 그대로
    # 믿는다. 기본과 다른 규칙이면 이 엔진은 재판정하지 않으므로 거절한다(조용히 무시하지 않는다).
    if replace(spec.universe, min_market_cap=None, min_analysts=None) != UniverseRule():
        raise ValueError(f"{spec.spec_id}: v3_zscore 는 기본 유니버스 규칙"
                         "(+min_market_cap·min_analysts)만 지원")
    errs = inputs.check(V3ZScoreEngine.name)
    if errs:
        raise ValueError(f"factor_inputs {inputs.build_id} 계약 불일치: {errs}")


class V3ZScoreEngine:
    """`contracts.Engine` 구현 — v3 `score_history` 48열을 낸다(지표 긴 표는 비움)."""

    name = "v3_zscore"

    def output_columns(self, spec: ModelSpec) -> tuple[str, ...]:
        return V3_SCORE_COLUMNS

    def run(self, spec: ModelSpec, inputs: FactorInputs) -> EngineResult:
        _check(spec, inputs)
        p: Mapping[str, Any] = spec.params      # 구조는 config/models/v3_zscore.toml
        d = inputs.date
        codes, caps = _universe(spec, inputs, d)
        histories = _price_histories(inputs, codes, d)
        annual = _annual_rows(inputs, codes)

        mom, mom_raw = _momentum(histories, p["momentum"]["sub_weights"])
        rev, rev_raw = _revision(inputs, codes, d, p["revision"]["metrics"],
                                 p["revision"]["periods"])
        flow, flow_raw = _flow(inputs, codes, caps, d, p["flow"]["sub_weights"])
        qual_raw = _quality_raw(annual, codes, histories)
        qual = _score_subs(qual_raw, p["quality"]["sub_weights"], QUALITY_REVERSE)
        val_raw = _valuation_raw(annual, codes)
        val = _score_subs(val_raw, p["valuation"]["sub_weights"], VALUATION_REVERSE)

        # 2단계 z(engine.py:209-219) — 팩터마다 원점수를 다시 winsorized z 로
        factor_scores = {}
        for name, scores in zip(FACTORS, (mom, rev, flow, qual, val), strict=True):
            cs = sorted(scores)
            factor_scores[name] = dict(zip(cs, z_score_winsorized([scores[c] for c in cs]),
                                           strict=True))

        out: list[dict[str, Any]] = []
        for code in codes:
            avail = {f: factor_scores[f][code] for f in spec.buckets if code in factor_scores[f]}
            composite = weighted_available(avail, spec.buckets)      # engine.py:90-101
            mr, rr = mom_raw.get(code, {}), rev_raw.get(code, {})
            fr, qr, vr = flow_raw.get(code, {}), qual_raw.get(code, {}), val_raw.get(code, {})
            row: dict[str, Any] = dict.fromkeys(V3_SCORE_COLUMNS)   # growth…shareholder 는 NULL
            row.update(
                stock_code=code, score_date=d,
                momentum_score=avail.get("momentum"), revision_score=avail.get("revision"),
                flow_score=avail.get("flow"), valuation_score=avail.get("valuation"),
                composite_score=0.0 if composite is None else composite,
                quality_score=avail.get("quality"),
                **{k: mr.get(k) for k in LOOKBACKS},
                **{f"{m}_change_{q}": rr.get(f"{m}_change_{q}")
                   for q in REVISION_PERIODS for m in ("op", "ni")},
                **{f"flow_{k}": fr.get(k) for k in FLOW_WINDOWS},
                **{f"qual_{k}": qr.get(k) for k in ("gpa", "roa", "fcf_assets", "debt_ratio",
                                                    "gpa_change", "std_20d")},
                **{f"val_{k}": vr.get(k) for k in ("per", "pbr", "ev_ebitda", "dividend_yield")},
                **{f"{m}_{q}_flag": rr.get(f"{m}_{q}_flag")
                   for q in REVISION_PERIODS for m in ("op", "ni")},
            )
            out.append(row)

        out.sort(key=lambda r: (-r["composite_score"], r["stock_code"]))
        for i, row in enumerate(out, start=1):                          # engine.py:155-157
            row["rank"] = i
        return EngineResult(scores=out, indicators=[])


ENGINE = V3ZScoreEngine()
