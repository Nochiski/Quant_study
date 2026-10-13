"""계열 DSR·알파 t 기준(검증 랩 V4-02, spec D8)을 논문 수치 예와 손계산으로 본다."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from strategy_workbench.domain.analytics.facade.metrics import probabilistic_sharpe
from strategy_workbench.domain.backtest.facade.runs import RunStatus
from strategy_workbench.domain.backtest.facade.trials import (
    TrialLedger,
    TrialLedgerEntry,
    summarize_trial_ledger,
)
from strategy_workbench.domain.experiment.facade.statistics import (
    alpha_t_threshold,
    deflated_sharpe,
    expected_maximum_sharpe,
)


def _spread(count: int, variance: float) -> list[float]:
    """표본분산(n − 1)이 `variance` 인 짝수 개의 대표 샤프: ±√(V(n − 1)/n) 를 번갈아 둔다."""
    half = math.sqrt(variance * (count - 1) / count)
    return [half if index % 2 else -half for index in range(count)]


def _ledger(trial_count: int, sharpes: Sequence[float]) -> TrialLedger:
    """N 이 `trial_count` 인 원장. 앞 시도들의 대표 샤프가 `sharpes` 이고 나머지는 샤프가 비었다.

    같은 키를 나중에 완료한 재확인과 결과 없이 끝난 실행은 N 에도 분산에도 들지 않는다 — 재확인
    샤프 9.9 가 분산에 들면 기대값이 크게 틀어진다.
    """
    at = datetime(2026, 9, 30, tzinfo=UTC)
    entries = [
        TrialLedgerEntry(
            f"run-{index}",
            f"key-{index}",
            RunStatus.COMPLETED,
            at,
            at,
            sharpes[index] if index < len(sharpes) else None,
        )
        for index in range(trial_count)
    ]
    if trial_count:
        entries.append(
            TrialLedgerEntry("recheck", "key-0", RunStatus.COMPLETED, at, at + timedelta(1), 9.9)
        )
    entries.append(TrialLedgerEntry("failed", "key-x", RunStatus.FAILED, at, at))
    ledger = summarize_trial_ledger("s-1", (), entries, ())
    assert ledger.trial_count == trial_count
    return ledger


def test_the_paper_example_deflates_to_0_9004() -> None:
    """Bailey·López de Prado(2014) 수치 예.

    연 샤프 2.5, 250일, 1250세션, 왜도 −3, 원 첨도 10, N = 100, 시도 샤프 분산 연 0.5.
    세션 단위로 V = 0.5/250 = 0.002, SR = 2.5/√250 = 0.15811. SR₀ = √0.002 × ((1−γ)Φ⁻¹(0.99) +
    γΦ⁻¹(1 − 1/(100e))) = 0.04472 × (0.4228 × 2.3263 + 0.5772 × 2.6797) = 0.1132(논문 0.1132).
    σ̂ = √((1 + 3 × 0.15811 + 9/4 × 0.15811²)/1249) = 0.035006, DSR = Φ((0.15811 − 0.11317)/0.035006)
    = Φ(1.2838) = 0.9004(논문 0.9004).
    """
    session_error = math.sqrt((1 + 3 * 0.15811388 + 9 / 4 * 0.15811388**2) / 1249)

    assert expected_maximum_sharpe(100, 0.5 / 250) == pytest.approx(0.1132, abs=1e-4)
    assert deflated_sharpe(
        2.5, session_error * math.sqrt(250), 250, _ledger(100, _spread(100, 0.5 / 250))
    ) == pytest.approx(0.9004, abs=1e-4)


def test_the_math_note_example_deflates_to_0_61() -> None:
    """수학 노트 예: 연 샤프 0.86, 889세션, N 238, 시도 샤프 흩어짐 연 0.25, 왜도 −0.41, 첨도 5.8,
    252일.

    s = 0.86/√252 = 0.054175, SR₀ = 0.25/√252 × 2.8219 = 0.044441, σ̂ = √((1 + 0.41s + 1.2s²)/888)
    = 0.033987, DSR = Φ(0.2860) = 0.6127.
    """
    s = 0.86 / math.sqrt(252)
    session_error = math.sqrt((1 + 0.41 * s + 4.8 / 4 * s**2) / 888)

    ledger = _ledger(238, _spread(238, (0.25 / math.sqrt(252)) ** 2))

    assert deflated_sharpe(0.86, session_error * math.sqrt(252), 252, ledger) == pytest.approx(
        0.6127, abs=1e-4
    )


def test_annual_and_session_units_are_not_mixed() -> None:
    """연 샤프를 세션 기준선과 섞으면 √A 배 부풀어 1 에 붙는다 — 같은 입력의 연환산 거래일만 바꿔도
    세션 단위 값은 같아야 한다."""
    session_sharpe, session_error = 0.05, 0.03

    values = {
        days: deflated_sharpe(
            session_sharpe * math.sqrt(days),
            session_error * math.sqrt(days),
            days,
            _ledger(50, [0.01, 0.03, 0.02]),
        )
        for days in (252, 365)
    }

    assert values[252] == pytest.approx(values[365])
    assert values[252] == pytest.approx(
        probabilistic_sharpe(session_sharpe, session_error, expected_maximum_sharpe(50, 0.0001))
    )
    assert values[252] is not None and values[252] < 0.9


def test_a_wiped_out_trial_counts_in_n_but_not_in_the_spread() -> None:
    """#383: 파산한 시도는 N 에 들고(SR₀ 가 커진다), 대표 샤프가 없어 분산에서만 빠진다."""
    at = datetime(2026, 9, 30, tzinfo=UTC)
    entries = [
        TrialLedgerEntry("run-0", "key-0", RunStatus.COMPLETED, at, at, 0.01),
        TrialLedgerEntry("run-1", "key-1", RunStatus.COMPLETED, at, at, 0.03),
        TrialLedgerEntry(
            "run-2", "key-2", RunStatus.FAILED, at, at, error_code="backtest.run.equity_wiped_out"
        ),
    ]
    ledger = summarize_trial_ledger("s-1", (), entries, ())

    # N = 3, V = 표본분산(0.01, 0.03) = 0.0002.
    assert deflated_sharpe(0.05 * math.sqrt(252), 0.03 * math.sqrt(252), 252, ledger) == (
        pytest.approx(probabilistic_sharpe(0.05, 0.03, expected_maximum_sharpe(3, 0.0002)))
    )


