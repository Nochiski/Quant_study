"""ruamel.yaml based codec for YAML 1.2 / JSON authoring sources.

Implements the parser ADR (docs/superpowers/specs/2026-09-04-yaml-parser-adr.md):

- pure YAML 1.2 safe loader, timestamps kept as strings;
- scan-level policy (streamed, before any recursive compose): directives, anchors/aliases, tags,
  merge keys, numbers outside the YAML 1.2 core schema, and nesting deeper than the limit;
- compose-level policy: duplicate keys, non-string keys, non-finite numbers, integers beyond
  Number.isSafeInteger, non-mapping roots, node/byte limits, unpaired surrogates;
- JSON sources: `json.loads` provides positioned syntax errors, rejects NaN/Infinity and duplicate
  keys, and its value tree is the one returned; ruamel only contributes the source map so the two
  parsers can never disagree on values;
- every accepted node gets a JSON Pointer → SourceRange entry, keys included.

The same reason codes as the cross-runtime manifest are exposed as `yaml.<reason>`; JSON documents
add `json.syntax`. Untrusted input never raises out of `parse()`: every rejection is a diagnostic.
"""

from __future__ import annotations

import json
import math
import re
import warnings
from collections.abc import Iterator
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.composer import ComposerError
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from ruamel.yaml.tokens import (
    AliasToken,
    AnchorToken,
    BlockEndToken,
    BlockMappingStartToken,
    BlockSequenceStartToken,
    DirectiveToken,
    FlowMappingEndToken,
    FlowMappingStartToken,
    FlowSequenceEndToken,
    FlowSequenceStartToken,
    ScalarToken,
    TagToken,
)

from strategy_workbench.application.strategy_authoring.facade.ports import (
    CodecLimits,
    DiagnosticKind,
    DiagnosticSeverity,
    ParsedDocument,
    ParseStatus,
    SourceDiagnostic,
    SourceFormat,
    SourcePosition,
    SourceRange,
    source_hash_of,
)

