"""텔레그램 봇으로 주문지 알림을 보낸다(S07). 주소는 텔레그램 Bot API 하나다."""

DEPENDS_ON: tuple[str, ...] = ("application.weekly_rebalance",)
