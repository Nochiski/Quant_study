"""Rust 코어 확장(`backtest_core`)이 현재 Rust 소스로 빌드됐는지 판정한다.

지문 규칙의 정본은 이 파일이다. crate의 `build.rs`가 같은 규칙으로 빌드 입력의 지문을 계산해
확장 상수 `backtest_core.SOURCE_DIGEST`로 굽는다(계약 테스트 `tests/test_core_source.py`가 둘이
같음을 고정한다). 엔진은 소스 트리가 있으면 이 지문을 비교해, 낡은 확장을 조용히 쓰지 않는다.

지문 규칙:
- 대상: crate 디렉터리 아래 모든 파일(최상위 `target/`과 편집기 부산물 제외)과, crate 위에서
  저장소 루트(`.git`이 있는 디렉터리)까지의 `rust-toolchain`·`rust-toolchain.toml`·
  `.cargo/config`·`.cargo/config.toml`.
- 키: crate 기준 POSIX 상대 경로. crate 위 파일은 `../`를 단계마다 붙인다.
- 해시: 키를 바이트 순으로 정렬해 `키 \\0 내용 \\0`을 이어 붙인 바이트의 FNV-1a 128비트, 소문자
  32자리 16진수. 변경 감지용이라 의존성 없는 같은 알고리즘을 두 언어에 둔다.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

CoreStatus = Literal["missing", "stale", "fresh", "unchecked"]

MODULE = "backtest_core"
# backend/src/backtest_engine/engine/core_source.py → backend
_BACKEND = Path(__file__).resolve().parents[3]
CRATE = _BACKEND / "rust" / "backtest_core"
BUILD_COMMAND = (
    "uv run --no-sync maturin develop --manifest-path rust/backtest_core/Cargo.toml --release"
)

_ANCESTOR_FILES = ("rust-toolchain", "rust-toolchain.toml", ".cargo/config", ".cargo/config.toml")
_SKIPPED_DIRECTORIES = frozenset({".idea", ".vscode"})
_SKIPPED_FILES = frozenset({".DS_Store"})
_SKIPPED_SUFFIXES = (".swp", ".swo", "~")

_FNV_OFFSET = 0x6C62272E07BB014262B821756295C58D
_FNV_PRIME = 0x0000000001000000000000000000013B
_MASK = (1 << 128) - 1


def _skipped_file(name: str) -> bool:
    return name in _SKIPPED_FILES or name.endswith(_SKIPPED_SUFFIXES)


def _crate_files(crate: Path) -> Iterator[tuple[str, Path]]:
    for directory, subdirectories, files in os.walk(crate):
        current = Path(directory)
        # 빌드 산출물은 crate 최상위 target/ 하나다. 하위의 같은 이름 디렉터리는 소스일 수 있다.
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name not in _SKIPPED_DIRECTORIES and not (current == crate and name == "target")
        )
        for name in files:
            if not _skipped_file(name):
                path = current / name
                yield path.relative_to(crate).as_posix(), path


def _ancestor_files(crate: Path) -> Iterator[tuple[str, Path]]:
    directory = crate.parent
    level = 1
    while True:
        for name in _ANCESTOR_FILES:
            path = directory / name
            if path.is_file():
                yield "../" * level + name, path
        if (directory / ".git").exists() or directory.parent == directory:
            return
        directory = directory.parent
        level += 1


def source_inputs(crate: Path = CRATE) -> list[tuple[str, Path]]:
    """지문에 들어가는 (키, 경로)를 키 순서로 돌려준다. build.rs의 rerun 목록과 같다."""
    entries = [*_crate_files(crate), *_ancestor_files(crate)]
    return sorted(entries, key=lambda entry: entry[0].encode())


def source_digest(crate: Path = CRATE) -> str:
    value = _FNV_OFFSET
    for key, path in source_inputs(crate):
        for chunk in (key.encode(), b"\0", path.read_bytes(), b"\0"):
            for byte in chunk:
                value ^= byte
                value = (value * _FNV_PRIME) & _MASK
    return f"{value:032x}"


def source_tree_present(crate: Path = CRATE) -> bool:
    """소스 트리가 없는 배포 환경이면 비교하지 않는다."""
    return (crate / "Cargo.toml").is_file()


def core_status(crate: Path = CRATE) -> CoreStatus:
    """설치된 확장이 없음/낡음/최신인지, 소스가 없어 확인하지 않았는지 알려 준다."""
    if importlib.util.find_spec(MODULE) is None:
        return "missing"
    if not source_tree_present(crate):
        return "unchecked"
    try:
        module = importlib.import_module(MODULE)
    except ImportError:
        return "missing"
    built = getattr(module, "SOURCE_DIGEST", None)
    # 지문 상수가 없는 확장은 이 규칙 이전의 빌드라 어느 소스에서 나왔는지 모른다.
    return "fresh" if built == source_digest(crate) else "stale"


def main(argv: list[str] | None = None) -> int:
    """상태 한 단어(missing·stale·fresh·unchecked)를 출력한다. dev 도구가 부른다."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in ([], ["status"]):
        print("usage: python -m backtest_engine.engine.core_source [status]", file=sys.stderr)
        return 2
    print(core_status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
