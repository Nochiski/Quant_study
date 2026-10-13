"""학습·검증 제출 기록의 불변식은 DB에 닿기 전에 같은 owner에서 검사한다."""

from datetime import UTC, datetime

import pytest

from strategy_workbench.application.experiment_run.facade.ports import TrialAttempt, WindowPick
from strategy_workbench.domain.backtest.facade.runs import AdmissionRejectionCode


@pytest.mark.parametrize(
    ("run_id", "code", "error", "valid"),
    [
        ("run", None, None, True),
        (None, "backtest.run.invalid", "rejected", True),
        (None, None, None, False),
        ("run", "backtest.run.invalid", "rejected", False),
        (None, "backtest.run.invalid", None, False),
        (None, None, "rejected", False),
        ("run", None, "rejected", False),
    ],
)
def test_training_and_validation_submissions_share_the_invariant(
    run_id: str | None, code: AdmissionRejectionCode | None, error: str | None, valid: bool
) -> None:
    for create in (
        lambda: TrialAttempt("e", 0, 1, datetime.now(UTC), run_id, code, error),
        lambda: WindowPick("e", 0, 1, 0, 0.1, datetime.now(UTC), run_id, code, error),
    ):
        if valid:
            create()
        else:
            with pytest.raises(ValueError, match="submission carries"):
                create()


def test_an_unselected_window_cannot_carry_a_submission_or_training_score() -> None:
    WindowPick("e", 0, 1, None, None, datetime.now(UTC))
    with pytest.raises(ValueError, match="training score"):
        WindowPick("e", 0, 1, None, 0.1, datetime.now(UTC))
    with pytest.raises(ValueError, match="no submission"):
        WindowPick("e", 0, 1, None, None, datetime.now(UTC), run_id="run")
