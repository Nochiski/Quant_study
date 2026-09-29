"""Declared dependencies for application.experiment_run.

trial 실행은 자기 outgoing port(`TrialRunPort`)로만 하고 bootstrap 이 실행 서비스를 감싸 주입하므로
`application.backtest_run` 에는 의존하지 않는다(spec D6). `domain.backtest` 는 실행 요청·상태와 시도
키·원장 규칙의 값 타입이다.
"""

DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.experiment", "domain.strategy")
