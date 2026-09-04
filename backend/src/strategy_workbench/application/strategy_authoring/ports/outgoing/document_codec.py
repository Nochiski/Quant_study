"""Outgoing port: parse an authoring source (YAML/JSON text) into a JSON-compatible tree with a
source map.

ADR: docs/superpowers/specs/2026-09-04-yaml-parser-adr.md (D1-D3),
     docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md (D1, D3, D6)

The codec never builds a StrategySpec; it only produces the untyped tree, JSON Pointer ranges and
syntax/policy diagnostics. Typed hydrate (domain.strategy) and semantic validation run afterwards so
that every diagnostic kind can be attached to a source range.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class SourceFormat(StrEnum):
    YAML = "yaml"
    JSON = "json"


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
    """0-based line/column and UTF-8 code point offset into the exact source text."""

    line: int
    column: int
    offset: int


@dataclass(frozen=True)
class SourceRange:
    start: SourcePosition
    end: SourcePosition


@dataclass(frozen=True)
class SourceDiagnostic:
    code: str
    kind: DiagnosticKind
    pointer: str
    message: str
    range: SourceRange | None = None
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR


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


def source_hash_of(source: str) -> str:
    """sha256 of the exact UTF-8 source text (comments and whitespace included)."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class DocumentCodecPort(Protocol):
    def parse(self, source: str, *, format: SourceFormat) -> ParsedDocument: ...
