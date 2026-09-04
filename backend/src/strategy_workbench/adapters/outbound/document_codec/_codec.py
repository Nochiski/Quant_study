"""ruamel.yaml based codec for YAML 1.2 / JSON authoring sources.

Implements the parser ADR (docs/superpowers/specs/2026-09-04-yaml-parser-adr.md):

- pure YAML 1.2 safe loader, timestamps kept as strings;
- scan-level policy: directives, anchors/aliases, tags, merge keys and numbers outside the YAML 1.2
  core schema are rejected before composing;
- compose-level policy: duplicate keys, non-string keys, non-finite numbers, integers beyond
  Number.isSafeInteger, non-mapping roots, depth/node/byte limits;
- every accepted node gets a JSON Pointer → SourceRange entry, keys included.

The same reason codes as the cross-runtime manifest are exposed as `yaml.<reason>`; JSON documents
add `json.syntax`.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.composer import ComposerError
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from ruamel.yaml.tokens import (
    AliasToken,
    AnchorToken,
    DirectiveToken,
    ScalarToken,
    TagToken,
)

from strategy_workbench.application.strategy_authoring.facade.ports import (
    CodecLimits,
    DiagnosticKind,
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
_STR_TAG = "tag:yaml.org,2002:str"
_NUMBER_TAGS = ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float")
_CORE_INT = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_CORE_FLOAT = re.compile(r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$")
_NON_FINITE = re.compile(r"^(?:[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$")
_MAX_SAFE_INTEGER = 2**53 - 1


class _Rejected(Exception):
    def __init__(
        self, code: str, message: str, pointer: str = "", range_: SourceRange | None = None
    ):
        super().__init__(message)
        self.code = code
        self.pointer = pointer
        self.range = range_


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


class RuamelDocumentCodec:
    def __init__(self, limits: CodecLimits | None = None) -> None:
        self._limits = limits or CodecLimits()

    def parse(self, source: str, *, format: SourceFormat) -> ParsedDocument:
        source_hash = source_hash_of(source)
        try:
            self._check_size(source)
            if format is SourceFormat.JSON:
                self._check_json_syntax(source)
            root = self._compose(source)
            value_ranges: dict[str, SourceRange] = {}
            key_ranges: dict[str, SourceRange] = {}
            counter = _Counter()
            tree = self._walk(root, "", 0, value_ranges, key_ranges, counter)
            if not isinstance(tree, dict):
                raise _Rejected(
                    "yaml.not_a_mapping",
                    f"document root must be a mapping — got={type(tree).__name__}",
                    "",
                    _range_of(root),
                )
        except _Rejected as rejected:
            return ParsedDocument(
                status=ParseStatus.REJECTED,
                format=format,
                source_hash=source_hash,
                tree=None,
                value_ranges={},
                key_ranges={},
                diagnostics=(
                    SourceDiagnostic(
                        code=rejected.code,
                        kind=DiagnosticKind.SYNTAX,
                        pointer=rejected.pointer,
                        message=str(rejected),
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

    def _check_size(self, source: str) -> None:
        size = len(source.encode("utf-8"))
        if size > self._limits.max_bytes:
            raise _Rejected(
                "yaml.too_large",
                f"source exceeds size limit — bytes={size} max_bytes={self._limits.max_bytes}",
            )

    @staticmethod
    def _check_json_syntax(source: str) -> None:
        try:
            json.loads(source)
        except json.JSONDecodeError as error:
            position = SourcePosition(error.lineno - 1, error.colno - 1, error.pos)
            raise _Rejected(
                "json.syntax",
                f"invalid JSON — {error.msg} at line={error.lineno} column={error.colno}",
                "",
                SourceRange(position, position),
            ) from error
        except RecursionError as error:
            raise _Rejected("yaml.too_deep", "invalid JSON — nesting too deep") from error

    def _compose(self, source: str) -> Node:
        loader = _loader()
        try:
            tokens = list(loader.scan(source))
        except MarkedYAMLError as error:
            raise _Rejected(
                "yaml.syntax", _marked_message(error), "", _mark_range(error)
            ) from error
        except YAMLError as error:
            raise _Rejected("yaml.syntax", f"invalid YAML — {error}") from error
        _scan_policy(tokens, loader)
        try:
            root = _loader().compose(source)
        except ComposerError as error:
            if "single document" in str(error):
                raise _Rejected(
                    "yaml.multiple_documents",
                    "source must contain exactly one YAML document",
                    "",
                    _mark_range(error),
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
        if root is None:
            raise _Rejected("yaml.not_a_mapping", "document is empty — expected a mapping")
        return root

    def _walk(
        self,
        node: Node,
        pointer: str,
        depth: int,
        value_ranges: dict[str, SourceRange],
        key_ranges: dict[str, SourceRange],
        counter: _Counter,
    ) -> Any:
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
                key = _loader().constructor.construct_object(key_node, deep=True)
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
                    value_node, child, depth + 1, value_ranges, key_ranges, counter
                )
            return result
        if isinstance(node, SequenceNode):
            return [
                self._walk(item, f"{pointer}/{index}", depth + 1, value_ranges, key_ranges, counter)
                for index, item in enumerate(node.value)
            ]
        if isinstance(node, ScalarNode):
            value = _loader().constructor.construct_object(node, deep=True)
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


class _Counter:
    nodes: int = 0


def _loader() -> YAML:
    loader = YAML(typ="safe", pure=True)
    loader.version = (1, 2)
    loader.constructor.add_constructor(_TIMESTAMP_TAG, lambda _constructor, node: node.value)
    return loader


def _scan_policy(tokens: Iterable[object], loader: YAML) -> None:
    for token in tokens:
        if isinstance(token, DirectiveToken):
            raise _Rejected(
                "yaml.directive",
                f"directives are not allowed — directive=%{token.name}",
                "",
                _token_range(token),
            )
        if isinstance(token, (AnchorToken, AliasToken)):
            raise _Rejected(
                "yaml.anchor_or_alias",
                f"anchors and aliases are not allowed — token={type(token).__name__}",
                "",
                _token_range(token),
            )
        if isinstance(token, TagToken):
            raise _Rejected(
                "yaml.tag", f"tags are not allowed — tag={token.value}", "", _token_range(token)
            )
        if isinstance(token, ScalarToken) and token.plain:
            tag = loader.resolver.resolve(ScalarNode, token.value, (True, False))
            if tag == _MERGE_TAG:
                raise _Rejected(
                    "yaml.merge_key", "merge keys (<<) are not allowed", "", _token_range(token)
                )
            core_number = bool(_CORE_INT.match(token.value) or _CORE_FLOAT.match(token.value))
            resolver_number = tag in _NUMBER_TAGS and not _NON_FINITE.match(token.value)
            if resolver_number != core_number:
                raise _Rejected(
                    "yaml.non_core_number",
                    "number literal outside the YAML 1.2 core schema — "
                    f"scalar={token.value!r} (frontend and backend would disagree)",
                    "",
                    _token_range(token),
                )


def _position(mark: Any) -> SourcePosition:
    return SourcePosition(line=mark.line, column=mark.column, offset=mark.index)


def _range_of(node: Node) -> SourceRange:
    return SourceRange(_position(node.start_mark), _position(node.end_mark))


def _token_range(token: Any) -> SourceRange:
    return SourceRange(_position(token.start_mark), _position(token.end_mark))


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
