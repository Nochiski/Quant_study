"""Declared dependencies for application.factor_research."""

# domain.backtest: 실행 설정이 없는 sandbox 요청이 실행과 같은 기본 결측 정책
# (`DEFAULT_MISSING_POLICY`)을 읽는다(P2-02 리뷰 P1 → P2-03). application 이 기본값을 다시
# 적지 않게 한다.
# domain.strategy: 실행 계획 설명이 compile 이 붙인 승격 노드를 표식으로 내보낸다(P3-01, Phase 2
# 감사 #13). 승격 노드 이름 규칙의 owner 는 `domain/strategy/_promotion.py` 하나다.
DEPENDS_ON: tuple[str, ...] = ("domain.backtest", "domain.factor", "domain.strategy")
