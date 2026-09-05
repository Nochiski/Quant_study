"""Outgoing port: parse an authoring source (YAML/JSON text) into a JSON-compatible tree with a
source map.

ADR: docs/superpowers/specs/2026-09-04-yaml-parser-adr.md (D1-D3),
     docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md (D1, D3, D6)

The codec never builds a StrategySpec; it only produces the untyped tree, JSON Pointer ranges and
syntax/policy diagnostics. Typed hydrate (domain.strategy) and semantic validation run afterwards so
that every diagnostic kind can be attached to a source range.

Diagnostic codes are declared here, not in the adapter, so a consumer can enumerate what a given
format may return before it ever calls `parse` — see `diagnostic_code` below.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from strategy_workbench.domain.strategy.facade.document import SourceFormat, source_hash_of

__all__ = [
    "DOCUMENT_POLICY_REASONS",
    "SYNTAX_REASON",
    "YAML_GRAMMAR_REASONS",
    "CodecLimits",
    "DiagnosticKind",
    "DiagnosticSeverity",
    "DocumentCodecPort",
    "ParseStatus",
    "ParsedDocument",
    "SourceDiagnostic",
    "SourceFormat",
    "SourcePosition",
    "SourceRange",
    "diagnostic_code",
    "diagnostic_codes",
    "source_hash_of",
]

# Rejection reasons, owned here rather than by whichever adapter detects them (DEFECT-103).
#
# The prefix names the rule that fired, not the parser that noticed it. A JSON document used to
# come back with `yaml.too_deep` / `yaml.duplicate_key` / `yaml.not_a_mapping` because the ruamel
# adapter walks both formats, which left a consumer no way to know which codes a `format=json`
# parse can produce. Policy that reads the same in both formats is therefore `document.<reason>`,
# constructs that exist only in YAML source stay `yaml.<reason>`, and a syntax error carries the
# format that failed to parse. Reason names are the cross-runtime vocabulary shared with
# `tests/fixtures/strategy_documents/yaml12/manifest.json`; only the prefix is decided here.

SYNTAX_REASON = "syntax"

DOCUMENT_POLICY_REASONS: frozenset[str] = frozenset(
    {
        "too_large",
        "too_deep",
        "too_many_nodes",
        "not_a_mapping",
        "duplicate_key",
        "non_string_key",
        "non_finite_number",
        "integer_out_of_range",
    }
)

YAML_GRAMMAR_REASONS: frozenset[str] = frozenset(
    {
        "directive",
        "anchor_or_alias",
        "tag",
        "merge_key",
        "non_core_number",
        "multiple_documents",
    }
)


def diagnostic_code(reason: str, format: SourceFormat) -> str:
    """Wire code for a rejection reason. The only sanctioned way to mint a codec diagnostic code."""
    if reason == SYNTAX_REASON:
        return f"{format.value}.{SYNTAX_REASON}"
    if reason in DOCUMENT_POLICY_REASONS:
        return f"document.{reason}"
    if reason in YAML_GRAMMAR_REASONS:
        # Only reachable for YAML sources — a JSON document has no directive, anchor or tag to
        # reject — so the code names the grammar, not the parse format.
        return f"yaml.{reason}"
    raise ValueError(
        "rejection reason has no owner — add it to DOCUMENT_POLICY_REASONS or "
        f"YAML_GRAMMAR_REASONS: reason={reason!r} format={format.value!r}"
    )


def diagnostic_codes(format: SourceFormat) -> frozenset[str]:
    """Every code `DocumentCodecPort.parse` may report for a document of this format."""
    reasons = DOCUMENT_POLICY_REASONS | {SYNTAX_REASON}
    if format is SourceFormat.YAML:
        reasons |= YAML_GRAMMAR_REASONS
    return frozenset(diagnostic_code(reason, format) for reason in reasons)


class DiagnosticKind(StrEnum):
    SYNTAX = "syntax"
    STRUCTURAL = "structural"
    SEMANTIC = "semantic"
    CAPABILITY = "capability"


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class SourcePosition:
    """0-based line/column and offset into the exact source text.

    Units are Unicode code points (Python `str` indices). JavaScript editors count UTF-16 code
    units; astral characters (emoji) shift later columns by one per character, so the frontend
    converts at the wire boundary (P3-04). Korean text is BMP-only and unaffected.
    """

    line: int
    column: int
    offset: int


@dataclass(frozen=True)
class SourceRange:
    start: SourcePosition
    end: SourcePosition


@dataclass(frozen=True)
class SourceDiagnostic:
    """One diagnostic. `severity` is always sent (no default) so the wire schema marks it required.

    `range` is None only when the source has no node to point at (empty document).
    `node_id` names the FactorGraph node a semantic issue is about, when known.
    """

    code: str
    kind: DiagnosticKind
    pointer: str
    message: str
    severity: DiagnosticSeverity
    range: SourceRange | None = None
    node_id: str | None = None


class ParseStatus(StrEnum):
    OK = "ok"
    REJECTED = "rejected"


@dataclass(frozen=True)
class CodecLimits:
    """Fail-closed resource bounds for untrusted source text (parser ADR D2)."""

    max_bytes: int = 512 * 1024
    max_depth: int = 32
    max_nodes: int = 20_000


@dataclass(frozen=True)
class ParsedDocument:
    status: ParseStatus
    format: SourceFormat
    source_hash: str
    tree: Mapping[str, object] | None
    value_ranges: Mapping[str, SourceRange]
    key_ranges: Mapping[str, SourceRange]
    diagnostics: tuple[SourceDiagnostic, ...]

    @property
    def ok(self) -> bool:
        return self.status is ParseStatus.OK

    def locate(self, pointer: str) -> SourceRange | None:
        """Best source range for a JSON Pointer.

        Exact value range first, then the key range, then the nearest existing ancestor's value
        range (a missing field points at its parent, per WORKFLOW P1-03).
        """
        if pointer in self.value_ranges:
            return self.value_ranges[pointer]
        if pointer in self.key_ranges:
            return self.key_ranges[pointer]
        current = pointer
        while current:
            current = current.rsplit("/", 1)[0]
            if current in self.value_ranges:
                return self.value_ranges[current]
        return self.value_ranges.get("")


class DocumentCodecPort(Protocol):
    def parse(self, source: str, *, format: SourceFormat) -> ParsedDocument: ...
