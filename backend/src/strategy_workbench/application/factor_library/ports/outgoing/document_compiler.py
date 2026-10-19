"""전략 문서 compile 포트.

라이브러리는 팩터를 따로 검증하지 않는다. 팩터 항목을 담은 최소 전략 문서를 전략 compile 에 넣어
같은 필드 계약·그래프 규칙으로 판정하고, compile 이 낸 정규 JSON 에서 버전 신원을 읽는다. 구현은
bootstrap 이 전략 저작 서비스를 감싼다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class LibraryDiagnostic:
    code: str
    message: str
    """backend 가 완성한 한글 문장(compile 진단 그대로)."""
    pointer: str
    severity: Literal["error", "warning", "info"]


@dataclass(frozen=True)
class CompiledLibraryDocument:
    canonical: Mapping[str, object] | None
    """compile 이 낸 정규 spec(JSON). 오류가 있으면 None."""
    diagnostics: tuple[LibraryDiagnostic, ...]


class FactorDocumentCompilerPort(Protocol):
    def compile(
        self, source: str, *, format: Literal["yaml", "json"]
    ) -> CompiledLibraryDocument: ...
