"""보조 팩터는 합성 점수가 같은 그룹에서만 선정 순서를 바꾼다."""

from dataclasses import replace

import pytest

from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    SignalNormalization,
)
from strategy_workbench.domain.strategy.facade.validation import validate_strategy
from tests.domain.test_risk_factor import _base, _factor, _frame, _observations


def _spec(direction=FactorDirection.HIGH):
    spec = _base(
        _factor("alpha"), _factor("aux", weight=1000), normalization=SignalNormalization.NONE
    )
    return replace(
        spec,
        portfolio=replace(
            spec.portfolio, tie_breaker_factor_id="aux", tie_breaker_direction=direction
        ),
    )


@pytest.mark.parametrize(
    "direction,expected",
    [
        (FactorDirection.HIGH, ["a", "c", "b", "d", "e", "f"]),
        (FactorDirection.LOW, ["a", "b", "c", "d", "e", "f"]),
    ],
)
def test_auxiliary_only_orders_primary_ties_and_missing_last(direction, expected):
    values = {
        "alpha": dict(a=3, b=2, c=2, d=2, e=2, f=1),
        "aux": dict(a=-100, b=10, c=20, d=None, e=None, f=1000),
    }
    frame = _frame(_spec(direction), _observations(values))
    ranked = sorted(frame.candidates, key=lambda row: row.rank or 999)
    assert [row.security_id for row in ranked] == expected
    assert all(row.composite_score == values["alpha"][row.security_id] for row in ranked)
    assert all(row.eligible for row in ranked)


def test_secondary_ties_keep_security_id_order():
    frame = _frame(
        _spec(), _observations({"alpha": dict(a=1, b=1, c=1), "aux": dict(a=5, b=5, c=5)})
    )
    assert [
        row.security_id for row in sorted(frame.candidates, key=lambda row: row.rank or 999)
    ] == ["a", "b", "c"]


def test_auxiliary_reference_and_alpha_presence_are_validated():
    spec = _spec()
    missing = replace(spec, portfolio=replace(spec.portfolio, tie_breaker_factor_id="absent"))
    assert "strategy.portfolio.tie_breaker_factor_missing" in {
        issue.code for issue in validate_strategy(missing).issues
    }
    only = replace(spec, factors=(spec.factors[1],))
    assert "strategy.signal.no_alpha_factor" in {
        issue.code for issue in validate_strategy(only).issues
    }


@pytest.mark.parametrize("normalization", list(SignalNormalization))
def test_auxiliary_weight_and_factor_direction_do_not_change_primary_scores(normalization):
    spec = _spec()
    spec = replace(spec, signal=replace(spec.signal, normalization=normalization))
    baseline = replace(
        spec,
        factors=(spec.factors[0],),
        portfolio=replace(spec.portfolio, tie_breaker_factor_id=None),
    )
    observations = _observations({"alpha": dict(a=3, b=2, c=2), "aux": dict(a=-999, b=100, c=1)})
    assert [row.composite_score for row in _frame(spec, observations).candidates] == [
        row.composite_score for row in _frame(baseline, observations).candidates
    ]
    changed = replace(
        spec,
        factors=(
            spec.factors[0],
            replace(spec.factors[1], weight=-50, direction=FactorDirection.LOW),
        ),
    )
    assert _frame(spec, observations).candidates == _frame(changed, observations).candidates


def test_unpublished_auxiliary_is_missing_instead_of_lookahead():
    from datetime import timedelta

    observations = _observations({"alpha": dict(a=2, b=2), "aux": dict(a=100, b=1)})
    first = observations[0]
    observations = (
        replace(
            first,
            factor_values=(
                first.factor_values[0],
                replace(first.factor_values[1], available_date=first.as_of + timedelta(days=1)),
            ),
        ),
        observations[1],
    )
    ranked = sorted(_frame(_spec(), observations).candidates, key=lambda row: row.rank or 999)
    assert [row.security_id for row in ranked] == ["b", "a"]
    assert all(row.eligible for row in ranked)


def test_short_side_also_prefers_valid_auxiliary_and_preserves_legacy_final_order():
    from strategy_workbench.domain.strategy.facade.specification import PortfolioSide

    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            side=PortfolioSide.LONG_SHORT,
            selection_count=1,
            short_selection_count=1,
        ),
        risk=replace(spec.risk, net_exposure=0, gross_exposure=1),
    )
    values = {"alpha": dict(a=3, b=1, c=1, d=1), "aux": dict(a=0, b=5, c=5, d=None)}
    weights = {
        target.security_id: target.weight for target in _frame(spec, _observations(values)).targets
    }
    assert weights["a"] > 0
    assert weights["c"] < 0
    assert "d" not in weights


