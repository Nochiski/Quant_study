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
