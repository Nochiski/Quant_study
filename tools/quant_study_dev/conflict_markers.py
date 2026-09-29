"""병합 충돌 표식이 커밋되지 않았는지 git 이 추적하는 파일 전체에서 검사한다.

P1-02 rebase에서 `.claude/rules/strategy-workbench-sot.md`의 충돌 표식이 해소되지 않은 채
커밋되어 SoT 대장 5행이 두 벌로 남은 사고(Phase 1 감사 BLOCKING)의 재발 방지 게이트다.
표식이 살아 있으면 되살아난 옛 행이 현재 규칙과 모순되는데, 파일은 정상적으로 읽히고
게이트도 통과해 버려 아무도 알아채지 못한다.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# git 이 쓰는 표식 세 종류. 표식 뒤에는 공백이 오거나 줄이 끝난다(CRLF 줄의 `\r` 까지). 7자가 아닌
# 밑줄·구분선은 잡지 않는다. 정확히 7자인 단독 줄(Markdown setext 밑줄 포함)은 git 도 충돌 표식으로 본다.
MARKER = re.compile(r"^(<<<<<<<|=======|>>>>>>>)( |\r?$)")

# 바이너리 판정은 git 과 같다 — BOM 없는 파일의 앞 8000바이트에 NUL 이 있으면 바이너리다.
BINARY_SNIFF_BYTES = 8000
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")

Finding = tuple[Path, int, str]


def tracked_files(root: Path) -> list[Path]:
    """`root` 아래에서 git 이 추적하는 파일. 비추적 `.orig`·`.rej`·빌드 산출물은 보지 않는다.

    병합을 푸는 도중에는 index 가 충돌 경로를 stage 마다 싣는데, 경로는 한 번만 낸다.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--deduplicate"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        raise RuntimeError(
            f"git ls-files failed — root={root} returncode={listed.returncode} "
            f"stderr={listed.stderr.decode(errors='replace').strip()}"
        )
    return [root / name for name in listed.stdout.decode("utf-8").split("\0") if name]


def scan_text(text: str) -> list[tuple[int, str]]:
    """텍스트에서 (1-기반 줄 번호, 줄) 목록을 낸다.

    줄은 git 처럼 LF 에서만 나눈다. `splitlines` 는 U+0085 같은 문자에서도 나눠, cp949 바이트를 대체
    디코딩한 텍스트에서 줄 번호가 git 과 어긋나고 줄 가운데의 표식 모양을 표식으로 본다.
    """
    return [
        (number, line)
        for number, line in enumerate(text.split("\n"), start=1)
        if MARKER.match(line)
    ]


def scan_file(path: Path) -> list[tuple[int, str]]:
    """파일 하나를 검사한다. BOM 이 있으면 UTF-16, 아니면 UTF-8 로 읽는다.

    깨진 바이트는 대체 문자로 두므로 cp949 같은 비 UTF-8 텍스트에서도 ASCII 표식은 그대로 잡힌다.
    """
    data = path.read_bytes()
    if data.startswith(UTF16_BOMS):
        return scan_text(data.decode("utf-16", errors="replace"))
    if b"\0" in data[:BINARY_SNIFF_BYTES]:
        return []
    return scan_text(data.decode("utf-8-sig", errors="replace"))


def find_conflict_markers(root: Path) -> list[Finding]:
    """`root` 아래 추적 파일에서 표식을 찾는다. 작업 트리에서 지운 추적 파일은 건너뛴다."""
    return [
        (path, number, line)
        for path in tracked_files(root)
        if path.is_file()
        for number, line in scan_file(path)
    ]


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
