from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from strategy_workbench.adapters.outbound.sqlite_store.facade.timestamp import datetime_text
from strategy_workbench.application.strategy_design.facade.ports import (
    RevisionOrigin,
    RevisionProvenance,
    RevisionSource,
    StrategyRevisionRecord,
)
from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    SourceFormat,
    hydrate_strategy_document,
    is_frozen_schema_version,
    upgrade_document,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
    canonical_json_spec_hash,
    canonical_strategy_json,
)

from ._errors import StrategyRepositoryStorageError
from ._values import optional_text, required_int, required_text

SourceSpecHashResolver = Callable[[str, SourceFormat], str]


def encode_record(
    record: StrategyRevisionRecord, *, source_spec_hash: SourceSpecHashResolver
) -> tuple[object, ...]:
    """Map a validated envelope to columns without defining a second StrategySpec DTO."""
    try:
        if record.requires_upgrade:
            raise ValueError(
                "a frozen retired-schema revision is read-only history and cannot be written — "
                f"schema_version={record.spec.identity.schema_version}"
            )
        _verify_source_spec(record, source_spec_hash)
        source = record.source
        return (
            record.strategy_id,
            record.revision,
            record.spec.identity.schema_version,
            canonical_strategy_json(record.spec),
            record.spec_hash,
            source.format.value if source else None,
            source.text if source else None,
            source.source_hash if source else None,
            record.provenance.origin.value,
            datetime_text(record.provenance.created_at, field="revision created_at"),
            record.provenance.change_note,
        )
    except (TypeError, ValueError) as error:
        raise StrategyRepositoryStorageError(
            "strategy revision cannot be persisted -- "
            f"strategy_id={record.strategy_id} revision={record.revision}: {error}"
        ) from error


def decode_record(
    row: sqlite3.Row, *, source_spec_hash: SourceSpecHashResolver
) -> StrategyRevisionRecord:
    """Rehydrate through the domain contract and reject every redundant-field mismatch."""
    location = "strategy_id=<invalid> revision=<invalid>"
    try:
        strategy_id = required_text(row, "strategy_id")
        revision = required_int(row, "revision")
        location = f"strategy_id={strategy_id} revision={revision}"
        schema_version = required_text(row, "schema_version")
        spec_json = required_text(row, "spec_json")
        payload: Any = json.loads(spec_json)
        if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
            raise ValueError("spec_json must contain a JSON object with string keys")
        if payload.get("schema_version") != schema_version:
            raise ValueError("schema_version column does not match the canonical strategy payload")
        spec_hash = required_text(row, "spec_hash")
        # 동결 판정 술어는 port와 같은 domain 함수 하나다(DEFECT-P1X-003). 은퇴 버전마다 필요한
        # 변환 단계는 domain 업그레이드 체인이 버전 디스패치로 고른다.
        frozen = is_frozen_schema_version(schema_version)
        if frozen:
            spec = _decode_frozen_spec(
                payload, spec_json, spec_hash, strategy_id, revision, schema_version
            )
        else:
            hydration = hydrate_strategy_document(
                _as_document(payload),
                identity=StrategyIdentity(strategy_id, revision, schema_version),
            )
            if not hydration.ok or hydration.spec is None:
                detail = ", ".join(
                    f"{issue.code}@{issue.pointer}" for issue in hydration.issues[:5]
                )
                raise ValueError(f"canonical strategy payload cannot hydrate — {detail}")
            spec = hydration.spec
            if canonical_strategy_json(spec) != spec_json:
                raise ValueError("spec_json is not canonical for its hydrated StrategySpec")

        origin = RevisionOrigin(required_text(row, "origin"))
        source_format_text = optional_text(row, "source_format")
        source_text = optional_text(row, "source_text")
        source_hash = optional_text(row, "source_hash")
        source: RevisionSource | None
        if source_format_text is None and source_text is None and source_hash is None:
            source = None
        elif source_format_text is not None and source_text is not None and source_hash is not None:
            source = RevisionSource(SourceFormat(source_format_text), source_text, source_hash)
        else:
            raise ValueError("source format/text/hash must be all present or all absent")

        created_at_text = required_text(row, "created_at")
        created_at = datetime.fromisoformat(created_at_text)
        if datetime_text(created_at, field="revision created_at") != created_at_text:
            raise ValueError("created_at is not canonical timezone-aware UTC text")
        record = StrategyRevisionRecord(
            spec=spec,
            spec_hash=spec_hash,
            source=source,
            provenance=RevisionProvenance(
                origin=origin,
                created_at=created_at,
                change_note=optional_text(row, "change_note"),
            ),
        )
        if not frozen:
            _verify_source_spec(record, source_spec_hash)
        return record
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise StrategyRepositoryStorageError(
            f"stored strategy revision failed integrity validation — {location}: {error}"
        ) from error


