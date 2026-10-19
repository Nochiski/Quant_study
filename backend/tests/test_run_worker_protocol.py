"""run worker IPC 프로토콜 v1(run-worker spec D3): 줄 인코딩·디코딩, 판본 거절, 진행 상한.

하위 프로세스 없이 줄만 본다. 실제 하위 프로세스는 `integration/test_run_worker_process.py` 가 돈다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.run_worker_process._worker import _ProgressThrottle
from strategy_workbench.adapters.outbound.run_worker_process.facade.protocol import (
    PROTOCOL_VERSION,
    CommittedMessage,
    FailedMessage,
    ProgressMessage,
    ProtocolError,
    ReadyMessage,
    VersionBundle,
    WorkerEvent,
    WorkerSettings,
    decode_event,
    decode_job,
    encode_event,
    encode_job,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    RunCommitted,
    RunHeadline,
    RunJob,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    InlineDraft,
    ResolvedBacktestRunSpec,
    StrategyProvenance,
    StrategySourceKind,
)
from strategy_workbench.domain.strategy.facade.specification import strategy_spec_hash


def resolved_job(run_id: str, *, lineage_strategy_id: str | None = None) -> RunJob:
    """mock 어댑터가 답하는 구간의 템플릿 전략 run 하나."""
    strategy = StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "x").template()
    environment = RunEnvironment(
        start=date(2026, 1, 2), end=date(2026, 2, 20), universe_id="krx.common-stock"
    )
    source = InlineDraft(spec=strategy, kind="inline_draft", source_hash="원문-해시")
    request = BacktestRunSpec(strategy=strategy, environment=environment)
    return RunJob(
        run_id=run_id,
        spec=ResolvedBacktestRunSpec.from_request(
            request,
            strategy=strategy,
            environment=environment,
            parameter_values={"n": 3, "on": True, "rate": 0.5, "name": "가"},
        ),
        provenance=StrategyProvenance(
            kind=StrategySourceKind.INLINE_DRAFT,
            spec_hash=strategy_spec_hash(strategy),
            schema_version=strategy.identity.schema_version,
            source_hash="원문-해시",
        ),
        strategy_source=source,
        lineage_strategy_id=lineage_strategy_id,
    )


def test_job_line_rebuilds_the_resolved_spec_and_settings(tmp_path: Path) -> None:
    job = resolved_job("run-1", lineage_strategy_id="계열")
    settings = WorkerSettings("duckdb", tmp_path / "원장", tmp_path / "artifacts")

    line = encode_job(job, settings)
    decoded = decode_job(line)

    assert line.endswith("\n") and line.count("\n") == 1
    assert "계열" in line  # 한글은 이스케이프하지 않는 UTF-8 이다
    assert decoded.job == job
    assert decoded.settings == settings


@pytest.mark.parametrize(
    "event",
    [
        ReadyMessage(VersionBundle("snap:판", "engine-v7", "metric-v5", "fe-v1", "fr-v1", "c0de")),
        ProgressMessage("tape", 0.25, "팩터 계산"),
        CommittedMessage(
            RunCommitted(
                sha256="0" * 64,
                session_sharpe=None,
                metric_registry_version="metric-v5",
                headline=RunHeadline(total_return=0.1, sharpe=None, max_drawdown=-0.2),
            ),
            max_rss_bytes=123,
        ),
        FailedMessage("backtest.run.benchmark_unknown", "알 수 없는 벤치마크 — got=['x']", None),
    ],
)
def test_event_lines_round_trip(event: WorkerEvent) -> None:
    assert decode_event(encode_event(event)) == event


def _with_version(line: str, version: object) -> str:
    payload = json.loads(line)
    payload["protocol"] = version
    return json.dumps(payload) + "\n"


@pytest.mark.parametrize("version", [PROTOCOL_VERSION + 1, None, "1"])
def test_both_directions_reject_another_protocol_version(tmp_path: Path, version: object) -> None:
    job_line = encode_job(resolved_job("run-1"), WorkerSettings("mock", None, tmp_path))
    event_line = encode_event(ProgressMessage("tape", 0.1, "x"))

    with pytest.raises(ProtocolError, match="protocol version mismatch"):
        decode_job(_with_version(job_line, version))
    with pytest.raises(ProtocolError, match="protocol version mismatch"):
        decode_event(_with_version(event_line, version))


@pytest.mark.parametrize(
    "line",
    [
        "not json\n",
        "[1, 2]\n",
        json.dumps({"protocol": PROTOCOL_VERSION, "type": "cancel"}) + "\n",
        json.dumps({"protocol": PROTOCOL_VERSION, "type": "progress", "stage": "nope"}) + "\n",
        json.dumps(
            {"protocol": PROTOCOL_VERSION, "type": "failed", "error_code": "made.up", "error": ""}
        )
        + "\n",
    ],
)
def test_malformed_event_lines_are_protocol_errors(line: str) -> None:
    with pytest.raises(ProtocolError):
        decode_event(line)


def test_progress_throttle_sends_changes_at_once_and_repeats_at_most_every_100ms() -> None:
    sent: list[WorkerEvent] = []
    now = [0.0]
    throttle = _ProgressThrottle(sent.append, clock=lambda: now[0])

    throttle("tape", 0.1, "a")  # 첫 값
    throttle("tape", 0.2, "a")  # 같은 설명, 0.1초 안 → 보류
    now[0] = 0.05
    throttle("tape", 0.3, "b")  # 설명이 바뀜 → 바로
    throttle("tape", 0.4, "b")  # 보류
    now[0] = 0.16
    throttle("tape", 0.5, "b")  # 0.1초 지남 → 보냄
    throttle("tape", 0.6, "b")  # 보류
    throttle.flush()  # 마지막 값은 항상 보낸다
    throttle.flush()  # 보낼 것이 없다

    progress = [event for event in sent if isinstance(event, ProgressMessage)]
    assert [(event.fraction, event.message) for event in progress] == [
        (0.1, "a"),
        (0.3, "b"),
        (0.5, "b"),
        (0.6, "b"),
    ]
