from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DatasetFieldProfile,
    DatasetRevision,
    DataSnapshot,
    FieldCoverageCapability,
    FieldValueType,
    SecurityRef,
)


@dataclass(frozen=True)
class Membership:
    security: SecurityRef
    first_session: date
    last_session: date


@dataclass(frozen=True)
class Observation:
    security_id: str
    field_id: str
    effective_date: date
    available_date: date
    value: float | None
    kind: CellKind


@dataclass(frozen=True)
class MockEquityFixture:
    snapshot: DataSnapshot
    sessions: tuple[date, ...]
    profiles: tuple[DatasetFieldProfile, ...]
    memberships: tuple[Membership, ...]
    observations: tuple[Observation, ...]


def build_demo_fixture() -> MockEquityFixture:
    sessions = tuple(date(2024, 1, day) for day in (2, 3, 4, 5, 8, 9, 10, 11, 12))
    securities = (
        SecurityRef("sec-005930-1", "005930", "삼성전자", "XKRX"),
        SecurityRef("sec-000660-1", "000660", "SK하이닉스", "XKRX"),
        SecurityRef("sec-035420-1", "035420", "NAVER", "XKRX"),
    )
    full_coverage = FieldCoverageCapability(
        starts_on=sessions[0],
        ends_on=sessions[-1],
        venues=("XKRX",),
        estimated_coverage_pct=100.0,
        supported_cell_kinds=(CellKind.OBSERVED,),
        point_in_time=True,
    )
    profiles = (
        DatasetFieldProfile(
            field_id="price.close",
            dataset_id="price_daily",
            label="종가",
            unit="KRW",
            value_type=FieldValueType.PRICE,
            frequency="daily",
            available_date_basis="session close",
            recommended_lag_sessions=0,
            description="KRX 원주가. 조정값은 별도 factor로 적용한다.",
            disclosure_basis="정규장 종가 확정 시점",
            evidence="KRX 일별매매정보 종가 필드",
            coverage=full_coverage,
        ),
        DatasetFieldProfile(
            field_id="price.market_cap",
            dataset_id="price_daily",
            label="시가총액",
            unit="KRW",
            value_type=FieldValueType.AMOUNT,
            frequency="daily",
            available_date_basis="next session knowledge",
            recommended_lag_sessions=1,
            description="당일 값이지만 기본 연구 랙은 1 session이다.",
            disclosure_basis="종가와 상장주식수 결합",
            evidence="KRX 일별매매정보·종목기본정보",
            coverage=full_coverage,
        ),
        DatasetFieldProfile(
            field_id="financial.book_equity",
            dataset_id="fin_std",
            label="자본총계",
            unit="KRW",
            value_type=FieldValueType.AMOUNT,
            frequency="quarterly",
            available_date_basis="filing available_date",
            recommended_lag_sessions=0,
            description="공시 available_date 이후에만 보인다.",
            disclosure_basis="DART 접수일 기준 사용 가능",
            evidence="DART 재무제표 자본총계 표준계정",
            coverage=FieldCoverageCapability(
                starts_on=sessions[0],
                ends_on=sessions[-1],
                venues=("XKRX",),
                estimated_coverage_pct=82.0,
                supported_cell_kinds=(
                    CellKind.OBSERVED,
                    CellKind.MISSING,
                    CellKind.COVERAGE_GAP,
                ),
                point_in_time=True,
                requires_confirmation=True,
            ),
        ),
        DatasetFieldProfile(
            field_id="consensus.forward_eps",
            dataset_id="consensus_daily",
            label="12개월 선행 EPS",
            unit="KRW/share",
            value_type=FieldValueType.PRICE,
            frequency="daily",
            available_date_basis="first_seen_fetched_date",
            recommended_lag_sessions=0,
            description="관측점의 최초 수집 판본과 이후 revision을 보존한다.",
            disclosure_basis="수집 시스템 최초 관측일",
            evidence="컨센서스 원천 판본·수집시각 로그",
            coverage=FieldCoverageCapability(
                starts_on=sessions[0],
                ends_on=sessions[-1],
                venues=("XKRX",),
                estimated_coverage_pct=76.0,
                supported_cell_kinds=(
                    CellKind.OBSERVED,
                    CellKind.NOT_COLLECTED,
                    CellKind.COVERAGE_GAP,
                ),
                point_in_time=True,
                requires_confirmation=True,
            ),
        ),
        DatasetFieldProfile(
            field_id="flow.foreign_net_buy",
            dataset_id="flow_daily",
            label="외국인 순매수",
            unit="KRW",
            value_type=FieldValueType.AMOUNT,
            frequency="daily",
            available_date_basis="session",
            recommended_lag_sessions=0,
            description="실제 0, 원천 생략 0, 미수집을 CellKind로 구분한다.",
            disclosure_basis="거래일별 투자자 매매 집계",
            evidence="KRX 투자자별 거래실적",
            coverage=FieldCoverageCapability(
                starts_on=sessions[0],
                ends_on=sessions[-1],
                venues=("XKRX",),
                estimated_coverage_pct=91.0,
                supported_cell_kinds=tuple(CellKind),
                point_in_time=True,
                requires_confirmation=True,
            ),
        ),
    )
    memberships = (
        Membership(securities[0], sessions[0], sessions[-1]),
        Membership(securities[1], sessions[0], sessions[-1]),
        Membership(securities[2], sessions[2], sessions[-2]),
    )
    observations: list[Observation] = []
    for index, session in enumerate(sessions):
        for security_index, security in enumerate(securities):
            if security_index == 2 and session < sessions[2]:
                continue
            observations.extend(
                (
                    Observation(
                        security.security_id,
                        "price.close",
                        session,
                        session,
                        70_000.0 + security_index * 40_000.0 + index * 500.0,
                        CellKind.OBSERVED,
                    ),
                    Observation(
                        security.security_id,
                        "price.market_cap",
                        session,
                        session,
                        400_000_000_000_000.0
                        + security_index * 20_000_000_000_000.0
                        + index * 1_000_000_000.0,
                        CellKind.OBSERVED,
                    ),
                )
            )
    observations.extend(
        (
            Observation(
                securities[0].security_id,
                "financial.book_equity",
                date(2023, 12, 31),
                sessions[2],
                363_000_000_000_000.0,
                CellKind.OBSERVED,
            ),
            Observation(
                securities[0].security_id,
                "consensus.forward_eps",
                sessions[0],
                sessions[1],
                5_000.0,
                CellKind.OBSERVED,
            ),
            Observation(
                securities[0].security_id,
                "consensus.forward_eps",
                sessions[0],
                sessions[3],
                5_400.0,
                CellKind.OBSERVED,
            ),
            Observation(
                securities[0].security_id,
                "flow.foreign_net_buy",
                sessions[1],
                sessions[1],
                0.0,
                CellKind.OBSERVED,
            ),
            Observation(
                securities[1].security_id,
                "flow.foreign_net_buy",
                sessions[1],
                sessions[1],
                None,
                CellKind.MISSING,
            ),
            Observation(
                securities[0].security_id,
                "flow.foreign_net_buy",
                sessions[2],
                sessions[2],
                0.0,
                CellKind.SOURCE_OMITTED_ZERO,
            ),
            Observation(
                securities[1].security_id,
                "flow.foreign_net_buy",
                sessions[2],
                sessions[2],
                None,
                CellKind.NOT_COLLECTED,
            ),
            Observation(
                securities[2].security_id,
                "flow.foreign_net_buy",
                sessions[4],
                sessions[4],
                None,
                CellKind.COVERAGE_GAP,
            ),
        )
    )
    return MockEquityFixture(
        snapshot=DataSnapshot(
            snapshot_id="mock-equity-v0.2-20260903",
            schema_version="equity-v0.2-mock",
            built_at=datetime(2026, 9, 3, tzinfo=UTC),
            source="deterministic-memory-fixture",
            point_in_time=True,
            dataset_revisions=(
                DatasetRevision("price_daily", "mock-r3", sessions[-1]),
                DatasetRevision("fin_std", "mock-r2", sessions[-1]),
                DatasetRevision("consensus_daily", "mock-r4", sessions[-1]),
                DatasetRevision("flow_daily", "mock-r1", sessions[-1]),
            ),
        ),
        sessions=sessions,
        profiles=profiles,
        memberships=memberships,
        observations=tuple(observations),
    )
