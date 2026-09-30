"""계열 시도 원장의 집계 — 시도 묶음·대표 샤프·N(검증 랩 spec D2, V1-05)."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import pytest

from strategy_workbench.domain.analytics.facade.metrics import session_sharpe
from strategy_workbench.domain.backtest.facade.runs import RunStatus
from strategy_workbench.domain.backtest.facade.trials import (
    BlockedTrialAttempt,
    TrialLedgerEntry,
    TrialPreviewReason,
    TrialRunRole,
    preview_trial,
    summarize_trial_ledger,
)

_AT = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
_A, _B, _C = "a" * 64, "b" * 64, "c" * 64


def _entry(
    run_id: str,
    key: str,
    status: RunStatus,
    *,
    done_after_minutes: int = 0,
    sharpe: float | None = None,
) -> TrialLedgerEntry:
    return TrialLedgerEntry(
        run_id=run_id,
        trial_key=key,
        status=status,
        created_at=_AT,
        updated_at=_AT + timedelta(minutes=done_after_minutes),
        session_sharpe=sharpe,
        metric_registry_version=None if sharpe is None else "metric-registry-v4",
    )


def test_a_trial_counts_once_and_its_first_completed_run_is_the_representative() -> None:
    # 접수 순: r1(늦게 완료) → r2(먼저 완료) → r3(실패) → r4(도는 중) → r5(다른 시도, 취소)
    ledger = summarize_trial_ledger(
        "s-1",
        (),
        (
            _entry("r1", _A, RunStatus.COMPLETED, done_after_minutes=9, sharpe=0.05),
            _entry("r2", _A, RunStatus.COMPLETED, done_after_minutes=3, sharpe=0.07),
            _entry("r3", _A, RunStatus.FAILED),
            _entry("r4", _A, RunStatus.RUNNING),
            _entry("r5", _B, RunStatus.CANCELLED),
        ),
        (),
    )

    first, second = ledger.trials
    assert ledger.trial_count == 1
    assert (first.trial_key, first.representative_run_id, first.representative_sharpe) == (
        _A,
        "r2",
        0.07,
    )
    # 재확인 실행도 자기 세션 샤프를 싣는다(워크포워드 창 고르기의 학습 점수).
    assert [(run.run_id, run.role, run.session_sharpe) for run in first.runs] == [
        ("r1", TrialRunRole.RECHECK, 0.05),
        ("r2", TrialRunRole.COUNTED, 0.07),
        ("r3", TrialRunRole.NO_RESULT, None),
        ("r4", TrialRunRole.PENDING, None),
    ]
    assert second.representative_run_id is None
    assert [run.role for run in second.runs] == [TrialRunRole.NO_RESULT]


def test_merged_lineages_share_trials_by_key_and_blocked_attempts_stay_out_of_n() -> None:
    blocked = BlockedTrialAttempt(_AT, "s-2", _C, "d" * 64, date(2019, 6, 3))
    ledger = summarize_trial_ledger(
        "s-1",
        ("s-2",),
        (
            _entry("s1-run", _A, RunStatus.COMPLETED, sharpe=0.1),
            _entry("s2-run", _A, RunStatus.COMPLETED, done_after_minutes=1, sharpe=0.2),
            _entry("s2-other", _B, RunStatus.COMPLETED, sharpe=0.3),
        ),
        (blocked,),
    )

    assert ledger.trial_count == 2
    assert [trial.trial_key for trial in ledger.trials] == [_A, _B]
    assert ledger.blocked == (blocked,)


@pytest.mark.parametrize(
    ("key", "new_trial", "after", "reason"),
    [(_A, False, 1, TrialPreviewReason.RECHECK), (_B, True, 2, TrialPreviewReason.NEW_TRIAL)],
)
def test_the_preview_says_whether_a_result_would_add_a_trial(
    key: str, new_trial: bool, after: int, reason: TrialPreviewReason
) -> None:
    ledger = summarize_trial_ledger(
        "s-1",
        (),
        (_entry("r1", _A, RunStatus.COMPLETED), _entry("r2", _B, RunStatus.FAILED)),
        (),
    )

    preview = preview_trial(ledger, key)

    assert (
        preview.trial_count,
        preview.new_trial,
        preview.trial_count_after,
        preview.reason,
    ) == (1, new_trial, after, reason)


def test_a_run_without_a_lineage_never_counts() -> None:
    preview = preview_trial(None, _A)

    assert (preview.lineage_id, preview.new_trial, preview.trial_count_after) == (None, False, 0)
    assert preview.reason is TrialPreviewReason.NO_LINEAGE


def test_the_session_sharpe_undoes_the_annualisation() -> None:
    # 연 샤프 1.5 를 252일로 연율화했다면 세션 샤프는 1.5 / √252 ≈ 0.094491.
    assert session_sharpe(1.5, 252) == pytest.approx(0.0944911, abs=1e-7)
    assert session_sharpe(1.5 * math.sqrt(250 / 252), 250) == pytest.approx(
        session_sharpe(1.5, 252)
    )
