"""개발 서버 시작 전에 Rust 백테스트 코어(backtest_core) 확장을 준비한다.

코어가 없거나 현재 Rust 소스보다 오래됐는지는 backend가 판정한다
(`backtest_engine.engine.core_source`, 확장에 구운 소스 지문 비교). 이 도구는 그 상태를 물어
없음·낡음이면 이미 설치된 backend 환경 안에서 ``maturin develop --release``로 빌드만 한다
(``--no-sync``, 의존성 설치·동기화 없음). 낡은 확장이 남아도 엔진이 쓰지 않으므로 지우지 않는다.
툴체인이 없거나 빌드가 실패해도, 어떤 예외가 나도 서버 시작은 막지 않는다.
"""

from __future__ import annotations

import enum
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from .backend_env import backend_process_environment, backend_uv_command, backend_venv

# 이 환경 변수를 1·true·yes·on 중 하나로 두면 자동 빌드를 건너뛴다.
SKIP_ENV = "QUANT_STUDY_DEV_SKIP_RUST_BUILD"
MANIFEST = "rust/backtest_core/Cargo.toml"
# 패키지가 이미 이 모듈을 import하므로 -m 대신 함수를 직접 부른다(runpy 이중 import 경고 회피).
STATUS_SCRIPT = "from backtest_engine.engine.core_source import main; raise SystemExit(main(['status']))"
INSTALL_HINT = "Rust 툴체인(https://rustup.rs)을 설치한 뒤 uv run dev를 다시 실행하면 자동으로 빌드합니다."
FALLBACK_HINT = (
    "그동안 백테스트는 실행 설정에서 Python reference 코어를 선택해 돌릴 수 있습니다."
)

Runner = Callable[..., subprocess.CompletedProcess[bytes]]


class Action(enum.Enum):
    """시작 전 Rust 코어 처리 결과."""

    SKIP_DISABLED = "skip_disabled"
    SKIP_NO_ENVIRONMENT = "skip_no_environment"
    UP_TO_DATE = "up_to_date"
    BUILT = "built"
    WARN_NO_TOOLCHAIN = "warn_no_toolchain"
    WARN_BUILD_FAILED = "warn_build_failed"
    WARN_DIGEST_MISMATCH = "warn_digest_mismatch"
    WARN_UNKNOWN_STATUS = "warn_unknown_status"
    WARN_ERROR = "warn_error"


# backend `core_source.CoreStatus`와 같은 값. 이 도구는 backend 패키지를 import하지 않는다.
STATUSES = frozenset({"missing", "stale", "debug", "fresh", "unchecked"})
_REASONS = {
    "missing": "설치되어 있지 않습니다",
    "stale": "현재 Rust 소스보다 오래됐습니다",
    "debug": "디버그 빌드라 느립니다",
}


def decide(status: str, toolchain: bool) -> Action | None:
    """backend 상태로 할 일을 정한다. None이면 release로 빌드한다."""
    if status not in STATUSES:
        return Action.WARN_UNKNOWN_STATUS
    if status in ("fresh", "unchecked"):
        return Action.UP_TO_DATE
    if not toolchain:
        return Action.WARN_NO_TOOLCHAIN
    return None


def flag(environ: Mapping[str, str], name: str) -> bool:
    return environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def find_cargo(
    environ: Mapping[str, str],
    which: Callable[[str], str | None],
    home: Path | None = None,
) -> Path | None:
    """PATH에 없더라도 rustup 기본 위치(~/.cargo/bin)의 cargo를 찾는다."""
    found = which("cargo")
    if found:
        return Path(found)
    cargo_home = environ.get("CARGO_HOME")
    base = Path(cargo_home) if cargo_home else (home or Path.home()) / ".cargo"
    candidate = base / "bin" / ("cargo.exe" if os.name == "nt" else "cargo")
    return candidate if candidate.is_file() else None


def build_environment(environ: Mapping[str, str], cargo: Path | None) -> dict[str, str]:
    environment = backend_process_environment(environ)
    if cargo is not None:
        # maturin은 PATH의 cargo를 쓴다. rustup 기본 위치만 있는 셸도 빌드되게 앞에 붙인다.
        path = environment.get("PATH", "")
        environment["PATH"] = os.pathsep.join(filter(None, (str(cargo.parent), path)))
    return environment


