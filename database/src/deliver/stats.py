"""전달층 순수 통계 — 의존 없는 함수만(qpack·common 이 함께 쓴다)."""
from __future__ import annotations

from collections.abc import Sequence


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    """선형 보간 분위수(numpy 기본과 같다). 빈 목록은 호출부가 거른다."""
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


__all__ = ["quantile"]
