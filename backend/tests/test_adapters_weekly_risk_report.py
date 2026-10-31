"""S07 일일 위험 보고 파일. 임시 폴더에만 쓴다."""

from __future__ import annotations

import json
import stat
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.rebalance_files.facade.risk import JsonRiskReportStore
from strategy_workbench.application.weekly_rebalance.facade.rebalance import RiskUnavailableError
from tests.domain.test_risk_report import sample_report


def test_report_round_trips_and_is_private(tmp_path: Path) -> None:
    store = JsonRiskReportStore(tmp_path)
    report = sample_report(account=[(date(2026, 10, 1), 100_000), (date(2026, 10, 7), 106_000)])
    assert store.report(report.day) is None
    store.save_report(report)
    assert store.report(report.day) == report
    path = tmp_path / "risk" / "report-2026-10-30.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # 같은 날 다시 만들면 갈아 끼운다
    store.save_report(sample_report())
    assert store.report(report.day) == sample_report()


def test_unexpected_schema_is_unreadable(tmp_path: Path) -> None:
    store = JsonRiskReportStore(tmp_path)
    store.save_report(sample_report())
    path = tmp_path / "risk" / "report-2026-10-30.json"
    path.write_text(json.dumps({"schema": "other/1"}), encoding="utf-8")
    with pytest.raises(RiskUnavailableError, match="report-2026-10-30.json"):
        store.report(date(2026, 10, 30))
