"""Tier 1 대조(columnar-tape spec 5절). 지금 엔진은 객체 경로 하나라 자기 일관성을 본다.

같은 사례를 엔진 A·B 로 돌려 L0~L3 결과를 비트 대조한다. 열 경로가 들어오면 `_ENGINE_B` 만 바꾼다.
Tier 1-b 는 해시 seed 가 다른 하위 프로세스에서 L3 `tape_hash` 가 같은지 본다(T0-02 회귀).
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.application.portfolio_design.facade.design import PortfolioPipelineResult
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from strategy_workbench.domain.factor.facade.evaluation import FactorFieldValue, FactorObservation
from strategy_workbench.domain.portfolio.facade.construction import (
    ExclusionReason,
    TargetTapeTraceResult,
)
from strategy_workbench.domain.strategy.facade.specification import (
    EligibilityOperator,
    FactorDirection,
    RebalanceFrequency,
    StrategySpec,
    WeightingMethod,
)

from ._compare import ParityReport, compare, format_report
from ._corpus import (
    MOCK_WINDOW,
    PipelineCase,
    adapter_cases,
    compiler_cases,
    evaluator_cases,
    factor_panel,
    pipeline_cases,
    portfolio_panel,
    synthetic_raw_panel,
)
from ._engines import ENGINES, Failure, TapeEngine, pipeline_source

_ENGINE_A = ENGINES["object"]
_ENGINE_B = ENGINES["object"]


def _assert_parity(report: ParityReport) -> None:
    assert report.ok, format_report(report, values=True)


def test_corpus_generation_is_deterministic() -> None:
    raw = synthetic_raw_panel(11, ("x", "y"), group="sector", extreme="x")
    pairs = [
        ("L1", evaluator_cases(), evaluator_cases()),
        ("L2", compiler_cases(), compiler_cases()),
        ("L3", pipeline_cases(), pipeline_cases()),
        # 필드 순서를 바꿔도 셀은 같다(질의와 무관한 난수열).
        ("raw", raw, synthetic_raw_panel(11, ("x", "y"), group="sector", extreme="x")),
    ]
    _assert_parity(compare("corpus", pairs))
    reordered = synthetic_raw_panel(11, ("y", "x"), group="sector", extreme="x")
    assert [{f.field_id: f for f in row.fields} for row in raw] == [
        {f.field_id: f for f in row.fields} for row in reordered
    ]
    assert factor_panel(11) != factor_panel(23)


def test_synthetic_panels_carry_every_required_edge() -> None:
    """합성 패널이 spec 5절이 요구한 성질을 모두 갖는다. 말뭉치가 줄어도 조용히 통과하지 않게."""
    rows = factor_panel(11)
    by_security: dict[str, list[FactorObservation]] = {}
    for row in rows:
        by_security.setdefault(row.security_id, []).append(row)

    def cell(row: FactorObservation, field_id: str) -> FactorFieldValue | None:
        return next((field for field in row.fields if field.field_id == field_id), None)

    cells = [field for row in rows for field in row.fields]
    xs = [item.value for item in cells if item.field_id == "x" and item.value is not None]
    ys = [item.value for item in cells if item.field_id == "y" and item.value is not None]
    floats = [x for x in xs if type(x) is float]
    s00 = [getattr(cell(row, "x"), "value", None) for row in by_security["S00"]]
    edges = {
        "행 공백": len(by_security["S05"]) < len(by_security["S00"]),
        "상장": by_security["S06"][0].as_of > rows[0].as_of,
        "폐지": by_security["S07"][-1].as_of < rows[-1].as_of,
        "비구성원 변동": {row.universe_member for row in by_security["S09"]} == {True, False},
        "가린 셀": any(item.masked for item in cells),
        "생략": any(cell(row, "x") is None for row in rows),
        "값 None": any(item.value is None and not item.masked for item in cells),
        "결측 연속": all(
            (found := cell(row, "x")) is not None and found.value is None
            for row in by_security["S04"][10:17]
        ),
        "동점": len(xs) > len(set(xs)),
        "+0": any(x == 0 and math.copysign(1, x) > 0 for x in floats),
        "-0": any(x == 0 and math.copysign(1, x) < 0 for x in floats),
        "0 나누기": 0 in ys,
        "극단 큰 값": any(abs(x) > 1e300 for x in floats),
        "극단 작은 값": any(0 < abs(x) < 1e-290 for x in floats),
        "정수 값": any(type(x) is int for x in xs),
        "bool·str 숫자 입력": {bool, str} <= {type(x) for x in xs},
        "상수 창": any(
            s00[i] is not None and s00[i] == s00[i + 1] == s00[i + 2] for i in range(len(s00) - 2)
        ),
        "그룹 결측": any(
            not isinstance(getattr(cell(row, "sector"), "value", None), str) for row in rows
        ),
    }
    portfolio = portfolio_panel(11)
    edges["늦은 공개"] = any(
        value.available_date > row.as_of for row in portfolio for value in row.factor_values
    )
    edges["bool 필드"] = any(field.value is True for row in portfolio for field in row.fields)
    edges["섹터 모름"] = any(row.sector_id is None for row in portfolio)
    assert [name for name, present in edges.items() if not present] == []


def test_l1_evaluator_node_caches_match() -> None:
    cases = evaluator_cases()
    results = [(case.key, _ENGINE_A.nodes(case), _ENGINE_B.nodes(case)) for case in cases]
    _assert_parity(compare("L1", results))
    # 넘침 그래프는 오류도 결과로 대조한다(node_id·as_of·security_id·value).
    assert any(isinstance(expected, Failure) for _, expected, _ in results)


def test_combinations_reach_every_level() -> None:
    """조합 열이 행 번호에 묶여 수준 하나를 놓치지 않는다(필드 역가중·분기·보조 방향 low 등)."""
    combos: list[list[StrategySpec]] = [
        [case.spec for case in compiler_cases() if "/combo=" in case.key],
        [case.spec for case in pipeline_cases() if case.key.startswith("L3/synthetic/combo=")],
    ]
    for specs in combos:
        rules = {rule.operator for spec in specs for rule in spec.eligibility.rules}
        assert rules == set(EligibilityOperator)
        assert {spec.portfolio.rebalance for spec in specs} == set(RebalanceFrequency)
        assert {spec.portfolio.weighting for spec in specs} == set(WeightingMethod)
        risk = [spec.risk for spec in specs if spec.portfolio.weighting is WeightingMethod.RISK]
        assert any(item.risk_field_id is not None for item in risk)
        assert any(item.risk_factor_id is not None for item in risk)
        assert any(
            spec.portfolio.tie_breaker_direction is FactorDirection.LOW
            and spec.portfolio.tie_breaker_factor_id is not None
            for spec in specs
        )


def test_l2_compiler_outputs_match() -> None:
    cases = compiler_cases()
    results = [(case, _ENGINE_A.compile(case), _ENGINE_B.compile(case)) for case in cases]
    _assert_parity(compare("L2", [(case.key, a, b) for case, a, b in results]))
    failures = {case.key: a.error_type for case, a, _ in results if isinstance(a, Failure)}
    assert failures == {"L2/negative/overflow": "NonFinitePortfolioCalculationError"}
    compiled = [(case, a.tape) for case, a, _ in results if isinstance(a, TargetTapeTraceResult)]
    reasons = [
        reason
        for _, tape in compiled
        for frame in tape.frames
        for candidate in frame.candidates
        for reason in candidate.exclusion_reasons
    ]
    # 최소 거래 조건(측 호환·상한 안·문턱)이 실제로 판정을 가른다.
    assert reasons.count(ExclusionReason.MINIMUM_TRADE) >= 5
    quarterly = [
        tape
        for case, tape in compiled
        if case.spec.portfolio.rebalance is RebalanceFrequency.QUARTERLY
    ]
    assert quarterly and all(tape.frames for tape in quarterly)


def _pipeline(engine: TapeEngine, case: PipelineCase) -> object:
    return engine.pipeline(case, *pipeline_source(case.source))


def test_l3_pipeline_results_match_and_negative_cases_fail_in_gate_order() -> None:
    cases = pipeline_cases()
    results = {case.key: (_pipeline(_ENGINE_A, case), _pipeline(_ENGINE_B, case)) for case in cases}
    _assert_parity(compare("L3", [(key, a, b) for key, (a, b) in results.items()]))
    failures = {key: a.error_type for key, (a, _) in results.items() if isinstance(a, Failure)}
    assert failures == {
        "L3/negative/overflow": "InvalidPortfolioRequestError",
        "L3/negative/lookahead": "LookAheadViolationError",
        "L3/negative/snapshot": "PortfolioSnapshotMismatchError",
        "L3/negative/outside-range": "RawObservationContractError",
        "L3/negative/trace-scope": "InvalidPortfolioTraceSelectionError",
    }
    # 시작 보유가 첫 프레임 시드를 실제로 바꾼다(없는 종목은 0.0, 관측에 없는 종목은 무시).
    held_key = next(key for key in results if key.endswith("/holdings"))
    held, plain = results[held_key][0], results[held_key.removesuffix("/holdings")][0]
    assert isinstance(held, PortfolioPipelineResult)
    assert isinstance(plain, PortfolioPipelineResult)
    assert held.preview.tape.tape_hash != plain.preview.tape.tape_hash


def test_object_path_is_window_invariant() -> None:
    """질의 시작을 늦춰도 겹치는 날짜의 원시 관측·팩터 값이 비트 단위로 같다(창 불변성)."""
    later = date(2023, 10, 2)
    pairs = []
    for case in (item for item in pipeline_cases() if item.key.startswith("L3/registry/")):
        short = replace(case, environment=replace(case.environment, start=later))
        full_result, short_result = _pipeline(_ENGINE_A, case), _pipeline(_ENGINE_B, short)
        assert isinstance(full_result, PortfolioPipelineResult)
        assert isinstance(short_result, PortfolioPipelineResult)
        pairs.append((case.key, _from(full_result, later), _from(short_result, later)))
    assert MOCK_WINDOW[0] < later
    _assert_parity(compare("L3-window", pairs))


def _from(result: PortfolioPipelineResult, start: date) -> tuple[object, ...]:
    raw = tuple(row for row in result.raw_observations if row.as_of >= start)
    values = tuple(
        value
        for record in result.factor_evaluations
        for value in record.values
        if value.as_of >= start
    )
    return raw, values


def tape_hashes() -> str:
    """L3 말뭉치의 사례별 tape_hash(실패면 예외 종류). Tier 1-b 하위 프로세스가 부른다."""
    digests = {}
    for case in pipeline_cases():
        result = _pipeline(_ENGINE_B, case)
        digests[case.key] = (
            result.preview.tape.tape_hash
            if isinstance(result, PortfolioPipelineResult)
            else repr(result)
        )
    return json.dumps(digests, sort_keys=True)


def test_l3_tape_hashes_do_not_depend_on_the_string_hash_seed() -> None:
    backend = Path(__file__).resolve().parents[2]
    code = "import tests.parity.test_parity as module; print(module.tape_hashes())"
    outputs = [
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=backend,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout
        for seed in ("0", "1")
    ]
    first, second = (json.loads(output) for output in outputs)
    assert first == second
    # 실패하지 않은 사례가 대부분이어야 해시 대조가 빈 채로 통과하지 않는다.
    assert sum(len(value) == 64 for value in first.values()) >= 40


def test_ledger_fixture_raw_and_pipeline_match(tmp_path: Path) -> None:
    """원장 손 픽스처(duckdb)로 L0 원시 관측과 L3 파이프라인을 대조한다."""
    pytest.importorskip("duckdb", reason="backend optional extra `equity`")
    pytest.importorskip("pyarrow", reason="backend optional extra `parquet`")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
    from tests.equity_fixture import build_workbench_root

    adapter = EquityDuckdbAdapter(build_workbench_root(tmp_path / "equity"))
    fields = tuple(profile.field_id for profile in adapter.list_fields())
    query = RawObservationQuery(
        "KRX", "krx.common-stock", date(2024, 1, 8), date(2024, 1, 12), fields, 4
    )
    _assert_parity(
        compare("L0", [("ledger", _ENGINE_A.raw(adapter, query), _ENGINE_B.raw(adapter, query))])
    )
    environment = RunEnvironment(
        start=date(2024, 1, 8), end=date(2024, 1, 12), universe_id="krx.common-stock"
    )
    cases = adapter_cases(adapter, environment, source="ledger")
    assert any(case.key.startswith("L3/registry/") for case in cases)
    results = [
        (
            case.key,
            _ENGINE_A.pipeline(case, adapter, adapter),
            _ENGINE_B.pipeline(case, adapter, adapter),
        )
        for case in cases
    ]
    _assert_parity(compare("L3", results))
