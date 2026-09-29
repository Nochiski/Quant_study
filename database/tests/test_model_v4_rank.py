"""v4 순위 엔진(`engines/v4_rank`, 플랜 D-13' · 연구 §1-7 R-1·R-2·R-3).

합성 `FactorInputs`(계약 열 그대로)로 규칙을 하나씩 핀으로 박는다.
(a) 레지스트리 — `v4_rank@0.1`·`@0.2` 적재·검증, 두 판은 버킷 가중만 다르다
(b) 지표 공식 — 손으로 계산한 값(창·최소 개수·부호·수정주가 미해결 창·입수일)
(c) 백분위 — 동률 평균순위, 대분류 내 vs 유니버스, 소수 업종 되돌림
(d) 버킷·종합 — 결측 비중 ≥ 50% 버킷 결측, 비례 재정규화, 버킷 부족 제외, 게이트 제외와 NULL rank
(e) 유니버스 재판정 · D-13 적격성 · 출력 모양 · 결정성
"""
from __future__ import annotations

import functools
import random
import statistics
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta

import pytest
from model import registry
from model.contracts import (
    ELIGIBILITY_FLAGS,
    FI_TABLES,
    INDICATOR_COLUMNS,
    FactorInputs,
    Indicator,
    ModelSpec,
    score_columns,
)
from model.engines import v4_rank

D = "2026-09-28"
D0 = date.fromisoformat(D)
SPEC_ID = "v4_rank@0.1"
ENGINE = v4_rank.ENGINE
SCORE_KEYS = ("VOL60", "EP", "DY0", "OPM_TTM", "FCF_A", "M_PULL_C", "REV_OP_1M", "REV_OP_3M",
              "REV_NI_1M", "REV_NI_3M", "CRDT_CHG", "FRGN60")
DISPLAY_KEYS = ("R1M", "M_52WH", "R3M", "R6M", "R12_1", "EP_FWD")
REV_KEYS = ("REV_OP_1M", "REV_OP_3M", "REV_NI_1M", "REV_NI_3M")


@functools.cache
def _spec(spec_id: str = SPEC_ID) -> ModelSpec:
    return registry.get(spec_id)


# ── 합성 판 ────────────────────────────────────────────────────────────────────
def _r(table: str, **values: object) -> dict[str, object]:
    return {c: values.get(c) for c in FI_TABLES[table].column_names}


def _day(k: int) -> date:
    """k 세션 전. 합성 판은 달력일 하루 = 세션 하나로 둔다(엔진은 행 순서로 센다)."""
    return D0 - timedelta(days=k)


