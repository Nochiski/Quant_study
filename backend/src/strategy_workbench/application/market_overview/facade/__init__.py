"""Declared dependencies for application.market_overview.

홈 시장 블록(지수·투자자별 순매수·섹터맵) 조회. 원장 행은 자기 outgoing `MarketDataPort`로만 읽고
계산 규칙은 `domain.market`이 소유한다. 연습용(mock) 원천에서는 포트 없이 만들어 시장 숫자를
지어내지 않는다.
"""

DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.market")
