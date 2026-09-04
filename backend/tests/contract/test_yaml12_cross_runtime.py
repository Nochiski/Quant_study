"""P0-03 YAML 1.2 cross-runtime contract (backend side), served by the P1-02 codec.

ADR: docs/superpowers/specs/2026-09-04-yaml-parser-adr.md

`tests/fixtures/strategy_documents/yaml12/manifest.json`의 case를 backend codec
(`adapters.outbound.document_codec`, ruamel.yaml YAML 1.2 pure safe loader)으로 읽어 accepted case는
기대 JSON과 같은 typed tree를, rejected case는 기대 reason을 내는지 검증한다. manifest가 담는 것은
prefix 없는 reason이며 wire code는 port(`diagnostic_code`)가 정한다 (DEFECT-103).
frontend는 같은 manifest를 `yaml` npm으로 검증한다
(`frontend/src/shared/lib/yaml12/__tests__/cross-runtime.test.ts`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.application.strategy_authoring.facade.ports import (
    SourceFormat,
    diagnostic_code,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents" / "yaml12"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
CODEC = RuamelDocumentCodec()


def _cases(expect: str) -> list[dict[str, Any]]:
    return [case for case in MANIFEST["cases"] if case["expect"] == expect]


def _read(case: dict[str, Any]) -> str:
    return (FIXTURES / case["file"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("case", _cases("accept"), ids=lambda case: case["name"])
def test_accepted_documents_match_expected_json_tree(case: dict[str, Any]) -> None:
    parsed = CODEC.parse(_read(case), format=SourceFormat.YAML)

    assert parsed.ok, parsed.diagnostics
    assert parsed.tree == case["json"]
    # JSON round-trip이 같아야 canonical bytes가 같다 (int 1 == float 1.0은 여기서 구분하지 않는다).
    assert json.loads(json.dumps(parsed.tree, ensure_ascii=False)) == case["json"]


@pytest.mark.parametrize("case", _cases("reject"), ids=lambda case: case["name"])
def test_rejected_documents_fail_closed_with_expected_reason(case: dict[str, Any]) -> None:
    parsed = CODEC.parse(_read(case), format=SourceFormat.YAML)

    assert not parsed.ok
    assert parsed.tree is None
    (diagnostic,) = parsed.diagnostics
    assert diagnostic.code == diagnostic_code(case["reason"], SourceFormat.YAML)


def test_manifest_covers_every_fixture_file() -> None:
    listed = {case["file"] for case in MANIFEST["cases"]}
    on_disk = {path.relative_to(FIXTURES).as_posix() for path in FIXTURES.glob("*/*.yaml")}
    assert listed == on_disk
