import os
from pathlib import Path

from strategy_workbench.adapters.inbound.http_api.facade.api import create_app

from ._container import build_container

DEFAULT_STRATEGY_REPOSITORY_PATH = (
    Path(__file__).resolve().parents[3] / ".local" / "strategy-revisions.sqlite3"
)
STRATEGY_REPOSITORY_PATH_ENV = "STRATEGY_WORKBENCH_DB_PATH"


def runtime_strategy_repository_path() -> Path:
    configured = os.environ.get(STRATEGY_REPOSITORY_PATH_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_STRATEGY_REPOSITORY_PATH


def build_http_app(
    *, strategy_repository_path: str | Path | None = None
):  # return type is inferred from the FastAPI factory at this composition root
    container = build_container(strategy_repository_path=strategy_repository_path)
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
    )


def build_runtime_http_app():
    """Uvicorn factory: only an actual server process opens the durable runtime database."""
    return build_http_app(strategy_repository_path=runtime_strategy_repository_path())
