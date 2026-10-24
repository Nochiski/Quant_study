import os
from pathlib import Path

from strategy_workbench.adapters.inbound.http_api.facade.api import create_app
from strategy_workbench.application.backtest_run.facade.runs import DEFAULT_RUN_SLOTS

from . import _secrets
from ._assistant import (
    DEFAULT_ASSISTANT_SETTINGS,
    PROVIDER_ADAPTER_FACTORIES,
    AssistantSettings,
    scripted_provider_factories,
)
from ._container import DEFAULT_RUN_ISOLATION, build_container

DEFAULT_STRATEGY_REPOSITORY_PATH = (
    Path(__file__).resolve().parents[3] / ".local" / "strategy-revisions.sqlite3"
)
DEFAULT_ASSISTANT_DB_PATH = Path(__file__).resolve().parents[3] / ".local" / "assistant.sqlite3"
DEFAULT_RESEARCH_DB_PATH = Path(__file__).resolve().parents[3] / ".local" / "research.sqlite3"
DEFAULT_CONTINUOUS_DB_PATH = (
    Path(__file__).resolve().parents[3] / ".local" / "continuous-accounts.sqlite3"
)
STRATEGY_REPOSITORY_PATH_ENV = "STRATEGY_WORKBENCH_DB_PATH"
ARTIFACT_ROOT_ENV = "STRATEGY_WORKBENCH_ARTIFACT_ROOT"
RESEARCH_DB_PATH_ENV = "STRATEGY_WORKBENCH_RESEARCH_DB_PATH"
CONTINUOUS_DB_PATH_ENV = "STRATEGY_WORKBENCH_CONTINUOUS_DB_PATH"
EQUITY_ADAPTER_ENV = "STRATEGY_WORKBENCH_EQUITY_ADAPTER"
EQUITY_ROOT_ENV = "STRATEGY_WORKBENCH_EQUITY_ROOT"
ALLOWED_ORIGINS_ENV = "STRATEGY_WORKBENCH_ALLOWED_ORIGINS"
RUN_SLOTS_ENV = "STRATEGY_WORKBENCH_RUN_SLOTS"
E2E_TRIAL_HOLD_SECONDS_ENV = "STRATEGY_WORKBENCH_E2E_TRIAL_HOLD_SECONDS"
RUN_ISOLATION_ENV = "STRATEGY_WORKBENCH_RUN_ISOLATION"
RUN_MEMORY_RESERVE_GB_ENV = "STRATEGY_WORKBENCH_RUN_MEMORY_RESERVE_GB"
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = ("http://localhost:5173",)
ASSISTANT_DB_PATH_ENV = "STRATEGY_WORKBENCH_ASSISTANT_DB_PATH"
ASSISTANT_SECRETS_PATH_ENV = "STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH"
ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV = "STRATEGY_WORKBENCH_ASSISTANT_ALLOW_INSECURE_BASE_URL"
ASSISTANT_FAKE_PROVIDER_ENV = "STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER"
# AI 공급자·키움 모의 연결을 저장하는 `.env` 경로. 없으면 내 문서 `auto_trading/.env`다.
# 테스트·e2e는
# 반드시 임시 경로로 바꾼다 — 그러지 않으면 실행이 개발자의 실제 문서 폴더에 쓴다.
CREDENTIALS_ENV_PATH_ENV = "STRATEGY_WORKBENCH_CREDENTIALS_ENV_PATH"
# 브라우저 e2e 전용. 키움 "연결 확인"을 대본 probe로 바꾼다. 정확히 "1"일 때만 켠다.
BROKER_FAKE_PROBE_ENV = "STRATEGY_WORKBENCH_BROKER_FAKE_PROBE"


