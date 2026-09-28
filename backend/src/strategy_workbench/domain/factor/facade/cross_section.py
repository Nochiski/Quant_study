"""횡단면 정규화 공식의 공개 지점.

같은 기준일의 동료 집단 안에서 값을 0~1 순위나 표준화 점수로 바꾸는 규칙은 `domain.factor` 가
소유한다. 팩터 그래프의 `CrossSectionalOperator` 와 전략 문서의 `signal.normalization`(P2-04,
`domain.portfolio` 컴파일러가 결합 전에 적용) 이 같은 정의를 읽어야 같은 문서가 두 가지 순위를
내지 않는다.
"""

from strategy_workbench.domain.factor._statistics import (
    cross_sectional_rank,
    cross_sectional_zscore,
)

__all__ = [
    "cross_sectional_rank",
    "cross_sectional_zscore",
]
