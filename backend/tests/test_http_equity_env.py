"""서버 기동 시 equity 어댑터 선택이 환경변수로 전달되는지 (candlefish 요청)."""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_workbench.bootstrap._http import (
    EQUITY_ADAPTER_ENV,
    EQUITY_ROOT_ENV,
    runtime_equity_selection,
)


def test_기본값은_mock_이고_루트는_없다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EQUITY_ADAPTER_ENV, raising=False)
    monkeypatch.delenv(EQUITY_ROOT_ENV, raising=False)
    assert runtime_equity_selection() == ("mock", None)


def test_환경변수가_duckdb_와_루트를_전달한다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(EQUITY_ADAPTER_ENV, "duckdb")
    monkeypatch.setenv(EQUITY_ROOT_ENV, str(tmp_path))
    assert runtime_equity_selection() == ("duckdb", tmp_path)


def test_빈_문자열은_mock_으로_떨어진다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EQUITY_ADAPTER_ENV, "   ")
    monkeypatch.delenv(EQUITY_ROOT_ENV, raising=False)
    assert runtime_equity_selection()[0] == "mock"


def test_duckdb_인데_루트가_없으면_기동이_실패한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from strategy_workbench.bootstrap._http import build_http_app

    monkeypatch.setenv(EQUITY_ADAPTER_ENV, "duckdb")
    monkeypatch.delenv(EQUITY_ROOT_ENV, raising=False)
    with pytest.raises(ValueError, match="equity_root"):
        build_http_app(equity_adapter="duckdb", equity_root=None)
