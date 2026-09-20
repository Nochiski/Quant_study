"""Typed hydrate: identity-free authoring payload → immutable StrategySpec.

ADR: docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md (D1, D4)

- The payload shape is derived from the dataclass type hints, so there is no second DTO.
- Unknown keys at any depth, missing required fields, type mismatches, unknown `kind`
  discriminators, bad enum/date literals and unsupported schema versions are structural
  issues with a JSON Pointer. Only what the model declares as a default is filled in — a
  dataclass default (schema 1.1 makes the boilerplate sections, `data.market`, factor `weight`
  optional this way) or a `default-from` sibling field (`label` ← `factor_id`); a missing
  required field is never guessed.
- Typed scalar fields normalise `1`/`1.0`, ISO date strings and enum strings; the
  `ParameterValue` union keeps bool/str as-is and folds integral floats to int.

진단 문장은 한국어로 완성해 보낸다(P1-05). Problems panel은 `message`를 그대로 보여주고 다시
조립하지 않으므로(`.claude/rules/strategy-workbench-sot.md`), 화면 문구의 owner가 이 모듈이다.
문장 뒤 `—` 다음에는 기계가 읽는 디테일(`got=`·`expected=`·`allowed=`·`missing=`)이 남는다
(`.claude/rules/error-messages.md`). 키·kind·enum 오타에는 가까운 후보를 한 개 제안한다.
버전 줄은 현재 버전인데 본문이 1.0 문법이면 그 자리의 진단이 `structure.legacy_shape`로 바뀐다.
"""

from __future__ import annotations

import dataclasses
import difflib
import types
import typing
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum, StrEnum
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from ._models import CURRENT_SCHEMA_VERSION, StrategyIdentity, StrategySpec
from ._upgrade import legacy_shape_hints

# 새 문서로 받는 버전 집합. 현재 버전 상수의 owner는 `_models.py`다(모델 기본값과 같은 값).
SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = (CURRENT_SCHEMA_VERSION,)

# 본문만 1.0 문법인 문서에 다는 힌트 코드. `structure.*`의 owner는 이 모듈이다
# (`.claude/rules/strategy-workbench-sot.md` authoring 진단 코드 행).
LEGACY_SHAPE_CODE = "structure.legacy_shape"

# 이 모듈이 낼 수 있는 구조 진단 코드 전부. codec 코드에 `diagnostic_code()` 게이트가 있듯,
# 구조 코드에도 게이트를 둬서 목록에 없는 코드가 조용히 생기지 않게 한다. 코드마다 문장 golden이
# 하나씩 있다는 사실도 이 집합으로 강제한다(`tests/domain/test_strategy_diagnostic_messages.py`).
STRUCTURE_CODES: frozenset[str] = frozenset(
    {
        "structure.invalid_date",
        "structure.invalid_enum",
        LEGACY_SHAPE_CODE,
        "structure.missing_field",
        "structure.type_mismatch",
        "structure.unknown_key",
        "structure.unknown_kind",
        "structure.unsupported_schema_version",
    }
)

# reason: sentinel shared by every hydrate branch; the walker is generic over dataclass hints,
# so its intermediate values are `Any` until the top-level isinstance(StrategySpec) check.
_MISSING: Any = object()


class HydrationStatus(StrEnum):
    OK = "ok"
    STRUCTURAL_ERROR = "structural_error"


@dataclass(frozen=True)
class StructuralIssue:
    code: str
    pointer: str
    message: str

    def __post_init__(self) -> None:
        if self.code not in STRUCTURE_CODES:
            raise ValueError(
                "structural diagnostic code has no owner — add it to STRUCTURE_CODES and give it "
                f"a message golden: code={self.code!r} pointer={self.pointer!r}"
            )


@dataclass(frozen=True)
class StrategyHydration:
    status: HydrationStatus
    spec: StrategySpec | None
    issues: tuple[StructuralIssue, ...]

    @property
    def ok(self) -> bool:
        return self.status is HydrationStatus.OK


