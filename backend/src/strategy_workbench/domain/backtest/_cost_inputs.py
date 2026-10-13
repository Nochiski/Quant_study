"""참여 한도·시장충격이 공유하는 판단 시점 비용 입력의 조립."""

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import TypeVar

from ._impact import ImpactUnavailableReason, impact_inputs
from ._models import DataWarning, ImpactModel, ParticipationBasis, RawFill, RunEnvironment
from ._participation import adv_observations, participation_volumes


_T = TypeVar("_T")


def _noop_checkpoint() -> None:
    pass


def _checkpointed(items: Iterable[_T], checkpoint: Callable[[], None]) -> Iterator[_T]:
    for index, item in enumerate(items):
        if index % 256 == 0:
            checkpoint()
        yield item
    checkpoint()


@dataclass(frozen=True)
class CostInputs:
    participation_volumes: dict[tuple[date, str], int] | None
    impact_scales: dict[tuple[date, str], float] | None
    impact_unavailable: dict[tuple[date, str], ImpactUnavailableReason]


def cost_inputs(
    environment: RunEnvironment,
    bars: Iterable[tuple[date, str, float, float | None]],
    settled: Mapping[tuple[date, str], float],
    *,
    checkpoint: Callable[[], None] = _noop_checkpoint,
) -> CostInputs:
    """기본 모델이면 행을 소비하지 않는다. 나머지는 정렬·ADV를 한 번만 계산한다."""
    if (
        environment.participation_basis is ParticipationBasis.SESSION_VOLUME
        and environment.impact_model is not ImpactModel.SQRT
    ):
        return CostInputs(None, None, {})
    rows = sorted(_checkpointed(bars, checkpoint), key=lambda row: row[0])
    adv = adv_observations(_checkpointed(rows, checkpoint), settled)
    impact = (
        impact_inputs(environment, _checkpointed(rows, checkpoint), settled, adv)
        if environment.impact_model is ImpactModel.SQRT
        else None
    )
    return CostInputs(
        participation_volumes(environment, adv),
        None if impact is None else impact.scales,
        {} if impact is None else impact.unavailable,
    )


def impact_input_warnings(inputs: CostInputs, fills: Iterable[RawFill]) -> tuple[DataWarning, ...]:
    """실제 체결 중 입력 부족으로 충격을 적용하지 못한 종목·세션만 알린다."""
    missing = sorted(
        {
            (item.session, item.security_id)
            for item in fills
            if (item.session, item.security_id) in inputs.impact_unavailable
        }
    )
    if not missing:
        return ()
    shown = ", ".join(
        f"{session.isoformat()}:{security}:{inputs.impact_unavailable[(session, security)].value}"
        for session, security in missing[:10]
    )
    return (
        DataWarning(
            code="cost.impact_scale_unavailable",
            message=(
                "충격 입력 이력이 부족한 실제 체결에는 시장충격을 적용하지 못했습니다. "
                f"affected_security_sessions={len(missing)} examples={shown}"
            ),
        ),
    )
