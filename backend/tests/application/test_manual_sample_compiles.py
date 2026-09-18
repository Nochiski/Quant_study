"""사용자 매뉴얼 1절의 첫 YAML 샘플이 현재 스키마로 깨끗이 compile되는지 고정한다.

스키마 1.0 → 1.1 전환 때 매뉴얼을 묶는 게이트가 없어 첫 실습 샘플이 조용히 낡았다(Phase 5 감사
DEFECT-P5X-002, #143 리뷰 회귀 방지 권고). 매뉴얼의 첫 ```yaml 블록을 그대로 뽑아 compile한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import SourceFormat
from strategy_workbench.domain.strategy._models import CURRENT_SCHEMA_VERSION

MANUAL = (
    Path(__file__).resolve().parents[3] / "docs" / "manual" / "strategy-workbench" / "README.md"
)


def _first_yaml_block(markdown: str) -> str:
    """1절("## 1.") 이후의 첫 ```yaml 블록.

    앞 절에 다른 YAML이 끼어도 대상이 바뀌지 않는다(#147 리뷰 P2-3).
    """
    section = markdown[markdown.index("\n## 1. ") :]
    blocks = re.findall(r"```yaml\n(.*?)```", section, re.S)
    assert blocks, "매뉴얼 1절에 ```yaml 블록이 없다"
    assert 'title: "사용자 매뉴얼 모멘텀"' in blocks[0], "1절 첫 블록이 실습 샘플이 아니다"
    return blocks[0]


def test_manual_first_sample_compiles_cleanly_on_the_current_schema() -> None:
    sample = _first_yaml_block(MANUAL.read_text(encoding="utf-8"))
    service = StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version="manual-test",
        dataset_snapshot_id=lambda: "manual-test",
    )
    compiled = service.compile(CompileRequest(sample, SourceFormat.YAML))
    assert list(compiled.diagnostics) == []
    assert compiled.spec is not None
    assert compiled.schema_version == CURRENT_SCHEMA_VERSION