def hydrate_strategy_document(
    document: Mapping[str, object], *, identity: StrategyIdentity
) -> StrategyHydration:
    """Hydrate an identity-free authoring document into a StrategySpec.

    `schema_version` is read from the document; identity comes from the revision envelope.
    """
    issues: list[StructuralIssue] = []
    schema_version = document.get("schema_version", _MISSING)
    if schema_version is _MISSING:
        issues.append(
            StructuralIssue(
                "structure.missing_field",
                "/schema_version",
                "문서 맨 위에 schema_version을 적어 주세요 — "
                f"missing='schema_version' supported={SUPPORTED_SCHEMA_VERSIONS}",
            )
        )
    elif schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        issues.append(
            StructuralIssue(
                "structure.unsupported_schema_version",
                "/schema_version",
                "지원하지 않는 schema_version입니다. 업그레이드하면 지금 버전으로 바꿔 "
                f"줍니다 — got={schema_version!r} supported={SUPPORTED_SCHEMA_VERSIONS}",
            )
        )
    if "identity" in document:
        issues.append(
            StructuralIssue(
                "structure.unknown_key",
                "/identity",
                "identity는 revision 봉투가 소유합니다. 문서에서 지워 주세요 — got='identity'",
            )
        )
    if issues:
        return StrategyHydration(HydrationStatus.STRUCTURAL_ERROR, None, tuple(issues))

    payload = {key: value for key, value in document.items() if key != "schema_version"}
    payload["identity"] = {
        "strategy_id": identity.strategy_id,
        "revision": identity.revision,
        "schema_version": str(schema_version),
    }
    spec = _hydrate(StrategySpec, payload, "", issues)
    if issues or spec is _MISSING:
        return StrategyHydration(
            HydrationStatus.STRUCTURAL_ERROR, None, _with_legacy_hints(document, issues)
        )
    if not isinstance(spec, StrategySpec):  # pragma: no cover - defensive
        raise TypeError(f"hydrate produced {type(spec).__name__}, expected StrategySpec")
    return StrategyHydration(HydrationStatus.OK, spec, ())


def hydrate_saved_strategy(document: Mapping[str, object]) -> StrategyHydration:
    """Hydrate a legacy payload that carries `identity` inside the document (current JSON API)."""
    issues: list[StructuralIssue] = []
    spec = _hydrate(StrategySpec, document, "", issues)
    if issues or spec is _MISSING:
        return StrategyHydration(
            HydrationStatus.STRUCTURAL_ERROR, None, _with_legacy_hints(document, issues)
        )
    if not isinstance(spec, StrategySpec):  # pragma: no cover - defensive
        raise TypeError(f"hydrate produced {type(spec).__name__}, expected StrategySpec")
    if spec.identity.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return StrategyHydration(
            HydrationStatus.STRUCTURAL_ERROR,
            None,
            (
                StructuralIssue(
                    "structure.unsupported_schema_version",
                    "/identity/schema_version",
                    "지원하지 않는 schema_version입니다. 업그레이드하면 지금 버전으로 "
                    f"바꿔 줍니다 — got={spec.identity.schema_version!r} "
                    f"supported={SUPPORTED_SCHEMA_VERSIONS}",
                ),
            ),
        )
    return StrategyHydration(HydrationStatus.OK, spec, ())


def _escape(key: str) -> str:
    """RFC 6901 token escaping so `a/b` and `~x` keys stay unambiguous in pointers."""
    return key.replace("~", "~0").replace("/", "~1")


def _child(pointer: str, key: object) -> str:
    return f"{pointer}/{_escape(str(key))}"


def _issue(issues: list[StructuralIssue], code: str, pointer: str, message: str) -> Any:
    # RFC 6901: the empty pointer addresses the whole document.
    issues.append(StructuralIssue(code, pointer, message))
    return _MISSING


def _closest(value: object, candidates: Iterable[object]) -> str | None:
    """오타로 보이는 값에 가장 가까운 후보 하나. 닮은 것이 없으면 None.

    초보자가 가장 자주 만나는 구조 오류가 키 오타(`max_name_wieght`)다. 허용 목록을 눈으로 훑는
    대신 한 번에 고칠 수 있게 한다. 닮음 판정은 difflib 기본 비율(0.6)을 그대로 쓴다.
    """
    if not isinstance(value, str):
        return None
    names = [candidate for candidate in candidates if isinstance(candidate, str)]
    close = difflib.get_close_matches(value, names, n=1)
    return close[0] if close else None


def _hint(closest: str | None) -> str:
    """문장에 붙일 제안 조각.

    키·kind·enum 값은 번역하면 계약이 달라지는 문자열이라 한글 이름으로 바꾸지 않고 `` `키` ``로
    인용한다. 사람 말 이름의 정본은 runtime schema의 `x-description-key`와 frontend i18n이고
    (P1-03), backend가 같은 이름을 한 벌 더 갖지 않는다.
    """
    return f" 혹시 `{closest}`인가요?" if closest else ""


def _hint_detail(closest: str | None) -> str:
    """같은 제안을 기계가 읽는 자리에도 남긴다 — 문장을 파싱해 꺼내 쓰지 않게."""
    return f" suggestion={closest!r}" if closest else ""


