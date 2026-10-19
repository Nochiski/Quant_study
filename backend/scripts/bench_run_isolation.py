"""run 실행 방식 측정: 서버 안 스레드 대 별도 프로세스. 실행 시간·API 응답·메모리를 잰다.

측정 절차와 결과의 정본은 `docs/planning/run-worker-process/BENCHMARK.md` 다. 같은 요청은
진행 중 run 에 합쳐지므로(US-SM-11) 동시 run 은 초기자본을 1원씩 달리한다. 구간 시작은
홀드아웃 규칙에 따라 2020-01-02 이후만 받는다(`.claude/rules/holdout-window.md`).

- `--mode thread`: `--server` 로 떠 있는 서버에 run 을 보내고, 끝날 때까지 health 지연을 잰다.
- `--mode process`: run 마다 새 프로세스가 임시 경로로 앱을 조립해 run 하나를 돌린다(라이브
  DB·산출물을 건드리지 않는다). `--server` 를 주면 그동안 서버 응답도 잰다.

psutil 이 있으면(`uv run --no-sync --with psutil ...`) 시스템 사용 코어·가용 메모리·최대 RSS 를
함께 적는다.

사용법:
    uv run --no-sync --with psutil python scripts/bench_run_isolation.py strategy.yaml \
        --start 2025-01-02 --end 2025-12-30 --mode process --parallel 2 \
        --equity-root ~/quant-ledger/data/equity --server http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path

from strategy_workbench.domain.backtest.facade.environment import RESEARCH_START

TERMINAL = ("completed", "failed", "cancelled")

try:
    import psutil
except ImportError:  # 선택 의존성이다. 없으면 시스템 지표만 빠진다
    psutil = None


def _call(base: str, path: str, body: object | None = None, timeout: float = 60) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _percentile(values: list[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 1)


class _Sampler:
    """0.25초마다 서버 health 지연, 1초마다 시스템 사용 코어와 가용 메모리를 적는다."""

    def __init__(self, server: str | None) -> None:
        self._server = server
        self.health_ms: list[float] = []
        self.health_failures = 0
        self.cores: list[float] = []
        self.available_gb: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        last = 0.0
        if psutil is not None:
            psutil.cpu_percent(None)
        while not self._stop.is_set():
            if self._server is not None:
                started = time.perf_counter()
                try:
                    _call(self._server, "/api/v1/health", timeout=20)
                except OSError:
                    # 시간초과·연결 실패도 그만큼 기다린 표본이다. 빼면 가장 나쁜 값이 사라진다
                    self.health_failures += 1
                self.health_ms.append((time.perf_counter() - started) * 1e3)
            if psutil is not None and time.perf_counter() - last >= 1.0:
                self.cores.append(psutil.cpu_percent(None) / 100 * (psutil.cpu_count() or 1))
                self.available_gb.append(psutil.virtual_memory().available / 2**30)
                last = time.perf_counter()
            time.sleep(0.25)

    def __enter__(self) -> _Sampler:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()

    def summary(self) -> dict[str, float | int | None]:
        return {
            "health_failures": self.health_failures,
            "health_p50_ms": _percentile(self.health_ms, 0.5),
            "health_p95_ms": _percentile(self.health_ms, 0.95),
            "health_max_ms": _percentile(self.health_ms, 1.0),
            "system_cores_mean": round(statistics.mean(self.cores), 2) if self.cores else None,
            "min_available_gb": round(min(self.available_gb), 1) if self.available_gb else None,
        }


def _run_request(spec_source: dict, start: str, end: str, cash: float) -> dict:
    return {
        "strategy_source": spec_source,
        "environment": {"start": start, "end": end, "universe_id": "krx.common-stock"},
        "core": "rust",
        "initial_cash": cash,
    }


def _child(args: argparse.Namespace) -> None:
    """프로세스 모드의 자식: 임시 경로로 앱을 조립해 run 하나를 돌리고 JSON 한 줄을 낸다."""
    started = time.perf_counter()
    peak = {"rss": 0}
    stop = threading.Event()
    if psutil is not None:
        process = psutil.Process()

        def sample() -> None:
            while not stop.is_set():
                peak["rss"] = max(peak["rss"], process.memory_info().rss)
                time.sleep(0.2)

        threading.Thread(target=sample, daemon=True).start()
    from fastapi.testclient import TestClient

    from strategy_workbench.bootstrap.facade.http import build_http_app

    tmp = Path(tempfile.mkdtemp(prefix="bench-run-"))
    app = build_http_app(
        strategy_repository_path=tmp / "strategies.sqlite3",
        research_db_path=tmp / "research.sqlite3",
        continuous_db_path=tmp / "continuous.sqlite3",
        artifact_root=tmp / "artifacts",
        equity_adapter="duckdb",
        equity_root=Path(args.equity_root).expanduser(),
    )
    boot_s = time.perf_counter() - started
    client = TestClient(app)
    compiled = client.post(
        "/api/v1/strategy-documents/compile",
        json={"source": Path(args.strategy).read_text(encoding="utf-8"), "format": "yaml"},
    ).json()
    if compiled.get("spec") is None:
        raise SystemExit(f"전략을 compile 하지 못했다 — diagnostics={compiled.get('diagnostics')}")
    source = {
        "kind": "inline_draft",
        "spec": compiled["spec"],
        "source_hash": compiled["source_hash"],
    }
    run_started = time.perf_counter()
    response = client.post(
        "/api/v1/backtests", json=_run_request(source, args.start, args.end, args.cash)
    )
    if response.status_code != 202:
        raise SystemExit(f"run 접수 거절 — status={response.status_code} body={response.text}")
    run_id = response.json()["run"]["run_id"]
    while True:
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        state = state.get("run", state)
        if state["status"] in TERMINAL:
            break
        time.sleep(0.2)
    stop.set()
    client.close()
    # Windows 에서는 아직 열린 DB 핸들이 있을 수 있어 지우지 못한 파일은 남긴다
    shutil.rmtree(tmp, ignore_errors=True)
    print(
        json.dumps(
            {
                "run_s": round(time.perf_counter() - run_started, 1),
                "boot_s": round(boot_s, 1),
                "status": state["status"],
                "error": state.get("error"),
                "peak_rss_mb": round(peak["rss"] / 2**20) if psutil is not None else None,
            }
        )
    )


def _measure_thread(args: argparse.Namespace) -> dict:
    compiled = _call(
        args.server,
        "/api/v1/strategy-documents/compile",
        {"source": Path(args.strategy).read_text(encoding="utf-8"), "format": "yaml"},
    )
    source = {
        "kind": "inline_draft",
        "spec": compiled["spec"],
        "source_hash": compiled["source_hash"],
    }
    started = time.perf_counter()
    run_ids = [
        _call(
            args.server,
            "/api/v1/backtests",
            _run_request(source, args.start, args.end, args.cash + index),
        )["run"]["run_id"]
        for index in range(args.parallel)
    ]
    if len(set(run_ids)) != len(run_ids):
        raise SystemExit("동시 run 이 하나로 합쳐졌다 — 요청을 서로 다르게 만들어야 한다")
    # 모든 run 을 함께 살펴 끝난 시각을 각자 적는다(차례로 기다리면 뒤 run 의 시간이 부푼다)
    finished: dict[str, tuple[float, str]] = {}
    while len(finished) < len(run_ids):
        for run_id in run_ids:
            if run_id in finished:
                continue
            state = _call(args.server, f"/api/v1/backtests/{run_id}")
            state = state.get("run", state)
            if state["status"] in TERMINAL:
                finished[run_id] = (round(time.perf_counter() - started, 1), state["status"])
        time.sleep(0.5)
    return {
        "wall_s": [finished[run_id][0] for run_id in run_ids],
        "status": [finished[run_id][1] for run_id in run_ids],
    }


def _measure_process(args: argparse.Namespace) -> dict:
    command = [
        sys.executable,
        __file__,
        args.strategy,
        "--child",
        "--start",
        args.start,
        "--end",
        args.end,
        "--equity-root",
        args.equity_root,
    ]
    children = [
        subprocess.Popen(
            [*command, "--cash", str(args.cash + index)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for index in range(args.parallel)
    ]
    rows = []
    for child in children:
        out, err = child.communicate()
        lines = out.strip().splitlines()
        if child.returncode != 0 or not lines:
            raise SystemExit(
                f"측정 자식 프로세스 실패 — exit={child.returncode} stderr 끝={err.strip()[-2000:]}"
            )
        rows.append(json.loads(lines[-1]))
    return {
        "wall_s": [row["run_s"] for row in rows],
        "boot_s": [row["boot_s"] for row in rows],
        "peak_rss_mb": [row["peak_rss_mb"] for row in rows],
        "status": [row["status"] for row in rows],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="run 실행 방식(스레드·별도 프로세스) 측정")
    parser.add_argument("strategy", help="YAML 전략 파일")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--mode", choices=("thread", "process"), default="process")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--server", default=None, help="예: http://127.0.0.1:8000")
    parser.add_argument("--equity-root", default="~/quant-ledger/data/equity")
    parser.add_argument("--cash", type=float, default=100_000_000.0)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if date.fromisoformat(args.start) < RESEARCH_START:
        raise SystemExit(
            f"구간 시작이 연구 하한보다 이르다 — start={args.start} floor={RESEARCH_START}"
        )
    if args.child:
        _child(args)
        return
    if args.mode == "thread" and args.server is None:
        raise SystemExit("--mode thread 는 --server 가 필요하다")
    with _Sampler(args.server) as sampler:
        result = _measure_thread(args) if args.mode == "thread" else _measure_process(args)
    row = {
        "mode": args.mode,
        "parallel": args.parallel,
        "window": [args.start, args.end],
        **result,
        **sampler.summary(),
    }
    print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
