"""엔진 확장 올리기(`BacktestEngineExecutorAdapter.load_kernel`)의 실패 문장."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from strategy_workbench.adapters.outbound.backtest_engine import _adapter
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)


def _missing(name: str) -> Callable[[str], object]:
    def import_module(_module: str) -> object:
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    return import_module


def test_a_missing_extension_says_how_to_build_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # 빌드하지 않은 checkout 은 패키지 안 확장 모듈이 없다(#591 2차 리뷰 nit).
    missing = _missing("backtest_core.backtest_core")
    monkeypatch.setattr(_adapter.importlib, "import_module", missing)

    with pytest.raises(RuntimeError, match="maturin develop") as raised:
        BacktestEngineExecutorAdapter().load_kernel()
    assert "missing=backtest_core.backtest_core" in str(raised.value)


def test_another_missing_module_is_not_relabelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_adapter.importlib, "import_module", _missing("numpy"))

    with pytest.raises(ModuleNotFoundError):
        BacktestEngineExecutorAdapter().load_kernel()
