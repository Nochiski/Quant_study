"""개발 서버 시작 전에 Rust 백테스트 코어(backtest_core) 확장을 준비한다.

확장이 없거나 Rust 소스가 마지막 빌드 뒤에 바뀌었으면 ``maturin develop --release``로 빌드한다.
빌드는 이미 설치된 backend 환경 안에서만 하고 의존성 설치·동기화는 하지 않는다(``--no-sync``).
Rust 툴체인이 없거나 빌드가 실패해도 서버 시작은 막지 않는다.

현재 소스와 다른(낡은) 확장이 남아 있는데 새로 빌드하지 못하면 그 확장을 지운다. 낡은 코어로 돈
백테스트가 현재 코드의 결과처럼 보이는 것을 막기 위해서다. 지운 뒤 Rust 코어 실행은 "코어 미설치"
오류로 명시적으로 막히고, Python reference 코어는 그대로 쓸 수 있다.
"""

from __future__ import annotations

import enum
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from .backend_env import backend_process_environment, backend_uv_command, backend_venv

# 이 환경 변수를 1·true·yes·on 중 하나로 두면 자동 빌드를 건너뛴다.
SKIP_ENV = "QUANT_STUDY_DEV_SKIP_RUST_BUILD"
# 같은 소스로 이미 실패한 빌드를 다시 시도하게 한다.
RETRY_ENV = "QUANT_STUDY_DEV_RETRY_RUST_BUILD"
CRATE = Path("rust") / "backtest_core"
MANIFEST = CRATE / "Cargo.toml"
PACKAGE = "backtest_core"
DISTRIBUTION = "backtest-core"
# 빌드 기록은 backend 가상환경 안에 둔다. 환경을 다시 만들면 기록도 함께 사라진다.
STAMP_NAME = "backtest_core.build-stamp.json"
INSTALL_HINT = "Rust 툴체인(https://rustup.rs)을 설치한 뒤 uv run dev를 다시 실행하면 자동으로 빌드합니다."
RETRY_HINT = (
    f"다시 시도하려면 uv run dev --retry-rust-build 또는 {RETRY_ENV}=1로 실행하세요."
)
# cargo는 crate 위 디렉터리에서도 설정·툴체인 파일을 찾는다. backend 안에서만 본다.
_ANCESTOR_FILES = (
    "rust-toolchain",
    "rust-toolchain.toml",
    ".cargo/config",
    ".cargo/config.toml",
)

Runner = Callable[..., subprocess.CompletedProcess[bytes]]
Artifact = tuple[str, int, int]


class Action(enum.Enum):
    """시작 전 Rust 코어 처리 판정."""

    SKIP_DISABLED = "skip_disabled"
    SKIP_NO_ENVIRONMENT = "skip_no_environment"
    UP_TO_DATE = "up_to_date"
    BUILD_MISSING = "build_missing"
    BUILD_STALE = "build_stale"
    WARN_NO_TOOLCHAIN = "warn_no_toolchain"
    WARN_PREVIOUS_FAILURE = "warn_previous_failure"
    WARN_ERROR = "warn_error"


@dataclass(frozen=True)
class Stamp:
    """마지막 성공 빌드의 소스 지문과 그때 설치된 확장 파일, 마지막 실패 빌드의 소스 지문."""

    source: str | None = None
    artifact: Artifact | None = None
    failed_source: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "source": self.source,
                "artifact": list(self.artifact) if self.artifact else None,
                "failed_source": self.failed_source,
            }
        )

    @classmethod
    def parse(cls, text: str) -> Stamp:
        try:
            data = json.loads(text)
            artifact = data.get("artifact")
            return cls(
                source=data.get("source"),
                artifact=(str(artifact[0]), int(artifact[1]), int(artifact[2]))
                if artifact
                else None,
                failed_source=data.get("failed_source"),
            )
        except (ValueError, TypeError, AttributeError, IndexError):
            # 깨진 기록은 기록이 없는 것으로 본다(다시 빌드해 새로 적는다).
            return cls()


@dataclass(frozen=True)
class CoreState:
    """판정에 필요한 사실만 모은다. 수집과 판정을 나눠 판정을 순수 함수로 시험한다."""

    artifact: Artifact | None
    stamp: Stamp
    source_digest: str
    toolchain: bool
    retry: bool


def is_fresh(state: CoreState) -> bool:
    """설치된 확장이 이 도구가 현재 소스로 빌드한 바로 그 파일인가.

    수동 빌드나 다른 도구가 확장을 바꿔 놓았으면 파일 서명이 달라져 낡은 것으로 본다.
    """
    return (
        state.artifact is not None
        and state.stamp.source == state.source_digest
        and state.stamp.artifact == state.artifact
    )


