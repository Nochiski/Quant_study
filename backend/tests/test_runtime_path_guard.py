"""테스트가 개발자 로컬 런타임 상태 경로를 열지 않는다는 가드와 격리 fixture를 고정한다(#211)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from strategy_workbench.bootstrap import _assistant, _container, _http

from .conftest import RUNTIME_PATH_GUARD
from .runtime_path_guard import RuntimePathGuard


def test_guard_records_path_events_under_the_watched_roots_only(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    guard = RuntimePathGuard((watched,))

    guard("open", (str(watched / "assistant.sqlite3"), "r", 0))
    guard("sqlite3.connect", (watched / "strategy.sqlite3",))
    guard("os.mkdir", (os.fsencode(watched / "backtest-runs"), 0o777, None))
    guard("os.replace", (str(tmp_path / "staging"), str(watched / "run"), None, None))
    guard("os.mkdir", (str(watched), 0o777, None))
    # 감시 밖, 파일 디스크립터, in-memory DB, 관심 없는 이벤트는 기록하지 않는다.
    guard("open", (str(tmp_path / "watched-sibling" / "x"), "r", 0))
    guard("open", (3, "r", 0))
    guard("sqlite3.connect", (":memory:",))
    guard("sqlite3.connect", ("file::memory:?cache=shared",))
    guard("import", ("json", None, None, None, None))

    touched = guard.drain()
    assert [event.split(" ", 1)[0] for event in touched] == [
        "open",
        "sqlite3.connect",
        "os.mkdir",
        "os.replace",
        "os.mkdir",
    ]
    assert guard.drain() == []


def test_guard_never_raises_into_the_audited_operation(tmp_path: Path) -> None:
    guard = RuntimePathGuard((tmp_path,))

    class _Unprintable(os.PathLike[str]):
        def __fspath__(self) -> str:
            raise RuntimeError("boom")

    guard("open", (_Unprintable(), "r", 0))

    assert guard.drain() == []


def test_session_guard_sees_a_real_open_under_the_default_runtime_directory() -> None:
    # 없는 파일을 읽기로 열면 감사 이벤트만 나고 디스크에는 아무것도 생기지 않는다.
    missing = Path(RUNTIME_PATH_GUARD.roots[0]) / "f211-guard-probe-missing.sqlite3"
    with pytest.raises(OSError):
        open(missing, encoding="utf-8")  # 여는 시도 자체가 검사 대상이다

    touched = RUNTIME_PATH_GUARD.drain()  # 이 테스트의 teardown 가드가 실패하지 않게 비운다
    assert any(event.startswith("open ") for event in touched), touched


def test_runtime_defaults_point_under_the_per_test_tmp_directory(
    isolate_runtime_state_paths: Path,
) -> None:
    root = isolate_runtime_state_paths

    assert _http.runtime_strategy_repository_path().parent == root
    assert Path(_http.runtime_assistant_settings().db_path or "").parent == root
    assert _container.DEFAULT_RUN_ARTIFACT_ROOT.parent == root
    assert _assistant.default_secrets_path().parent == root
    for name in (
        _http.STRATEGY_REPOSITORY_PATH_ENV,
        _http.ASSISTANT_DB_PATH_ENV,
        _http.ASSISTANT_SECRETS_PATH_ENV,
    ):
        assert name not in os.environ
