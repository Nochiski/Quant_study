"""duckdb 어댑터의 폴백 랙과 손 픽스처 랙이 원장 `dataset_profile` 선언과 같다 (이슈 #246).

랙의 정본은 원장 `dataset_profile`(S19)이다. 어댑터는 그 표가 없는 루트(옛 루트·부분 동기화
루트)에서 원천 상수(`SourceSpec.lag_sessions`, 필드별 override)로 폴백한다. 폴백이 원장보다 짧으면
그 루트의 백테스트가 공개되기 전 값을 조용히 읽는다(silent look-ahead). 근거 문자열에 `fallback`
이 찍혀도 값은 앞당겨진다. 그래서 폴백도 원장 선언과 같은 값을 낸다.

원장 선언은 원장 빌드가 `dataset_profile` 을 만드는 `database/src/equity` 의 `rules_s*.FIELDS` 를
그대로 읽는다. 실원장 파일 없이 CI 에서 돈다.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from strategy_workbench.adapters.outbound.equity_duckdb._specs import FIELD_BY_ID
from tests.equity_fixture import WB_PROFILE_ROWS, build_workbench_root

pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")

from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (  # noqa: E402
    EquityDuckdbAdapter,
)

_EQUITY_SRC = Path(__file__).resolve().parents[3] / "database" / "src"


def _ledger_lags() -> dict[str, int]:
    """원장 빌드가 `dataset_profile` 로 내는 필드별 권장 랙(세션)."""
    if not _EQUITY_SRC.is_dir():
        pytest.fail(f"원장 선언 경로가 없다 — path={_EQUITY_SRC}")
    added = str(_EQUITY_SRC) not in sys.path
    if added:
        sys.path.insert(0, str(_EQUITY_SRC))
    try:
        rules_s19: Any = importlib.import_module("equity.rules_s19")
        return {
            profile.field_id: profile.recommended_lag_sessions
            for _, profile in rules_s19.owned_fields()
        }
    finally:
        if added:
            sys.path.remove(str(_EQUITY_SRC))


def test_폴백_랙이_원장_선언과_같다(tmp_path: Path) -> None:
    root = build_workbench_root(tmp_path / "equity", profile=False)
    served = {item.field_id: item for item in EquityDuckdbAdapter(root).list_fields()}
    assert set(served) == set(FIELD_BY_ID), "손 픽스처가 선언표의 필드를 다 내지 않는다"
    assert all("fallback" in item.available_date_basis for item in served.values())
    ledger = _ledger_lags()

    drift = sorted(
        (field_id, item.recommended_lag_sessions, ledger[field_id])
        for field_id, item in served.items()
        if item.recommended_lag_sessions != ledger[field_id]
    )

    assert not drift, f"폴백 랙이 원장과 다르다 — (field_id, fallback, ledger)={drift}"


def test_손_픽스처_dataset_profile_랙이_원장_선언과_같다() -> None:
    ledger = _ledger_lags()

    drift = sorted(
        (field_id, lag, ledger[field_id])
        for field_id, lag, _ in WB_PROFILE_ROWS
        if lag != ledger[field_id]
    )

    assert not drift, f"손 픽스처 랙이 원장과 다르다 — (field_id, fixture, ledger)={drift}"


# ── 폴백 사용 경고 ─────────────────────────────────────────────────────────────
# 폴백 값을 원장과 맞춰도 원장 선언이 바뀌면 다시 조용히 어긋날 수 있다. 그래서 폴백 랙을 쓰는
# 필드가 있으면 어댑터가 부팅할 때 한 번 경고한다(카탈로그 경고 #233 과 같은 경로·형식).
FALLBACK_CODE = "profile_lag_fallback"


def _fallback_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records if FALLBACK_CODE in record.getMessage()
    ]


def test_root_without_dataset_profile_warns_once_at_boot(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = build_workbench_root(tmp_path / "equity", profile=False)

    with caplog.at_level("WARNING"):
        adapter = EquityDuckdbAdapter(root)
        adapter.list_fields()
        adapter.list_fields()

    warned = _fallback_warnings(caplog)
    assert len(warned) == 1, warned
    message = warned[0]
    assert "dataset_profile" in message and "폴백" in message
    assert f"fields={len(FIELD_BY_ID)} " in message
    assert "table_present=False" in message
    assert str(root) in message


def test_partial_dataset_profile_warns_with_the_fields_that_fall_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rows = [row for row in WB_PROFILE_ROWS if row[0] != "credit.margin_balance"]
    root = build_workbench_root(tmp_path / "equity", profile_rows=rows)

    with caplog.at_level("WARNING"):
        EquityDuckdbAdapter(root)

    warned = _fallback_warnings(caplog)
    assert len(warned) == 1, warned
    assert "fields=1 " in warned[0]
    assert "credit.margin_balance" in warned[0]
    assert "table_present=True" in warned[0]


def test_complete_dataset_profile_raises_no_fallback_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = build_workbench_root(tmp_path / "equity")

    with caplog.at_level("WARNING"):
        EquityDuckdbAdapter(root).list_fields()

    assert _fallback_warnings(caplog) == []
