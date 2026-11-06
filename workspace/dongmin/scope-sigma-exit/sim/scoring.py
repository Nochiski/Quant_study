"""팩터별 점수에 가중을 다시 곱해 순위를 낸다(PREREG_v3)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def rescore(
    rows: Iterable[tuple[str, Mapping[str, float | None]]],
    weights: tuple[tuple[str, float], ...],
    *,
    required: tuple[str, ...],
) -> list[tuple[str, float]]:
    """(종목, 팩터별 점수) → 합성 점수 내림차순 (종목, 점수). 같으면 종목코드 순.

    `required` 팩터가 하나라도 비면 뺀다. 그 밖에 빈 팩터는 남은 팩터의 가중으로 재정규화한다(운영 v3_zscore 와 같음).
    """
    out: list[tuple[str, float]] = []
    for ticker, factors in rows:
        if any(factors.get(k) is None for k in required):
            continue
        used = [(w, factors[k]) for k, w in weights if factors.get(k) is not None]
        total = sum(w for w, _ in used)
        if total <= 0:
            continue
        out.append((ticker, sum(w * x for w, x in used) / total))  # pyright: ignore[reportOperatorIssue]  # reason: used 는 None 을 걸렀다
    out.sort(key=lambda r: (-r[1], r[0]))
    return out
