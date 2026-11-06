"""사전등록 갈래 4개와 민감도 2개(`~/quant-data/research/2026-10-30-scope-sigma-exit/PREREG.md`). 결과를 보기 전에 고정한다.

엔진은 10-26 매매 규칙 시험(`workspace/dongmin/trading-rules`, 로컬 브랜치 KaelPlayground/trading-rule-research)에서
가져왔다. 그 시험의 변형 축(국면·비중·유동성)은 이번 시험에 없지만 엔진이 읽으므로 필드는 남긴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Variant:
    name: str
    axis: str
    rebalance: str = "weekly"  # daily · weekly · biweekly · monthly · quarterly
    execution: str = "next_open"  # next_open · next_close · delay1
    slots: int = 20
    keep_rank: int = 20
    band: float = math.inf  # 남는 종목 비중을 되돌리지 않는다(사용자 10-30, D4)
    min_adv_krw: float | None = None
    weighting: str = "equal"
    cash: float = 0.02  # 신규 매수 = NAV × 0.98 ÷ 20 = 4.9%(사용자 10-30)
    stop: float | None = None  # 10-26 시험의 매입가 대비 손절(이번엔 안 씀)
    regime: str | None = None
    fee_bps: float = 15.0
    slippage_bps: float = 10.0
    universe_ew: bool = False
    # 이번 시험의 규칙. None 이면 그 규칙을 쓰지 않는다
    stop_sigma: float | None = None  # z ≤ −값 → 전량 매도
    take_sigma: float | None = None  # z ≥ +값 → 절반 매도, 기준점 다시 잡기
    trim_weight: float | None = None  # 포지션 ÷ NAV < 값 → 전량 매도
    sigma_window: int = 60
    sigma_min_obs: int = 20
    sell_tax: bool = True  # 진단(비용 0)만 끈다
    # 2차(PREREG_v2)
    stop_min_days: int = 1  # 기준점 이후 장 마감 수 t 가 이 값 이상일 때만 손절을 본다
    stop_cooldown_days: int = 0  # 손절 신호일 D 에서 달력 이 일수 안의 신호일엔 그 종목을 사지 않는다
    # 3차(PREREG_v3). None 이면 신호의 운영 합성 점수·순위를 그대로 쓴다
    factor_weights: tuple[tuple[str, float], ...] | None = None  # 신호 A(리비전 없음)
    factor_weights_b: tuple[tuple[str, float], ...] | None = None  # 신호 B(리비전 있음)
    required_factors: tuple[str, ...] = ()

    @property
    def uses_rules(self) -> bool:
        return self.stop_sigma is not None or self.take_sigma is not None or self.trim_weight is not None


BASELINE = Variant(name="baseline", axis="baseline")


def _v(name: str, axis: str, **changes) -> Variant:
    return replace(BASELINE, name=name, axis=axis, **changes)


ARMS: tuple[Variant, ...] = (
    _v("stop_only", "arm", stop_sigma=2.0),
    _v("take_only", "arm", take_sigma=3.0, trim_weight=0.03),
    _v("full", "arm", stop_sigma=2.0, take_sigma=3.0, trim_weight=0.03),
)

# 판정에 쓰지 않는 민감도
SENSITIVITY: tuple[Variant, ...] = (
    _v("full_s1.5_t2.5", "sensitivity", stop_sigma=1.5, take_sigma=2.5, trim_weight=0.03),
    _v("full_s2.5_t3.5", "sensitivity", stop_sigma=2.5, take_sigma=3.5, trim_weight=0.03),
)


# 사전등록 밖 진단(결과를 본 뒤 추가, 판정에 안 씀): 수수료·슬리피지·거래세 0 — 손실이 비용에서 오는지 신호에서 오는지 가른다
DIAGNOSTICS: tuple[Variant, ...] = (
    _v("diag_baseline_nocost", "diagnostic", fee_bps=0.0, slippage_bps=0.0, sell_tax=False),
    _v("diag_full_nocost", "diagnostic", stop_sigma=2.0, take_sigma=3.0, trim_weight=0.03, fee_bps=0.0, slippage_bps=0.0, sell_tax=False),
)


# 2차 사전등록(PREREG_v2.md): 100위 보유 한도 + 고친 규칙. 결과를 보기 전에 고정한다
V2_BASELINE = _v("v2_baseline", "v2_baseline", keep_rank=100)
_V2_RULES = dict(stop_sigma=2.0, take_sigma=3.0, trim_weight=0.015, stop_min_days=5, stop_cooldown_days=28)
V2_ARMS: tuple[Variant, ...] = (_v("v2_full", "v2_arm", keep_rank=100, **_V2_RULES),)
_NO_COST = dict(fee_bps=0.0, slippage_bps=0.0, sell_tax=False)
V2_DIAGNOSTICS: tuple[Variant, ...] = (
    _v("v2_diag_baseline_nocost", "v2_diagnostic", keep_rank=100, **_NO_COST),
    _v("v2_diag_full_nocost", "v2_diagnostic", keep_rank=100, **_V2_RULES, **_NO_COST),
)

# 2차 결과를 본 뒤 추가한 진단(판정 안 함): 규칙 손해가 손절·익절 중 어디서 왔는지 가른다
V2_SPLIT_DIAGNOSTICS: tuple[Variant, ...] = (
    _v("v2_diag_stop_only", "v2_diagnostic", keep_rank=100, stop_sigma=2.0, stop_min_days=5, stop_cooldown_days=28),
    _v("v2_diag_take_only", "v2_diagnostic", keep_rank=100, take_sigma=3.0, trim_weight=0.015),
)

# 3차 사전등록(PREREG_v3.md): 모멘텀 가중 줄이기. 매매는 2차 규칙 없음과 같다
_FOUR = ("momentum", "flow", "quality", "valuation")
V3_ARMS: tuple[Variant, ...] = (
    _v(
        "v3_m1", "v3_arm", keep_rank=100, required_factors=_FOUR,
        factor_weights=(("momentum", 0.30), ("flow", 0.20), ("quality", 0.25), ("valuation", 0.25)),
        factor_weights_b=(("momentum", 0.30), ("revision", 0.30), ("flow", 0.20), ("quality", 0.10), ("valuation", 0.10)),
    ),
    _v(
        "v3_m2", "v3_arm", keep_rank=100, required_factors=_FOUR,
        factor_weights=(("momentum", 0.125), ("flow", 0.1875), ("quality", 0.375), ("valuation", 0.3125)),
        factor_weights_b=(("momentum", 0.10), ("revision", 0.20), ("flow", 0.15), ("quality", 0.30), ("valuation", 0.25)),
    ),
)


def by_name() -> dict[str, Variant]:
    return {
        v.name: v
        for v in (
            BASELINE, *ARMS, *SENSITIVITY, *DIAGNOSTICS,
            V2_BASELINE, *V2_ARMS, *V2_DIAGNOSTICS, *V2_SPLIT_DIAGNOSTICS, *V3_ARMS,
        )
    }
