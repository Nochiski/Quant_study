"""`ChatEvent` union ↔ 태그 있는 JSON (설계 spec D2/D5).

union의 어느 갈래인지는 **저장된 태그**가 말한다. 구조를 보고 맞춰 추측하면(예: `text` 필드가
있으면 `TextDelta`) `ThinkingSummary`와 구분되지 않고, 새 이벤트가 생길 때 과거 행의 해석이
조용히 바뀐다. 태그를 모르면 읽기에서 멈춘다 — 이력의 구멍은 조용히 넘기는 쪽이 더 위험하다.

wire 계약이 아니라 **이 어댑터의 저장 표현**이다. SSE 표현은 A-04의 inbound adapter가 따로
소유한다. 두 표현이 같은 문자열을 쓰더라도 owner는 둘이다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from strategy_workbench.domain.assistant.facade.models import (
    ChatEvent,
    Done,
    Failure,
    FailureCode,
    Proposal,
    ProposalCompileResult,
    ProposalDiagnostic,
    SearchActivity,
    Source,
    StrategyProposal,
    TextDelta,
    ThinkingSummary,
    ToolCall,
    ToolResultSummary,
    Usage,
)

from ._errors import AssistantStorageError

__all__ = ["decode_event", "encode_event"]


def encode_event(event: ChatEvent) -> tuple[str, str]:
    """이벤트 하나를 (태그, JSON 문자열)로. 저장은 두 컬럼으로 나눠 태그만으로 조회할 수 있다."""
    match event:
        case TextDelta():
            return "text_delta", _dumps({"text": event.text})
        case ThinkingSummary():
            return "thinking_summary", _dumps({"text": event.text})
        case ToolCall():
            return "tool_call", _dumps(
                {
                    "call_id": event.call_id,
                    "name": event.name,
                    "arguments": dict(event.arguments),
                }
            )
        case ToolResultSummary():
            return "tool_result_summary", _dumps(
                {
                    "call_id": event.call_id,
                    "name": event.name,
                    "ok": event.ok,
                    "summary": event.summary,
                }
            )
        case SearchActivity():
            return "search_activity", _dumps(
                {
                    "query": event.query,
                    "sources": [_source_payload(source) for source in event.sources],
                }
            )
        case Proposal():
            return "proposal", _dumps({"proposal": _proposal_payload(event.proposal)})
        case Usage():
            return "usage", _dumps(
                {"input_tokens": event.input_tokens, "output_tokens": event.output_tokens}
            )
        case Done():
            return "done", _dumps({"stop_reason": event.stop_reason})
        case Failure():
            return "failure", _dumps({"code": event.code.value, "message": event.message})
    # match가 union 전부를 덮으므로 여기는 새 이벤트를 추가하고 위를 안 고쳤을 때만 닿는다.
    raise AssistantStorageError(  # pragma: no cover - union 확장 방어
        f"no storage encoding for this chat event — type={type(event).__name__}"
    )


def decode_event(tag: str, payload: str) -> ChatEvent:
    """저장된 (태그, JSON)을 이벤트로. 태그를 모르거나 모양이 다르면 저장 오류다."""
    body = _loads(payload, tag=tag)
    match tag:
        case "text_delta":
            return TextDelta(text=_text(body, "text", tag))
        case "thinking_summary":
            return ThinkingSummary(text=_text(body, "text", tag))
        case "tool_call":
            return ToolCall(
                call_id=_text(body, "call_id", tag),
                name=_text(body, "name", tag),
                arguments=_mapping(body.get("arguments"), "arguments", tag),
            )
        case "tool_result_summary":
            return ToolResultSummary(
                call_id=_text(body, "call_id", tag),
                name=_text(body, "name", tag),
                ok=_bool(body, "ok", tag),
                summary=_text(body, "summary", tag),
            )
        case "search_activity":
            return SearchActivity(
                query=_text(body, "query", tag),
                sources=_sources(body.get("sources"), tag),
            )
        case "proposal":
            return Proposal(proposal=_proposal(body.get("proposal"), tag))
        case "usage":
            return Usage(
                input_tokens=_int(body, "input_tokens", tag),
                output_tokens=_int(body, "output_tokens", tag),
            )
        case "done":
            return Done(stop_reason=_text(body, "stop_reason", tag))
        case "failure":
            return Failure(code=_failure_code(body, tag), message=_text(body, "message", tag))
    raise AssistantStorageError(
        f"unknown stored chat event tag — event_type={tag!r} "
        "(a newer schema wrote it, or the row was edited by hand)"
    )


# -- 인코딩 도우미 ----------------------------------------------------------------------------


def _dumps(payload: Mapping[str, object]) -> str:
    # sort_keys: 같은 이벤트가 언제나 같은 바이트가 되어 덤프 비교 테스트가 안정적이다.
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_payload(source: Source) -> dict[str, object]:
    return {"title": source.title, "url": source.url}


def _proposal_payload(proposal: StrategyProposal) -> dict[str, object]:
    return {
        "title": proposal.title,
        "summary": proposal.summary,
        "rationale": proposal.rationale,
        "sources": [_source_payload(source) for source in proposal.sources],
        "source_text": proposal.source_text,
        "source_format": proposal.source_format,
        "compile": {
            "ok": proposal.compile.ok,
            "spec_hash": proposal.compile.spec_hash,
            "diagnostics": [
                {
                    "code": diagnostic.code,
                    "pointer": diagnostic.pointer,
                    "message": diagnostic.message,
                    "severity": diagnostic.severity,
                }
                for diagnostic in proposal.compile.diagnostics
            ],
        },
    }


# -- 디코딩 도우미 ----------------------------------------------------------------------------


def _loads(payload: str, *, tag: str) -> Mapping[str, object]:
    try:
        decoded = json.loads(payload)
    except ValueError as error:
        raise AssistantStorageError(
            f"stored chat event is not valid JSON — event_type={tag!r}"
        ) from error
    if not isinstance(decoded, dict):
        raise AssistantStorageError(
            f"stored chat event is not a JSON object — event_type={tag!r} "
            f"type={type(decoded).__name__}"
        )
    return decoded


def _field(body: Mapping[str, object], key: str, tag: str) -> object:
    if key not in body:
        raise AssistantStorageError(
            f"stored chat event is missing a field — event_type={tag!r} field={key!r}"
        )
    return body[key]


def _text(body: Mapping[str, object], key: str, tag: str) -> str:
    value = _field(body, key, tag)
    if not isinstance(value, str):
        raise AssistantStorageError(
            f"stored chat event field is not text — event_type={tag!r} field={key!r} "
            f"type={type(value).__name__}"
        )
    return value


def _optional_text(body: Mapping[str, object], key: str, tag: str) -> str | None:
    value = _field(body, key, tag)
    if value is None:
        return None
    return _text(body, key, tag)


def _int(body: Mapping[str, object], key: str, tag: str) -> int:
    value = _field(body, key, tag)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AssistantStorageError(
            f"stored chat event field is not an integer — event_type={tag!r} field={key!r} "
            f"type={type(value).__name__}"
        )
    return value


def _bool(body: Mapping[str, object], key: str, tag: str) -> bool:
    value = _field(body, key, tag)
    if not isinstance(value, bool):
        raise AssistantStorageError(
            f"stored chat event field is not a boolean — event_type={tag!r} field={key!r} "
            f"type={type(value).__name__}"
        )
    return value


def _mapping(value: object, key: str, tag: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise AssistantStorageError(
            f"stored chat event field is not a JSON object — event_type={tag!r} field={key!r} "
            f"type={type(value).__name__}"
        )
    for name in value:
        if not isinstance(name, str):  # pragma: no cover - JSON 키는 언제나 문자열이다
            raise AssistantStorageError(
                f"stored chat event object has a non-text key — event_type={tag!r} field={key!r}"
            )
    return value


def _sequence(value: object, key: str, tag: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise AssistantStorageError(
            f"stored chat event field is not a JSON array — event_type={tag!r} field={key!r} "
            f"type={type(value).__name__}"
        )
    return value


def _sources(value: object, tag: str) -> tuple[Source, ...]:
    items = _sequence(value, "sources", tag)
    return tuple(
        Source(title=_text(item, "title", tag), url=_text(item, "url", tag))
        for item in (_mapping(entry, "sources[]", tag) for entry in items)
    )


def _proposal(value: object, tag: str) -> StrategyProposal:
    body = _mapping(value, "proposal", tag)
    compile_body = _mapping(_field(body, "compile", tag), "compile", tag)
    source_format = _text(body, "source_format", tag)
    if source_format != "yaml":
        raise AssistantStorageError(
            f"stored proposal has an unsupported source format — event_type={tag!r} "
            f"source_format={source_format!r} supported='yaml'"
        )
    return StrategyProposal(
        title=_text(body, "title", tag),
        summary=_text(body, "summary", tag),
        rationale=_text(body, "rationale", tag),
        sources=_sources(body.get("sources"), tag),
        source_text=_text(body, "source_text", tag),
        source_format="yaml",
        compile=ProposalCompileResult(
            ok=_bool(compile_body, "ok", tag),
            spec_hash=_optional_text(compile_body, "spec_hash", tag),
            diagnostics=tuple(
                ProposalDiagnostic(
                    code=_text(item, "code", tag),
                    pointer=_text(item, "pointer", tag),
                    message=_text(item, "message", tag),
                    severity=_text(item, "severity", tag),
                )
                for item in (
                    _mapping(entry, "diagnostics[]", tag)
                    for entry in _sequence(compile_body.get("diagnostics"), "diagnostics", tag)
                )
            ),
        ),
    )


def _failure_code(body: Mapping[str, object], tag: str) -> FailureCode:
    raw = _text(body, "code", tag)
    try:
        return FailureCode(raw)
    except ValueError as error:
        raise AssistantStorageError(
            f"stored failure event has an unknown code — event_type={tag!r} code={raw!r}"
        ) from error