class Board:
    """종목을 더해 가며 계약 표를 채운다. 기본 = 적격 보통주 · 신선 · 추정기관 5 · 시총 5,000억 ·
    20세션 평균 거래대금 100억 · D-13 표식 전부 False."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, object]]] = {n: [] for n in FI_TABLES}

    def stock(self, t: str, closes: list[float], *, adj: Sequence[float | None] | None = None,
              adj_ok: Sequence[bool] | None = None, sector: str | None = "G10",
              end_k: int = 0, **uni: object) -> Board:
        """종가 목록(마지막 값 = D − end_k 세션) + fi_universe 행."""
        n = len(closes)
        for i, c in enumerate(closes):
            day = _day(n - 1 - i + end_k)
            self.tables["fi_prices"].append(
                _r("fi_prices", ticker=t, date=day, close=c, price_source="krx"))
            self.tables["fi_adj_prices"].append(
                _r("fi_adj_prices", ticker=t, date=day,
                   adj_close=float(c) if adj is None else adj[i], adj_factor=1.0,
                   adj_ok=True if adj_ok is None else adj_ok[i]))
        row: dict[str, object] = dict(
            ticker=t, date=D0, name=t, market="KOSPI", sec_type="common", market_cap=5000.0,
            mktcap_basis="krx", sector_l1=sector, sector_l1_name=sector,
            sector_l2=None if sector is None else sector + "10", has_estimates=True,
            coverage_state="fresh", coverage_age_days=0, n_analysts=5, adv20=100.0,
            is_admin=False, is_halted=False, audit_adverse=False, filing_late=False,
            eligible=True, exclude_reason=None)
        row.update(uni)
        self.tables["fi_universe"].append(_r("fi_universe", **row))
        return self

    def fin(self, t: str, period: str, period_type: str = "annual", **vals: object) -> Board:
        self.tables["fi_fin_summary"].append(
            _r("fi_fin_summary", ticker=t, period=period, period_type=period_type, **vals))
        return self

    def cons(self, t: str, period: str, horizon: str, n: int | None = 5,
             **vals: object) -> Board:
        self.tables["fi_consensus"].append(
            _r("fi_consensus", ticker=t, target_period=period, horizon=horizon, n_analysts=n,
               **vals))
        return self

    def flow(self, t: str, k: int, foreign: float | None) -> Board:
        self.tables["fi_flows"].append(
            _r("fi_flows", ticker=t, date=_day(k), foreign_investor=foreign))
        return self

    def credit(self, t: str, k: int, balance: int, lag: int = 2) -> Board:
        """k 세션 전 잔고 — 입수일 = 그 날 + lag 세션(D 보다 뒤면 엔진이 보면 안 된다)."""
        self.tables["fi_credit"].append(
            _r("fi_credit", ticker=t, date=_day(k), credit_balance=balance,
               available_date=_day(k - lag)))
        return self

    def fi(self) -> FactorInputs:
        return FactorInputs(D, "morning", "synthetic", {k: list(v) for k, v in self.tables.items()})


def _walk(seed: int, n: int = 260) -> list[float]:
    rng = random.Random(seed)
    px, out = 10_000.0, []
    for _ in range(n):
        px *= 1.0 + rng.uniform(-0.02, 0.02) * (1 + seed % 4)
        out.append(px)
    return out


def _full(b: Board, t: str, i: int, sector: str | None = "G10", **uni: object) -> Board:
    """점수 지표 12개가 전부 나오는 종목 — i 로 값을 흩는다."""
    b.stock(t, _walk(i), sector=sector, market_cap=1000.0 + 37.0 * i)
    b.tables["fi_universe"][-1].update(uni)
    b.fin(t, "2025/12", ni=5.0 + (i * 7) % 23, fcf=3.0 + (i * 5) % 17, total_assets=500.0,
          dps=50.0 + 13 * i)
    for j, per in enumerate(("2026/06", "2026/03", "2025/12", "2025/09")):
        b.fin(t, per, "quarter", op=2.0 + (i + j) % 5, revenue=40.0 + (i * 3 + j) % 11)
    b.cons(t, "2026/12", "cur", op=100.0 + i, ni=60.0 + (i * 3) % 7)
    b.cons(t, "2026/12", "1m", op=100.0 + (i * 5) % 9, ni=60.0)
    b.cons(t, "2026/12", "3m", op=90.0 + i % 4, ni=55.0 + i % 6)
    for k in range(60):
        b.flow(t, k, float((i * 7 + k) % 13 - 6))
    for k in range(40):
        b.credit(t, k, 1000 + (i * 11 + k * 3) % 97)
    return b


def _patch(b: Board, table: str, t: str, where: dict[str, object] | None = None,
           **vals: object) -> None:
    """이미 넣은 행 고치기 — 종목 t 이고 where 가 맞는 행에 vals 를 덮는다."""
    for r in b.tables[table]:
        if r["ticker"] == t and all(r[k] == v for k, v in (where or {}).items()):
            r.update(vals)


def _run(b: Board, spec: ModelSpec | None = None):
    res = ENGINE.run(spec or _spec(), b.fi())
    scores = {str(r["ticker"]): r for r in res.scores}
    inds = {(str(r["ticker"]), str(r["key"])): r for r in res.indicators}
    return scores, inds


def _why(row: dict[str, object]) -> str | None:
    """지표 자신의 사유·표식만 — 뒤에 붙는 업종 되돌림·버킷결측·적격미확인 표시는 뺀다."""
    own = [x for x in str(row["flag"] or "").split(";")
           if x and not x.startswith(("업종", "버킷결측", "적격미확인"))]
    return own[0] if own else None


def _pct(rank: float, n: int) -> float:
    """1-기반 (평균)순위 → 0~100 백분위(엔진 규약)."""
    return 100.0 * (rank - 1) / (n - 1)


# ── (a) 레지스트리 ─────────────────────────────────────────────────────────────
def test_registry_both_v4_specs_load_validate_and_differ_only_in_bucket_weights() -> None:
    s1, s2 = _spec("v4_rank@0.1"), _spec("v4_rank@0.2")
    assert s1.validate() == [] and s2.validate() == []
    assert s1.engine == s2.engine == "v4_rank" == ENGINE.name
    assert dict(s1.buckets) == {"low_risk": .25, "value": .25, "quality": .20, "pull": .10,
                                "revision": .10, "aux": .10}
    assert list(s2.buckets) == list(s1.buckets)
    assert all(w == pytest.approx(1 / 6, abs=1e-15) for w in s2.buckets.values())
    assert replace(s2, version="0.1", buckets=s1.buckets) == s1

    ind = {i.key: i for i in s1.indicators}
    assert tuple(i.key for i in s1.indicators if i.role == "score") == SCORE_KEYS
    assert tuple(i.key for i in s1.indicators if i.role == "display") == DISPLAY_KEYS
    assert {k for k, i in ind.items() if i.direction == -1} == {"VOL60", "CRDT_CHG"}
    assert all(i.definition and i.weight == 1.0 for i in s1.indicators)
    assert [(g.key, g.rule, g.value) for g in s1.gates] == [("M_PULL_C", "exclude_bottom_pct", .30)]
    assert s1.sector_neutral == "L1"
    assert s1.params["sector_neutral_buckets"] == ["value", "quality"]
    assert s1.universe.sec_types == ("common",) and s1.universe.require_estimates
    assert s1.universe.coverage_grace_days == 5 and s1.universe.min_market_cap is None
    assert s1.universe.min_adv20 == 10.0 and s1.universe.exclude == ELIGIBILITY_FLAGS
    assert s1.params["bucket_missing_share"] == 0.5


def test_output_columns_are_base_plus_bucket_scores() -> None:
    spec = _spec()
    cols = ENGINE.output_columns(spec)
    assert cols == score_columns(spec)
    assert cols[-6:] == ("low_risk_score", "value_score", "quality_score", "pull_score",
                         "revision_score", "aux_score")


def test_engine_rejects_specs_it_cannot_honor() -> None:
    spec, fi = _spec(), Board().fi()
    with pytest.raises(ValueError, match="v4_rank"):
        ENGINE.run(replace(spec, engine="v3_zscore"), fi)
    with pytest.raises(ValueError, match="FOO"):
        ENGINE.run(replace(spec, indicators=(*spec.indicators, Indicator("FOO", "aux"))), fi)
    # 기본 규칙(eligible)보다 넓은 유니버스는 재판정으로 되살릴 수 없다 → 조용히 무시하지 않는다
    wide = replace(spec.universe, sec_types=("common", "preferred"))
    with pytest.raises(ValueError, match="sec_types"):
        ENGINE.run(replace(spec, universe=wide), fi)
    with pytest.raises(ValueError, match="min_buckets"):
        ENGINE.run(replace(spec, params={}), fi)
    with pytest.raises(ValueError, match="fi_credit"):
        ENGINE.run(spec, FactorInputs(D, "morning", "x", {n: [] for n in FI_TABLES
                                                          if n != "fi_credit"}))


# ── (b) 지표 공식 ──────────────────────────────────────────────────────────────
def test_vol60_is_sample_std_of_last_60_returns_with_45_minimum() -> None:
    p = [100.0 + (i % 3) + i * 0.1 for i in range(70)]
    b = Board().stock("A", p).stock("B", p[:46]).stock("C", p[:45])
    _, ind = _run(b)
    assert ind["A", "VOL60"]["raw"] == statistics.stdev([p[j] / p[j - 1] - 1.0
                                                         for j in range(10, 70)])
    assert ind["B", "VOL60"]["raw"] == statistics.stdev([p[j] / p[j - 1] - 1.0
                                                         for j in range(1, 46)])
    assert ind["C", "VOL60"]["raw"] is None and ind["C", "VOL60"]["flag"] == "이력부족"
    # 원값은 표준편차 그대로(양수), 방향 −1 → 변동성이 작을수록 백분위가 높다
    assert ind["A", "VOL60"]["raw"] > 0


def test_price_windows_count_sessions() -> None:
    p = [100.0 + i for i in range(260)]
    p[5] = 1000.0          # 254세션 전 — 252세션 창 밖
    p[10] = 500.0          # 249세션 전 — 창 안
    b = (Board().stock("A", p).stock("B", p[-200:]).stock("C", p[-199:])
         .stock("E", p[-253:]).stock("F", p[-252:]))
    _, ind = _run(b)
    assert ind["A", "M_52WH"]["raw"] == p[-1] / 500.0
    assert ind["A", "R1M"]["raw"] == p[-1] / p[-22] - 1.0
    assert ind["A", "R3M"]["raw"] == p[-1] / p[-64] - 1.0
    assert ind["A", "R6M"]["raw"] == p[-1] / p[-127] - 1.0
    assert ind["A", "R12_1"]["raw"] == p[-22] / p[-253] - 1.0
    assert ind["B", "M_52WH"]["raw"] == p[-1] / max(p[-200:])        # 200행이면 된다
    assert ind["C", "M_52WH"]["raw"] is None and ind["C", "M_52WH"]["flag"] == "이력부족"
    assert ind["E", "R12_1"]["raw"] == p[-22] / p[-253] - 1.0        # 253행 = t−252 까지
    assert ind["F", "R12_1"]["raw"] is None and ind["F", "R12_1"]["flag"] == "이력부족"


def test_adj_close_first_close_fallback_and_unresolved_event_windows() -> None:
    flat = [100.0] * 260
    rising = [50.0 + i for i in range(260)]
    recent = [True] * 229 + [False] * 31     # 30세션 전 미해결 사건 → 그 행부터 D 까지 False
    old = [True] * 2 + [False] * 258         # 257세션 전 사건 → 최근 253행은 전부 False
    b = (Board().stock("A", flat, adj=rising)
         .stock("B", rising, adj=[None] * 260)
         .stock("C", rising, adj_ok=recent)
         .stock("E", rising, adj_ok=old))
    _, ind = _run(b)
    assert ind["A", "R1M"]["raw"] == rising[-1] / rising[-22] - 1.0     # 수정종가가 먼저
    assert ind["B", "R1M"]["raw"] == rising[-1] / rising[-22] - 1.0     # 없으면 종가
    # 사건을 넘는 창만 결측 — 창이 전부 사건 뒤면 척도가 이어진다
    for key in ("VOL60", "M_52WH", "R3M", "M_PULL_C"):
        assert ind["C", key]["raw"] is None and ind["C", key]["flag"] == "수정주가미해결", key
    assert ind["C", "R1M"]["raw"] == rising[-1] / rising[-22] - 1.0
    for key in ("VOL60", "M_52WH", "R1M", "R12_1", "M_PULL_C"):
        assert ind["E", key]["raw"] is not None, key


def test_m_pull_c_is_mean_of_min_tie_percent_ranks() -> None:
    n = 210
    b = (Board().stock("A", [200.0] + [100.0] * (n - 1))             # 52WH .5  · 1개월 0
         .stock("B", [100.0] * n)                                     # 52WH 1   · 1개월 0
         .stock("C", [200.0] + [100.0] * (n - 2) + [90.0])            # 52WH .45 · 1개월 −10%
         .stock("E", [100.0] * (n - 1) + [110.0]))                    # 52WH 1   · 1개월 +10%
    _, ind = _run(b)
    # 52WH: C 0 · A 1/3 · B=E 2/3(동률은 낮은 순위) / −1개월: E 0 · A=B 1/3 · C 1
    want = {"A": (1 / 3 + 1 / 3) / 2, "B": (2 / 3 + 1 / 3) / 2, "C": (0 + 1) / 2,
            "E": (2 / 3 + 0) / 2}
    for t, v in want.items():
        assert ind[t, "M_PULL_C"]["raw"] == pytest.approx(v, abs=1e-15), t


def test_value_and_quality_formulas() -> None:
    b = Board()
    b.stock("X", [50_000.0] * 30, market_cap=2000.0)
    b.fin("X", "2025/12", ni=100.0, fcf=30.0, total_assets=600.0, dps=1000.0)
    b.fin("X", "2024/12", ni=80.0, fcf=10.0, total_assets=500.0, dps=800.0)
    for per, op, rev in (("2026/06", 10, 100), ("2026/03", 20, 100), ("2025/12", 30, 200),
                         ("2025/09", 40, 100), ("2025/06", 999, 1)):
        b.fin("X", per, "quarter", op=float(op), revenue=float(rev))
    # Y: 최신 연간이 비어 있으면 그 전 기 · 최신 분기가 비면 그 다음 연속 4분기 · DPS 없음 = 0
    b.stock("Y", [10_000.0] * 30, market_cap=1000.0)
    b.fin("Y", "2025/12", ni=None, fcf=None, total_assets=800.0)
    b.fin("Y", "2024/12", ni=30.0, fcf=40.0, total_assets=400.0)
    for per, op in (("2026/06", None), ("2026/03", 5.0), ("2025/12", 5.0), ("2025/09", 5.0),
                    ("2025/06", 5.0)):
        b.fin("Y", per, "quarter", op=op, revenue=50.0)
    # Z: 연간이 D−730일 밖(2023/12) · 분기가 연속이 아니다
    b.stock("Z", [10_000.0] * 30)
    b.fin("Z", "2023/12", ni=10.0, dps=100.0, fcf=1.0, total_assets=10.0)
    for per in ("2026/06", "2026/03", "2025/09", "2025/06"):
        b.fin("Z", per, "quarter", op=1.0, revenue=10.0)
    # V: 연속 4분기지만 최근 분기 기말이 D−550일 밖 · W: 분모 0
    b.stock("V", [10_000.0] * 30)
    for per in ("2024/12", "2024/09", "2024/06", "2024/03"):
        b.fin("V", per, "quarter", op=1.0, revenue=10.0)
    b.stock("W", [10_000.0] * 30)
    b.fin("W", "2025/12", ni=1.0, fcf=10.0, total_assets=0.0)
    for per in ("2026/06", "2026/03", "2025/12", "2025/09"):
        b.fin("W", per, "quarter", op=1.0, revenue=0.0)
    _, ind = _run(b)

    assert ind["X", "EP"]["raw"] == 100.0 / 2000.0
    assert ind["X", "DY0"]["raw"] == 1000.0 / 50_000.0 and _why(ind["X", "DY0"]) is None
    assert ind["X", "FCF_A"]["raw"] == 30.0 / 600.0
    assert ind["X", "OPM_TTM"]["raw"] == (10 + 20 + 30 + 40) / (100 + 100 + 200 + 100)
    assert ind["Y", "EP"]["raw"] == 30.0 / 1000.0
    assert ind["Y", "FCF_A"]["raw"] == 40.0 / 400.0
    assert ind["Y", "DY0"]["raw"] == 0.0 and _why(ind["Y", "DY0"]) == "무배당"
    assert ind["Y", "OPM_TTM"]["raw"] == 20.0 / 200.0
    for key in ("EP", "FCF_A", "OPM_TTM"):
        assert ind["Z", key]["raw"] is None and _why(ind["Z", key]) == "원천없음", key
    assert ind["Z", "DY0"]["raw"] == 0.0 and _why(ind["Z", "DY0"]) == "무배당"
    assert ind["V", "OPM_TTM"]["raw"] is None and _why(ind["V", "OPM_TTM"]) == "원천없음"
    for key in ("FCF_A", "OPM_TTM"):
        assert ind["W", key]["raw"] is None and _why(ind["W", key]) == "분모≤0", key


def test_foreign_60_and_credit_change_formulas() -> None:
    b = Board()
    b.stock("F", [100.0] * 70)
    for k in range(60):
        b.flow("F", k, 1.0)
    b.flow("F", 60, 1000.0)                     # 61세션 전 — 창 밖
    b.stock("G", [100.0] * 70)
    for k in range(44):
        b.flow("G", k, 1.0)                     # 44개 < 45
    b.stock("H", [100.0] * 70, market_cap=2000.0)
    for k in range(50):
        b.flow("H", k, 2.0 if k < 45 else None)     # 값 있는 행 45개
    # 신용: 입수일(date + 2세션) > D 인 k=0·1 행은 보면 안 된다 → t = 2세션 전, t−20 = 22세션 전
    for k in range(40):
        b.credit("F", k, {0: 99_999, 1: 99_999, 2: 1200, 22: 1000}.get(k, 1000))
    for k in range(40):
        b.credit("G", k, {2: 900, 22: 1000}.get(k, 1000))
    b.credit("H", 2, 100).credit("H", 22, 0)
    b.stock("I", [100.0] * 70)
    b.credit("I", 0, 500).credit("I", 1, 500)       # 전부 D 뒤 입수
    _, ind = _run(b)

    assert ind["F", "FRGN60"]["raw"] == 60.0 / (5000.0 * 100)     # 백만원 ÷ (억원 × 100)
    assert ind["G", "FRGN60"]["raw"] is None and _why(ind["G", "FRGN60"]) == "이력부족"
    assert ind["H", "FRGN60"]["raw"] == 90.0 / (2000.0 * 100)
    assert ind["F", "CRDT_CHG"]["raw"] == 1200 / 1000 - 1.0
    assert ind["G", "CRDT_CHG"]["raw"] == 900 / 1000 - 1.0
    assert ind["H", "CRDT_CHG"]["raw"] is None and _why(ind["H", "CRDT_CHG"]) == "분모≤0"
    assert ind["I", "CRDT_CHG"]["raw"] is None and _why(ind["I", "CRDT_CHG"]) == "원천없음"
    # 원값은 변화율 그대로, 방향 −1 → 신용이 준(G) 쪽 백분위가 높다
    assert ind["G", "CRDT_CHG"]["pct"] > ind["F", "CRDT_CHG"]["pct"]


def test_revision_changes_flags_period_and_coverage() -> None:
    b = Board()
    for t, n in (("R", 5), ("S", 2)):
        b.stock(t, [100.0] * 30)
        b.cons(t, "2025/12", "cur", n=n, op=999.0, ni=999.0)     # 끝난 결산기 — 안 본다
        b.cons(t, "2026/12", "cur", n=n, op=110.0, ni=-50.0)
        b.cons(t, "2026/12", "1m", n=n, op=100.0, ni=-40.0)
        b.cons(t, "2026/12", "3m", n=n, op=-10.0, ni=20.0)
        b.cons(t, "2027/12", "cur", n=n, op=1.0, ni=1.0)          # 더 먼 결산기 — 안 본다
    b.stock("T", [100.0] * 30)
    b.cons("T", "2026/12", "cur", op=-30.0, ni=10.0)
    b.cons("T", "2026/12", "1m", op=-40.0, ni=0.0)
    b.cons("T", "2026/12", "3m", op=None, ni=10.0)
    b.stock("U", [100.0] * 30)
    _, ind = _run(b)

    got = {k: (ind["R", k]["raw"], _why(ind["R", k]))
           for k in ("REV_OP_1M", "REV_NI_1M", "REV_OP_3M", "REV_NI_3M")}
    assert got == {"REV_OP_1M": ((110 - 100) / 100, None),
                   "REV_NI_1M": ((-50 + 40) / 40, "적확"),
                   "REV_OP_3M": ((110 + 10) / 10, "흑전"),
                   "REV_NI_3M": ((-50 - 20) / 20, "적전")}
    assert ind["R", "EP_FWD"]["raw"] == -50.0 / 5000.0
    for k in ("REV_OP_1M", "REV_NI_1M", "REV_OP_3M", "REV_NI_3M"):
        assert ind["S", k]["raw"] is None and _why(ind["S", k]) == "커버리지<3", k
    assert ind["S", "EP_FWD"]["raw"] == -50.0 / 5000.0        # 표시 지표는 커버리지와 무관
    assert (ind["T", "REV_OP_1M"]["raw"], _why(ind["T", "REV_OP_1M"])) == (0.25, "적축")
    assert ind["T", "REV_NI_1M"]["raw"] is None and _why(ind["T", "REV_NI_1M"]) == "분모≤0"
    assert ind["T", "REV_OP_3M"]["raw"] is None and _why(ind["T", "REV_OP_3M"]) == "원천없음"
    assert (ind["T", "REV_NI_3M"]["raw"], _why(ind["T", "REV_NI_3M"])) == (0.0, None)
    for k in ("REV_OP_1M", "EP_FWD"):
        assert ind["U", k]["raw"] is None and _why(ind["U", k]) == "원천없음", k


# ── (c) 백분위 ─────────────────────────────────────────────────────────────────
def test_percentile_helpers_average_and_min_ties() -> None:
    vals = {"a": 1.0, "b": 2.0, "c": 2.0, "d": 3.0}
    assert v4_rank.pct_rank_avg(vals) == {"a": 0.0, "b": 50.0, "c": 50.0, "d": 100.0}
    assert v4_rank.pct_rank_avg({"x": 7.0}) == {"x": 50.0}
    assert v4_rank.pct_rank_avg({}) == {}
    assert v4_rank.percent_rank_min(vals) == {"a": 0.0, "b": 1 / 3, "c": 1 / 3, "d": 1.0}
    assert v4_rank.percent_rank_min({"x": 7.0}) == {"x": 0.0}


def test_value_quality_percentiles_within_l1_others_universe_wide() -> None:
    b = Board()
    eps = {"s1": .01, "s2": .02, "s3": .03, "s4": .04, "s5": .05,       # G10
           "t1": .10, "t2": .20, "t3": .30, "t4": .40, "t5": .50,       # G20
           "u1": .06, "u2": .07, "u3": .08,                             # G30 — 3종목(소수)
           "n1": .09}                                                   # 업종 없음
    for i, (t, ep) in enumerate(eps.items()):
        sector = {"s": "G10", "t": "G20", "u": "G30", "n": None}[t[0]]
        b.stock(t, _walk(i), sector=sector, market_cap=1000.0)
        b.fin(t, "2025/12", ni=ep * 1000.0)
    _, ind = _run(b)

    for grp in ("s", "t"):
        for k in range(1, 6):
            r = ind[f"{grp}{k}", "EP"]
            assert r["pct"] == _pct(k, 5) and r["flag"] is None, (grp, k)
    order = sorted(eps, key=eps.__getitem__)
    for t, flag in (("u1", "업종소수→전체"), ("u2", "업종소수→전체"), ("u3", "업종소수→전체"),
                    ("n1", "업종없음→전체")):
        assert ind[t, "EP"]["pct"] == _pct(order.index(t) + 1, len(eps)), t
        assert ind[t, "EP"]["flag"] == flag, t
    # 저위험은 유니버스 백분위(방향 −1: 변동성 작은 순서가 높다)
    vol = {t: ind[t, "VOL60"]["raw"] for t in eps}
    by_low = sorted(eps, key=lambda t: -vol[t])
    for t in eps:
        assert ind[t, "VOL60"]["pct"] == _pct(by_low.index(t) + 1, len(eps)), t
    # 무배당 0 동률 → 업종 안 평균순위 50
    assert {ind[t, "DY0"]["pct"] for t in eps if t[0] in "st"} == {50.0}


# ── (d) 버킷 · 종합 · 게이트 ────────────────────────────────────────────────────
def test_pull_gate_excludes_bottom_30pct_keeps_scores_and_nulls_rank() -> None:
    n = 210
    b = (Board().stock("A", [200.0] + [100.0] * (n - 1))
         .stock("B", [100.0] * n)
         .stock("C", [200.0] + [100.0] * (n - 2) + [90.0])
         .stock("E", [100.0] * (n - 1) + [110.0]))
    for t in "ABCE":
        b.fin(t, "2025/12", ni=10.0)          # 밸류 버킷(E/P) — 저위험·밸류·고점근접 3버킷
    sc, ind = _run(b)
    # M_PULL_C: A = E = 1/3(하위 동률 → 백분위 16.7) · B = C = 1/2(83.3)
    assert ind["A", "M_PULL_C"]["pct"] == pytest.approx(100 / 6)
    for t in "AE":
        assert sc[t]["excluded"] is True and sc[t]["exclude_reason"] == "pull_gate", t
        assert sc[t]["rank"] is None
        assert sc[t]["composite"] is not None and sc[t]["pull_score"] is not None
    ranked = sorted("BC", key=lambda t: (-sc[t]["composite"], t))
    assert [sc[t]["rank"] for t in ranked] == [1, 2]
    assert all(sc[t]["excluded"] is False and sc[t]["exclude_reason"] is None for t in "BC")


def test_bucket_and_composite_renormalize_over_available_parts() -> None:
    b = Board()
    for i in range(1, 7):
        _full(b, f"F{i}", i)
    b.stock("M", _walk(99))     # 재무·컨센서스 없음 → 밸류는 DY0 만(50% 결측) · 퀄리티·리비전 없음
    for k in range(60):
        b.flow("M", k, 1.0)
    for k in range(40):
        b.credit("M", k, 500)
    sc, ind = _run(b)
    spec = _spec()

    f1 = sc["F1"]
    assert f1["value_score"] == pytest.approx(
        (ind["F1", "EP"]["pct"] + ind["F1", "DY0"]["pct"]) / 2, abs=1e-12)
    assert f1["n_buckets_used"] == 6
    m = sc["M"]
    assert ind["M", "EP"]["raw"] is None
    # 2지표 버킷에서 하나 결측 = 50% → 버킷 결측. 있는 DY0 백분위는 남기고 flag 로 알린다
    assert m["value_score"] is None and ind["M", "DY0"]["pct"] == 0.0
    assert ind["M", "DY0"]["flag"] == "무배당;버킷결측(50%)"
    assert m["quality_score"] is None and m["revision_score"] is None
    assert m["n_buckets_used"] == 3 and m["exclude_reason"] != "insufficient_data"
    used = ("low_risk", "pull", "aux")
    total = sum(spec.buckets[x] for x in used)
    assert m["composite"] == pytest.approx(
        sum(m[f"{x}_score"] * spec.buckets[x] for x in used) / total, abs=1e-12)


def test_insufficient_buckets_or_missing_low_risk_is_excluded() -> None:
    b = Board()
    for i in range(1, 7):
        _full(b, f"F{i}", i)
    b.stock("I", _walk(50, 30))           # 30행: 저위험·고점근접 없음 · 밸류는 EP 없어 결측
    _full(b, "J", 51)                                  # 저위험만 빠진 종목(40세션 전 미해결 사건)
    b.tables["fi_adj_prices"] = [
        dict(r, adj_ok=not (r["ticker"] == "J" and str(r["date"]) >= str(_day(40))))
        for r in b.tables["fi_adj_prices"]]
    sc, _ = _run(b)
    assert sc["I"]["n_buckets_used"] == 0
    assert sc["J"]["low_risk_score"] is None and sc["J"]["n_buckets_used"] >= 3
    for t in "IJ":
        assert sc[t]["excluded"] is True and sc[t]["exclude_reason"] == "insufficient_data", t
        assert sc[t]["rank"] is None
    ranks = sorted(r["rank"] for r in sc.values() if r["rank"] is not None)
    assert ranks == list(range(1, len(ranks) + 1))


def test_bucket_missing_when_missing_weight_share_reaches_half() -> None:
    b = Board()
    for i in range(1, 7):
        _full(b, f"F{i}", i)
    _full(b, "H2", 21)          # 리비전 4개 중 2개 결측(1개월 전 행 없음) = 정확히 50%
    b.tables["fi_consensus"] = [r for r in b.tables["fi_consensus"]
                                if not (r["ticker"] == "H2" and r["horizon"] == "1m")]
    _full(b, "Q1", 22)          # 리비전 1개 결측(3개월 전 op 없음) = 25% → 남은 3개로
    _patch(b, "fi_consensus", "Q1", {"horizon": "3m"}, op=None)
    _full(b, "V1", 23)          # 퀄리티 2개 중 FCF/자산 결측 = 50%
    _patch(b, "fi_fin_summary", "V1", {"period_type": "annual"}, fcf=None)
    _full(b, "E0", 24)          # 밸류 2개 중 E/P 결측 = 50%
    _patch(b, "fi_fin_summary", "E0", {"period_type": "annual"}, ni=None)
    sc, ind = _run(b)

    assert sc["H2"]["revision_score"] is None
    assert ind["H2", "REV_OP_3M"]["pct"] is not None            # 있는 지표 백분위는 남는다
    for k in REV_KEYS:
        assert ind["H2", k]["flag"].endswith("버킷결측(50%)"), k
    kept = [ind["Q1", k]["pct"] for k in REV_KEYS if k != "REV_OP_3M"]
    assert sc["Q1"]["revision_score"] == pytest.approx(sum(kept) / 3, abs=1e-12)
    assert not any("버킷결측" in (ind["Q1", k]["flag"] or "") for k in REV_KEYS)
    assert sc["V1"]["quality_score"] is None
    assert ind["V1", "OPM_TTM"]["flag"] == "버킷결측(50%)"       # G10 10종목 → 업종 안 백분위
    assert _why(ind["V1", "FCF_A"]) == "원천없음"
    assert sc["E0"]["value_score"] is None and sc["E0"]["n_buckets_used"] == 5
    # 결측 비중은 지표 weight 로 잰다 — DY0 가중 3 이면 E/P 결측은 25% 라 버킷이 산다
    spec = _spec()
    heavy = replace(spec, indicators=tuple(replace(i, weight=3.0) if i.key == "DY0" else i
                                           for i in spec.indicators))
    sc3, ind3 = _run(b, heavy)
    assert sc3["E0"]["value_score"] == ind3["E0", "DY0"]["pct"]
    f1 = (ind3["F1", "EP"]["pct"] + 3 * ind3["F1", "DY0"]["pct"]) / 4
    assert sc3["F1"]["value_score"] == pytest.approx(f1, abs=1e-12)


# ── (e) 유니버스 · 출력 모양 · 결정성 ───────────────────────────────────────────
def test_universe_is_rechecked_against_the_spec_rule() -> None:
    b = Board()
    _full(b, "OK", 1)
    _full(b, "KQ", 2, market="KOSDAQ")
    _full(b, "GR", 3, coverage_state="grace", coverage_age_days=5)
    _full(b, "SP", 4, sec_type="spac")                          # 기본 규칙은 통과, v4 는 보통주만
    _full(b, "GX", 5, coverage_state="grace", coverage_age_days=6)    # 유예 5거래일 초과
    _full(b, "LP", 6, coverage_state="lapsed", coverage_age_days=9, eligible=False,
          exclude_reason="estimates_lapsed")
    _full(b, "NE", 7, eligible=False, exclude_reason="no_price")
    b.stock("ND", [100.0] * 30, end_k=1)                        # D 가격 행 없음
    sc, ind = _run(b)
    assert set(sc) == {"OK", "KQ", "GR"}
    assert {t for t, _ in ind} == {"OK", "KQ", "GR"}
    assert sc["GR"]["coverage_state"] == "grace"


def test_d13_eligibility_reasons_null_flags_and_loss_makers() -> None:
    b = Board()
    for i in range(1, 7):
        _full(b, f"F{i}", i)
    _full(b, "AD", 11, is_admin=True, adv20=3.0)        # 표식이 거래대금보다 먼저 걸린다
    _full(b, "HT", 12, is_halted=True)
    _full(b, "AU", 13, audit_adverse=True)
    _full(b, "FL", 14, filing_late=True)
    _full(b, "LO", 15, adv20=9.99)
    _full(b, "LU", 16, adv20=None)
    _full(b, "NA", 17, is_admin=None, filing_late=None)   # 모른다 → 적격 + 표시
    _full(b, "LS", 18)                                      # 적자 — 적격, E/P 음수는 값이다
    _patch(b, "fi_fin_summary", "LS", {"period_type": "annual"}, ni=-30.0)
    sc, ind = _run(b)

    want = {"AD": "admin", "HT": "halted", "AU": "audit_adverse", "FL": "filing_late",
            "LO": "adv20", "LU": "adv20_unknown"}
    for t, why in want.items():
        r = sc[t]
        assert (r["excluded"], r["exclude_reason"], r["rank"]) == (True, why, None), t
        assert r["composite"] is None and r["n_buckets_used"] == 0, t
        assert all(r[f"{x}_score"] is None for x in _spec().buckets), t
    eligible = {f"F{i}" for i in range(1, 7)} | {"NA", "LS"}
    assert {t for t, _ in ind} == eligible                   # 탈락 종목은 지표 행도 없다
    assert set(sc) == eligible | set(want)
    # 백분위 단면 = 적격 8종목(탈락 종목은 들어가지 않는다)
    vols = sorted(ind[t, "VOL60"]["pct"] for t in eligible)
    assert vols == [_pct(k, len(eligible)) for k in range(1, len(eligible) + 1)]
    for key in (*SCORE_KEYS, *DISPLAY_KEYS):
        assert ind["NA", key]["flag"].endswith("적격미확인(admin);적격미확인(filing_late)"), key
    assert ind["LS", "EP"]["raw"] < 0 and ind["LS", "EP"]["pct"] == 0.0
    assert sc["LS"]["exclude_reason"] != "insufficient_data"
    # 규칙은 spec 에서 읽는다 — 적격성 조건을 빼면 전원 적격
    loose = replace(_spec(), universe=replace(_spec().universe, min_adv20=None, exclude=()))
    sc_loose, _ = _run(b, loose)
    assert not any(r["exclude_reason"] in want.values() for r in sc_loose.values())
    assert len(sc_loose) == len(sc)


def test_output_shape_every_ticker_times_indicator() -> None:
    b = Board()
    for i in range(1, 9):
        _full(b, f"F{i}", i, sector="G10" if i <= 5 else "G20")
    spec = _spec()
    res = ENGINE.run(spec, b.fi())
    assert all(tuple(r) == score_columns(spec) for r in res.scores)
    assert all(tuple(r) == INDICATOR_COLUMNS for r in res.indicators)
    assert len(res.indicators) == len(res.scores) * len(spec.indicators) == 8 * 18
    role = {i.key: i.role for i in spec.indicators}
    for r in res.indicators:
        assert r["score_date"] == D and r["spec_id"] == SPEC_ID
        assert r["role"] == role[r["key"]]
        if r["role"] == "display":
            assert r["pct"] is None, r
        else:
            assert (r["pct"] is None) == (r["raw"] is None), r
        if r["raw"] is None:
            assert r["flag"], r              # 결측이면 사유가 반드시 있다
    s = res.scores[0]
    assert s["spec_id"] == SPEC_ID and s["score_date"] == D and s["sector_l1"] in ("G10", "G20")
    assert s["sector_l2"] == f"{s['sector_l1']}10" and s["coverage_state"] == "fresh"
    # 행 순서: 순위 1…N, 제외 종목은 뒤
    ranks = [r["rank"] for r in res.scores]
    n_ranked = sum(r is not None for r in ranks)
    assert ranks[:n_ranked] == list(range(1, n_ranked + 1))
    assert all(r is None for r in ranks[n_ranked:])


def test_deterministic_and_row_order_independent() -> None:
    b = Board()
    for i in range(1, 13):
        _full(b, f"T{i:02d}", i, sector=("G10", "G20", "G30")[i % 3])
    fi = b.fi()
    spec = _spec()
    first = ENGINE.run(spec, fi)
    again = ENGINE.run(spec, fi)
    rng = random.Random(20260928)
    shuffled = {}
    for name, rows in fi.tables.items():
        rows = list(rows)
        rng.shuffle(rows)
        shuffled[name] = rows
    mixed = ENGINE.run(spec, FactorInputs(fi.date, fi.basis, fi.build_id, shuffled))
    assert first == again == mixed
    # 동일가중 판도 같은 입력에서 돈다(버킷 가중만 다름 → 버킷 점수는 같다)
    eq = {r["ticker"]: r for r in ENGINE.run(_spec("v4_rank@0.2"), fi).scores}
    for r in first.scores:
        assert eq[r["ticker"]]["value_score"] == r["value_score"]
