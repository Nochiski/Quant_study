"""판 하나(하루)를 엑셀이 쓰는 모양으로 모은다 — 주 모델 점수 · 버킷 백분위 · 후보 · 입력 원자료.

주 모델 = run 의 `primary_spec`(기본 scope@1.0). 버킷 백분위는 엔진 버킷 점수를 한 번 더
순위 매긴 **표시값**이다: 유니버스 백분위 = 그 버킷 점수가 있는 전 종목 안, 업종 백분위 = WICS
대분류 안(표본 < min_sector_size 면 유니버스로 되돌린다 — 엔진 규칙과 같다). 일간 점수 시트는 z 엔진
(scope·v3) 이면 백분위 대신 버킷 z 그대로와 대분류 안 다시 매긴 z(`zsec`)를 싣는다(10-08 '둘 다 z').
주간 엑셀은 아직 백분위.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path

from model import registry
from model.contracts import ModelSpec, OutputRule
from model.engines.v3_zscore import V3ZScoreEngine

from .common import cap_candidates, group_pct, group_z, pct_rank_avg
from .reader import DeliverError, ModelRun, read_fi, read_indicators, read_scores

COMPOSITE_COLS = ("composite", "composite_score", "total_score")
MIN_SECTOR_DEFAULT = 5


@functools.lru_cache(maxsize=16)
def spec_of(spec_id: str, config_dir: str | None = None) -> ModelSpec | None:
    """레지스트리 항목. 등록에서 빠진 옛 spec 이면 None(열은 데이터에서 만든다)."""
    try:
        return registry.get(spec_id, None if config_dir is None else Path(config_dir))
    except (KeyError, ValueError):
        return None


def ticker_of(row: dict[str, object]) -> str:
    return str(row.get("ticker") if row.get("ticker") is not None else row.get("stock_code"))


def rank_of(row: dict[str, object] | None) -> int | None:
    if row is None:
        return None
    r = row.get("rank")
    if r is None:
        return None
    if isinstance(r, float) and r != r:          # NaN
        return None
    return int(r)  # type: ignore[call-overload]


def composite_of(row: dict[str, object] | None) -> float | None:
    if row is None:
        return None
    for c in COMPOSITE_COLS:
        v = row.get(c)
        if isinstance(v, int | float) and not isinstance(v, bool):
            return float(v)
    return None


def _num(v: object) -> float | None:
    if isinstance(v, int | float) and not isinstance(v, bool) and v == v:
        return float(v)
    return None


@dataclass
class DayView:
    run: ModelRun
    spec_id: str
    spec: ModelSpec | None
    buckets: tuple[str, ...]
    rows: list[dict[str, object]]                 # 순위 종목(순위순) → 제외 종목(코드순)
    by_ticker: dict[str, dict[str, object]]
    upct: dict[str, dict[str, float]]             # 버킷 → 종목 → 유니버스 백분위
    spct: dict[str, dict[str, float]]             # 버킷 → 종목 → 업종(대분류) 백분위
    candidates: list[str]                         # 업종 상한을 건 후보(output.top_n)
    output: OutputRule
    uni: dict[str, dict[str, object]] = field(default_factory=dict)
    ind: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict)
    other_ranks: dict[str, dict[str, int | None]] = field(default_factory=dict)
    # 버킷 → 종목 → 업종 z(z 엔진만)
    zsec: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def date(self) -> str:
        return self.run.date

    @property
    def z_engine(self) -> bool:
        """주 모델 엔진이 z 엔진(v3_zscore — scope·v3 원본)인가. 버킷 점수가 z 라 점수 시트 축을
        z 로 싣고, 원값이 점수 표 열에 있다. 백분위 엔진(v4_rank)은 버킷 점수 자체가 0~100 백분위다.
        엑셀의 엔진 판정은 이 한 곳."""
        return self.spec is not None and self.spec.engine == V3ZScoreEngine.name

    @property
    def ranked(self) -> list[dict[str, object]]:
        return [r for r in self.rows if rank_of(r) is not None]

    def rank(self, t: str) -> int | None:
        return rank_of(self.by_ticker.get(t))

    def name(self, t: str) -> str:
        u = self.uni.get(t)
        return str(u["name"]) if u and u.get("name") is not None else ""

    def bucket_score(self, t: str, b: str) -> float | None:
        row = self.by_ticker.get(t)
        return None if row is None else _num(row.get(f"{b}_score"))

    def scored(self, t: str) -> bool:
        """엔진이 점수를 매긴 종목(결측 버킷 표기·결측 축·메타 결측 수의 대상) — 지표 행이
        있거나(v4) 점수 행에 종합 점수가 있다(scope·v3·v2 는 지표 긴 표가 0행이라 점수 행으로
        가린다, E-02). v4 의 D-13 적격성 탈락 행은 둘 다 없어 대상이 아니다."""
        return t in self.ind or composite_of(self.by_ticker.get(t)) is not None


def _buckets(spec: ModelSpec | None, rows: list[dict[str, object]]) -> tuple[str, ...]:
    if spec is not None:
        return tuple(spec.buckets)
    cols = rows[0].keys() if rows else ()
    return tuple(c[:-6] for c in cols if c.endswith("_score") and c not in COMPOSITE_COLS)


def load_day(model_root: Path, fi_root: Path | None, run: ModelRun, *,
             config_dir: Path | None = None, with_fi: bool = True, others: bool = True,
             spec_id: str | None = None) -> DayView:
    """판 하나 → DayView. `with_fi=False` 면 점수 표만(주간 추이용 가벼운 적재).
    `spec_id` 를 주면 그 모델을 주 모델로 본다(주간 파일이 한 주 내내 같은 모델을 쓰게)."""
    spec_id = spec_id or run.primary_spec
    if spec_id not in run.specs:
        raise DeliverError(f"주 모델 {spec_id} 가 판 {run.build_id} specs 에 없다")
    spec = spec_of(spec_id, None if config_dir is None else str(config_dir))
    raw = read_scores(model_root, run, spec_id)
    uni_rows = None if fi_root is None else read_fi(fi_root, "fi_universe", run.fi_build_id)
    if raw and "sector_l1" not in raw[0]:
        # v3·v2 엔진 점수 행에는 업종 열이 없다(원본 score_history 48·21열 그대로). 그대로 두면 업종 상한
        # 후보가 한 묶음(9개)으로 잘리고 업종 백분위·업종 시트가 무너진다 → 그 판 fi_universe 업종으로 채운다.
        if uni_rows is None:
            raise DeliverError(f"{spec_id} 점수에 업종 열이 없어 fi_root 가 필요하다(판 {run.build_id})")
        sect = {str(u["ticker"]): u for u in uni_rows}
        raw = [{**r, "sector_l1": sect.get(ticker_of(r), {}).get("sector_l1"),
                "sector_l2": sect.get(ticker_of(r), {}).get("sector_l2")} for r in raw]
    ranked = sorted((r for r in raw if rank_of(r) is not None), key=lambda r: rank_of(r) or 0)
    rest = sorted((r for r in raw if rank_of(r) is None), key=ticker_of)
    rows = ranked + rest
    by_ticker = {ticker_of(r): r for r in rows}
    buckets = _buckets(spec, rows)
    output = spec.output if spec is not None else OutputRule()
    min_size = MIN_SECTOR_DEFAULT
    if spec is not None and isinstance(spec.params.get("min_sector_size"), int):
        min_size = int(spec.params["min_sector_size"])  # type: ignore[arg-type]

    l1 = {t: (None if r.get("sector_l1") is None else str(r["sector_l1"]))
          for t, r in by_ticker.items()}
    upct: dict[str, dict[str, float]] = {}
    spct: dict[str, dict[str, float]] = {}
    bucket_vals: dict[str, dict[str, float]] = {}
    for b in buckets:
        vals = {t: v for t, r in by_ticker.items() if (v := _num(r.get(f"{b}_score"))) is not None}
        bucket_vals[b] = vals
        upct[b] = pct_rank_avg(vals)
        spct[b] = group_pct(vals, l1, min_size)

    level = "sector_l2" if output.sector_level == "L2" else "sector_l1"
    cands = cap_candidates(ranked, output.top_n, output.max_per_sector, lambda r: r.get(level))
    view = DayView(run, spec_id, spec, buckets, rows, by_ticker, upct, spct,
                   [ticker_of(dict(r)) for r in cands], output)
    if view.z_engine:                     # 업종 z — 점수 시트·업종 시트 참고값(순위·점수에 안 쓴다)
        view.zsec = {b: group_z(vals, l1, min_size) for b, vals in bucket_vals.items()}
    if with_fi:
        if uni_rows is None:
            raise DeliverError("fi_root 가 필요하다")
        view.uni = {str(r["ticker"]): r for r in uni_rows}
        for r in read_indicators(model_root, run, spec_id):
            view.ind.setdefault(str(r["ticker"]), {})[str(r["key"])] = r
    if others:
        for sid in sorted(run.specs):
            if sid == spec_id:
                continue
            view.other_ranks[sid] = {ticker_of(r): rank_of(r)
                                     for r in read_scores(model_root, run, sid)}
    return view


__all__ = ["DayView", "composite_of", "load_day", "rank_of", "spec_of", "ticker_of"]
