"""run worker IPC 프로토콜 v1 의 인코딩·디코딩(run-worker spec D3).

부모와 worker 가 이 모듈 하나로 줄을 쓰고 읽는다. 한 줄은 UTF-8 JSON 객체 하나이고 `protocol`·
`type` 칸을 싣는다. 버전이 다르면 양쪽 모두 `ProtocolError` 로 거절한다.

- 부모 → worker(stdin): `job`
- worker → 부모(stdout): `ready`, `progress`, `committed`, `failed`(종결은 뒤의 둘)

작업 입력은 pickle 이 아니라 기존 직렬화 계약으로 옮긴다. spec 은
`ResolvedBacktestRunSpec.to_wire()` 의 pydantic 표현(HTTP 요청 본문과 같은 검증)이고, worker 가
`ResolvedBacktestRunSpec` 을 다시 만든다. 전략이 같은지는 계산의 tape 해시 대조
(`TargetTape.strategy_hash` 대 출처 `spec_hash`)가 본다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError

from strategy_workbench.application.backtest_run.facade.ports import RunCommitted, RunJob
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    ResolvedBacktestRunSpec,
    RunFailureCode,
    RunStage,
    StrategyProvenance,
)

PROTOCOL_VERSION = 1

_T = TypeVar("_T")


class ProtocolError(ValueError):
    """상대가 보낸 줄을 이 판본의 프로토콜로 읽을 수 없다."""


@dataclass(frozen=True)
class WorkerSettings:
    """부모가 해석해 worker 에 넘기는 설정. worker 는 환경 변수를 다시 읽지 않는다(spec D10)."""

    equity_adapter: str
    equity_root: Path | None
    artifact_root: Path


@dataclass(frozen=True)
class JobMessage:
    job: RunJob
    settings: WorkerSettings


@dataclass(frozen=True)
class VersionBundle:
    """worker 가 부팅 뒤 읽은 데이터·규칙·코드 판본(spec D6). 대조는 부모가 한다(W1-03)."""

    snapshot_id: str
    engine_rules_version: str
    metric_registry_version: str
    factor_evaluation_version: str
    factor_registry_version: str
    code_fingerprint: str


@dataclass(frozen=True)
class ReadyMessage:
    versions: VersionBundle


@dataclass(frozen=True)
class ProgressMessage:
    stage: RunStage
    fraction: float
    message: str


@dataclass(frozen=True)
class CommittedMessage:
    committed: RunCommitted
    max_rss_bytes: int | None


@dataclass(frozen=True)
class FailedMessage:
    error_code: RunFailureCode
    error: str
    max_rss_bytes: int | None


WorkerEvent = ReadyMessage | ProgressMessage | CommittedMessage | FailedMessage


@dataclass(frozen=True)
class _JobWire:
    run_id: str
    # 출처·계열은 `to_wire` 가 요청 칸으로 싣는다.
    spec: BacktestRunSpec
    provenance: StrategyProvenance
    settings: WorkerSettings


_JOB = TypeAdapter(_JobWire)
_EVENTS: dict[str, TypeAdapter[Any]] = {
    "ready": TypeAdapter(ReadyMessage),
    "progress": TypeAdapter(ProgressMessage),
    "committed": TypeAdapter(CommittedMessage),
    "failed": TypeAdapter(FailedMessage),
}
_EVENT_TYPES: dict[type, str] = {
    ReadyMessage: "ready",
    ProgressMessage: "progress",
    CommittedMessage: "committed",
    FailedMessage: "failed",
}


def encode_job(job: RunJob, settings: WorkerSettings) -> str:
    wire = _JobWire(
        run_id=job.run_id,
        spec=job.spec.to_wire(
            strategy_source=job.strategy_source, lineage_strategy_id=job.lineage_strategy_id
        ),
        provenance=job.provenance,
        settings=settings,
    )
    return _line("job", _JOB.dump_python(wire, mode="json"))


def decode_job(line: str) -> JobMessage:
    kind, body = _read(line)
    if kind != "job":
        raise ProtocolError(f"run worker expected a job line — type={kind!r}")
    wire = _validate(_JOB, kind, body)
    request = wire.spec
    if request.strategy is None or request.environment is None:
        raise ProtocolError(
            "run worker job lacks the resolved strategy or environment — "
            f"run_id={wire.run_id} strategy={request.strategy is not None} "
            f"environment={request.environment is not None}"
        )
    spec = ResolvedBacktestRunSpec.from_request(
        request,
        strategy=request.strategy,
        environment=request.environment,
        parameter_values=request.parameter_values,
    )
    return JobMessage(
        RunJob(
            run_id=wire.run_id,
            spec=spec,
            provenance=wire.provenance,
            strategy_source=request.strategy_source,
            lineage_strategy_id=request.lineage_strategy_id,
        ),
        wire.settings,
    )


def encode_event(event: WorkerEvent) -> str:
    kind = _EVENT_TYPES[type(event)]
    return _line(kind, _EVENTS[kind].dump_python(event, mode="json"))


def decode_event(line: str) -> WorkerEvent:
    kind, body = _read(line)
    adapter = _EVENTS.get(kind) if isinstance(kind, str) else None
    if adapter is None:
        raise ProtocolError(
            f"run worker sent an unknown event type — type={kind!r} known={sorted(_EVENTS)}"
        )
    return _validate(adapter, kind, body)


def _line(kind: str, body: dict[str, Any]) -> str:
    # 한글을 이스케이프하지 않는다 — 양쪽이 파이프를 UTF-8 로 연다(spec D2).
    return (
        json.dumps({"protocol": PROTOCOL_VERSION, "type": kind, **body}, ensure_ascii=False) + "\n"
    )


def _read(line: str) -> tuple[object, dict[str, Any]]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as error:
        raise ProtocolError(f"run worker line is not JSON — {error} line={line[:200]!r}") from error
    if not isinstance(payload, dict):
        raise ProtocolError(f"run worker line is not a JSON object — line={line[:200]!r}")
    version = payload.pop("protocol", None)
    kind = payload.pop("type", None)
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            "run worker protocol version mismatch — "
            f"expected={PROTOCOL_VERSION} got={version!r} type={kind!r}"
        )
    return kind, payload


def _validate(adapter: TypeAdapter[_T], kind: object, body: dict[str, Any]) -> _T:
    try:
        return adapter.validate_python(body)
    except ValidationError as error:
        first = error.errors()[0]
        raise ProtocolError(
            f"run worker {kind} line does not decode — errors={error.error_count()} "
            f"first={first['loc']}: {first['msg']}"
        ) from error