def decide(state: CoreState) -> Action:
    """설치된 확장, 빌드 기록, 소스 지문으로 할 일을 정한다."""
    if is_fresh(state):
        return Action.UP_TO_DATE
    if not state.toolchain:
        return Action.WARN_NO_TOOLCHAIN
    if state.stamp.failed_source == state.source_digest and not state.retry:
        return Action.WARN_PREVIOUS_FAILURE
    return Action.BUILD_STALE if state.artifact is not None else Action.BUILD_MISSING


def flag(environ: Mapping[str, str], name: str) -> bool:
    return environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _source_files(backend: Path) -> Iterator[Path]:
    crate = backend / CRATE
    for path in crate.rglob("*"):
        relative = path.relative_to(crate).parts
        # 빌드 산출물과 OS가 만드는 부가 파일은 소스가 아니다.
        if "target" in relative or path.name == ".DS_Store" or not path.is_file():
            continue
        yield path
    for directory in (crate.parent, backend):
        for name in _ANCESTOR_FILES:
            candidate = directory / name
            if candidate.is_file():
                yield candidate


def source_digest(backend: Path) -> str:
    """빌드 결과에 영향을 줄 수 있는 파일의 상대 경로와 내용으로 결정적인 지문을 만든다."""
    files = sorted(
        _source_files(backend), key=lambda path: path.relative_to(backend).as_posix()
    )
    digest = hashlib.sha256()
    for path in files:
        # OS별 경로 구분자 차이가 지문을 바꾸지 않게 POSIX 형식으로 적는다.
        digest.update(path.relative_to(backend).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def site_packages(venv: Path) -> list[Path]:
    if os.name == "nt":
        return [venv / "Lib" / "site-packages"]
    return sorted((venv / "lib").glob("python*/site-packages"))


def find_artifact(venv: Path) -> Artifact | None:
    """subprocess import 대신 설치된 확장 파일(.so·.pyd)의 경로·크기·수정 시각을 본다."""
    for packages in site_packages(venv):
        package = packages / PACKAGE
        if not package.is_dir():
            continue
        for path in sorted(package.iterdir()):
            if path.name.startswith(PACKAGE) and path.suffix in {".so", ".pyd"}:
                info = path.stat()
                return (
                    path.relative_to(venv).as_posix(),
                    info.st_size,
                    info.st_mtime_ns,
                )
    return None


def read_stamp(venv: Path) -> Stamp:
    try:
        return Stamp.parse((venv / STAMP_NAME).read_text(encoding="utf-8"))
    except OSError:
        return Stamp()


def write_stamp(venv: Path, stamp: Stamp) -> None:
    (venv / STAMP_NAME).write_text(stamp.to_json() + "\n", encoding="utf-8")


def find_cargo(
    environ: Mapping[str, str],
    which: Callable[[str], str | None] = shutil.which,
    home: Path | None = None,
) -> Path | None:
    """PATH에 없더라도 rustup 기본 위치(~/.cargo/bin)의 cargo를 찾는다."""
    found = which("cargo")
    if found:
        return Path(found)
    cargo_home = environ.get("CARGO_HOME")
    base = Path(cargo_home) if cargo_home else (home or Path.home()) / ".cargo"
    name = "cargo.exe" if os.name == "nt" else "cargo"
    candidate = base / "bin" / name
    return candidate if candidate.is_file() else None


def build_environment(environ: Mapping[str, str], cargo: Path | None) -> dict[str, str]:
    environment = backend_process_environment(environ)
    if cargo is not None:
        # maturin은 PATH의 cargo를 쓴다. rustup 기본 위치만 있는 셸도 빌드되게 앞에 붙인다.
        path = environment.get("PATH", "")
        environment["PATH"] = os.pathsep.join(filter(None, (str(cargo.parent), path)))
    return environment


def _flush_print(message: str) -> None:
    print(message, flush=True)


def build(uv: str, backend: Path, environment: Mapping[str, str], run: Runner) -> bool:
    # 진행 상황은 cargo·maturin 출력을 그대로 터미널에 보인다.
    command = backend_uv_command(
        uv,
        backend,
        "maturin",
        "develop",
        "--manifest-path",
        MANIFEST.as_posix(),
        "--release",
    )
    completed = run(list(command), cwd=backend, env=dict(environment), check=False)
    return completed.returncode == 0


def remove_stale(
    uv: str, backend: Path, venv: Path, environment: Mapping[str, str], run: Runner
) -> bool:
    """현재 소스와 다른 확장을 backend 환경에서 지운다. uv가 RECORD대로 정리한다."""
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    completed = run(
        [uv, "pip", "uninstall", "--python", str(python), DISTRIBUTION],
        cwd=backend,
        env=dict(environment),
        check=False,
    )
    return completed.returncode == 0 and find_artifact(venv) is None


def ensure_core(
    root: Path,
    *,
    disabled: bool,
    retry: bool = False,
    environ: Mapping[str, str] | None = None,
    run: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    log: Callable[[str], None] | None = None,
) -> Action:
    """필요하면 빌드하고 결과를 알린다. 어떤 경우에도 예외로 시작을 막지 않는다."""
    say = log or _flush_print
    source = os.environ if environ is None else environ
    if disabled or flag(source, SKIP_ENV):
        return Action.SKIP_DISABLED
    backend = root / "backend"
    venv = backend_venv(backend, source)
    uv = which("uv")
    if uv is None or not venv.is_dir():
        say(
            "[dev] backend 가상환경이 없어 Rust 코어 확인을 건너뜁니다. 먼저 uv sync를 실행해 주세요."
        )
        return Action.SKIP_NO_ENVIRONMENT
    try:
        return _ensure(
            uv, backend, venv, source, retry or flag(source, RETRY_ENV), run, which, say
        )
    except OSError as error:
        # 파일을 읽거나 쓰지 못하거나 실행 파일이 사라져도 서버 시작은 막지 않는다.
        say(
            "[dev] 경고: Rust 백테스트 코어를 확인하지 못했습니다"
            f"({type(error).__name__}: {error}). 서버는 계속 시작합니다. "
            "Rust 코어 백테스트가 실패하면 백테스트 실행 설정에서 Python reference를 선택하세요."
        )
        return Action.WARN_ERROR


def _ensure(
    uv: str,
    backend: Path,
    venv: Path,
    source: Mapping[str, str],
    retry: bool,
    run: Runner,
    which: Callable[[str], str | None],
    say: Callable[[str], None],
) -> Action:
    cargo = find_cargo(source, which)
    environment = build_environment(source, cargo)
    stamp = read_stamp(venv)
    state = CoreState(
        artifact=find_artifact(venv),
        stamp=stamp,
        source_digest=source_digest(backend),
        toolchain=cargo is not None,
        retry=retry,
    )
    action = decide(state)
    if action is Action.UP_TO_DATE:
        return action
    if action in (Action.BUILD_MISSING, Action.BUILD_STALE):
        reason = (
            "설치되어 있지 않아"
            if action is Action.BUILD_MISSING
            else "현재 소스와 달라"
        )
        say(
            f"[dev] Rust 백테스트 코어가 {reason} 빌드합니다(release, 처음에는 몇 분 걸릴 수 있음)."
        )
        if build(uv, backend, environment, run):
            built = find_artifact(venv)
            write_stamp(venv, Stamp(source=state.source_digest, artifact=built))
            say("[dev] Rust 백테스트 코어 빌드 완료.")
            return action
        # 같은 소스로 시작할 때마다 긴 빌드를 되풀이하지 않게 실패를 기록한다.
        write_stamp(
            venv, Stamp(stamp.source, stamp.artifact, failed_source=state.source_digest)
        )
        say(
            "[dev] 경고: Rust 백테스트 코어 빌드에 실패했습니다. 위 빌드 로그를 확인해 주세요. "
            + RETRY_HINT
        )
    elif action is Action.WARN_NO_TOOLCHAIN:
        say(
            "[dev] 경고: Rust 툴체인(cargo)을 찾지 못해 Rust 백테스트 코어를 빌드하지 않습니다. "
            + INSTALL_HINT
        )
    elif action is Action.WARN_PREVIOUS_FAILURE:
        say(
            "[dev] 경고: 지금 Rust 소스로 한 지난 빌드가 실패해 이번에는 빌드하지 않습니다. "
            + RETRY_HINT
        )
    _drop_stale(uv, backend, venv, environment, run, say)
    return action


def _drop_stale(
    uv: str,
    backend: Path,
    venv: Path,
    environment: Mapping[str, str],
    run: Runner,
    say: Callable[[str], None],
) -> None:
    if find_artifact(venv) is None:
        say(
            "[dev] Rust 코어가 없어 백테스트는 Python reference 코어로만 실행할 수 있습니다."
        )
        return
    if remove_stale(uv, backend, venv, environment, run):
        say(
            "[dev] 현재 소스와 다른 낡은 Rust 코어를 지웠습니다. 낡은 코어로 계산한 결과가 섞이지 않게 "
            "하기 위해서입니다. 백테스트는 Python reference 코어로만 실행할 수 있습니다."
        )
    else:
        say(
            "[dev] 경고: 현재 소스와 다른 낡은 Rust 코어를 지우지 못했습니다. 지금 Rust 코어로 돌린 "
            "백테스트는 현재 코드와 다른 결과를 낼 수 있습니다. Python reference를 쓰거나 "
            f"backend 환경에서 `uv pip uninstall {DISTRIBUTION}`을 실행하세요."
        )
