import os
from pathlib import Path

from strategy_workbench.adapters.inbound.http_api.facade.api import create_app

from ._container import build_container

DEFAULT_STRATEGY_REPOSITORY_PATH = (
    Path(__file__).resolve().parents[3] / ".local" / "strategy-revisions.sqlite3"
)
STRATEGY_REPOSITORY_PATH_ENV = "STRATEGY_WORKBENCH_DB_PATH"
EQUITY_ADAPTER_ENV = "STRATEGY_WORKBENCH_EQUITY_ADAPTER"
EQUITY_ROOT_ENV = "STRATEGY_WORKBENCH_EQUITY_ROOT"
ALLOWED_ORIGINS_ENV = "STRATEGY_WORKBENCH_ALLOWED_ORIGINS"
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = ("http://localhost:5173",)


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


def build_http_app(
    *,
    strategy_repository_path: str | Path | None = None,
    equity_adapter: str = "mock",
    equity_root: Path | None = None,
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS,
):  # return type is inferred from the FastAPI factory at this composition root
    container = build_container(
        strategy_repository_path=strategy_repository_path,
        equity_adapter=equity_adapter,
        equity_root=equity_root,
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
        allowed_origins=allowed_origins,
    )


def build_runtime_http_app():
    """Uvicorn factory: only an actual server process opens the durable runtime database."""
    adapter, root = runtime_equity_selection()
    return build_http_app(
        strategy_repository_path=runtime_strategy_repository_path(),
        equity_adapter=adapter,
        equity_root=root,
        allowed_origins=runtime_allowed_origins(),
    )
