"""엔진 공용 순수 함수 — v3 `backend/scoring/factors/ranking.py` 등에서 그대로 옮겼다.

표준 라이브러리만 쓴다. Python ≥ 3.11 의 `statistics.mean`·`stdev` 는 분수로 정확히 계산한 뒤
한 번만 반올림하므로 입력 순서와 무관하게 같은 값을 낸다(결정성의 근거).
⚠ v3 런타임은 Python 3.9 라 `stdev` 가 부동소수 (x−c)² 합 뒤 `math.sqrt` 다 — 같은 입력에서 끝자리
1 ULP 가 다를 수 있다(골든 09-28 실측 |Δ| ≤ 8.9e-16). 우리 런타임 쪽 값을 정본으로 둔다.
"""
from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:     # 주석 전용 — 실행 때는 표준 라이브러리만 쓴다
    from model.contracts import UniverseRule

# 추정치 소멸 사유 — fi `factor_inputs.queries.EXCLUDE_REASONS` 의 'estimates_lapsed' 와 같은 코드
ESTIMATES_LAPSED = "estimates_lapsed"


def estimates_lapsed(rule: UniverseRule, row: Mapping[str, object]) -> str | None:
    """spec 유예로 다시 자른 추정치 소멸 — 빼야 하면 `ESTIMATES_LAPSED`, 아니면 None(N-47).

    fi 는 유예 창 `FI_COVERAGE_GRACE_MAX`(5)로 신선도를 판정해 나이 ≤ 5 인 grace 종목까지
    eligible 로 둔다. 엔진은 그중 `coverage_state = 'grace'` 이고 나이(`coverage_age_days`)가
    spec 의 `coverage_grace_days` 를 넘는 종목을 뺀다 — 유예 0 spec(원본 미러·scope_v1.0)은
    fi 유예 0 이던 때와 같은 종목을 본다. 나이 0 인 grace(WISE 를 휴장일에 수집해 마지막
    신선일과 D* 사이에 거래일이 없는 경우)는 유예 0 spec 에도 남는다(옛 fi 유예 0 판정과
    같다). 나이를 모르는 grace 는 뺀다(FG-fresh 가 막는 모양). 신선도 열이 비어 있는 행
    (compat 어댑터 골든 입력)과 `require_estimates = false` spec 은 보지 않는다. 시총 하한 등
    다른 규칙보다 먼저 부른다 — fi eligible 이 거짓인 종목과 같게 다룬다.
    """
    if not rule.require_estimates or row.get("coverage_state") != "grace":
        return None
    age = row.get("coverage_age_days")
    if isinstance(age, int) and age <= rule.coverage_grace_days:
        return None
    return ESTIMATES_LAPSED


def z_score_winsorized(values: Sequence[float], sigma: float = 3.0) -> list[float]:
    """v3 `ranking.py:6-17` 그대로.

    평균·**표본** 표준편차(n−1)를 자르기 전 값으로 구하고, 값을 [평균 ± sigma·σ] 로 자른 뒤 그
    평균·σ 로 표준화한다. n < 2 이거나 σ = 0 이면 전부 0.0.
    """
    n = len(values)
    if n < 2:
        return [0.0] * n
    mean = statistics.mean(values)
    std = statistics.stdev(values)
    if std == 0:
        return [0.0] * n
    lo = mean - sigma * std
    hi = mean + sigma * std
    clipped = [max(lo, min(hi, v)) for v in values]
    return [(v - mean) / std for v in clipped]


def crosses_step(flags: Sequence[bool]) -> bool:
    """창 안 행들의 계단 표식(`fi_adj_prices.adj_ok`·`adj_jump_ok`)이 바뀌나 — 바뀌면 그 창이 표시된
    사건의 적용일을 넘는다(값이 한결같으면 척도가 이어진다). v4 `_Series.crosses_event`·scope(T-9)
    가 같이 쓴다."""
    return any(flags) and not all(flags)


def weighted_available(scores: Mapping[str, float], weights: Mapping[str, float]) -> float | None:
    """있는 하위 점수만으로 비례 재정규화한 가중합 Σ s·(w/Σw). 하나도 없거나 Σw = 0 이면 None.

    v3 의 팩터 합성·종합점수 합성이 모두 이 모양이다(`momentum.py:20-31`·`flow.py:35-45`·
    `revision.py:62-71`·`quality.py:52-62`·`valuation.py:47-57`·`engine.py:90-101`).
    **합산 순서 = `weights` 의 키 순서**다. v3 는 대부분 같은 순서(config 순)로 더하지만
    quality·valuation 은 `set(sub_weights)` 순(해시 시드에 따라 실행마다 다름)이라 그 둘은 v3 자체가
    끝자리(1 ULP 수준)에서 비결정적이다 — 여기서는 config 순으로 고정한다.
    """
    keys = [k for k in weights if k in scores]
    if not keys:
        return None
    total_w = sum(weights[k] for k in keys)
    if total_w == 0:
        return None
    return sum(scores[k] * (weights[k] / total_w) for k in keys)
