"""사용자 팩터 라이브러리(US-CS-08, 2026-10-19 결정).

전략은 라이브러리를 참조하지 않고 **복사**한다: 꺼내 쓰면 그 버전의 팩터 항목이 전략 문서 `factors`
에 그대로 들어간다. 그래서 전략 언어·`spec_hash`·시도 키는 라이브러리를 모른다. 라이브러리는 버전을
지우거나 고치지 않고 쌓기만 하며, 버전 신원은 compile 이 정규화한 팩터 그래프와 그 그래프가 읽는
파라미터 정의의 정규 JSON 해시다 — 어느 전략에 어느 버전이 들어 있는지를 그래프 비교로 안다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from strategy_workbench.domain.canonical.facade.encoding import canonical_json_hash

# reason: 전략 문서의 팩터 항목·파라미터 정의는 열린 JSON 트리다(스키마는 runtime schema 가 소유).
JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class LibraryFactorVersion:
    """라이브러리 팩터의 한 버전. 만든 뒤 바뀌지 않는다."""

    version: int
    identity: str
    """정규화한 그래프·읽는 파라미터의 해시(`version_identity`)."""
    factor: JsonObject
    """전략 문서에 그대로 복사할 팩터 항목(`factor_id`·`label`·`direction`·`graph` …)."""
    parameters: tuple[JsonObject, ...]
    """그래프가 읽는 파라미터 정의. 꺼내 쓸 때 문서 `parameters` 에 함께 들어간다."""
    created_at: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise ValueError(f"library factor version must be a positive integer: {self.version!r}")
        if len(self.identity) != 64:
            raise ValueError(
                f"library factor identity must be a sha256 hex digest: {self.identity!r}"
            )


@dataclass(frozen=True)
class LibraryFactor:
    """이름 붙여 저장한 팩터와 그 버전들(오래된 것부터)."""

    library_id: str
    key: str
    """꺼내 쓸 때의 `factor_id`. 라이브러리 안에서 하나뿐이다."""
    name: str
    description: str
    hidden: bool
    created_at: datetime
    versions: tuple[LibraryFactorVersion, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                f"library factor name must not be blank — library_id={self.library_id}"
            )
        if not self.versions:
            raise ValueError(f"library factor has no version — library_id={self.library_id}")
        numbers = [version.version for version in self.versions]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(
                "library factor versions must be 1..n in order — "
                f"library_id={self.library_id} versions={numbers}"
            )

    @property
    def latest(self) -> LibraryFactorVersion:
        return self.versions[-1]


class FactorMatchStatus(StrEnum):
    """전략 문서의 팩터 항목이 라이브러리와 어떤 관계인가."""

    CURRENT = "current"
    """라이브러리 최신 버전과 그래프가 같다."""
    OUTDATED = "outdated"
    """예전 버전과 같고 새 버전이 있다."""
    MODIFIED = "modified"
    """같은 키로 꺼내 썼지만 그래프를 고쳐 어느 버전과도 같지 않다."""


@dataclass(frozen=True)
class FactorMatch:
    library_id: str
    status: FactorMatchStatus
    matched_version: int | None
    """그래프가 같은 버전. `MODIFIED` 면 없다."""
    latest_version: int


def read_parameter_ids(graph: JsonObject) -> tuple[str, ...]:
    """그래프의 `parameter` 노드가 읽는 파라미터 id(처음 나온 순서, 중복 없음)."""
    nodes = graph.get("nodes")
    if not isinstance(nodes, Sequence) or isinstance(nodes, str):
        return ()
    seen: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        parameter_id = node.get("parameter_id")
        if node.get("kind") == "parameter" and isinstance(parameter_id, str):
            if parameter_id not in seen:
                seen.append(parameter_id)
    return tuple(seen)


def read_field_ids(graph: JsonObject) -> tuple[str, ...]:
    """그래프의 `field` 노드가 읽는 원천 필드 id(처음 나온 순서, 중복 없음)."""
    nodes = graph.get("nodes")
    if not isinstance(nodes, Sequence) or isinstance(nodes, str):
        return ()
    seen: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        field_id = node.get("field_id")
        if node.get("kind") == "field" and isinstance(field_id, str) and field_id not in seen:
            seen.append(field_id)
    return tuple(seen)


def version_identity(graph: JsonObject, parameters: Sequence[JsonObject]) -> str:
    """정규화한(compile 이 낸) 그래프와 그 그래프가 읽는 파라미터 정의의 해시.

    파라미터는 id 순으로 정렬해 문서에 적힌 순서가 신원을 바꾸지 않게 한다. 그래프 노드의
    순서·이름은 그대로 넣는다 — 계산 순서와 참조 이름이 다르면 다른 정의로 본다.
    """
    read = set(read_parameter_ids(graph))
    used = sorted(
        (parameter for parameter in parameters if parameter.get("parameter_id") in read),
        key=lambda parameter: str(parameter.get("parameter_id")),
    )
    return canonical_json_hash({"graph": graph, "parameters": used})


def match_factor(key: str, identity: str, entries: Sequence[LibraryFactor]) -> FactorMatch | None:
    """문서 팩터 하나를 라이브러리와 맞춘다.

    같은 키의 항목이 있으면 그 항목과만 비교한다(꺼내 쓴 팩터는 키를 지닌다). 키가 다르면 그래프가
    같은 버전을 찾는다. 숨긴 항목은 비교하지 않는다. 어느 쪽도 아니면 None 이다.
    """
    visible = [entry for entry in entries if not entry.hidden]
    keyed = next((entry for entry in visible if entry.key == key), None)
    candidates = [keyed] if keyed is not None else visible
    for entry in candidates:
        same = next(
            (version for version in reversed(entry.versions) if version.identity == identity),
            None,
        )
        if same is not None:
            return FactorMatch(
                library_id=entry.library_id,
                status=(
                    FactorMatchStatus.CURRENT
                    if same.version == entry.latest.version
                    else FactorMatchStatus.OUTDATED
                ),
                matched_version=same.version,
                latest_version=entry.latest.version,
            )
    if keyed is not None:
        return FactorMatch(
            library_id=keyed.library_id,
            status=FactorMatchStatus.MODIFIED,
            matched_version=None,
            latest_version=keyed.latest.version,
        )
    return None
