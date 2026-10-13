"""실험 시간 추정(로드맵 M6) — 계열의 최근 완료 실행이 걸린 시간으로 실험 전체 시간을 어림한다.

최적화 도구의 흔한 어림(백테스트 수 × 백테스트 한 번 시간 ÷ 동시 실행 수)을 따르되 숫자는
길게 잡는다.

- 실행 하나의 시간은 계열에서 가장 최근에 접수된 완료 실행 `RECENT_RUNS` 개 중 가장 긴 것이다. 시도
  창은 기반 실행 구간보다 짧아 실제보다 길게 어림하는 쪽이다.
- 워크포워드는 학습 실행을 다 돈 뒤 창마다 검증 실행을 돈다고 보고 두 단계를 더한다.
- 다른 실험·단일 실행과 슬롯을 나눠 쓰는 대기는 넣지 않는다 — 이 실험만 돌 때의 시간이다.
- 메모리는 어림하지 않는다. run 이 한 프로세스의 스레드로 돌아 run 하나의 메모리를 잴 수 없다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from strategy_workbench.domain.backtest.facade.trials import TrialLedger

from ._design import ExperimentDesign

# 어림의 근거로 보는 최근 완료 실행 수. 하나면 우연히 짧은 실행에 끌리고, 많으면 오래전 데이터
# 크기·엔진 속도가 섞인다.
RECENT_RUNS = 5


@dataclass(frozen=True)
class TimeEstimate:
    total_seconds: float
    # 실행 하나로 잡은 시간(근거 실행 중 가장 긴 것).
    run_seconds: float
    # 근거가 된 완료 실행 수(최대 `RECENT_RUNS`).
    sample_count: int
    # 실험 run 이 동시에 쓰는 슬롯 수.
    slots: int


def estimate_time(ledger: TrialLedger, design: ExperimentDesign, slots: int) -> TimeEstimate | None:
    """계열에 시작~완료 시간이 남은 실행이 없으면 None — 어림할 근거가 없다."""
    if slots < 1:
        raise ValueError(f"slots must be at least 1 — slots={slots!r}")
    finished = sorted(
        (
            (run.created_at, run.seconds)
            for trial in ledger.trials
            for run in trial.runs
            if run.seconds is not None
        ),
        key=lambda item: item[0],
    )[-RECENT_RUNS:]
    if not finished:
        return None
    run_seconds = max(seconds for _created_at, seconds in finished)
    phases = (len(design.trials()), len(design.windows) if design.walks_forward else 0)
    waves = sum(math.ceil(count / slots) for count in phases)
    return TimeEstimate(
        total_seconds=waves * run_seconds,
        run_seconds=run_seconds,
        sample_count=len(finished),
        slots=slots,
    )
