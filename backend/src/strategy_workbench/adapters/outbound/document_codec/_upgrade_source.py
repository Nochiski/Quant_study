"""schema 1.0 → 1.1 source 텍스트 변환 (spec D3 source 경로, P1-04).

규칙은 domain `UPGRADE_STEPS` 하나뿐이다. 이 모듈은 그 step을 주석·순서를 보존하는 컨테이너 위에서
실행하고 다시 텍스트로 만드는 것만 맡는다.

- YAML: ruamel round-trip(`typ="rt"`) CST. `CommentedMap`/`CommentedSeq`는 `MutableMapping`/
  `MutableSequence`라 step이 그대로 적용된다. 따옴표·키 순서·독립 주석은 보존된다. 삭제되는 키의
  줄끝 주석과 그 키 **위**의 독립 주석은 키와 함께 사라지고, 그 키 **아래**의 독립 주석(다음 키를
  설명하는 주석)은 자리를 지킨다. 통째로 갈아끼운 `factors.factors` 안쪽 키의 줄끝 주석은 바깥 키로
  옮겨진다. 줄바꿈은 LF로 통일되고 여러 줄 flow style은 한 줄로 접힌다.
- JSON: 주석이 없으므로 dict 변환 뒤 원문의 들여쓰기 폭으로 다시 직렬화한다.

전제: 호출자가 safe codec으로 parse에 성공했고 tree가 1.0임을 확인했다. 결과 텍스트가 dict 경로와
같은 tree를 내는지는 application이 다시 parse해 검사한다(drift fail-closed).
"""

from __future__ import annotations

import json
import re
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import CommentMark
from ruamel.yaml.tokens import CommentToken

from strategy_workbench.domain.strategy.facade.document import (
    REMOVED_FIELDS,
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


def _relocate_comments_of_removed_keys(document: CommentedMap) -> None:
    """삭제될 키 위의 독립 주석은 버리고, 아래의 독립 주석은 다음 키 앞에 남긴다.

    ruamel은 "어떤 키 줄부터 다음 키 직전까지"의 주석을 그 키의 슬롯에 담는다. 그래서 그대로
    `del`하면 삭제 키 **아래**(다음 키를 설명하던) 주석이 사라지고 **위**(삭제 키를 설명하던) 주석은
    앞 키에 붙어 남는다 — 독자를 오도하는 반대 결과다. 삭제 전에 두 묶음을 맞바꾼다.
    """
    sections = sorted({section for section, _key in REMOVED_FIELDS})
    for section in sections:
        block = document.get(section)
        if not isinstance(block, CommentedMap):
            continue
        keys = [str(key) for key in block.keys()]
        removed = [key for s, key in REMOVED_FIELDS if s == section and key in block]
        # 뒤에서부터: 앞 형제도 삭제 대상이면 그 형제의 꼬리로 옮긴 주석을 이어서 다시 넘겨야 한다.
        for key in sorted(removed, key=keys.index, reverse=True):
            index = keys.index(key)
            _head, below = _split_token(_eol_token(block, key))
            if index > 0:
                # 삭제 키 위의 주석은 앞 형제 키의 슬롯 꼬리에 있다: 버리고 아래 주석으로 바꾼다.
                _set_tail(block, keys[index - 1], below)
            else:
                # 첫 키: 위의 주석은 섹션 키의 슬롯 꼬리에 있다.
                _set_tail(document, section, below)


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
                document.ca.items.pop(section, None)
        if not tail:
            continue
        keys = [str(key) for key in document.keys()]
        following = keys[keys.index(section) + 1 :]
        if not following:
            continue
        next_key = following[0]
        moved = CommentToken(tail, CommentMark(0))
        slot = document.ca.items.get(next_key)
        if slot is None:
            document.ca.items[next_key] = [None, [moved], None, None]
        else:
            existing = slot[1] if len(slot) > 1 and slot[1] is not None else []
            slot[1] = [moved, *existing]


def upgrade_yaml_source(source: str) -> str:
    loader = _round_trip_loader()
    # ruamel은 주석 토큰 안의 CR을 그대로 두므로 CRLF 원문은 먼저 LF로 통일한다(출력은 항상 LF).
    document = loader.load(source.replace("\r\n", "\n"))
    if not isinstance(document, CommentedMap):
        raise ValueError(f"YAML source root must be a mapping — got={type(document).__name__}")
    keys_before = {
        section: tuple(str(key) for key in block.keys())
        for section in {section for section, _key in REMOVED_FIELDS}
        if isinstance(block := document.get(section), CommentedMap)
    }
    _relocate_comments_of_removed_keys(document)
    apply_upgrade_steps(document)
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
