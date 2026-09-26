"""대본 공급자의 "새 전략" 제안이 현재 authoring schema로 깨끗이 compile되는지 고정한다.

브라우저 e2e(유저 스토리 US-DM-03)는 빈 새 전략에서 이 제안을 적용하고 백테스트까지 간다. 대본의
본문 섹션은 authoring schema를 따르므로 schema가 바뀌면(예: 1.2가 `data`·`execution`을 뺀다) 여기가
먼저 깨져야 한다. 그러지 않으면 e2e가 "검증에 실패한 제안" 문구로만 드러난다.
"""

from __future__ import annotations

import json

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.llm_scripted.facade.provider import idea_to_new_strategy
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import SourceFormat
from strategy_workbench.domain.assistant.facade.models import ToolCall, ToolResult
from strategy_workbench.domain.assistant.facade.tools import PROPOSE_STRATEGY
from strategy_workbench.domain.strategy._models import CURRENT_SCHEMA_VERSION


def test_the_new_strategy_idea_compiles_cleanly_from_the_new_strategy_starter() -> None:
    # 새 전략 화면의 시작 문서와 같은 모양(`NEW_STRATEGY_STARTER`, 버전은 runtime schema const).
    starter = f'schema_version: "{CURRENT_SCHEMA_VERSION}"\ntitle: ""\n'
    proposed: list[str] = []

    def execute(call: ToolCall) -> ToolResult:
        if call.name == PROPOSE_STRATEGY:
            source_text = call.arguments["source_text"]
            assert isinstance(source_text, str)
            proposed.append(source_text)
            return ToolResult(call_id=call.call_id, ok=True, content="{}")
        payload = {"source_text": starter, "source_format": "yaml", "diagnostics": []}
        return ToolResult(call_id=call.call_id, ok=True, content=json.dumps(payload))

    list(idea_to_new_strategy(execute))

    service = StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version="scripted-idea-test",
        dataset_snapshot_id=lambda: "scripted-idea-test",
    )
    compiled = service.compile(CompileRequest(proposed[0], SourceFormat.YAML))
    assert list(compiled.diagnostics) == []
    assert compiled.spec is not None
    assert compiled.schema_version == CURRENT_SCHEMA_VERSION
