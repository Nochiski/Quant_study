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
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.tokens import (
    AliasToken,
    AnchorToken,
    DirectiveToken,
    ScalarToken,
    TagToken,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents" / "yaml12"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))

_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
_MERGE_TAG = "tag:yaml.org,2002:merge"
_NUMBER_TAGS = ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float")
# YAML 1.2 core schema 숫자 표기. ruamel resolver는 1.1 잔재(`_` 구분자, `0b`)도 숫자로 읽지만
# frontend(`yaml` core schema)는 문자열로 읽으므로 core 밖 표기는 거부한다.
_CORE_INT = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_CORE_FLOAT = re.compile(r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$")
_MAX_SAFE_INTEGER = 2**53 - 1  # frontend Number.isSafeInteger와 맞춘다.
_NON_FINITE = re.compile(r"^(?:[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")


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
        if isinstance(token, ScalarToken) and token.plain:
            tag = loader.resolver.resolve(ScalarNode, token.value, (True, False))
            if tag == _MERGE_TAG:
                raise Yaml12Rejected("merge_key", f"scalar={token.value!r}")
            is_core_number = bool(_CORE_INT.match(token.value) or _CORE_FLOAT.match(token.value))
            resolver_number = tag in _NUMBER_TAGS and not _NON_FINITE.match(token.value)
            # resolver 판정과 core schema가 어느 방향으로든 어긋나면 frontend(core)와 tree가 갈린다.
            # 예: `1_000.5`(resolver float, core 아님), `.5e3`(resolver str, core float).
            if resolver_number != is_core_number:
                raise Yaml12Rejected("non_core_number", f"scalar={token.value!r} tag={tag}")


def _check_tree(value: object, pointer: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise Yaml12Rejected("non_finite_number", f"pointer={pointer} value={value!r}")
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > _MAX_SAFE_INTEGER:
        raise Yaml12Rejected("integer_out_of_range", f"pointer={pointer} value={value!r}")
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
        # 임시 문자열 매칭. P1-02 codec은 compose_all 문서 수로 판정한다.
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
    on_disk = {path.relative_to(FIXTURES).as_posix() for path in FIXTURES.glob("*/*.yaml")}
    assert listed == on_disk
