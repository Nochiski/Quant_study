from strategy_workbench.adapters.inbound.http_api.facade.api import create_app

from ._container import build_container


def build_http_app():  # type annotation is inferred from FastAPI factory at this composition root
    container = build_container()
    return create_app(
        strategy_design=container.strategy_design,
        equity_workspace=container.equity_workspace,
        factor_research=container.factor_research,
        portfolio_design=container.portfolio_design,
    )


app = build_http_app()