def _with_legacy_hints(
    document: Mapping[str, object], issues: list[StructuralIssue]
) -> tuple[StructuralIssue, ...]:
    """1.0 문법이 놓인 자리의 구조 오류를 `structure.legacy_shape`로 바꿔 단다.

    코드가 바뀌면 frontend 업그레이드 배너가 이 문서에도 뜬다(P1-05). pointer는 그대로 두므로
    편집기가 가리키는 범위는 달라지지 않고, 1.0 문법이 없는 문서는 이 함수가 원본을 그대로 돌려준다.
    """
    hints = legacy_shape_hints(document)
    if not hints:
        return tuple(issues)
    return tuple(
        StructuralIssue(LEGACY_SHAPE_CODE, issue.pointer, hints[issue.pointer])
        if issue.pointer in hints
        else issue
        for issue in issues
    )


def _hydrate(tp: Any, value: object, pointer: str, issues: list[StructuralIssue]) -> Any:
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        return _hydrate_union(get_args(tp), value, pointer, issues)
    if origin is Literal:
        allowed = get_args(tp)
        if value in allowed and not isinstance(value, bool):
            return value
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            f"여기에 쓸 수 있는 값이 아닙니다 — got={value!r} allowed={list(allowed)!r}",
        )
    if origin is tuple:
        return _hydrate_sequence(get_args(tp)[0], value, pointer, issues)
    if dataclasses.is_dataclass(tp) and isinstance(tp, type):
        return _hydrate_dataclass(tp, value, pointer, issues)
    if tp is type(None):
        if value is None:
            return None
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            f"값이 비어 있어야 합니다 — expected=null got={value!r}",
        )
    return _hydrate_scalar(tp, value, pointer, issues)


def _hydrate_union(
    members: tuple[Any, ...], value: object, pointer: str, issues: list[StructuralIssue]
) -> Any:
    if value is None and type(None) in members:
        return None
    dataclass_members = [m for m in members if dataclasses.is_dataclass(m) and isinstance(m, type)]
    if dataclass_members:
        if not isinstance(value, Mapping):
            return _issue(
                issues,
                "structure.type_mismatch",
                pointer,
                "여기에는 하위 항목을 가진 블록이 와야 합니다 — "
                f"expected=mapping got={type(value).__name__}",
            )
        kinds = {_kind_of(m): m for m in dataclass_members}
        if None not in kinds:
            kind = value.get("kind", _MISSING)
            if kind is _MISSING:
                return _issue(
                    issues,
                    "structure.missing_field",
                    _child(pointer, "kind"),
                    "어떤 종류의 노드인지 kind로 골라 주세요 — "
                    f"missing='kind' allowed={sorted(k for k in kinds if k)}",
                )
            member = kinds.get(kind if isinstance(kind, str) else None)
            if member is None:
                closest_kind = _closest(kind, [k for k in kinds if k])
                return _issue(
                    issues,
                    "structure.unknown_kind",
                    _child(pointer, "kind"),
                    "모르는 kind입니다"
                    + _hint(closest_kind)
                    + f" — got={kind!r}{_hint_detail(closest_kind)} "
                    + f"allowed={sorted(k for k in kinds if k)}",
                )
            return _hydrate_dataclass(member, value, pointer, issues)
        if len(dataclass_members) == 1:
            return _hydrate_dataclass(dataclass_members[0], value, pointer, issues)
        raise TypeError(  # pragma: no cover - model authoring error
            f"union of dataclasses without kind discriminator at pointer={pointer!r}"
        )
    # scalar union (ParameterValue): keep bool/str, fold integral floats to int.
    scalar_members = tuple(m for m in members if m is not type(None))
    if isinstance(value, bool):
        if bool in scalar_members:
            return value
    elif isinstance(value, int):
        if int in scalar_members:
            return value
        if float in scalar_members:
            return float(value)
    elif isinstance(value, float):
        if int in scalar_members and value.is_integer():
            return int(value)
        if float in scalar_members:
            return value
    elif isinstance(value, str):
        if str in scalar_members:
            return value
    names = "|".join(getattr(m, "__name__", str(m)) for m in scalar_members)
    return _issue(
        issues,
        "structure.type_mismatch",
        pointer,
        f"값의 타입이 맞지 않습니다 — expected={names} got={value!r}",
    )


def _hydrate_sequence(
    item_tp: Any, value: object, pointer: str, issues: list[StructuralIssue]
) -> Any:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            "여기에는 목록이 와야 합니다. 항목마다 줄 앞에 `- `를 붙이세요 — "
            f"expected=sequence got={type(value).__name__}",
        )
    items = [
        _hydrate(item_tp, item, _child(pointer, index), issues) for index, item in enumerate(value)
    ]
    if any(item is _MISSING for item in items):
        return _MISSING
    return tuple(items)


