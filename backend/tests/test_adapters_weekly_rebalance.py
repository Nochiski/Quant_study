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
    EarlierClose,
    PlanExistsError,
    PlanRecord,
    SignalBasis,
    SignalReadStatus,
    SignalRecord,
)
from strategy_workbench.domain.rebalance.facade.rebalance import RebalancePolicy, plan_rebalance
from strategy_workbench.domain.risk.facade.risk import SignalFacts

KST = timezone(timedelta(hours=9))
DAY = date(2026, 10, 23)
BUILD, PRICES = "e_20261023T065500Z", "e_20261023T065450Z"


def model_db(
    root: Path,
    *,
    latest: dict[str, object] | None = None,
    manifest_build: str = BUILD,
    committed: tuple[str, ...] | None = None,
    scores: list[tuple[str, int, str]] | None = None,
    basis: SignalBasis = SignalBasis.EVENING,
) -> Path:
    model = root / "model"
    (model / "scope@2.0" / f"v={BUILD}").mkdir(parents=True)
    pointer = {
        "status": "ok",
        "basis": basis.value,
        "date": DAY.isoformat(),
        "build_id": BUILD,
        "fi_build_id": PRICES,
        "excluded_specs": {},
        "specs": {"scope@2.0": {"gates": {"MG0": {"status": "pass"}, "MG5": {"status": "skip"}}}},
    }
    pointer.update(latest or {})
    (model / f"latest_{basis.value}.json").write_text(json.dumps(pointer))
    # MANIFEST 빌드 목록 = 커밋된 판. current_build 는 종류와 상관없이 마지막 커밋이다
    listed = committed if committed is not None else tuple(dict.fromkeys((BUILD, manifest_build)))
    (model / "scope@2.0" / "MANIFEST.json").write_text(
        json.dumps({"current_build": manifest_build, "builds": [{"build_id": b} for b in listed]})
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
        model / "scope@2.0" / f"v={BUILD}" / "scores.parquet",
    )
    prices = root / "factor_inputs" / "fi_prices" / f"v={PRICES}"
    prices.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                # 000009 는 신호일 행이 없다(거래정지 등). 그 전 마지막 종가는 10-21 7,500원이다
                "ticker": ["000001", "000002", "000003", "000001", "000009", "000009"],
                "date": [DAY, DAY, DAY, date(2026, 10, 22), date(2026, 10, 20), date(2026, 10, 21)],
                "close": [10_000, 20_000, 30_000, 9_000, 7_000, 7_500],
                "volume": [100, 0, 50, 100, 10, 0],
            }
        ),
        prices / "part0.parquet",
    )
    (prices / "_meta.json").write_text("{}")
    return root


def test_signal_reads_the_current_build_ranks_and_closes(tmp_path: Path) -> None:
    root = model_db(tmp_path)
    read = ScopeSignalSource(root).read(DAY, SignalBasis.EVENING)
    assert read.status is SignalReadStatus.OK and read.snapshot is not None
    snapshot = read.snapshot
    assert (snapshot.build_id, snapshot.prices_build_id) == (BUILD, PRICES)
    assert dict(snapshot.ranks) == {"000001": 1, "000002": 2}
    assert dict(snapshot.closes_krw) == {"000001": 10_000, "000002": 20_000, "000003": 30_000}
    assert snapshot.untraded == frozenset({"000002"})
    scores = root / "model" / "scope@2.0" / f"v={BUILD}" / "scores.parquet"
    assert snapshot.scores_sha256 == hashlib.sha256(scores.read_bytes()).hexdigest()
    # 신호일 행이 없는 종목만 그 전 마지막 종가를 따로 싣는다(#603 리뷰: 보유 종목 평가용)
    assert snapshot.earlier_closes == {"000009": EarlierClose(date(2026, 10, 21), 7_500)}


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"latest": {"date": "2026-10-22"}}, SignalReadStatus.NOT_READY),
        ({"latest": {"status": "failed"}}, SignalReadStatus.NOT_READY),
        (
            {"latest": {"specs": {"scope@2.0": {"gates": {"MG1": {"status": "fail"}}}}}},
            SignalReadStatus.GATE_FAILED,
        ),
        ({"latest": {"excluded_specs": {"scope@2.0": "gate"}}}, SignalReadStatus.GATE_FAILED),
        ({"committed": ("e_older",), "manifest_build": "e_older"}, SignalReadStatus.UNREADABLE),
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
    read = ScopeSignalSource(root).read(DAY, SignalBasis.EVENING)
    assert read.status is status, read.detail
    assert read.snapshot is None


