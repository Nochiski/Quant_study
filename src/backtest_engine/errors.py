"""엔진 전역 예외 계층.

예상된 도메인 실패(Capability 거절, 미선언 데이터 접근)와
예상하지 못한 무결성 위반(시간 역행, 음수 현금)을 구분해 정의한다.
"""

from __future__ import annotations


class EngineError(Exception):
    """backtest_engine이 발생시키는 모든 예외의 공통 베이스."""


class CapabilityNotImplemented(EngineError):
    """전략 요구사항 중 현재 엔진이 실행할 수 없는 항목이 있어 실행 전에 거절함.

    데이터 루프를 시작하기 전, 전략 등록 직후에 발생해야 한다.
    """


class UndeclaredDataAccess(EngineError):
    """requirements()에 선언하지 않은 HistoryRequest를 ctx.history()로 조회함."""


class UndeclaredActionReturned(EngineError):
    """requirements().actions에 선언하지 않은 ActionKind를 Decision으로 반환함."""


class UndeclaredFeatureUsed(EngineError):
    """requirements().features에 선언하지 않은 EngineFeature가 필요한 Action을 반환함."""


class UnknownOrderId(EngineError):
    """Cancel/Replace 대상 order_id가 대기 중인 주문이 아님 (없거나 이미 종료됨)."""


class CorporateActionWithoutBar(EngineError):
    """자본변동 사건 세션에 해당 종목 Bar가 없어 단주 정산 가격을 정할 수 없음."""


class UniverseNotProvided(EngineError):
    """run()에 universe를 넘기지 않았는데 ctx.universe()를 조회함."""


class CorporateActionsNotProvided(EngineError):
    """CORPORATE_ACTION을 선언한 전략인데 run()에 corporate_actions가 없음 (조용한 0건 방지)."""


class EquityWipedOut(EngineError):
    """세션 종료 평가에서 equity가 0 미만 (신용·공매도 손실이 자본을 초과). 계속 진행 불가."""


class CoreUnavailable(EngineError):
    """요청한 코어(core="rust" 등)가 설치돼 있지 않거나 알 수 없는 이름. 조용한 fallback 금지."""


class SchemaVersionMismatch(EngineError):
    """StrategyDecision.schema_version이 엔진이 아는 버전과 다름."""


class TimeReversalError(EngineError):
    """동일 instrument에서 Bar timestamp가 역행함. 데이터 정합성 위반으로 즉시 중단."""


class InsufficientHistoryError(EngineError):
    """선언한 lookback보다 확보된 세션 수가 적은 상태에서 window를 요청함."""


class InstrumentNotInSnapshot(EngineError):
    """MarketSnapshot에 존재하지 않는 instrument의 Bar를 조회함."""


class UnsupportedActionValue(EngineError):
    """Action 스키마는 유효하지만 값이 미구현 기능을 요구함 (예: 음수 비중 = 공매도)."""


class NegativeCashError(EngineError):
    """Fill 적용 결과 현금이 음수가 됨. MARGIN 미구현 상태에서는 무결성 위반."""


class NegativePositionError(EngineError):
    """Fill 적용 결과 보유 수량이 음수가 됨. SHORT_SELLING 미구현 상태에서는 무결성 위반."""
