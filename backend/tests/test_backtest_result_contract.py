"""결과 조회 응답: 보유 기록만 수로 바꾸고 나머지는 저장 결과 그대로."""

from __future__ import annotations

from dataclasses import fields

from strategy_workbench.adapters.inbound.http_api._backtest_result_contract import backtest_result
from tests.test_backtest_artifact_store import _result


def test_the_result_response_counts_positions_and_keeps_every_other_field() -> None:
    stored = _result()

    response = backtest_result(stored)

    assert response.artifacts.position_count == len(stored.artifacts.positions) == 1
    stored_names = {item.name for item in fields(stored.artifacts)}
    response_names = {item.name for item in fields(response.artifacts)}
    assert response_names == stored_names - {"positions"} | {"position_count"}
    for name in stored_names - {"positions"}:
        assert getattr(response.artifacts, name) == getattr(stored.artifacts, name), name
    assert (
        response.manifest,
        response.metric_definitions,
        response.metrics,
        response.series,
    ) == (stored.manifest, stored.metric_definitions, stored.metrics, stored.series)
