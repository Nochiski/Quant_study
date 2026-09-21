"""P2-05 횡단면 eligibility(`top_percent`·`top_count`)의 2-pass 프레임 컴파일 계약 (spec D3 S5).

절대 규칙(`gt`~`eq`)은 후보 하나만 보고 판정할 수 있지만 `top_*` 는 같은 기준일 프레임의 모집단이
있어야 판정된다. 이 파일이 고정하는 사실은 넷이다.

1. 모집단 — 유니버스 멤버 중 절대 규칙을 전부 통과했고 그 규칙의 `field_id` 값이 있는 종목.
   결측·공개일 초과는 탈락이면서 **분모에서도 빠진다**.
2. cut 크기 — `top_percent` 는 비율 × 모집단 크기, `top_count` 는 개수. 둘 다 소수점 절사이며
   비율은 사용자가 쓴 10진 표기 그대로 곱한다(이진 부동소수 곱은 0.29 × 100 을 28 로 깎는다).
3. 동점 — 값 내림차순, 같으면 `security_id` 오름차순으로 정렬한 뒤 자른다(전순서라 결정적).
4. 탈락 사유 — 순위에서 잘린 종목은 `ELIGIBILITY_RANK_CUT` 이다. 규칙 위반(`ELIGIBILITY_FAILED`)
   과 같은 값을 쓰면 trace 화면이 "규칙을 어겼다"로 잘못 읽는다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.portfolio.facade.construction import (
    CandidateDecision,
    ExclusionReason,
    PortfolioFactorValue,
    PortfolioFieldValue,
    PortfolioObservation,
    PortfolioTraceSelection,
    compile_target_tape,
    compile_target_tape_with_trace,
)
from strategy_workbench.domain.strategy.facade.specification import (
    EligibilityOperator,
    EligibilityRule,
    EligibilityStep,
    RebalanceFrequency,
    StrategySpec,
)

_DAY = date(2026, 1, 2)
_LIQUIDITY = "price.turnover"


def _environment() -> RunEnvironment:
    return RunEnvironment(
        start=date(2026, 1, 1), end=date(2026, 12, 31), universe_id="krx.common-stock"
    )


def _spec(*rules: EligibilityRule) -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    return replace(
        template,
        eligibility=EligibilityStep(rules),
        portfolio=replace(
            template.portfolio,
            selection_count=1,
            # 세션 두 개짜리 프레임 하나만 컴파일한다(월간 기본값은 마지막 세션만 골라 프레임이 0).
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
        risk=replace(template.risk, max_name_weight=1.0, max_sector_weight=1.0),
    )


def _observation(
    security_id: str,
    *,
    liquidity: float | None = None,
    extra_fields: tuple[PortfolioFieldValue, ...] = (),
    universe_member: bool = True,
    liquidity_available_date: date | None = None,
) -> PortfolioObservation:
    fields = extra_fields
    if liquidity is not None:
        fields = (
            PortfolioFieldValue(_LIQUIDITY, liquidity, liquidity_available_date or _DAY),
            *fields,
        )
    return PortfolioObservation(
        as_of=_DAY,
        security_id=security_id,
        universe_member=universe_member,
        factor_values=(
            PortfolioFactorValue(factor_id="price.close", value=1.0, available_date=_DAY),
        ),
        fields=fields,
        sector_id="sector-a",
        previous_weight=0.0,
    )


def _candidates(
    spec: StrategySpec, observations: tuple[PortfolioObservation, ...]
) -> dict[str, CandidateDecision]:
    tape = compile_target_tape(
        spec,
        environment=_environment(),
        data_snapshot_id="snapshot-1",
        sessions=(_DAY, _DAY + timedelta(days=1)),
        observations=observations,
    )
    return {item.security_id: item for item in tape.frames[0].candidates}


def _ladder(count: int, *, start: int = 0) -> tuple[PortfolioObservation, ...]:
    """거래대금이 서로 다른 종목 `count` 개. `s000` 이 가장 크고 뒤로 갈수록 작아진다."""
    return tuple(
        _observation(f"s{index:03d}", liquidity=float(count - index))
        for index in range(start, start + count)
    )


def _cut_ids(candidates: dict[str, CandidateDecision]) -> set[str]:
    return {
        security_id
        for security_id, decision in candidates.items()
        if ExclusionReason.ELIGIBILITY_RANK_CUT in decision.exclusion_reasons
    }


def _kept_ids(candidates: dict[str, CandidateDecision]) -> set[str]:
    """필터를 전부 통과한 종목. `OUTSIDE_SELECTION` 은 선정 결과라 `eligible` 을 바꾸지 않는다."""
    return {security_id for security_id, decision in candidates.items() if decision.eligible}


def test_top_percent_keeps_exactly_the_fraction_of_the_population() -> None:
    """아이디어 4(거래대금 상위 20%): 후보 100개에서 정확히 20개가 남는다."""
    observations = _ladder(100)
    spec = _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_PERCENT, 0.2))

    candidates = _candidates(spec, observations)

    assert len(_kept_ids(candidates)) == 20
    assert len(_cut_ids(candidates)) == 80
    assert _kept_ids(candidates) == {f"s{index:03d}" for index in range(20)}


def test_top_percent_truncates_the_decimal_the_document_wrote() -> None:
    """비율 cut 은 사용자가 쓴 10진 표기로 계산한다.

    `math.floor(100 * 0.29)` 는 이진 부동소수에서 28 이다. 그대로 쓰면 "상위 29%" 문서가
    진단도 예외도 없이 28 종목만 남긴다 — 이 PR 이 `_compare` 의 catch-all 에서 막으려던 것과
    같은 조용한 오필터다.
    """
    observations = _ladder(100)

    assert (
        len(
            _kept_ids(
                _candidates(
                    _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_PERCENT, 0.29)),
                    observations,
                )
            )
        )
        == 29
    )
    # 절사 경계: 7 × 0.5 = 3.5 → 3 개.
    assert (
        len(
            _kept_ids(
                _candidates(
                    _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_PERCENT, 0.5)),
                    _ladder(7),
                )
            )
        )
        == 3
    )


def test_top_count_keeps_the_requested_number() -> None:
    observations = _ladder(10)
    spec = _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_COUNT, 3))

    candidates = _candidates(spec, observations)

    assert _kept_ids(candidates) == {"s000", "s001", "s002"}


def test_a_cut_larger_than_the_population_keeps_everyone() -> None:
    observations = _ladder(4)
    spec = _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_COUNT, 10))

    candidates = _candidates(spec, observations)

    assert _cut_ids(candidates) == set()


def test_ties_are_cut_by_security_id_ascending() -> None:
    """값이 모두 같으면 `security_id` 오름차순이 전순서를 만든다 — 같은 입력이 같은 컷을 낸다.

    관측을 **뒤집어** 한 번 더 컴파일하는 것이 이 테스트의 핵심이다. 오름차순 입력만 쓰면
    파이썬의 안정 정렬이 타이브레이커를 대신해 주므로, 정렬 2차 키(`item[0]`)를 지워도
    통과한다(리뷰 DEFECT-P2-1 실측). 도메인 facade `compile_target_tape` 는 관측 순서를
    정렬하지도 검사하지도 않으므로(프레임 그룹화가 호출자 순서를 그대로 보존한다) 2차 키가
    없으면 같은 문서·같은 스냅샷이 관측 순서에 따라 다른 종목을 남긴다.
    """
    observations = tuple(_observation(f"s{index:03d}", liquidity=1.0) for index in range(5))
    spec = _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_COUNT, 2))

    candidates = _candidates(spec, observations)

    assert _kept_ids(candidates) == {"s000", "s001"}
    assert _cut_ids(candidates) == {"s002", "s003", "s004"}
    assert _kept_ids(_candidates(spec, tuple(reversed(observations)))) == _kept_ids(candidates)


def test_missing_values_drop_out_of_the_population_and_the_denominator() -> None:
    """결측 2개는 탈락이고 분모에서도 빠진다 — 8 × 0.5 = 4 개가 남는다."""
    observations = (*_ladder(8), _observation("s900"), _observation("s901"))
    spec = _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_PERCENT, 0.5))

    candidates = _candidates(spec, observations)

    assert candidates["s900"].exclusion_reasons == (ExclusionReason.MISSING_ELIGIBILITY,)
    assert candidates["s901"].exclusion_reasons == (ExclusionReason.MISSING_ELIGIBILITY,)
    assert len(_kept_ids(candidates)) == 4
    assert _kept_ids(candidates) == {f"s{index:03d}" for index in range(4)}


def test_a_value_published_after_the_signal_date_leaves_the_population() -> None:
    """공개일이 기준일보다 늦은 값이 모집단에 들어가면 look-ahead 로 남의 순위를 바꾼다."""
    observations = (
        *_ladder(4),
        _observation("s900", liquidity=999.0, liquidity_available_date=_DAY + timedelta(days=1)),
    )
    spec = _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_PERCENT, 0.5))

    candidates = _candidates(spec, observations)

    assert candidates["s900"].exclusion_reasons == (ExclusionReason.FUTURE_DATA,)
    # 모집단은 4개(999.0 은 빠졌다) → 2개가 남고, 상위 2개는 여전히 s000·s001 이다.
    assert _kept_ids(candidates) == {"s000", "s001"}


def test_universe_non_members_never_enter_the_population() -> None:
    observations = (
        *_ladder(4),
        _observation("s900", liquidity=999.0, universe_member=False),
    )
    spec = _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_PERCENT, 0.5))

    candidates = _candidates(spec, observations)

    assert candidates["s900"].exclusion_reasons == (ExclusionReason.NOT_IN_UNIVERSE,)
    assert _kept_ids(candidates) == {"s000", "s001"}


def test_absolute_and_cross_sectional_rules_are_anded_on_the_shrunken_population() -> None:
    """절대 규칙이 먼저 후보를 거르고, 남은 후보 안에서만 순위를 센다.

    거래대금 10~1 인 10 종목 중 시가총액 절대 규칙이 5 개를 떨어뜨리면 `top_percent: 0.4` 의
    분모는 10 이 아니라 5 다(→ 2 개). 분모를 10 으로 세면 4 개가 남아 "상위 40%"가 실제로는
    상위 80% 가 된다.
    """
    market_cap = "price.market_cap"
    observations = tuple(
        _observation(
            f"s{index:03d}",
            liquidity=float(10 - index),
            extra_fields=(PortfolioFieldValue(market_cap, 200.0 if index < 5 else 50.0, _DAY),),
        )
        for index in range(10)
    )
    spec = _spec(
        EligibilityRule(market_cap, EligibilityOperator.GREATER_THAN, 100.0),
        EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_PERCENT, 0.4),
    )

    candidates = _candidates(spec, observations)

    assert _kept_ids(candidates) == {"s000", "s001"}
    assert _cut_ids(candidates) == {"s002", "s003", "s004"}
    for index in range(5, 10):
        assert candidates[f"s{index:03d}"].exclusion_reasons == (
            ExclusionReason.ELIGIBILITY_FAILED,
        )


def test_two_cross_sectional_rules_are_anded() -> None:
    other = "price.market_cap"
    observations = tuple(
        _observation(
            f"s{index:03d}",
            liquidity=float(10 - index),
            extra_fields=(PortfolioFieldValue(other, float(index), _DAY),),
        )
        for index in range(10)
    )
    spec = _spec(
        EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_COUNT, 6),
        EligibilityRule(other, EligibilityOperator.TOP_COUNT, 6),
    )

    candidates = _candidates(spec, observations)

    # 거래대금 상위 6 = s000~s005, 시총 상위 6 = s009~s004. 교집합은 s004·s005 다.
    assert _kept_ids(candidates) == {"s004", "s005"}


def test_rank_cut_candidates_are_neither_ranked_nor_selected() -> None:
    """순위 탈락은 `eligible=False` 다 — 선정 순위에 남으면 잘린 종목이 다시 포트폴리오에 든다."""
    base = _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_COUNT, 2))
    spec = replace(base, portfolio=replace(base.portfolio, selection_count=5))

    candidates = _candidates(spec, _ladder(10))

    assert _cut_ids(candidates) == {f"s{index:03d}" for index in range(2, 10)}
    for security_id in _cut_ids(candidates):
        assert not candidates[security_id].eligible
        assert candidates[security_id].rank is None
        assert not candidates[security_id].selected
    assert {item.security_id for item in candidates.values() if item.selected} == {"s000", "s001"}


def test_the_rank_cut_reason_reaches_the_construction_trace() -> None:
    observations = _ladder(4)
    spec = _spec(EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_COUNT, 1))

    result = compile_target_tape_with_trace(
        spec,
        environment=_environment(),
        data_snapshot_id="snapshot-1",
        sessions=(_DAY, _DAY + timedelta(days=1)),
        observations=observations,
        trace_selection=PortfolioTraceSelection(
            as_of=_DAY, security_ids=("s000", "s003"), include_order_delta=False
        ),
    )

    assert result.trace is not None
    traced = {item.security_id: item for item in result.trace.candidates}
    assert traced["s000"].exclusion_reasons == ()
    assert traced["s003"].exclusion_reasons == (ExclusionReason.ELIGIBILITY_RANK_CUT,)


def test_compare_refuses_an_operator_it_cannot_decide_alone() -> None:
    """`_compare` 의 catch-all 제거 회귀.

    예전 구현은 마지막 줄이 `return value == threshold` 여서, 모집단이 필요한 연산자가
    들어오면 예외 없이 "값이 같은가"로 답했다. 진단도 로그도 없이 다른 종목이 선정된다.
    """
    from strategy_workbench.domain.portfolio import _compiler as compiler_module

    assert compiler_module._compare(2.0, EligibilityOperator.GREATER_THAN, 1.0)
    with pytest.raises(ValueError, match="top_percent"):
        compiler_module._compare(0.2, EligibilityOperator.TOP_PERCENT, 0.2)


def test_eligibility_operator_is_not_the_shared_comparison_enum() -> None:
    """공유 enum 에 얹으면 `top_*` 가 다른 소비자의 exhaustive 분기에 조용히 흘러든다."""
    import strategy_workbench.domain.strategy.facade.specification as specification

    assert not hasattr(specification, "ComparisonOperator")
    assert [member.value for member in EligibilityOperator] == [
        "gt",
        "gte",
        "lt",
        "lte",
        "eq",
        "top_percent",
        "top_count",
    ]


def test_a_non_finite_cut_size_names_the_rule_instead_of_crashing_bare() -> None:
    """validator 를 건너뛴 경로가 생겨도 `Decimal("NaN")` 의 맨몸 ValueError 로 끝나지 않는다."""
    from strategy_workbench.domain.portfolio import _compiler as compiler_module

    rule = EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_PERCENT, float("nan"))
    with pytest.raises(ValueError, match="non-finite size"):
        compiler_module._cross_sectional_cut(rule, 10)


def test_both_passes_read_the_same_field_when_a_field_id_repeats() -> None:
    """중복 `field_id` 에서 절대 규칙과 횡단면 모집단이 같은 값을 읽는다 (리뷰 DEFECT-P3-2).

    포트 계약이 중복을 거절하므로 실 파이프라인에서는 나지 않지만, `compile_target_tape` 는
    공개 도메인 facade 라 임의 관측으로도 불린다. 두 패스가 서로 다른 조회 방식을 쓰면 같은
    규칙이 종목마다 다른 값을 보고도 조용히 통과한다.
    """
    # 마지막 항목(5.0)이 이긴다 — 절대 규칙 `> 3` 을 통과하고, 순위도 그 값으로 매겨진다.
    duplicated = PortfolioObservation(
        as_of=_DAY,
        security_id="s000",
        universe_member=True,
        factor_values=(PortfolioFactorValue("price.close", 1.0, _DAY),),
        fields=(
            PortfolioFieldValue(_LIQUIDITY, 1.0, _DAY),
            PortfolioFieldValue(_LIQUIDITY, 5.0, _DAY),
        ),
        sector_id="sector-a",
        previous_weight=0.0,
    )
    observations = (duplicated, _observation("s001", liquidity=3.0))
    spec = _spec(
        EligibilityRule(_LIQUIDITY, EligibilityOperator.GREATER_THAN, 2.0),
        EligibilityRule(_LIQUIDITY, EligibilityOperator.TOP_COUNT, 1),
    )

    candidates = _candidates(spec, observations)

    assert _kept_ids(candidates) == {"s000"}
    assert _cut_ids(candidates) == {"s001"}
