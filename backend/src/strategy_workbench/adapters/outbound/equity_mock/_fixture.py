from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DatasetFieldProfile,
    DatasetRevision,
    DataSnapshot,
    FieldCoverageCapability,
    FieldValueType,
    SecurityRef,
    canonical_revision,
)


# 분할 사건 하나. `security_index` 종목은 `effective` 세션부터 원주가가 `1 / ratio`로 떨어지고,
# 전방 조정 수정주가(첫 관측 수준 고정)는 사건 뒤 원주가 × `ratio`로 이어진다. 실제 삼성전자
# 2018-05-04 50:1 액면분할을 본떴고, 날짜는 파이프라인이 측정할 수 있는 연구 구간(2020-01-02 이후,
# spec D1) 안으로 옮겼다. fixture 달력(2024-01)은 사건 뒤라 그 안의 수정주가는 원주가 × `ratio`다.
# 가격 변화 팩터가 원주가를 읽으면 이 사건에 오염된다(이슈 #214).
@dataclass(frozen=True)
class MockSplit:
    security_index: int
    effective: date
    ratio: float


MOCK_SPLIT = MockSplit(security_index=0, effective=date(2020, 5, 8), ratio=50.0)

# 원장 뷰가 값이 틀려 가린 신용잔고 셀 하나(무상증자 척도 창의 흉내, #249) — (종목 index, 원장 행
# 날짜). 값이 없고 셀 종류가 MASKED 라 실행 결측 정책이 채우지 않는다(#298). 원장은 신용잔고와
# 수정주가를 가리므로 mock 도 두 필드에 MASKED 를 선언한다(수정주가는 mock 에 공백 사건이 없어
# 선언만 한다).
MOCK_MASKED_CREDIT = (1, date(2024, 1, 8))


# 원주가 필드 → 시점 간 변화를 잴 때 쓸 조정 짝(필드 계약 `FieldMetadata.adjusted_field_id`,
# BACKLOG-018). mock 의 원주가 필드 중 조정 짝이 있는 것은 종가 하나다.
ADJUSTED_FIELD_BY_RAW: dict[str, str] = {"price.close": "price.adj_close"}


