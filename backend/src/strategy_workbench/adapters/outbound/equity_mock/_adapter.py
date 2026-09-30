from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta

from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataQuery,
    BacktestDataset,
    BacktestDataUnavailableError,
    MarketBarRecord,
    UniverseMembershipRecord,
)
from strategy_workbench.application.factor_research.facade.ports import (
    FactorMetadataSnapshot,
    FactorObservationQuery,
    FactorObservationSet,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    RawFieldValue,
    RawObservation,
    RawObservationQuery,
    RawObservationSet,
)
from strategy_workbench.domain.backtest.facade.runs import DataWarning, WarningSeverity
from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DataLoadStatus,
    DatasetFieldProfile,
    DataSnapshot,
    FieldValueType,
    ResearchPanelCell,
    ResearchPanelQuery,
    ResearchPanelResult,
    UniverseHistoryQuery,
    UniverseHistoryResult,
    UniversePoint,
    field_contract_snapshot_id,
)
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
)
from strategy_workbench.domain.factor.facade.expression import (
    FieldMetadata,
    NodeValueType,
)

from ._fixture import (
    ADJUSTED_FIELD_BY_RAW,
    MOCK_SPLIT,
    Membership,
    MockEquityFixture,
    Observation,
    adjusted_close,
    build_demo_fixture,
)

_MOCK_EPOCH = date(2000, 1, 3)  # Monday
_MOCK_SECTORS = ("technology", "industrial", "consumer")
# 합성 구간에서 완전자본잠식(자본총계 < 0)이면서 적자인 종목의 순번(세 번째, sec-035420-1).
# 실데이터에 이런 기업이 있고(P2-08 리뷰 실측 28개), 자본총계를 분모로 쓰는 팩터가
# 부호 함정에 빠지는지 테스트가 mock 에서 재현할 수 있어야 한다(DEFECT-P208-001).
_CAPITAL_IMPAIRED_INDEX = 2
# (market, universe_id) -> venue the fixture memberships are keyed by.
_MOCK_UNIVERSES: dict[tuple[str, str], str] = {("KRX", "krx.common-stock"): "XKRX"}