def _hydrate_dataclass(tp: type, value: object, pointer: str, issues: list[StructuralIssue]) -> Any:
    if not isinstance(value, Mapping):
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            "여기에는 하위 항목을 가진 블록이 와야 합니다 — "
            f"expected=mapping got={type(value).__name__}",
        )
    hints = get_type_hints(tp)
    fields = {field.name: field for field in dataclasses.fields(tp)}
    failed = False
    for key in value:
        if key not in fields:
            closest_key = _closest(key, fields)
            _issue(
                issues,
                "structure.unknown_key",
                _child(pointer, key),
                "모르는 키입니다"
                + _hint(closest_key)
                + f" — got={key!r}{_hint_detail(closest_key)} allowed={sorted(fields)}",
            )
            failed = True
    kwargs: dict[str, Any] = {}
    derived_from: dict[str, str] = {}
    for name, field in fields.items():
        if name in value:
            hydrated = _hydrate(hints[name], value[name], _child(pointer, name), issues)
            if hydrated is _MISSING:
                failed = True
            else:
                kwargs[name] = hydrated
        elif "default-from" in field.metadata:
            # 파생 기본값(예: FactorSignal.label ← factor_id). 원천은 같은 dataclass의 필수 필드여야
            # 한다: 그래야 원천이 문서에 없을 때 그 필드의 missing_field 이슈가 실패를 만들고, 아래
            # 복사 단계가 KeyError 없이 성립한다. 어긋나면 사용자 문서가 아니라 모델 선언 오류다.
            source = str(field.metadata["default-from"])
            source_field = fields.get(source)
            if (
                source_field is None
                or source_field.default is not dataclasses.MISSING
                or source_field.default_factory is not dataclasses.MISSING
                or "default-from" in source_field.metadata
            ):
                raise TypeError(
                    "default-from must name a required field of the same dataclass — "
                    f"type={tp.__name__} field={name!r} source={source!r}"
                )
            derived_from[name] = source
        elif field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
            _issue(
                issues,
                "structure.missing_field",
                _child(pointer, name),
                f"필수 키가 없습니다 — missing={name!r} in={tp.__name__!r}",
            )
            failed = True
    if failed:
        return _MISSING
    for name, source in derived_from.items():
        kwargs[name] = kwargs[source]
    return tp(**kwargs)


def _hydrate_scalar(tp: Any, value: object, pointer: str, issues: list[StructuralIssue]) -> Any:
    if isinstance(tp, type) and issubclass(tp, Enum):
        if isinstance(value, tp):
            return value
        if isinstance(value, str):
            try:
                return tp(value)
            except ValueError:
                pass
        allowed = [member.value for member in tp]
        closest_value = _closest(value, allowed)
        return _issue(
            issues,
            "structure.invalid_enum",
            pointer,
            "고를 수 있는 값이 아닙니다"
            + _hint(closest_value)
            + f" — got={value!r}{_hint_detail(closest_value)} allowed={allowed!r}",
        )
    if tp is bool:
        if isinstance(value, bool):
            return value
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            f"참 또는 거짓(true·false)이 와야 합니다 — expected=bool got={value!r}",
        )
    if tp is int:
        if isinstance(value, bool):
            return _issue(
                issues,
                "structure.type_mismatch",
                pointer,
                f"정수가 와야 합니다 — expected=int got={value!r}",
            )
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            f"정수가 와야 합니다 — expected=int got={value!r}",
        )
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _issue(
                issues,
                "structure.type_mismatch",
                pointer,
                f"숫자가 와야 합니다 — expected=float got={value!r}",
            )
        try:
            return float(value)
        except OverflowError:
            return _issue(
                issues,
                "structure.type_mismatch",
                pointer,
                "정수가 너무 커서 숫자로 바꿀 수 없습니다 — "
                f"digits={len(str(value))} expected=float",
            )
    if tp is str:
        if isinstance(value, str):
            return value
        return _issue(
            issues,
            "structure.type_mismatch",
            pointer,
            f"문자열이 와야 합니다 — expected=str got={value!r}",
        )
    if tp is date:
        # datetime is a date subclass; a timestamp on a date field is a different value, not a date.
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        return _issue(
            issues,
            "structure.invalid_date",
            pointer,
            "날짜는 YYYY-MM-DD로 적어 주세요. 예: 2021-01-01 — "
            f"expected=YYYY-MM-DD example=2021-01-01 got={value!r}",
        )
    raise TypeError(  # pragma: no cover - model authoring error
        f"unsupported hydrate type {tp!r} at pointer={pointer!r}"
    )


def _kind_of(tp: type) -> str | None:
    hint = get_type_hints(tp).get("kind")
    if hint is not None and get_origin(hint) is Literal:
        return typing.cast(str, get_args(hint)[0])
    return None
