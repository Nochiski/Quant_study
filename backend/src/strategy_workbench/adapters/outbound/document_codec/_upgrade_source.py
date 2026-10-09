"""schema 1.0 → 1.1 source 텍스트 변환 (spec D3 source 경로, P1-04).

규칙은 domain `UPGRADE_STEPS` 하나뿐이다. 이 모듈은 그 step을 주석·순서를 보존하는 컨테이너 위에서
실행하고 다시 텍스트로 만드는 것만 맡는다.

- YAML: ruamel round-trip(`typ="rt"`) CST. `CommentedMap`/`CommentedSeq`는 `MutableMapping`/
  `MutableSequence`라 step이 그대로 적용된다. 따옴표·키 순서·독립 주석은 보존된다. 삭제되는 키의
  줄끝 주석과 그 키 **위**의 독립 주석은 키와 함께 사라지고, 그 키 **아래**의 독립 주석(다음 키를
  설명하는 주석)은 자리를 지킨다. 이 규칙은 어떤 step이 어떤 mapping에서 키를 지우든 같다: 어댑터는
  step 적용 전후의 키 집합 차이로 삭제를 알아내며 삭제 목록을 따로 갖지 않는다(Phase 1 감사
  DEFECT-P1X-002). 시퀀스 항목 mapping의 **첫** 키가 지워질 때만 예외가 하나 있다: 그 위의 독립
  주석은 항목 슬롯이 소유해 (지워진 키를 설명하던 것이지만) 그대로 남고, 아래 주석은 다음 남는 키
  앞으로 옮겨진다. 통째로 갈아끼운 `factors.factors` 안쪽 키의 줄끝 주석은 바깥 키로 옮겨진다.
  줄바꿈은 LF로 통일되고 여러 줄 flow style은 한 줄로 접힌다.
- JSON: 주석이 없으므로 dict 변환 뒤 원문의 들여쓰기 폭으로 다시 직렬화한다.

전제: 호출자가 safe codec으로 parse에 성공했고 tree가 1.0임을 확인했다. 결과 텍스트가 dict 경로와
같은 tree를 내는지는 application이 다시 parse해 검사한다(drift fail-closed).
"""

from __future__ import annotations

import json
import re
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark
from ruamel.yaml.tokens import CommentToken

from strategy_workbench.domain.strategy.facade.document import (
    apply_upgrade_steps,
    upgrade_document_1_0,
)

_JSON_INDENT_PATTERN = re.compile(r"^( +)\S", re.MULTILINE)
_EOL_SLOT = (
    2  # ruamel `ca.items[key]` 슬롯: [pre, key-eol, value-eol+다음 키까지의 독립 주석, post]
)


def _round_trip_loader() -> YAML:
    loader = YAML(typ="rt")
    loader.preserve_quotes = True
    loader.width = 4096  # 긴 스칼라를 다시 접지 않는다
    # fixture·스니펫 관례: 시퀀스 항목은 부모 키보다 2칸 안쪽에 `- `를 둔다.
    loader.indent(mapping=2, sequence=4, offset=2)
    return loader


def _eol_token(owner: CommentedMap, key: str) -> CommentToken | None:
    slot = owner.ca.items.get(key)
    if slot is None or len(slot) <= _EOL_SLOT:
        return None
    token = slot[_EOL_SLOT]
    return token if isinstance(token, CommentToken) else None


def _split_token(token: CommentToken | None) -> tuple[str, str]:
    """(줄끝 주석, 그 아래 독립 주석 줄들). ruamel은 둘을 한 토큰에 개행으로 이어 담는다."""
    if token is None:
        return "", ""
    head, _newline, tail = token.value.partition("\n")
    return head, tail


def _column_of(owner: CommentedMap, key: str) -> int:
    token = _eol_token(owner, key)
    if token is not None and token.start_mark is not None:
        return int(token.start_mark.column)
    return 0


