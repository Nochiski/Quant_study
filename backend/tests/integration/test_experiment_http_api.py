"""실험 HTTP(V3-03·V3-05·V4-03·V4-04) — 미리 계산과 실제 원장 증가, 워크포워드, 파라미터 지도,
용량 스윕, 기반 리비전 제약, 코드화된 거절(spec D2·D5)."""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from strategy_workbench.adapters.inbound.http_api._experiment_routes import (
    ExperimentAdmissionErrorResponse,
    ExperimentErrorResponse,
)
from strategy_workbench.adapters.outbound.research_sqlite.facade.repository import (
    SQLiteExperimentRepository,
)
from strategy_workbench.application.experiment_run.facade.ports import (
    ExperimentRecord,
    TrialAttempt,
)
from strategy_workbench.bootstrap.facade.http import build_http_app
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunSpec,
    SavedRevisionReference,
)
from strategy_workbench.domain.experiment.facade.design import (
    ExperimentDesign,
    SplitMode,
    SplitSpec,
    build_search_spec,
)
from tests.backtest_run_wait import wait_for_terminal_state
from tests.frozen_revision_rows import FROZEN_SPEC_HASH, seed_frozen_rows

_SPLIT = {"mode": "rolling", "train_years": 1, "test_years": 1, "embargo_sessions": 0}


def _experiment(client: TestClient, **environment: str) -> dict[str, Any]:
    template = client.get("/api/v1/strategies/template").json()
    template.pop("identity")
    template["schema_version"] = "1.2"
    # 20세션 모멘텀으로 매 세션 고른다 — 검증 실행 첫 세션의 결정은 검증 창 앞 워밍업 관측으로만
    # 채워진다(V3-05). 워밍업을 읽지 않으면 첫 주문이 20세션 뒤로 밀린다.
    template["portfolio"] |= {"rebalance": "every_n_sessions", "rebalance_every_n_sessions": 1}
    template["factors"][0]["graph"] = {
        "nodes": [
            {"node_id": "close", "field_id": "price.close", "kind": "field"},
            {
                "node_id": "momentum",
                "operator": "momentum",
                "input_node_id": "close",
                "window": 20,
                "kind": "time_series",
            },
        ],
        "output_node_id": "momentum",
    }
    template["parameters"] = [
        {
            "parameter_id": "scale",
            "default": 1.0,
            "minimum": -1.0,
            "maximum": 1.0,
            "step": 1.0,
            "kind": "float",
        }
    ]
    saved = client.post(
        "/api/v1/strategy-documents",
        json={"format": "json", "source": json.dumps(template, default=str)},
    ).json()
    run = {
        "strategy_source": {
            "kind": "saved_revision",
            "strategy_id": saved["strategy_id"],
            "revision": saved["revision"],
            "expected_spec_hash": saved["spec_hash"],
        },
        "core": "python",
        "environment": {
            "start": "2021-01-04",
            "end": "2023-06-30",
            "universe_id": "krx.common-stock",
        }
        | environment,
    }
    return {"run": run, "search": {"scale": None}, "split": _SPLIT}


