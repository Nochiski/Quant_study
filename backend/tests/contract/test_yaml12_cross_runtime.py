"""P0-03 YAML 1.2 cross-runtime contract (backend side).

ADR: docs/superpowers/specs/2026-09-04-yaml-parser-adr.md

`tests/fixtures/strategy_documents/yaml12/manifest.json`의 case를 backend parser(ruamel.yaml,
YAML 1.2, pure safe loader)로 읽어 accepted case는 기대 JSON과 같은 typed tree를, rejected case는
기대 reason code를 내는지 검증한다. frontend는 같은 manifest를 `yaml` npm으로 검증한다
(`frontend/src/shared/lib/yaml12/__tests__/cross-runtime.test.ts`).

`load_yaml12_mapping`은 ADR D2의 허용 문법을 구현한 임시 loader다. P1-02 codec이 source map과
diagnostic을 붙여 `adapters/outbound/yaml_codec`으로 옮기면 이 helper는 codec 호출로 바뀐다.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.composer import ComposerError
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError
from ruamel.yaml.tokens import (
    AliasToken,
    AnchorToken,
    DirectiveToken,
    ScalarToken,
    TagToken,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents" / "yaml12"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))

_UNDERSCORE_NUMBER = re.compile(r"^[+-]?[0-9][0-9_]*[0-9]$")
_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


class Yaml12Rejected(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"yaml 1.2 document rejected — reason={reason} detail={detail}")
        self.reason = reason


def _yaml() -> YAML:
    loader = YAML(typ="safe", pure=True)
    loader.version = (1, 2)
    # 날짜는 typed hydrate가 변환한다. parser 단계에서 date object가 되면 frontend(`yaml`)와 갈린다.
    loader.constructor.add_constructor(_TIMESTAMP_TAG, lambda _constructor, node: node.value)
    return loader


def _scan_policy(text: str) -> None:
    loader = _yaml()
    try:
        tokens = list(loader.scan(text))
    except YAMLError as error:
        raise Yaml12Rejected("syntax", str(error).splitlines()[0]) from error
    for token in tokens:
        if isinstance(token, DirectiveToken):
            raise Yaml12Rejected("directive", f"directive={token.name}")
        if isinstance(token, (AnchorToken, AliasToken)):
            raise Yaml12Rejected("anchor_or_alias", f"token={type(token).__name__}")
        if isinstance(token, TagToken):
            raise Yaml12Rejected("tag", f"tag={token.value}")
        if (
            isinstance(token, ScalarToken)
            and token.plain
            and "_" in token.value
            and _UNDERSCORE_NUMBER.match(token.value)
        ):
            raise Yaml12Rejected("ambiguous_number_underscore", f"scalar={token.value!r}")


def _check_tree(value: object, pointer: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise Yaml12Rejected("non_finite_number", f"pointer={pointer} value={value!r}")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise Yaml12Rejected("non_string_key", f"pointer={pointer} key={key!r}")
            _check_tree(item, f"{pointer}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_tree(item, f"{pointer}/{index}")


def load_yaml12_mapping(text: str) -> dict[str, Any]:
    _scan_policy(text)
    try:
        loaded = _yaml().load(text)
    except DuplicateKeyError as error:
        raise Yaml12Rejected("duplicate_key", str(error).splitlines()[0]) from error
    except ComposerError as error:
        if "single document" in str(error):
            raise Yaml12Rejected("multiple_documents", str(error).splitlines()[0]) from error
        raise Yaml12Rejected("syntax", str(error).splitlines()[0]) from error
    except YAMLError as error:
        raise Yaml12Rejected("syntax", str(error).splitlines()[0]) from error
    if not isinstance(loaded, dict):
        raise Yaml12Rejected("not_a_mapping", f"root={type(loaded).__name__}")
    _check_tree(loaded, "")
    return loaded


def _cases(expect: str) -> list[dict[str, Any]]:
    return [case for case in MANIFEST["cases"] if case["expect"] == expect]


def _read(case: dict[str, Any]) -> str:
    return (FIXTURES / case["file"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("case", _cases("accept"), ids=lambda case: case["name"])
def test_accepted_documents_match_expected_json_tree(case: dict[str, Any]) -> None:
    loaded = load_yaml12_mapping(_read(case))

    assert loaded == case["json"]
    # JSON round-trip이 같아야 canonical bytes가 같다 (int 1 == float 1.0은 여기서 구분하지 않는다).
    assert json.loads(json.dumps(loaded, ensure_ascii=False)) == case["json"]


@pytest.mark.parametrize("case", _cases("reject"), ids=lambda case: case["name"])
def test_rejected_documents_fail_closed_with_expected_reason(case: dict[str, Any]) -> None:
    with pytest.raises(Yaml12Rejected) as raised:
        load_yaml12_mapping(_read(case))

    assert raised.value.reason == case["reason"]


def test_manifest_covers_every_fixture_file() -> None:
    listed = {case["file"] for case in MANIFEST["cases"]}
    on_disk = {
        path.relative_to(FIXTURES).as_posix()
        for path in FIXTURES.glob("*/*.yaml")
    }
    assert listed == on_disk