def _as_document(payload: dict[str, Any]) -> Mapping[str, object]:
    return payload


def _decode_frozen_spec(
    payload: dict[str, Any],
    spec_json: str,
    spec_hash: str,
    strategy_id: str,
    revision: int,
    schema_version: str,
) -> StrategySpec:
    """Read a retired-schema row without a model for that schema (spec D2).

    은퇴 버전의 모델은 더 이상 없으므로 "source 가 저장된 spec 으로 컴파일된다"를 다시 증명할 수
    없다. row 는 immutable 이고 기록 시점에 이미 증명됐다. 여기서 검증하는 것은 저장된 바이트가
    hash 컬럼이 말하는 그 바이트인지와, 도메인 업그레이드 변환이 아직 그 문서를 이해하는지다.
    spec 은 row 가 저장된 은퇴 버전을 동결 표식으로 그대로 들고 나간다.

    변환은 domain 업그레이드 체인(`upgrade_document`) 하나로 현재 버전까지 간다: 1.0 row 는
    1.0 → 1.1 → 1.2, 1.1 row 는 1.1 → 1.2 를 탄다. 1.1 → 1.2 단계가 `normalization: none` 을
    명시하므로 복원한 spec 의 합성 방식이 그 revision 의 실제 의미(원시값 가중 합)와 같다 — 기본값
    `rank` 로 읽으면 설명 문장이 저장된 적 없는 의미를 말한다(P2-04 1차 리뷰 P3). 떼어 낸 실행
    설정은 읽지 않는다: 동결 row 는 실행할 수 없고(`requires_upgrade`), 실행 설정은 업그레이드
    응답이 사용자에게 돌려준다.

    무결성 검증 세 가지(spec D2): 저장된 바이트가 hash 컬럼과 같다(여기), 컬럼과 payload 의 버전이
    같다(`decode_record`), 체인이 그 문서를 끝까지 올려 현재 모델로 hydrate 된다(여기). 체인 대상은
    **알려진 은퇴 버전**뿐이다 — `is_frozen_schema_version` 은 "현재 버전이 아닌 모든 것"이라 열린
    집합이어서, 모르는 버전(`"1.3"`·`"9.9"`)은 체인이 `NotUpgradeableDocumentError` 로 거절한다.
    그러지 않으면 미래 버전이나 손상된 값이 조용히 현재 모델로 해석되고, `spec_hash` 검증은 변환
    전에 끝나 그 변형을 잡지 못한다(P2-03 리뷰 P2-02).
    """
    computed = canonical_json_spec_hash(spec_json)
    if computed != spec_hash:
        raise ValueError(
            "frozen spec_json bytes do not match the stored spec_hash -- "
            f"strategy_id={strategy_id} revision={revision} computed={computed} stored={spec_hash}"
        )
    upgraded = upgrade_document(payload).tree
    hydration = hydrate_strategy_document(
        upgraded, identity=StrategyIdentity(strategy_id, revision, CURRENT_SCHEMA_VERSION)
    )
    if not hydration.ok or hydration.spec is None:
        detail = ", ".join(f"{issue.code}@{issue.pointer}" for issue in hydration.issues[:5])
        raise ValueError(
            "frozen strategy payload cannot be upgraded -- "
            f"strategy_id={strategy_id} revision={revision} issues={detail}"
        )
    spec = hydration.spec
    # 동결 표식은 row가 저장된 그 버전이다(1.0만이 아니라 은퇴한 모든 버전).
    return replace(spec, identity=replace(spec.identity, schema_version=schema_version))


def _verify_source_spec(
    record: StrategyRevisionRecord, source_spec_hash: SourceSpecHashResolver
) -> None:
    """Bind exact document source to the canonical spec via the injected authoring SoT."""
    source = record.source
    if source is None:
        return
    compiled_hash = source_spec_hash(source.text, source.format)
    if not isinstance(compiled_hash, str):
        raise TypeError("source spec hash resolver must return text")
    if compiled_hash != record.spec_hash:
        raise ValueError(
            "source compiles to a different StrategySpec -- "
            f"stored={record.spec_hash} compiled={compiled_hash}"
        )
