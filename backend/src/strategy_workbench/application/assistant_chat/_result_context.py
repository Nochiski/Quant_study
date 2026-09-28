"""끝난 백테스트 결과를 모델이 읽을 요약으로 줄인다 (결과 설명 spec R2).

모델이 결과에서 보는 것은 이 요약 하나다. 규칙은 셋이다.

1. **새로 계산하지 않는다.** 지표 공식·방향·단위의 owner는 backend Metric Registry이고, 여기서는
   서버가 이미 낸 지표와 월별 값을 옮긴다. 시계열에서 꺼내는 것은 양 끝 값과 최솟값의 위치뿐이며
   이것은 공식이 아니라 선택이다. 연 수익률처럼 공식이 필요한 값은 만들지 않는다.
2. **원시 시계열은 넣지 않는다.** 일별 자산·낙폭·롤링 샤프, 주문·체결·포지션 행은 수천 행이다.
   토큰만 먹고 설명에 쓸 정보는 늘지 않는다.
3. **길이 상한을 지킨다.** 직렬화한 JSON이 `max_chars`를 넘으면 정해진 순서로 덜어 내고 덜어 낸
   개수를 `omitted`에 적는다. 모델이 "없다"와 "잘렸다"를 구분해야 해서다. 순서는 월별 수익률(오래된
   달부터) → 데이터 경고(info를 먼저, 같은 등급은 뒤에서부터) → full이 아닌 구간 지표(뒤에서부터) →
   팩터(뒤에서부터) → 결과 숫자를 해석하는 경고(뒤에서부터)다. full 구간 지표는 자르지 않는다.
   사용자가 화면에서 보는 숫자가 그것이다. 결과 숫자를 해석하는 경고(`benchmark.*`, 섹터 제약
   제외)는 그 full 지표가 왜 사용 불가인지, 초과수익을 어떻게 읽어야 하는지를 알려 주므로 가장
   늦게 던다(이슈 #241).

필드 이름은 JSON 키로만 쓴다. 전략 언어 요약과 달리 모델이 이 키로 문서를 쓰지 않으므로 스키마에서
생성하지 않는다. 실행 설정은 `RunEnvironment`를 통째로 옮겨 필드가 늘면 따라 늘게 한다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import date, datetime
from enum import Enum

from strategy_workbench.domain.analytics.facade.metrics import (
    MetricDefinition,
    MetricScope,
    MetricValue,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult

__all__ = [
    "MAX_RESULT_CONTEXT_CHARS",
    "MAX_SUMMARY_MONTHS",
    "summarize_backtest_result",
]

# 요약 JSON의 글자 수 상한. 한글을 섞은 JSON에서 약 4천 토큰이다. 기본 표본(지표 17개, 월 36개,
# 경고 1개)이 5천 자 안팎이라 평소에는 아무것도 덜지 않고, 구간 지표·경고가 쌓인 실행에서만 자른다.
MAX_RESULT_CONTEXT_CHARS = 12_000

# 월별 수익률은 최근 3년만 싣는다. 그보다 오래된 달은 "최근 흐름"을 설명하는 데 거의 쓰이지 않는다.
MAX_SUMMARY_MONTHS = 36

# 결과 숫자(full 지표)를 해석하는 경고의 code 접두어. 요약이 상한을 넘을 때 가장 늦게 던다
# (모듈 docstring 3, 이슈 #241). 벤치마크 경고는 benchmark_return·excess_return이 사용 불가인 이유와
# 동결 구간의 초과수익 해석을, 섹터 제외 경고는 섹터 상한이 왜 적용되지 않았는지를 알려 준다.
_METRIC_EXPLAINING_WARNING_PREFIXES = ("benchmark.", "portfolio.sector_unknown_excluded")

# 사용자가 쓴 자유 문장의 길이 상한. 제목·설명 하나가 상한을 혼자 다 먹지 못하게 한다.
_MAX_TITLE_CHARS = 200
_MAX_DESCRIPTION_CHARS = 600
_ELLIPSIS = "…"


def summarize_backtest_result(
    result: BacktestRunResult, *, max_chars: int = MAX_RESULT_CONTEXT_CHARS
) -> str:
    """결과 요약 JSON 문자열. 길이는 언제나 `max_chars` 이하다."""
    manifest = result.manifest
    definitions = {item.metric_id: item for item in result.metric_definitions}
    full_metrics: list[object] = [
        _metric_payload(metric, definitions.get(metric.metric_id))
        for metric in result.metrics
        if metric.scope is MetricScope.FULL
    ]
    window_metrics: list[object] = [
        _metric_payload(metric, definitions.get(metric.metric_id))
        for metric in result.metrics
        if metric.scope is not MetricScope.FULL
    ]
    months: list[object] = [
        {"month": f"{point.year:04d}-{point.month:02d}", "value": point.value}
        for point in result.series.monthly_returns
    ]
    omitted: dict[str, int] = {}
    if len(months) > MAX_SUMMARY_MONTHS:
        omitted["monthly_returns"] = len(months) - MAX_SUMMARY_MONTHS
        months = months[-MAX_SUMMARY_MONTHS:]
    warnings: list[object] = [
        {"code": warning.code, "message": warning.message, "severity": warning.severity.value}
        for warning in manifest.warnings
    ]
    factors = _factor_payloads(result)
    strategy = _strategy_payload(result, factors)

    payload: dict[str, object] = {
        "run": {
            "run_id": manifest.run_id,
            "completed_at": manifest.completed_at.isoformat(),
        },
        "environment": _plain(asdict(manifest.environment)),
        "capital": {
            "initial_cash": manifest.initial_cash,
            "annualization_days": manifest.annualization_days,
            "benchmark_security_id": manifest.run_spec.benchmark_security_id,
        },
        "strategy": strategy,
        "metrics": full_metrics + window_metrics,
        "equity": _equity_payload(result),
        "monthly_returns": months,
        "warnings": warnings,
        "activity": {
            "orders": len(result.artifacts.orders),
            "fills": len(result.artifacts.fills),
            "closed_trades": len(result.artifacts.trades),
        },
    }

    # 덜어 낼 목록, `omitted` 키, 덜 항목을 고르는 규칙의 짝. 앞에 있는 것부터 줄인다(모듈
    # docstring 3). 고를 항목이 없으면 다음 단계로 넘어간다.
    shrinkable: tuple[tuple[str, list[object], Callable[[list[object]], int | None]], ...] = (
        ("monthly_returns", months, _first),
        ("warnings", warnings, _ordinary_warning),
        ("window_metrics", window_metrics, _last),
        ("factors", factors, _last),
        ("warnings", warnings, _last),
    )
    while True:
        payload["metrics"] = full_metrics + window_metrics
        if omitted:
            payload["omitted"] = dict(omitted)
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(text) <= max_chars:
            return text
        for key, items, pick in shrinkable:
            index = pick(items)
            if index is not None:
                items.pop(index)
                omitted[key] = omitted.get(key, 0) + 1
                break
        else:
            raise ValueError(
                "backtest result summary does not fit even after dropping every optional "
                f"section — run_id={manifest.run_id} chars={len(text)} max_chars={max_chars} "
                f"full_metrics={len(full_metrics)}"
            )


def _first(items: list[object]) -> int | None:
    return 0 if items else None


def _last(items: list[object]) -> int | None:
    return len(items) - 1 if items else None


def _ordinary_warning(items: list[object]) -> int | None:
    """결과 숫자를 해석하지 않는 경고 중 덜 것 하나. info를 먼저, 같은 등급은 뒤에서부터 고른다."""
    ordinary = [
        (index, item)
        for index, item in enumerate(items)
        if isinstance(item, dict)
        and not str(item.get("code", "")).startswith(_METRIC_EXPLAINING_WARNING_PREFIXES)
    ]
    if not ordinary:
        return None
    info = [index for index, item in ordinary if item.get("severity") == "info"]
    return info[-1] if info else ordinary[-1][0]


def _metric_payload(metric: MetricValue, definition: MetricDefinition | None) -> dict[str, object]:
    """지표 하나. 정의가 없는 id는 값만 옮긴다(registry와 결과가 어긋난 실행도 설명은 된다)."""
    entry: dict[str, object] = {"metric_id": metric.metric_id}
    if definition is not None:
        entry.update(
            {
                "label": definition.label,
                "category": definition.category.value,
                "unit": definition.unit.value,
                "higher_is_better": definition.higher_is_better,
            }
        )
    entry["scope"] = metric.scope.value
    if metric.scope_label is not None:
        entry["scope_label"] = metric.scope_label
    entry["value"] = metric.value
    entry["sample_count"] = metric.sample_count
    if metric.unavailable_reason is not None:
        entry["unavailable_reason"] = metric.unavailable_reason
    return entry


def _factor_payloads(result: BacktestRunResult) -> list[object]:
    """팩터 이름·방향·가중치.

    그래프는 싣지 않는다. 노드 수에 비례해 길어지고 설명에는 이름이면 된다.
    """
    strategy = result.manifest.run_spec.strategy
    if strategy is None:
        return []
    return [
        {
            "factor_id": factor.factor_id,
            "label": _cut(factor.label, _MAX_TITLE_CHARS),
            "direction": factor.direction.value,
            "weight": factor.weight,
        }
        for factor in strategy.factors
    ]


def _strategy_payload(result: BacktestRunResult, factors: list[object]) -> dict[str, object]:
    """무엇을 돌렸는가. `factors`는 상한 때문에 줄어들 수 있어 호출자가 쥔 목록을 그대로 싣는다."""
    provenance = result.manifest.strategy_provenance
    strategy = result.manifest.run_spec.strategy
    payload: dict[str, object] = {
        "title": "" if strategy is None else _cut(strategy.title, _MAX_TITLE_CHARS),
        "description": ""
        if strategy is None
        else _cut(strategy.description, _MAX_DESCRIPTION_CHARS),
        "source": {
            "kind": provenance.kind.value,
            "strategy_id": provenance.strategy_id,
            "revision": provenance.revision,
        },
        "factors": factors,
    }
    if strategy is not None:
        payload["portfolio"] = _plain(asdict(strategy.portfolio))
    return payload


def _equity_payload(result: BacktestRunResult) -> dict[str, object]:
    equity = result.series.equity
    drawdown = result.series.drawdown
    payload: dict[str, object] = {"sessions": len(equity)}
    if equity:
        for key, point in (("first", equity[0]), ("last", equity[-1])):
            payload[key] = {
                "session": point.session.isoformat(),
                "equity": point.equity,
                "benchmark_equity": point.benchmark_equity,
            }
    if drawdown:
        # 같은 깊이가 여러 날이면 처음 닿은 날이다. `min`은 앞선 항목을 고른다.
        deepest = min(drawdown, key=lambda point: point.drawdown)
        payload["deepest_drawdown"] = {
            "session": deepest.session.isoformat(),
            "drawdown": deepest.drawdown,
        }
    return payload


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + _ELLIPSIS


def _plain(value: object) -> object:
    """`asdict` 결과를 JSON 값으로. enum은 값, 날짜는 ISO 문자열이다."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
