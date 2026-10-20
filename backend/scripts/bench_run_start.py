"""run 시작 지연과 예비 worker 유지 비용 측정(run-worker W2-01, spec D7·V-5).

프로세스 방식 앱 둘을 임시 경로로 조립한다. 하나는 예비 worker 를 두고(제품 기본), 하나는 예비 없이
run 마다 worker 를 띄운다. 같은 요청을 번갈아 보내 접수부터 첫 tape 진행까지를 재고, tape 에 들면
그 run 을 취소한다(결과를 만들지 않는다). 같은 요청은 진행 중 run 에 합쳐지므로 초기자본을 1원씩
바꾼다. 이어 job 을 기다리는 예비 하나의 RSS·private 메모리·CPU 를 잰다(psutil 이 있으면).

측정 절차와 결과의 정본은 `docs/planning/run-worker-process/BENCHMARK.md` 다. 구간 시작은 홀드아웃
규칙에 따라 2020-01-02 이후만 받는다(`.claude/rules/holdout-window.md`).

사용법:
    uv run --no-sync --with psutil python scripts/bench_run_start.py \
        ../docs/planning/run-worker-process/bench-strategy.yaml \
        --start 2025-01-02 --end 2025-12-30 --equity-root ~/quant-ledger/data/equity --rounds 3
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import tempfile
import time
from contextlib import ExitStack
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.run_worker_process.facade.execution import (
    ProcessRunExecution,
)
from strategy_workbench.bootstrap import _container
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.environment import RESEARCH_START

try:
    import psutil
except ImportError:  # 선택 의존성이다. 없으면 유휴 비용만 빠진다
    psutil = None

TERMINAL = ("completed", "failed", "cancelled")
_POLL_S = 0.01
_TIMEOUT_S = 300.0


class _Capture(ProcessRunExecution):
    """서버 조립이 만든 프로세스 전송을 붙잡아 예비 상태를 본다."""

    made: list[ProcessRunExecution] = []

    # reason: 서버 조립이 넘기는 인자를 그대로 넘기는 패스스루라 `Any` 다.
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _Capture.made.append(self)


def _app(tmp: Path, args: argparse.Namespace, *, spare: bool) -> tuple[Any, ProcessRunExecution]:
    """임시 경로의 프로세스 방식 앱과 그 전송. `spare=False` 면 예비 없이 run 마다 띄운다."""
    _Capture.made.clear()
    equity_root = Path(args.equity_root).expanduser() if args.equity_adapter == "duckdb" else None
    # 조립 중에만 전송 하나를 바꿔 낀다. 요청 처리 중의 전역 patch 는 없다.
    with patch.object(
        _container, "ProcessRunExecution", _Capture if spare else partial(_Capture, spare=False)
    ):
        app = build_http_app(
            strategy_repository_path=tmp / "strategies.sqlite3",
            research_db_path=tmp / "research.sqlite3",
            continuous_db_path=tmp / "continuous.sqlite3",
            artifact_root=tmp / "artifacts",
            run_isolation="process",
            equity_adapter=args.equity_adapter,
            equity_root=equity_root,
        )
    return app, _Capture.made[0]


def _wait(condition: Any, what: str) -> Any:
    deadline = time.perf_counter() + _TIMEOUT_S
    while time.perf_counter() < deadline:
        if value := condition():
            return value
        time.sleep(_POLL_S)
    raise SystemExit(f"{what} — {_TIMEOUT_S}s 안에 오지 않았다")


def _start_latency(
    client: TestClient, source: dict, args: argparse.Namespace, cash: float
) -> float:
    """접수(202)부터 첫 tape 진행까지 초. 그 run 은 취소하고 끝날 때까지 기다린다."""
    body = {
        "strategy_source": source,
        "environment": {"start": args.start, "end": args.end, "universe_id": "krx.common-stock"},
        "core": "rust",
        "initial_cash": cash,
    }
    response = client.post("/api/v1/backtests", json=body)
    accepted = time.perf_counter()
    if response.status_code != 202:
        raise SystemExit(f"run 접수 거절 — status={response.status_code} body={response.text}")
    run_id = response.json()["run"]["run_id"]

    def tape() -> dict | None:
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        state = state.get("run", state)
        if state["status"] in TERMINAL:
            raise SystemExit(f"run 이 tape 전에 끝났다 — state={state}")
        return state if state["stage"] == "tape" else None

    _wait(tape, f"run {run_id} 의 첫 tape 진행")
    latency = time.perf_counter() - accepted
    client.post(f"/api/v1/backtests/{run_id}/cancel")

    def finished() -> bool:
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        return state.get("run", state)["status"] in TERMINAL

    _wait(finished, f"run {run_id} 취소")
    return latency


def _idle_cost(execution: ProcessRunExecution, seconds: float) -> dict[str, float] | None:
    """job 을 기다리는 예비(런처와 그 자식 인터프리터)의 메모리와 CPU."""
    if psutil is None:
        return None
    pid = _wait(execution.spare_pid, "준비된 예비")
    root = psutil.Process(pid)
    tree = [root, *root.children(recursive=True)]
    cpu_before = sum(sum(process.cpu_times()[:2]) for process in tree)
    started = time.perf_counter()
    time.sleep(seconds)
    elapsed = time.perf_counter() - started
    cpu = sum(sum(process.cpu_times()[:2]) for process in tree) - cpu_before
    memory = [process.memory_full_info() for process in tree]
    return {
        "processes": len(tree),
        "rss_mb": round(sum(item.rss for item in memory) / 2**20, 1),
        "private_mb": round(sum(getattr(item, "private", item.uss) for item in memory) / 2**20, 1),
        "cpu_percent_of_one_core": round(100 * cpu / elapsed, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="run 시작 지연(예비 대 직접 띄우기)과 예비 유지 비용"
    )
    parser.add_argument("strategy", help="YAML 전략 파일")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--equity-adapter", choices=("duckdb", "mock"), default="duckdb")
    parser.add_argument("--equity-root", default="~/quant-ledger/data/equity")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--cash", type=float, default=100_000_000.0)
    parser.add_argument("--idle-seconds", type=float, default=60.0)
    args = parser.parse_args()
    if date.fromisoformat(args.start) < RESEARCH_START:
        raise SystemExit(f"구간 시작은 {RESEARCH_START} 이후여야 한다(홀드아웃) — got={args.start}")

    tmp = Path(tempfile.mkdtemp(prefix="bench-run-start-"))
    latencies: dict[str, list[float]] = {"spare": [], "direct": []}
    with ExitStack() as stack:
        apps = {}
        for mode in ("spare", "direct"):
            app, execution = _app(tmp / mode, args, spare=mode == "spare")
            apps[mode] = (stack.enter_context(TestClient(app)), execution)
        client = apps["spare"][0]
        compiled = client.post(
            "/api/v1/strategy-documents/compile",
            json={"source": Path(args.strategy).read_text(encoding="utf-8"), "format": "yaml"},
        ).json()
        if compiled.get("spec") is None:
            raise SystemExit(
                f"전략을 compile 하지 못했다 — diagnostics={compiled.get('diagnostics')}"
            )
        source = {
            "kind": "inline_draft",
            "spec": compiled["spec"],
            "source_hash": compiled["source_hash"],
        }
        cash = args.cash
        for round_index in range(args.rounds):
            # 순서 효과(캐시·부하)를 빼려고 매 회 앞뒤를 바꾼다.
            order = ("spare", "direct") if round_index % 2 == 0 else ("direct", "spare")
            for mode in order:
                client, execution = apps[mode]
                if mode == "spare":
                    _wait(execution.spare_pid, "준비된 예비")
                cash += 1
                latencies[mode].append(round(_start_latency(client, source, args, cash), 3))
        idle = _idle_cost(apps["spare"][1], args.idle_seconds)
    shutil.rmtree(tmp, ignore_errors=True)
    print(
        json.dumps(
            {
                "equity_adapter": args.equity_adapter,
                "start_latency_s": latencies,
                "start_latency_min_s": {mode: min(values) for mode, values in latencies.items()},
                "start_latency_median_s": {
                    mode: round(statistics.median(values), 3) for mode, values in latencies.items()
                },
                "idle_spare": idle,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
