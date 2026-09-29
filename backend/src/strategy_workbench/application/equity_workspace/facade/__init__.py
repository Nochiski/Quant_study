"""Declared dependencies for application.equity_workspace; exports use named modules."""

# domain.backtest: 두 미리보기(`preview_panel`·`preview`)가 연구 구간 잠금 판정(`require_research_window`)을 부른다(spec D1).
# 봉인 구간·연구 하한의 owner 는 `domain/backtest/_research_window.py` 하나다.
DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.equity")