class MockEquityDataAdapter:
    """Small but adversarial Equity v0.2 fixture with vintages, lags, and gaps."""

    def __init__(self, fixture: MockEquityFixture) -> None:
        # fixture 의 원천 판 뒤에 필드 선언표의 판을 붙인다 — 선언(단위·랙·값 타입·조정 짝)이
        # 바뀌면 같은 fixture 라도 다른 데이터 스냅샷이다(#235). 원천 판은 fixture 가 정하므로 이미
        # 합친 id 를 다시 받을 길이 없다.
        self._snapshot = replace(
            fixture.snapshot,
            snapshot_id=field_contract_snapshot_id(
                fixture.snapshot.snapshot_id,
                {
                    "profiles": {profile.field_id: profile for profile in fixture.profiles},
                    "adjusted_field_by_raw": ADJUSTED_FIELD_BY_RAW,
                },
            ),
        )
        self._sessions = fixture.sessions
        self._profiles = fixture.profiles
        self._memberships = fixture.memberships
        self._observations = fixture.observations

    @classmethod
    def demo(cls) -> MockEquityDataAdapter:
        """결정적 데모 fixture 로 만든다."""
        return cls(build_demo_fixture())

    def trading_sessions(self, start: date, end: date) -> tuple[date, ...]:
        return _business_sessions(start, end)

    def snapshot(self) -> DataSnapshot:
        return self._snapshot

    def list_fields(self) -> tuple[DatasetFieldProfile, ...]:
        return self._profiles

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        """Resolve graph field contracts inside the data adapter, never in the browser."""
        profile_by_id = {profile.field_id: profile for profile in self._profiles}
        fields: list[FieldMetadata] = []
        for field_id in field_ids:
            profile = profile_by_id.get(field_id)
            if profile is not None:
                fields.append(
                    FieldMetadata(
                        field_id=field_id,
                        unit=profile.unit,
                        value_type=(
                            NodeValueType.GROUP_SERIES
                            if profile.value_type is FieldValueType.CATEGORY
                            else NodeValueType.NUMERIC_SERIES
                        ),
                        adjusted_field_id=ADJUSTED_FIELD_BY_RAW.get(field_id),
                    )
                )
        return FactorMetadataSnapshot(
            data_snapshot_id=self._snapshot.snapshot_id,
            fields=tuple(fields),
        )

    def factor_field_catalog(self) -> tuple[FieldMetadata, ...]:
        """compile 이 읽는 필드 계약 전부(P2-07). `resolve_factor_fields` 와 같은 변환을 거친다."""
        field_ids = tuple(profile.field_id for profile in self._profiles)
        return self.resolve_factor_fields(field_ids).fields

    def load_universe(self, query: UniverseHistoryQuery) -> UniverseHistoryResult:
        sessions = tuple(
            session for session in self._sessions if query.start <= session <= query.end
        )
        points = tuple(
            UniversePoint(
                session=session,
                members=tuple(
                    membership.security
                    for membership in self._memberships
                    if membership.security.venue == query.venue
                    and membership.first_session <= session <= membership.last_session
                ),
            )
            for session in sessions
        )
        status = DataLoadStatus.OK if points else DataLoadStatus.NO_DATA
        return UniverseHistoryResult(
            points=points,
            status=status,
            snapshot_id=self._snapshot.snapshot_id,
            detail=None if points else f"no mock universe sessions — query={query}",
        )

    def load_panel(self, query: ResearchPanelQuery) -> ResearchPanelResult:
        profile_by_id = {profile.field_id: profile for profile in self._profiles}
        known_security_ids = {membership.security.security_id for membership in self._memberships}
        unknown_fields = sorted(set(query.field_ids) - set(profile_by_id))
        unknown_securities = sorted(set(query.security_ids) - known_security_ids)
        if unknown_fields or unknown_securities:
            return ResearchPanelResult(
                cells=(),
                status=DataLoadStatus.INVALID_QUERY,
                snapshot_id=self._snapshot.snapshot_id,
                detail=(
                    "unknown mock panel identifiers — "
                    f"fields={unknown_fields} securities={unknown_securities}"
                ),
            )

        lag_by_field = {
            profile.field_id: profile.recommended_lag_sessions for profile in self._profiles
        }
        lag_by_field.update({item.field_id: item.sessions for item in query.lag_overrides})
        sessions = tuple(
            session for session in self._sessions if query.start <= session <= query.end
        )
        cells: list[ResearchPanelCell] = []
        warnings: set[str] = set()
        for session in sessions:
            for field_id in query.field_ids:
                cutoff = self._cutoff(session, lag_by_field[field_id])
                if cutoff is None:
                    warnings.add(
                        "mock 거래일 달력이 랙만큼 거슬러 올라가기에 모자라다 — "
                        f"field_id={field_id} as_of={session}"
                    )
                    continue
                for security_id in query.security_ids:
                    candidate = self._latest_observation(
                        security_id=security_id,
                        field_id=field_id,
                        as_of=session,
                        available_cutoff=cutoff,
                    )
                    if candidate is None:
                        continue
                    cells.append(
                        ResearchPanelCell(
                            as_of=session,
                            security_id=security_id,
                            field_id=field_id,
                            source_effective_date=candidate.effective_date,
                            available_date=candidate.available_date,
                            value=candidate.value,
                            kind=candidate.kind,
                        )
                    )
        return ResearchPanelResult(
            cells=tuple(cells),
            status=DataLoadStatus.OK if cells else DataLoadStatus.NO_DATA,
            snapshot_id=self._snapshot.snapshot_id,
            warnings=tuple(sorted(warnings)),
            detail=None if cells else f"no mock panel cells — query={query}",
        )

    def load_factor_observations(self, query: FactorObservationQuery) -> FactorObservationSet:
        """Generate a deterministic PIT-shaped factor panel behind the replaceable port."""
        sessions = _factor_sessions(query)
        security_ids = tuple(membership.security.security_id for membership in self._memberships)
        observations = tuple(
            FactorObservation(
                as_of=session,
                security_id=security_id,
                fields=tuple(
                    FactorFieldValue(
                        field_id=field_id,
                        value=_factor_field_value(
                            field_id,
                            security_index=security_index,
                            session_index=session_index,
                            session=session,
                        ),
                    )
                    for field_id in query.required_field_ids
                ),
                forward_return=(security_index - 1) * 0.003 + ((session_index % 5) - 2) * 0.0002,
            )
            for session_index, session in enumerate(sessions)
            for security_index, security_id in enumerate(security_ids)
        )
        return FactorObservationSet(
            data_snapshot_id=self._snapshot.snapshot_id, observations=observations
        )

    def load_raw_observations(
        self,
        query: RawObservationQuery,
    ) -> RawObservationSet:
        """Original preview/backtest port; cancellation is an optional trace capability."""
        return self.load_raw_observations_cancellable(query, checkpoint=lambda: None)

    def load_raw_observations_cancellable(
        self,
        query: RawObservationQuery,
        *,
        checkpoint: Callable[[], None],
    ) -> RawObservationSet:
        """Raw PIT panel for the truthful pipeline (P1.5-03).

        Inside the fixture calendar every value comes from the same fixture `Observation` rows and
        the same PIT cut-off as `load_panel`, so Data workspace and Portfolio preview agree cell by
        cell. Outside the calendar the mock synthesises a deterministic series keyed by the
        absolute business-day index (never by the query window), lagged by the field profile's
        recommended lag; `available_date` is the session the value became visible. Membership is a
        function of (security, date) only. The synthetic series is not continuous with the fixture
        values at the calendar boundary (a mock data-quality artifact, deterministic either way).
        """
        checkpoint()
        venue = _MOCK_UNIVERSES.get((query.market, query.universe_id))
        profile_by_id = {profile.field_id: profile for profile in self._profiles}
        unknown_fields = sorted(set(query.field_ids) - set(profile_by_id))
        if venue is None or unknown_fields:
            return RawObservationSet(
                status=DataLoadStatus.INVALID_QUERY,
                data_snapshot_id=self._snapshot.snapshot_id,
                sessions=(),
                history_sessions=(),
                observations=(),
                detail=(
                    "unknown mock raw observation identifiers — "
                    f"market={query.market!r} universe_id={query.universe_id!r} "
                    f"supported={sorted(_MOCK_UNIVERSES)} unknown_fields={unknown_fields}"
                ),
                validation_checkpoint=checkpoint,
            )
        history, requested = _sessions_with_history(
            query.start, query.end, query.history_sessions_before_start
        )
        # Fixture order drives the synthetic seed and sector so values never remap when the
        # output ordering changes; the output itself is sorted by (as_of, security_id).
        memberships = [m for m in self._memberships if m.security.venue == venue]
        warnings: set[str] = set()
        observations: list[RawObservation] = []
        for session in history + requested:
            checkpoint()
            for security_index, membership in enumerate(memberships):
                if security_index % 64 == 0:
                    checkpoint()
                security_id = membership.security.security_id
                fields: list[RawFieldValue] = []
                for field_id in query.field_ids:
                    raw = self._raw_field(
                        security_id=security_id,
                        security_index=security_index,
                        field_id=field_id,
                        session=session,
                        lag_sessions=profile_by_id[field_id].recommended_lag_sessions,
                        warnings=warnings,
                    )
                    if raw is not None:
                        fields.append(raw)
                observations.append(
                    RawObservation(
                        as_of=session,
                        security_id=security_id,
                        universe_member=self._member(membership, security_index, session),
                        fields=tuple(fields),
                        sector_id=_MOCK_SECTORS[security_index % len(_MOCK_SECTORS)],
                        previous_weight=0.0,
                    )
                )
        observations.sort(key=lambda item: (item.as_of, item.security_id))
        return RawObservationSet(
            status=DataLoadStatus.OK if observations else DataLoadStatus.NO_DATA,
            data_snapshot_id=self._snapshot.snapshot_id,
            sessions=requested,
            history_sessions=history,
            observations=tuple(observations),
            detail=None if observations else f"no mock raw observations — query={query}",
            warnings=tuple(sorted(warnings)),
            validation_checkpoint=checkpoint,
        )

    def _raw_field(
        self,
        *,
        security_id: str,
        security_index: int,
        field_id: str,
        session: date,
        lag_sessions: int,
        warnings: set[str],
    ) -> RawFieldValue | None:
        if self._sessions[0] <= session <= self._sessions[-1]:
            if session not in self._sessions:
                return None  # a non-trading day inside the fixture calendar
            cutoff = self._cutoff(session, lag_sessions)
            if cutoff is None:
                warnings.add(
                    "mock 거래일 달력이 랙만큼 거슬러 올라가기에 모자라다 — "
                    f"field_id={field_id} as_of={session}"
                )
                return None
            candidate = self._latest_observation(
                security_id=security_id,
                field_id=field_id,
                as_of=session,
                available_cutoff=cutoff,
            )
            if candidate is None:
                return None
            return RawFieldValue(
                field_id=field_id,
                value=candidate.value,
                available_date=candidate.available_date,
                kind=candidate.kind,
            )
        effective_index = _business_day_index(session) - lag_sessions
        return RawFieldValue(
            field_id=field_id,
            value=_factor_field_value(
                field_id,
                security_index=security_index,
                session_index=effective_index,
                session=_session_at(effective_index),
            ),
            available_date=session,
            kind=CellKind.OBSERVED,
        )

    def _member(self, membership: Membership, security_index: int, session: date) -> bool:
        if self._sessions[0] <= session <= self._sessions[-1]:
            return membership.first_session <= session <= membership.last_session
        # Synthetic period: the third name and beyond sit out the first two sessions of every
        # month. A pure function of the date, so the same (security, date) never flips.
        if security_index < 2:
            return True
        return _business_day_index(session) - _business_day_index(session.replace(day=1)) >= 2

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        """Generate deterministic OHLCV until the real Equity DB adapter is selected.

        요청한 종목과 벤치마크만 답한다. fixture 밖 id 는 duckdb 어댑터처럼
        `BacktestDataUnavailableError` 로 거절하고, 요청하지 않은 벤치마크를 지어내지 않는다(#361).
        세션이 없는 창도 duckdb 처럼 bar 없이 답한다 — 판단은 tape 단계 몫이다.
        """
        history, sessions = _sessions_with_history(
            query.start, query.end, query.history_sessions_before_start
        )
        benchmark = () if query.benchmark_security_id is None else (query.benchmark_security_id,)
        security_ids = tuple(dict.fromkeys((*query.security_ids, *benchmark)))
        known = {membership.security.security_id for membership in self._memberships}
        unknown = sorted(set(security_ids) - known)
        if unknown:
            raise BacktestDataUnavailableError(
                f"unknown mock security_id — got={unknown} known={sorted(known)}"
            )
        bars: list[MarketBarRecord] = []
        history_bars: list[MarketBarRecord] = []
        for security_index, security_id in enumerate(security_ids):
            stable = int.from_bytes(hashlib.sha256(security_id.encode("utf-8")).digest()[:2], "big")
            base = 40_000.0 + security_index * 25_000.0 + stable % 5_000
            previous_close = base
            # 워밍업 세션은 음수 번호다. 가격 사슬은 start 에서 시작하므로 워밍업을 요청해도 측정
            # 구간 bar 는 그대로다.
            for session_index, session in enumerate((*history, *sessions), -len(history)):
                cycle = ((session_index + stable) % 17 - 8) * 0.0008
                trend = (security_index - 0.5) * 0.00015
                open_price = previous_close * (1 + cycle * 0.35)
                close_price = open_price * (1 + cycle + trend)
                volume = 1_000_000 + security_index * 250_000 + session_index * 100
                (history_bars if session_index < 0 else bars).append(
                    MarketBarRecord(
                        session=session,
                        security_id=security_id,
                        open=open_price,
                        high=max(open_price, close_price) * 1.004,
                        low=min(open_price, close_price) * 0.996,
                        close=close_price,
                        volume=volume,
                        trading_value=close_price * volume,
                    )
                )
                if session_index >= 0:
                    previous_close = close_price
        return BacktestDataset(
            data_snapshot_id=self._snapshot.snapshot_id,
            bars=tuple(bars),
            history_bars=tuple(history_bars),
            memberships=tuple(
                UniverseMembershipRecord(
                    security_id=security_id,
                    first_session=sessions[0],
                    last_session=sessions[-1],
                )
                for security_id in security_ids
                if sessions
            ),
            corporate_actions=(),
            benchmark_security_id=query.benchmark_security_id,
            warnings=(
                DataWarning(
                    code="mock_equity_data",
                    message=(
                        "결정적으로 생성한 mock OHLCV로 실행했다. 실제 연구에는 실데이터 "
                        "어댑터로 바꿔야 한다."
                    ),
                    severity=WarningSeverity.INFO,
                ),
                DataWarning(
                    code="corporate_action_feed_empty",
                    message="mock 실행은 기업 행동 피드를 비워 둔다(분할·병합 없음).",
                ),
            ),
        )

    def _cutoff(self, session: date, lag_sessions: int) -> date | None:
        try:
            session_index = self._sessions.index(session)
        except ValueError:
            return None
        cutoff_index = session_index - lag_sessions
        return self._sessions[cutoff_index] if cutoff_index >= 0 else None

    def _latest_observation(
        self,
        *,
        security_id: str,
        field_id: str,
        as_of: date,
        available_cutoff: date,
    ) -> Observation | None:
        candidates = (
            observation
            for observation in self._observations
            if observation.security_id == security_id
            and observation.field_id == field_id
            and observation.effective_date <= as_of
            and observation.available_date <= available_cutoff
        )
        return max(
            candidates,
            key=lambda observation: (observation.effective_date, observation.available_date),
            default=None,
        )


