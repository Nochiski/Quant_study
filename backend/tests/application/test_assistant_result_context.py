"""결과 설명 요약 테스트 (결과 설명 spec R2).

모델이 결과에서 보는 것은 이 요약 하나다. 무엇을 넣고 무엇을 덜어 내는지, 그리고 길이 상한을
어떤 순서로 지키는지를 고정한다. 요약 전문은 골든(`tests/fixtures/assistant/result_context.json`)이
따로 잠근다.
"""

from __future__ import annotations

import json
from dataclasses import replace

from strategy_workbench.application.assistant_chat._result_context import (
    MAX_RESULT_CONTEXT_CHARS,
    MAX_SUMMARY_MONTHS,
    summarize_backtest_result,
)
from strategy_workbench.domain.analytics.facade.metrics import MetricScope, MetricValue
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult, DataWarning

from ..assistant_result_samples import SAMPLE_RUN_ID, sample_backtest_result


def _summary(result: BacktestRunResult | None = None, **kwargs: int) -> dict[str, object]:
    text = summarize_backtest_result(result or sample_backtest_result(), **kwargs)
    payload = json.loads(text)
    assert isinstance(payload, dict)
    return payload


def _metric(payload: dict[str, object], metric_id: str, scope: str = "full") -> dict[str, object]:
    metrics = payload["metrics"]
    assert isinstance(metrics, list)
    matches = [m for m in metrics if m["metric_id"] == metric_id and m["scope"] == scope]
    assert len(matches) == 1, (metric_id, scope, matches)
    return matches[0]


def test_the_summary_names_the_run_its_period_and_its_benchmark() -> None:
    payload = _summary()
    result = sample_backtest_result()

    assert payload["run"] == {
        "run_id": SAMPLE_RUN_ID,
        "completed_at": "2026-09-01T09:00:00+00:00",
    }
    environment = payload["environment"]
    assert isinstance(environment, dict)
    assert environment["start"] == result.manifest.environment.start.isoformat()
    assert environment["end"] == result.manifest.environment.end.isoformat()
    assert environment["fee_bps"] == result.manifest.environment.fee_bps
    assert payload["capital"] == {
        "initial_cash": 100_000_000.0,
        "annualization_days": 252,
        "benchmark_security_id": "sec-005930-1",
    }


def test_metrics_carry_the_registry_meaning_next_to_the_value() -> None:
    """단위와 방향이 없으면 모델이 0.3412를 34%인지 0.34배인지, 낙폭이 클수록 좋은지 모른다."""
    payload = _summary()

    assert _metric(payload, "total_return") == {
        "metric_id": "total_return",
        "label": "Total return",
        "category": "return",
        "unit": "percent",
        "higher_is_better": True,
        "scope": "full",
        "value": 0.3412,
        "sample_count": 820,
    }
    unrecovered = _metric(payload, "max_drawdown_recovery_sessions")
    assert unrecovered["value"] is None
    assert unrecovered["unavailable_reason"] == "drawdown_not_recovered"
    assert _metric(payload, "sharpe", "out_of_sample")["scope_label"] == "OOS 2026-03-02"


def test_the_summary_carries_the_ends_of_the_curve_but_not_the_raw_series() -> None:
    """원시 시계열은 수천 행이다. 양 끝과 가장 깊은 낙폭의 위치만 옮긴다."""
    payload = _summary()
    result = sample_backtest_result()
    first, last = result.series.equity[0], result.series.equity[-1]
    deepest = result.series.drawdown[17]

    assert payload["equity"] == {
        "sessions": len(result.series.equity),
        "first": {
            "session": first.session.isoformat(),
            "equity": first.equity,
            "benchmark_equity": first.benchmark_equity,
        },
        "last": {
            "session": last.session.isoformat(),
            "equity": last.equity,
            "benchmark_equity": last.benchmark_equity,
        },
        "deepest_drawdown": {
            "session": deepest.session.isoformat(),
            "drawdown": deepest.drawdown,
        },
    }
    text = summarize_backtest_result(result)
    for raw in ("rolling_sharpe", "snapshots", "positions", 'fills":['):
        assert raw not in text