def core_status(
    uv: str, backend: Path, environment: Mapping[str, str], run: Runner
) -> str:
    """backend 판정을 받는다. 출력의 마지막 줄만 상태로 본다(앞의 경고 등은 무시)."""
    completed = run(
        list(backend_uv_command(uv, backend, "python", "-c", STATUS_SCRIPT)),
        cwd=backend,
        env=dict(environment),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"코어 상태 확인이 실패했습니다(exit={completed.returncode}): "
            + completed.stderr.decode(errors="replace").strip()[-300:]
        )
    lines = completed.stdout.decode(errors="replace").strip().splitlines()
    return lines[-1].strip() if lines else ""


def build(uv: str, backend: Path, environment: Mapping[str, str], run: Runner) -> bool:
    # 진행 상황은 cargo·maturin 출력을 그대로 터미널에 보인다.
    command = backend_uv_command(
        uv, backend, "maturin", "develop", "--manifest-path", MANIFEST, "--release"
    )
    return (
        run(list(command), cwd=backend, env=dict(environment), check=False).returncode
        == 0
    )


def _flush_print(message: str) -> None:
    print(message, flush=True)


def ensure_core(
    root: Path,
    *,
    uv: str | None,
    disabled: bool,
    environ: Mapping[str, str] | None = None,
    run: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    log: Callable[[str], None] | None = None,
) -> Action:
    """필요하면 빌드하고 결과를 알린다. 어떤 예외도 서버 시작을 막지 않는다."""
    say = log or _flush_print
    source = os.environ if environ is None else environ
    if disabled or flag(source, SKIP_ENV):
        return Action.SKIP_DISABLED
    backend = root / "backend"
    if uv is None or not backend_venv(backend, source).is_dir():
        say(
            "[dev] backend 가상환경이 없어 Rust 코어 확인을 건너뜁니다. 먼저 uv sync를 실행해 주세요."
        )
        return Action.SKIP_NO_ENVIRONMENT
    try:
        return _ensure(uv, backend, source, run, which, say)
    except Exception as error:  # noqa: BLE001  # reason: 사전 준비의 어떤 실패도 서버 기동을 막지 않는다
        say(
            "[dev] 경고: Rust 백테스트 코어를 준비하지 못했습니다"
            f"({type(error).__name__}: {error}). 서버는 계속 시작합니다. "
            + FALLBACK_HINT
        )
        return Action.WARN_ERROR


def _ensure(
    uv: str,
    backend: Path,
    source: Mapping[str, str],
    run: Runner,
    which: Callable[[str], str | None],
    say: Callable[[str], None],
) -> Action:
    cargo = find_cargo(source, which)
    environment = build_environment(source, cargo)
    status = core_status(uv, backend, environment, run)
    action = decide(status, cargo is not None)
    if action is Action.UP_TO_DATE:
        return action
    if action is Action.WARN_UNKNOWN_STATUS:
        say(
            f"[dev] 경고: backend가 알 수 없는 Rust 코어 상태를 돌려줘 빌드하지 않습니다"
            f"(status={status!r}). 서버는 계속 시작합니다."
        )
        return action
    reason = _REASONS[status]
    if action is Action.WARN_NO_TOOLCHAIN:
        say(
            f"[dev] 경고: Rust 백테스트 코어가 {reason}. Rust 툴체인(cargo)을 찾지 못해 빌드하지 "
            f"않습니다. {INSTALL_HINT} {FALLBACK_HINT}"
        )
        return action
    say(
        f"[dev] Rust 백테스트 코어가 {reason}. release로 빌드합니다"
        "(처음에는 몇 분 걸릴 수 있음)."
    )
    if not build(uv, backend, environment, run):
        say(
            "[dev] 경고: Rust 백테스트 코어 빌드에 실패했습니다. 위 빌드 로그를 확인해 주세요. "
            "다음 uv run dev에서 다시 시도하며, 끄려면 --no-rust-build를 씁니다. "
            + FALLBACK_HINT
        )
        return Action.WARN_BUILD_FAILED
    after = core_status(uv, backend, environment, run)
    if after == "fresh":
        say("[dev] Rust 백테스트 코어 빌드 완료.")
        return Action.BUILT
    say(
        "[dev] 경고: 빌드는 성공했지만 설치된 Rust 코어가 현재 소스와 맞지 않습니다"
        f"(status={after!r}). 빌드하는 동안 소스가 바뀌었거나, 확장이 다른 Python 환경에"
        " 설치됐거나, build.rs와 backend의 지문 규칙이 어긋났을 수 있습니다. "
        "다음 uv run dev에서 다시 시도합니다. " + FALLBACK_HINT
    )
    return Action.WARN_DIGEST_MISMATCH
