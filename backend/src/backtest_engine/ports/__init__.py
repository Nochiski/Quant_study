"""포트(port): 엔진 도메인이 외부 세계에 요구하는 인터페이스 정의.

헥사고날 아키텍처의 안쪽 경계다. 여기 있는 타입은 도메인 타입(`types/`)만
참조하고, 파일 형식·벤더 SDK·DB 드라이버는 절대 import 하지 않는다.
구체 구현은 `backtest_engine.adapters`에 둔다.
"""

from backtest_engine.ports.corporate_actions import (
    CorporateActionQuery,
    CorporateActionResult,
    CorporateActionSource,
)
from backtest_engine.ports.execution import SlippageModel
from backtest_engine.ports.market_data import (
    BarQuery,
    BarSource,
    LoadResult,
    LoadStatus,
    OhlcPolicy,
)
from backtest_engine.ports.universe import (
    Membership,
    UniverseQuery,
    UniverseResult,
    UniverseSource,
)

__all__ = [
    "BarQuery",
    "BarSource",
    "CorporateActionQuery",
    "CorporateActionResult",
    "CorporateActionSource",
    "LoadResult",
    "LoadStatus",
    "Membership",
    "OhlcPolicy",
    "SlippageModel",
    "UniverseQuery",
    "UniverseResult",
    "UniverseSource",
]
