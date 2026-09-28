"""Semantic diff between two StrategySpecs (WORKFLOW P1-08).

The diff is computed over the canonical payloads (`canonical_strategy_payload`), so it sees
exactly what `spec_hash` sees: identity is excluded by construction, source comments and
formatting never appear, `schema_version` is compared like any other field, and array positions
keep the canonical order semantics (an item moved is reported as changes at its old and new
positions, never as a rename).

예외 하나(BACKLOG-014): compile 이 boolean 출력 끝에 붙인 승격 노드(P2-07)는 걷어 내고 비교한다
(`demote_boolean_factor_outputs`). 승격은 사용자 그래프의 함수라 걷어 낸 payload 가 같으면
`spec_hash` 도 같으므로 "변경 없음" 판정은 그대로이고, 표는 사용자가 쓴 변경만 말한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ._canonical import canonical_strategy_json
from ._models import StrategySpec
from ._promotion import demote_boolean_factor_outputs


class DiffKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


@dataclass(frozen=True)
class DiffEntry:
    """One leaf-level difference at a JSON Pointer of the canonical payload.

    `before`/`after` are JSON values (scalars, or a whole subtree when a key or array item was
    added/removed). `changed` never carries containers: nested differences are reported per
    leaf so an editor can highlight exact ranges.
    """

    pointer: str
    kind: DiffKind
    before: object
    after: object


def diff_strategy_specs(base: StrategySpec, target: StrategySpec) -> tuple[DiffEntry, ...]:
    before = json.loads(canonical_strategy_json(demote_boolean_factor_outputs(base)))
    after = json.loads(canonical_strategy_json(demote_boolean_factor_outputs(target)))
    entries: list[DiffEntry] = []
    _walk(before, after, "", entries)
    return tuple(entries)


def _walk(before: Any, after: Any, pointer: str, entries: list[DiffEntry]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{pointer}/{_escape(key)}"
            if key not in before:
                entries.append(DiffEntry(child, DiffKind.ADDED, None, after[key]))
            elif key not in after:
                entries.append(DiffEntry(child, DiffKind.REMOVED, before[key], None))
            else:
                _walk(before[key], after[key], child, entries)
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            child = f"{pointer}/{index}"
            if index >= len(before):
                entries.append(DiffEntry(child, DiffKind.ADDED, None, after[index]))
            elif index >= len(after):
                entries.append(DiffEntry(child, DiffKind.REMOVED, before[index], None))
            else:
                _walk(before[index], after[index], child, entries)
        return
    if before != after or type(before) is not type(after):
        entries.append(DiffEntry(pointer, DiffKind.CHANGED, before, after))


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")