def _wait_until_finished(client: TestClient, experiment_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        experiment = client.get(f"/api/v1/experiments/{experiment_id}").json()
        if experiment["status"] in ("completed", "cancelled"):
            return experiment
        time.sleep(0.2)
    raise AssertionError(f"experiment did not finish — experiment_id={experiment_id}")


def test_the_preview_equals_the_ledger_growth_after_the_experiment() -> None:
    client = TestClient(build_http_app())
    request = _experiment(client)
    lineage = request["run"]["strategy_source"]["strategy_id"]

    preview = client.post("/api/v1/experiments/preview", json=request)
    created = client.post("/api/v1/experiments", json=request)
    experiment = _wait_until_finished(client, created.json()["record"]["experiment_id"])
    trials = client.get(f"/api/v1/experiments/{experiment['record']['experiment_id']}/trials")
    ledger = client.get(f"/api/v1/strategies/{lineage}/trials").json()

    assert preview.status_code == 200, preview.text
    assert created.status_code == 202, created.text
    # scale 격자 -1, 0, 1 × 롤링 창 둘 = 실행 6. 창은 분할 설계가 정한 평가 구간이라 시도 키는 실험
    # 기반 실행 설정으로 낸다 — 한 칸의 두 창이 한 시도이고 N 은 0 → 3 이다.
    assert {key: preview.json()[key] for key in ("combination_count", "run_count")} == {
        "combination_count": 3,
        "run_count": 6,
    }
    assert (preview.json()["trial_count"], preview.json()["trial_count_after"]) == (0, 3)
    assert experiment["status"] == "completed"
    assert [trial["status"] for trial in trials.json()] == ["completed"] * 6
    assert ledger["trial_count"] == preview.json()["trial_count_after"]
    # 칸마다 학습 두 번 + 창마다 고른 칸의 검증 실행 한 번(같은 시도 키라 재확인이다).
    assert sum(len(group["runs"]) for group in ledger["trials"]) == 6 + 2
    listed = client.get("/api/v1/experiments", params={"limit": 1}).json()
    assert [item["record"]["experiment_id"] for item in listed["items"]] == [
        experiment["record"]["experiment_id"]
    ]
    assert listed["next_after"] is None
    # 대기열 화면의 표면(V5-01): 끝난 뒤라 도는 실행이 없고, 끝난 trial 은 고를 수 있다.
    assert (listed["slots"]["running"], listed["max_priority"]) == (0, 5)
    assert listed["slots"]["total"] >= 1
    assert [(trial["selectable"], trial["retryable"]) for trial in trials.json()] == [
        (True, False)
    ] * 6
    blank = client.post(
        f"/api/v1/experiments/{experiment['record']['experiment_id']}/selections",
        json={"trial_index": 0, "reason": "   "},
    )
    assert blank.status_code == 422, blank.text
    # 고를 때의 계열 N·대표 샤프·DSR 을 남긴다(V4-02).
    selected = client.post(
        f"/api/v1/experiments/{experiment['record']['experiment_id']}/selections",
        json={"trial_index": 0, "reason": "학습 샤프가 가장 높다"},
    ).json()
    assert selected["trial_count"] == ledger["trial_count"]
    assert selected["ledger_representative_sharpe"] == ledger["trials"][0]["representative_sharpe"]
    assert 0.0 <= selected["deflated_sharpe"] <= 1.0
    walk_forward = client.get(
        f"/api/v1/experiments/{experiment['record']['experiment_id']}/walk-forward"
    ).json()
    picks = [window["pick"] for window in walk_forward["windows"]]
    assert {run["run_id"] for group in ledger["trials"] for run in group["runs"]} == {
        trial["attempts"][0]["run_id"] for trial in trials.json()
    } | {pick["run_id"] for pick in picks}
    assert [pick["window_index"] for pick in picks] == [0, 1]
    assert [window["run_status"] for window in walk_forward["windows"]] == ["completed"] * 2
    # 곡선은 검증 창(2022-01-04 ~ 2023-01-03, 2023-01-04 ~ 2023-06-30) 세션만 잇는다. 학습 점수처럼
    # 창마다 첫 스냅숏부터 세므로 둘째 창 첫 세션 2023-01-04 점은 없고 학습 구간 세션도 없다.
    sessions = [point["session"] for point in walk_forward["curve"]]
    assert (sessions[0], sessions[-1]) == ("2022-01-04", "2023-06-30")
    boundary = sessions.index("2023-01-03")
    assert sessions[boundary + 1] == "2023-01-05"
    assert sessions == sorted(set(sessions))
    assert walk_forward["gap"] is None
    assert walk_forward["out_of_sample_sharpe"] is not None
    # 워밍업은 검증 창 앞에서 읽고 성과에서는 뺀다: 첫 검증 실행의 곡선은 검증 시작일부터지만, 그날
    # 이미 20세션 모멘텀으로 종목을 골라 주문을 낸다.
    tested = client.get(f"/api/v1/backtests/{picks[0]['run_id']}/result").json()
    assert tested["series"]["equity"][0]["session"] == "2022-01-04"
    assert tested["artifacts"]["orders"][0]["session"] == "2022-01-04"
    # 파라미터 지도(V4-03): 칸 점수는 그 칸 두 학습 실행의 원장 세션 샤프 평균이다.
    parameter_map = client.get(
        f"/api/v1/experiments/{experiment['record']['experiment_id']}/parameter-map"
    )
    sharpes = {
        run["run_id"]: run["session_sharpe"] for group in ledger["trials"] for run in group["runs"]
    }
    expected: dict[int, list[float]] = {}
    for trial in trials.json():
        expected.setdefault(trial["trial"]["grid_index"][0], []).append(
            sharpes[trial["attempts"][0]["run_id"]]
        )
    assert parameter_map.status_code == 200, parameter_map.text
    assert [cell["grid_index"] for cell in parameter_map.json()["cells"]] == [[0], [1], [2]]
    assert [cell["score"] for cell in parameter_map.json()["cells"]] == pytest.approx(
        [sum(values) / 2 for values in expected.values()]
    )
    assert [cell["verdict"] for cell in parameter_map.json()["cells"]].count("recommended") == 1


def test_a_capacity_sweep_from_a_finished_run_leaves_the_trial_count_as_it_was() -> None:
    """V4-04: 결과를 본 실행에서 연 용량 스윕은 초기 자본만 다른 실행이라 같은 시도의 재확인이다."""
    client = TestClient(build_http_app())
    run = _experiment(client)["run"]
    lineage = run["strategy_source"]["strategy_id"]
    base = client.post("/api/v1/backtests", json=run)
    assert base.status_code == 202, base.text
    assert wait_for_terminal_state(client, base.json()["run"]["run_id"])["status"] == "completed"
    before = client.get(f"/api/v1/strategies/{lineage}/trials").json()["trial_count"]
    request = {"run": run, "initial_cash": [1e10, 1e8, 1e9]}

    preview = client.post("/api/v1/experiments/capacity/preview", json=request)
    created = client.post("/api/v1/experiments/capacity", json=request)
    experiment_id = created.json()["record"]["experiment_id"]
    experiment = _wait_until_finished(client, experiment_id)
    capacity = client.get(f"/api/v1/experiments/{experiment_id}/capacity").json()
    walk_forward = client.get(f"/api/v1/experiments/{experiment_id}/walk-forward")
    ledger = client.get(f"/api/v1/strategies/{lineage}/trials").json()

    assert preview.status_code == 200, preview.text
    assert (preview.json()["run_count"], preview.json()["new_trial_count"]) == (3, 0)
    assert created.status_code == 202, created.text
    assert experiment["status"] == "completed"
    assert experiment["record"]["design"]["kind"] == "capacity_sweep"
    assert experiment["record"]["split"] is None
    # 금액 순으로 편다. 고정 슬리피지(기본 10bp)는 규칙 가격 기준이라 체결 금액(슬리피지를 더하거나
    # 뺀 가격) 대비로는 매수 10/1.001·매도 10/0.999bp 사이다.
    points = capacity["points"]
    assert [point["initial_cash"] for point in points] == [1e8, 1e9, 1e10]
    assert all(point["status"] == "completed" for point in points)
    assert all(point["sharpe"] is not None for point in points)
    assert [point["impact_cost_bps"] for point in points] == pytest.approx([10.0] * 3, rel=1e-3)
    assert all(0 <= point["session_unfilled_ratio"] <= 1 for point in points)
    # 모든 금액이 끝났으니 확정됐다(곡선 모양에 따라 한계 금액이나 "양수 샤프 없음").
    assert capacity["limit"]["gap"] not in ("pending", "cancelled")
    # 스윕 실행 셋은 모두 기반 실행의 시도 키로 원장에 적힌 재확인이라 N 이 그대로다.
    assert ledger["trial_count"] == before
    (group,) = [
        g
        for g in ledger["trials"]
        if any(r["run_id"] == base.json()["run"]["run_id"] for r in g["runs"])
    ]
    assert [r["role"] for r in group["runs"]] == ["counted", "recheck", "recheck", "recheck"]
    assert (walk_forward.status_code, walk_forward.json()["detail"]["code"]) == (
        409,
        "experiment.kind.mismatch",
    )


def test_design_and_lookup_rejections_are_coded() -> None:
    client = TestClient(build_http_app())
    request = _experiment(client)
    sealed = _experiment(client, start="2019-06-03")
    inline = request | {
        "run": request["run"]
        | {"strategy_source": None, "strategy": client.get("/api/v1/strategies/template").json()}
    }

    responses = {
        "inline": client.post("/api/v1/experiments", json=inline),
        "split": client.post(
            "/api/v1/experiments/preview", json=request | {"split": _SPLIT | {"train_years": 0}}
        ),
        "search": client.post(
            "/api/v1/experiments/preview", json=request | {"search": {"missing": [1]}}
        ),
        "field": client.post(
            "/api/v1/experiments/preview",
            json=request | {"run": request["run"] | {"initial_cash": -1}},
        ),
        "sealed": client.post("/api/v1/experiments", json=sealed),
        "capacity": client.post(
            "/api/v1/experiments/capacity/preview",
            json={"run": request["run"], "initial_cash": [1e8, 1e8, 2e8]},
        ),
        # 이 설정으로 돌린 백테스트가 아직 없다.
        "capacity_base": client.post(
            "/api/v1/experiments/capacity",
            json={"run": request["run"], "initial_cash": [1e8, 1e9, 1e10]},
        ),
    }
    missing = client.get("/api/v1/experiments/missing")
    missing_map = client.get("/api/v1/experiments/missing/parameter-map")

    assert {name: response.json()["detail"]["code"] for name, response in responses.items()} == {
        "inline": "experiment.base.unsaved",
        "split": "experiment.split.invalid",
        "search": "experiment.search.unknown_parameter",
        "field": "backtest.run.field_invalid",
        "sealed": "backtest.run.research_window_violation",
        "capacity": "experiment.capacity.invalid_amounts",
        "capacity_base": "experiment.capacity.base_not_run",
    }
    for response in responses.values():
        assert response.status_code == 422, response.text
        TypeAdapter(ExperimentAdmissionErrorResponse).validate_python(response.json())
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "experiment.not_found")
    TypeAdapter(ExperimentErrorResponse).validate_python(missing.json())
    assert (missing_map.status_code, missing_map.json()["detail"]["code"]) == (
        404,
        "experiment.not_found",
    )
    # 실험 기반 검사는 실행 요청이 아니므로 봉인 원장에 차단 기록을 남기지 않는다.
    sealed_lineage = sealed["run"]["strategy_source"]["strategy_id"]
    assert client.get(f"/api/v1/strategies/{sealed_lineage}/trials").json()["blocked"] == []


