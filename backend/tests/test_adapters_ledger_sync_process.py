"""`ledger_sync` subprocess 어댑터: 기계용 출력만 읽고, 기록이 없으면 반영하지 않는 쪽으로."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.ledger_sync_process import _process
from strategy_workbench.adapters.outbound.ledger_sync_process._process import (
    LAST_RUN_NAME,
    LOCK_NAME,
    LOCK_STALE_S,
    SYNC_DIR,
    LedgerSyncProcess,
    ledger_sync_command,
    outcome_from_last_run,
)
from strategy_workbench.application.ledger_refresh.facade.refresh import (
    SyncFailureKind,
    SyncStage,
)
from tests.equity_fixture import import_ledger_module

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "name",
    [
        "LOCK_STALE_S",
        "LOCK_NAME",
        "LAST_RUN_NAME",
        "SYNC_DIR",
        "EXIT_OK",
        "EXIT_ERROR",
        "EXIT_DRIFTED",
        "EXIT_MISMATCH",
    ],
)
def test_constants_match_the_ledger_sync_module(name: str) -> None:
    assert getattr(_process, name) == getattr(import_ledger_module("ledger_sync.__main__"), name)


def test_the_paramiko_pin_matches_the_wrappers() -> None:
    for wrapper in ("ledger_sync.ps1", "ledger_sync.sh"):
        text = (REPO / "database" / "scripts" / wrapper).read_text(encoding="utf-8")
        assert _process.PARAMIKO in text, wrapper


def test_the_command_targets_the_app_root_without_resyncing_the_app_environment() -> None:
    command = ledger_sync_command("uv", REPO, Path("/data/equity"), "--json", "plan")
    assert command[:4] == ["uv", "run", "--project", str(REPO / "backend")]
    assert "--no-sync" in command
    root = command.index("--root")
    assert command[root + 1 : root + 4] == [str(Path("/data")), "--layer", "equity"]
    assert command[-2:] == ["--json", "plan"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # 표를 받기 전에 접속 실패 — 카탈로그를 다시 만들지 않았다
        (
            {"exit_code": 2, "error": "x", "error_kind": "connect"},
            (SyncFailureKind.CONNECT, 0, False, False, False, False),
        ),
        (
            {
                "exit_code": 0,
                "pull": {"results": [{"outcome": "done"}], "drifted": []},
                "catalog_rc": 0,
                "verify": {"ok": True},
            },
            (None, 0, True, False, False, False),
        ),
        # 받을 새 판이 없어도 카탈로그는 다시 만든다
        (
            {
                "exit_code": 0,
                "pull": {"results": [{"outcome": "unchanged"}]},
                "catalog_rc": 0,
                "verify": {"ok": True},
            },
            (None, 0, True, False, False, False),
        ),
        (
            {"exit_code": 2, "pull": {"results": [{"outcome": "done"}, {"outcome": "failed"}]}},
            (None, 1, False, False, False, False),
        ),
        (
            {"exit_code": 2, "pull": {"results": [{"outcome": "done"}]}, "catalog_rc": 2},
            (None, 0, False, True, False, False),
        ),
        (
            {
                "exit_code": 4,
                "pull": {"results": [{"outcome": "done"}]},
                "catalog_rc": 0,
                "verify": {"ok": False},
            },
            (None, 0, True, False, True, False),
        ),
        # drifted 는 확인하기의 차이보다 먼저 남는 종료 코드다
        (
            {
                "exit_code": 3,
                "pull": {"results": [{"outcome": "done"}], "drifted": ["price"]},
                "catalog_rc": 0,
                "verify": {"ok": False},
            },
            (None, 0, True, False, True, True),
        ),
        # 모르는 실패 갈래·종료 코드는 예상 밖으로 접는다
        (
            {"exit_code": 2, "error_kind": "weird"},
            (SyncFailureKind.UNEXPECTED, 0, False, False, False, False),
        ),
        ({"exit_code": 9}, (SyncFailureKind.UNEXPECTED, 0, False, False, False, False)),
    ],
)
def test_last_run_is_read_into_an_outcome(payload: dict[str, object], expected: tuple) -> None:
    outcome = outcome_from_last_run(payload, 0)
    assert (
        outcome.failure, outcome.tables_failed, outcome.catalog_rebuilt, outcome.catalog_failed,
        outcome.verify_mismatch, outcome.drifted,
    ) == expected  # fmt: skip


def test_a_missing_record_is_an_unexpected_failure_that_rebuilt_nothing() -> None:
    outcome = outcome_from_last_run(None, 1)
    assert (outcome.failure, outcome.catalog_rebuilt) == (SyncFailureKind.UNEXPECTED, False)


def _adapter(tmp_path: Path, **kwargs: object) -> LedgerSyncProcess:
    root = tmp_path / "data" / "equity"
    root.mkdir(parents=True, exist_ok=True)
    return LedgerSyncProcess("uv", REPO, root, environ={}, **kwargs)  # pyright: ignore[reportArgumentType]  # reason: 테스트 대역 키워드


def test_lock_reports_the_holder_and_staleness(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    assert adapter.lock() is None
    lock = tmp_path / "data" / "equity" / SYNC_DIR / LOCK_NAME
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"pid": 1, "started_at_utc": NOW.isoformat()}), encoding="utf-8")
    held = adapter.lock()
    assert held is not None and (held.started_at, held.stale) == (NOW, False)
    old = time.time() - LOCK_STALE_S - 60
    os.utime(lock, (old, old))
    held = adapter.lock()
    assert held is not None and held.stale
    # 깨진 잠금은 도구(`lock_info`)처럼 낡은 것으로 본다
    lock.write_text("{", encoding="utf-8")
    held = adapter.lock()
    assert held is not None and held.stale


def test_plan_counts_new_builds_and_reads_the_build_window(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        stdout = json.dumps(
            {
                "download_bytes": 1234,
                "in_build_window": True,
                "tables": [
                    {"action": "new_build"},
                    {"action": "up_to_date"},
                    {"action": "new_build"},
                ],
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    plan = _adapter(tmp_path, run=run).plan()
    assert (plan.new_tables, plan.download_bytes, plan.in_build_window, plan.failure) == (
        2,
        1234,
        True,
        None,
    )
    assert calls[0][-2:] == ["--json", "plan"]

    def refuse(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, "", "error: server host is not set")

    assert _adapter(tmp_path, run=refuse).plan().failure is SyncFailureKind.CONNECT


_FAKE_SYNC = """
import json, sys, time
from datetime import datetime, timezone
progress, last_run = sys.argv[1], sys.argv[2]
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
open(progress, "w", encoding="utf-8").write(json.dumps({
    "stage": "catalog", "stage_started_at_utc": now, "tables_total": 3, "tables_done": 3,
    "bytes_total": 10, "bytes_done": 10}))
