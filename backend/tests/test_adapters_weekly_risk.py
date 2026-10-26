"""S07 리스크 기록 파일과 한도 파일. 임시 폴더에 직접 만든 기록만 쓴다(저장소 한도 파일은
읽기만)."""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.rebalance_files.facade.plans import JsonRiskStore
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    PlanExistsError,
    RiskDay,
    RiskUnavailableError,
    RiskVerdict,
)
from strategy_workbench.bootstrap.weekly_rebalance.facade.command import RISK_LIMITS_FILE
from strategy_workbench.domain.risk.facade.risk import (
    ActionKind,
    DrawdownState,
    GuardedLine,
    GuardedTargets,
    GuardReason,
    PostTradeReview,
    ReviewedOrder,
    RiskAction,
    RiskLevel,
)
from tests.domain.test_risk import PAYLOAD

KST = timezone(timedelta(hours=9))
SATURDAY = datetime(2026, 10, 31, 9, 1, tzinfo=KST)
STATE = DrawdownState(
    RiskLevel.REDUCE, 100_000_000, date(2026, 10, 21), 1_620, date(2026, 10, 30), 83_800_000
)


def _limits_file(tmp_path: Path, payload: object = PAYLOAD) -> Path:
    path = tmp_path / "limits.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _store(tmp_path: Path) -> JsonRiskStore:
    return JsonRiskStore(tmp_path / "work", _limits_file(tmp_path))


def _verdict() -> RiskVerdict:
    guarded = GuardedTargets(
        RiskLevel.REDUCE,
        5_000,
        (
            GuardedLine("000001", 0, 101, 50, (GuardReason.EXPOSURE,)),
            GuardedLine("000002", 30, 0, 0, ()),
        ),
    )
    return RiskVerdict(
        "weekly-2026-10-30",
        SATURDAY,
        1,
        "ab" * 32,
        STATE,
        guarded,
        ("위반 한 줄",),
        plan_created_at=SATURDAY - timedelta(seconds=1),
        close_from_plan=True,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_verdict_is_written_once_and_read_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.verdict("weekly-2026-10-30") is None
    store.save_verdict(_verdict())
    assert store.verdict("weekly-2026-10-30") == _verdict()
    path = tmp_path / "work" / "risk" / "verdict-weekly-2026-10-30.json"
    assert _mode(path) == 0o600
    with pytest.raises(PlanExistsError):
        store.save_verdict(_verdict())


def test_a_day_check_is_replaced_by_the_last_close_of_the_day(tmp_path: Path) -> None:
    store = _store(tmp_path)
    review = PostTradeReview(
        (ReviewedOrder("000001", "buy", 101, 50, 40, 51, 2_000, 12),),
        ("위반",),
        ("기록",),
    )
    first = RiskDay(date(2026, 11, 2), SATURDAY, 1, "ab" * 32, RiskLevel.NORMAL, STATE, None)
    store.save_day(first)
    second = RiskDay(date(2026, 11, 2), SATURDAY, 1, "ab" * 32, RiskLevel.WARN, STATE, review)
    store.save_day(second)
    assert store.days() == (second,)
    assert _mode(tmp_path / "work" / "risk" / "day-2026-11-02.json") == 0o600


def test_actions_are_appended_in_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    resume = RiskAction(SATURDAY, ActionKind.RESUME, "동민", "다시 봄")
    reset = RiskAction(SATURDAY + timedelta(hours=1), ActionKind.RESET, "동민", "", 80_000_000)
    store.add_action(resume)
    store.add_action(reset)
    assert store.actions() == (resume, reset)
    assert _mode(tmp_path / "work" / "risk" / "actions.jsonl") == 0o600


def test_limits_carry_the_file_fingerprint(tmp_path: Path) -> None:
    path = _limits_file(tmp_path)
    limits = JsonRiskStore(tmp_path / "work", path).limits()
    assert limits.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert limits.version == 1


@pytest.mark.parametrize("content", ["{not json", json.dumps({**PAYLOAD, "max_weight_bp": -1})])
def test_unreadable_limits_stop_risk_judgement(tmp_path: Path, content: str) -> None:
    path = tmp_path / "broken.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RiskUnavailableError, match="broken.json"):
        JsonRiskStore(tmp_path / "work", path).limits()
    with pytest.raises(RiskUnavailableError):
        JsonRiskStore(tmp_path / "work", tmp_path / "missing.json").limits()


def test_a_damaged_verdict_stops_risk_judgement(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_verdict(_verdict())
    path = tmp_path / "work" / "risk" / "verdict-weekly-2026-10-30.json"
    path.chmod(0o600)
    path.write_text('{"schema": "s07-risk-verdict/1"}', encoding="utf-8")
    with pytest.raises(RiskUnavailableError, match="verdict-weekly-2026-10-30.json"):
        store.verdict("weekly-2026-10-30")


def test_a_verdict_filed_under_another_plan_is_unreadable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save_verdict(_verdict())
    folder = tmp_path / "work" / "risk"
    (folder / "verdict-weekly-2026-10-30.json").rename(folder / "verdict-weekly-2026-11-06.json")
    with pytest.raises(RiskUnavailableError, match="weekly-2026-11-06"):
        store.verdict("weekly-2026-11-06")


def test_a_write_failure_is_a_risk_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_action(RiskAction(SATURDAY, ActionKind.RESUME, "동민"))  # 폴더를 만든다
    folder = tmp_path / "work" / "risk"
    folder.chmod(0o500)
    try:
        with pytest.raises(RiskUnavailableError):
            store.save_verdict(_verdict())
        day = RiskDay(date(2026, 11, 2), SATURDAY, 1, "ab" * 32, RiskLevel.NORMAL, STATE, None)
        with pytest.raises(RiskUnavailableError):
            store.save_day(day)
    finally:
        folder.chmod(0o700)


def test_the_shipped_limits_file_reads() -> None:
    # 배포본이 읽는 저장소 한도 파일. 깨진 채로 배포되면 예약 주문안이 모두 리스크 판정 불가로
    # 멈춘다
    limits = JsonRiskStore(Path("/nonexistent"), RISK_LIMITS_FILE).limits()
    assert limits.drawdown.halt_bp > limits.drawdown.reduce_bp > limits.drawdown.warn_bp
