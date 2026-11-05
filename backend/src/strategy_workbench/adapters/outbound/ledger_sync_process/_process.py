"""`LedgerSyncPort` 구현 — 원장 동기화 CLI(`python -m ledger_sync`)를 subprocess 로 부른다.

명령은 래퍼(`database/scripts/ledger_sync.ps1`·`.sh`)와 같은 인터프리터 구성이다 — backend
프로젝트 환경(duckdb·pyarrow)에 paramiko 만 얹고 `PYTHONPATH=database/src`, `PYTHONUTF8=1`. 다만
앱이 쓰고 있는 환경을 바꾸지 않도록 `--no-sync` 를 준다(`uv run` 의 동기화는 개발용으로 설치한 Rust
코어를 걷어낼 수 있고, Windows 에서는 앱이 연 파일 때문에 중간에 실패한다). 받을 루트는 앱이 읽는
equity 루트의 부모로 명시한다 — 앱이 읽지 않는 곳을 받고 "완료"라고 말하지 않게.

기계용 출력만 읽는다: `plan --json`, `sync --progress-file`, `<equity>/_sync/last_run.json`·`lock`.
사람용 로그·stderr 문장은 해석하지 않는다(서버 주소·계정이 실릴 수 있어 화면에도 보내지 않는다).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from strategy_workbench.application.ledger_refresh.facade.refresh import (
    SyncFailureKind,
    SyncLock,
    SyncOutcome,
    SyncPlan,
    SyncProgress,
    SyncStage,
)

logger = logging.getLogger(__name__)

# `database/src/ledger_sync/__main__.py` 의 `LOCK_STALE_S`·`LOCK_NAME`·`LAST_RUN_NAME`·`EXIT_*`,
# `layout.SYNC_DIR` 와 같은 값이다(backend 는 그 패키지를 import 하지 않는다 — 테스트가 그 모듈의
# 값과 대조한다). 종료 코드는 이 어댑터만 해석하고 포트에는 뜻(`SyncOutcome`)만 넘긴다.
LOCK_STALE_S = 3 * 3600
SYNC_DIR = "_sync"
LOCK_NAME = "lock"
LAST_RUN_NAME = "last_run.json"
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_DRIFTED = 3
EXIT_MISMATCH = 4
PLAN_TIMEOUT_S = 120
PARAMIKO = "paramiko>=3.4,<4"
# 표마다 남길 판본 수. 도구 기본값(2)보다 하나 더 남긴다 — 반영하지 못한 받기(일부만 받음) 뒤 다시
# 받을 때, 옛 앱이 부팅 때 고정한 판을 gc 가 지우지 않게. 반영하지 못한 받기가 두 번 이어지면 지울
# 수 있다(알려진 한계).
KEEP_BUILDS = 3
# last_run.json 이 이번 실행의 것인가 — 시작 시각보다 이 이상 앞이면 지난 실행의 기록이다.
_CLOCK_SLACK = timedelta(seconds=5)

Runner = Callable[..., subprocess.CompletedProcess[str]]
Spawner = Callable[..., subprocess.Popen[bytes]]


def ledger_sync_command(uv: str, repo_root: Path, equity_root: Path, *arguments: str) -> list[str]:
    """앱이 읽는 equity 루트를 받는 `ledger_sync` 명령. 전역 옵션 뒤에 `arguments`(동사 등)."""
    return [
        uv, "run", "--project", str(repo_root / "backend"), "--no-sync", "--with", PARAMIKO,
        "python", "-m", "ledger_sync",
        "--root", str(equity_root.parent), "--layer", equity_root.name,
        *arguments,
    ]  # fmt: skip


def _environment(repo_root: Path, base: Mapping[str, str]) -> dict[str, str]:
    environment = dict(base)
    environment["PYTHONPATH"] = str(repo_root / "database" / "src")
    environment["PYTHONUTF8"] = "1"
    return environment


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _failure(value: object) -> SyncFailureKind | None:
    if value is None:
        return None
    try:
        return SyncFailureKind(str(value))
    except ValueError:
        return SyncFailureKind.UNEXPECTED


def outcome_from_last_run(payload: Mapping[str, object] | None, return_code: int) -> SyncOutcome:
    """`last_run.json` 을 결과 값으로 옮긴다. 기록이 없거나 이번 실행의 것이 아니면 예상 밖 실패다.

    기록이 없으면(도구가 뜨기도 전에 실패) 카탈로그를 다시 만들었다는 근거가 없다. 모르는 종료
    코드도 예상 밖 실패로 접는다.
    """
    if payload is None:
        return SyncOutcome(SyncFailureKind.UNEXPECTED, 0, False, False, False, False)
    pull = payload.get("pull")
    results = pull.get("results") if isinstance(pull, dict) else None
    rows = [row for row in results if isinstance(row, dict)] if isinstance(results, list) else []
    recorded = _int(payload.get("exit_code"))
    exit_code = return_code if recorded is None else recorded
    failure = _failure(payload.get("error_kind"))
    if failure is None and exit_code not in (EXIT_OK, EXIT_ERROR, EXIT_DRIFTED, EXIT_MISMATCH):
        failure = SyncFailureKind.UNEXPECTED
    catalog_rc = _int(payload.get("catalog_rc"))
    verify = payload.get("verify")
    return SyncOutcome(
        failure=failure,
        tables_failed=sum(row.get("outcome") in ("failed", "error") for row in rows),
        catalog_rebuilt=catalog_rc == EXIT_OK,
        catalog_failed=catalog_rc not in (None, EXIT_OK),
        verify_mismatch=exit_code == EXIT_MISMATCH
        or (isinstance(verify, dict) and verify.get("ok") is False),
        drifted=isinstance(pull, dict) and bool(pull.get("drifted")),
    )


class _ProcessJob:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        progress_path: Path,
        last_run: Path,
        started: datetime,
    ) -> None:
        self._process = process
        self._progress_path = progress_path
        self._last_run = last_run
        self._started = started

    def progress(self) -> SyncProgress | None:
        document = _read_json(self._progress_path)
        if document is None:
            return None
        try:
            stage = SyncStage(str(document.get("stage")))
        except ValueError:
            return None
        return SyncProgress(
            stage=stage,
            stage_started_at=_parse_time(document.get("stage_started_at_utc")),
            tables_total=_int(document.get("tables_total")),
            tables_done=_int(document.get("tables_done")) or 0,
            bytes_total=_int(document.get("bytes_total")),
            bytes_done=_int(document.get("bytes_done")) or 0,
        )

    def wait(self, timeout_s: float) -> SyncOutcome | None:
        try:
            return_code = self._process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None
        payload = _read_json(self._last_run)
        started = _parse_time(payload.get("started_at_utc")) if payload else None
        if started is None or started < self._started - _CLOCK_SLACK:
            logger.warning(
                "ledger sync left no record of this run — return_code=%s last_run=%s",
                return_code,
                self._last_run,
            )
            payload = None
        try:
            self._progress_path.unlink(missing_ok=True)
        except OSError:
            pass  # 다음 실행이 같은 이름을 쓰지 않는다
        return outcome_from_last_run(payload, return_code)

    def cancel(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()


class LedgerSyncProcess:
    """`repo_root`(이 저장소)의 동기화 도구로 `equity_root`(앱이 읽는 루트)를 받는다."""

    def __init__(
        self,
        uv: str,
        repo_root: Path,
        equity_root: Path,
        *,
        environ: Mapping[str, str] = os.environ,
        run: Runner = subprocess.run,
        spawn: Spawner = subprocess.Popen,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._uv = uv
        self._repo_root = repo_root
        self._equity_root = equity_root
        self._environ = environ
        self._run = run
        self._spawn = spawn
        self._now = now

    def _command(self, *arguments: str) -> list[str]:
        return ledger_sync_command(self._uv, self._repo_root, self._equity_root, *arguments)

    def _sync_dir(self) -> Path:
        return self._equity_root / SYNC_DIR

    def lock(self) -> SyncLock | None:
        path = self._sync_dir() / LOCK_NAME
        try:
            age_s = max(0.0, time.time() - path.stat().st_mtime)
        except FileNotFoundError:
            return None
        except OSError:
            return SyncLock(None, True)
        document = _read_json(path)
        if document is None:  # 도구의 `lock_info` 처럼 깨진 잠금은 낡은 것으로 본다
            return SyncLock(None, True)
        return SyncLock(_parse_time(document.get("started_at_utc")), age_s > LOCK_STALE_S)

    def plan(self) -> SyncPlan:
        try:
            completed = self._run(
                self._command("--json", "plan"),
                cwd=self._repo_root,
                env=_environment(self._repo_root, self._environ),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=PLAN_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            logger.warning("ledger sync plan did not finish — error=%r", error)
            return SyncPlan(0, 0, False, failure=SyncFailureKind.CONNECT)
        try:
            document = json.loads(completed.stdout)
        except ValueError:
            document = None
        if not isinstance(document, dict):
            # 접속 실패·설정 오류는 JSON 없이 끝난다. 원문은 로그에만(주소가 실릴 수 있다).
            logger.warning(
                "ledger sync plan failed — return_code=%s stderr=%s",
                completed.returncode,
                completed.stderr[-500:],
            )
            return SyncPlan(0, 0, False, failure=SyncFailureKind.CONNECT)
        tables = document.get("tables")
        rows: Sequence[object] = tables if isinstance(tables, list) else []
        return SyncPlan(
            new_tables=sum(
                isinstance(row, dict) and row.get("action") == "new_build" for row in rows
            ),
            download_bytes=_int(document.get("download_bytes")) or 0,
            in_build_window=document.get("in_build_window") is True,
        )

    def start(self) -> _ProcessJob:
        self._sync_dir().mkdir(parents=True, exist_ok=True)
        started = self._now()
        progress_path = (
            self._sync_dir() / f"app_progress_{os.getpid()}_{started:%Y%m%dT%H%M%S}.json"
        )
        process = self._spawn(
            self._command(
                "sync", "--keep", str(KEEP_BUILDS), "--progress-file", str(progress_path)
            ),
            cwd=self._repo_root,
            env=_environment(self._repo_root, self._environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("ledger sync started — pid=%s root=%s", process.pid, self._equity_root)
        return _ProcessJob(process, progress_path, self._sync_dir() / LAST_RUN_NAME, started)