def test_disabled_default_preserves_old_spec_hash_and_enabled_settings_change_it():
    from dataclasses import asdict

    from strategy_workbench.domain.strategy.facade.specification import (
        canonical_strategy_payload,
        strategy_spec_hash,
    )

    spec = _base(_factor("alpha"), normalization=SignalNormalization.NONE)
    payload = canonical_strategy_payload(spec)
    assert "tie_breaker_factor_id" not in payload["portfolio"]
    assert "tie_breaker_direction" not in payload["portfolio"]
    old = asdict(spec)
    old["schema_version"] = old.pop("identity")["schema_version"]
    old["portfolio"].pop("tie_breaker_factor_id")
    old["portfolio"].pop("tie_breaker_direction")
    import hashlib
    import json

    assert (
        strategy_spec_hash(spec)
        == hashlib.sha256(
            json.dumps(old, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert strategy_spec_hash(_spec()) != strategy_spec_hash(_spec(FactorDirection.LOW))


def test_long_and_short_tied_groups_fill_from_distinct_candidates():
    from strategy_workbench.domain.strategy.facade.specification import PortfolioSide

    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            side=PortfolioSide.LONG_SHORT,
            selection_count=1,
            short_selection_count=1,
        ),
        risk=replace(spec.risk, net_exposure=0, gross_exposure=1),
    )
    values = {"alpha": dict(a=1, b=1, c=1), "aux": dict(a=30, b=20, c=10)}
    weights = {
        target.security_id: target.weight for target in _frame(spec, _observations(values)).targets
    }
    assert weights["a"] > 0
    assert weights["b"] < 0


def test_semantic_hash_normalizes_auxiliary_reference_with_factor_rename():
    from strategy_workbench.domain.strategy.facade.specification import strategy_semantic_hash

    spec = _spec()
    renamed = replace(
        spec,
        factors=(spec.factors[0], replace(spec.factors[1], factor_id="renamed")),
        portfolio=replace(spec.portfolio, tie_breaker_factor_id="renamed"),
    )
    assert strategy_semantic_hash(spec) == strategy_semantic_hash(renamed)


def test_short_rank_weights_follow_auxiliary_order_after_long_selection():
    from strategy_workbench.domain.strategy.facade.specification import (
        PortfolioSide,
        WeightingMethod,
    )

    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            side=PortfolioSide.LONG_SHORT,
            selection_count=2,
            short_selection_count=2,
            weighting=WeightingMethod.RANK,
        ),
        risk=replace(spec.risk, net_exposure=0, gross_exposure=1),
    )
    values = {"alpha": dict(a=1, b=1, c=1, d=1), "aux": dict(a=40, b=30, c=20, d=10)}
    weights = {
        target.security_id: target.weight for target in _frame(spec, _observations(values)).targets
    }
    assert weights == pytest.approx({"a": 1 / 3, "b": 1 / 6, "c": -1 / 3, "d": -1 / 6})


def test_short_buffer_uses_the_same_available_candidates_as_selection():
    from strategy_workbench.domain.strategy.facade.specification import PortfolioSide

    spec = _spec()
    spec = replace(
        spec,
        portfolio=replace(
            spec.portfolio,
            side=PortfolioSide.LONG_SHORT,
            selection_count=2,
            short_selection_count=2,
            turnover_buffer_count=1,
        ),
        risk=replace(spec.risk, net_exposure=0, gross_exposure=1),
    )
    observations = _observations(
        {"alpha": dict(a=1, b=1, c=1, d=1, e=1), "aux": dict(a=50, b=40, c=30, d=20, e=10)}
    )
    observations = tuple(
        replace(row, previous_weight=-0.2) if row.security_id == "e" else row
        for row in observations
    )
    weights = {target.security_id: target.weight for target in _frame(spec, observations).targets}
    assert weights["e"] < 0


def test_auxiliary_exclusion_notice_names_the_factor_without_yaml_identifiers():
    spec = _spec()
    spec = replace(spec, factors=(spec.factors[0], replace(spec.factors[1], label="비교할 모멘텀")))
    notice = next(
        issue for issue in validate_strategy(spec).issues
        if issue.code == "strategy.portfolio.tie_breaker_factor_excluded"
    )
    assert "비교할 모멘텀" in notice.message
    assert "factor_id" not in notice.message
    assert notice.path == "portfolio.tie_breaker_factor_id"
