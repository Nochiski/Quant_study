"""모델이 읽는 문장을 골든 파일로 잠근다 (WORKFLOW A-07, 설계 spec D8).

시스템 프롬프트·도구 설명·고정 통지 문구는 코드가 아니라 **지시문**이다. 한 줄이 조용히
바뀌어도 타입 검사도 단위 테스트도 잡지 못하고, 결과는 모델이 도구를 부르는 순서나 제안의
모양이 달라지는 형태로 몇 주 뒤에 나타난다. 그래서 전문을 byte로 고정하고, 바뀌면 diff가
리뷰에 올라오게 한다.

골든의 owner는 `tools/export_assistant_prompts.py`의 `build_artifacts()` 하나다. 여기서 같은
문장을 다시 조립하면 owner가 둘이 되어 재생성 스크립트와 테스트가 따로 놀게 된다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest

from strategy_workbench.domain.assistant.facade.tools import ASSISTANT_TOOLS

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_DIR = _BACKEND_ROOT / "tools"


@pytest.fixture(scope="module")
def exporter() -> Iterator[ModuleType]:
    """`tools/`는 패키지가 아니라 실행 스크립트 디렉터리라 sys.path에 없다.

    `test_core_parity.py`가 벤치 스크립트를 읽을 때 쓰는 것과 같은 관례다.
    """
    path = str(_TOOLS_DIR)
    sys.path.insert(0, path)
    try:
        yield import_module("export_assistant_prompts")
    finally:
        sys.path.remove(path)


def test_every_prompt_artifact_matches_its_golden_file(exporter: ModuleType) -> None:
    for name, text in exporter.build_artifacts().items():
        golden = exporter.FIXTURE_DIR / name
        assert golden.read_text(encoding="utf-8") == text, (
            f"{name} is stale; regenerate with: {exporter.REGENERATE_COMMAND}"
        )


def test_no_golden_file_outlives_the_artifact_that_produced_it(exporter: ModuleType) -> None:
    """이름을 바꾸거나 없앤 산출물의 골든이 남아 있으면 다음 사람이 그것을 정본으로 읽는다."""
    on_disk = {path.name for path in exporter.FIXTURE_DIR.iterdir() if path.is_file()}

    assert on_disk == set(exporter.build_artifacts()), (
        f"unexpected files under {exporter.FIXTURE_DIR.name}/; "
        f"regenerate with: {exporter.REGENERATE_COMMAND}"
    )


def test_the_tool_golden_carries_every_declared_tool_with_its_strict_schema(
    exporter: ModuleType,
) -> None:
    """도구가 늘거나 스키마의 strict 조건이 빠지면 공급자가 임의 키를 조용히 통과시킨다."""
    entries = json.loads((exporter.FIXTURE_DIR / "tools.json").read_text(encoding="utf-8"))

    assert [entry["name"] for entry in entries] == [spec.name for spec in ASSISTANT_TOOLS]
    for entry in entries:
        assert entry["description"].strip(), entry["name"]
        assert entry["input_schema"]["additionalProperties"] is False, entry["name"]
        assert "required" in entry["input_schema"], entry["name"]


def test_the_system_prompt_golden_names_every_tool_the_model_may_call(
    exporter: ModuleType,
) -> None:
    """도구를 하나 더하고 프롬프트의 작업 순서를 안 고치면 모델이 그 도구를 영영 안 부른다."""
    prompt = (exporter.FIXTURE_DIR / "system_prompt.ko.md").read_text(encoding="utf-8")

    missing = [spec.name for spec in ASSISTANT_TOOLS if spec.name not in prompt]
    assert missing == []


def test_the_system_prompt_golden_states_the_quality_rules_of_the_design_spec(
    exporter: ModuleType,
) -> None:
    """spec D8이 요구하는 품질 항목이 프롬프트에서 사라지면 여기서 걸린다.

    문장 전문은 골든 파일이 고정한다. 여기서는 "그 규칙을 다루는 절이 있는가"만 본다 — 절
    제목까지 없어지는 것은 문장 다듬기가 아니라 규칙을 뺀 것이기 때문이다.
    """
    prompt = (exporter.FIXTURE_DIR / "system_prompt.ko.md").read_text(encoding="utf-8")

    headings = [line for line in prompt.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 일하는 순서",
        "## 지켜야 할 것",
        "## 무엇을 근거로 삼는가",
        "## 제안하는 법",
        "## 출처를 붙이는 법",
        "## 전략 문서 언어 요약",
    ]
    assert exporter.FIXTURE_TODAY.isoformat() in prompt


def test_the_notice_golden_is_a_flat_map_of_non_empty_korean_sentences(
    exporter: ModuleType,
) -> None:
    notices = json.loads((exporter.FIXTURE_DIR / "model_notices.json").read_text(encoding="utf-8"))

    assert notices
    for key, sentence in notices.items():
        assert isinstance(sentence, str) and sentence.strip(), key
