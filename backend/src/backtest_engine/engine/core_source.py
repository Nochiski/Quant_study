"""Rust 코어 확장(`backtest_core`)이 현재 Rust 소스로 빌드됐는지 판정한다.

지문 규칙의 정본은 이 파일이다. crate의 `build.rs`가 같은 규칙으로 빌드 입력의 지문을 계산해
확장 상수 `backtest_core.SOURCE_DIGEST`로 굽고, 빌드 프로필을 `BUILD_PROFILE`로 굽는다(계약 테스트
`tests/test_core_source.py`가 둘의 지문이 같음을 고정한다). 엔진은 소스 트리가 있으면 이 지문을
비교해, 낡은 확장을 조용히 쓰지 않는다.

지문 규칙:
- 대상: crate 루트의 빌드 파일(`ROOT_FILES`), `src/`·`.cargo/` 아래 모든 파일(편집기 부산물·임시
  파일 제외), crate 위에서 저장소 루트(`.git`이 있는 디렉터리)까지의 `rust-toolchain`·
  `rust-toolchain.toml`·`.cargo/config`·`.cargo/config.toml`. 확장 빌드에 들어가는 입력만 본다
  (`target/`·문서·`tests/` 등은 빼며, build.rs가 그 목록만 감시해 매 빌드 다시 돌지 않게 한다).
- 심볼릭 링크는 따라가지 않고 건너뛴다. UTF-8이 아닌 이름도 건너뛴다(두 구현 같은 규칙).
- 키: crate 기준 POSIX 상대 경로. crate 위 파일은 `../`를 단계마다 붙인다.
- 일반 파일만 넣는다(FIFO·소켓·장치는 경고 후 제외). 읽지 못한 파일·디렉터리가 있으면 Python은
  낡음으로 판정하고 build.rs는 정본과 맞을 수 없는 지문을 굽는다.
- build.rs는 존재하는 입력 파일과 `src/`·`.cargo/` 디렉터리만 감시한다(없는 경로는 cargo가 매번
  바뀐 것으로 봐 매 빌드 다시 컴파일한다). 그래서 crate 루트나 상위 폴더에 후보 파일을 새로 만들면
  수동 빌드는 build.rs를 다시 돌리지 않을 수 있다. `uv run dev`는 빌드 전에 build.rs의 수정 시각을
  갱신해 늘 다시 계산하게 하고, 수동 빌드라면 `build.rs`를 touch한 뒤 빌드한다.
- 해시: 키를 바이트 순으로 정렬해 `키 \\0 내용 \\0`을 이어 붙인 바이트의 FNV-1a 128비트, 소문자
  32자리 16진수. 변경 감지용이라 의존성 없는 같은 알고리즘을 두 언어에 둔다.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, get_args

CoreStatus = Literal["missing", "broken", "stale", "debug", "fresh", "unchecked"]
CORE_STATUSES: tuple[str, ...] = get_args(CoreStatus)
# 엔진이 쓸 수 있는 상태. debug는 쓰되 경고하고, dev 도구는 release로 다시 빌드한다.
USABLE_STATUSES = frozenset({"fresh", "debug", "unchecked"})
# 사용 불가 상태 중 다시 빌드로 풀리는 것(dev 도구가 빌드한다).
REBUILD_STATUSES = frozenset({"missing", "broken", "stale", "debug"})

MODULE = "backtest_core"
# backend/src/backtest_engine/engine/core_source.py → backend
_BACKEND = Path(__file__).resolve().parents[3]
CRATE = _BACKEND / "rust" / "backtest_core"
BUILD_COMMAND = (
    "uv run --no-sync maturin develop --manifest-path rust/backtest_core/Cargo.toml --release"
)

# crate 루트에서 지문에 드는 파일. 루트의 다른 파일(문서 등)은 빌드에 영향이 없어 뺀다.
ROOT_FILES = frozenset(
    {
        "build.rs",
        "Cargo.toml",
        "Cargo.lock",
        "pyproject.toml",
        "rust-toolchain",
        "rust-toolchain.toml",
    }
)
SOURCE_DIRECTORIES = ("src", ".cargo")
_ANCESTOR_FILES = ("rust-toolchain", "rust-toolchain.toml", ".cargo/config", ".cargo/config.toml")
_SKIPPED_DIRECTORIES = frozenset({".idea", ".vscode"})
# 편집기 부산물·임시 파일: vim 스왑과 쓰기 시험 파일(4913), emacs 잠금(.#*), 백업(~), *.tmp.
_SKIPPED_FILES = frozenset({".DS_Store", "4913"})
_SKIPPED_PREFIXES = (".#",)
_SKIPPED_SUFFIXES = (".swp", ".swo", ".tmp", "~")

_FNV_OFFSET = 0x6C62272E07BB014262B821756295C58D
_FNV_PRIME = 0x0000000001000000000000000000013B
_MASK = (1 << 128) - 1

_logger = logging.getLogger(__name__)


def _utf8(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        # 비 UTF-8 바이트 이름은 surrogateescape로 들어온다. Rust 쪽도 같은 이름을 건너뛴다.
        _logger.warning("Rust 코어 지문에서 UTF-8이 아닌 이름을 건너뜁니다 — name=%r", name)
        return False
    return True


def skipped_file(name: str) -> bool:
    return (
        name in _SKIPPED_FILES
        or name.startswith(_SKIPPED_PREFIXES)
        or name.endswith(_SKIPPED_SUFFIXES)
    )


def _regular(path: Path) -> bool:
    """일반 파일만 지문에 넣는다. 링크는 조용히, FIFO·소켓·장치는 경고하고 뺀다(Rust와 같은 규칙).

    lstat이 실패하면 OSError를 그대로 올린다(읽지 못한 입력은 판정자가 낡음으로 본다).
    """
    mode = os.lstat(path).st_mode
    if stat.S_ISREG(mode):
        return True
    if not stat.S_ISLNK(mode) and not stat.S_ISDIR(mode):
        _logger.warning("Rust 코어 지문에서 일반 파일이 아닌 항목을 건너뜁니다 — path=%s", path)
    return False


def _raise(error: OSError) -> None:
    # 읽지 못한 하위 디렉터리를 조용히 건너뛰면 빠진 입력으로 최신 판정이 날 수 있다.
    raise error


def _crate_files(crate: Path) -> Iterator[tuple[str, Path]]:
    for name in sorted(ROOT_FILES):
        path = crate / name
        if path.exists() and _regular(path):
            yield name, path
    for source in SOURCE_DIRECTORIES:
        root = crate / source
        if not root.is_dir() or root.is_symlink():
            continue
        for directory, subdirectories, files in os.walk(root, onerror=_raise):
            current = Path(directory)
            # os.walk는 링크된 디렉터리로 내려가지 않지만 목록에는 남긴다. 명시적으로 뺀다.
            subdirectories[:] = sorted(
                name
                for name in subdirectories
                if name not in _SKIPPED_DIRECTORIES
                and not (current / name).is_symlink()
                and _utf8(name)
            )
            for name in files:
                path = current / name
                if skipped_file(name) or not _utf8(name) or not _regular(path):
                    continue
                yield path.relative_to(crate).as_posix(), path


def ancestor_directories(crate: Path) -> Iterator[Path]:
    """crate 위에서 저장소 루트까지 cargo 설정·툴체인 파일을 찾는 디렉터리."""
    directory = crate.parent
    while True:
        yield directory
        if (directory / ".git").exists() or directory.parent == directory:
            return
        directory = directory.parent


def _ancestor_files(crate: Path) -> Iterator[tuple[str, Path]]:
    for level, directory in enumerate(ancestor_directories(crate), start=1):
        for name in _ANCESTOR_FILES:
            path = directory / name
            if path.exists() and _regular(path):
                yield "../" * level + name, path


def source_inputs(crate: Path = CRATE) -> list[tuple[str, Path]]:
    """지문에 들어가는 (키, 경로)를 키 순서로 돌려준다."""
    entries = [*_crate_files(crate), *_ancestor_files(crate)]
    return sorted(entries, key=lambda entry: entry[0].encode())


def source_digest(crate: Path = CRATE) -> str:
    """지문을 계산한다. 파일을 읽지 못하면 OSError를 그대로 올린다(판정은 호출자가 한다)."""
    value = _FNV_OFFSET
    for key, path in source_inputs(crate):
        for chunk in (key.encode(), b"\0", path.read_bytes(), b"\0"):
            for byte in chunk:
                value ^= byte
                value = (value * _FNV_PRIME) & _MASK
    return f"{value:032x}"


def source_tree_present(crate: Path = CRATE) -> bool:
    """crate의 핵심 파일이 모두 있을 때만 비교한다. 일부만 있는 배포는 확인하지 않는다."""
    return (crate / "Cargo.toml").is_file() and (crate / "src" / "lib.rs").is_file()


def _judge(crate: Path) -> tuple[CoreStatus, bool]:
    """(상태, 프로세스 안에서 고정해도 되는가)를 돌려준다."""
    if importlib.util.find_spec(MODULE) is None:
        return "missing", True
    if not source_tree_present(crate):
        return "unchecked", True
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError:
        return "missing", True
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:  # noqa: BLE001  # reason: 확장 초기화 panic(PanicException은 BaseException)·링크 오류도 판정해야 한다
        _logger.warning(
            "Rust 코어 확장을 불러오지 못했습니다 — 다시 빌드하세요: %s (error=%s: %s)",
            BUILD_COMMAND,
            type(error).__name__,
            error,
        )
        return "broken", True
    try:
        digest = source_digest(crate)
    except OSError as error:
        # 저장 중인 파일·깨진 링크·권한 문제로 읽지 못하면 최신이라고 단정하지 않는다. 일시적일 수
        # 있어 프로세스 판정으로 고정하지 않는다.
        _logger.warning(
            "Rust 소스 지문을 계산하지 못해 코어를 낡은 것으로 봅니다 — 잠시 뒤 다시 시도하세요 "
            "(error=%s: %s)",
            type(error).__name__,
            error,
        )
        return "stale", False
    # 지문 상수가 없는 확장은 이 규칙 이전의 빌드라 어느 소스에서 나왔는지 모른다.
    if getattr(module, "SOURCE_DIGEST", None) != digest:
        return "stale", True
    profile = getattr(module, "BUILD_PROFILE", None)
    return ("fresh" if profile == "release" else "debug"), True


def core_status(crate: Path = CRATE) -> CoreStatus:
    """설치된 확장의 상태(없음·깨짐·낡음·디버그 빌드·최신, 소스가 없으면 확인 생략)."""
    return _judge(crate)[0]


_process_status: CoreStatus | None = None


def process_core_status() -> CoreStatus:
    """이 프로세스에서 한 번 내린 판정을 돌려준다.

    한 프로세스가 import한 확장은 바뀌지 않으므로 판정도 그 프로세스 동안 고정한다. 그래서 run
    도중 .rs를 저장해도 진행 중인 run이 깨지지 않는다. 백테스트 run은 run마다 새 worker 프로세스에서
    돌므로, 다시 빌드하면 다음 run부터 새 판정이 적용된다. 읽기 실패로 나온 일시적 낡음은 고정하지
    않고 다음 호출에서 다시 판정한다.
    """
    global _process_status
    if _process_status is not None:
        return _process_status
    status, stable = _judge(CRATE)
    if status == "debug":
        _logger.warning(
            "Rust 코어가 디버그 빌드입니다 — 결과는 같지만 느립니다. release로 다시 빌드하세요: %s",
            BUILD_COMMAND,
        )
    if stable:
        _process_status = status
    return status


def reset_process_core_status() -> None:
    """테스트용: 프로세스 판정을 비운다."""
    global _process_status
    _process_status = None


def main(argv: list[str] | None = None) -> int:
    """상태 한 단어를 마지막 줄에 출력한다. dev 도구가 부른다."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in ([], ["status"]):
        print("usage: core_source [status]", file=sys.stderr)
        return 2
    print(core_status())
    return 0
