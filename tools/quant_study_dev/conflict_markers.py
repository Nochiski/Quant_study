"""병합 충돌 표식이 커밋되지 않았는지 저장소 전체에서 검사한다.

P1-02 rebase에서 `.claude/rules/strategy-workbench-sot.md`의 충돌 표식이 해소되지 않은 채
커밋되어 SoT 대장 5행이 두 벌로 남은 사고(Phase 1 감사 BLOCKING)의 재발 방지 게이트다.
표식이 살아 있으면 되살아난 옛 행이 현재 규칙과 모순되는데, 파일은 정상적으로 읽히고
게이트도 통과해 버려 아무도 알아채지 못한다.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

# git이 쓰는 표식 세 종류. 뒤에 공백이 오거나 줄이 끝나야 한다 — `=======`만 쓰는 구분선이나
# Markdown setext 밑줄과 달리 표식은 정확히 7자다.
MARKER = re.compile(r"^(<<<<<<<|=======|>>>>>>>)( |$)")

# 생성물·의존성·작업 산출물. 저장소가 소유하지 않는 파일에서 표식을 찾을 이유가 없다.
SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        "coverage",
        "playwright-report",
        "test-results",
    }
)

BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".webp",
        ".pdf",
        ".zip",
        ".gz",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".duckdb",
        ".parquet",
        ".sqlite",
        ".so",
        ".dll",
        ".pyd",
        ".exe",
        ".lock",
    }
)

Finding = tuple[Path, int, str]


def iter_candidate_files(root: Path) -> Iterator[Path]:
    """검사 대상 파일. 생성물 디렉터리와 알려진 바이너리 확장자는 건너뛴다."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        yield path


def scan_text(text: str) -> list[tuple[int, str]]:
    """텍스트에서 (1-기반 줄 번호, 줄) 목록을 낸다."""
    return [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if MARKER.match(line)
    ]


def scan_file(path: Path) -> list[tuple[int, str]]:
    """파일 하나를 검사한다. 디코딩되지 않으면 바이너리로 보고 건너뛴다."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    if "\x00" in text:
        return []
    return scan_text(text)


def find_conflict_markers(root: Path) -> list[Finding]:
    """`root` 아래 전체에서 표식을 찾는다."""
    findings: list[Finding] = []
    for path in iter_candidate_files(root):
        findings.extend(
            (path, number, line) for number, line in scan_file(path)
        )
    return findings


def repo_root(start: Path | None = None) -> Path:
    """체크아웃 루트. `server.py`와 같은 판정 기준을 쓴다."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "backend" / "pyproject.toml").is_file() and (
            candidate / "frontend" / "package.json"
        ).is_file():
            return candidate
    raise RuntimeError("run this check from the Quant_study checkout")


def main(argv: list[str] | None = None) -> int:
    """표식이 하나라도 있으면 위치를 모두 찍고 1을 돌려준다."""
    arguments = sys.argv[1:] if argv is None else argv
    root = Path(arguments[0]).resolve() if arguments else repo_root()
    findings = find_conflict_markers(root)
    for path, number, line in findings:
        print(f"{path.relative_to(root).as_posix()}:{number}: {line}")
    if findings:
        print(
            f"병합 충돌 표식 {len(findings)}건이 커밋되어 있습니다. "
            "해소한 뒤 다시 커밋하세요.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 진입점
    raise SystemExit(main())
