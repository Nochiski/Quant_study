"""T2.5 모델 판 빌드 — `python -m model build`(플랜 `2026-09-24-v3-merge.md` M2 W2-a).

factor_inputs 판(합성 트리)을 읽어 레지스트리 spec 을 돌리고 판·MANIFEST·판 manifest·latest 포인터와
게이트 MG0~MG5 를 본다. 입력 트리는 셋이다.
  - 보드 트리: `test_model_v4_rank.Board`·`_full` 로 만든 40종목(네 spec 이 전부 돈다, 적은 수라
    MG4·MG1(v4) 하한은 테스트에서 낮춘다)
  - 골든 트리: `tests/tools/compat_to_fi` 가 09-28 골든 compat 표를 옮긴 판(v3·v2 원본 점수와 대조)
  - 실제 factor_inputs 판: `test_factor_inputs.make_roots` 합성 equity/stage → `factor_inputs.build`
게이트 FAIL 은 엔진을 감싸 한 가지씩 망가뜨려(monkeypatch) 확인한다.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import pytest
from model import build as mbuild
from model import gates as mgates
from model import registry
from model.__main__ import main as cli_main
from model.build import ModelBuildError, build
from model.contracts import FI_TABLES, INDICATOR_COLUMNS, EngineResult, FactorInputs, score_columns
from model.engines import ENGINES
from stage import manifest
from test_model_v4_rank import Board, _full

_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
# tests/tools/ 는 패키지가 아니라 위 sys.path 주입 뒤에야 import 된다.
import compat_to_fi  # noqa: E402

D = "2026-09-28"
D_S = "20260928"
FI_BID = "m_20260929T000500_000000Z"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "model_golden" / "2026-09-28"
ALL_SPECS = ("scope@1.0", "v2_percentrank@1.0", "v3_zscore@1.0", "v4_rank@0.1", "v4_rank@0.2")
V3, V2, V4 = "v3_zscore@1.0", "v2_percentrank@1.0", "v4_rank@0.1"
N_BOARD = 40
# 보드 트리는 40종목이라 서버 하한(D 가격 2,000 · v4 순위 100)을 그대로 두면 MG4·MG1 이 FAIL 한다
SMALL: dict[str, Any] = {"min_prices_on_d": 10, "min_ranked": 10}


# ── 합성 factor_inputs 트리 ──────────────────────────────────────────────────
def write_fi_tree(root: Path, fi: FactorInputs, build_id: str = FI_BID, *, basis: str = "morning",
                  date: str = D, latest: bool = True) -> Path:
    """FactorInputs → factor_inputs 판 규약(`<표>/v=<id>/part0.parquet` + `_meta.json` +
    `latest_<basis>.json`). 표마다 계약 dtype 으로 쓴다."""
    for name, t in FI_TABLES.items():
        out = root / name / f"v={build_id}" / "part0.parquet"
        mbuild.write_parquet(list(fi.tables.get(name, ())),
                             {c.name: c.dtype for c in t.columns}, out)
        (out.parent / "_meta.json").write_text(json.dumps(
            {"table": name, "build_id": build_id, "basis": basis, "date": date}))
    if latest:
        (root / f"latest_{basis}.json").write_text(json.dumps(
            {"layer": "factor_inputs", "status": "ok", "build_id": build_id, "date": date,
             "basis": basis}))
    return root


def board_fi(n: int = N_BOARD) -> FactorInputs:
    """점수 지표가 다 나오는 n 종목. fi_prices.close 는 계약이 BIGINT 라 정수로 둔다."""
    b = Board()
    for i in range(n):
        _full(b, f"{100000 + i:06d}", i, sector=("G10", "G20", "G30")[i % 3])
    for r in b.tables["fi_prices"]:
        r["close"] = round(float(r["close"]))           # type: ignore[arg-type]
    return b.fi()


@pytest.fixture(scope="module")
def board_tree(tmp_path_factory) -> Path:
    return write_fi_tree(tmp_path_factory.mktemp("fi_board") / "factor_inputs", board_fi())


@pytest.fixture(scope="module")
def golden_fi() -> FactorInputs:
    return compat_to_fi.load_golden(GOLDEN)


@pytest.fixture(scope="module")
def golden_tree(tmp_path_factory, golden_fi) -> Path:
    return write_fi_tree(tmp_path_factory.mktemp("fi_golden") / "factor_inputs", golden_fi)


@pytest.fixture(scope="module")
def built(board_tree, tmp_path_factory):
    root = tmp_path_factory.mktemp("model_board") / "model"
    return root, build(D_S, "morning", root, board_tree, **SMALL)


def rows(path: Path) -> list[dict[str, object]]:
    rel = duckdb.sql(f"SELECT * FROM read_parquet('{path}', hive_partitioning=false)")
    return [dict(zip(rel.columns, t, strict=True)) for t in rel.fetchall()]


def describe(path: Path) -> list[tuple[str, str]]:
    return [(str(r[0]), str(r[1])) for r in duckdb.sql(
        f"DESCRIBE SELECT * FROM read_parquet('{path}', hive_partitioning=false)").fetchall()]


def gate(res, spec_id: str, name: str) -> Any:
    return res.specs[spec_id]["gates"][name]


class Wrap:
    """엔진 감싸기 — 결과를 `fn` 으로 망가뜨린다(게이트 FAIL 경로)."""

    def __init__(self, base, fn: Callable[[EngineResult], EngineResult]) -> None:
        self.base, self.fn, self.name = base, fn, base.name

    def output_columns(self, spec):
        return self.base.output_columns(spec)

    def run(self, spec, inputs):
        return self.fn(self.base.run(spec, inputs))


def patch_engine(monkeypatch, engine: str, fn: Callable[[EngineResult], EngineResult]) -> None:
    monkeypatch.setitem(ENGINES, engine, Wrap(ENGINES[engine], fn))


def edit_scores(fn: Callable[[list[dict[str, object]]], None]):
    def apply(r: EngineResult) -> EngineResult:
        scores = [dict(x) for x in r.scores]
        fn(scores)
        return EngineResult(scores, [dict(x) for x in r.indicators])
    return apply


# ── 판 규약 · 산출 ────────────────────────────────────────────────────────────
def test_build_writes_every_spec_under_one_build_id(built) -> None:
    root, res = built
    assert res.ok and res.status == "ok"
    assert res.build_id.startswith("m_") and res.fi_build_id == FI_BID
    assert tuple(res.specs) == ALL_SPECS
    for spec in registry.all_specs():
        vdir = root / spec.spec_id / f"v={res.build_id}"
        assert sorted(p.name for p in vdir.iterdir()) == ["indicators.parquet", "scores.parquet"]
        want_s = mgates.score_dtypes(spec)
        assert describe(vdir / "scores.parquet") == list(want_s.items())
        assert tuple(want_s) == score_columns(spec)
        assert describe(vdir / "indicators.parquet") == list(mgates.INDICATOR_DTYPES.items())
        assert tuple(mgates.INDICATOR_DTYPES) == INDICATOR_COLUMNS
    assert not (root / "_tmp").exists() or not any((root / "_tmp").iterdir())
    assert not (root / "_failed").exists()


def test_parquet_equals_engine_run_on_the_same_inputs(built) -> None:
    root, res = built
    fi = board_fi()
    for spec in registry.all_specs():
        want = ENGINES[spec.engine].run(spec, fi)
        vdir = root / spec.spec_id / f"v={res.build_id}"
        assert rows(vdir / "scores.parquet") == want.scores
        assert rows(vdir / "indicators.parquet") == want.indicators
        n = res.specs[spec.spec_id]
        assert n["n_scores"] == len(want.scores)
        assert n["n_ranked"] == sum(r["rank"] is not None for r in want.scores)
        assert n["n_ranked"] + n["n_excluded"] == n["n_scores"]


def test_v4_has_exclusions_and_indicators(built) -> None:
    _, res = built
    v4 = res.specs[V4]
    assert v4["n_excluded"] > 0 and v4["n_ranked"] >= SMALL["min_ranked"]
    assert res.specs[V3]["n_excluded"] == 0 == res.specs[V2]["n_excluded"]


def test_spec_manifest_uses_stage_manifest_machinery(built) -> None:
    root, res = built
    for spec_id in ALL_SPECS:
        m = manifest.load(root / spec_id / "MANIFEST.json")
        assert m.table == spec_id and m.current_build == res.build_id
        rec = m.builds[-1]
        assert rec.build_id == res.build_id and rec.basis == "morning"
        assert rec.inputs == {"factor_inputs": FI_BID}
        assert rec.n_rows == res.specs[spec_id]["n_scores"]
        assert rec.partitions[0]["path"] == f"v={res.build_id}"
        assert [g["name"] for g in rec.gates] == list(mgates.GATE_ORDER)


def test_run_manifest_and_latest_pointer(built) -> None:
    root, res = built
    run = json.loads((root / "_runs" / f"{D_S}_morning.json").read_text())
    latest = json.loads((root / "latest_morning.json").read_text())
    assert run == latest
    assert set(run) == {"layer", "status", "build_id", "date", "basis", "fi_build_id",
                        "generated_at", "specs", "excluded_specs", "primary_spec", "elapsed_s"}
    assert run["layer"] == "model" and run["status"] == "ok" and run["excluded_specs"] == {}
    assert (run["build_id"], run["date"], run["basis"], run["fi_build_id"]) == (
        res.build_id, D, "morning", FI_BID)
    assert run["primary_spec"] == "scope@1.0"          # 기본 주 모델(10-05 v4_rank@0.1 → scope@1.0)
    assert tuple(run["specs"]) == ALL_SPECS
    for spec_id, s in run["specs"].items():
        assert set(s) == {"n_scores", "n_ranked", "n_excluded", "gates"}
        assert tuple(s["gates"]) == mgates.GATE_ORDER
        assert {g["status"] for g in s["gates"].values()} <= {"pass", "skip"}, spec_id
        assert s["gates"]["MG5"]["status"] == "skip"          # 첫 판 — 전판이 없다
        assert s["gates"]["MG4"]["metrics"]["n_prices_on_d"] == N_BOARD


# ── 골든 · 실제 factor_inputs 판 ─────────────────────────────────────────────
def test_golden_tree_reproduces_the_ported_engines(golden_tree, golden_fi, tmp_path) -> None:
    res = build(D_S, "morning", tmp_path / "model", golden_tree, specs=[V3, V2], primary=V3,
                min_prices_on_d=500)
    assert res.ok, res.specs
    for spec_id, n, golden in ((V3, 579, "score_history"), (V2, 619, "score_history_v2")):
        spec = registry.get(spec_id)
        got = rows(tmp_path / "model" / spec_id / f"v={res.build_id}" / "scores.parquet")
        assert got == ENGINES[spec.engine].run(spec, golden_fi).scores
        orig = {r["stock_code"]: r["rank"]
                for r in compat_to_fi.read_parquet_rows(GOLDEN / f"{golden}.parquet")}
        assert {r["stock_code"]: r["rank"] for r in got} == orig
        mg1 = gate(res, spec_id, "MG1")["metrics"]
        assert (mg1["n_scores"], mg1["n_eligible"], mg1["coverage"]) == (n, n, 1.0)
    assert set(res.specs) == {V3, V2}
    assert not (tmp_path / "model" / V4).exists()


def test_real_factor_inputs_build_feeds_the_model(tmp_path) -> None:
    from factor_inputs import build as fi_build
    from test_factor_inputs import make_roots

    eq, st = make_roots(tmp_path / "src")
    fi = fi_build(D_S, "morning", tmp_path / "fi", st, eq, min_eligible=5, golden_path=None)
    assert fi.ok
    res = build(D_S, "morning", tmp_path / "model", tmp_path / "fi", specs=[V2], primary=V2,
                min_prices_on_d=1)
    assert res.ok, res.specs
    assert res.fi_build_id == fi.build_id
    assert res.specs[V2]["n_scores"] == fi.coverage["n_eligible"]


# ── 게이트 FAIL · 판 유지 ────────────────────────────────────────────────────
def test_gate_failure_keeps_previous_latest(board_tree, tmp_path) -> None:
    root = tmp_path / "model"
    first = build(D_S, "morning", root, board_tree, **SMALL)
    before = (root / "latest_morning.json").read_text()
    bad = build(D_S, "morning", root, board_tree, min_prices_on_d=10**6,
                min_ranked=SMALL["min_ranked"])
    assert not bad.ok and bad.status == "gate_failed"
    assert (root / "latest_morning.json").read_text() == before
    assert bad.failed_report == root / "_failed" / f"{bad.build_id}.json"
    report = json.loads((root / "_failed" / f"{bad.build_id}.json").read_text())
    assert report["status"] == "gate_failed" and report["build_id"] == bad.build_id
    assert report["specs"][V3]["gates"]["MG4"]["status"] == "fail"
    run = json.loads((root / "_runs" / f"{D_S}_morning.json").read_text())
    assert run["status"] == "ok" and run["build_id"] == first.build_id    # 같은 날 성공 기록(D-09)
    for spec_id in ALL_SPECS:
        assert not (root / spec_id / f"v={bad.build_id}").exists()
        assert manifest.load(root / spec_id / "MANIFEST.json").current_build == first.build_id
    assert not (root / "_tmp").exists() or not any((root / "_tmp").iterdir())


def test_comparison_model_failure_is_isolated(board_tree, tmp_path, monkeypatch, capsys) -> None:
    """비교 모델(V2)만 FAIL 이면 그 spec 만 빼고 주 모델 판은 올린다(N-11 격리)."""
    patch_engine(monkeypatch, "v2_percentrank", edit_scores(lambda s: s[0].update(rank=None)))
    root = tmp_path / "model"
    res = build(D_S, "morning", root, board_tree, specs=[V2, V4], primary=V4, **SMALL)
    assert res.ok and res.excluded == (V2,)
    assert gate(res, V2, "MG3")["status"] == "fail"
    assert f"EXCLUDED={V2}" in res.summary()
    latest = json.loads((root / "latest_morning.json").read_text())
    assert latest["status"] == "ok" and latest["build_id"] == res.build_id
    assert set(latest["specs"]) == {V4} and set(latest["excluded_specs"]) == {V2}
    assert latest["excluded_specs"][V2]["gates"]["MG3"]["status"] == "fail"
    assert (root / V4 / f"v={res.build_id}" / "scores.parquet").exists()
    assert not (root / V2 / f"v={res.build_id}").exists()
    assert manifest.load(root / V2 / "MANIFEST.json").current_build is None
    assert not (root / "_failed").exists()
    rc = cli_main(["build", "--date", D_S, "--basis", "morning", "--fi-root", str(board_tree),
                   "--root", str(tmp_path / "cli"), "--specs", f"{V2},{V4}", "--primary", V4,
                   "--min-prices-on-d", "10", "--min-ranked", "10"])
    err = capsys.readouterr().err
    assert rc == 0 and f"{V2} MG3 FAIL" in err and "비교 모델 제외" in err


def test_primary_failure_publishes_nothing_even_if_others_pass(board_tree, tmp_path,
                                                              monkeypatch) -> None:
    """주 모델(V4)이 FAIL 이면 통과한 비교 모델(V2)도 올리지 않는다."""
    patch_engine(monkeypatch, "v4_rank", edit_scores(
        lambda s: next(r for r in s if r["rank"] == 1).update(composite=100.5)))
    root = tmp_path / "model"
    res = build(D_S, "morning", root, board_tree, specs=[V2, V4], primary=V4, **SMALL)
    assert not res.ok and res.status == "gate_failed" and res.excluded == ()
    assert not (root / "latest_morning.json").exists()
    assert not (root / V2 / f"v={res.build_id}").exists()
    run = json.loads((root / "_runs" / f"{D_S}_morning.json").read_text())
    assert run["status"] == "gate_failed" and set(run["specs"]) == {V2, V4}
    assert run["excluded_specs"] == {}


def test_failed_rerun_keeps_the_same_day_ok_run(board_tree, tmp_path, monkeypatch,
                                                capsys) -> None:
    """D-09: 같은 날 성공 판 뒤의 FAIL 재실행은 `_runs` 를 덮지 않는다(실패는 `_failed/` 에만).
    인계 `find_run` 이 그날 성공 판을 계속 찾는다. 성공 재실행은 지금처럼 덮는다."""
    from deliver.reader import find_run

    root = tmp_path / "model"
    run_path = root / "_runs" / f"{D_S}_morning.json"
    first = build(D_S, "morning", root, board_tree, specs=[V2, V4], primary=V4, **SMALL)
    assert first.ok
    with monkeypatch.context() as m:                     # 주 모델(V4) MG3 FAIL
        patch_engine(m, "v4_rank", edit_scores(
            lambda s: next(r for r in s if r["rank"] == 1).update(composite=100.5)))
        bad = build(D_S, "morning", root, board_tree, specs=[V2, V4], primary=V4, **SMALL)
        rc = cli_main(["build", "--date", D_S, "--basis", "morning", "--fi-root",
                       str(board_tree), "--root", str(root), "--specs", f"{V2},{V4}",
                       "--primary", V4, "--min-prices-on-d", "10", "--min-ranked", "10"])
    assert not bad.ok and bad.status == "gate_failed"
    report = json.loads((root / "_failed" / f"{bad.build_id}.json").read_text())
    assert report["status"] == "gate_failed" and report["build_id"] == bad.build_id
    run = json.loads(run_path.read_text())
    assert run["status"] == "ok" and run["build_id"] == first.build_id
    got = find_run(root, D, "morning")
    assert got is not None and got.build_id == first.build_id
    assert bad.run_manifest_kept
    assert rc == 1 and "같은 날 성공 기록" in capsys.readouterr().err
    again = build(D_S, "morning", root, board_tree, specs=[V2, V4], primary=V4, **SMALL)
    assert again.ok and json.loads(run_path.read_text())["build_id"] == again.build_id


@pytest.mark.parametrize("before", [None, b'{"status": "gate_failed"}', b'{"status": "ok", ',
                                    b'["ok"]', b"\xff\xfe"],
                         ids=["absent", "failed", "broken", "not_object", "not_utf8"])
def test_failed_build_writes_the_run_record_unless_the_day_is_ok(board_tree, tmp_path,
                                                                 before) -> None:
    """D-09 판정의 나머지 갈래 — 같은 날 기록이 없거나 · status ≠ ok 거나 · 내용이 깨졌으면
    (JSON · UTF-8 · 객체가 아님) FAIL 도 `_runs` 를 쓴다."""
    root = tmp_path / "model"
    run_path = root / "_runs" / f"{D_S}_morning.json"
    if before is not None:
        run_path.parent.mkdir(parents=True)
        run_path.write_bytes(before)
    bad = build(D_S, "morning", root, board_tree, specs=[V2], primary=V2,
                min_prices_on_d=10**6, min_ranked=SMALL["min_ranked"])
    run = json.loads(run_path.read_text())
    assert not bad.ok and run["status"] == "gate_failed" and run["build_id"] == bad.build_id
    assert not bad.run_manifest_kept


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 0 파일도 읽는다")
def test_unreadable_same_day_run_is_kept_and_raises(board_tree, tmp_path) -> None:
    """D-09: 같은 날 기록이 있는데 못 읽으면(권한 등) 덮지 않고 오류로 낸다 — 성공 기록이
    조용히 gate_failed 로 바뀌지 않는다. 실패 보고서는 그 전에 쓴다."""
    root = tmp_path / "model"
    assert build(D_S, "morning", root, board_tree, specs=[V2], primary=V2, **SMALL).ok
    run_path = root / "_runs" / f"{D_S}_morning.json"
    before = run_path.read_bytes()
    run_path.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            build(D_S, "morning", root, board_tree, specs=[V2], primary=V2,
                  min_prices_on_d=10**6, min_ranked=SMALL["min_ranked"])
    finally:
        run_path.chmod(0o644)
    assert run_path.read_bytes() == before
    assert [json.loads(p.read_text())["status"] for p in (root / "_failed").glob("*.json")] == [
        "gate_failed"]


def test_mg4_fails_below_two_thousand_prices_on_d(golden_tree, tmp_path) -> None:
    res = build(D_S, "morning", tmp_path / "model", golden_tree, specs=[V3], primary=V3)
    assert not res.ok
    g = gate(res, V3, "MG4")
    assert g["status"] == "fail" and g["metrics"]["n_prices_on_d"] == 618
    assert g["metrics"]["min_prices_on_d"] == mgates.MIN_PRICES_ON_D == 2000
    assert not (tmp_path / "model" / "latest_morning.json").exists()


def test_mg0_extra_column_fails_and_skips_downstream(board_tree, tmp_path, monkeypatch) -> None:
    def extra(s: list[dict[str, object]]) -> None:
        for r in s:
            r["extra"] = 1.0
    patch_engine(monkeypatch, "v3_zscore", edit_scores(extra))
    res = build(D_S, "morning", tmp_path / "model", board_tree, specs=[V3], primary=V3, **SMALL)
    g = res.specs[V3]["gates"]
    assert not res.ok and g["MG0"]["status"] == "fail"
    assert {g[n]["status"] for n in ("MG1", "MG2", "MG3", "MG5")} == {"skip"}
    assert g["MG4"]["status"] == "pass"


def test_mg0_mixed_types_fail(board_tree, tmp_path, monkeypatch) -> None:
    def bad(s: list[dict[str, object]]) -> None:
        s[0]["rank"] = "1"
        s[1]["composite"] = True
    patch_engine(monkeypatch, "v4_rank", edit_scores(bad))
    res = build(D_S, "morning", tmp_path / "model", board_tree, specs=[V4], primary=V4, **SMALL)
    g = gate(res, V4, "MG0")
    assert g["status"] == "fail"
    assert g["metrics"]["type_violations"] == {"scores.rank": 1, "scores.composite": 1}


def test_mg1_v3_coverage_below_95pct_fails(board_tree, tmp_path, monkeypatch) -> None:
    patch_engine(monkeypatch, "v3_zscore", edit_scores(lambda s: s.__delitem__(slice(0, 3))))
    res = build(D_S, "morning", tmp_path / "model", board_tree, specs=[V3], primary=V3, **SMALL)
    g = gate(res, V3, "MG1")
    assert g["status"] == "fail"
    assert (g["metrics"]["n_scores"], g["metrics"]["n_eligible"]) == (N_BOARD - 3, N_BOARD)
    assert g["metrics"]["coverage"] == pytest.approx((N_BOARD - 3) / N_BOARD)


def test_mg1_v3_universe_applies_min_market_cap(tmp_path) -> None:
    fi = board_fi()
    for r in fi.tables["fi_universe"][:5]:
        r["market_cap"] = 999.0                       # type: ignore[index]
    tree = write_fi_tree(tmp_path / "fi", fi)
    res = build(D_S, "morning", tmp_path / "model", tree, specs=[V3, V2], primary=V3, **SMALL)
    assert res.ok
    assert gate(res, V3, "MG1")["metrics"]["n_eligible"] == N_BOARD - 5
    assert gate(res, V2, "MG1")["metrics"]["n_eligible"] == N_BOARD


def test_mg1_v4_needs_one_hundred_ranked(board_tree, tmp_path) -> None:
    res = build(D_S, "morning", tmp_path / "model", board_tree, specs=[V4], primary=V4,
                min_prices_on_d=SMALL["min_prices_on_d"])
    g = gate(res, V4, "MG1")
    assert g["status"] == "fail" and g["metrics"]["min_ranked"] == mgates.MIN_RANKED == 100
    assert g["metrics"]["n_ranked"] == res.specs[V4]["n_ranked"] < 100


def test_mg2_nondeterministic_engine_fails(board_tree, tmp_path, monkeypatch) -> None:
    calls = {"n": 0}

    def drift(s: list[dict[str, object]]) -> None:
        calls["n"] += 1
        s[0]["r1m"] = float(calls["n"])
    patch_engine(monkeypatch, "v2_percentrank", edit_scores(drift))
    res = build(D_S, "morning", tmp_path / "model", board_tree, specs=[V2], primary=V2, **SMALL)
    g = gate(res, V2, "MG2")
    assert g["status"] == "fail"
    assert g["metrics"]["scores_sha256"] != g["metrics"]["rerun_scores_sha256"]


@pytest.mark.parametrize(("engine", "spec_id", "fn", "key"), [
    ("v3_zscore", V3, lambda s: s[0].update(r1m=math.nan), "n_nonfinite"),
    ("v3_zscore", V3, lambda s: s[1].update(rank=1), "rank_not_1_to_n"),
    ("v3_zscore", V3, lambda s: s[0].update(growth_score=0.5), "v3_always_null_filled"),
    ("v2_percentrank", V2, lambda s: s[0].update(rank=None), "unranked_rows"),
    ("v4_rank", V4, lambda s: next(r for r in s if r["rank"] == 1).update(composite=100.5),
     "score_out_of_range"),
    ("v4_rank", V4, lambda s: next(r for r in s if r["excluded"]).update(rank=999),
     "excluded_with_rank"),
])
def test_mg3_sanity_failures(board_tree, tmp_path, monkeypatch, engine, spec_id, fn, key) -> None:
    patch_engine(monkeypatch, engine, edit_scores(fn))
    res = build(D_S, "morning", tmp_path / "model", board_tree, specs=[spec_id],
                primary=spec_id, **SMALL)
    g = gate(res, spec_id, "MG3")
    assert g["status"] == "fail" and g["metrics"][key] >= 1, g


# ── MG5 전판 대비 ────────────────────────────────────────────────────────────
def test_mg5_records_against_previous_build_and_warns(board_tree, tmp_path, monkeypatch) -> None:
    root = tmp_path / "model"
    first = build(D_S, "morning", root, board_tree, specs=[V2, V4], primary=V4, **SMALL)
    second = build(D_S, "morning", root, board_tree, specs=[V2, V4], primary=V4, **SMALL)
    for spec_id in (V2, V4):
        g = gate(second, spec_id, "MG5")
        assert g["status"] == "pass"
        m = g["metrics"]
        assert m["prev_build_id"] == first.build_id and m["spearman"] == pytest.approx(1.0)
        assert m["top30_overlap"] == min(30, second.specs[spec_id]["n_ranked"])
        assert m["warn"] is False

    def reverse(s: list[dict[str, object]]) -> None:
        for r in s:
            r["total_score"] = -float(r["total_score"])        # type: ignore[arg-type]
        s.sort(key=lambda r: (-float(r["total_score"]), str(r["stock_code"])))  # type: ignore
        for k, r in enumerate(s, start=1):
            r["rank"] = k
    patch_engine(monkeypatch, "v2_percentrank", edit_scores(reverse))
    third = build(D_S, "morning", root, board_tree, specs=[V2, V4], primary=V4, **SMALL)
    g = gate(third, V2, "MG5")
    assert third.ok and g["status"] == "warn"
    assert g["metrics"]["spearman"] < mgates.SPEARMAN_WARN and g["metrics"]["warn"] is True
    latest = json.loads((root / "latest_morning.json").read_text())
    assert latest["build_id"] == third.build_id
    assert latest["specs"][V2]["gates"]["MG5"]["status"] == "warn"


def test_keep_default_holds_three_months_of_runs() -> None:
    """엑셀 Δ순위 1W·1M·순위 흐름이 한 달 전 판까지 연다 — 3 이면 같은 날 재빌드에
    전날 판이 지워졌다."""
    assert mbuild.KEEP_DEFAULT == 60


def test_keep_prunes_old_versions(board_tree, tmp_path) -> None:
    root = tmp_path / "model"
    a = build(D_S, "morning", root, board_tree, specs=[V2], primary=V2, keep=1, **SMALL)
    b = build(D_S, "morning", root, board_tree, specs=[V2], primary=V2, keep=1, **SMALL)
    assert not (root / V2 / f"v={a.build_id}").exists()
    assert (root / V2 / f"v={b.build_id}" / "scores.parquet").exists()
    assert gate(b, V2, "MG5")["metrics"]["prev_build_id"] == a.build_id


# ── 입력 판 · spec 선택 · primary ────────────────────────────────────────────
def test_spec_selection_and_primary(board_tree, tmp_path) -> None:
    res = build(D_S, "morning", tmp_path / "model", board_tree, specs=[V3], primary=V3, **SMALL)
    assert tuple(res.specs) == (V3,) and res.primary_spec == V3
    assert {p.name for p in (tmp_path / "model").iterdir()} - {"_tmp"} == {
        "_runs", "latest_morning.json", V3}
    with pytest.raises(ModelBuildError, match="primary"):
        build(D_S, "morning", tmp_path / "m2", board_tree, specs=[V3], **SMALL)
    with pytest.raises(ModelBuildError, match="nope@9"):
        build(D_S, "morning", tmp_path / "m3", board_tree, specs=["nope@9"], primary="nope@9")


def test_fi_build_resolution_refuses_mismatches(board_tree, tmp_path) -> None:
    with pytest.raises(ModelBuildError, match="date"):
        build("20260929", "morning", tmp_path / "m", board_tree)
    with pytest.raises(ModelBuildError, match="latest_evening"):
        build(D_S, "evening", tmp_path / "m", board_tree)
    other = "m_20260929T010000_000000Z"
    tree = write_fi_tree(tmp_path / "fi", board_fi(), other, basis="evening", latest=False)
    with pytest.raises(ModelBuildError, match="basis"):
        build(D_S, "morning", tmp_path / "m", tree, fi_build=other)
    with pytest.raises(ModelBuildError, match="없다"):
        build(D_S, "morning", tmp_path / "m", board_tree, fi_build="m_nope")
    with pytest.raises(ModelBuildError, match="YYYYMMDD"):
        build("2026-09-28", "morning", tmp_path / "m", board_tree)


def test_explicit_fi_build_id(board_tree, tmp_path) -> None:
    other = "m_20260929T020000_000000Z"
    fi = board_fi()
    tree = write_fi_tree(tmp_path / "fi", fi, FI_BID)
    write_fi_tree(tree, replace(fi, build_id=other), other, latest=False)
    res = build(D_S, "morning", tmp_path / "model", tree, fi_build=other, specs=[V2],
                primary=V2, **SMALL)
    assert res.ok and res.fi_build_id == other


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_cli_return_codes(board_tree, golden_tree, tmp_path, capsys) -> None:
    common = ["build", "--date", D_S, "--basis", "morning", "--fi-root"]
    ok = cli_main([*common, str(board_tree), "--root", str(tmp_path / "a"),
                   "--specs", f"{V2},{V4}", "--primary", V4,
                   "--min-prices-on-d", "10", "--min-ranked", "10"])
    assert ok == 0
    assert json.loads((tmp_path / "a" / "latest_morning.json").read_text())["primary_spec"] == V4
    fail = cli_main([*common, str(golden_tree), "--root", str(tmp_path / "b"),
                     "--specs", V3, "--primary", V3])
    assert fail == 1
    assert "MG4" in capsys.readouterr().err
    bad = cli_main([*common, str(board_tree), "--root", str(tmp_path / "c"),
                    "--specs", V3])                     # primary 기본 scope@1.0 이 선택 밖
    assert bad == 2


# ── 게이트 단위 ──────────────────────────────────────────────────────────────
def test_spearman_average_ranks_ties() -> None:
    assert mgates.spearman({"a": 1.0, "b": 2.0, "c": 3.0},
                           {"a": 3.0, "b": 2.0, "c": 1.0}) == pytest.approx(-1.0)
    assert mgates.spearman({"a": 1.0, "b": 1.0, "c": 2.0},
                           {"a": 5.0, "b": 5.0, "c": 9.0}) == pytest.approx(1.0)
    assert mgates.spearman({"a": 1.0}, {"a": 1.0}) is None
    assert mgates.spearman({"a": 1.0, "b": 1.0}, {"a": 1.0, "b": 2.0}) is None   # 분산 0


def test_score_dtypes_follow_the_v3_schema() -> None:
    d = mgates.score_dtypes(registry.get(V3))
    assert d["stock_code"] == d["score_date"] == d["op_1w_flag"] == "VARCHAR"
    assert d["rank"] == "BIGINT" and d["composite_score"] == d["growth_score"] == "DOUBLE"
    v4 = mgates.score_dtypes(registry.get(V4))
    assert (v4["excluded"], v4["n_buckets_used"], v4["low_risk_score"]) == (
        "BOOLEAN", "BIGINT", "DOUBLE")