def test_signal_reads_the_pointer_of_the_requested_basis(tmp_path: Path) -> None:
    # 주말 보강판은 따로 남는다(2026-10-23 사용자 결정). 장 마감 포인터는 하루 전 판을 가리킨다
    root = model_db(tmp_path, basis=SignalBasis.SUPPLEMENT)
    evening = {"status": "ok", "basis": "evening", "date": "2026-10-22", "build_id": "e_prev"}
    (root / "model" / "latest_evening.json").write_text(json.dumps(evening))
    supplement = ScopeSignalSource(root).read(DAY, SignalBasis.SUPPLEMENT)
    assert supplement.ok and supplement.snapshot is not None
    assert supplement.snapshot.build_id == BUILD
    assert ScopeSignalSource(root).read(DAY, SignalBasis.EVENING).status is (
        SignalReadStatus.NOT_READY
    )
    # 포인터 안의 종류가 파일 이름과 다르면 쓰지 않는다
    mixed = model_db(tmp_path / "mixed", latest={"basis": "evening"}, basis=SignalBasis.SUPPLEMENT)
    assert ScopeSignalSource(mixed).read(DAY, SignalBasis.SUPPLEMENT).status is (
        SignalReadStatus.NOT_READY
    )


def test_supplement_survives_a_later_morning_rebuild_of_the_same_root(tmp_path: Path) -> None:
    # 2026-10-24 사용자 결정 D2: 원장은 아침판만 다시 지어도 MANIFEST current_build 를 그 판으로
    # 바꾼다(연구 루트는 아침판·보강판이 한 MANIFEST 를 쓴다). 보강판 포인터가 가리키는 판을 읽는다
    root = model_db(tmp_path, basis=SignalBasis.SUPPLEMENT, manifest_build="m_rebuild")
    read = ScopeSignalSource(root).read(DAY, SignalBasis.SUPPLEMENT)
    assert read.ok and read.snapshot is not None, read.detail
    assert read.snapshot.build_id == BUILD


def test_supplement_never_falls_back_to_another_build(tmp_path: Path) -> None:
    # 보강판 포인터가 없거나 다른 날짜면 MANIFEST 의 현재 판(같은 날 아침판)으로 넘어가지 않는다.
    # 명령줄은 이 상태를 실패 알림으로 보낸다(test_basis_picks_the_build_and_the_notice_names_it)
    missing = model_db(tmp_path / "missing", basis=SignalBasis.SUPPLEMENT, manifest_build="m_same")
    (missing / "model" / "latest_supplement.json").unlink()
    assert ScopeSignalSource(missing).read(DAY, SignalBasis.SUPPLEMENT).status is (
        SignalReadStatus.UNREADABLE
    )
    stale = model_db(
        tmp_path / "stale",
        basis=SignalBasis.SUPPLEMENT,
        manifest_build="m_same",
        latest={"date": "2026-10-16"},
    )
    assert ScopeSignalSource(stale).read(DAY, SignalBasis.SUPPLEMENT).status is (
        SignalReadStatus.NOT_READY
    )


def test_signal_reads_names_and_why_unranked_names_left(tmp_path: Path) -> None:
    root = model_db(tmp_path)
    universe = root / "factor_inputs" / "fi_universe" / f"v={PRICES}"
    universe.mkdir(parents=True)
    rows = [
        ("000001", "순위 있음", 5_000.0, True, None, "fresh"),
        ("000003", "시총 작음", 812.4, True, None, "fresh"),
        ("000004", "추정치 끊김", 3_000.0, False, "estimates_lapsed", "lapsed"),
        ("000005", "유예 중", 3_000.0, True, None, "grace"),
        ("000006", "가격 없음", 3_000.0, True, None, "fresh"),
        ("000007", "새 사유", 3_000.0, False, "new_rule", "fresh"),
    ]
    columns = ["ticker", "name", "market_cap", "eligible", "exclude_reason", "coverage_state"]
    pq.write_table(
        pa.table({name: [row[i] for row in rows] for i, name in enumerate(columns)}),
        universe / "part0.parquet",
    )
    snapshot = ScopeSignalSource(root).read(DAY, SignalBasis.EVENING).snapshot
    assert snapshot is not None and snapshot.names is not None
    assert snapshot.names["000004"] == "추정치 끊김"
    assert dict(snapshot.exclusion_reasons or {}) == {
        "000003": "시총 하한 미만(시총 812억)",
        "000004": "WISE 추정치 공백",
        "000005": "WISE 추정치 공백(유예 중)",
        "000006": "가격 없음(거래정지 등)",
        "000007": "유니버스 제외(new_rule)",
    }
    # 유니버스 판이 없으면 이름·이유 없이 진행한다
    bare = ScopeSignalSource(model_db(tmp_path / "bare")).read(DAY, SignalBasis.EVENING).snapshot
    assert bare is not None and bare.names is None and bare.exclusion_reasons is None


