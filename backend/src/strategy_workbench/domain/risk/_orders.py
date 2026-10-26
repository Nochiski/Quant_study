"""주문 하나를 내기 직전의 거부권(S07 리스크 가드 ②). 그 순간의 종목 상태로 판정한다.

- 막음(BLOCK): 한도 파일이 막는 표시(관리종목·거래정지·정리매매·투자경고·투자위험·모르는 표시)가
  있으면 매수를 내지 않는다. 매도는 막지 않는다(정리매매 종목은 팔아야 한다).
- 보류(HOLD): 그 회차만 내지 않고 다음 추적 회차에 다시 본다.
  - VI 발동 중이면 매수·매도 모두. 해제 시각이 없으면 발동 뒤 정한 시간 동안 발동 중으로 본다.
  - 매수 지정가가 상한가 근처(상한가에서 `near_limit_bp` 안)면 매수.
- 허용하되 기록: 하한가 근처 매도, 막지 않는 표시(단기과열·투자주의)가 붙은 매수.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from strategy_workbench.domain.risk._limits import BP, RiskLimits, SecurityFlag


@dataclass(frozen=True)
class ViWindow:
    """그날 VI 한 번. `release`가 None이면 해제 시각을 아직 모른다."""

    start: time
    release: time | None


@dataclass(frozen=True)
class TradeStatus:
    symbol: str
    flags: frozenset[SecurityFlag]
    upper_limit_krw: int
    lower_limit_krw: int
    vi: tuple[ViWindow, ...] = ()


class OrderVerdict(StrEnum):
    ALLOW = "allow"
    HOLD = "hold"  # 이번 회차만 내지 않는다
    BLOCK = "block"  # 내지 않는다


@dataclass(frozen=True)
class OrderCheck:
    verdict: OrderVerdict
    code: str = ""
    message: str = ""
    notes: tuple[str, ...] = ()


def check_order(
    side: str, limit_price_krw: int, status: TradeStatus, now: time, limits: RiskLimits
) -> OrderCheck:
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be buy or sell — side={side!r}")
    flags = sorted(flag.value for flag in status.flags)
    if side == "buy" and status.flags & limits.blocked_buy_flags:
        blocked = sorted(flag.value for flag in status.flags & limits.blocked_buy_flags)
        return OrderCheck(
            OrderVerdict.BLOCK,
            "status_blocked",
            f"매수를 막는 종목 표시 — symbol={status.symbol} flags={blocked}",
        )
    if any(_vi_active(window, now, limits) for window in status.vi):
        return OrderCheck(
            OrderVerdict.HOLD, "vi_active", f"VI 발동 중 — symbol={status.symbol} side={side}"
        )
    band = limits.near_limit_bp
    if side == "buy" and limit_price_krw * BP >= status.upper_limit_krw * (BP - band):
        return OrderCheck(
            OrderVerdict.HOLD,
            "near_upper_limit",
            f"상한가 근처 매수 — symbol={status.symbol} limit={limit_price_krw} "
            f"upper={status.upper_limit_krw}",
        )
    notes: list[str] = []
    if side == "sell" and limit_price_krw * BP <= status.lower_limit_krw * (BP + band):
        notes.append(f"하한가 근처 매도 — limit={limit_price_krw} lower={status.lower_limit_krw}")
    if side == "buy" and flags:
        notes.append(f"종목 표시가 있는 매수 — flags={flags}")
    return OrderCheck(OrderVerdict.ALLOW, notes=tuple(notes))


def _vi_active(window: ViWindow, now: time, limits: RiskLimits) -> bool:
    if now < window.start:
        return False
    if window.release is not None:
        return now < window.release
    started = datetime.combine(date.min, window.start)
    held_until = started + timedelta(minutes=limits.vi_unreleased_hold_minutes)
    return datetime.combine(date.min, now) < held_until
