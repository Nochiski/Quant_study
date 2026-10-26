"""S07 리스크 한도 값(2026-10-26 사용자 결정). 값은 전략 코드 밖 한도 파일 하나가 갖는다.

한도 파일(`backend/scripts/weekly_rebalance_risk_limits.json`)의 칸을 검사해 값 타입으로 바꾼다.
기본값을 코드에 두지 않는다 — 수치가 두 곳에 있으면 한쪽만 바뀐다. 판정 기록마다 판본과 파일
SHA-256을 남겨, 어느 한도로 판정했는지 결과보다 먼저 남는다.

비율은 모두 bp 정수다. 낙폭 한도는 손실 크기(고점 대비 −10%면 1,000bp)로 적는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

BP = 10_000
LIMITS_SCHEMA = "s07-risk-limits/1"


class SecurityFlag(StrEnum):
    """거래소가 종목에 단 표시. 브로커 어휘는 어댑터가 이 값으로 옮긴다."""

    ADMIN = "admin"  # 관리종목
    HALTED = "halted"  # 거래정지
    LIQUIDATION = "liquidation"  # 정리매매
    INVESTMENT_WARNING = "investment_warning"  # 투자경고
    INVESTMENT_DANGER = "investment_danger"  # 투자위험
    OVERHEATED = "overheated"  # 단기과열
    CAUTION = "caution"  # 투자주의
    UNKNOWN = "unknown"  # 어댑터가 모르는 표시(막는 쪽으로 다룬다)


@dataclass(frozen=True)
class DrawdownLimits:
    """고점 대비 낙폭 단계. 축소에서 정상으로는 `recover_bp`보다 작게 회복해야 돌아온다."""

    initial_peak_krw: int  # 장 마감 기록이 생기기 전의 고점(운용 시작 자본)
    warn_bp: int
    reduce_bp: int
    halt_bp: int
    recover_bp: int
    reduced_exposure_bp: int  # 축소·정지 단계에서 목표 주식 수에 곱하는 비율

    def __post_init__(self) -> None:
        _positive(self, ("initial_peak_krw", "warn_bp", "reduce_bp", "halt_bp", "recover_bp"))
        _positive(self, ("reduced_exposure_bp",))
        if not self.warn_bp <= self.reduce_bp <= self.halt_bp < BP:
            raise ValueError(
                f"drawdown steps must grow — warn_bp={self.warn_bp} reduce_bp={self.reduce_bp} "
                f"halt_bp={self.halt_bp}"
            )
        if self.recover_bp > self.reduce_bp or self.reduced_exposure_bp > BP:
            raise ValueError(
                f"recover above reduce or exposure above 100% — recover_bp={self.recover_bp} "
                f"reduced_exposure_bp={self.reduced_exposure_bp}"
            )


@dataclass(frozen=True)
class RiskLimits:
    version: int
    sha256: str  # 한도 파일 바이트의 SHA-256
    drawdown: DrawdownLimits
    max_weight_bp: int  # 매수 뒤 한 종목 평가액 ÷ 총자산 상한
    max_adv_participation_bp: int  # 한 종목 매수 금액 ÷ 20일 평균 거래대금 상한
    near_limit_bp: int  # 상한가(매수)·하한가(매도)에서 이 폭 안이면 근처로 본다
    vi_unreleased_hold_minutes: int  # VI 해제 시각이 없을 때 발동 뒤 발동 중으로 보는 시간
    blocked_buy_flags: frozenset[SecurityFlag]

    def __post_init__(self) -> None:
        _positive(
            self,
            (
                "version",
                "max_weight_bp",
                "max_adv_participation_bp",
                "near_limit_bp",
                "vi_unreleased_hold_minutes",
            ),
        )
        too_large = self.max_weight_bp > BP or self.max_adv_participation_bp > BP
        if too_large or self.near_limit_bp >= BP:
            raise ValueError(
                f"ratio above 100% — max_weight_bp={self.max_weight_bp} "
                f"max_adv_participation_bp={self.max_adv_participation_bp} "
                f"near_limit_bp={self.near_limit_bp}"
            )


_DRAWDOWN_FIELDS = (
    "initial_peak_krw",
    "warn_bp",
    "reduce_bp",
    "halt_bp",
    "recover_bp",
    "reduced_exposure_bp",
)
_TOP_FIELDS = frozenset(
    {
        "schema",
        "version",
        "drawdown",
        "max_weight_bp",
        "max_adv_participation_bp",
        "near_limit_bp",
        "vi_unreleased_hold_minutes",
        "blocked_buy_flags",
        "changes",
    }
)


def parse_limits(payload: Mapping[str, object], sha256: str) -> RiskLimits:
    """한도 파일 내용을 검사한다. 칸이 빠졌거나 모르는 칸·범위 밖 값이 있으면 `ValueError`."""
    keys = set(payload)
    if keys != _TOP_FIELDS:
        raise ValueError(
            f"risk limits fields differ — missing={sorted(_TOP_FIELDS - keys)} "
            f"unknown={sorted(keys - _TOP_FIELDS)}"
        )
    if payload["schema"] != LIMITS_SCHEMA:
        raise ValueError(f"unknown risk limits schema — got={payload['schema']!r}")
    if not isinstance(payload["changes"], list) or not payload["changes"]:
        raise ValueError("risk limits need a change record — field=changes")
    drawdown = payload["drawdown"]
    if not isinstance(drawdown, Mapping) or set(drawdown) != set(_DRAWDOWN_FIELDS):
        raise ValueError(f"drawdown fields differ — expected={list(_DRAWDOWN_FIELDS)}")
    flags = payload["blocked_buy_flags"]
    known = {flag.value for flag in SecurityFlag}
    if not isinstance(flags, list) or any(flag not in known for flag in flags):
        raise ValueError(f"unknown security flag — blocked_buy_flags={flags!r}")
    return RiskLimits(
        version=_integer(payload, "version"),
        sha256=sha256,
        drawdown=DrawdownLimits(**{name: _integer(drawdown, name) for name in _DRAWDOWN_FIELDS}),
        max_weight_bp=_integer(payload, "max_weight_bp"),
        max_adv_participation_bp=_integer(payload, "max_adv_participation_bp"),
        near_limit_bp=_integer(payload, "near_limit_bp"),
        vi_unreleased_hold_minutes=_integer(payload, "vi_unreleased_hold_minutes"),
        blocked_buy_flags=frozenset(SecurityFlag(flag) for flag in flags),
    )


def _integer(values: Mapping[str, object], name: str) -> int:
    value = values[name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"risk limit must be an integer — {name}={value!r}")
    return value


def _positive(target: object, names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(target, name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"risk limit must be a positive integer — {name}={value!r}")
