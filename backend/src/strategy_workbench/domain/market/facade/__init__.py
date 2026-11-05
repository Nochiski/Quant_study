"""홈 시장 블록의 순수 계산: 지수 카드, 투자자별 순매수 기간 합·측정 비중·요약 문장, 섹터 가중 묶음.

원장 행을 읽지 않는다 — 행은 `application.market_overview`의 포트가 나른다. 지수 이름의 정본은
`domain.backtest`의 `MARKET_REFERENCE_INDICES`다.
"""

DEPENDS_ON: tuple[str, ...] = ("domain.backtest",)
