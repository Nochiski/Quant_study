import os
from pathlib import Path

from strategy_workbench.adapters.inbound.http_api.facade.api import create_app

from ._assistant import DEFAULT_ASSISTANT_SETTINGS, AssistantSettings
from ._container import build_container

DEFAULT_STRATEGY_REPOSITORY_PATH = (
    Path(__file__).resolve().parents[3] / ".local" / "strategy-revisions.sqlite3"
)
DEFAULT_ASSISTANT_DB_PATH = Path(__file__).resolve().parents[3] / ".local" / "assistant.sqlite3"
STRATEGY_REPOSITORY_PATH_ENV = "STRATEGY_WORKBENCH_DB_PATH"
EQUITY_ADAPTER_ENV = "STRATEGY_WORKBENCH_EQUITY_ADAPTER"
EQUITY_ROOT_ENV = "STRATEGY_WORKBENCH_EQUITY_ROOT"
ASSISTANT_DB_PATH_ENV = "STRATEGY_WORKBENCH_ASSISTANT_DB_PATH"
ASSISTANT_SECRETS_PATH_ENV = "STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH"
ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV = "STRATEGY_WORKBENCH_ASSISTANT_ALLOW_INSECURE_BASE_URL"


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


def runtime_assistant_settings() -> AssistantSettings:
    """환경 변수에서 어시스턴트 배포 설정을 읽는다 (설계 spec D5/D6).

    `..._SECRETS_PATH`가 비어 있으면 OS 기본 사용자 설정 위치를 그대로 쓴다. 그 경로를 계산하는
    owner는 어댑터이고 composition root가 다시 유도하지 않는다.
    `..._ALLOW_INSECURE_BASE_URL`은 로컬 프록시용 예외이며 정확히 `"1"`일 때만 켜진다 — 알 수
    없는 값은 꺼진 채로 두어, 오타가 허용되는 base URL 범위를 넓히지 못하게 한다.
    """
    configured_db = os.environ.get(ASSISTANT_DB_PATH_ENV)
    configured_secrets = os.environ.get(ASSISTANT_SECRETS_PATH_ENV)
    return AssistantSettings(
        db_path=Path(configured_db).expanduser() if configured_db else DEFAULT_ASSISTANT_DB_PATH,
        secrets_path=Path(configured_secrets).expanduser() if configured_secrets else None,
        allow_insecure_base_url=(
            os.environ.get(ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV, "").strip() == "1"
        ),
    )


def build_http_app(
    *,
    strategy_repository_path: str | Path | None = None,
    equity_adapter: str = "mock",
    equity_root: Path | None = None,
    assistant: AssistantSettings = DEFAULT_ASSISTANT_SETTINGS,
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
    )


def build_runtime_http_app():
    """Uvicorn factory: only an actual server process opens the durable runtime database."""
    adapter, root = runtime_equity_selection()
    return build_http_app(
        strategy_repository_path=runtime_strategy_repository_path(),
        equity_adapter=adapter,
        equity_root=root,
        assistant=runtime_assistant_settings(),
    )