def _set_tail(owner: CommentedMap, key: str, tail: str) -> None:
    """`key` 줄의 줄끝 주석은 그대로 두고, 그 아래 독립 주석 줄들만 `tail`로 바꾼다."""
    token = _eol_token(owner, key)
    head, _old_tail = _split_token(token)
    if not head and not tail:
        if token is not None:
            owner.ca.items.pop(key, None)
        return
    value = f"{head}\n{tail}"
    if token is not None:
        token.value = value
        return
    owner.ca.items[key] = [
        None,
        None,
        CommentToken(value, CommentMark(_column_of(owner, key))),
        None,
    ]


class _MappingSnapshot:
    """step 적용 전의 mapping 하나.

    객체 자체(step은 제자리에서 지운다), 부모 mapping과 그 키, 키 순서를 기억한다.
    """

    __slots__ = ("block", "keys", "parent", "parent_key")

    def __init__(
        self, block: CommentedMap, parent: CommentedMap | None, parent_key: str | None
    ) -> None:
        self.block = block
        self.parent = parent
        self.parent_key = parent_key
        self.keys = [str(key) for key in block.keys()]


def _snapshot_mappings(
    node: object, parent: CommentedMap | None, parent_key: str | None
) -> list[_MappingSnapshot]:
    """문서 순서로 모든 mapping을 모은다. 시퀀스 안의 mapping은 부모 mapping이 없다(None)."""
    found: list[_MappingSnapshot] = []
    if isinstance(node, CommentedMap):
        found.append(_MappingSnapshot(node, parent, parent_key))
        for key, value in node.items():
            found.extend(_snapshot_mappings(value, node, str(key)))
    elif isinstance(node, CommentedSeq):
        for item in node:
            found.extend(_snapshot_mappings(item, None, None))
    return found


def _relocate_comments_of_removed_keys(snapshots: list[_MappingSnapshot]) -> None:
    """지워진 키 위의 독립 주석은 버리고, 아래의 독립 주석은 다음 키 앞에 남긴다.

    ruamel은 "어떤 키 줄부터 다음 키 직전까지"의 주석을 그 키의 슬롯에 담고, 키를 지워도 그 슬롯은
    `ca.items`에 남는다. 그래서 아무것도 안 하면 지워진 키 **아래**(다음 키를 설명하던) 주석이
    사라지고 **위**(지워진 키를 설명하던) 주석은 앞 키에 붙어 남는다 — 독자를 오도하는 반대 결과다.
    step 적용 뒤 mapping마다 "사라진 키의 연속 구간"을 찾아, 구간 마지막 키의 아래 주석을 구간 앞
    형제(없으면 부모 키)의 꼬리로 옮기고 그 형제의 원래 꼬리(지워진 키를 설명하던 주석)는 버린다.
    시퀀스 항목 mapping의 첫 키가 지워지면 부모 키가 없으므로 아래 주석을 다음 남는 키의 앞 주석
    슬롯에 넣는다(P1-06 리뷰 P2-001).
    """
    for snapshot in snapshots:
        block = snapshot.block
        keys = snapshot.keys
        index = 0
        while index < len(keys):
            if keys[index] in block:
                index += 1
                continue
            start = index
            while index < len(keys) and keys[index] not in block:
                index += 1
            _head, below = _split_token(_eol_token(block, keys[index - 1]))
            if start > 0:
                _set_tail(block, keys[start - 1], below)
            elif snapshot.parent is not None and snapshot.parent_key is not None:
                _set_tail(snapshot.parent, snapshot.parent_key, below)
            elif below and index < len(keys):
                _prepend_before_key(block, keys[index], below)
            # 남는 키가 하나도 없는 시퀀스 항목: 옮길 곳이 없어 아래 주석은 사라진다.