def test_signal_reads_the_trading_value_and_flags_the_risk_guard_needs(tmp_path: Path) -> None:
    root = model_db(tmp_path)
    universe = root / "factor_inputs" / "fi_universe" / f"v={PRICES}"
    universe.mkdir(parents=True)
    # adv20 은 신호일 포함 20거래일 평균 거래대금(억 원)이다
    pq.write_table(
        pa.table(
            {
                "ticker": ["000001", "000002", "000003"],
                "adv20": [45_139.435991656, 2.15, None],
                "is_admin": [False, True, False],
                "is_halted": [False, False, True],
            }
        ),
        universe / "part0.parquet",
    )
    snapshot = ScopeSignalSource(root).read(DAY, SignalBasis.EVENING).snapshot
    assert snapshot is not None and snapshot.facts is not None
    assert dict(snapshot.facts) == {
        "000001": SignalFacts(4_513_943_599_166, admin=False, halted=False),
        "000002": SignalFacts(215_000_000, admin=True, halted=False),
        "000003": SignalFacts(None, admin=False, halted=True),
    }
    # 옛 판처럼 칸이 없으면 자료 없음(None) — 리스크는 그 종목들을 사지 않는다
    bare = ScopeSignalSource(model_db(tmp_path / "bare")).read(DAY, SignalBasis.EVENING).snapshot
    assert bare is not None and bare.facts is None


def test_missing_model_db_is_unreadable(tmp_path: Path) -> None:
    read = ScopeSignalSource(tmp_path).read(DAY, SignalBasis.EVENING)
    assert read.status is SignalReadStatus.UNREADABLE


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
        SignalRecord("scope@2.0", BUILD, "2026-10-23", "ab" * 32, PRICES, (("000001", 1),)),
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
    model = root / "model" / "scope@2.0"
    (model / "v=e_prev").mkdir()
    pq.write_table(
        pa.table(
            {"stock_code": ["000001", "000009"], "rank": [2, 1], "score_date": ["2026-10-22"] * 2}
        ),
        model / "v=e_prev" / "scores.parquet",
    )
    builds = [{"build_id": "e_prev"}, {"build_id": BUILD}]
    (model / "MANIFEST.json").write_text(json.dumps({"current_build": BUILD, "builds": builds}))
    snapshot = ScopeSignalSource(root).read(DAY, SignalBasis.EVENING).snapshot
    assert snapshot is not None and snapshot.previous_build_id == "e_prev"
    assert dict(snapshot.previous_ranks or {}) == {"000001": 2, "000009": 1}
    # 직전 빌드가 없으면 점검을 건너뛴다
    fresh = ScopeSignalSource(model_db(tmp_path / "fresh")).read(DAY, SignalBasis.EVENING).snapshot
    assert fresh is not None and fresh.previous_ranks is None


def test_gap_check_skips_same_day_rebuilds_back_to_the_day_before(tmp_path: Path) -> None:
    # 주말 보강판 바로 앞 판은 같은 신호일의 장 마감 판이다. 하루 사이 누락을 보는 점검이라
    # 그 판을 건너뛰고 신호일 전 날짜의 판과 견준다
    root = model_db(tmp_path)
    model = root / "model" / "scope@2.0"
    for build, day, ranks in (("e_prev", "2026-10-22", [2, 1]), ("e_same", "2026-10-23", [1, 2])):
        (model / f"v={build}").mkdir()
        pq.write_table(
            pa.table({"stock_code": ["000001", "000009"], "rank": ranks, "score_date": [day] * 2}),
            model / f"v={build}" / "scores.parquet",
        )

    def previous(*builds: str) -> tuple[str | None, dict[str, int] | None]:
        listed = [{"build_id": build} for build in (*builds, BUILD)]
        (model / "MANIFEST.json").write_text(json.dumps({"current_build": BUILD, "builds": listed}))
        snapshot = ScopeSignalSource(root).read(DAY, SignalBasis.EVENING).snapshot
        assert snapshot is not None
        ranks = None if snapshot.previous_ranks is None else dict(snapshot.previous_ranks)
        return snapshot.previous_build_id, ranks

    assert previous("e_prev", "e_same") == ("e_prev", {"000001": 2, "000009": 1})
    assert previous("e_same") == (None, None)


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
        base.signal,
        previous_build_id="e_prev",
        anomalies=("상위 30위 중 2종목이 사라짐",),
        names=(("000001", "가"), ("000009", "보유")),
        gap_check_skipped="직전 빌드 e_prev 순위를 쓸 수 없음",
        basis=SignalBasis.SUPPLEMENT,
    )
    record = replace(base, signal=signal, plan=plan)
    store = JsonPlanStore(tmp_path)
    store.save(record)
    assert store.load(record.plan_id) == record
    # 빌드 종류 칸이 생기기 전(2026-10-24)에 남긴 주문안은 장 마감 빌드로 읽힌다
    path = tmp_path / "plans" / f"{record.plan_id}.json"
    stored = json.loads(path.read_text())
    del stored["signal"]["basis"]
    path.write_text(json.dumps(stored))
    assert store.load(record.plan_id) == replace(
        record, signal=replace(signal, basis=SignalBasis.EVENING)
    )
    approval = Approval(datetime(2026, 10, 23, 17, 0, tzinfo=KST), "동민", "", ("000009",))
    store.save_approval(record.plan_id, approval)
    assert store.approval(record.plan_id) == approval
