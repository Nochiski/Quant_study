"""Declared dependencies for application.factor_research."""

# domain.backtest: 실행 설정이 없는 sandbox 요청의 결측 정책 판정을 브리지 owner 에게
# 맡긴다(P2-02 리뷰 P1). application 이 `graph.missing_policy` 를 직접 읽지 않게 한다.
DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.factor")
