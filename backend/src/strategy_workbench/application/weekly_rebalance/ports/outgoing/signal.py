"""Outgoing port: 모델 신호(순위)와 그 신호를 낸 날의 기준가격."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

__all__ = [
    "EarlierClose",
    "SignalBasis",
    "SignalRead",
    "SignalReadStatus",
    "SignalSnapshot",
    "SignalSource",
]


class SignalBasis(StrEnum):
    """어느 모델 빌드를 읽는가. 값은 모델 DB 빌드의 `basis` 그대로다."""

    EVENING = "evening"  # 장 마감 빌드(신호일 15:30~16:00 가격·그날까지 받은 추정치)
    # 주말 보강판: 휴장 첫날 아침에 신호일 확정 가격과 신호일 분 WISE 추정치로 다시 지은 빌드
    # (2026-10-23 사용자 결정 — 금요일 추정치가 토요일 새벽에 올라온다)
    SUPPLEMENT = "supplement"


class SignalReadStatus(StrEnum):
    OK = "ok"
    NOT_READY = "not_ready"  # 그 날짜의 빌드가 아직 현재 빌드가 아니다
    GATE_FAILED = "gate_failed"  # 빌드 게이트가 통과하지 않았거나 모델이 제외됐다
    UNREADABLE = "unreadable"  # 파일이 없거나 형식·내용이 맞지 않는다


@dataclass(frozen=True)
class EarlierClose:
    """신호일 가격 행이 없는 종목(거래정지 등)의, 같은 가격 판에서 신호일 전 마지막 종가(원)."""

    day: date
    close_krw: int


@dataclass(frozen=True)
class SignalSnapshot:
    """한 빌드의 순위(1이 가장 좋다)와, 점수를 계산한 가격 판의 신호일 종가(원).

    직전 빌드의 순위는 데이터 누락 점검용이다. 직전 빌드가 없거나 읽지 못하면 None이다.
    """

    model_id: str
    build_id: str
    signal_date: date
    ranks: Mapping[str, int]
    closes_krw: Mapping[str, int]
    untraded: frozenset[str]  # 신호일 거래량 0
    scores_sha256: str
    prices_build_id: str
    previous_build_id: str | None = None
    previous_ranks: Mapping[str, int] | None = None
    # 종목 이름, 그리고 유니버스에 있었지만 순위가 없는 종목이 빠진 이유(사람이 읽는 문장).
    # 못 읽으면 None이다.
    names: Mapping[str, str] | None = None
    exclusion_reasons: Mapping[str, str] | None = None
    # 신호일 가격 행이 없는 종목의 그 전 마지막 종가. 보유 종목 평가에만 쓴다.
    earlier_closes: Mapping[str, EarlierClose] | None = None


@dataclass(frozen=True)
class SignalRead:
    status: SignalReadStatus
    snapshot: SignalSnapshot | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is SignalReadStatus.OK and self.snapshot is not None


class SignalSource(Protocol):
    def read(self, signal_date: date, basis: SignalBasis) -> SignalRead: ...
