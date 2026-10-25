"""신호 평가가 읽는 단방향 도메인 의존성."""

DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.factor", "domain.portfolio")
