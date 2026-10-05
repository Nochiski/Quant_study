"""모델 판 게이트 MG0~MG5 (플랜 `docs/plans/2026-09-24-v3-merge.md` M2 W2-a · T2.5).

`build.py` 가 spec 마다 엔진을 **두 번** 돌린 결과(메모리의 행 목록)로 여기 게이트를 돈다.
**FAIL 이 하나라도 있으면 판 전체(모든 spec)를 올리지 않는다** — MANIFEST·latest 는 마지막 성공 판
그대로(factor_inputs 규약). 결과 타입은 stage/equity/factor_inputs 와 같은 `stage.gates.GateResult`.

  MG0 스키마   — 점수·지표 행의 열 = 계약(`score_columns(spec)`·`INDICATOR_COLUMNS`, 순서까지)이고
                 값 타입이 열 dtype(`score_dtypes`·`INDICATOR_DTYPES`)과 맞다(한 열에 타입이 섞이지
                 않는다). FAIL 이면 MG1·MG2·MG3·MG5 는 `skip(upstream_failed)`
                 (MG4 는 입력 판정이라 돈다).
  MG1 커버리지 — v3·v2: 그 spec 자신의 유니버스 규칙으로 센 종목 중 점수가 나온 비율 ≥ 0.95
                 (v3 = eligible ∧ D 가격 행 ∧ 시총 ≥ min_market_cap ∧ 추정기관수 ≥ min_analysts
                 (NULL 은 통과), v2 = eligible ∧ 시총 > 0).
                 그 밖(v4 계열): 순위가 매겨진 종목 ≥ `min_ranked`(기본 100).
  MG2 결정성   — 같은 FactorInputs 두 번 실행의 점수·지표 직렬화 sha256 이 같다.
  MG3 온전성   — NaN·inf 없음 · 종목 중복 없음 · 순위 = 순위 있는 행의 1…n · v3·v2 는 전 행 순위 ·
                 v3 의 항상 NULL 6열이 NULL · v4 계열은 종합·버킷 점수·지표 백분위 ∈ [0, 100],
                 제외 ⇔ 사유 ⇔ 순위 NULL, 순위 행은 종합이 있다.
  MG4 신선도   — 전체 fi_prices(eligible 로 자르기 전)에서 D 종가가 있는 종목 ≥ 2,000
                 (v3 원본 stale guard `backend/scoring/engine.py:40-51` 과 같은 하한).
  MG5 전판 대비 — 같은 spec 의 직전 성공 판(MANIFEST current_build)과 종합점수 Spearman·
                 상위 30 겹침을 기록만 한다. Spearman < 0.8(또는 셀 수 없음)이면 상태 `warn`
                 — 판은 올린다.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from stage.gates import GateResult, GateStatus

from model.contracts import (
    INDICATOR_COLUMNS,
    EngineResult,
    FactorInputs,
    ModelSpec,
    score_columns,
)

GATE_ORDER = ("MG0", "MG1", "MG2", "MG3", "MG4", "MG5")
COVERAGE_MIN = 0.95          # MG1 v3·v2 — 자기 유니버스 대비 점수 비율 하한
MIN_RANKED = 100             # MG1 v4 계열 — 순위 종목 하한(09-28 서버 첫 판 317)
MIN_PRICES_ON_D = 2000       # MG4 — v3 `V3_MIN_DAILY_PRICES_THRESHOLD`(v2 가드와 같은 값)
SPEARMAN_WARN = 0.8          # MG5 — 이 값 미만이면 warn
TOP_N = 30                   # MG5 — 상위 겹침 크기(주간 후보 수)
WARN = "warn"                # MG5 의 기록 상태(GateStatus 에는 없다 — PASS + metrics.warn)
SCORE_MIN, SCORE_MAX = 0.0, 100.0
# v3 `score_history` 에만 있고 v3 엔진이 채우지 않는 6열(`engines/v3_zscore.py` dict.fromkeys)
V3_ALWAYS_NULL = ("growth_score", "sentiment_score", "volatility_score", "size_score",
                  "foreign_score", "shareholder_score")

# ── 출력 dtype ───────────────────────────────────────────────────────────────
# 골든 `score_history(_v2).parquet` 의 열 타입과 같다(식별자·날짜·플래그 VARCHAR · rank BIGINT ·
# 나머지 DOUBLE). v4 계열 기본 열과 지표 긴 표도 같은 규칙.
_VARCHAR = frozenset({"stock_code", "ticker", "score_date", "spec_id", "exclude_reason",
                      "sector_l1", "sector_l2", "coverage_state", "key", "bucket", "role", "flag"})
_BIGINT = frozenset({"rank", "n_buckets_used"})
_BOOLEAN = frozenset({"excluded"})


def _dtype(col: str) -> str:
    if col in _VARCHAR or col.endswith("_flag"):
        return "VARCHAR"
    if col in _BIGINT:
        return "BIGINT"
    if col in _BOOLEAN:
        return "BOOLEAN"
    return "DOUBLE"


def score_dtypes(spec: ModelSpec) -> dict[str, str]:
    """점수 표 열 → parquet dtype(`score_columns(spec)` 순서)."""
    return {c: _dtype(c) for c in score_columns(spec)}


INDICATOR_DTYPES: dict[str, str] = {c: _dtype(c) for c in INDICATOR_COLUMNS}


def _type_ok(dtype: str, v: object) -> bool:
    if v is None:
        return True
    if dtype == "VARCHAR":
        return isinstance(v, str)
    if dtype == "BOOLEAN":
        return isinstance(v, bool)
    if isinstance(v, bool):
        return False
    if dtype == "BIGINT":
        return isinstance(v, int)
    return isinstance(v, int | float)          # DOUBLE — 정수 값은 그대로 실수로 쓴다


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


# ── spec 별 열 이름 ──────────────────────────────────────────────────────────
def ticker_col(spec: ModelSpec) -> str:
    return "stock_code" if spec.engine in ("v3_zscore", "v2_percentrank") else "ticker"


def composite_col(spec: ModelSpec) -> str:
    return {"v3_zscore": "composite_score", "v2_percentrank": "total_score"}.get(
        spec.engine, "composite")


def counts(spec: ModelSpec, result: EngineResult) -> dict[str, int]:
    """판 manifest 의 spec 요약 수. v3·v2 는 제외 개념이 없다(점수 행 = 순위 행)."""
    n = len(result.scores)
    ranked = sum(1 for r in result.scores if r.get("rank") is not None)
    if "excluded" in score_columns(spec):
        excluded = sum(1 for r in result.scores if r.get("excluded") is True)
    else:
        excluded = n - ranked
    return {"n_scores": n, "n_ranked": ranked, "n_excluded": excluded}


def digest(rows: Sequence[Mapping[str, object]]) -> str:
    """행 목록 직렬화 sha256 — 열 순서·행 순서·부동소수 전체 자릿수(repr)가 다 들어간다."""
    blob = json.dumps(list(rows), ensure_ascii=False, default=str, allow_nan=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def spearman(a: Mapping[str, float], b: Mapping[str, float]) -> float | None:
    """공통 키의 Spearman(동률 평균순위 → Pearson). 공통 < 2 이거나 분산 0 이면 None."""
    keys = sorted(set(a) & set(b))
    if len(keys) < 2:
        return None

    def ranks(d: Mapping[str, float]) -> list[float]:
        order = sorted(keys, key=lambda k: (d[k], k))
        out: dict[str, float] = {}
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and d[order[j + 1]] == d[order[i]]:
                j += 1
            for k in order[i:j + 1]:
                out[k] = (i + j) / 2
            i = j + 1
        return [out[k] for k in keys]

    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    if va == 0 or vb == 0:
        return None
    return cov / math.sqrt(va * vb)


# ── 문맥 ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Previous:
    """같은 spec 의 직전 성공 판(MG5)."""

    build_id: str
    score_date: str | None
    composite: Mapping[str, float]          # 종합이 있는 종목만
    rank: Mapping[str, int]                 # 순위가 있는 종목만


@dataclass(frozen=True)
class GateContext:
    spec: ModelSpec
    date: str                               # D (YYYY-MM-DD)
    inputs: FactorInputs                    # 엔진이 읽은 판(eligible 종목만 실린다)
    result: EngineResult
    rerun: EngineResult                     # MG2 — 같은 입력 두 번째 실행
    n_prices_on_d: int                      # MG4 — 전체 fi_prices 에서 D 종가가 있는 종목 수
    min_prices_on_d: int = MIN_PRICES_ON_D
    min_ranked: int = MIN_RANKED
    previous: Previous | None = None


def _result(name: str, violations: Mapping[str, int], metrics: Mapping[str, object],
            ok_detail: str) -> GateResult:
    bad = {k: v for k, v in violations.items() if v}
    status = GateStatus.FAIL if bad else GateStatus.PASS
    detail = ok_detail if not bad else "; ".join(f"{k}={v}" for k, v in bad.items())
    return GateResult(name, status, detail, {**violations, **metrics})


# ── MG0 ──────────────────────────────────────────────────────────────────────
def mg0_schema(ctx: GateContext) -> GateResult:
    bad_cols: dict[str, int] = {}
    types: Counter[str] = Counter()
    sample: list[str] = []
    for part, rows, dtypes in (("scores", ctx.result.scores, score_dtypes(ctx.spec)),
                               ("indicators", ctx.result.indicators, INDICATOR_DTYPES)):
        want = tuple(dtypes)
        bad_cols[f"{part}.columns_mismatch"] = 0
        for r in rows:
            if tuple(r) != want:
                bad_cols[f"{part}.columns_mismatch"] += 1
                if len(sample) < 3:
                    extra = sorted(set(r) - set(want))
                    miss = sorted(set(want) - set(r))
                    sample.append(f"{part}: 계약 밖 {extra} · 없음 {miss}")
                continue
            for c, dt in dtypes.items():
                if not _type_ok(dt, r[c]):
                    types[f"{part}.{c}"] += 1
                    if len(sample) < 3:
                        sample.append(f"{part}.{c}={r[c]!r} ∉ {dt}")
    viol = {**bad_cols, "n_type_violations": sum(types.values())}
    res = _result("MG0", viol, {"type_violations": dict(types)},
                  "점수·지표 열이 계약과 같고 열마다 타입이 하나다")
    if res.status is GateStatus.FAIL and sample:
        res = GateResult("MG0", res.status, f"{res.detail} | {' | '.join(sample)}", res.metrics)
    return res


# ── MG1 ──────────────────────────────────────────────────────────────────────
def _spec_universe(ctx: GateContext) -> set[str] | None:
    """v3·v2 가 자기 규칙으로 점수를 내야 하는 종목. 규칙이 엔진 안에만 있는 v4 계열은 None."""
    uni = [r for r in ctx.inputs.rows("fi_universe") if r["eligible"]]
    if ctx.spec.engine == "v2_percentrank":
        return {str(r["ticker"]) for r in uni if (_num(r["market_cap"]) or 0) > 0}
    if ctx.spec.engine == "v3_zscore":
        on_d = {str(r["ticker"]) for r in ctx.inputs.rows("fi_prices")
                if str(r["date"])[:10] == ctx.date}
        min_cap = ctx.spec.universe.min_market_cap or 0
        min_an = ctx.spec.universe.min_analysts
        out = set()
        for r in uni:
            cap = _num(r["market_cap"])
            if str(r["ticker"]) not in on_d:
                continue
            if min_cap > 0 and (cap is None or cap < min_cap):
                continue
            n_an = _num(r.get("n_analysts"))
            if min_an is not None and n_an is not None and n_an < min_an:
                continue
            out.add(str(r["ticker"]))
        return out
    return None


def mg1_coverage(ctx: GateContext) -> GateResult:
    tcol = ticker_col(ctx.spec)
    scored = {str(r[tcol]) for r in ctx.result.scores}
    n_scores = len(ctx.result.scores)
    universe = _spec_universe(ctx)
    if universe is None:
        c = counts(ctx.spec, ctx.result)
        reasons = Counter(str(r["exclude_reason"]) for r in ctx.result.scores
                          if r.get("excluded") is True)
        n_eligible = sum(1 for r in ctx.inputs.rows("fi_universe") if r["eligible"])
        return _result("MG1", {"ranked_below_min": int(c["n_ranked"] < ctx.min_ranked)},
                       {"n_scores": n_scores, "n_ranked": c["n_ranked"],
                        "min_ranked": ctx.min_ranked, "n_fi_eligible": n_eligible,
                        "exclude_reasons": dict(sorted(reasons.items()))},
                       f"순위 {c['n_ranked']} ≥ {ctx.min_ranked}")
    n_uni = len(universe)
    covered = len(scored & universe)
    coverage = covered / n_uni if n_uni else 0.0
    return _result("MG1", {"coverage_below_min": int(coverage < COVERAGE_MIN)},
                   {"n_scores": n_scores, "n_eligible": n_uni, "n_covered": covered,
                    "n_outside_universe": len(scored - universe),
                    "coverage": coverage, "coverage_min": COVERAGE_MIN},
                   f"점수 {covered}/{n_uni} = {coverage:.4f} ≥ {COVERAGE_MIN}")


# ── MG2 ──────────────────────────────────────────────────────────────────────
def mg2_determinism(ctx: GateContext) -> GateResult:
    h = {"scores_sha256": digest(ctx.result.scores),
         "rerun_scores_sha256": digest(ctx.rerun.scores),
         "indicators_sha256": digest(ctx.result.indicators),
         "rerun_indicators_sha256": digest(ctx.rerun.indicators)}
    viol = {"scores_differ": int(h["scores_sha256"] != h["rerun_scores_sha256"]),
            "indicators_differ": int(h["indicators_sha256"] != h["rerun_indicators_sha256"])}
    return _result("MG2", viol, h, "두 번 실행의 점수·지표 해시가 같다")


# ── MG3 ──────────────────────────────────────────────────────────────────────
def _nonfinite(rows: Sequence[Mapping[str, object]]) -> int:
    return sum(1 for r in rows for v in r.values()
               if isinstance(v, float) and not math.isfinite(v))


def _out_of_range(v: object) -> bool:
    return isinstance(v, int | float) and not SCORE_MIN <= v <= SCORE_MAX


def mg3_sanity(ctx: GateContext) -> GateResult:
    spec, scores = ctx.spec, ctx.result.scores
    tcol = ticker_col(spec)
    ranks = sorted(int(str(r["rank"])) for r in scores if r["rank"] is not None)
    tickers = [str(r[tcol]) for r in scores]
    viol: dict[str, int] = {
        "n_nonfinite": _nonfinite(scores) + _nonfinite(ctx.result.indicators),
        "duplicate_ticker": len(tickers) - len(set(tickers)),
        "rank_not_1_to_n": int(ranks != list(range(1, len(ranks) + 1))),
    }
    if spec.engine in ("v3_zscore", "v2_percentrank"):
        comp = composite_col(spec)
        viol["unranked_rows"] = sum(1 for r in scores if r["rank"] is None)
        viol["composite_null"] = sum(1 for r in scores if r[comp] is None)
        if spec.engine == "v3_zscore":
            viol["v3_always_null_filled"] = sum(1 for r in scores for c in V3_ALWAYS_NULL
                                                if r[c] is not None)
    else:
        cols = ("composite", *(f"{b}_score" for b in spec.buckets))
        viol["score_out_of_range"] = (
            sum(1 for r in scores for c in cols if _out_of_range(r[c]))
            + sum(1 for r in ctx.result.indicators if _out_of_range(r["pct"])))
        viol["excluded_without_reason"] = sum(
            1 for r in scores if bool(r["excluded"]) != (r["exclude_reason"] is not None))
        viol["excluded_with_rank"] = sum(
            1 for r in scores if bool(r["excluded"]) == (r["rank"] is not None))
        viol["ranked_without_composite"] = sum(
            1 for r in scores if r["rank"] is not None and r["composite"] is None)
        viol["spec_id_mismatch"] = sum(1 for r in scores if r["spec_id"] != spec.spec_id)
    return _result("MG3", viol, {"n_ranked": len(ranks)}, "NaN·inf 없음 · 순위 1…n · 열 규약")


# ── MG4 ──────────────────────────────────────────────────────────────────────
def mg4_freshness(ctx: GateContext) -> GateResult:
    return _result("MG4", {"prices_on_d_below_min": int(ctx.n_prices_on_d < ctx.min_prices_on_d)},
                   {"n_prices_on_d": ctx.n_prices_on_d, "min_prices_on_d": ctx.min_prices_on_d},
                   f"D 종가 종목 {ctx.n_prices_on_d} ≥ {ctx.min_prices_on_d}")


# ── MG5 ──────────────────────────────────────────────────────────────────────
def mg5_day_over_day(ctx: GateContext) -> GateResult:
    prev = ctx.previous
    if prev is None:
        return GateResult("MG5", GateStatus.SKIP, "no_previous — 같은 spec 의 성공 판이 없다", {})
    tcol, comp = ticker_col(ctx.spec), composite_col(ctx.spec)
    cur_comp = {str(r[tcol]): v for r in ctx.result.scores
                if (v := _num(r[comp])) is not None}
    cur_top = {str(r[tcol]) for r in ctx.result.scores
               if r["rank"] is not None and int(str(r["rank"])) <= TOP_N}
    prev_top = {t for t, k in prev.rank.items() if k <= TOP_N}
    rho = spearman(prev.composite, cur_comp)
    warn = rho is None or rho < SPEARMAN_WARN
    metrics: dict[str, object] = {
        "prev_build_id": prev.build_id, "prev_score_date": prev.score_date,
        "n_common": len(set(prev.composite) & set(cur_comp)),
        "spearman": rho, "spearman_warn": SPEARMAN_WARN,
        "top30_overlap": len(cur_top & prev_top), "top30_n": len(cur_top),
        "top30_prev_n": len(prev_top), "warn": warn}
    rho_s = "없음" if rho is None else f"{rho:.4f}"
    detail = (f"{WARN}: " if warn else "") + (
        f"Spearman {rho_s} · 상위 {TOP_N} 겹침 {metrics['top30_overlap']} "
        f"(전판 {prev.build_id})")
    return GateResult("MG5", GateStatus.PASS, detail, metrics)


# ── 실행 ─────────────────────────────────────────────────────────────────────
_AFTER_SCHEMA: tuple[tuple[str, Callable[[GateContext], GateResult]], ...] = (
    ("MG1", mg1_coverage), ("MG2", mg2_determinism), ("MG3", mg3_sanity),
    ("MG4", mg4_freshness), ("MG5", mg5_day_over_day))


def run_all(ctx: GateContext) -> list[GateResult]:
    """GATE_ORDER 순서. MG0 FAIL 이면 MG4 만 돌고 나머지는 `skip(upstream_failed)`."""
    out = [mg0_schema(ctx)]
    schema_ok = out[0].status is not GateStatus.FAIL
    for name, gate in _AFTER_SCHEMA:
        if schema_ok or name == "MG4":
            out.append(gate(ctx))
        else:
            out.append(GateResult(name, GateStatus.SKIP, "upstream_failed — MG0", {}))
    return out


def status_of(g: GateResult) -> str:
    """기록 상태 — pass·fail·skip 에 MG5 의 warn(PASS + metrics.warn)을 더한다."""
    if g.status is GateStatus.PASS and g.metrics.get("warn") is True:
        return WARN
    return g.status.value


def as_dicts(results: Sequence[GateResult]) -> dict[str, dict[str, object]]:
    """판 manifest 용 {게이트: {status, detail, metrics}}."""
    return {g.name: {"status": status_of(g), "detail": g.detail, "metrics": dict(g.metrics)}
            for g in results}


def failed(results: Sequence[GateResult]) -> list[GateResult]:
    return [g for g in results if g.status is GateStatus.FAIL]
