"""DB 새로고침 API: 받는 동안 계산 시작을 막고, 끝나면 컨테이너를 통째로 새로 세워 바꿔 끼운다."""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from strategy_workbench.application.ledger_refresh.facade.refresh import (
    LedgerRefreshService,
    SyncLock,
    SyncOutcome,
    SyncPlan,
    SyncProgress,
    SyncStage,
)
from strategy_workbench.bootstrap._reload import ContainerActiveWork, ReloadableApp
from strategy_workbench.bootstrap.facade.http import build_http_app

PATH = "/api/v1/ledger-refresh"
# 계산을 시작하는 라우트 — 저마다 `STARTS_WORK` 를 단다.
COMPUTE_STARTS = (
    "/api/v1/backtests",
    "/api/v1/backtests/run-1/robustness-check",
    "/api/v1/experiments",
    "/api/v1/experiments/capacity",
    "/api/v1/experiments/exp-1/trials/0/retry",
    "/api/v1/experiments/exp-1/continuous-account",
)


def test_practice_data_hides_the_refresh_and_rejects_a_start() -> None:
    client = TestClient(build_http_app())
    status = client.get(PATH).json()
    assert (status["code"], status["reason"], status["can_start"]) == (
        "unavailable",
        "practice_data",
        False,
    )
    rejected = client.post(PATH)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "ledger_refresh.rejected"


@dataclass
class HeldJob:
    """`release` 가 서기 전까지 끝나지 않는 sync."""

    release: threading.Event

    def progress(self) -> SyncProgress | None:
        return SyncProgress(SyncStage.DOWNLOAD, datetime.now(UTC), 2, 1, 100, 50)

    def wait(self, timeout_s: float) -> SyncOutcome | None:
        if not self.release.wait(timeout_s):
            return None
        return SyncOutcome(None, 0, True, False, False, False)

    def cancel(self) -> None:
        self.release.set()


@dataclass
class FakeSync:
    release: threading.Event = field(default_factory=threading.Event)

    def lock(self) -> SyncLock | None:
        return None

    def plan(self) -> SyncPlan:
        return SyncPlan(new_tables=2, download_bytes=100, in_build_window=False)

    def start(self) -> HeldJob:
        return HeldJob(self.release)


def test_a_refresh_blocks_new_runs_then_swaps_in_a_new_container() -> None:
    sync = FakeSync()
    holder = ReloadableApp()
    refresh = LedgerRefreshService(sync, ContainerActiveWork(lambda: holder.container), holder)
    holder.load(
        partial(
            build_http_app,
            run_slots=2,
            run_isolation="thread",
            ledger_refresh=refresh,
        )
    )
    with TestClient(holder) as client:
        first = holder.container
        assert client.post(f"{PATH}/check").json()["remote"]["state"] == "behind"
        started = client.post(PATH)
        assert started.status_code == 202, started.text
        assert started.json()["code"] == "running"
        # 받는 동안 계산을 시작하는 요청은 본문을 읽기 전에 막는다
        for path in COMPUTE_STARTS:
            blocked = client.post(path, json={})
            assert blocked.status_code == 409, path
            assert blocked.json()["detail"]["code"] == "data.refreshing", path
        # 미리 계산은 막지 않는다(본문 오류로 끝난다)
        assert client.post("/api/v1/experiments/preview", json={}).status_code == 422
        assert client.get("/api/v1/market/overview").status_code == 200  # 조회는 돈다
        sync.release.set()
        deadline = time.monotonic() + 30
        status = client.get(PATH).json()
        while status["code"] in ("running", "applying") and time.monotonic() < deadline:
            time.sleep(0.05)
            status = client.get(PATH).json()
        assert status["code"] == "succeeded", status
        assert status["remote"]["state"] == "up_to_date"
        assert holder.container is not first
        # 새 앱이 요청을 받는다 — 막힘도 풀렸다
        assert client.post("/api/v1/backtests", json={}).status_code != 409


def test_shutting_down_after_a_reload_closes_the_app_in_use() -> None:
    events: list[str] = []

    def build() -> FastAPI:
        number = sum(event.startswith("open") for event in events) + 1

        @asynccontextmanager
        async def lifespan(_: FastAPI) -> AsyncIterator[None]:
            events.append(f"open{number}")
            yield
            events.append(f"close{number}")

        return FastAPI(lifespan=lifespan)

    holder = ReloadableApp().load(build, on_shutdown=lambda: events.append("stop"))
    with TestClient(holder):
        holder.apply()
        assert events == ["open1", "open2", "close1"]
    # 서버가 끝나면 받는 중인 도구를 끊고(반영을 넘어 사는 정리), 바꿔 끼운 앱(run worker 정리
    # 등)을 닫는다
    assert events == ["open1", "open2", "close1", "stop", "close2"]
    with pytest.raises(RuntimeError):  # 끝난 서버는 다시 세우지 않는다
        holder.apply()
