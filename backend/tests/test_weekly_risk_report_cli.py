"""S07 위험 보고 명령줄. 서버 후속 명령은 `risk-show --telegram`의 출력(글만)을 그대로 보낸다."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, time
from pathlib import Path

import pytest

from strategy_workbench.adapters.inbound.weekly_rebalance_cli.facade.command import (
    CliFactories,
    FillRecording,
    run_cli,
)
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    NoticeSender,
    RiskReportService,
    WeeklyExecutionService,
    WeeklyRebalanceService,
)
from tests.application.test_weekly_rebalance import Log, Plans, Risk
from tests.application.test_weekly_risk_report import START, Prices, Reports, record
from tests.domain.test_risk import LIMITS


def _unused_planning(workdir: Path, model_root: Path | None) -> WeeklyRebalanceService:
    raise AssertionError("위험 보고는 주문안 서비스를 열지 않는다")


@contextmanager
def _unused_execution(
    workdir: Path, send: bool, approval: bool
) -> Iterator[WeeklyExecutionService]:
    raise AssertionError("위험 보고는 실행기를 열지 않는다")
    yield  # pragma: no cover


def _unused_recorder(workdir: Path, until: time) -> FillRecording:
    raise AssertionError("위험 보고는 체결 알림을 기록하지 않는다")


def _unused_notifier() -> NoticeSender:
    raise AssertionError("위험 보고는 보내지 않는다")


def _factories(service: RiskReportService, roots: list[Path | None]) -> CliFactories:
    def risk_report(workdir: Path, equity_root: Path | None) -> RiskReportService:
        roots.append(equity_root)
        return service

    return CliFactories(
        _unused_planning,
        _unused_execution,
        _unused_recorder,
        _unused_notifier,
        risk_report=risk_report,
    )


def test_report_is_made_then_shown_as_telegram_text_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = Log()
    log.save_day(record(START, 95_000_000))
    service = RiskReportService(log, Risk(limits_=LIMITS), Reports(), Prices(), Plans())
    roots: list[Path | None] = []
    factories = _factories(service, roots)
    work = str(tmp_path / "work")
    made = ["risk-report", "--workdir", work, "--equity-root", str(tmp_path / "eq")]
    assert run_cli(factories, made) == 0
    out = capsys.readouterr().out
    assert out.startswith("위험 보고 2026-10-26 장 마감") and out.rstrip().endswith(
        "[ok] prices=m_1"
    )
    assert roots == [tmp_path / "eq"]
    shown = ["risk-show", "--workdir", work, "--day", "2026-10-26", "--telegram"]
    assert run_cli(factories, shown) == 0
    text = capsys.readouterr().out
    assert text.startswith("<b>S07 위험 보고</b>") and "[ok]" not in text
    assert roots == [tmp_path / "eq", None]


def test_missing_report_or_day_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    service = RiskReportService(Log(), Risk(limits_=LIMITS), Reports(), Prices(), Plans())
    factories = _factories(service, [])
    work = str(tmp_path / "work")
    assert run_cli(factories, ["risk-show", "--workdir", work, "--day", str(date(2026, 1, 2))]) == 1
    assert capsys.readouterr().out.startswith("[no_day]")
    made = ["risk-report", "--workdir", work, "--equity-root", str(tmp_path)]
    assert run_cli(factories, made) == 1
    assert capsys.readouterr().out.startswith("[no_day]")
