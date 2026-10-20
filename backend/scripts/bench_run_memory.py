"""run 메모리 예약치 대 실측 최대 RSS, 메모리 가드 전후 실험 처리량(run-worker W2-02, spec D8).

프로세스 방식 앱을 임시 경로로 조립해 실데이터 run 하나를 끝까지 돌리고, 부모 로그에서 그 run 의
예약치·세션 수 어림(배정 때)과 worker 가 보고한 최대 RSS·읽은 입력의 크기(완료 때)를 모은다.
`--experiment-start`·`--experiment-end` 를 주면 같은 전략의 롤링 실험(학습 1년·검증 1년)을 가드
기본값과 가드를 사실상 끈 설정(예약치 0.001GB)으로 한 번씩 돌려 끝날 때까지의 시간을 잰다. 실험
trial 은 실험 몫 슬롯 하나를 차례로 쓰므로 두 시간이 같아야 한다.

run 은 한 번에 하나만 돈다. 시작 전 가용 메모리가 예약치보다 작으면 돌리지 않는다(페이징 방지).
측정 절차와 결과의 정본은 `docs/planning/run-worker-process/BENCHMARK.md` 다. 구간 시작은 홀드아웃
규칙에 따라 2020-01-02 이후만 받는다(`.claude/rules/holdout-window.md`).

사용법:
    uv run --no-sync python scripts/bench_run_memory.py \
        ../docs/planning/run-worker-process/bench-strategy.yaml \
        --start 2025-01-02 --end 2025-12-30 --equity-root ~/quant-ledger/data/equity \
        --experiment-start 2023-01-02 --experiment-end 2024-12-30
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from strategy_workbench.adapters.outbound.run_worker_process._memory import (
    available_memory_bytes,
)
from strategy_workbench.application.backtest_run._memory_guard import (
    estimated_sessions,
    run_reserve_bytes,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.environment import RESEARCH_START

TERMINAL = ("completed", "failed", "cancelled")
_GB = 2**30
_POLL_S = 0.2
_TIMEOUT_S = 3600.0
# 가드를 사실상 끄는 예약치(GB). 도는 run 이 있어도 늘 들어간다.
_GUARD_OFF_GB = 0.001


class _Records(logging.Handler):
    """부모 로그에서 메모리 가드·worker 보고 줄의 인자를 모은다."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.dispatched: dict[str, tuple[int, int]] = {}
        self.committed: dict[str, tuple[object, ...]] = {}
        self.waited: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if not isinstance(record.args, tuple) or not record.args:
            return
        args: tuple[object, ...] = record.args
        message = str(record.msg)
        if message.startswith("backtest run dispatched"):
            self.dispatched[str(args[0])] = (int(str(args[1])), int(str(args[2])))
        elif message.startswith("run worker committed"):
            self.committed[str(args[0])] = args[1:]
        elif message.startswith("backtest run waits for memory"):
            self.waited.append(str(args[0]))


def _app(tmp: Path, args: argparse.Namespace, reserve_gb: float | None) -> Any:
    return build_http_app(
        strategy_repository_path=tmp / "strategies.sqlite3",
        research_db_path=tmp / "research.sqlite3",
        continuous_db_path=tmp / "continuous.sqlite3",
        artifact_root=tmp / "artifacts",
        run_isolation="process",
        run_memory_reserve_gb=reserve_gb,
        equity_adapter="duckdb",
        equity_root=Path(args.equity_root).expanduser(),
    )


def _wait(condition: Any, what: str) -> Any:
    deadline = time.perf_counter() + _TIMEOUT_S
    while time.perf_counter() < deadline:
        if value := condition():
            return value
        time.sleep(_POLL_S)
    raise SystemExit(f"{what} — {_TIMEOUT_S}s 안에 끝나지 않았다")


def _require_memory(start: str, end: str, history: int) -> None:
    """예약치만큼 가용 메모리가 없으면 멈춘다. 측정이 PC 를 페이징으로 몰지 않게 한다."""
    reserve = run_reserve_bytes(
        estimated_sessions(date.fromisoformat(start), date.fromisoformat(end), history)
    )
    available = available_memory_bytes() or 0
    if available < reserve:
        raise SystemExit(
            f"가용 메모리가 예약치보다 작다 — available_gb={available / _GB:.2f} "
            f"reserve_gb={reserve / _GB:.2f} start={start} end={end}"
        )