def _factor_sessions(query: FactorObservationQuery) -> tuple[date, ...]:
    requested = list(_business_sessions(query.start, query.end))
    history: list[date] = []
    cursor = query.start - timedelta(days=1)
    while len(history) < max(query.minimum_history_sessions - 1, 0):
        if cursor.weekday() < 5:
            history.append(cursor)
        cursor -= timedelta(days=1)
    return tuple((*reversed(history), *requested))


def _sessions_with_history(
    start: date, end: date, history_sessions: int
) -> tuple[tuple[date, ...], tuple[date, ...]]:
    """Business sessions in [start, end] plus `history_sessions` business days before start."""
    history: list[date] = []
    cursor = start - timedelta(days=1)
    while len(history) < history_sessions:
        if cursor.weekday() < 5:
            history.append(cursor)
        cursor -= timedelta(days=1)
    history.reverse()
    return tuple(history), _business_sessions(start, end)


def _business_day_index(session: date) -> int:
    """Weekday count from a fixed Monday epoch: the mock's absolute session clock."""
    days = (session - _MOCK_EPOCH).days
    weeks, remainder = divmod(days, 7)
    return weeks * 5 + min(remainder, 5)


def _session_at(business_day_index: int) -> date:
    """`_business_day_index`의 역함수 — 절대 세션 번호가 가리키는 평일."""
    weeks, remainder = divmod(business_day_index, 5)
    return _MOCK_EPOCH + timedelta(days=weeks * 7 + remainder)


