"""Declared dependencies for application.equity_workspace; exports use named modules."""

# domain.paging: 카탈로그 페이지 요청 검증·창 계산의 정본(#375 DR-A-14).
DEPENDS_ON: tuple[str, ...] = ("domain.equity", "domain.paging")
