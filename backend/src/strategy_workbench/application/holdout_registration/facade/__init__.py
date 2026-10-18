"""합성 사전등록 절차의 선언된 의존성."""

DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.canonical", "domain.strategy")
