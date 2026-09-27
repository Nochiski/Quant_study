"""Declared dependencies for application.assistant_chat; exports use named modules.

`application.equity_workspace`는 그 노드가 소유한 outgoing port(`EquityDataPort`)만 소비한다
(backend-package-boundary.md의 application → application 허용 사유). 유스케이스 로직은 빌리지
않는다. `domain.strategy`는 시스템 프롬프트의 언어 요약을 runtime schema에서 생성하는 데,
`domain.factor`는 팩터 카탈로그 타입에 쓴다. `domain.backtest`·`domain.analytics`는 결과 설명
세션이 `BacktestResultPort`로 받은 실행 결과의 값 타입이다(결과 설명 spec R2). 결과를 읽는 쪽은
bootstrap이 감싼 포트라 `application.backtest_run`에는 의존하지 않는다.
"""

DEPENDS_ON: tuple[str, ...] = (
    "application.equity_workspace",
    "domain.analytics",
    "domain.assistant",
    "domain.backtest",
    "domain.factor",
    "domain.strategy",
)