def test_a_single_trial_is_not_deflated() -> None:
    """N = 1 이면 고른 것이 없어 SR₀ = 0 이고 DSR 은 PSR(기준 0)이다."""
    assert expected_maximum_sharpe(1, 0.5) == 0.0
    assert deflated_sharpe(0.8, 0.4, 252, _ledger(1, [0.05])) == pytest.approx(
        probabilistic_sharpe(0.8 / math.sqrt(252), 0.4 / math.sqrt(252), 0.0)
    )


@pytest.mark.parametrize(
    ("standard_error", "trial_count", "trial_sharpes"),
    [
        # 분산을 낼 대표 샤프가 둘 미만 — 분산 0 으로 두면 DSR 이 PSR 로 부푼다.
        (0.4, 3, [0.05]),
        (0.4, 2, []),
        # 결과 난 시도가 없다.
        (0.4, 0, []),
        # 표준오차가 0 이다(곡선 두 값만 나오는 경계).
        (0.0, 5, [0.01, 0.02]),
    ],
)
def test_undecidable_inputs_give_no_deflated_sharpe(
    standard_error: float, trial_count: int, trial_sharpes: list[float]
) -> None:
    ledger = _ledger(trial_count, trial_sharpes)

    assert deflated_sharpe(0.8, standard_error, 252, ledger) is None


def test_more_trials_raise_the_luck_baseline() -> None:
    assert expected_maximum_sharpe(2, 0.001) < expected_maximum_sharpe(20, 0.001)
    assert expected_maximum_sharpe(20, 0.001) < expected_maximum_sharpe(200, 0.001)


@pytest.mark.parametrize(("trial_count", "expected"), [(1, 2.0), (19, 2.0), (20, 3.0), (238, 3.0)])
def test_alpha_t_threshold_rises_at_twenty_trials(trial_count: int, expected: float) -> None:
    assert alpha_t_threshold(trial_count) == expected