def _prepend_before_key(owner: CommentedMap, key: str, lines: str) -> None:
    """`key` 줄 앞에 독립 주석 줄들을 둔다(ruamel `ca.items[key]` 슬롯 [1] = 키 앞 주석 토큰들).

    `key`가 시퀀스 항목의 첫 키면 ruamel은 `-`만 있는 줄 뒤에 주석과 항목 내용을 다음 줄로 내려
    찍는다(`-` 단독 줄 + 원래 열의 주석 + 키). 유효한 YAML이고 reparse가 같으므로 그대로 둔다
    (P1-06 리뷰 nit 13).
    """
    moved = CommentToken(lines, CommentMark(0))
    slot = owner.ca.items.get(key)
    if slot is None:
        owner.ca.items[key] = [None, [moved], None, None]
        return
    existing = slot[1] if len(slot) > 1 and slot[1] is not None else []
    slot[1] = [moved, *existing]


def _finish_emptied_sections(
    document: CommentedMap, keys_before: dict[str, tuple[str, ...]]
) -> None:
    """이번 변환으로 비어 버린 섹션만 손본다.

    삭제 키만 있던 섹션은 빈 `CommentedMap`이 되는데, ruamel은 섹션 키 슬롯 꼬리의 독립 주석을 빈
    mapping 앞에 찍어 `{}`가 열 0에 오고 그 텍스트는 다시 parse되지 않는다. 그 꼬리는
    `_relocate_comments_of_removed_keys`가 남겨 둔 "다음 키를 설명하는 주석"이므로 다음 최상위 키의
    앞 주석으로 옮기고, 섹션 키의 줄끝 주석 토큰은 텍스트를 바꾸지 않고 그대로 둔다. 섹션이 문서의
    마지막 키면 옮길 곳이 없어 그 꼬리는 사라진다. 원래부터 비어 있던 섹션은 건드리지 않는다.
    """
    for section, before in keys_before.items():
        block = document.get(section)
        if not before or not isinstance(block, CommentedMap) or len(block) > 0:
            continue
        token = _eol_token(document, section)
        head, tail = _split_token(token)
        document[section] = CommentedMap()
        if token is not None:
            if head:
                token.value = (
                    f"{head}\n"  # 원본 토큰을 그대로 두면 `#`·`##` 같은 주석도 변형되지 않는다
                )
            else:
                # 줄끝 슬롯만 비운다. 슬롯을 통째로 pop하면 직전에 처리된 앞 섹션이 이 키의 앞 주석
                # 슬롯([1])으로 옮겨 둔 "다음 키 설명" 주석까지 사라진다.
                document.ca.items[section][_EOL_SLOT] = None
        if not tail:
            continue
        keys = [str(key) for key in document.keys()]
        following = keys[keys.index(section) + 1 :]
        if not following:
            continue
        _prepend_before_key(document, following[0], tail)


def upgrade_yaml_source(source: str) -> str:
    loader = _round_trip_loader()
    # ruamel은 주석 토큰 안의 CR을 그대로 두므로 CRLF 원문은 먼저 LF로 통일한다(출력은 항상 LF).
    document = loader.load(source.replace("\r\n", "\n"))
    if not isinstance(document, CommentedMap):
        raise ValueError(f"YAML source root must be a mapping — got={type(document).__name__}")
    # 문서 순서로 순회한다: set 순서(hash randomization)에 기대면 출력이 프로세스마다 달라진다.
    keys_before = {
        str(section): tuple(str(key) for key in block.keys())
        for section in document.keys()
        if isinstance(block := document.get(section), CommentedMap)
    }
    snapshots = _snapshot_mappings(document, None, None)
    apply_upgrade_steps(document)
    _relocate_comments_of_removed_keys(snapshots)
    _finish_emptied_sections(document, keys_before)
    buffer = StringIO()
    loader.dump(document, buffer)
    return buffer.getvalue()


def upgrade_json_source(source: str) -> str:
    tree = json.loads(source)
    if not isinstance(tree, dict):
        raise ValueError(f"JSON source root must be an object — got={type(tree).__name__}")
    match = _JSON_INDENT_PATTERN.search(source)
    indent = len(match.group(1)) if match and len(match.group(1)) in (2, 4) else 2
    text = json.dumps(upgrade_document_1_0(tree), ensure_ascii=False, indent=indent)
    return text + ("\n" if source.endswith("\n") else "")