def adjusted_close(raw_close: float, *, security_index: int, session: date) -> float:
    """원주가 → 전방 조정 수정주가. 그 날까지 적용된 사건 계수만 곱하므로 PIT다."""
    if security_index == MOCK_SPLIT.security_index and session >= MOCK_SPLIT.effective:
        return raw_close * MOCK_SPLIT.ratio
    return raw_close


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
    value: float | str | bool | None
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
    masked_coverage = replace(
        full_coverage, supported_cell_kinds=(CellKind.OBSERVED, CellKind.MASKED)
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
            description=(
                "KRX 원주가 — 분할·증자·병합에 조정하지 않는다. 표시·거래대금·가격 필터처럼 "
                "그날의 절대 가격이 필요할 때 쓴다. 수익률·모멘텀·이평·변동성은 price.adj_close."
            ),
            disclosure_basis="정규장 종가 확정 시점",
            evidence="KRX 일별매매정보 종가 필드",
            coverage=full_coverage,
        ),
        DatasetFieldProfile(
            field_id="price.adj_close",
            dataset_id="price_adj_daily",
            label="수정 종가(전방 조정)",
            unit="KRW",
            value_type=FieldValueType.PRICE,
            frequency="daily",
            available_date_basis="session close",
            recommended_lag_sessions=0,
            description=(
                "원주가 × 그날까지 적용된 분할·증자·병합 계수의 누적곱. 첫 관측 수준을 고정하고 "
                "사건 뒤 가격을 올리므로 과거 값이 바뀌지 않는다(PIT). 실데이터에서는 원장이 그날 "
                "사건을 접지 못한 적용일이 원장이 가린 셀(MASKED)이고(#220), 원장이 조정하지 않는 "
                "사건(유상증자 권리락 등)은 조정 없이 남는다. 수익률·모멘텀·이평·변동성 계산에 "
                "쓴다. mock 분할: sec-005930-1 2020-05-08 50:1."
            ),
            disclosure_basis="원주가 세션 확정 + 사건 계수 공개",
            evidence="KRX 일별매매정보 종가 × mock 분할 사건 계수",
            coverage=masked_coverage,
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
        # 아이디어 4(거래대금 상위 20%)의 유니버스 조건 필드(P2-08). 단위·값 타입은 실데이터
        # 어댑터 선언(`equity_duckdb/_specs.py`)과 같다 — `test_idea_fixtures.py` 가 대조한다.
        DatasetFieldProfile(
            field_id="price.trading_value",
            dataset_id="price_daily",
            label="거래대금",
            unit="KRW",
            value_type=FieldValueType.AMOUNT,
            frequency="daily",
            available_date_basis="session close",
            recommended_lag_sessions=0,
            description="정규장 거래대금.",
            disclosure_basis="정규장 종가 확정 시점",
            evidence="KRX 일별매매정보 거래대금 필드",
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
            # 랙은 원장 dataset_profile 과 같아야 한다(#230). 접수일 다음 세션부터 쓴다.
            recommended_lag_sessions=1,
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
        # 아이디어 2(저PBR + 고ROE)의 ROE 분자(P2-08). 자본총계와 같은 공시 기준이다.
        DatasetFieldProfile(
            field_id="financial.net_income",
            dataset_id="fin_std",
            label="당기순이익(TTM)",
            unit="KRW",
            value_type=FieldValueType.AMOUNT,
            frequency="quarterly",
            available_date_basis="filing available_date",
            recommended_lag_sessions=1,
            # 원장(duckdb)과 같은 의미다(#212): 공시일 기준 최근 4분기 합이고, 4분기를 채울 수
            # 없으면 값을 내지 않는다(3개월·연간 값으로 대신하지 않는다).
            description=(
                "최근 4분기 합(TTM). 보고서 종류와 무관하게 늘 12개월 값이며, 창의 마지막 분기 "
                "보고서 접수일(available_date)부터 보인다. 4분기를 채울 수 없으면 값이 없다."
            ),
            disclosure_basis="DART 정기보고서 접수일 기준 사용 가능",
            evidence="DART 재무제표 당기순이익 표준계정의 분기 축 4행 합",
            coverage=FieldCoverageCapability(
                starts_on=sessions[0],
                ends_on=sessions[-1],
                venues=("XKRX",),
                estimated_coverage_pct=67.0,
                supported_cell_kinds=(CellKind.OBSERVED, CellKind.COVERAGE_GAP),
                point_in_time=True,
                requires_confirmation=True,
            ),
        ),
        DatasetFieldProfile(
            field_id="consensus.forward_eps",
            dataset_id="consensus_daily",
            # 단위·값 타입은 원장 정본(equity_duckdb FIELD_SPECS)과 같아야 한다(#207). 원장은 FY1
            # 컨센서스 평균을 내며 12개월 선행 합성은 하지 않는다. 값 타입(주당 금액)·월 빈도·
            # 랙 1은 원장 dataset_profile 과 같다(#230).
            label="선행 EPS(FY1 컨센서스 평균)",
            unit="KRW",
            value_type=FieldValueType.AMOUNT,
            frequency="monthly",
            available_date_basis="first_seen_fetched_date",
            recommended_lag_sessions=1,
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
            recommended_lag_sessions=1,
            description="실제 0, 결측, 미수집을 CellKind로 구분한다.",
            disclosure_basis="거래일별 투자자 매매 집계",
            evidence="KRX 투자자별 거래실적",
            coverage=FieldCoverageCapability(
                starts_on=sessions[0],
                ends_on=sessions[-1],
                venues=("XKRX",),
                estimated_coverage_pct=91.0,
                # 원장이 가리는 셀(MASKED)은 수급 축에 없다 — 원장 뷰가 가리는 필드만 선언한다(#298)
                # 원천 생략 0(SOURCE_OMITTED_ZERO)도 없다 — 수급의 원천 생략은 MISSING 이다(#371)
                supported_cell_kinds=tuple(
                    kind
                    for kind in CellKind
                    if kind not in (CellKind.MASKED, CellKind.SOURCE_OMITTED_ZERO)
                ),
                point_in_time=True,
                requires_confirmation=True,
            ),
        ),
    )
    profiles = (
        *profiles,
        _factor_field_profile(
            field_id="classification.sector",
            dataset_id="classification_pit",
            label="Sector classification",
            unit="category",
            value_type=FieldValueType.CATEGORY,
            coverage=full_coverage,
        ),
        _factor_field_profile(
            field_id="short.short_balance_ratio",
            dataset_id="short_daily",
            label="Short balance ratio",
            unit="ratio",
            value_type=FieldValueType.RATIO,
            coverage=full_coverage,
        ),
        _factor_field_profile(
            field_id="credit.margin_balance",
            dataset_id="credit_daily",
            # 원장 정본은 신용융자 잔고 주식수 축이다(금액축은 단위 미상이라 나가지 않는다, #207).
            label="Margin balance (shares)",
            unit="shares",
            value_type=FieldValueType.COUNT,
            coverage=masked_coverage,
            # 원장은 실입수 기준 3세션 뒤에 공개한다(EQUITY_FIELD_MAP DEFECT-E01 정정, #230).
            recommended_lag_sessions=3,
        ),
        _factor_field_profile(
            field_id="price.shares_outstanding",
            dataset_id="price_daily",
            # 신용잔고율(잔고 ÷ 상장주식수)의 분모다(#234). 원장 정본과 같은 주식수 축이다.
            label="Shares outstanding",
            unit="shares",
            value_type=FieldValueType.COUNT,
            coverage=full_coverage,
            # 랙은 원장 dataset_profile 과 같아야 한다(#230) — 상장주식수는 1세션이다.
            recommended_lag_sessions=1,
        ),
        _factor_field_profile(
            field_id="event.earnings_surprise",
            dataset_id="event_pit",
            label="Earnings surprise",
            unit="ratio",
            value_type=FieldValueType.RATIO,
            coverage=full_coverage,
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
            credit_masked = (security_index, session) == MOCK_MASKED_CREDIT
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
                        "price.adj_close",
                        session,
                        session,
                        adjusted_close(
                            70_000.0 + security_index * 40_000.0 + index * 500.0,
                            security_index=security_index,
                            session=session,
                        ),
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
                    Observation(
                        security.security_id,
                        "price.trading_value",
                        session,
                        session,
                        500_000_000_000.0 + security_index * 150_000_000_000.0 + index * 1_000_000,
                        CellKind.OBSERVED,
                    ),
                    Observation(
                        security.security_id,
                        "short.short_balance_ratio",
                        session,
                        session,
                        0.01 + security_index * 0.015 + index * 0.0001,
                        CellKind.OBSERVED,
                    ),
                    Observation(
                        security.security_id,
                        "credit.margin_balance",
                        session,
                        session,
                        None
                        if credit_masked
                        else 8_000_000.0 + security_index * 1_000_000.0 + index * 10_000,
                        CellKind.MASKED if credit_masked else CellKind.OBSERVED,
                    ),
                    Observation(
                        security.security_id,
                        "event.earnings_surprise",
                        session,
                        session,
                        (security_index - 1) * 0.05 + (index % 3) * 0.005,
                        CellKind.OBSERVED,
                    ),
                    Observation(
                        security.security_id,
                        "classification.sector",
                        session,
                        session,
                        ("technology", "industrial", "consumer")[security_index % 3],
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
            # TTM 순이익: 000660 은 3분기 보고서가 늦게(sessions[3]) 접수돼 그 전엔 반기 말
            # TTM 이다. 035420 은 4분기를 채울 수 없어 관측이 없다.
            Observation(
                securities[0].security_id,
                "financial.net_income",
                date(2023, 9, 30),
                date(2023, 11, 14),
                15_000_000_000_000.0,
                CellKind.OBSERVED,
            ),
            Observation(
                securities[1].security_id,
                "financial.net_income",
                date(2023, 6, 30),
                date(2023, 8, 14),
                -8_000_000_000_000.0,
                CellKind.OBSERVED,
            ),
            Observation(
                securities[1].security_id,
                "financial.net_income",
                date(2023, 9, 30),
                sessions[3],
                -6_000_000_000_000.0,
                CellKind.OBSERVED,
            ),
            Observation(
                securities[0].security_id,
                "financial.net_income",
                date(2023, 12, 31),
                sessions[2],
                15_000_000_000_000.0,
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
            # flow 는 원장처럼 랙 1이라(#230) 관측 세션 다음 세션에 보인다. 셀 종류를 보는 테스트의
            # as_of(01-03·01-04·01-08)가 그대로이도록 관측을 한 세션 앞에 둔다.
            Observation(
                securities[0].security_id,
                "flow.foreign_net_buy",
                sessions[0],
                sessions[0],
                0.0,
                CellKind.OBSERVED,
            ),
            Observation(
                securities[1].security_id,
                "flow.foreign_net_buy",
                sessions[0],
                sessions[0],
                None,
                CellKind.MISSING,
            ),
            # 원천이 행을 뺀 칸 — 수급은 0 으로 단정하지 않고 MISSING 이다(실원장과 같다, #371)
            Observation(
                securities[0].security_id,
                "flow.foreign_net_buy",
                sessions[1],
                sessions[1],
                None,
                CellKind.MISSING,
            ),
            Observation(
                securities[1].security_id,
                "flow.foreign_net_buy",
                sessions[1],
                sessions[1],
                None,
                CellKind.NOT_COLLECTED,
            ),
            Observation(
                securities[2].security_id,
                "flow.foreign_net_buy",
                sessions[3],
                sessions[3],
                None,
                CellKind.COVERAGE_GAP,
            ),
        )
    )
    observed = tuple(observations)
    return MockEquityFixture(
        snapshot=DataSnapshot(
            # 원천 판은 fixture 데이터(세션·구성·관측·분할 사건)에서 계산한다. 데이터가 바뀌면 손
            # 상수를 올리지 않아도 id 가 바뀐다(#291 리뷰 P3-6). 가격을 만드는 식은 어댑터 코드라
            # 판 밖이다.
            snapshot_id="mock-equity-v0.2-"
            + canonical_revision((sessions, memberships, observed, MOCK_SPLIT)),
            schema_version="equity-v0.2-mock",
            built_at=datetime(2026, 9, 3, tzinfo=UTC),
            source="deterministic-memory-fixture",
            point_in_time=True,
            dataset_revisions=(
                DatasetRevision("price_daily", "mock-r3", sessions[-1]),
                DatasetRevision("price_adj_daily", "mock-r1", sessions[-1]),
                DatasetRevision("fin_std", "mock-r2", sessions[-1]),
                DatasetRevision("consensus_daily", "mock-r4", sessions[-1]),
                DatasetRevision("flow_daily", "mock-r1", sessions[-1]),
                DatasetRevision("short_daily", "mock-r1", sessions[-1]),
                DatasetRevision("credit_daily", "mock-r1", sessions[-1]),
                DatasetRevision("event_pit", "mock-r1", sessions[-1]),
                DatasetRevision("classification_pit", "mock-r1", sessions[-1]),
            ),
        ),
        sessions=sessions,
        profiles=profiles,
        memberships=memberships,
        observations=observed,
    )


def _factor_field_profile(
    *,
    field_id: str,
    dataset_id: str,
    label: str,
    unit: str,
    value_type: FieldValueType,
    coverage: FieldCoverageCapability,
    recommended_lag_sessions: int = 0,
) -> DatasetFieldProfile:
    return DatasetFieldProfile(
        field_id=field_id,
        dataset_id=dataset_id,
        label=label,
        unit=unit,
        value_type=value_type,
        frequency="daily",
        available_date_basis="point-in-time session fixture",
        recommended_lag_sessions=recommended_lag_sessions,
        description="Deterministic mock subset for the replaceable Equity DB adapter.",
        disclosure_basis="Mock PIT availability contract",
        evidence="M3 factor research fixture",
        coverage=coverage,
    )
