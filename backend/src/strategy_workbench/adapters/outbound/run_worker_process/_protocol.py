"""run worker IPC 프로토콜 v2 의 인코딩·디코딩(run-worker spec D3·D7·D11).

부모와 worker 가 이 모듈 하나로 줄을 쓰고 읽는다. 한 줄은 UTF-8 JSON 객체 하나이고 `protocol`·
`type` 칸을 싣는다. 버전이 다르면 양쪽 모두 `ProtocolError` 로 거절한다.

- 부모 → worker(stdin): `boot` 한 줄(설정), `ready` 를 받은 뒤 `job` 한 줄, 엔진 관문을 켠 job 이면
  `proceed` 한 줄
- worker → 부모(stdout): `ready`, `progress`, `engine_ready`(엔진 관문), `committed`, `failed`
  (종결은 뒤의 둘)

v2 는 설정을 job 에서 떼어 `boot` 로 먼저 보낸다. 예비 worker 는 run 이 정해지기 전에 `ready` 까지
부팅해 두므로(spec D7) 부팅에 쓸 설정이 job 보다 먼저 와야 한다.

작업 입력은 pickle 이 아니라 기존 직렬화 계약으로 옮긴다. spec 은
`ResolvedBacktestRunSpec.to_wire()` 의 pydantic 표현(HTTP 요청 본문과 같은 검증)이고, worker 가
`ResolvedBacktestRunSpec` 을 다시 만든다. 전략이 같은지는 계산의 tape 해시 대조
(`TargetTape.strategy_hash` 대 출처 `spec_hash`)가 본다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import TypeAdapter, ValidationError

from strategy_workbench.application.backtest_run.facade.ports import RunCommitted, RunJob
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    ResolvedBacktestRunSpec,
    RunFailureCode,
    RunStage,
    StrategyProvenance,
)

PROTOCOL_VERSION = 2

_T = TypeVar("_T")

ProtocolErrorReason = Literal[
    "not_utf8",
    "not_json",
    "not_object",
    # JSON 객체이지만 `protocol` 칸이 없다 — 프로토콜 줄이 아니다(다른 판본과 구별한다).
    "not_protocol",
    "version_mismatch",
    "unexpected_type",
    "invalid_fields",
    "before_ready",
    "after_terminal",
]


class ProtocolError(ValueError):
    """상대가 보낸 줄을 이 판본의 프로토콜로 읽을 수 없다.

    `reason`·`message_type` 은 상태에 실어도 되는 요약이고(`message_type` 은 이 판본이 아는
    이름이거나 `<unknown>`), 메시지 본문(줄 발췌 포함)은 로그용이다.
    """

    def __init__(
        self, reason: ProtocolErrorReason, detail: str, *, message_type: object = None
    ) -> None:
        super().__init__(detail)
        self.reason: ProtocolErrorReason = reason
        self.message_type: str | None = (
            None
            if message_type is None
            else message_type
            if isinstance(message_type, str) and message_type in _MESSAGE_TYPES
            else "<unknown>"
        )


@dataclass(frozen=True)
class WorkerSettings:
    """부모가 해석해 worker 에 넘기는 설정. worker 는 환경 변수를 다시 읽지 않는다(spec D10).

    `boot` 줄에 실린다. `engine_gate` 가 참이면 worker 는 엔진 단계 앞에서 `engine_ready` 를 보내고
    `proceed` 를 기다린다(e2e hold, spec D11).
    """

    equity_adapter: str
    equity_root: Path | None
    artifact_root: Path
    engine_gate: bool = False


@dataclass(frozen=True)
class VersionBundle:
    """run 을 계산하는 데이터·규칙·코드 판본(spec D6). 부모가 자기 묶음과 대조한다."""

    snapshot_id: str
    engine_rules_version: str
    metric_registry_version: str
    factor_evaluation_version: str
    factor_registry_version: str
    code_fingerprint: str

    def mismatched(self, other: VersionBundle) -> tuple[str, ...]:
        """값이 다른 항목 이름(선언 순서)."""
        return tuple(
            item.name
            for item in fields(self)
            if getattr(self, item.name) != getattr(other, item.name)
        )


@dataclass(frozen=True)
class ReadyMessage:
    versions: VersionBundle


@dataclass(frozen=True)
class ProgressMessage:
    stage: RunStage
    fraction: float
    message: str


@dataclass(frozen=True)
class EngineReadyMessage:
    """엔진 단계 앞에 왔다. 부모의 `proceed` 를 기다린다(spec D11)."""


@dataclass(frozen=True)
class CommittedMessage:
    committed: RunCommitted
    max_rss_bytes: int | None


@dataclass(frozen=True)
class FailedMessage:
    error_code: RunFailureCode
    error: str
    max_rss_bytes: int | None


@dataclass(frozen=True)
class ProceedMessage:
    """엔진 단계로 넘어가도 된다(부모 → worker, spec D11)."""


WorkerEvent = ReadyMessage | ProgressMessage | EngineReadyMessage | CommittedMessage | FailedMessage


@dataclass(frozen=True)
class _JobWire:
    run_id: str
    # 출처·계열은 `to_wire` 가 요청 칸으로 싣는다.
    spec: BacktestRunSpec
    provenance: StrategyProvenance


_BOOT = TypeAdapter(WorkerSettings)
_JOB = TypeAdapter(_JobWire)
_EVENTS: dict[str, TypeAdapter[Any]] = {
    "ready": TypeAdapter(ReadyMessage),
    "progress": TypeAdapter(ProgressMessage),
    "engine_ready": TypeAdapter(EngineReadyMessage),
    "committed": TypeAdapter(CommittedMessage),
    "failed": TypeAdapter(FailedMessage),
}
_EVENT_TYPES: dict[type, str] = {
    ReadyMessage: "ready",
    ProgressMessage: "progress",
    EngineReadyMessage: "engine_ready",
    CommittedMessage: "committed",
    FailedMessage: "failed",
}
# 상태 문장에 실어도 되는 메시지 type(`ProtocolError.message_type`). 이 밖의 값은 상대가 보낸
# 원문이라 `<unknown>` 으로 접는다.
_MESSAGE_TYPES = frozenset({"boot", "job", "proceed", *_EVENTS})


def encode_boot(settings: WorkerSettings) -> str:
    return _line("boot", _BOOT.dump_python(settings, mode="json"))


def decode_boot(line: str) -> WorkerSettings:
    kind, body = _read(line)
    _expect(kind, "boot")
    return _validate(_BOOT, kind, body)


def encode_job(job: RunJob) -> str:
    wire = _JobWire(
        run_id=job.run_id,
        spec=job.spec.to_wire(
            strategy_source=job.strategy_source, lineage_strategy_id=job.lineage_strategy_id
        ),
        provenance=job.provenance,
    )
    return _line("job", _JOB.dump_python(wire, mode="json"))


def decode_job(line: str) -> RunJob:
    kind, body = _read(line)
    _expect(kind, "job")
    wire = _validate(_JOB, kind, body)
    request = wire.spec
    if request.strategy is None or request.environment is None:
        raise ProtocolError(
            "invalid_fields",
            "run worker job lacks the resolved strategy or environment — "
            f"run_id={wire.run_id} strategy={request.strategy is not None} "
            f"environment={request.environment is not None}",
            message_type=kind,
        )
    spec = ResolvedBacktestRunSpec.from_request(
        request,
        strategy=request.strategy,
        environment=request.environment,
        parameter_values=request.parameter_values,
    )
    return RunJob(
        run_id=wire.run_id,
        spec=spec,
        provenance=wire.provenance,
        strategy_source=request.strategy_source,
        lineage_strategy_id=request.lineage_strategy_id,
    )


def encode_proceed() -> str:
    return _line("proceed", {})


def decode_proceed(line: str) -> ProceedMessage:
    kind, body = _read(line)
    if kind != "proceed" or body:
        raise ProtocolError(
            "unexpected_type",
            f"run worker expected a proceed line — type={kind!r} fields={sorted(body)}",
            message_type=kind,
        )
    return ProceedMessage()


def event_type(event: WorkerEvent) -> str:
    """worker 사건의 프로토콜 type 이름(`ready`·`progress`·…)."""
    return _EVENT_TYPES[type(event)]


def encode_event(event: WorkerEvent) -> str:
    kind = event_type(event)
    return _line(kind, _EVENTS[kind].dump_python(event, mode="json"))


def decode_event(line: str) -> WorkerEvent:
    kind, body = _read(line)
    adapter = _EVENTS.get(kind) if isinstance(kind, str) else None
    if adapter is None:
        raise ProtocolError(
            "unexpected_type",
            f"run worker sent an unknown event type — type={kind!r} known={sorted(_EVENTS)}",
            message_type=kind,
        )
    return _validate(adapter, kind, body)


def _expect(kind: object, expected: str) -> None:
    if kind != expected:
        raise ProtocolError(
            "unexpected_type",
            f"run worker expected a {expected} line — type={kind!r}",
            message_type=kind,
        )


def _line(kind: str, body: dict[str, Any]) -> str:
    # 한글을 이스케이프하지 않는다 — 양쪽이 파이프를 UTF-8 로 연다(spec D2).
    return (
        json.dumps({"protocol": PROTOCOL_VERSION, "type": kind, **body}, ensure_ascii=False) + "\n"
    )


def _read(line: str) -> tuple[object, dict[str, Any]]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as error:
        raise ProtocolError(
            "not_json", f"run worker line is not JSON — {error} line={line[:200]!r}"
        ) from error
    if not isinstance(payload, dict):
        raise ProtocolError(
            "not_object", f"run worker line is not a JSON object — line={line[:200]!r}"
        )
    if "protocol" not in payload:
        raise ProtocolError(
            "not_protocol", f"run worker line has no protocol field — line={line[:200]!r}"
        )
    version = payload.pop("protocol")
    kind = payload.pop("type", None)
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            "version_mismatch",
            "run worker protocol version mismatch — "
            f"expected={PROTOCOL_VERSION} got={version!r} type={kind!r}",
            message_type=kind,
        )
    return kind, payload


def _validate(adapter: TypeAdapter[_T], kind: object, body: dict[str, Any]) -> _T:
    try:
        return adapter.validate_python(body)
    except ValidationError as error:
        first = error.errors()[0]
        raise ProtocolError(
            "invalid_fields",
            f"run worker {kind} line does not decode — errors={error.error_count()} "
            f"first={first['loc']}: {first['msg']}",
            message_type=kind,
        ) from error
