"""결과 조회 응답: 보유 기록만 수로 바꾸고 나머지는 저장 결과 그대로."""

from __future__ import annotations

from dataclasses import fields

from strategy_workbench.adapters.inbound.http_api._backtest_result_contract import (
    BacktestResult,
    BacktestResultArtifacts,
    backtest_result,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult, RawArtifactBundle
from tests.test_backtest_artifact_store import _result


def _shape(model: type) -> dict[str, tuple[object, object]]:
    return {item.name: (item.type, item.default) for item in fields(model)}


def test_the_result_response_has_every_stored_field_with_positions_counted() -> None:
    """저장 결과의 칸이 늘거나 타입·선택 여부가 바뀌면 응답 모양도 따라 바꾸게 한다 — 그러지 않으면
    응답에서 칸이 조용히 빠지거나 생성 SDK 타입이 실제 응답과 어긋난다."""
    assert _shape(BacktestResult) == {
        **_shape(BacktestRunResult),
        "artifacts": _shape(BacktestResult)["artifacts"],
    }
    stored = _shape(RawArtifactBundle)
    response = _shape(BacktestResultArtifacts)
    assert set(response) == set(stored) - {"positions"} | {"position_count"}
    assert {name: response[name] for name in stored if name != "positions"} == {
        name: shape for name, shape in stored.items() if name != "positions"
    }


def test_the_result_response_counts_positions_and_keeps_every_other_value() -> None:
    stored = _result()

    response = backtest_result(stored)

    assert response.artifacts.position_count == len(stored.artifacts.positions) == 1
    for item in fields(stored.artifacts):
        if item.name != "positions":
            assert getattr(response.artifacts, item.name) == getattr(stored.artifacts, item.name)
    assert (
        response.manifest,
        response.metric_definitions,
        response.metrics,
        response.series,
    ) == (stored.manifest, stored.metric_definitions, stored.metrics, stored.series)