def runtime_strategy_repository_path() -> Path:
    configured = os.environ.get(STRATEGY_REPOSITORY_PATH_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_STRATEGY_REPOSITORY_PATH


def runtime_research_db_path() -> Path:
    """실행 기록 DB(검증 랩 spec D3). 전략 DB 의 불변 트리거와 섞지 않으려고 파일을 나눈다."""
    configured = os.environ.get(RESEARCH_DB_PATH_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_RESEARCH_DB_PATH


def runtime_continuous_db_path() -> Path:
    """일반 실험·전략 기록과 분리한 연속 평가 DB 경로."""
    configured = os.environ.get(CONTINUOUS_DB_PATH_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_CONTINUOUS_DB_PATH


def runtime_artifact_root() -> Path | None:
    """프로세스가 소유할 산출물 루트. 미설정 기본값은 컨테이너가 소유한다."""
    configured = os.environ.get(ARTIFACT_ROOT_ENV)
    return Path(configured).expanduser() if configured else None


def runtime_run_slots() -> int:
    """동시 실행 슬롯 수(검증 랩 spec D6). 없으면 실행 유스케이스의 기본값이다."""
    configured = os.environ.get(RUN_SLOTS_ENV, "").strip()
    if not configured:
        return DEFAULT_RUN_SLOTS
    if not configured.isdigit() or int(configured) < 1:
        raise ValueError(
            f"run slots env var must be a positive integer — {RUN_SLOTS_ENV}={configured!r}"
        )
    return int(configured)


def runtime_trial_hold_seconds() -> float:
    """e2e 훅: 실험 trial run 을 붙잡아 둘 초(검증 랩 spec D6). 없거나 0 이면 붙잡지 않는다."""
    configured = os.environ.get(E2E_TRIAL_HOLD_SECONDS_ENV, "").strip()
    try:
        seconds = float(configured) if configured else 0.0
    except ValueError:
        seconds = -1.0
    if not 0 <= seconds < float("inf"):
        raise ValueError(
            "trial hold env var must be a non-negative number of seconds — "
            f"{E2E_TRIAL_HOLD_SECONDS_ENV}={configured!r}"
        )
    return seconds


def runtime_run_memory_reserve_gb() -> float | None:
    """메모리 가드가 run 마다 잡는 예약치를 고정하는 GB(run-worker spec D8). 없으면 추정식이다."""
    configured = os.environ.get(RUN_MEMORY_RESERVE_GB_ENV, "").strip()
    if not configured:
        return None
    try:
        reserve_gb = float(configured)
    except ValueError:
        reserve_gb = -1.0
    if not 0 < reserve_gb < float("inf"):
        raise ValueError(
            "run memory reserve env var must be a positive number of GB — "
            f"{RUN_MEMORY_RESERVE_GB_ENV}={configured!r}"
        )
    return reserve_gb


def runtime_run_isolation() -> str:
    """run 계산을 어디서 돌리는가(`thread`·`process`, run-worker spec D9). 없으면 기본값이다.

    허용 값 판정은 `build_container` 가 한다 — 모르는 값은 스레드로 돌아가지 않고 멈춘다.
    """
    return os.environ.get(RUN_ISOLATION_ENV, "").strip() or DEFAULT_RUN_ISOLATION


def runtime_equity_selection() -> tuple[str, Path | None]:
    """Read the equity adapter selection from the environment.

    The server process is the only place that knows which equity layer to open, so the
    choice arrives as environment variables rather than a hard-coded default. Selecting
    ``duckdb`` without a root fails in ``build_container`` with an explicit message.
    """
    adapter = os.environ.get(EQUITY_ADAPTER_ENV, "mock").strip() or "mock"
    configured_root = os.environ.get(EQUITY_ROOT_ENV)
    root = Path(configured_root).expanduser() if configured_root else None
    return adapter, root


def runtime_allowed_origins() -> tuple[str, ...]:
    """브라우저가 이 서버를 부를 수 있는 origin 목록(쉼표로 구분).

    기본값은 개발 서버 하나다. 포트를 옮겨 e2e를 돌릴 때(워크트리별 `PW_PREVIEW_PORT`) 그
    origin 이 목록에 없으면 브라우저 요청이 CORS 로 막혀 화면이 빈다 — 서버는 멀쩡한데 화면만
    죽은, 원인을 짚기 어려운 조합이다. 어떤 주소를 허용할지는 프로세스를 띄우는 쪽만 알므로
    환경 변수로 받는다.
    """
    configured = os.environ.get(ALLOWED_ORIGINS_ENV)
    if configured is None:
        return DEFAULT_ALLOWED_ORIGINS
    # 값이 있는데 origin 이 하나도 없으면 전부 같은 결말이다: 공백만("   ")과 쉼표만(" , ")이
    # 갈라지면 같은 의도의 입력이 다르게 끝난다(1차 리뷰 P3-12).
    origins = tuple(part.strip() for part in configured.split(",") if part.strip())
    if not origins:
        raise ValueError(
            "allowed origins env var is set but lists no origin — "
            f"{ALLOWED_ORIGINS_ENV}={configured!r}"
        )
    return origins


def runtime_assistant_settings() -> AssistantSettings:
    """환경 변수에서 어시스턴트 배포 설정을 읽는다 (설계 spec D5/D6).

    `..._SECRETS_PATH`가 비어 있으면 OS 기본 사용자 설정 위치를 그대로 쓴다. 그 경로를 계산하는
    owner는 어댑터이고 composition root가 다시 유도하지 않는다.
    `..._ALLOW_INSECURE_BASE_URL`은 로컬 프록시용 예외이며 정확히 `"1"`일 때만 켜진다 — 알 수
    없는 값은 꺼진 채로 두어, 오타가 허용되는 base URL 범위를 넓히지 못하게 한다.

    `..._FAKE_PROVIDER=1`은 공급자 레지스트리를 대본 adapter 한 벌로 **덮어쓴다**(WORKFLOW B-05).
    브라우저 e2e가 실 SDK·키·네트워크 없이 설정 등록부터 제안 적용까지 돌기 위한 통로이며, 같은
    규칙으로 `"1"`이 아닌 값은 전부 꺼진 것으로 본다 — 실행 중인 서버가 대본을 답하고 있는지는
    사용자가 화면에서 구분할 수 없으므로 켜는 자리를 하나로 좁힌다.
    """
    configured_db = os.environ.get(ASSISTANT_DB_PATH_ENV)
    configured_secrets = os.environ.get(ASSISTANT_SECRETS_PATH_ENV)
    configured_credentials = os.environ.get(CREDENTIALS_ENV_PATH_ENV)
    fake_provider = os.environ.get(ASSISTANT_FAKE_PROVIDER_ENV, "").strip() == "1"
    return AssistantSettings(
        db_path=Path(configured_db).expanduser() if configured_db else DEFAULT_ASSISTANT_DB_PATH,
        secrets_path=Path(configured_secrets).expanduser() if configured_secrets else None,
        credentials_env_path=(
            Path(configured_credentials).expanduser()
            if configured_credentials
            # 모듈 속성으로 부른다. 테스트 격리가 이 기본값을 tmp로 바꾼다(#211과 같은 방식).
            else _secrets.default_credentials_env_path()
        ),
        allow_insecure_base_url=(
            os.environ.get(ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV, "").strip() == "1"
        ),
        provider_factories=(
            scripted_provider_factories() if fake_provider else PROVIDER_ADAPTER_FACTORIES
        ),
    )


def runtime_broker_fake_probe() -> bool:
    return os.environ.get(BROKER_FAKE_PROBE_ENV, "").strip() == "1"


def build_http_app(
    *,
    strategy_repository_path: str | Path | None = None,
    research_db_path: str | Path | None = None,
    continuous_db_path: str | Path | None = None,
    artifact_root: Path | None = None,
    run_slots: int = DEFAULT_RUN_SLOTS,
    trial_hold_seconds: float = 0.0,
    run_isolation: str = DEFAULT_RUN_ISOLATION,
    run_memory_reserve_gb: float | None = None,
    equity_adapter: str = "mock",
    equity_root: Path | None = None,
    assistant: AssistantSettings = DEFAULT_ASSISTANT_SETTINGS,
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS,
    broker_fake_probe: bool = False,
):  # return type is inferred from the FastAPI factory at this composition root
    container = build_container(
        strategy_repository_path=strategy_repository_path,
        research_db_path=research_db_path,
        continuous_db_path=continuous_db_path,
        artifact_root=artifact_root,
        run_slots=run_slots,
        trial_hold_seconds=trial_hold_seconds,
        run_isolation=run_isolation,
        run_memory_reserve_gb=run_memory_reserve_gb,
        equity_adapter=equity_adapter,
        equity_root=equity_root,
        assistant=assistant,
        broker_fake_probe=broker_fake_probe,
    )
    return create_app(
        strategy_design=container.strategy_design,
        strategy_authoring=container.strategy_authoring,
        strategy_documents=container.strategy_documents,
        strategy_drafts=container.strategy_drafts,
        equity_workspace=container.equity_workspace,
        factor_research=container.factor_research,
        portfolio_design=container.portfolio_design,
        strategy_traces=container.strategy_traces,
        backtest_runs=container.backtest_runs,
        experiments=container.experiments,
        continuous_accounts=container.continuous_accounts,
        factor_library=container.factor_library,
        broker_connections=container.broker_connections,
        assistant_profiles=container.assistant_profiles,
        assistant_chat=container.assistant_chat,
        assistant_turns=container.assistant_turns,
        credentials_file_path=container.credentials_file_path,
        allowed_origins=allowed_origins,
    )


def build_runtime_http_app():
    """Uvicorn factory: only an actual server process opens the durable runtime database."""
    adapter, root = runtime_equity_selection()
    return build_http_app(
        strategy_repository_path=runtime_strategy_repository_path(),
        research_db_path=runtime_research_db_path(),
        continuous_db_path=runtime_continuous_db_path(),
        artifact_root=runtime_artifact_root(),
        run_slots=runtime_run_slots(),
        trial_hold_seconds=runtime_trial_hold_seconds(),
        run_isolation=runtime_run_isolation(),
        run_memory_reserve_gb=runtime_run_memory_reserve_gb(),
        equity_adapter=adapter,
        equity_root=root,
        assistant=runtime_assistant_settings(),
        broker_fake_probe=runtime_broker_fake_probe(),
        allowed_origins=runtime_allowed_origins(),
    )
