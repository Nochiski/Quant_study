"""backtest_engine: 이벤트 드리븐 백테스트 엔진 (Python reference implementation).

전략은 판단만 하고, 엔진이 시장 시뮬레이션과 회계를 책임진다.
공개 API는 여기서 재노출하는 이름으로 고정한다.
"""

from backtest_engine.capability import (
    EngineCapabilities,
    SupportLevel,
    prepare_strategy,
    reference_engine_capabilities,
    validate_requirements,
)
from backtest_engine.engine.loop import BacktestEngine
from backtest_engine.types.results import BacktestResult, PerformanceMetrics, RunConfig

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "EngineCapabilities",
    "PerformanceMetrics",
    "RunConfig",
    "SupportLevel",
    "prepare_strategy",
    "reference_engine_capabilities",
    "validate_requirements",
]
