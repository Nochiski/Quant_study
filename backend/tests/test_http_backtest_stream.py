"""백테스트 run SSE 프레이밍(#161).

HTTP 왕복은 `tests/integration/test_backtest_http_api.py`가 본다. 여기서는 스트림 제너레이터 하나를
직접 돌려, 진짜 run 스레드로는 재현하기 어려운 두 가지를 고정한다.

1. **run 이 끝나는 순간에 기록된 마지막 이벤트를 놓치지 않는가.** 종결 여부를 이벤트보다 먼저 읽는
   순서가 이 성질의 전부라, 순서를 뒤집으면 이 테스트만 깨진다.
2. 조용한 구간(자리를 기다리는 `queued`, 긴 tape)의 keepalive 주석.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

from strategy_workbench.adapters.inbound.http_api._app import (
    _backtest_event_stream,  # pyright: ignore[reportPrivateUsage]  # reason: 라우트 내부 제너레이터라 공개 facade가 없다
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunService,
    BacktestRunState,
    RunProgressEvent,
    RunStatus,
)

_RUN = "run-1"
_AT = datetime(2026, 9, 29, tzinfo=UTC)


class _ScriptedRuns:
    """`state()`·`events()` 가짜 구현. `state()` 호출마다 다음 단계로 넘어간다.

    단계마다 "그 조회 시점에 기록돼 있던 이벤트"를 통째로 적는다. 스트림은 종결 여부와 이벤트를
    같은 순간에 읽지 않으므로, 그 사이 기록된 이벤트가 어떻게 보이는지 단계별 스냅샷으로 재현한다.
    """

    def __init__(self, steps: list[tuple[RunStatus, list[RunProgressEvent]]]) -> None:
        self._steps = steps
        self._index = -1

    def state(self, run_id: str) -> BacktestRunState:
        self._index = min(self._index + 1, len(self._steps) - 1)
        status = self._steps[self._index][0]
        return BacktestRunState(run_id, status, 0.0, "engine", status.value, _AT, _AT)

    def events(self, run_id: str, *, after_sequence: int = -1) -> tuple[RunProgressEvent, ...]:
        stored = self._steps[max(self._index, 0)][1]
        return tuple(event for event in stored if event.sequence > after_sequence)


def _event(sequence: int, status: RunStatus) -> RunProgressEvent:
    return RunProgressEvent(sequence, _RUN, status, 0.0, "engine", status.value, _AT)


def _run(runs: _ScriptedRuns, *, keepalive_seconds: float = 15.0) -> list[str]:
    async def collect() -> list[str]:
        return [
            frame
            async for frame in _backtest_event_stream(
                cast(BacktestRunService, runs),
                _RUN,
                after_sequence=-1,
                keepalive_seconds=keepalive_seconds,
                poll_seconds=0.0,
            )
        ]

    return asyncio.run(collect())


def test_the_last_event_recorded_as_the_run_ends_is_not_dropped() -> None:
    """두 번째 조회에서 run 은 이미 끝났고 종결 이벤트는 그 조회 뒤에야 보인다.

    이벤트를 먼저 읽고 종결을 나중에 읽으면 이 프레임이 영영 전송되지 않는다.
    """
    runs = _ScriptedRuns(
        [
            (RunStatus.RUNNING, [_event(0, RunStatus.QUEUED)]),
            (
                RunStatus.COMPLETED,
                [_event(0, RunStatus.QUEUED), _event(1, RunStatus.COMPLETED)],
            ),
        ]
    )

    frames = _run(runs)

    assert [frame.splitlines()[0] for frame in frames] == ["id: 0", "id: 1"]
    assert '"status":"completed"' in frames[1]


def test_a_quiet_stream_sends_a_keepalive_comment() -> None:
    runs = _ScriptedRuns(
        [
            (RunStatus.QUEUED, []),
            (RunStatus.CANCELLED, [_event(0, RunStatus.CANCELLED)]),
        ]
    )

    frames = _run(runs, keepalive_seconds=0.0)

    assert frames[0] == ": keepalive\n\n"
    assert frames[1].startswith("id: 0\nevent: progress\ndata: ")
