"""Declared dependencies for domain.portfolio."""

# domain.backtest: 체결 시점(`RunEnvironment.timing`)이 1.2 부터 실행 설정의 사실이라 tape 컴파일이
# 인자로 받는다(P2-03). `domain.backtest` 는 `domain.portfolio` 를 읽지 않으므로 순환이 아니다.
DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.strategy")
