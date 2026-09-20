"""Declared dependencies for domain.portfolio."""

# domain.backtest: 체결 시점(`RunEnvironment.timing`)이 1.2 부터 실행 설정의 사실이라 tape 컴파일이
# 인자로 받는다(P2-03). `domain.backtest` 는 `domain.portfolio` 를 읽지 않으므로 순환이 아니다.
# domain.factor: `signal.normalization` 의 횡단면 순위·표준화 공식은 팩터 그래프의 횡단면
# 연산자와 같은 정의여야 하고, 그 정의는 `domain.factor` 가 소유한다(P2-04). `domain.factor` 의
# DEPENDS_ON 은 비어 있어 순환이 아니다.
DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.factor", "domain.strategy")