_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
_MERGE_TAG = "tag:yaml.org,2002:merge"
_NUMBER_TAGS = ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float")
_CORE_INT = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_CORE_FLOAT = re.compile(r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$")
_NON_FINITE = re.compile(r"^(?:[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")
_SURROGATE = re.compile("[\ud800-\udfff]")
_MAX_SAFE_INTEGER = 2**53 - 1
_OPENING = (
    BlockMappingStartToken,
    BlockSequenceStartToken,
    FlowMappingStartToken,
    FlowSequenceStartToken,
)
_CLOSING = (BlockEndToken, FlowMappingEndToken, FlowSequenceEndToken)


class _Rejected(Exception):
    def __init__(
        self, code: str, message: str, pointer: str = "", range_: SourceRange | None = None
    ):
        super().__init__(message)
        self.code = code
        self.pointer = pointer
        self.range = range_


class _Counter:
    nodes: int = 0


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


class RuamelDocumentCodec:
    def __init__(self, limits: CodecLimits | None = None) -> None:
        self._limits = limits or CodecLimits()

    def parse(self, source: str, *, format: SourceFormat) -> ParsedDocument:
        try:
            self._check_encodable(source)
            source_hash = source_hash_of(source)
            self._check_size(source)
            json_duplicates: list[str] = []
            json_tree = (
                self._check_json_syntax(source, json_duplicates)
                if format is SourceFormat.JSON
                else None
            )
            loader = _loader()
            root = self._compose(source, loader)
            value_ranges: dict[str, SourceRange] = {}
            key_ranges: dict[str, SourceRange] = {}
            tree = self._walk(root, "", 0, value_ranges, key_ranges, _Counter(), loader)
            if not isinstance(tree, dict):
                raise _Rejected(
                    "yaml.not_a_mapping",
                    f"document root must be a mapping — got={type(tree).__name__}",
                    "",
                    _range_of(root),
                )
            if json_duplicates:  # pragma: no cover - _walk reports located duplicates first
                raise _Rejected("yaml.duplicate_key", f"duplicate key — key={json_duplicates[0]!r}")
            if format is SourceFormat.JSON:
                if not isinstance(json_tree, dict):
                    raise _Rejected(
                        "yaml.not_a_mapping",
                        f"document root must be a mapping — got={type(json_tree).__name__}",
                        "",
                        _range_of(root),
                    )
                tree = json_tree  # values come from the JSON parser; ruamel only mapped ranges
        except _Rejected as rejected:
            return ParsedDocument(
                status=ParseStatus.REJECTED,
                format=format,
                source_hash=_safe_hash(source),
                tree=None,
                value_ranges={},
                key_ranges={},
                diagnostics=(
                    SourceDiagnostic(
                        code=rejected.code,
                        kind=DiagnosticKind.SYNTAX,
                        pointer=rejected.pointer,
                        message=str(rejected),
                        severity=DiagnosticSeverity.ERROR,
                        range=rejected.range,
                    ),
                ),
            )
        return ParsedDocument(
            status=ParseStatus.OK,
            format=format,
            source_hash=source_hash,
            tree=tree,
            value_ranges=value_ranges,
            key_ranges=key_ranges,
            diagnostics=(),
        )

    # -- stages -------------------------------------------------------------------------------

    @staticmethod
    def _check_encodable(source: str) -> None:
        match = _SURROGATE.search(source)
        if match is not None:
            position = _offset_position(source, match.start())
            raise _Rejected(
                "yaml.syntax",
                "source is not valid UTF-8 text — unpaired surrogate "
                f"at line={position.line + 1} column={position.column + 1}",
                "",
                SourceRange(position, position),
            )

    def _check_size(self, source: str) -> None:
        size = len(source.encode("utf-8"))
        if size > self._limits.max_bytes:
            raise _Rejected(
                "yaml.too_large",
                f"source exceeds size limit — bytes={size} max_bytes={self._limits.max_bytes}",
            )

    def _check_json_syntax(self, source: str, duplicates: list[str]) -> Any:
        # reason: JSON value tree (dict/list/scalar) typed by json.loads
        def reject_constant(name: str) -> Any:  # reason: json hook signature
            raise ValueError(f"non-finite literal {name} is not valid JSON")

        def keep_first(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            # Duplicates are remembered here and reported by `_walk`, which knows the
            # key's source range; keep-first so the tree shape stays deterministic.
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    duplicates.append(key)
                    continue
                result[key] = value
            return result

        try:
            tree = json.loads(source, parse_constant=reject_constant, object_pairs_hook=keep_first)
        except json.JSONDecodeError as error:
            position = SourcePosition(error.lineno - 1, error.colno - 1, error.pos)
            raise _Rejected(
                "json.syntax",
                f"invalid JSON — {error.msg} (line={error.lineno} column={error.colno})",
                "",
                SourceRange(position, position),
            ) from error
        except ValueError as error:
            raise _Rejected("json.syntax", f"invalid JSON — {error}") from error
        except RecursionError as error:
            raise _Rejected(
                "yaml.too_deep",
                f"nesting exceeds limit — max_depth={self._limits.max_depth}",
            ) from error
        return tree

    def _compose(self, source: str, loader: YAML) -> Node:
        try:
            deferred = self._scan_policy(loader.scan(source), loader)
        except MarkedYAMLError as error:
            raise _Rejected(
                "yaml.syntax", _marked_message(error), "", _mark_range(error)
            ) from error
        except YAMLError as error:
            raise _Rejected("yaml.syntax", f"invalid YAML — {error}") from error
        try:
            with warnings.catch_warnings():
                # Policy violations (duplicate anchors, tags) are composed before being rejected
                # below; ruamel's advisory warnings about them are not server log material.
                warnings.simplefilter("ignore")
                root = loader.compose(source)
        except ComposerError as error:
            if "single document" in str(error):
                raise _Rejected(
                    "yaml.multiple_documents",
                    "source must contain exactly one YAML document",
                    "",
                    _problem_range(error),
                ) from error
            raise _Rejected(
                "yaml.syntax", _marked_message(error), "", _mark_range(error)
            ) from error
        except MarkedYAMLError as error:
            raise _Rejected(
                "yaml.syntax", _marked_message(error), "", _mark_range(error)
            ) from error
        except YAMLError as error:
            raise _Rejected("yaml.syntax", f"invalid YAML — {error}") from error
        except RecursionError as error:  # pragma: no cover - scan depth guard runs first
            raise _Rejected(
                "yaml.too_deep", f"nesting exceeds limit — max_depth={self._limits.max_depth}"
            ) from error
        except Exception as error:  # noqa: BLE001  # reason: untrusted input must never raise
            # ruamel's pure parser uses bare asserts (e.g. `%YAML 1.3` version setter); any
            # non-YAMLError escaping compose is still a rejected document, not a server fault.
            raise _Rejected(
                "yaml.syntax",
                f"invalid YAML — parser error {type(error).__name__}: {error}",
            ) from error
        if deferred is not None:
            raise deferred  # policy violation reported only after compose found no syntax error
        if root is None:
            raise _Rejected("yaml.not_a_mapping", "document is empty — expected a mapping")
        return root

    def _scan_policy(self, tokens: Iterator[object], loader: YAML) -> _Rejected | None:
        """Streamed token policy.

        Depth violations stop immediately so deep input never composes. Other policy
        violations are returned to the caller, which raises them only after compose succeeded,
        so a syntax error anywhere in the document wins (ADR D2: reason order matches the
        frontend parser, which reports syntax before directive/tag/anchor policy).
        """
        depth = 0
        first: _Rejected | None = None
        for token in tokens:
            if isinstance(token, _OPENING):
                depth += 1
                if depth > self._limits.max_depth + 1:
                    raise _Rejected(
                        "yaml.too_deep",
                        f"nesting exceeds limit — depth={depth - 1} "
                        f"max_depth={self._limits.max_depth}",
                        "",
                        _token_range(token),
                    )
            elif isinstance(token, _CLOSING):
                depth -= 1
            elif first is not None:
                continue
            elif isinstance(token, DirectiveToken):
                first = _Rejected(
                    "yaml.directive",
                    f"directives are not allowed — directive=%{token.name}",
                    "",
                    _token_range(token),
                )
            elif isinstance(token, (AnchorToken, AliasToken)):
                first = _Rejected(
                    "yaml.anchor_or_alias",
                    f"anchors and aliases are not allowed — token={type(token).__name__}",
                    "",
                    _token_range(token),
                )
            elif isinstance(token, TagToken):
                first = _Rejected(
                    "yaml.tag", f"tags are not allowed — tag={token.value}", "", _token_range(token)
                )
            elif isinstance(token, ScalarToken) and token.plain:
                tag = loader.resolver.resolve(ScalarNode, token.value, (True, False))
                if tag == _MERGE_TAG:
                    first = _Rejected(
                        "yaml.merge_key", "merge keys (<<) are not allowed", "", _token_range(token)
                    )
                    continue
                core_number = bool(_CORE_INT.match(token.value) or _CORE_FLOAT.match(token.value))
                resolver_number = tag in _NUMBER_TAGS and not _NON_FINITE.match(token.value)
                if resolver_number != core_number:
                    first = _Rejected(
                        "yaml.non_core_number",
                        "number literal outside the YAML 1.2 core schema — "
                        f"scalar={token.value!r} (frontend and backend would disagree)",
                        "",
                        _token_range(token),
                    )
        return first

    def _walk(
        self,
        node: Node,
        pointer: str,
        depth: int,
        value_ranges: dict[str, SourceRange],
        key_ranges: dict[str, SourceRange],
        counter: _Counter,
        loader: YAML,
    ) -> Any:  # reason: JSON-compatible tree (dict/list/scalar) built generically from ruamel nodes
        counter.nodes += 1
        if counter.nodes > self._limits.max_nodes:
            raise _Rejected(
                "yaml.too_many_nodes",
                f"node count exceeds limit — max_nodes={self._limits.max_nodes}",
                pointer,
                _range_of(node),
            )
        if depth > self._limits.max_depth:
            raise _Rejected(
                "yaml.too_deep",
                f"nesting exceeds limit — depth={depth} max_depth={self._limits.max_depth}",
                pointer,
                _range_of(node),
            )
        value_ranges[pointer] = _range_of(node)
        if isinstance(node, MappingNode):
            result: dict[str, Any] = {}
            for key_node, value_node in node.value:
                key = loader.constructor.construct_object(key_node, deep=True)
                if isinstance(key, str):
                    key = _merge_surrogates(key, pointer, key_node)
                if not isinstance(key, str):
                    raise _Rejected(
                        "yaml.non_string_key",
                        f"mapping keys must be strings — got={key!r}",
                        pointer,
                        _range_of(key_node),
                    )
                child = f"{pointer}/{_escape(key)}"
                if key in result:
                    raise _Rejected(
                        "yaml.duplicate_key",
                        f"duplicate key — key={key!r}",
                        child,
                        _range_of(key_node),
                    )
                key_ranges[child] = _range_of(key_node)
                result[key] = self._walk(
                    value_node, child, depth + 1, value_ranges, key_ranges, counter, loader
                )
            return result
        if isinstance(node, SequenceNode):
            return [
                self._walk(
                    item, f"{pointer}/{index}", depth + 1, value_ranges, key_ranges, counter, loader
                )
                for index, item in enumerate(node.value)
            ]
        if isinstance(node, ScalarNode):
            value = loader.constructor.construct_object(node, deep=True)
            if isinstance(value, str):
                return _merge_surrogates(value, pointer, node)
            if isinstance(value, float) and not math.isfinite(value):
                raise _Rejected(
                    "yaml.non_finite_number",
                    f"non-finite numbers are not allowed — value={node.value!r}",
                    pointer,
                    _range_of(node),
                )
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and abs(value) > _MAX_SAFE_INTEGER
            ):
                raise _Rejected(
                    "yaml.integer_out_of_range",
                    f"integer exceeds 2^53-1 — value={value}",
                    pointer,
                    _range_of(node),
                )
            if isinstance(value, bytes):  # pragma: no cover - tags are rejected at scan time
                raise _Rejected(
                    "yaml.tag", "binary values are not allowed", pointer, _range_of(node)
                )
            return value
        raise _Rejected(  # pragma: no cover - ruamel node kinds are exhaustive
            "yaml.syntax",
            f"unsupported node — type={type(node).__name__}",
            pointer,
            _range_of(node),
        )


def _merge_surrogates(value: str, pointer: str, node: Node) -> str:
    """Escaped surrogate pairs (`\\ud83d\\ude00`) become one code point, as JSON.parse and
    json.loads do; a lone surrogate is not encodable text and is rejected."""
    if _SURROGATE.search(value) is None:
        return value
    try:
        return value.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeDecodeError as error:
        raise _Rejected(
            "yaml.syntax",
            f"string contains an unpaired surrogate escape — pointer={pointer!r}",
            pointer,
            _range_of(node),
        ) from error


def _loader() -> YAML:
    loader = YAML(typ="safe", pure=True)
    loader.version = (1, 2)
    loader.constructor.add_constructor(_TIMESTAMP_TAG, lambda _constructor, node: node.value)
    return loader


def _safe_hash(source: str) -> str:
    try:
        return source_hash_of(source)
    except UnicodeEncodeError:
        return source_hash_of(source.encode("utf-8", "replace").decode("utf-8"))


def _offset_position(source: str, offset: int) -> SourcePosition:
    line = source.count("\n", 0, offset)
    column = offset - (source.rfind("\n", 0, offset) + 1)
    return SourcePosition(line=line, column=column, offset=offset)


def _position(mark: Any) -> SourcePosition:  # reason: ruamel Mark objects are untyped
    return SourcePosition(line=mark.line, column=mark.column, offset=mark.index)


def _range_of(node: Node) -> SourceRange:
    return SourceRange(_position(node.start_mark), _position(node.end_mark))


def _token_range(token: Any) -> SourceRange:  # reason: ruamel Token objects are untyped
    return SourceRange(_position(token.start_mark), _position(token.end_mark))


def _problem_range(error: MarkedYAMLError) -> SourceRange | None:
    mark = error.problem_mark or error.context_mark
    if mark is None:
        return None
    position = _position(mark)
    return SourceRange(position, position)


def _mark_range(error: MarkedYAMLError) -> SourceRange | None:
    """Span from the construct that was being parsed (context) to where parsing failed (problem)."""
    problem = error.problem_mark
    context = error.context_mark
    if problem is None and context is None:
        return None
    start = _position(context if context is not None else problem)
    end = _position(problem if problem is not None else context)
    if (end.line, end.column) < (start.line, start.column):
        start, end = end, start
    return SourceRange(start, end)


def _marked_message(error: MarkedYAMLError) -> str:
    mark = error.problem_mark or error.context_mark
    where = f" at line={mark.line + 1} column={mark.column + 1}" if mark is not None else ""
    problem = error.problem or error.context or "invalid YAML"
    return f"invalid YAML — {problem}{where}"
