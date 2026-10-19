"""열 평가기의 시계열 창 커널(columnar-tape spec D3, T3-02).

객체 평가기 `_time_series` 와 같은 의미를 종목 우선 정렬 위치 위에서 계산한다. 창 정본은
`node_read_window` 이고, 창은 종목 안 행 위치 [p − far, p − near] 의 정확히 `window` 칸이다. 시작이
음수(이력 부족)이거나 창에 숫자가 아닌 칸이 하나라도 있으면 None 이다. 가린 칸이 든 창은 호출자가
`_masked_in` 으로 None 처리한다(값을 계산해도 그 칸은 쓰지 않는다).

T3-02 는 momentum·delta·min·max 다. mean·std 는 정확 커널(T3-03·T3-04)이 맡는다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import numpy as np
from numpy.typing import NDArray

from ._history import node_read_window
from ._nodes import TimeSeriesNode, TimeSeriesOperator

# min·max 의 교체 비교. Python `min`/`max` 는 더 작을(클) 때만 바꿔 같으면 앞 원소가 남는다
# (`min(0.0, -0.0)` 은 0.0). `np.minimum` 은 이 규칙을 지키지 않아 쓰지 않는다.
_REPLACES = {TimeSeriesOperator.MINIMUM: np.less, TimeSeriesOperator.MAXIMUM: np.greater}
WINDOW_TIME_SERIES_OPERATORS = frozenset(
    {TimeSeriesOperator.MOMENTUM, TimeSeriesOperator.DELTA, *_REPLACES}
)


def time_series_window(
    node: TimeSeriesNode,
    values: NDArray[np.float64],
    valid: NDArray[np.bool_],
    *,
    order: NDArray[np.intp],
    position: NDArray[np.intp],
    chunks: Callable[[int], Iterable[slice]],
    checkpoint: Callable[[], None],
    advance: Callable[[float], None],
) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    """행 순서의 입력 열을 받아 행 순서의 (값, 유효) 열을 낸다. `chunks(n)` 은 [0, n) 을 나눈다."""
    rows = values.shape[0]
    window = node.window
    far = node_read_window(node).far
    ordered = values[order]
    result = np.zeros(rows, np.float64)
    ok = np.zeros(rows, bool)
    # 정렬 위치 j(≥ far)의 창은 [j − far, j − far + window). t = j − far 로 센다
    candidates = rows - far
    if candidates > 0:
        numeric = np.concatenate(([0], np.cumsum(valid[order], dtype=np.int64)))
        with np.errstate(all="ignore"):
            for chunk in chunks(candidates):
                first = ordered[chunk]
                target = slice(chunk.start + far, chunk.stop + far)
                # 시작이 같은 종목 안이고(종목 안 위치 ≥ far) 창 칸이 모두 숫자다
                ok[target] = (position[order[target]] >= far) & (
                    numeric[chunk.start + window : chunk.stop + window] - numeric[chunk] == window
                )
                if node.operator in _REPLACES:
                    result[target] = _extremum(
                        _REPLACES[node.operator], ordered, chunk, window, checkpoint
                    )
                else:
                    last = ordered[chunk.start + window - 1 : chunk.stop + window - 1]
                    if node.operator is TimeSeriesOperator.MOMENTUM:
                        # first == 0(−0.0 포함)이면 None. 객체 경로와 같은 순서로 나누고 뺀다
                        ok[target] &= first != 0
                        result[target] = last / first - 1
                    else:
                        result[target] = last - first
                advance(chunk.stop / candidates)
    values_out = np.empty(rows, np.float64)
    valid_out = np.empty(rows, bool)
    values_out[order] = result
    valid_out[order] = ok
    return values_out, valid_out


def _extremum(
    replaces: Callable[[np.ndarray, np.ndarray], np.ndarray],
    ordered: NDArray[np.float64],
    chunk: slice,
    window: int,
    checkpoint: Callable[[], None],
) -> NDArray[np.float64]:
    """오래된 칸부터 누적한다. 새 칸이 엄격히 작을(클) 때만 바꿔 같으면 앞 원소를 유지한다."""
    acc = ordered[chunk].copy()
    for offset in range(1, window):
        checkpoint()
        item = ordered[chunk.start + offset : chunk.stop + offset]
        acc = np.where(replaces(item, acc), item, acc)
    return acc
