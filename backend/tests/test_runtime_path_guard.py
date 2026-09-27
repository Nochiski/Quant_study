"""테스트가 개발자 로컬 런타임 상태 경로를 열지 않는다는 가드와 격리 fixture를 고정한다(#211)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from strategy_workbench.bootstrap import _assistant, _container, _http

from .conftest import RUNTIME_PATH_GUARD, RUNTIME_SETTING_ENVS
from .runtime_path_guard import RuntimePathGuard


def test_guard_records_path_events_under_the_watched_roots_only(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    guard = RuntimePathGuard((watched,))

    guard("open", (str(watched / "assistant.sqlite3"), "r", 0))
    guard("sqlite3.connect", (watched / "strategy.sqlite3",))
    guard("os.mkdir", (os.fsencode(watched / "backtest-runs"), 0o777, None))
    guard("os.rename", (str(tmp_path / "staging"), str(watched / "run"), None, None))
    guard("os.link", (str(tmp_path / "source"), str(watched / "hard-link"), None, None, True))
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
        "os.rename",
        "os.link",
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


def _missing_probe() -> Path:
    # 감시 root 아래 없는 하위 디렉터리다. 감사 이벤트는 시스템 호출 전에 나고, 호출은 실패하므로
    # 디스크에는 아무것도 생기지 않는다.
    return Path(RUNTIME_PATH_GUARD.roots[0]) / "f211-guard-probe-missing-dir" / "probe"


@pytest.mark.parametrize(
    ("event", "operation"),
    [
        ("open", lambda probe: open(probe, encoding="utf-8")),
        ("os.truncate", lambda probe: os.truncate(probe, 0)),
        ("os.utime", lambda probe: os.utime(probe)),
        ("os.chmod", lambda probe: os.chmod(probe, 0o600)),
        ("os.link", lambda probe: os.link(probe, probe.with_name("link"))),
        ("os.symlink", lambda probe: os.symlink(probe, probe.with_name("symlink"))),
        ("os.rename", lambda probe: os.replace(probe, probe.with_name("moved"))),
    ],
)
def test_session_guard_sees_real_operations_under_the_default_runtime_directory(
    event: str, operation: Callable[[Path], object]
) -> None:
    with pytest.raises(OSError):
        operation(_missing_probe())

    touched = RUNTIME_PATH_GUARD.drain()  # 이 테스트의 teardown 가드가 실패하지 않게 비운다
    assert any(entry.startswith(f"{event} ") for entry in touched), touched


def test_runtime_defaults_point_under_the_per_test_tmp_directory(
    isolate_runtime_state_paths: Path,
) -> None:
    root = isolate_runtime_state_paths

    assert _http.runtime_strategy_repository_path().parent == root
    assert Path(_http.runtime_assistant_settings().db_path or "").parent == root
    assert _container.DEFAULT_RUN_ARTIFACT_ROOT.parent == root
    assert _assistant.default_secrets_path().parent == root
    for name in RUNTIME_SETTING_ENVS:
        assert name not in os.environ


def test_isolation_clears_every_runtime_setting_the_composition_root_reads() -> None:
    # `_http.py`의 `*_ENV` 상수에서 유도한 목록이 실제로 읽는 변수를 모두 담는지 고정한다.
    # 개발자 셸의 `EQUITY_ADAPTER=duckdb`·가짜 공급자 값이 `build_runtime_http_app` 테스트로
    # 새던 결함이다
    # (#215 리뷰 P3-2).
    assert set(RUNTIME_SETTING_ENVS) >= {
        _http.STRATEGY_REPOSITORY_PATH_ENV,
        _http.EQUITY_ADAPTER_ENV,
        _http.EQUITY_ROOT_ENV,
        _http.ALLOWED_ORIGINS_ENV,
        _http.ASSISTANT_DB_PATH_ENV,
        _http.ASSISTANT_SECRETS_PATH_ENV,
        _http.ASSISTANT_ALLOW_INSECURE_BASE_URL_ENV,
        _http.ASSISTANT_FAKE_PROVIDER_ENV,
    }
    assert all(name.startswith("STRATEGY_WORKBENCH_") for name in RUNTIME_SETTING_ENVS)
    assert _http.runtime_equity_selection() == ("mock", None)
