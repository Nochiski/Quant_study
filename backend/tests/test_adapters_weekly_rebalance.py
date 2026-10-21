"""S07 어댑터: 모델 DB 신호 읽기와 주문안 파일 저장. 임시 폴더에 직접 만든 파일만 쓴다."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from strategy_workbench.adapters.outbound.rebalance_files.facade.plans import JsonPlanStore
from strategy_workbench.adapters.outbound.scope_signal.facade.source import ScopeSignalSource
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountState,
    Approval,
    PlanExistsError,
    PlanRecord,
    SignalReadStatus,
    SignalRecord,
)
from strategy_workbench.domain.rebalance.facade.rebalance import RebalancePolicy, plan_rebalance

KST = timezone(timedelta(hours=9))
DAY = date(2026, 10, 23)
BUILD, PRICES = "e_20261023T065500Z", "e_20261023T065450Z"


def model_db(
    root: Path,
    *,
    latest: dict[str, object] | None = None,
    manifest_build: str = BUILD,
    scores: list[tuple[str, int, str]] | None = None,
) -> Path:
    model = root / "model"
    (model / "scope@1.0" / f"v={BUILD}").mkdir(parents=True)
    pointer = {
        "status": "ok",
        "basis": "evening",
        "date": DAY.isoformat(),
        "build_id": BUILD,
        "fi_build_id": PRICES,
        "excluded_specs": {},
        "specs": {"scope@1.0": {"gates": {"MG0": {"status": "pass"}, "MG5": {"status": "skip"}}}},
    }
    pointer.update(latest or {})
    (model / "latest_evening.json").write_text(json.dumps(pointer))
    (model / "scope@1.0" / "MANIFEST.json").write_text(
        json.dumps({"current_build": manifest_build})
    )
    rows = scores or [("000001", 1, "2026-10-23"), ("000002", 2, "2026-10-23")]
    pq.write_table(
        pa.table(
            {
                "stock_code": [row[0] for row in rows],
                "rank": [row[1] for row in rows],
                "score_date": [row[2] for row in rows],
            }
        ),
        model / "scope@1.0" / f"v={BUILD}" / "scores.parquet",
    )
    prices = root / "factor_inputs" / "fi_prices" / f"v={PRICES}"
    prices.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "ticker": ["000001", "000002", "000003", "000001"],
                "date": [DAY, DAY, DAY, date(2026, 10, 22)],
                "close": [10_000, 20_000, 30_000, 9_000],
                "volume": [100, 0, 50, 100],
            }
        ),
        prices / "part0.parquet",
    )
    (prices / "_meta.json").write_text("{}")
    return root


def test_signal_reads_the_current_build_ranks_and_closes(tmp_path: Path) -> None:
    root = model_db(tmp_path)
    read = ScopeSignalSource(root).read(DAY)
    assert read.status is SignalReadStatus.OK and read.snapshot is not None
    snapshot = read.snapshot
    assert (snapshot.build_id, snapshot.prices_build_id) == (BUILD, PRICES)
    assert dict(snapshot.ranks) == {"000001": 1, "000002": 2}
    assert dict(snapshot.closes_krw) == {"000001": 10_000, "000002": 20_000, "000003": 30_000}
    assert snapshot.untraded == frozenset({"000002"})
    scores = root / "model" / "scope@1.0" / f"v={BUILD}" / "scores.parquet"
    assert snapshot.scores_sha256 == hashlib.sha256(scores.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"latest": {"date": "2026-10-22"}}, SignalReadStatus.NOT_READY),
        ({"latest": {"status": "failed"}}, SignalReadStatus.NOT_READY),
        (
            {"latest": {"specs": {"scope@1.0": {"gates": {"MG1": {"status": "fail"}}}}}},
            SignalReadStatus.GATE_FAILED,
        ),
        ({"latest": {"excluded_specs": {"scope@1.0": "gate"}}}, SignalReadStatus.GATE_FAILED),
        ({"manifest_build": "e_older"}, SignalReadStatus.UNREADABLE),
        ({"scores": [("000001", 1, "2026-10-22")]}, SignalReadStatus.UNREADABLE),
        (
            {"scores": [("000001", 1, "2026-10-23"), ("000002", 1, "2026-10-23")]},
            SignalReadStatus.UNREADABLE,
        ),
    ],
)
def test_signal_refuses_stale_failed_or_inconsistent_builds(
    tmp_path: Path, kwargs: dict[str, object], status: SignalReadStatus
) -> None:
    root = model_db(tmp_path, **kwargs)  # pyright: ignore[reportArgumentType]  # reason: 사례 표
    read = ScopeSignalSource(root).read(DAY)
    assert read.status is status, read.detail
    assert read.snapshot is None


def test_missing_model_db_is_unreadable(tmp_path: Path) -> None:
    assert ScopeSignalSource(tmp_path).read(DAY).status is SignalReadStatus.UNREADABLE


def _record() -> PlanRecord:
    plan = plan_rebalance(
        RebalancePolicy(slots=2, keep_rank=3),
        ranks={"000001": 1, "000002": 2, "000003": 3},
        holdings={"000003": 10},
        prices={"000001": 10_000, "000002": 20_000, "000003": 30_000},
        cash_krw=1_000_000,
        blocked={"000002": "정지"},
    )
    return PlanRecord(
        "weekly-2026-10-23",
        datetime(2026, 10, 23, 16, 10, tzinfo=KST),
        SignalRecord("scope@1.0", BUILD, "2026-10-23", "ab" * 32, PRICES, (("000001", 1),)),
        AccountState(1_000_000, {"000003": 10}, None, "실행 원장 없음"),
        plan,
    )


def test_plan_store_round_trips_and_never_overwrites(tmp_path: Path) -> None:
    store = JsonPlanStore(tmp_path)
    record = _record()
    assert store.load(record.plan_id) is None
    store.save(record)
    assert store.load(record.plan_id) == record
    with pytest.raises(PlanExistsError):
        store.save(record)
    path = tmp_path / "plans" / "weekly-2026-10-23.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in (tmp_path / "plans").iterdir()] == [path.name]  # 임시 파일 없음

    approval = Approval(datetime(2026, 10, 23, 17, 0, tzinfo=KST), "동민", "첫 주")
    store.save_approval(record.plan_id, approval)
    assert store.approval(record.plan_id) == approval
    with pytest.raises(PlanExistsError):
        store.save_approval(record.plan_id, approval)


def test_plan_store_rejects_path_like_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        JsonPlanStore(tmp_path).load("../escape")


def test_signal_carries_the_previous_build_ranks_for_the_gap_check(tmp_path: Path) -> None:
    root = model_db(tmp_path)
    model = root / "model" / "scope@1.0"
    (model / "v=e_prev").mkdir()
    pq.write_table(
        pa.table(
            {"stock_code": ["000001", "000009"], "rank": [2, 1], "score_date": ["2026-10-22"] * 2}
        ),
        model / "v=e_prev" / "scores.parquet",
    )
    builds = [{"build_id": "e_prev"}, {"build_id": BUILD}]
    (model / "MANIFEST.json").write_text(json.dumps({"current_build": BUILD, "builds": builds}))
    snapshot = ScopeSignalSource(root).read(DAY).snapshot
    assert snapshot is not None and snapshot.previous_build_id == "e_prev"
    assert dict(snapshot.previous_ranks or {}) == {"000001": 2, "000009": 1}
    # 직전 빌드가 없으면 점검을 건너뛴다
    fresh = ScopeSignalSource(model_db(tmp_path / "fresh")).read(DAY).snapshot
    assert fresh is not None and fresh.previous_ranks is None


def test_plan_store_keeps_review_flags_anomalies_and_no_trade(tmp_path: Path) -> None:
    base = _record()
    plan = plan_rebalance(
        RebalancePolicy(slots=2, keep_rank=3),
        ranks={"000001": 1},
        holdings={"000009": 5},
        prices={"000001": 10_000, "000009": 10_000},
        cash_krw=0,
        blocked={},
        previous_ranks={"000009": 2},
    )
    signal = replace(
        base.signal, previous_build_id="e_prev", anomalies=("상위 30위 중 2종목이 사라짐",)
    )
    record = replace(base, signal=signal, plan=plan)
    store = JsonPlanStore(tmp_path)
    store.save(record)
    assert store.load(record.plan_id) == record
    approval = Approval(datetime(2026, 10, 23, 17, 0, tzinfo=KST), "동민", "", ("000009",))
    store.save_approval(record.plan_id, approval)
    assert store.approval(record.plan_id) == approval
