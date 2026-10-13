"""작성 문서 fixture도 운영 YAML 1.2 codec으로 읽는다."""

from typing import Any

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.domain.strategy.facade.document import SourceFormat


def load_authoring_yaml(source: str) -> dict[str, Any]:
    # 테스트가 서로 다른 문서 판본의 중첩 fixture를 직접 수정하므로 Any 값을 허용한다.
    parsed = RuamelDocumentCodec().parse(source, format=SourceFormat.YAML)
    assert parsed.ok, parsed.diagnostics
    assert isinstance(parsed.tree, dict)
    return parsed.tree
