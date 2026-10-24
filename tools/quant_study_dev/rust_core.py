"""개발 서버 시작 전에 Rust 백테스트 코어(backtest_core) 확장을 준비한다.

확장이 없거나 Rust 소스가 마지막 빌드 뒤에 바뀌었으면 ``maturin develop --release``로 빌드한다.
빌드는 이미 설치된 backend 환경 안에서만 하고 의존성 설치·동기화는 하지 않는다(``--no-sync``).
Rust 툴체인이 없거나 빌드가 실패해도 서버 시작은 막지 않는다. 그때 백테스트는 Python reference
코어로만 돌릴 수 있다.
"""

from __future__ import annotations

import enum
import hashlib
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

# 이 환경 변수를 1·true·yes·on 중 하나로 두면 자동 빌드를 건너뛴다.
SKIP_ENV = "QUANT_STUDY_DEV_SKIP_RUST_BUILD"
MANIFEST = Path("rust") / "backtest_core" / "Cargo.toml"
# 빌드한 소스의 지문을 backend 가상환경 안에 둔다. 환경을 다시 만들면 지문도 함께 사라진다.
STAMP_NAME = "backtest_core.build-stamp"
INSTALL_HINT = "Rust 툴체인(https://rustup.rs)을 설치한 뒤 uv run dev를 다시 실행하면 자동으로 빌드합니다."

Runner = Callable[..., subprocess.CompletedProcess[bytes]]


class Action(enum.Enum):
    """시작 전 Rust 코어 처리 판정."""

    SKIP_DISABLED = "skip_disabled"
    SKIP_NO_ENVIRONMENT = "skip_no_environment"
    UP_TO_DATE = "up_to_date"
    BUILD_MISSING = "build_missing"
    BUILD_STALE = "build_stale"
    WARN_NO_TOOLCHAIN = "warn_no_toolchain"


@dataclass(frozen=True)
class CoreState:
    """판정에 필요한 사실만 모은다. 수집과 판정을 나눠 판정을 순수 함수로 시험한다."""

    disabled: bool
    environment_ready: bool
    installed: bool
    built_digest: str | None
    source_digest: str
    toolchain: bool


def decide(state: CoreState) -> Action:
    """설치 여부와 소스 지문으로 빌드할지 정한다."""
    if state.disabled:
        return Action.SKIP_DISABLED
    if not state.environment_ready:
        return Action.SKIP_NO_ENVIRONMENT
    # 지문이 없으면(이 도구 도입 전 수동 빌드 등) 설치본이 어느 소스에서 나왔는지 알 수 없어 낡은 것으로 본다.
    fresh = state.installed and state.built_digest == state.source_digest
    if fresh:
        return Action.UP_TO_DATE
    if not state.toolchain:
        return Action.WARN_NO_TOOLCHAIN
    return Action.BUILD_STALE if state.installed else Action.BUILD_MISSING


