"""schema 1.0 → 1.1 source 텍스트 변환 (spec D3 source 경로, P1-04).

규칙은 domain `UPGRADE_STEPS` 하나뿐이다. 이 모듈은 그 step을 주석·순서를 보존하는 컨테이너 위에서
실행하고 다시 텍스트로 만드는 것만 맡는다.

- YAML: ruamel round-trip(`typ="rt"`) CST. `CommentedMap`/`CommentedSeq`는 `MutableMapping`/
  `MutableSequence`라 step이 그대로 적용된다. 따옴표·키 순서·독립 주석은 보존되고, 통째로 갈아끼운
  노드(`factors.factors` 안쪽 키)와 삭제된 키에 붙은 줄끝 주석은 그 줄과 함께 사라진다.
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

from strategy_workbench.domain.strategy.facade.document import (
    REMOVED_FIELDS,
    apply_upgrade_steps,
    upgrade_document_1_0,
)

_JSON_INDENT_PATTERN = re.compile(r"^( +)\S", re.MULTILINE)


def _round_trip_loader() -> YAML:
    loader = YAML(typ="rt")
    loader.preserve_quotes = True
    loader.width = 4096  # 긴 스칼라를 다시 접지 않는다
    # fixture·스니펫 관례: 시퀀스 항목은 부모 키보다 2칸 안쪽에 `- `를 둔다.
    loader.indent(mapping=2, sequence=4, offset=2)
    return loader


def _drop_orphaned_section_comments(document: CommentedMap) -> None:
    """삭제된 키 하나만 있던 섹션이 비면 그 키에 붙어 있던 주석 잔해를 함께 버린다.

    ruamel은 삭제된 키의 앞 주석을 빈 mapping 앞에 남겨 `{}`를 열 0에 찍는데, 그 텍스트는 다시
    parse되지 않는다. 주석의 대상(삭제된 필드)이 사라졌으므로 주석도 함께 사라지는 것이 맞다.
    """
    for section in {section for section, _key in REMOVED_FIELDS}:
        block = document.get(section)
        if isinstance(block, CommentedMap) and len(block) == 0:
            document[section] = CommentedMap()
            # 삭제된 첫 키 앞의 독립 주석은 부모의 comment slot에 남아 있다.
            document.ca.items.pop(section, None)


def upgrade_yaml_source(source: str) -> str:
    loader = _round_trip_loader()
    document = loader.load(source)
    if not isinstance(document, CommentedMap):
        raise ValueError(f"YAML source root must be a mapping — got={type(document).__name__}")
    apply_upgrade_steps(document)
    _drop_orphaned_section_comments(document)
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