def test_only_the_most_recent_months_are_kept_and_the_rest_is_counted() -> None:
    payload = _summary()
    months = payload["monthly_returns"]
    assert isinstance(months, list)

    assert len(months) == MAX_SUMMARY_MONTHS
    assert months[-1] == {"month": "2026-04", "value": 0.01}
    assert payload["omitted"] == {"monthly_returns": 40 - MAX_SUMMARY_MONTHS}


def test_the_strategy_summary_names_its_source_and_factors_without_graphs() -> None:
    payload = _summary()
    strategy = payload["strategy"]
    assert isinstance(strategy, dict)

    assert strategy["title"] == "KRX 모멘텀 표본"
    assert strategy["source"] == {
        "kind": "saved_revision",
        "strategy_id": "strategy-sample",
        "revision": 2,
    }
    assert strategy["factors"] == [
        {"factor_id": "price.close", "label": "종가", "direction": "high", "weight": 1.0}
    ]
    assert "graph" not in json.dumps(strategy)


def test_activity_counts_orders_fills_and_closed_trades() -> None:
    assert _summary()["activity"] == {"orders": 6, "fills": 5, "closed_trades": 4}


def test_warnings_are_passed_through() -> None:
    assert _summary()["warnings"] == [
        {
            "code": "data.coverage.gap",
            "message": "2023-05 한 달 동안 일부 종목의 종가가 비어 있습니다.",
            "severity": "warning",
        }
    ]


def test_the_default_summary_fits_the_cap() -> None:
    assert len(summarize_backtest_result(sample_backtest_result())) <= MAX_RESULT_CONTEXT_CHARS


def test_a_tight_cap_drops_months_then_warnings_then_window_metrics_but_never_full_metrics() -> (
    None
):
    """상한을 넘기면 정해진 순서로 덜어 내고, 덜어 낸 개수를 `omitted`에 남긴다."""
    base = sample_backtest_result()
    noisy = replace(
        base,
        manifest=replace(
            base.manifest,
            warnings=tuple(
                DataWarning(f"data.warning.{index}", "경고 문장 " * 20) for index in range(30)
            ),
        ),
        metrics=base.metrics
        + tuple(
            MetricValue("sharpe", 0.5, MetricScope.WINDOW, 60, f"window {index}")
            for index in range(40)
        ),
    )
    cap = 4_500

    text = summarize_backtest_result(noisy, max_chars=cap)
    payload = json.loads(text)

    assert len(text) <= cap
    omitted = payload["omitted"]
    assert omitted["monthly_returns"] == 40
    assert omitted["warnings"] == 30
    assert omitted["window_metrics"] > 0
    assert payload["monthly_returns"] == []
    assert payload["warnings"] == []
    full_ids = {m["metric_id"] for m in payload["metrics"] if m["scope"] == "full"}
    assert full_ids == set(
        metric.metric_id for metric in base.metrics if metric.scope is MetricScope.FULL
    )


def test_an_oversized_description_is_cut_before_it_reaches_the_model() -> None:
    """설명은 사용자가 쓴 자유 문장이라 길이가 무한하다. 상한을 혼자 다 먹지 못하게 자른다."""
    base = sample_backtest_result()
    strategy = base.manifest.run_spec.strategy
    assert strategy is not None
    long_run = replace(
        base,
        manifest=replace(
            base.manifest,
            run_spec=replace(
                base.manifest.run_spec,
                strategy=replace(strategy, description="가" * 50_000),
            ),
        ),
    )

    payload = _summary(long_run)
    described = payload["strategy"]
    assert isinstance(described, dict)
    description = described["description"]
    assert isinstance(description, str)

    assert len(description) < 1_000
    assert description.endswith("…")
