"""CLI는 입력 해석·출력만 맡는다."""

DEPENDS_ON: tuple[str, ...] = ("application.weekly_rebalance", "domain.rebalance")