def _business_sessions(start: date, end: date) -> tuple[date, ...]:
    sessions: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return tuple(sessions)


def _factor_field_value(
    field_id: str,
    *,
    security_index: int,
    session_index: int,
    session: date,
) -> float | str:
    if field_id.endswith("sector") or field_id.endswith("sector_code"):
        return ("technology", "industrial", "consumer")[security_index % 3]
    if field_id in ("price.close", "price.adj_close"):
        return _mock_close(
            field_id, security_index=security_index, session_index=session_index, session=session
        )
    stable = int.from_bytes(hashlib.sha256(field_id.encode("utf-8")).digest()[:2], "big")
    scale = 1 + stable % 17
    trend = (session_index + 1) * (security_index + 1) * scale
    if field_id == "price.market_cap":
        return 10_000_000_000.0 + security_index * 2_000_000_000.0 + trend * 10_000
    impaired = security_index == _CAPITAL_IMPAIRED_INDEX
    if field_id == "financial.book_equity":
        if impaired:
            # 자본잠식 규모가 순손실보다 작다 — 음수/음수 ROE 가 크게 나와 함정이 1위로 드러난다.
            return -(100_000_000.0 + trend * 10)
        return 4_000_000_000.0 + security_index * 900_000_000.0 + trend * 1_000
    if field_id == "financial.net_income":
        # TTM 은 분기 공시마다 한 번 바뀐다 — 약 63세션(한 분기) 동안 같은 값이다(#212).
        quarter = session_index // 63
        income = (
            400_000_000.0 + security_index * 90_000_000.0 + quarter * (security_index + 1) * 1e6
        )
        # 자본잠식 종목은 순손실이다(P2-08 아이디어 함정). 크기는 TTM 규칙을 그대로 따른다.
        return -income if impaired else income
    if field_id == "consensus.forward_eps":
        return 2_000.0 + security_index * 350.0 + trend * 0.2
    if field_id == "flow.foreign_net_buy":
        return (security_index - 1) * 100_000_000.0 + trend * 10_000
    if field_id == "short.short_balance_ratio":
        return 0.01 + security_index * 0.015 + (session_index % 7) * 0.0001
    if field_id == "price.shares_outstanding":
        # 신용잔고(800만 주대)보다 커야 잔고율이 0~1 사이에 선다 — 잔고율은 약 0.2% 다. 창 안에서
        # 바뀌지 않는다.
        return 5_000_000_000.0 + security_index * 500_000_000.0
    if field_id == "credit.margin_balance":
        # 주식수 축(원장 정본 단위 shares, #207)
        return 8_000_000.0 + security_index * 1_000_000.0 + trend * 10
    if field_id == "event.earnings_surprise":
        return (security_index - 1) * 0.05 + (session_index % 3) * 0.005
    return float(stable + trend)


def _mock_close(field_id: str, *, security_index: int, session_index: int, session: date) -> float:
    """합성 구간의 종가. 추세는 원주가 키 하나로 만들어 두 필드가 같은 가격 경로를 공유한다.

    분할 종목은 사건 전 원주가가 사건 뒤 수준의 `ratio`배라, 사건 뒤 원주가는 사건이 없는 종목과
    같은 식이 된다(사건 뒤 창의 기존 값이 그대로다). 수정주가는 사건 전 수준에 이어 붙는다.
    """
    stable = int.from_bytes(hashlib.sha256(b"price.close").digest()[:2], "big")
    trend = (session_index + 1) * (security_index + 1) * (1 + stable % 17)
    after_split = 40_000.0 + security_index * 20_000.0 + trend
    if security_index != MOCK_SPLIT.security_index:
        return after_split
    raw = after_split if session >= MOCK_SPLIT.effective else after_split * MOCK_SPLIT.ratio
    if field_id == "price.close":
        return raw
    return adjusted_close(raw, security_index=security_index, session=session)