def _single_run(
    client: TestClient, source: dict, args: argparse.Namespace, records: _Records
) -> dict:
    body = {
        "strategy_source": source,
        "environment": {"start": args.start, "end": args.end, "universe_id": "krx.common-stock"},
        "core": "rust",
    }
    started = time.perf_counter()
    response = client.post("/api/v1/backtests", json=body)
    if response.status_code != 202:
        raise SystemExit(f"run 접수 거절 — status={response.status_code} body={response.text}")
    run_id = response.json()["run"]["run_id"]

    def finished() -> dict | None:
        state = client.get(f"/api/v1/backtests/{run_id}").json()
        state = state.get("run", state)
        return state if state["status"] in TERMINAL else None

    state = _wait(finished, f"run {run_id}")
    elapsed = time.perf_counter() - started
    reserve, sessions = records.dispatched[run_id]
    max_rss, read_sessions, fields, factors, rows = _wait(
        lambda: records.committed.get(run_id), f"run {run_id} 의 worker 보고"
    )
    return {
        "status": state["status"],
        "elapsed_s": round(elapsed, 1),
        "estimated_sessions": sessions,
        "reserve_gb": round(reserve / _GB, 3),
        "max_rss_gb": None if max_rss is None else round(max_rss / _GB, 3),
        "reserve_over_max_rss": None if not max_rss else round(reserve / max_rss, 3),
        "read_sessions": read_sessions,
        "fields": fields,
        "factors": factors,
        "observation_rows": rows,
    }


def _experiment(
    tmp: Path, args: argparse.Namespace, reserve_gb: float | None, records: _Records
) -> dict:
    strategy = Path(args.strategy).read_text(encoding="utf-8")
    with TestClient(_app(tmp, args, reserve_gb)) as client:
        saved = client.post(
            "/api/v1/strategy-documents", json={"format": "yaml", "source": strategy}
        )
        if saved.status_code not in (200, 201):
            raise SystemExit(f"전략 저장 실패 — status={saved.status_code} body={saved.text}")
        revision = saved.json()
        request = {
            "run": {
                "strategy_source": {
                    "kind": "saved_revision",
                    "strategy_id": revision["strategy_id"],
                    "revision": revision["revision"],
                    "expected_spec_hash": revision["spec_hash"],
                },
                "core": "rust",
                "environment": {
                    "start": args.experiment_start,
                    "end": args.experiment_end,
                    "universe_id": "krx.common-stock",
                },
            },
            "search": {},
            "split": {"mode": "rolling", "train_years": 1, "test_years": 1, "embargo_sessions": 0},
        }
        started = time.perf_counter()
        created = client.post("/api/v1/experiments", json=request)
        if created.status_code != 202:
            raise SystemExit(f"실험 접수 거절 — status={created.status_code} body={created.text}")
        experiment_id = created.json()["record"]["experiment_id"]

        def finished() -> dict | None:
            experiment = client.get(f"/api/v1/experiments/{experiment_id}").json()
            return experiment if experiment["status"] in ("completed", "cancelled") else None

        experiment = _wait(finished, f"실험 {experiment_id}")
        elapsed = time.perf_counter() - started
        trials = client.get(f"/api/v1/experiments/{experiment_id}/trials").json()
    return {
        "reserve_gb": reserve_gb,
        "status": experiment["status"],
        "elapsed_s": round(elapsed, 1),
        "trials": len(trials) if isinstance(trials, list) else trials,
        "memory_waits": len(records.waited),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="run 메모리 예약치 대 최대 RSS, 가드 전후 실험 시간"
    )
    parser.add_argument("strategy", help="YAML 전략 파일")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--equity-root", default="~/quant-ledger/data/equity")
    parser.add_argument("--experiment-start")
    parser.add_argument("--experiment-end")
    parser.add_argument(
        "--warm-up-history", type=int, default=272, help="가용 메모리 확인용 워밍업 어림"
    )
    args = parser.parse_args()
    for value in (args.start, args.experiment_start):
        if value is not None and date.fromisoformat(value) < RESEARCH_START:
            raise SystemExit(f"구간 시작은 {RESEARCH_START} 이후여야 한다(홀드아웃) — got={value}")

    records = _Records()
    logger = logging.getLogger("strategy_workbench")
    logger.setLevel(logging.INFO)
    logger.addHandler(records)
    tmp = Path(tempfile.mkdtemp(prefix="bench-run-memory-"))
    report: dict[str, Any] = {}
    try:
        _require_memory(args.start, args.end, args.warm_up_history)
        with TestClient(_app(tmp / "single", args, None)) as client:
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
            report["single_run"] = _single_run(client, source, args, records)
        if args.experiment_start and args.experiment_end:
            report["experiment"] = []
            for name, reserve_gb in (("guard", None), ("guard_off", _GUARD_OFF_GB)):
                # 실험 run 은 1년 학습·검증이다. 한 번에 하나만 돈다.
                _require_memory(args.start, args.end, args.warm_up_history)
                records.waited.clear()
                result = _experiment(tmp / name, args, reserve_gb, records)
                report["experiment"].append({"mode": name, **result})
    finally:
        logger.removeHandler(records)
        shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
