"""Rust 코어가 필요한 테스트의 공용 판정.

확장이 없으면 건너뛰고, 현재 소스보다 오래됐으면 실패시킨다. 낡은 코어 때문에 Rust 테스트가
조용히 건너뛰어져 통과처럼 보이면 안 된다(사유에 판정 상태를 함께 싣는다).
"""

from __future__ import annotations

import pytest

from backtest_engine.engine.core_source import BUILD_COMMAND, process_core_status

# 테스트 함수·클래스·pytest.param에 붙이는 표식. 판정은 conftest의 setup 훅이 한다.
RUST_CORE = pytest.mark.rust_core


def require_rust_core() -> None:
    """테스트 본문에서 Rust 코어가 필요할 때 부른다."""
    status = process_core_status()
    if status == "missing":
        pytest.skip(f"Rust 코어 확장 없음(core_status={status})")
    if status in ("stale", "broken"):
        pytest.fail(
            f"Rust 코어가 낡았거나 불러올 수 없음(core_status={status}) — 다시 빌드: "
            f"`cd backend && {BUILD_COMMAND}`",
            pytrace=False,
        )
