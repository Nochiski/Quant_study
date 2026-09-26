"""Declared dependencies for application.factor_research."""

# domain.backtest: 실행 설정이 없는 sandbox 요청이 실행과 같은 기본 결측 정책
# (`DEFAULT_MISSING_POLICY`)을 읽는다(P2-02 리뷰 P1 → P2-03). application 이 기본값을 다시
# 적지 않게 한다.
DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.factor")
