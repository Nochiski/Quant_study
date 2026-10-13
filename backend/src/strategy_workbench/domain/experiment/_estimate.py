"""실험 시간 어림(로드맵 M6) — 계열의 최근 완료 실행이 걸린 시간으로 실험 전체 시간을 어림한다.

숫자는 길게 잡는다(#519 리뷰 B1·N1).

- 실행 하나의 시간은 근거 실행마다 "그 실행 시간 × max(1, 이 실행 구간 ÷ 근거 실행 구간)" 을 내고
  그중 가장 긴 값이다. 긴 구간은 비례로 늘리고 짧은 구간은 줄이지 않는다 — 데이터 읽기·컴파일 같은
  고정 비용이 있어 짧은 구간이 비례만큼 빨라지지 않는다.
- 근거는 계열에서 가장 최근에 접수된 완료 실행 `RECENT_RUNS` 개다(시작 시각·구간이 남은 실행).
- 실행은 차례로 돈다고 보고 모두 더한다. run 이 한 프로세스의 스레드라 슬롯 수만큼 빨라진다고 볼 수
  없다. 다른 실험·단일 실행을 기다리는 시간은 넣지 않는다.
- 메모리는 어림하지 않는다. 같은 이유로 run 하나의 메모리를 잴 수 없다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from strategy_workbench.domain.backtest.facade.trials import TrialLedger, span_days

from ._design import ExperimentDesign

# 어림의 근거로 보는 최근 완료 실행 수. 하나면 우연히 짧은 실행에 끌리고, 많으면 오래전 데이터
# 크기·엔진 속도가 섞인다.
RECENT_RUNS = 5


@dataclass(frozen=True)
class TimeEstimate:
    total_seconds: float
    # 근거가 된 완료 실행 수(최대 `RECENT_RUNS`).
    sample_count: int


def planned_spans(design: ExperimentDesign, base_start: date, base_end: date) -> tuple[int, ...]:
    """실험이 돌릴 실행마다의 구간 일수 — trial(창의 학습 구간, 용량 스윕은 기반 구간)과
    워크포워드면 창마다 검증 실행 하나."""
    trials = tuple(
        span_days(base_start, base_end)
        if trial.window is None
        else span_days(trial.window.train_start, trial.window.train_end)
        for trial in design.trials()
    )
    tests = (
        tuple(span_days(window.test_start, window.test_end) for window in design.windows)
        if design.walks_forward
        else ()
    )
    return trials + tests


def estimate_time(ledger: TrialLedger, spans: Sequence[int]) -> TimeEstimate | None:
    """`spans` 는 돌릴 실행마다의 구간 일수다. 근거 실행이 없으면 None — 어림할 근거가 없다."""
    timed = [
        (run.created_at, run.seconds, run.span_days)
        for trial in ledger.trials
        for run in trial.runs
        if run.seconds is not None and run.span_days is not None
    ]
    samples = sorted(timed, key=lambda sample: sample[0])[-RECENT_RUNS:]
    if not samples:
        return None
    total = sum(
        max(seconds * max(1.0, span / sample_span) for _at, seconds, sample_span in samples)
        for span in spans
    )
    return TimeEstimate(total_seconds=total, sample_count=len(samples))
