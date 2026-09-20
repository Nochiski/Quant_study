import os
from pathlib import Path

from strategy_workbench.adapters.inbound.http_api.facade.api import create_app

from ._assistant import (
    DEFAULT_ASSISTANT_SETTINGS,
    PROVIDER_ADAPTER_FACTORIES,
    AssistantSettings,
    scripted_provider_factories,
)
from ._container import build_container

DEFAULT_STRATEGY_REPOSITORY_PATH = (
    Path(__file__).resolve().parents[3] / ".local" / "strategy-revisions.sqlite3"
)
DEFAULT_ASSISTANT_DB_PATH = Path(__file__).resolve().parents[3] / ".local" / "assistant.sqlite3"
STRATEGY_REPOSITORY_PATH_ENV = "STRATEGY_WORKBENCH_DB_PATH"
EQUITY_ADAPTER_ENV = "STRATEGY_WORKBENCH_EQUITY_ADAPTER"
EQUITY_ROOT_ENV = "STRATEGY_WORKBENCH_EQUITY_ROOT"
ALLOWED_ORIGINS_ENV = "STRATEGY_WORKBENCH_ALLOWED_ORIGINS"
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = ("http://localhost:5173",)
ASSISTANT_DB_PATH_ENV = "STRATEGY_WORKBENCH_ASSISTANT_DB_PATH"
ASSISTANT_SECRETS_PATH_ENV = "STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH"
ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV = "STRATEGY_WORKBENCH_ASSISTANT_ALLOW_INSECURE_BASE_URL"
ASSISTANT_FAKE_PROVIDER_ENV = "STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER"


def runtime_strategy_repository_path() -> Path:
    configured = os.environ.get(STRATEGY_REPOSITORY_PATH_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_STRATEGY_REPOSITORY_PATH


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
    configured = os.environ.get(ALLOWED_ORIGINS_ENV, "").strip()
    if not configured:
        return DEFAULT_ALLOWED_ORIGINS
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
    fake_provider = os.environ.get(ASSISTANT_FAKE_PROVIDER_ENV, "").strip() == "1"
    return AssistantSettings(
        db_path=Path(configured_db).expanduser() if configured_db else DEFAULT_ASSISTANT_DB_PATH,
        secrets_path=Path(configured_secrets).expanduser() if configured_secrets else None,
        allow_insecure_base_url=(
            os.environ.get(ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV, "").strip() == "1"
        ),
        provider_factories=(
            scripted_provider_factories() if fake_provider else PROVIDER_ADAPTER_FACTORIES
        ),
    )


def build_http_app(
    *,
    strategy_repository_path: str | Path | None = None,
    equity_adapter: str = "mock",
    equity_root: Path | None = None,
    assistant: AssistantSettings = DEFAULT_ASSISTANT_SETTINGS,
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS,
):  # return type is inferred from the FastAPI factory at this composition root
    container = build_container(
        strategy_repository_path=strategy_repository_path,
        equity_adapter=equity_adapter,
        equity_root=equity_root,
        assistant=assistant,
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
        assistant_profiles=container.assistant_profiles,
        assistant_chat=container.assistant_chat,
        assistant_turns=container.assistant_turns,
        allowed_origins=allowed_origins,
    )


def build_runtime_http_app():
    """Uvicorn factory: only an actual server process opens the durable runtime database."""
    adapter, root = runtime_equity_selection()
    return build_http_app(
        strategy_repository_path=runtime_strategy_repository_path(),
        equity_adapter=adapter,
        equity_root=root,
        assistant=runtime_assistant_settings(),
        allowed_origins=runtime_allowed_origins(),
    )
