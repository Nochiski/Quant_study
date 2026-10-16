"""신호 평가용 가격·달력·실제 상장구간 계약. 창에서 잘린 backtest membership은 사용하지 않는다."""

from __future__ import annotations

from strategy_workbench.application.backtest_run.facade.ports import BacktestDataQuery
from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
from strategy_workbench.application.signal_research.facade.ports import (
    LabelDataLoad,
    LabelDataQuery,
    LabelDataStatus,
    ListingEvidence,
    SignalLabelData,
)
from strategy_workbench.domain.equity.facade.research_data import CellKind
from strategy_workbench.domain.signal_evaluation.facade.evaluation import (
    AdjustedCloseObservation,
    ListingInterval,
)

from ._adapter import EquityDuckdbAdapter, _as_date, _open
from ._specs import SPAN_TABLE


class DuckdbSignalLabelAdapter:
    """기존 가격·세션 조회를 재사용하고 원장 end_reason으로만 상장 종료를 구분한다."""

    def __init__(self, equity: EquityDuckdbAdapter) -> None:
        self._equity = equity

    def load(self, query: LabelDataQuery) -> LabelDataLoad:
        equity = self._equity
        sessions = equity.trading_sessions(query.start, query.calendar_through)
        if not sessions:
            return LabelDataLoad(
                LabelDataStatus.UNAVAILABLE, detail="평가 범위에 원장의 공통 거래 달력이 없습니다."
            )
        profile = next(
            (field for field in equity.list_fields() if field.field_id == "price.adj_close"), None
        )
        if profile is None or profile.recommended_lag_sessions != 0:
            return LabelDataLoad(
                LabelDataStatus.UNSUPPORTED,
                detail=(
                    "종가 라벨에는 같은 세션의 price.adj_close 계약이 필요합니다. "
                    "원천 부재나 0이 아닌 랙을 다른 가격으로 대체하지 않습니다."
                ),
            )
        raw = equity.load_raw_observations(
            RawObservationQuery(
                "KRX", "krx.all", query.start, query.evaluation_as_of, ("price.adj_close",), 0
            )
        )
        if not raw.ok:
            return LabelDataLoad(
                LabelDataStatus.UNAVAILABLE,
                detail=(
                    f"미래 가격 원천 조회를 완료하지 못했습니다: {raw.status.value}; {raw.detail}"
                ),
            )
        security_ids = set(query.security_ids)
        requested_rows = tuple(row for row in raw.observations if row.security_id in security_ids)
        masked = [
            (row.security_id, row.as_of)
            for row in requested_rows
            for field in row.fields
            if field.kind is CellKind.MASKED
        ]
        if masked:
            return LabelDataLoad(
                LabelDataStatus.UNSUPPORTED,
                detail=(
                    "label_adjustment_gap: 요청 가격 범위에 조정 공백이 있어 "
                    "두 끝 가격만으로 비교할 수 없습니다. 가린 구간을 메우지 않았습니다 — "
                    f"count={len(masked)} first={masked[0]}"
                ),
            )
        # 원주가 bar 포트의 거래 행 판정만 재사용한다. 가격 라벨의 값은 조정종가 원천에서 읽는다.
        executable = equity.load_backtest_dataset(
            BacktestDataQuery(query.start, query.evaluation_as_of, query.security_ids, None)
        )
        if executable.data_snapshot_id != raw.data_snapshot_id:
            raise ValueError("label raw and tradable-price snapshots differ")
        trade_keys = {(bar.session, bar.security_id) for bar in executable.bars}
        prices: list[AdjustedCloseObservation] = []
        for row in requested_rows:
            if (row.as_of, row.security_id) not in trade_keys:
                continue
            for field in row.fields:
                if (
                    field.field_id == "price.adj_close"
                    and isinstance(field.value, (int, float))
                    and not isinstance(field.value, bool)
                ):
                    prices.append(
                        AdjustedCloseObservation(
                            row.as_of, row.security_id, float(field.value), field.available_date
                        )
                    )
        # 같은 노드의 기존 current_build 해석·연결을 사용해 security_span을 자르지 않고 읽는다.
        with _open(None) as connection:
            rows = connection.execute(
                "SELECT ticker || ':' || CAST(span_seq AS VARCHAR), first_date, last_date, "
                f"end_reason FROM {equity._source(SPAN_TABLE)} "
                "WHERE ticker || ':' || CAST(span_seq AS VARCHAR) IN (SELECT unnest(?)) "
                "ORDER BY ticker, span_seq",
                [list(query.security_ids)],
            ).fetchall()
        evidence = tuple(
            ListingEvidence(
                str(security_id),
                _as_date(first, "first_date"),
                _as_date(last, "last_date"),
                str(reason),
            )
            for security_id, first, last, reason in rows
        )
        listings: list[ListingInterval] = []
        warnings = [*raw.warnings, *(warning.message for warning in executable.warnings)]
        for item in evidence:
            if item.end_reason == "coverage_gap":
                if item.last_session != equity.backfill_end:
                    return LabelDataLoad(
                        LabelDataStatus.UNSUPPORTED,
                        detail=(
                            "coverage_gap이 공통 달력의 수집 상한과 일치하지 않습니다 — "
                            f"security_id={item.security_id} span_end={item.last_session} "
                            f"calendar_end={equity.backfill_end}"
                        ),
                    )
            elif item.end_reason == "data_gap":
                warnings.append(
                    "상장구간의 data_gap은 상폐로 추정하지 않습니다 — "
                    f"security_id={item.security_id}"
                )
                continue
            elif item.end_reason != "delisted":
                return LabelDataLoad(
                    LabelDataStatus.UNSUPPORTED,
                    detail=(
                        "알 수 없는 상장구간 종료 근거를 추정하지 않습니다 — "
                        f"end_reason={item.end_reason!r}"
                    ),
                )
            listings.append(
                ListingInterval(item.security_id, item.first_session, item.last_session)
            )
        return LabelDataLoad(
            LabelDataStatus.READY,
            SignalLabelData(
                raw.data_snapshot_id,
                sessions,
                tuple(prices),
                tuple(listings),
                evidence,
                tuple(warnings),
            ),
        )
