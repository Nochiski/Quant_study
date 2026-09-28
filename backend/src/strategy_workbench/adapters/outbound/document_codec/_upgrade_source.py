"""은퇴 schema source 텍스트를 현재 버전으로 올리는 변환 (spec D3·D7 source 경로, P1-04 → P2-09).

규칙은 domain 업그레이드 체인(`UPGRADE_STEPS`, `apply_upgrade_steps`) 하나뿐이다. 이 모듈은
그 체인을 주석·순서를 보존하는 컨테이너 위에서 실행하고 다시 텍스트로 만드는 것만 맡는다.

- YAML: ruamel round-trip(`typ="rt"`) CST. `CommentedMap`/`CommentedSeq`는 `MutableMapping`/
  `MutableSequence`라 step이 그대로 적용된다. 따옴표·키 순서·독립 주석은 보존된다. 삭제되는 키의
  줄끝 주석과 그 키 **위**의 독립 주석은 키와 함께 사라지고, 그 키 **아래**의 독립 주석(다음 키를
  설명하는 주석)은 자리를 지킨다. 이 규칙은 어떤 step이 어떤 mapping에서 키를 지우든 같다: 어댑터는
  step 적용 전후의 키 집합 차이로 삭제를 알아내며 삭제 목록을 따로 갖지 않는다(Phase 1 감사
  DEFECT-P1X-002). 시퀀스 항목 mapping의 **첫** 키가 지워질 때만 예외가 하나 있다: 그 위의 독립
  주석은 항목 슬롯이 소유해 (지워진 키를 설명하던 것이지만) 그대로 남고, 아래 주석은 다음 남는 키
  앞으로 옮겨진다. 통째로 갈아끼운 `factors.factors` 안쪽 키의 줄끝 주석은 바깥 키로 옮겨진다.
  섹션을 통째로 지울 때(1.1 → 1.2 의 `data`·`execution`)도 같은 규칙이다: 섹션 안 주석은 함께
  사라지고, 섹션이 끝난 뒤 다음 키를 설명하던 주석은 남는다. ruamel 은 그 주석을 섹션 **안 가장
  깊은 마지막 키**의 슬롯에 담으므로 꼬리를 읽고 쓸 때 그 키까지 내려간다(P2-09).
  줄바꿈은 LF로 통일되고 여러 줄 flow style은 한 줄로 접힌다.
- step 이 **새 키를 넣을 때**(1.1 → 1.2 의 `signal.normalization`, 없던 `signal` 섹션)도 주석이
  자리를 지킨다: 새 키 앞 키의 꼬리(다음 원래 키를 설명하던 주석)를 새 키 뒤로 옮긴다. 옮기지
  않으면 `portfolio` 를 설명하던 주석이 새 `signal:` 위에 붙어 독자를 오도한다.
- JSON: 주석이 없으므로 dict 변환 뒤 원문의 들여쓰기 폭으로 다시 직렬화한다.

전제: 호출자가 safe codec으로 parse에 성공했고 domain 이 업그레이드 가능하다고 판정했다. 결과
텍스트가 dict 경로와 같은 tree를 내는지는 application이 다시 parse해 검사한다(drift fail-closed).
`until` 은 체인 중간 버전에서 멈춘다 — 중간 단계 golden(`quality_momentum.v1_1.commented.yaml`)을
고정하는 데 쓴다.
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
    upgrade_document,
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
            # 줄끝 슬롯만 비운다. 슬롯을 통째로 pop 하면 앞 주석 슬롯([1])에 옮겨 둔 주석까지
            # 사라진다(`_finish_emptied_sections` 와 같은 이유).
            owner.ca.items[key][_EOL_SLOT] = None
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


def _tail_owner(block: CommentedMap, key: str, value: object) -> tuple[CommentedMap, str]:
    """`key` 의 값이 끝나는 줄의 주석 슬롯 주인.

    값이 비지 않은 mapping(또는 마지막 항목이 mapping 인 시퀀스)이면 ruamel 은 그 값이 끝난 뒤의
    주석을 가장 깊은 마지막 키의 슬롯에 담는다. `key` 자기 슬롯은 `key:` 줄 바로 뒤, 첫 자식 앞에
    찍히므로 거기 꼬리를 쓰면 주석이 섹션 머리로 올라간다. 마지막 항목이 스칼라인 시퀀스는 주석을
    시퀀스 슬롯에 담아 이 함수가 다루지 않는다 — 그때는 `key` 자기 슬롯을 돌려준다.
    """
    while True:
        if isinstance(value, CommentedSeq) and len(value) > 0:
            value = value[-1]
            if not isinstance(value, CommentedMap):
                return block, key
        if not isinstance(value, CommentedMap) or len(value) == 0:
            return block, key
        last = str(list(value.keys())[-1])
        block, key, value = value, last, value[last]


class _MappingSnapshot:
    """step 적용 전의 mapping 하나.

    객체 자체(step은 제자리에서 지운다), 부모 mapping과 그 키, 키 순서와 값(통째로 지워진 섹션의
    꼬리 주석을 찾으려고)을 기억한다.
    """

    __slots__ = ("block", "keys", "parent", "parent_key", "values")

    def __init__(
        self, block: CommentedMap, parent: CommentedMap | None, parent_key: str | None
    ) -> None:
        self.block = block
        self.parent = parent
        self.parent_key = parent_key
        self.keys = [str(key) for key in block.keys()]
        self.values: dict[str, object] = {str(key): value for key, value in block.items()}


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
            removed = keys[index - 1]
            owner, owner_key = _tail_owner(block, removed, snapshot.values[removed])
            _head, below = _split_token(_eol_token(owner, owner_key))
            if start > 0:
                previous = keys[start - 1]
                _set_tail(*_tail_owner(block, previous, block[previous]), below)
            elif snapshot.parent is not None and snapshot.parent_key is not None:
                _set_tail(snapshot.parent, snapshot.parent_key, below)
            elif below and index < len(keys):
                _prepend_before_key(block, keys[index], below)
            # 남는 키가 하나도 없는 시퀀스 항목: 옮길 곳이 없어 아래 주석은 사라진다.


def _carry_comments_past_inserted_keys(snapshots: list[_MappingSnapshot]) -> None:
    """step 이 넣은 새 키 뒤로, 새 키 앞 키의 꼬리 주석(다음 원래 키 설명)을 옮긴다.

    새 키 run 앞에 원래 키가 있으면 그 키 값이 끝나는 줄의 꼬리를, run 이 mapping 맨 앞이면 부모
    키 슬롯의 꼬리(첫 자식 앞 주석)를 run 의 마지막 새 키 뒤로 옮긴다. 새 값이 plain dict 면 block
    스타일로 찍히고 주석을 달 수 있게 `CommentedMap` 으로 바꾼다. 시퀀스 항목·문서 루트 맨 앞의
    run 은 옮길 꼬리가 없다(루트 맨 앞 주석은 문서 머리 주석이라 제자리에 둔다).
    """
    for snapshot in snapshots:
        block = snapshot.block
        original = set(snapshot.keys)
        current = [str(key) for key in block.keys()]
        for position, key in enumerate(current):
            if key in original:
                continue
            value = block[key]
            if isinstance(value, dict) and not isinstance(value, CommentedMap):
                value = CommentedMap(value)
                block[key] = value
            if position + 1 < len(current) and current[position + 1] not in original:
                continue  # run 의 마지막 새 키만 꼬리를 받는다
            run_start = position
            while run_start > 0 and current[run_start - 1] not in original:
                run_start -= 1
            if run_start > 0:
                previous = current[run_start - 1]
                source = _tail_owner(block, previous, block[previous])
            elif snapshot.parent is not None and snapshot.parent_key is not None:
                source = (snapshot.parent, snapshot.parent_key)
            else:
                continue
            _head, tail = _split_token(_eol_token(*source))
            if not tail:
                continue
            _set_tail(*source, "")
            _set_tail(*_tail_owner(block, key, value), tail)


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


def upgrade_yaml_source(source: str, *, until: str | None = None) -> str:
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
    apply_upgrade_steps(document, until=until)
    _relocate_comments_of_removed_keys(snapshots)
    _carry_comments_past_inserted_keys(snapshots)
    _finish_emptied_sections(document, keys_before)
    buffer = StringIO()
    loader.dump(document, buffer)
    return buffer.getvalue()


def upgrade_json_source(source: str, *, until: str | None = None) -> str:
    tree = json.loads(source)
    if not isinstance(tree, dict):
        raise ValueError(f"JSON source root must be an object — got={type(tree).__name__}")
    match = _JSON_INDENT_PATTERN.search(source)
    indent = len(match.group(1)) if match and len(match.group(1)) in (2, 4) else 2
    text = json.dumps(upgrade_document(tree, until=until).tree, ensure_ascii=False, indent=indent)
    return text + ("\n" if source.endswith("\n") else "")
