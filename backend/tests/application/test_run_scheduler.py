"""검증 랩 V3-04: 대기 run 배정 순서 — 단일 실행 먼저, 실험끼리 라운드로빈(spec D6)."""

from __future__ import annotations

import pytest

from strategy_workbench.application.backtest_run._scheduler import RunQueue, experiment_slots


def test_single_runs_go_first_and_experiments_take_turns_without_starving() -> None:
    queue: RunQueue[str] = RunQueue()
    for item in ("a1", "a2", "a3"):
        queue.push(item, "A")
    queue.push("b1", "B")
    queue.push("s1", None)
    queue.push("c1", "C")  # 가장 늦게 온 실험도 A 가 셋을 다 돌기 전에 차례가 온다

    order = [queue.pop(experiments=True) for _ in range(6)]

    # FIFO 였다면 a1, a2, a3, b1, c1 순이다.
    assert order == ["s1", "a1", "b1", "c1", "a2", "a3"]
    assert not queue and queue.pop(experiments=True) is None


def test_a_full_experiment_share_still_lets_single_runs_through() -> None:
    queue: RunQueue[str] = RunQueue()
    queue.push("a1", "A")
    queue.push("s1", None)

    assert queue.pop(experiments=False) == "s1"
    assert queue.pop(experiments=False) is None
    assert "a1" in queue
    queue.remove("a1")
    assert not queue


@pytest.mark.parametrize(("run_slots", "expected"), [(1, 1), (2, 1), (3, 2), (8, 7)])
def test_one_slot_is_kept_for_single_runs_only_when_there_are_two_or_more(
    run_slots: int, expected: int
) -> None:
    assert experiment_slots(run_slots) == expected


def test_priority_weights_take_that_many_turns_and_paused_lanes_keep_their_place() -> None:
    queue: RunQueue[str] = RunQueue()
    queue.configure("A", paused=False, weight=2)
    for item in ("a1", "a2", "a3", "a4"):
        queue.push(item, "A")
    for item in ("b1", "b2"):
        queue.push(item, "B")
    queue.push("c1", "C")

    first = [queue.pop(experiments=True) for _ in range(3)]
    queue.configure("B", paused=True, weight=1)
    while_paused = [queue.pop(experiments=True) for _ in range(3)]
    assert queue.pop(experiments=True) is None
    queue.configure("B", paused=False, weight=1)
    rest = [queue.pop(experiments=True) for _ in range(2)]

    # 우선순위 2 인 A 는 한 차례에 둘씩 나간다.
    assert first == ["a1", "a2", "b1"]
    # 멈춘 B 는 건너뛰고, 대기 run 이 없으면 None 이다.
    assert while_paused == ["c1", "a3", "a4"]
    assert rest == ["b2", None]
