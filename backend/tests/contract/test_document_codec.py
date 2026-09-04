"""P1-02 document codec contract.

exact source hash, JSON-compatible tree, source map and fail-closed policy.
ADR: docs/superpowers/specs/2026-09-04-yaml-parser-adr.md
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.application.strategy_authoring.facade.ports import (
    YAML_GRAMMAR_REASONS,
    CodecLimits,
    DiagnosticKind,
    ParseStatus,
    SourceFormat,
    diagnostic_code,
    diagnostic_codes,
)
from strategy_workbench.domain.strategy.facade.document import hydrate_strategy_document
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    strategy_spec_hash,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
QUALITY_MOMENTUM_SPEC_HASH = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"


def _source(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


YAML_CODES = frozenset(f"yaml.{reason}" for reason in YAML_GRAMMAR_REASONS)


def _codec(**limits: int) -> RuamelDocumentCodec:
    return RuamelDocumentCodec(CodecLimits(**limits) if limits else None)


def test_yaml_with_comments_parses_to_the_same_tree_as_json() -> None:
    codec = _codec()
    yaml_doc = codec.parse(_source("quality_momentum.yaml"), format=SourceFormat.YAML)
    json_doc = codec.parse(_source("quality_momentum.json"), format=SourceFormat.JSON)

    assert yaml_doc.ok and json_doc.ok
    assert yaml_doc.tree is not None and json_doc.tree is not None
    yaml_tree = json.loads(json.dumps(yaml_doc.tree))
    assert yaml_tree["risk"] == {"max_name_weight": 0.05}
    assert yaml_tree["factors"]["factors"][0]["graph"]["nodes"][1]["window"] == 252
    # The JSON twin spells some defaults out; both hydrate to the same spec hash below.
    assert yaml_doc.tree["title"] == json_doc.tree["title"]


def test_codec_output_hydrates_to_the_golden_spec_hash() -> None:
    codec = _codec()
    identity = StrategyIdentity("draft", 0)
    for name, fmt in [
        ("quality_momentum.yaml", SourceFormat.YAML),
        ("quality_momentum.json", SourceFormat.JSON),
    ]:
        parsed = codec.parse(_source(name), format=fmt)
        assert parsed.tree is not None
        hydrated = hydrate_strategy_document(parsed.tree, identity=identity)
        assert hydrated.ok and hydrated.spec is not None, hydrated.issues
        assert strategy_spec_hash(hydrated.spec) == QUALITY_MOMENTUM_SPEC_HASH


def test_source_hash_is_the_exact_text_and_comments_change_it() -> None:
    codec = _codec()
    text = _source("quality_momentum.yaml")
    parsed = codec.parse(text, format=SourceFormat.YAML)
    assert parsed.source_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()

    with_comment = codec.parse(text + "# trailing comment\n", format=SourceFormat.YAML)
    assert with_comment.source_hash != parsed.source_hash
    assert with_comment.tree == parsed.tree


def test_source_map_points_at_the_exact_scalar_and_key() -> None:
    text = _source("quality_momentum.yaml")
    parsed = _codec().parse(text, format=SourceFormat.YAML)
    lines = text.splitlines()

    value_range = parsed.locate("/risk/max_name_weight")
    assert value_range is not None
    line = lines[value_range.start.line]
    assert line[value_range.start.column : value_range.end.column] == "0.05"
    assert text[value_range.start.offset : value_range.end.offset] == "0.05"

    key_range = parsed.key_ranges["/risk/max_name_weight"]
    assert lines[key_range.start.line][key_range.start.column : key_range.end.column] == (
        "max_name_weight"
    )

    node_range = parsed.locate("/factors/factors/0/graph/nodes/1/window")
    assert node_range is not None
    assert text[node_range.start.offset : node_range.end.offset] == "252"


def test_missing_pointer_falls_back_to_the_nearest_parent_range() -> None:
    parsed = _codec().parse(_source("quality_momentum.yaml"), format=SourceFormat.YAML)

    assert parsed.locate("/data/end_date") == parsed.value_ranges["/data"]
    assert (
        parsed.locate("/factors/factors/0/graph/nodes/9/kind")
        == parsed.value_ranges["/factors/factors/0/graph/nodes"]
    )
    assert parsed.locate("/nowhere/deep") == parsed.value_ranges[""]


def test_structural_issue_pointers_resolve_to_source_ranges() -> None:
    text = _source("quality_momentum.unknown_key.yaml")
    parsed = _codec().parse(text, format=SourceFormat.YAML)
    assert parsed.tree is not None

    hydrated = hydrate_strategy_document(parsed.tree, identity=StrategyIdentity("draft", 0))

    assert not hydrated.ok
    (issue,) = hydrated.issues
    assert issue.pointer == "/risk/max_name_wieght"
    # An unknown key exists in the source: the value range is located, the key range names it.
    located = parsed.locate(issue.pointer)
    assert located is not None
    assert text[located.start.offset : located.end.offset] == "0.05"
    key = parsed.key_ranges[issue.pointer]
    assert text[key.start.offset : key.end.offset] == "max_name_wieght"


@pytest.mark.parametrize(
    ("text", "code", "line", "column"),
    [
        ('title: "unterminated\ndata:\n', "yaml.syntax", 0, 7),
        ("a: [1, 2\nb: 3\n", "yaml.syntax", 0, 3),
        ("a: 1\na: 2\n", "document.duplicate_key", 1, 0),
        ("base: &b [1]\nc: *b\n", "yaml.anchor_or_alias", 0, 6),
        ("a: !custom 1\n", "yaml.tag", 0, 3),
        ("%YAML 1.1\n---\na: 1\n", "yaml.directive", 0, 0),
        ("a:\n  <<: {x: 1}\n", "yaml.merge_key", 1, 2),
        ("window: 1_000\n", "yaml.non_core_number", 0, 8),
        ("a: .nan\n", "document.non_finite_number", 0, 3),
        ("a: 9007199254740993\n", "document.integer_out_of_range", 0, 3),
        ("1: v\n", "document.non_string_key", 0, 0),
        ("a: 1\n---\nb: 2\n", "yaml.multiple_documents", 1, 0),
        ("- a\n", "document.not_a_mapping", 0, 0),
    ],
)
def test_rejections_carry_code_and_position(text: str, code: str, line: int, column: int) -> None:
    parsed = _codec().parse(text, format=SourceFormat.YAML)

    assert parsed.status is ParseStatus.REJECTED
    assert parsed.tree is None
    (diagnostic,) = parsed.diagnostics
    assert diagnostic.code == code
    assert diagnostic.kind is DiagnosticKind.SYNTAX
    assert diagnostic.range is not None
    assert (diagnostic.range.start.line, diagnostic.range.start.column) == (line, column)


def test_empty_document_is_rejected_without_a_range() -> None:
    parsed = _codec().parse("", format=SourceFormat.YAML)
    assert parsed.status is ParseStatus.REJECTED
    assert parsed.diagnostics[0].code == "document.not_a_mapping"


def test_json_syntax_errors_report_json_positions() -> None:
    parsed = _codec().parse('{"a": 1,\n "b": }', format=SourceFormat.JSON)

    assert parsed.status is ParseStatus.REJECTED
    (diagnostic,) = parsed.diagnostics
    assert diagnostic.code == "json.syntax"
    assert diagnostic.range is not None
    assert (diagnostic.range.start.line, diagnostic.range.start.column) == (1, 6)


def test_json_duplicate_keys_are_rejected_with_the_second_key_range() -> None:
    source = '{"a": 1,\n "a": 2}'
    parsed = _codec().parse(source, format=SourceFormat.JSON)

    (diagnostic,) = parsed.diagnostics
    assert diagnostic.code == "document.duplicate_key"
    assert diagnostic.pointer == "/a"
    assert diagnostic.range is not None
    assert (diagnostic.range.start.line, diagnostic.range.start.column) == (1, 1)

    nested = _codec().parse('{"x": {"a": 1, "b": 2, "a": 3}}', format=SourceFormat.JSON)
    assert nested.diagnostics[0].pointer == "/x/a"


def test_json_non_mapping_root_keeps_the_root_range() -> None:
    parsed = _codec().parse("[1, 2]", format=SourceFormat.JSON)

    assert parsed.diagnostics[0].code == "document.not_a_mapping"
    assert parsed.diagnostics[0].range is not None
    assert parsed.diagnostics[0].range.start.column == 0


def test_json_values_come_from_the_json_parser_not_ruamel() -> None:
    nan = _codec().parse('{"a": NaN, "b": Infinity}', format=SourceFormat.JSON)
    assert nan.diagnostics[0].code == "json.syntax"

    escaped = _codec().parse('{"title": "x\\ud83d\\ude00y"}', format=SourceFormat.JSON)
    assert escaped.ok and escaped.tree is not None
    assert escaped.tree["title"] == "x\U0001f600y"  # one astral code point, not two surrogates


def test_yaml_escaped_surrogate_pairs_become_one_code_point_and_lone_ones_fail() -> None:
    paired = _codec().parse('title: "x\\ud83d\\ude00y"\n', format=SourceFormat.YAML)
    assert paired.ok and paired.tree is not None
    assert paired.tree["title"] == "x\U0001f600y"

    lone = _codec().parse('title: "x\\ud83dy"\n', format=SourceFormat.YAML)
    assert lone.status is ParseStatus.REJECTED
    assert lone.diagnostics[0].code == "yaml.syntax"
    assert lone.diagnostics[0].pointer == "/title"

    key = _codec().parse('"\\ud83d\\ude00": 1\n', format=SourceFormat.YAML)
    assert key.ok and key.tree is not None
    assert set(key.tree) == {"\U0001f600"}
    assert "/\U0001f600" in key.value_ranges


def test_parser_internal_errors_are_rejections_not_exceptions() -> None:
    """ruamel asserts on `%YAML 1.3`; policy deferral means compose sees such documents."""
    parsed = _codec().parse("%YAML 1.3\n---\na: 1\n", format=SourceFormat.YAML)

    assert parsed.status is ParseStatus.REJECTED
    assert parsed.diagnostics[0].code in {"yaml.syntax", "yaml.directive"}


def test_duplicate_anchors_are_rejected_without_warnings(recwarn: pytest.WarningsRecorder) -> None:
    parsed = _codec().parse("a: &x 1\nb: &x 2\n", format=SourceFormat.YAML)

    assert parsed.diagnostics[0].code == "yaml.anchor_or_alias"
    assert not [w for w in recwarn if "anchor" in str(w.message).lower()]


def test_syntax_error_wins_over_an_earlier_policy_violation() -> None:
    parsed = _codec().parse('%YAML 1.2\n---\na: "unterminated\n', format=SourceFormat.YAML)
    assert parsed.diagnostics[0].code == "yaml.syntax"

    tagged = _codec().parse("a: !custom 1\nb: [1, 2\n", format=SourceFormat.YAML)
    assert tagged.diagnostics[0].code == "yaml.syntax"


def test_unpaired_surrogates_in_source_are_rejected_not_raised() -> None:
    parsed = _codec().parse('title: "\ud83d"\n', format=SourceFormat.YAML)

    assert parsed.status is ParseStatus.REJECTED
    assert parsed.diagnostics[0].code == "yaml.syntax"
    assert parsed.diagnostics[0].range is not None
    assert parsed.diagnostics[0].range.start.column == 8


@pytest.mark.parametrize(
    ("source", "fmt"),
    [
        ("a: " + "[" * 500 + "]" * 500 + "\n", SourceFormat.YAML),
        ("a: " + "{" * 500 + "}" * 500 + "\n", SourceFormat.YAML),
        (
            "".join("  " * i + f"k{i}:\n" for i in range(500)) + "  " * 500 + "v: 1\n",
            SourceFormat.YAML,
        ),
        ('{"a": ' + "[" * 500 + "]" * 500 + "}", SourceFormat.JSON),
    ],
    ids=["flow-seq", "flow-map", "block", "json"],
)
def test_deep_nesting_is_rejected_before_any_recursion(source: str, fmt: SourceFormat) -> None:
    parsed = _codec().parse(source, format=fmt)

    assert parsed.status is ParseStatus.REJECTED
    assert parsed.diagnostics[0].code == "document.too_deep"


def test_resource_limits_fail_closed() -> None:
    assert _codec(max_bytes=8).parse("title: 'x'\n", format=SourceFormat.YAML).diagnostics[
        0
    ].code == ("document.too_large")
    deep = "a:\n" + "".join("  " * i + "b:\n" for i in range(1, 6)) + "  " * 6 + "c: 1\n"
    assert _codec(max_depth=3).parse(deep, format=SourceFormat.YAML).diagnostics[0].code == (
        "document.too_deep"
    )
    many = "\n".join(f"k{i}: {i}" for i in range(50)) + "\n"
    assert _codec(max_nodes=20).parse(many, format=SourceFormat.YAML).diagnostics[0].code == (
        "document.too_many_nodes"
    )


def test_pointer_keys_are_rfc6901_escaped() -> None:
    parsed = _codec().parse('"a/b": 1\n"~x": 2\n', format=SourceFormat.YAML)
    assert parsed.ok
    assert set(parsed.value_ranges) == {"", "/a~1b", "/~0x"}


@pytest.mark.parametrize(
    ("source", "format", "code"),
    [
        ("{" + '"a":{' * 40 + "}" + "}" * 40, SourceFormat.JSON, "document.too_deep"),
        ('{"a":1,"a":2}', SourceFormat.JSON, "document.duplicate_key"),
        ("[1, 2]", SourceFormat.JSON, "document.not_a_mapping"),
        ('{"a": 9007199254740993}', SourceFormat.JSON, "document.integer_out_of_range"),
        ('{"a": NaN}', SourceFormat.JSON, "json.syntax"),
        ('{"a": "\ud800"}', SourceFormat.JSON, "json.syntax"),
        ("a: 1\na: 2\n", SourceFormat.YAML, "document.duplicate_key"),
        ("- a\n", SourceFormat.YAML, "document.not_a_mapping"),
        ("a: !custom 1\n", SourceFormat.YAML, "yaml.tag"),
        ("a: [1, 2\n", SourceFormat.YAML, "yaml.syntax"),
    ],
)
def test_the_format_decides_the_code_prefix(source: str, format: SourceFormat, code: str) -> None:
    """DEFECT-103: a JSON document used to be told `yaml.too_deep` because ruamel walks both
    formats. Policy shared by both formats is `document.*`; only YAML grammar keeps `yaml.*`."""
    parsed = _codec().parse(source, format=format)

    (diagnostic,) = parsed.diagnostics
    assert diagnostic.code == code


@pytest.mark.parametrize("format", list(SourceFormat), ids=lambda item: item.value)
def test_every_reported_code_is_declared_by_the_port(format: SourceFormat) -> None:
    """The port owns the code set, so a consumer can enumerate it before calling parse."""
    declared = diagnostic_codes(format)
    assert {code for code in declared if code.startswith("yaml.")} == (
        set() if format is SourceFormat.JSON else {"yaml.syntax"} | YAML_CODES
    )

    sources = [
        "",
        "- a\n",
        '{"a":1,"a":2}',
        "{" + '"a":{' * 40 + "}" + "}" * 40,
        '{"a": 9007199254740993}',
        '{"a": }',
        "a: !custom 1\n",
        "a: &b 1\nc: *b\n",
        "%YAML 1.1\n---\na: 1\n",
        "a:\n  <<: {x: 1}\n",
        "window: 1_000\n",
        "a: .nan\n",
        "1: v\n",
        "a: 1\n---\nb: 2\n",
        "x" * 600_000,
    ]
    reported = {
        diagnostic.code
        for source in sources
        for diagnostic in _codec().parse(source, format=format).diagnostics
    }

    assert reported, "no rejection reproduced — the probe stopped exercising the codec"
    assert reported <= declared, sorted(reported - declared)


def test_an_unregistered_reason_cannot_become_a_code() -> None:
    with pytest.raises(ValueError, match="rejection reason has no owner"):
        diagnostic_code("smuggled_probe", SourceFormat.YAML)