time.sleep(0.5)
open(last_run, "w", encoding="utf-8").write(json.dumps({
    "started_at_utc": now, "exit_code": 0, "pull": {"results": [{"outcome": "done"}]},
    "catalog_rc": 0, "verify": {"ok": True}}))
"""


def test_a_started_sync_reports_progress_then_the_recorded_outcome(tmp_path: Path) -> None:
    sync_dir = tmp_path / "data" / "equity" / SYNC_DIR

    def spawn(command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        progress = command[command.index("--progress-file") + 1]
        return subprocess.Popen(  # 진짜 프로세스 — 진행 파일과 기록을 남기고 끝난다
            [sys.executable, "-c", _FAKE_SYNC, progress, str(sync_dir / LAST_RUN_NAME)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )

    job = _adapter(
        tmp_path, spawn=spawn, now=lambda: datetime.now(UTC) - timedelta(seconds=1)
    ).start()
    deadline = time.monotonic() + 10
    while job.progress() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    progress = job.progress()
    assert progress is not None and (progress.stage, progress.tables_done) == (SyncStage.CATALOG, 3)
    outcome = None
    while outcome is None and time.monotonic() < deadline:
        outcome = job.wait(0.2)
    assert outcome is not None and (outcome.failure, outcome.catalog_rebuilt) == (None, True)
    assert not list(sync_dir.glob("app_progress_*.json"))  # 진행 파일을 치운다


def test_a_stale_record_from_an_earlier_run_is_not_trusted(tmp_path: Path) -> None:
    sync_dir = tmp_path / "data" / "equity" / SYNC_DIR
    sync_dir.mkdir(parents=True)
    (sync_dir / LAST_RUN_NAME).write_text(
        json.dumps({"started_at_utc": (NOW - timedelta(days=3)).isoformat(), "exit_code": 0}),
        encoding="utf-8",
    )

    def spawn(command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        return subprocess.Popen([sys.executable, "-c", "pass"])

    job = _adapter(tmp_path, spawn=spawn, now=lambda: NOW).start()
    outcome = None
    while outcome is None:
        outcome = job.wait(1.0)
    assert (outcome.failure, outcome.catalog_rebuilt) == (SyncFailureKind.UNEXPECTED, False)