def test_a_frozen_revision_cannot_be_the_base(tmp_path: Path) -> None:
    path = tmp_path / "frozen-http.sqlite3"
    seed_frozen_rows(path)
    client = TestClient(build_http_app(strategy_repository_path=path))
    run = {
        "strategy_source": {
            "kind": "saved_revision",
            "strategy_id": "frozen-doc",
            "revision": 1,
            "expected_spec_hash": FROZEN_SPEC_HASH,
        },
        "environment": {
            "start": "2021-01-04",
            "end": "2023-06-30",
            "universe_id": "krx.common-stock",
        },
    }

    response = client.post("/api/v1/experiments", json={"run": run, "search": {}, "split": _SPLIT})

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "backtest.strategy.requires_upgrade"


def test_a_base_that_is_no_longer_admitted_is_recorded_and_refused_with_its_code(
    tmp_path: Path,
) -> None:
    """#380 DEFECT-V3D-02: 만든 뒤 기반 리비전이 동결된 실험. 재시작 복구는 남은 trial 에 거절
    attempt 를 남겨 멈추지 않고, 재시도는 백테스트 시작과 같은 코드(422)로 거절한다."""
    strategy_path = tmp_path / "strategies.sqlite3"
    research_path = tmp_path / "research.sqlite3"
    seed_frozen_rows(strategy_path)
    split = SplitSpec(mode=SplitMode.ROLLING, train_years=1, test_years=1, embargo_sessions=0)
    at = datetime(2026, 9, 30, tzinfo=UTC)
    repository = SQLiteExperimentRepository(research_path)
    repository.add(
        ExperimentRecord(
            experiment_id="frozen-experiment",
            created_at=at,
            run=BacktestRunSpec(
                strategy_source=SavedRevisionReference(
                    "frozen-doc", 1, FROZEN_SPEC_HASH, "saved_revision"
                ),
                environment=RunEnvironment(
                    start=date(2021, 1, 4), end=date(2023, 6, 30), universe_id="krx.common-stock"
                ),
            ),
            split=split,
            design=ExperimentDesign(
                search=build_search_spec((), {}),
                parameter_values={},
                windows=split.windows(date(2021, 1, 4), date(2023, 6, 30)),
                measured=True,
            ),
        )
    )
    # 창 0 학습은 이미 거절로 실패했고, 창 1 학습은 넘기기 전에 서버가 내려갔다.
    repository.add_attempt(
        TrialAttempt("frozen-experiment", 0, 1, at, error_code="backtest.run.invalid", error="x")
    )

    client = TestClient(
        build_http_app(strategy_repository_path=strategy_path, research_db_path=research_path)
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        trials = client.get("/api/v1/experiments/frozen-experiment/trials").json()
        if trials[1]["attempts"]:
            break
        time.sleep(0.1)
    retried = client.post("/api/v1/experiments/frozen-experiment/trials/0/retry")

    assert [attempt["error_code"] for attempt in trials[1]["attempts"]] == [
        "backtest.strategy.requires_upgrade"
    ]
    assert retried.status_code == 422, retried.text
    assert retried.json()["detail"]["code"] == "backtest.strategy.requires_upgrade"
    experiment = client.get("/api/v1/experiments/frozen-experiment").json()
    assert experiment["status"] == "completed"


def test_a_held_experiment_can_be_paused_streamed_and_cancelled() -> None:
    """V3-04: e2e 훅이 trial 을 붙잡은 동안 일시정지·우선순위·취소와 진행 스트림을 본다."""
    client = TestClient(build_http_app(trial_hold_seconds=30))
    experiment_id = client.post("/api/v1/experiments", json=_experiment(client)).json()["record"][
        "experiment_id"
    ]
    base = f"/api/v1/experiments/{experiment_id}"

    deadline = time.monotonic() + 30
    while "running" not in [trial["status"] for trial in client.get(f"{base}/trials").json()]:
        assert time.monotonic() < deadline, "no trial was held running"
        time.sleep(0.1)
    prioritised = client.patch(f"{base}/controls", json={"priority": 2})
    paused = client.patch(f"{base}/controls", json={"paused": True})
    refused = client.patch(f"{base}/controls", json={"priority": 99})
    cancelled = client.post(f"{base}/cancel")
    with client.stream("GET", f"{base}/events") as stream:
        frames = [line for line in stream.iter_lines() if line.startswith("data:")]
    missing = client.get("/api/v1/experiments/missing/events")

    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    assert paused.json()["record"]["controls"] == {"paused": True, "priority": 2}
    assert paused.json()["trial_counts"] == {"running": 1, "queued": 5}
    assert (refused.status_code, refused.json()["detail"]["code"]) == (
        422,
        "backtest.run.field_invalid",
    )
    assert cancelled.json()["status"] == "cancelled"
    # 끝난 실험의 스트림은 마지막 진행 한 번을 보내고 닫힌다.
    # 도는 trial 이 멈출 때까지 스트림은 열려 있고, 마지막 프레임이 최종 수다.
    final = json.loads(frames[-1][len("data:") :])
    assert final["status"] == "cancelled"
    assert set(final["trial_counts"]) == {"cancelled"}
    assert prioritised.json()["record"]["controls"] == {"paused": False, "priority": 2}
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "experiment.not_found")