def skip_requested(environ: Mapping[str, str]) -> bool:
    return environ.get(SKIP_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def source_digest(crate: Path) -> str:
    """빌드 결과에 영향을 주는 파일의 상대 경로와 내용으로 결정적인 지문을 만든다."""
    files = [crate / "Cargo.toml", crate / "Cargo.lock", crate / "pyproject.toml"]
    files.extend(sorted((crate / "src").rglob("*.rs")))
    digest = hashlib.sha256()
    for path in files:
        if not path.is_file():
            continue
        # OS별 경로 구분자 차이가 지문을 바꾸지 않게 POSIX 형식으로 적는다.
        digest.update(path.relative_to(crate).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _venv(backend: Path) -> Path:
    return backend / ".venv"


def read_stamp(backend: Path) -> str | None:
    try:
        return (_venv(backend) / STAMP_NAME).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_stamp(backend: Path, digest: str) -> None:
    (_venv(backend) / STAMP_NAME).write_text(digest + "\n", encoding="utf-8")


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
    environment = dict(environ)
    # 루트 uv 환경을 backend 가상환경으로 오인하지 않게 한다(dev._start와 같은 이유).
    environment.pop("VIRTUAL_ENV", None)
    if cargo is not None:
        # maturin은 PATH의 cargo를 쓴다. rustup 기본 위치만 있는 셸도 빌드되게 앞에 붙인다.
        path = environment.get("PATH", "")
        environment["PATH"] = os.pathsep.join(filter(None, (str(cargo.parent), path)))
    return environment


def _flush_print(message: str) -> None:
    print(message, flush=True)


def _uv_command(uv: str, backend: Path, *arguments: str) -> list[str]:
    return [uv, "run", "--no-sync", "--project", str(backend), *arguments]


def installed(
    uv: str, backend: Path, environment: Mapping[str, str], run: Runner
) -> bool:
    completed = run(
        _uv_command(uv, backend, "python", "-c", "import backtest_core"),
        cwd=backend,
        env=dict(environment),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def build(uv: str, backend: Path, environment: Mapping[str, str], run: Runner) -> bool:
    # 진행 상황은 cargo·maturin 출력을 그대로 터미널에 보인다.
    completed = run(
        _uv_command(
            uv,
            backend,
            "maturin",
            "develop",
            "--manifest-path",
            MANIFEST.as_posix(),
            "--release",
        ),
        cwd=backend,
        env=dict(environment),
        check=False,
    )
    return completed.returncode == 0


def ensure_core(
    root: Path,
    *,
    disabled: bool,
    environ: Mapping[str, str] | None = None,
    run: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    log: Callable[[str], None] | None = None,
) -> Action:
    """필요하면 빌드하고 결과를 알린다. 어떤 경우에도 예외로 시작을 막지 않는다."""
    if log is None:
        # 빌드 출력보다 안내가 먼저 보이게 즉시 내보낸다.
        log = _flush_print
    source = os.environ if environ is None else environ
    backend = root / "backend"
    disabled = disabled or skip_requested(source)
    uv = which("uv")
    environment_ready = uv is not None and _venv(backend).is_dir()
    cargo = find_cargo(source, which) if not disabled and environment_ready else None
    environment = build_environment(source, cargo)
    state = CoreState(
        disabled=disabled,
        environment_ready=environment_ready,
        installed=(
            environment_ready
            and not disabled
            and installed(str(uv), backend, environment, run)
        ),
        built_digest=read_stamp(backend),
        source_digest=source_digest(backend / MANIFEST.parent),
        toolchain=cargo is not None,
    )
    action = decide(state)
    if action is Action.SKIP_NO_ENVIRONMENT:
        log(
            "[dev] backend 가상환경이 없어 Rust 코어 확인을 건너뜁니다. 먼저 uv sync를 실행해 주세요."
        )
    elif action is Action.WARN_NO_TOOLCHAIN:
        log(
            "[dev] 경고: Rust 툴체인(cargo)을 찾지 못해 Rust 백테스트 코어를 빌드하지 않습니다. "
            "백테스트 실행 설정에서 Python reference 코어만 쓸 수 있습니다. "
            + INSTALL_HINT
        )
    elif action in (Action.BUILD_MISSING, Action.BUILD_STALE):
        reason = (
            "설치되어 있지 않아" if action is Action.BUILD_MISSING else "소스가 바뀌어"
        )
        log(
            f"[dev] Rust 백테스트 코어가 {reason} 빌드합니다(release, 처음에는 몇 분 걸릴 수 있음)."
        )
        if build(str(uv), backend, environment, run):
            write_stamp(backend, state.source_digest)
            log("[dev] Rust 백테스트 코어 빌드 완료.")
        else:
            log(
                "[dev] 경고: Rust 백테스트 코어 빌드에 실패했습니다. 서버는 계속 시작하며 "
                "백테스트는 Python reference 코어로만 실행할 수 있습니다. 위 빌드 로그를 확인해 주세요."
            )
    return action
