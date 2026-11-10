"""자동 기록 조립·검사의 owner — 후보 응답의 기록, 판정 기록, 저장 전 출처·버전 검사."""

from __future__ import annotations

from collections.abc import Mapping

from strategy_workbench.application.stock_chart._errors import ChartRequestError
from strategy_workbench.application.stock_chart._provenance import AutoParams
from strategy_workbench.application.stock_chart._views import WindowView
from strategy_workbench.application.stock_chart.ports.outgoing.chart_drawings import (
    AutoRecord,
    ParamValue,
    RegimeRecord,
)
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    ALGORITHM_VERSION,
    ChosenBy,
    LineSource,
)
from strategy_workbench.domain.chart_analysis.facade.regime import RegimeJudgement
from strategy_workbench.domain.chart_analysis.facade.series import SwingParams


def present_params(params: Mapping[str, ParamValue]) -> dict[str, ParamValue]:
    """값이 있는 칸만 — 빈 칸(None)을 싣든 빼든 같은 기록이다."""
    return {key: value for key, value in params.items() if value is not None}


def regime_record(judgement: RegimeJudgement) -> RegimeRecord:
    return RegimeRecord(judgement.verdict, judgement.strength, judgement.structure)


def auto_params(swing: SwingParams) -> AutoParams:
    return AutoParams(swing.candidate_sensitivity, swing.structure_sensitivity)


def auto_record(
    view: WindowView,
    params: AutoParams,
    *,
    regime: RegimeRecord | None = None,
    chosen: ChosenBy | None = None,
) -> AutoRecord:
    """자동 후보의 기록 — 판정 구간과 파라미터(저장 요청에 그대로 싣는다)."""
    return AutoRecord(
        algorithm_version=ALGORITHM_VERSION,
        params=params.to_params(),
        regime_window=(view.start, view.end),
        regime=regime,
        chosen_by=chosen,
        window_source=view.source,
    )


def check_record(source: LineSource, auto: AutoRecord | None) -> None:
    """자동 기록은 자동 선(`auto`·`auto_copied`)에만 있고 이 판의 알고리즘 기록이어야 한다. 기록의
    내용(파라미터·구간, 자동 선은 기준점까지)은 `_verified_record`가 본다."""
    if (source is LineSource.MANUAL) != (auto is None):
        raise ChartRequestError(
            "chart.line.auto_record",
            f"auto record must be present exactly for auto lines — source={source.value} "
            f"auto={'present' if auto is not None else 'missing'}",
        )
    if auto is not None and auto.algorithm_version != ALGORITHM_VERSION:
        raise ChartRequestError(
            "chart.line.auto_record",
            f"auto record from another algorithm version — got={auto.algorithm_version!r} "
            f"current={ALGORITHM_VERSION!r}",
        )
