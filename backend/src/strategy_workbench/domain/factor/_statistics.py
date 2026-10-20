from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from math import isqrt
from statistics import mean, pstdev

import numpy as np
from numpy.typing import NDArray


def cross_sectional_rank(values: Sequence[float]) -> list[float]:
    """횡단면 백분위 순위를 0~1 로 돌려준다(입력 순서 그대로).

    동점은 평균 순위를 공유하므로 입력 순서가 결과를 바꾸지 않는다. 표본이 1개면 분모가 0 이 되지
    않도록 1 로 막아 유일한 값이 0.0 이 된다 — 횡단면에 비교 대상이 없으면 순위 정보도 없다.

    `CrossSectionalOperator.RANK`·`GroupOperator.RANK` 와 `signal.normalization: rank` 가 같은
    정의를 쓴다(P2-04). 공식은 구간 커널 `segment_rank` 하나가 소유하고, 이 함수는 입력 전체를
    구간 하나로 감싼다(columnar-tape spec D5). 값은 유한한 float 여야 한다(NaN 은 `ValueError`).
    """
    array = np.asarray(values, dtype=np.float64)
    ranks = segment_rank(array, np.array([0, array.shape[0]], np.intp))
    return ranks.tolist()


def cross_sectional_zscore(values: Sequence[float]) -> list[float]:
    """횡단면 표준화 값을 돌려준다(입력 순서 그대로).

    모집단 표준편차(`pstdev`)를 쓴다. 분산이 0 이면(표본 1개 포함) 편차 정보가 없으므로 전부
    0.0 이다 — 0 으로 나누는 대신 "정보 없음"을 중립값으로 표현한다.

    `CrossSectionalOperator.ZSCORE` 와 `signal.normalization: zscore` 가 같은 정의를 쓴다(P2-04).
    공식은 구간 커널 `segment_zscore` 하나가 소유하고, 이 함수는 입력 전체를 구간 하나로 감싼다
    (columnar-tape spec D5).
    """
    if not values:
        return []
    array = np.asarray(values, dtype=np.float64)
    scores = segment_zscore(array, np.array([0, array.shape[0]], np.intp))
    return scores.tolist()


def quantile(ordered: list[float], probability: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - lower_index
    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


# ---------------------------------------------------------------------------
# 정확 평균 커널(columnar-tape spec D3 mean 행, T3-03)
#
# 객체 평가기는 칸마다 `statistics.mean(list[float])` 을 부른다. CPython 3.11 의 이 함수는 원소를
# 정확한 유리수로 더해 원소 수로 나눈 값을 한 번만 반올림한다(가장 가까운 float, 동점은 짝수 쪽).
# 그래서 합 순서와 무관한 수학적 정의이고, 아래 커널은 그 값을 벡터로 "증명하며" 재현한다.
#
# 1. 합: +0.0 에서 시작해 TwoSum(Knuth)으로 더한다. 각 덧셈의 반올림 오차 e 는 정확히 표현되므로
#    W(정확한 합) = s + Σe 가 항등식이다. c = fl(Σe), a = fl(Σ|e|) 를 함께 나른다.
#    |c − Σe| ≤ γ_{T−1}·Σ|e| ≤ 2·T·u·a (T = TwoSum 횟수, u = 2^-53, T·u ≪ 1). 창은 T ≤ n + 1,
#    구간은 블록 두 단계라 T ≤ 2n 이므로 두 형태 모두 E_sum = 4·(n + 2)·u·a 로 덮는다.
# 2. 후보: q0 = fl(fl(s + c)/n). Dekker TwoProduct(numpy 에 fma 가 없다)로 q0·n = p + pe 를 정확히
#    쪼개 잔차 R = W − q0·n 을 구하고, q = fl(q0 + fl(R/n)) 로 한 번 고친다.
# 3. 인증: q 에 대해 잔차 R 을 다시 구한다. (d, de) = TwoSum(s, −p) 일 때
#    r = fl(d + fl(fl(de − pe) + c)). 정확한 R = d + de − pe + Σe 이므로
#    |R − r| ≤ E_sum + E_res, E_res = 3·u·(|de| + |pe| + |c| + |r|).
#    반올림 경계 (q + succ(q))/2 · (q + pred(q))/2 까지의 거리를 잔차 단위로 재면
#    n·(succ(q) − q)/2 − R 과 n·(q − pred(q))/2 + R 이다(2의 거듭제곱 경계는 아래 간격이 절반이다).
#    두 거리가 모두 4·(E_sum + E_res) + 2^-1060 보다 **크면** W/n 은 q 의 반올림 칸 안쪽에 있어
#    statistics.mean 과 같은 q 다. 같거나 작으면(정확한 중점 포함 — 동점 짝수 규칙은 계산하지
#    않는다) 그 칸만 `statistics.mean` 으로 정한다. 4배는 경계식 자체의 float 계산 오차다.
#    2^-1060 은 부분정규 크기에서 상대 오차 모형이 깨지는 두 곳의 절대 오차(각 ≤ 2^-1075)를
#    덮는다: n 이 홀수이고 간격이 2^-1074 이면 n·gap/2(= n·2^-1075)가 표현되지 않아 반올림되고,
#    E_res 의 곱(u·|x|)도 부분정규 영역에서는 상대 오차가 아니라 절대 오차로 반올림된다.
# 4. 정확 경로로 보내는 칸: 비유한값(합·후보가 inf/NaN — 비교가 거짓이라 인증되지 않는다),
#    |q| > 1e300(TwoProduct 분할 상수 2^27+1 을 곱하면 |q| > 2^997 ≈ 1.3e300 에서 넘친다. 넘쳐도
#    NaN 이 되어 인증되지 않지만 가드로 미리 막는다), 0 이 아닌 |q| < 1e-290, q == 0.
#    1e-290 가드는 정확성에 필요한 경계가 아니라 보수적 여유다. n 이 정수(< 2^26 이면 분할이
#    (n, 0))라 q·n 의 Dekker 분해는 부분정규 크기에서도 정확하고(#584 리뷰 실측 30만 건), 작은
#    크기에서 정확하지 않은 n·gap/2 와 E_res 의 상대 오차 가정은 위 2^-1060 이 덮는다.
# 5. s == 0 이고 a == 0 이면 모든 e 가 0 이라 W 가 정확히 0 이다. statistics.mean 은 정확한 0 을
#    +0.0 으로 돌려주므로(−0.0 만 있는 칸도 `mean([-0.0]) == +0.0`) 그 칸은 +0.0 이다. +0.0 에서
#    시작한 TwoSum 은 −0.0 을 만들지 않는다(−0.0 + −0.0 만 −0.0 이다).
#
# 객체 경로는 `_as_number` 가 float() 로 바꾼 값만 mean 에 넘긴다 — int 가 들어가는 경로는 없다.
# 그래서 커널 입력은 float64 열 하나이고, 정수 값 float 도 같은 정의로 다룬다.
# ---------------------------------------------------------------------------

_UNIT_ROUNDOFF = 2.0**-53
_SPLITTER = 2.0**27 + 1.0
_LARGEST_CERTIFIED = 1e300
_SMALLEST_CERTIFIED = 1e-290
_UNDERFLOW_SLACK = 2.0**-1060
# 원소 단위 벡터 연산은 이 칸 수마다 checkpoint 를 부른다(spec D7, `_panel_evaluation._CHUNK_ROWS`)
_KERNEL_CHUNK = 1 << 18
# 인증식 조각. 칸마다 연산이 80개 남짓이라 2^16 칸(0.5MB 배열)이 2^18 칸보다 칸당 1.5배 빠르다
_CERTIFY_CHUNK = 1 << 16
# 정확 경로는 256칸 또는 이 원소 수마다 checkpoint 를 부른다. 큰 그룹은 칸 하나가 길다
_EXACT_CHECK_CELLS = 256
_EXACT_CHECK_ELEMENTS = 1 << 15
_MIN_SEGMENT_BLOCK = 64
# 구간 정렬 커널(rank·winsorize·median)의 조각 원소 수. 정렬과 동점 묶기가 원소당 연산이 많아 2^18
# 원소 조각은 4.2M행에서 checkpoint 간격이 60ms 가까이 된다(spec D7)
_SORT_CHUNK = 1 << 16

_Floats = NDArray[np.float64]
_Bools = NDArray[np.bool_]


def _no_checkpoint() -> None:
    return None


@dataclass(frozen=True, eq=False)
class ExactMeans:
    """칸마다의 평균. `exact_path` 가 참인 칸은 `statistics.mean` 이 정했다."""

    values: _Floats
    exact_path: _Bools


def window_mean(
    values: _Floats,
    window: int,
    *,
    starts: slice | None = None,
    wanted: _Bools | None = None,
    checkpoint: Callable[[], None] = _no_checkpoint,
) -> ExactMeans:
    """시작 위치 t 마다 `statistics.mean(values[t : t + window].tolist())` 와 비트가 같은 값.

    Args:
        values: 1차원 float64 열. 창은 이 순서 그대로의 연속 구간이다(종목 우선 정렬 위치).
        window: 창 길이(≥ 1).
        starts: 계산할 시작 위치 구간. 기본은 전체 `[0, len − window]`.
        wanted: `starts` 길이의 마스크. 거짓인 칸은 값을 쓰지 않으므로 계산하지 않고 0.0 이다
            (종목 경계를 넘거나 숫자가 아닌 칸이 든 창 — 그 칸의 원소는 의미가 없다).
        checkpoint: 협조적 취소 확인. 창 offset 반복·청크·정확 경로 256칸마다 부른다.

    Returns:
        `starts` 길이의 값과 정확 경로 표시.
    """
    low, high = _window_range(values, window, starts, wanted, "window mean")
    count = high - low
    means = np.zeros(count, np.float64)
    exact = np.zeros(count, bool)
    for first in range(low, high, _KERNEL_CHUNK):
        checkpoint()
        last = min(first + _KERNEL_CHUNK, high)
        part = slice(first - low, last - low)
        total, error, magnitude = _window_sums(values, window, first, last, checkpoint)
        _settle(
            last - first,
            _mean_certifier(total, error, magnitude, np.full(last - first, float(window))),
            None if wanted is None else wanted[part],
            lambda cell, origin=first: values[origin + cell : origin + cell + window],
            mean,
            checkpoint,
            out=means[part],
            exact=exact[part],
        )
    return ExactMeans(means, exact)


def segment_mean(
    values: _Floats,
    offsets: NDArray[np.intp],
    *,
    checkpoint: Callable[[], None] = _no_checkpoint,
) -> ExactMeans:
    """구간 g 마다 `statistics.mean(values[offsets[g] : offsets[g + 1]].tolist())` 와 같은 비트.

    횡단면·그룹 평균(demean·neutralize·zscore 중심)이 쓸 형태다. `statistics.mean` 은 정확한
    유리수 합을 한 번 반올림하므로 구간 안 원소 순서는 결과를 바꾸지 않는다. 빈 구간은 평균이
    정의되지 않아(`statistics.mean` 은 예외) NaN 이고 정확 경로 표시는 거짓이다.

    Args:
        values: 1차원 float64 열.
        offsets: 길이 G + 1 의 비감소 정수 열. `offsets[0] == 0`, `offsets[-1] == len(values)`.
        checkpoint: 협조적 취소 확인. 블록 offset 반복·청크·정확 경로 256칸마다 부른다.
    """
    bounds = _segment_bounds(values, offsets, "segment mean")
    lengths = np.diff(bounds)
    total, error, magnitude = _segment_sums(values, bounds, lengths, checkpoint)
    groups = lengths.shape[0]
    means = np.zeros(groups, np.float64)
    exact = np.zeros(groups, bool)
    counts = lengths.astype(np.float64)
    nonempty = lengths > 0
    for first in range(0, groups, _KERNEL_CHUNK):
        checkpoint()
        part = slice(first, min(first + _KERNEL_CHUNK, groups))
        _settle(
            part.stop - part.start,
            _mean_certifier(total[part], error[part], magnitude[part], counts[part]),
            nonempty[part],
            lambda cell, origin=first: values[bounds[origin + cell] : bounds[origin + cell + 1]],
            mean,
            checkpoint,
            out=means[part],
            exact=exact[part],
        )
    means[~nonempty] = np.nan
    return ExactMeans(means, exact)


def _window_range(
    values: _Floats, window: int, starts: slice | None, wanted: _Bools | None, kernel: str
) -> tuple[int, int]:
    """창 커널 입력을 검사하고 시작 위치 구간 [low, high) 를 돌려준다."""
    _require_floats(values, "values")
    if window < 1:
        raise ValueError(f"{kernel} needs window >= 1 — window={window}")
    limit = max(values.shape[0] - window + 1, 0)
    span = slice(0, limit) if starts is None else starts
    low = 0 if span.start is None else span.start
    high = limit if span.stop is None else span.stop
    if span.step not in (None, 1) or not 0 <= low <= high <= limit:
        raise ValueError(
            f"{kernel} starts must be a contiguous range inside the series — "
            f"starts={span!r} rows={values.shape[0]} window={window} limit={limit}"
        )
    count = high - low
    if wanted is not None and (wanted.dtype != np.bool_ or wanted.shape != (count,)):
        raise ValueError(
            f"{kernel} wanted mask must be a bool array as long as starts — "
            f"wanted_shape={wanted.shape} wanted_dtype={wanted.dtype} starts={count}"
        )
    return low, high


def _segment_bounds(values: _Floats, offsets: NDArray[np.intp], kernel: str) -> NDArray[np.intp]:
    """구간 커널 입력을 검사하고 intp 경계 열을 돌려준다."""
    _require_floats(values, "values")
    bounds = np.asarray(offsets)
    if (
        bounds.ndim != 1
        or bounds.shape[0] < 1
        or not np.issubdtype(bounds.dtype, np.integer)
        or int(bounds[0]) != 0
        or int(bounds[-1]) != values.shape[0]
        or bool(np.any(np.diff(bounds) < 0))
    ):
        raise ValueError(
            f"{kernel} offsets must be non-decreasing integers from 0 to len(values) — "
            f"offsets_shape={bounds.shape} dtype={bounds.dtype} rows={values.shape[0]}"
        )
    return bounds.astype(np.intp)


def _require_floats(values: np.ndarray, name: str) -> None:
    if values.ndim != 1 or values.dtype != np.float64:
        raise TypeError(
            f"exact statistics kernel needs a 1-D float64 array — {name} shape={values.shape} "
            f"dtype={values.dtype}"
        )


def _two_sum(left: _Floats, right: _Floats) -> tuple[_Floats, _Floats]:
    """Knuth TwoSum: total + error == left + right 가 정확하다(넘침이 없으면)."""
    total = left + right
    virtual = total - left
    error = (left - (total - virtual)) + (right - virtual)
    return total, error


def _split(value: _Floats) -> tuple[_Floats, _Floats]:
    """Veltkamp 분할: value == high + low, 두 부분은 각각 26비트 이하다."""
    scaled = _SPLITTER * value
    high = scaled - (scaled - value)
    return high, value - high


def _two_product(left: _Floats, right: _Floats) -> tuple[_Floats, _Floats]:
    """Dekker TwoProduct: product + error == left × right (넘침·부분정규 오차항이 없으면 정확)."""
    product = left * right
    left_high, left_low = _split(left)
    right_high, right_low = _split(right)
    error = left_low * right_low - (
        ((product - left_high * right_high) - left_low * right_high) - left_high * right_low
    )
    return product, error


def _residual_terms(
    total: _Floats, error: _Floats, candidate: _Floats, count: _Floats
) -> tuple[_Floats, _Floats, _Floats]:
    """(W − candidate·count 의 근사 r, de, pe). (p, pe) = TwoProduct, (d, de) = TwoSum(s, −p)."""
    product, product_error = _two_product(candidate, count)
    difference, difference_error = _two_sum(total, -product)
    residual = difference + ((difference_error - product_error) + error)
    return residual, difference_error, product_error


def _residual(
    total: _Floats, error: _Floats, candidate: _Floats, count: _Floats
) -> tuple[_Floats, _Floats]:
    """(잔차 r, r 을 만든 세 덧셈의 반올림 오차 상한 E_res)."""
    residual, difference_error, product_error = _residual_terms(total, error, candidate, count)
    rounding = (3.0 * _UNIT_ROUNDOFF) * (
        np.abs(difference_error) + np.abs(product_error) + np.abs(error) + np.abs(residual)
    )
    return residual, rounding


def _corrected(total: _Floats, error: _Floats, candidate: _Floats, count: _Floats) -> _Floats:
    """한 번 고친 후보 fl(q0 + fl(R/n)). 오차 상한은 인증 단계(`_residual`)에서만 잰다."""
    residual, _, _ = _residual_terms(total, error, candidate, count)
    return candidate + residual / count


def _certify(
    total: _Floats, error: _Floats, magnitude: _Floats, count: _Floats
) -> tuple[_Floats, _Bools, _Bools]:
    """(후보 q, 인증됨, 정확한 0). 식은 위 머리 주석 2~5."""
    with np.errstate(all="ignore"):
        candidate = _corrected(total, error, (total + error) / count, count)
        residual, rounding = _residual(total, error, candidate, count)
        summation = (4.0 * _UNIT_ROUNDOFF) * (count + 2.0) * magnitude
        bound = 4.0 * (summation + rounding) + _UNDERFLOW_SLACK
        half = 0.5 * count
        above = (np.nextafter(candidate, np.inf) - candidate) * half - residual
        below = (candidate - np.nextafter(candidate, -np.inf)) * half + residual
        size = np.abs(candidate)
        certified = (
            (size >= _SMALLEST_CERTIFIED)
            & (size <= _LARGEST_CERTIFIED)
            & (above > bound)
            & (below > bound)
        )
        zero = (total == 0.0) & (magnitude == 0.0)
    return candidate, certified, zero


_Certifier = Callable[[slice], tuple[_Floats, _Bools, _Bools]]


def _mean_certifier(
    total: _Floats, error: _Floats, magnitude: _Floats, count: _Floats
) -> _Certifier:
    return lambda cells: _certify(total[cells], error[cells], magnitude[cells], count[cells])


def _settle(
    cells_total: int,
    certify: _Certifier,
    wanted: _Bools | None,
    members: Callable[[int], _Floats],
    exact_value: Callable[[list[float]], float],
    checkpoint: Callable[[], None],
    *,
    out: _Floats,
    exact: _Bools,
) -> None:
    """인증된 칸은 후보를, 정확한 0 은 +0.0 을, 나머지 원하는 칸은 `exact_value` 를 쓴다.

    `certify(조각)` 은 그 조각의 (후보, 인증됨, 정확한 0) 이고 `exact_value` 는 객체 경로와 같은
    `statistics` 함수다(원소 list 를 그대로 넘긴다).
    """
    pending = np.zeros(cells_total, bool)
    # 인증식은 칸마다 연산이 많아 캐시에 드는 조각으로 나눠 계산하고 조각마다 checkpoint 한다
    for low in range(0, cells_total, _CERTIFY_CHUNK):
        checkpoint()
        part = slice(low, min(low + _CERTIFY_CHUNK, cells_total))
        candidate, certified, zero = certify(part)
        if wanted is not None:
            certified &= wanted[part]
            zero &= wanted[part]
        out[part] = np.where(certified, candidate, 0.0)
        pending[part] = ~(certified | zero)
    if wanted is not None:
        pending &= wanted
    cells = 0
    elements = 0
    for cell in np.flatnonzero(pending).tolist():
        items = members(cell)
        out[cell] = exact_value(items.tolist())
        exact[cell] = True
        cells += 1
        elements += items.shape[0]
        if cells >= _EXACT_CHECK_CELLS or elements >= _EXACT_CHECK_ELEMENTS:
            checkpoint()
            cells = elements = 0


def _window_sums(
    values: _Floats, window: int, first: int, last: int, checkpoint: Callable[[], None]
) -> tuple[_Floats, _Floats, _Floats]:
    """시작 t ∈ [first, last) 창의 (s, c, a).

    [first, …) 를 창 길이 블록으로 자르면 창 하나는 블록 i 의 접미(offset o 부터)와 블록 i + 1 의
    접두(o 칸)다. 블록마다 접두·접미 합을 한 번씩 누적해(창 길이 반복 × 블록 수 벡터) 창마다
    둘을 TwoSum 으로 잇는다. 각 창의 합은 자기 원소만 담는다 — 다른 종목의 큰 값이나 NaN 이
    섞이지 않는다.
    """
    rows, block, offset_in_block = _window_rows(values, window, first, last)
    blocks = rows.shape[1]
    prefix = [np.zeros((window, blocks), np.float64) for _ in range(3)]
    suffix = [np.zeros((window, blocks), np.float64) for _ in range(3)]
    with np.errstate(all="ignore"):
        state = _zero_state(blocks)
        # prefix[·][o] = 블록 앞 o 칸의 합(o = 0 은 0)
        for offset in range(1, window):
            checkpoint()
            state = _accumulate(state, rows[offset - 1])
            for target, part in zip(prefix, state, strict=True):
                target[offset] = part
        state = _zero_state(blocks)
        # suffix[·][o] = 블록 o 칸부터 끝까지의 합
        for offset in range(window - 1, -1, -1):
            checkpoint()
            state = _accumulate(state, rows[offset])
            for target, part in zip(suffix, state, strict=True):
                target[offset] = part
        tail = offset_in_block * blocks + block
        head = tail + 1
        tail_total, tail_error, tail_magnitude = (part.reshape(-1)[tail] for part in suffix)
        head_total, head_error, head_magnitude = (part.reshape(-1)[head] for part in prefix)
        total, joint = _two_sum(tail_total, head_total)
        error = (tail_error + head_error) + joint
        magnitude = (tail_magnitude + head_magnitude) + np.abs(joint)
    return total, error, magnitude


def _window_rows(
    values: _Floats, window: int, first: int, last: int
) -> tuple[_Floats, NDArray[np.intp], NDArray[np.intp]]:
    """시작 t ∈ [first, last) 창을 덮는 창 길이 블록 격자와 시작마다의 (블록, 블록 안 offset).

    행 = 블록 안 offset, 열 = 블록이라 한 offset 의 모든 블록이 연속 메모리다. 창이 끝 블록
    다음까지 닿도록 블록을 하나 더 두고, 열 끝을 넘는 칸은 0.0 이다(그 칸을 쓰는 창은 열 밖이라
    원하지 않는다).
    """
    count = last - first
    blocks = (count - 1) // window + 2
    stop = min(first + blocks * window, values.shape[0])
    grid = np.zeros(blocks * window, np.float64)
    grid[: stop - first] = values[first:stop]
    rows = np.ascontiguousarray(grid.reshape(blocks, window).T)
    block, offset_in_block = np.divmod(np.arange(count), window)
    return rows, block, offset_in_block


@dataclass(frozen=True, eq=False)
class _SegmentBlocks:
    """구간을 길이 `block` 조각으로 자른 배치. 조각은 길이 내림차순으로 정렬해 놓는다.

    `sorted_starts[k]` 는 k 번째(정렬 뒤) 조각의 시작 위치, `sorted_owners[k]` 는 그 조각의
    구간, `active_blocks[o]` 는 offset o 에서 아직 원소가 남은 조각 수(앞쪽 연속), `position[b]` 는
    원래(구간 순서) 조각 b 의 정렬 뒤 자리, `first_block[g]` 는 구간 g 의 첫 조각의 원래 번호다.
    2단계는 조각 수 내림차순 구간 순서 `group_order` 와 j 번째 조각이 남은 구간 수
    `active_groups[j]` 로 돈다.
    """

    block: int
    sorted_starts: NDArray[np.intp]
    sorted_owners: NDArray[np.intp]
    active_blocks: NDArray[np.intp]
    position: NDArray[np.intp]
    first_block: NDArray[np.intp]
    group_order: NDArray[np.intp]
    active_groups: NDArray[np.intp]


def _segment_blocks(bounds: NDArray[np.intp], lengths: NDArray[np.intp]) -> _SegmentBlocks:
    groups = lengths.shape[0]
    longest = int(lengths.max()) if groups else 0
    block = max(_MIN_SEGMENT_BLOCK, isqrt(max(longest - 1, 0)) + 1)
    per_group = (lengths + block - 1) // block
    blocks = int(per_group.sum())
    owner = np.repeat(np.arange(groups), per_group)
    first_block = np.cumsum(per_group) - per_group
    within = np.arange(blocks) - first_block[owner]
    starts = bounds[:-1][owner] + within * block
    sizes = np.minimum(block, bounds[1:][owner] - starts)
    # 길이 내림차순으로 놓으면 offset k 에서 도는 조각은 앞쪽 연속 구간이다
    order = np.argsort(-sizes, kind="stable")
    position = np.empty(blocks, np.intp)
    position[order] = np.arange(blocks)
    return _SegmentBlocks(
        block=block,
        sorted_starts=starts[order],
        sorted_owners=owner[order],
        active_blocks=_active_counts(sizes, block),
        position=position,
        first_block=first_block,
        group_order=np.argsort(-per_group, kind="stable"),
        active_groups=_active_counts(per_group, int(per_group.max()) if groups else 0),
    )


def _segment_sums(
    values: _Floats,
    bounds: NDArray[np.intp],
    lengths: NDArray[np.intp],
    checkpoint: Callable[[], None],
) -> tuple[_Floats, _Floats, _Floats]:
    """구간마다 (s, c, a). 구간을 길이 B 블록으로 자르고 블록 합 → 구간 합 두 단계로 더한다.

    B = max(64, ⌈√최장⌉) 라 반복 수는 단계마다 B 이하·최장/B 이하이고, 활성 블록·구간만 앞쪽에
    모아 벡터 길이를 줄인다. 합 순서는 인증 식에 들지 않는다(오차 상한은 순서와 무관하다).
    """
    groups = lengths.shape[0]
    layout = _segment_blocks(bounds, lengths)
    starts = layout.sorted_starts
    block_state = _zero_state(starts.shape[0])
    with np.errstate(all="ignore"):
        # 1단계: 블록 합
        for offset in range(layout.block):
            _accumulate_prefix(
                block_state,
                layout.active_blocks[offset],
                lambda part, k=offset: values[starts[part] + k],
                checkpoint,
            )
        # 2단계: 구간마다 블록 합을 블록 순서대로 잇는다
        group_state = _zero_state(groups)
        for index in range(layout.active_groups.shape[0]):
            _join_prefix(
                group_state,
                block_state,
                layout.active_groups[index],
                lambda part, j=index: layout.position[
                    layout.first_block[layout.group_order[part]] + j
                ],
                checkpoint,
            )
    result = [np.empty(groups, np.float64) for _ in range(3)]
    for target, part in zip(result, group_state, strict=True):
        target[layout.group_order] = part
    return result[0], result[1], result[2]


def _zero_state(size: int) -> tuple[_Floats, _Floats, _Floats]:
    return (
        np.zeros(size, np.float64),
        np.zeros(size, np.float64),
        np.zeros(size, np.float64),
    )


def _accumulate(
    state: tuple[_Floats, _Floats, _Floats], item: _Floats
) -> tuple[_Floats, _Floats, _Floats]:
    total, error, magnitude = state
    total, step = _two_sum(total, item)
    return total, error + step, magnitude + np.abs(step)


def _active_counts(sizes: NDArray[np.intp], span: int) -> NDArray[np.intp]:
    """k ∈ [0, span) 마다 크기가 k 보다 큰 항목 수."""
    histogram = np.bincount(sizes, minlength=span + 1)
    return (sizes.shape[0] - np.cumsum(histogram))[:span].astype(np.intp)


def _accumulate_prefix(
    state: tuple[_Floats, _Floats, _Floats],
    active: int,
    items: Callable[[slice], _Floats],
    checkpoint: Callable[[], None],
) -> None:
    """state 의 앞 `active` 칸에 items(구간) 를 더한다. 청크마다 checkpoint."""
    total, error, magnitude = state
    for low in range(0, int(active), _KERNEL_CHUNK):
        checkpoint()
        part = slice(low, min(low + _KERNEL_CHUNK, int(active)))
        total[part], step = _two_sum(total[part], items(part))
        error[part] += step
        magnitude[part] += np.abs(step)


def _join_prefix(
    state: tuple[_Floats, _Floats, _Floats],
    blocks: tuple[_Floats, _Floats, _Floats],
    active: int,
    positions: Callable[[slice], NDArray[np.intp]],
    checkpoint: Callable[[], None],
) -> None:
    """state 의 앞 `active` 칸에 블록 합(s, c, a)을 잇는다. c·a 에 블록 값과 이음 오차를 더한다."""
    total, error, magnitude = state
    block_total, block_error, block_magnitude = blocks
    for low in range(0, int(active), _KERNEL_CHUNK):
        checkpoint()
        part = slice(low, min(low + _KERNEL_CHUNK, int(active)))
        index = positions(part)
        total[part], step = _two_sum(total[part], block_total[index])
        error[part] = (error[part] + block_error[index]) + step
        magnitude[part] = (magnitude[part] + block_magnitude[index]) + np.abs(step)


# ---------------------------------------------------------------------------
# 정확 모집단 표준편차 커널(columnar-tape spec D3 std 행, T3-04)
#
# 객체 평가기는 칸마다 `statistics.pstdev(list[float])` 을 부른다(ts std, cs zscore 의 편차).
# CPython 3.11 은 분산 V = Σ(x − μ)²/n 을 정확한 유리수로 구하고(`_ss`), `_float_sqrt_of_frac`
# 이 √V 를 한 번만 반올림한다(109비트 round-to-odd 정수 제곱근 뒤 int/int 참 나눗셈 — 가장
# 가까운 float, 동점은 짝수 쪽). 그래서 pstdev 가 후보 s 와 같을 필요충분조건은 lo² < V < hi²
# 이다(lo = (pred(s) + s)/2, hi = (s + succ(s))/2). V 가 lo² 나 hi² 와 같으면 동점이다.
# 객체 경로는 `_as_number` 가 float() 로 바꾼 유한값만 넘긴다(자식 노드가 노드마다 유한값
# 검사를 먼저 통과한다). 비유한값이 들어오면 정확 경로가 `statistics.pstdev` 를 그대로 불러
# 같은 결과(3.11 은 AttributeError)를 낸다.
#
# 1. 이동값: 칸 안의 원소 m 하나를 뺀다. 창은 그 창이 시작하는 블록의 마지막 원소(그 블록에서
#    시작하는 모든 창이 담는다), 구간은 첫 원소다. d = x − m 은 TwoSum 으로 (dh, dl) 로 정확히
#    나눈다. 어떤 m 이든 W = n²V = n·S2 − S1² (S1 = Σd, S2 = Σd²) 가 항등식이다. m 이 칸 안에
#    있으면 (m − μ)² ≤ nV 라 n·S2 / W = 1 + (μ − m)²/V ≤ n + 1 이다(상쇄가 n + 1 배 이하).
# 2. 합: S1 = Σdh + Σdl 은 dh 를 TwoSum 으로 (s1, c1) 에 더하고 dl 은 c1 에 더한다.
#    S2 = Σ(dh² + 2·dh·dl + dl²) 는 dh² 를 TwoProduct 로 (p, pe) 로 쪼개 p 를 TwoSum 으로
#    (s2, c2) 에, pe + fl(2·dh·dl) + fl(dl²) 를 c2 에 더한다. a_k 는 c_k 에 들어간 잎(TwoSum
#    오차·dl·pe·두 곱·블록 이음 오차)의 절대값 합이다. 잎 하나가 c_k 에 닿기까지 반올림은
#    곱 1번·원소 안 덧셈 3번·누적 n 번·이음 2번이라 n + 6 번(구간은 블록 이음이 블록마다 2번이라
#    2n + 6 번) 이하다. 그래서 |c_k − C_k| ≤ γ_{2n+6}·Σ|잎| ≤ e_k = 8·(n + 2)·u·a_k 다
#    (u = 2^-53, C_k = S_k − s_k 는 정확한 나머지, 8n + 16 ≥ 1.5·(2n + 6)).
# 3. 후보: (P2, P2e) = TwoProduct(n, s2), (P1, P1e) = TwoProduct(s1, s1), (D1, D1e) =
#    TwoSum(P2, −P1), base = fl((D1e + (P2e − P1e)) + (n·c2 − (2·s1·c1 + c1²))) 이면
#    W ≈ D1 + base 다. s0 = fl(√W / n) 에 잔차 R0 = W − n²·s0² 로 뉴턴을 한 번 해
#    s = fl(s0 + fl(R0 / (2·n²·s0))) 를 낸다.
# 4. 잔차: (Ps, Pse) = TwoProduct(s, s), (Q, Qe) = TwoProduct(n², Ps), (D2, D2e) =
#    TwoSum(D1, −Q) 이면 R = W − n²s² = D2 + D1e + D2e + P2e − P1e − Qe − n²·Pse + n·C2
#    − 2·s1·C1 − C1² 이다. R 을 한 float 로 줄이지 않고 high = D2 와
#    low = fl((base + D2e) − (Qe + fl(n²·Pse))) 두 조각으로 둔다. |R − high − low| 는
#    E_sum = n·e2 + (2|s1| + 2|c1| + e1)·e1 (c 를 C 대신 쓴 몫, |c1² − C1²| ≤ e1·(2|c1| + e1))
#    과 E_res = 8u·(|D1e| + |P2e| + |P1e| + |n·c2| + |2·s1·c1| + |c1²| + |D2e| + |Qe| +
#    |n²·Pse|) (작은 항 트리의 반올림이 잎마다 6번 이하라 γ_6 ≤ 8u) 의 합 이하다.
# 5. 경계: g = s − pred(s), h = succ(s) − s (2의 거듭제곱이면 g = h/2) 이면
#    W − n²·lo² = R + n²·(s·g) − n²·g²/4, n²·hi² − W = n²·(s·h) + n²·h²/4 − R 이다.
#    s·g·s·h 는 정확한 float 이고 n²·(s·g) 는 TwoProduct 로 (G, Ge), n²·g²/4 는 정확한 곱이다.
#    (z, ze) = TwoSum(G, high) 로 below = fl(z + fl(ze + fl(low + fl(Ge − n²·g²/4)))),
#    above 도 같은 모양이다. 안쪽 세 덧셈의 오차는 E_edge = 4u·(|ze| + |ye| + 2|low| + |Ge| +
#    |He| + n²·g²/4 + n²·h²/4) 이하이고, 마지막 덧셈의 오차는 결과 자신의 u 배라 인증을
#    뒤집지 않는다. 두 거리가 모두 4·(E_sum + E_res + E_edge) + 2^-1060 보다 **클 때만**
#    확정한다. 같거나 작으면(정확한 동점 포함 — 짝수 규칙은 계산하지 않는다) 그 칸만
#    `statistics.pstdev` 로 정한다. 4배는 상한식 자체의 float 오차다. 2^-1060 은 상한식의 곱
#    (u·잎)이 부분정규로 반올림될 때의 절대 오차(곱마다 ≤ 2^-1075, 열 개 남짓)를 덮는다. 가드
#    안에서 간격 n²·s·g 는 2^-917 이상이라 이 여유로 정확 경로에 가는 칸은 경계에서 간격의
#    2^-143 안쪽뿐이다.
# 6. 가드(정확 경로로 보내는 칸): 원소가 비유한이거나 |x| > 1e144 이거나 0 이 아닌
#    |x| < 1e-126 인 칸(그 원소를 NaN 으로 바꿔 합에 넣으면 비교가 거짓이라 인증되지 않는다),
#    후보가 1e-130 ≤ s ≤ 1e144 밖인 칸, n > 2^26 인 칸. 근거:
#    - 1e144 < 2^479 라 |d| < 2^480, d² < 2^960 이다. n ≤ 2^26 이면 S2 < 2^986 이고
#      n·S2·S1²·n²s² < 2^1012 라 넘치지 않으며, TwoProduct 의 분할(2^27 + 1 곱)이 넘치는
#      2^996 아래다.
#    - 0 이 아닌 원소가 1e-126 > 2^-419 이상이면 모든 원소·m 이 2^-471 의 배수라 dh·dl·s1·c1
#      도 그렇다. 0 이 아니면 |dh|, |dl| ≥ 2^-471 이고 dh²·2·dh·dl·dl² 이 2^-942 이상
#      정규수라 곱의 반올림이 상대 오차다. Dekker 곱의 중간값은 두 인수 ulp 의 곱(여기서
#      2^-1074 이상)의 배수이고 53비트 안이라 부분정규 크기에서도 정확하다
#      (TwoProduct(dh, dh)·(s1, s1)·(n, s2)).
#    - 후보 하한이 정확성에 필요한 곳은 2^-483 이다. s 의 지수 e 가 −483 이상이어야
#      g²/4 ≥ 2^(2e−108) 가 0 으로 사라지지 않고, s·g 와 TwoProduct(s, s)·(n², s·g) 의
#      중간값이 2^-1074 의 배수라 정확하다. 1e-130 > 2^-432 은 그 위에 둔 보수적 여유이고, 그
#      위에서는 s·g ≥ 2^-917, g²/4 ≥ 2^-972 가 정규수다.
#    - n ≤ 2^26 이면 n² 이 정확한 float 이고 γ_{2n+6} 이 작다.
# 7. 정확한 0: 칸의 모든 원소가 m 과 같으면(x − m == 0 ⇔ x == m, ±0 포함) V 가 정확히 0 이고
#    statistics.pstdev 는 +0.0 을 낸다(`pstdev([-0.0]) == +0.0`, n = 1 도 같다).
#    spread = Σ|x − m| 이 0 인 칸이 그렇다(음이 아닌 수의 합은 하나라도 0 이 아니면 0 이 되지
#    않고, NaN 이면 같음 비교가 거짓이다).
# ---------------------------------------------------------------------------

_DEVIATION_LARGEST = 1e144
_DEVIATION_SMALLEST = 1e-126
_DEVIATION_SMALLEST_RESULT = 1e-130
_DEVIATION_MAX_COUNT = float(1 << 26)
# 합 상태의 행: (s1, c1, a1) = Σd, (s2, c2, a2) = Σd², spread = Σ|x − m|
_FIRST, _FIRST_ERROR, _FIRST_SIZE, _SECOND, _SECOND_ERROR, _SECOND_SIZE, _SPREAD = range(7)
_DEVIATION_ROWS = 7


@dataclass(frozen=True, eq=False)
class ExactDeviations:
    """칸마다의 모집단 표준편차. `exact_path` 가 참인 칸은 `statistics.pstdev` 가 정했다."""

    values: _Floats
    exact_path: _Bools


def window_pstdev(
    values: _Floats,
    window: int,
    *,
    starts: slice | None = None,
    wanted: _Bools | None = None,
    checkpoint: Callable[[], None] = _no_checkpoint,
) -> ExactDeviations:
    """시작 위치 t 마다 `statistics.pstdev(values[t : t + window].tolist())` 와 비트가 같은 값.

    인자와 `wanted` 의 뜻은 `window_mean` 과 같다. 거짓인 칸은 계산하지 않고 0.0 이다.

    Returns:
        `starts` 길이의 값과 정확 경로 표시.
    """
    low, high = _window_range(values, window, starts, wanted, "window pstdev")
    count = high - low
    deviations = np.zeros(count, np.float64)
    exact = np.zeros(count, bool)
    for first in range(low, high, _KERNEL_CHUNK):
        checkpoint()
        last = min(first + _KERNEL_CHUNK, high)
        part = slice(first - low, last - low)
        sums = _window_deviation_sums(values, window, first, last, checkpoint)
        _settle(
            last - first,
            _deviation_certifier(sums, np.full(last - first, float(window))),
            None if wanted is None else wanted[part],
            lambda cell, origin=first: values[origin + cell : origin + cell + window],
            pstdev,
            checkpoint,
            out=deviations[part],
            exact=exact[part],
        )
    return ExactDeviations(deviations, exact)


def segment_pstdev(
    values: _Floats,
    offsets: NDArray[np.intp],
    *,
    checkpoint: Callable[[], None] = _no_checkpoint,
) -> ExactDeviations:
    """구간 g 마다 `statistics.pstdev(values[offsets[g] : offsets[g + 1]].tolist())` 와 같은 비트.

    횡단면 zscore 의 편차가 쓸 형태다. 분산은 정확한 유리수라 구간 안 원소 순서는 결과를 바꾸지
    않는다. 빈 구간은 표준편차가 정의되지 않아(`statistics.pstdev` 는 예외) NaN 이고 정확 경로
    표시는 거짓이다. `offsets` 의 조건은 `segment_mean` 과 같다.
    """
    bounds = _segment_bounds(values, offsets, "segment pstdev")
    lengths = np.diff(bounds)
    sums = _segment_deviation_sums(values, bounds, lengths, checkpoint)
    groups = lengths.shape[0]
    deviations = np.zeros(groups, np.float64)
    exact = np.zeros(groups, bool)
    counts = lengths.astype(np.float64)
    nonempty = lengths > 0
    for first in range(0, groups, _KERNEL_CHUNK):
        checkpoint()
        part = slice(first, min(first + _KERNEL_CHUNK, groups))
        _settle(
            part.stop - part.start,
            _deviation_certifier(sums[:, part], counts[part]),
            nonempty[part],
            lambda cell, origin=first: values[bounds[origin + cell] : bounds[origin + cell + 1]],
            pstdev,
            checkpoint,
            out=deviations[part],
            exact=exact[part],
        )
    deviations[~nonempty] = np.nan
    return ExactDeviations(deviations, exact)


def _guarded(values: _Floats) -> _Floats:
    """인증할 수 있는 크기(머리 주석 6)의 원소는 그대로, 나머지(비유한 포함)는 NaN."""
    size = np.abs(values)
    keep = (size <= _DEVIATION_LARGEST) & ((size >= _DEVIATION_SMALLEST) | (values == 0.0))
    return np.where(keep, values, np.nan)


def _accumulate_deviations(
    state: _Floats, items: _Floats, shift: _Floats, raw_shift: _Floats
) -> _Floats:
    """합 상태(7행)에 원소 하나씩을 더한 새 상태. `shift` 는 가드를 거친 m, `raw_shift` 는 원값."""
    high, low = _two_sum(_guarded(items), -shift)
    square, square_error = _two_product(high, high)
    cross = (2.0 * high) * low
    tail = low * low
    first, first_step = _two_sum(state[_FIRST], high)
    second, second_step = _two_sum(state[_SECOND], square)
    return np.stack(
        (
            first,
            state[_FIRST_ERROR] + (first_step + low),
            state[_FIRST_SIZE] + (np.abs(first_step) + np.abs(low)),
            second,
            state[_SECOND_ERROR] + (second_step + (square_error + (cross + tail))),
            state[_SECOND_SIZE]
            + (np.abs(second_step) + (np.abs(square_error) + (np.abs(cross) + np.abs(tail)))),
            state[_SPREAD] + np.abs(items - raw_shift),
        )
    )


def _join_deviations(left: _Floats, right: _Floats) -> _Floats:
    """두 합 상태를 잇는다. 이음 오차는 c 에, 그 절대값은 a 에 더한다."""
    rows: list[_Floats] = []
    for total, error, size in (
        (_FIRST, _FIRST_ERROR, _FIRST_SIZE),
        (_SECOND, _SECOND_ERROR, _SECOND_SIZE),
    ):
        joined, step = _two_sum(left[total], right[total])
        rows += [
            joined,
            (left[error] + right[error]) + step,
            (left[size] + right[size]) + np.abs(step),
        ]
    rows.append(left[_SPREAD] + right[_SPREAD])
    return np.stack(rows)


def _window_deviation_sums(
    values: _Floats, window: int, first: int, last: int, checkpoint: Callable[[], None]
) -> _Floats:
    """시작 t ∈ [first, last) 창의 합 상태(7행). 창 하나 = 블록 i 의 접미 + 블록 i + 1 의 접두.

    블록 i 에서 시작하는 창은 모두 블록 i 의 마지막 원소를 담으므로 그 원소를 m 으로 삼는다. 접미는
    블록 i, 접두는 블록 i + 1 의 원소에서 같은 m 을 빼 누적한다 — 그래서 두 합을 그대로 잇는다.
    """
    rows, block, offset_in_block = _window_rows(values, window, first, last)
    width = rows.shape[1] - 1
    own = rows[:, :width]
    following = rows[:, 1:]
    raw_shift = np.ascontiguousarray(own[window - 1])
    shift = _guarded(raw_shift)
    prefix = np.zeros((_DEVIATION_ROWS, window, width), np.float64)
    suffix = np.zeros((_DEVIATION_ROWS, window, width), np.float64)
    with np.errstate(all="ignore"):
        state = np.zeros((_DEVIATION_ROWS, width), np.float64)
        # prefix[:, o] = 다음 블록 앞 o 칸의 합(o = 0 은 0)
        for offset in range(1, window):
            checkpoint()
            state = _accumulate_deviations(state, following[offset - 1], shift, raw_shift)
            prefix[:, offset] = state
        state = np.zeros((_DEVIATION_ROWS, width), np.float64)
        # suffix[:, o] = 블록 o 칸부터 끝까지의 합
        for offset in range(window - 1, -1, -1):
            checkpoint()
            state = _accumulate_deviations(state, own[offset], shift, raw_shift)
            suffix[:, offset] = state
        index = offset_in_block * width + block
        tails = suffix.reshape(_DEVIATION_ROWS, -1)
        heads = prefix.reshape(_DEVIATION_ROWS, -1)
        joined = np.empty((_DEVIATION_ROWS, index.shape[0]), np.float64)
        # 모으기·이음은 2^16칸 조각마다 checkpoint 한다(창이 짧으면 2^18칸 전체가 한 번에 길다)
        for low in range(0, index.shape[0], _CERTIFY_CHUNK):
            checkpoint()
            part = index[low : low + _CERTIFY_CHUNK]
            joined[:, low : low + part.shape[0]] = _join_deviations(tails[:, part], heads[:, part])
        return joined


def _segment_deviation_sums(
    values: _Floats,
    bounds: NDArray[np.intp],
    lengths: NDArray[np.intp],
    checkpoint: Callable[[], None],
) -> _Floats:
    """구간마다 합 상태(7행). 배치는 `_segment_sums` 와 같고 m 은 구간의 첫 원소다."""
    groups = lengths.shape[0]
    layout = _segment_blocks(bounds, lengths)
    starts = layout.sorted_starts
    raw_shift = values[bounds[:-1][layout.sorted_owners]]
    shift = _guarded(raw_shift)
    block_state = np.zeros((_DEVIATION_ROWS, starts.shape[0]), np.float64)
    group_state = np.zeros((_DEVIATION_ROWS, groups), np.float64)
    with np.errstate(all="ignore"):
        # 1단계: 블록 합
        for offset in range(layout.block):
            active = int(layout.active_blocks[offset])
            for low in range(0, active, _KERNEL_CHUNK):
                checkpoint()
                part = slice(low, min(low + _KERNEL_CHUNK, active))
                block_state[:, part] = _accumulate_deviations(
                    block_state[:, part],
                    values[starts[part] + offset],
                    shift[part],
                    raw_shift[part],
                )
        # 2단계: 구간마다 블록 합을 블록 순서대로 잇는다
        for index in range(layout.active_groups.shape[0]):
            active = int(layout.active_groups[index])
            for low in range(0, active, _KERNEL_CHUNK):
                checkpoint()
                part = slice(low, min(low + _KERNEL_CHUNK, active))
                position = layout.position[layout.first_block[layout.group_order[part]] + index]
                group_state[:, part] = _join_deviations(
                    group_state[:, part], block_state[:, position]
                )
    result = np.empty((_DEVIATION_ROWS, groups), np.float64)
    result[:, layout.group_order] = group_state
    return result


def _deviation_certifier(sums: _Floats, count: _Floats) -> _Certifier:
    return lambda cells: _certify_deviation(sums[:, cells], count[cells])


def _deviation_residual(
    difference: _Floats, base: _Floats, candidate: _Floats, square: _Floats
) -> tuple[_Floats, _Floats, _Floats]:
    """W − n²·candidate² ≈ high + low(더하지 않은 두 조각)와 low 의 뒤쪽 잎 합. 머리 주석 4."""
    product, product_error = _two_product(candidate, candidate)
    scaled, scaled_error = _two_product(square, product)
    tail_error = square * product_error
    high, high_error = _two_sum(difference, -scaled)
    low = (base + high_error) - (scaled_error + tail_error)
    return high, low, np.abs(high_error) + np.abs(scaled_error) + np.abs(tail_error)


def _certify_deviation(sums: _Floats, count: _Floats) -> tuple[_Floats, _Bools, _Bools]:
    """(후보 s, 인증됨, 정확한 0). 식은 위 머리 주석 3~7."""
    first, first_error, first_size = sums[_FIRST], sums[_FIRST_ERROR], sums[_FIRST_SIZE]
    second, second_error, second_size = sums[_SECOND], sums[_SECOND_ERROR], sums[_SECOND_SIZE]
    with np.errstate(all="ignore"):
        square = count * count
        scaled_second, scaled_second_error = _two_product(count, second)
        first_square, first_square_error = _two_product(first, first)
        difference, difference_error = _two_sum(scaled_second, -first_square)
        scaled_error = count * second_error
        cross = (2.0 * first) * first_error
        error_square = first_error * first_error
        base = (difference_error + (scaled_second_error - first_square_error)) + (
            scaled_error - (cross + error_square)
        )
        base_size = (
            np.abs(difference_error)
            + np.abs(scaled_second_error)
            + np.abs(first_square_error)
            + np.abs(scaled_error)
            + np.abs(cross)
            + np.abs(error_square)
        )
        estimate = np.sqrt(difference + base) / count
        rough_high, rough_low, _ = _deviation_residual(difference, base, estimate, square)
        candidate = estimate + (rough_high + rough_low) / ((2.0 * square) * estimate)
        high, low, residual_size = _deviation_residual(difference, base, candidate, square)
        first_bound = (8.0 * _UNIT_ROUNDOFF) * (count + 2.0) * first_size
        second_bound = (8.0 * _UNIT_ROUNDOFF) * (count + 2.0) * second_size
        summation = (
            count * second_bound
            + ((2.0 * np.abs(first) + 2.0 * np.abs(first_error)) + first_bound) * first_bound
        )
        rounding = (8.0 * _UNIT_ROUNDOFF) * (base_size + residual_size)
        # 경계까지의 거리: n²·s·h 와 n²·s·g 는 TwoProduct 로, n²·h²/4·n²·g²/4 는 정확한 곱이다
        up_gap = np.nextafter(candidate, np.inf) - candidate
        down_gap = candidate - np.nextafter(candidate, -np.inf)
        up_edge, up_edge_error = _two_product(square, candidate * up_gap)
        down_edge, down_edge_error = _two_product(square, candidate * down_gap)
        up_quarter = square * (0.25 * (up_gap * up_gap))
        down_quarter = square * (0.25 * (down_gap * down_gap))
        above, above_error = _two_sum(up_edge, -high)
        below, below_error = _two_sum(down_edge, high)
        above = above + (above_error + ((up_edge_error + up_quarter) - low))
        below = below + (below_error + (low + (down_edge_error - down_quarter)))
        edges = (4.0 * _UNIT_ROUNDOFF) * (
            np.abs(above_error)
            + np.abs(below_error)
            + 2.0 * np.abs(low)
            + np.abs(up_edge_error)
            + np.abs(down_edge_error)
            + up_quarter
            + down_quarter
        )
        bound = 4.0 * (summation + rounding + edges) + _UNDERFLOW_SLACK
        certified = (
            (candidate >= _DEVIATION_SMALLEST_RESULT)
            & (candidate <= _DEVIATION_LARGEST)
            & (count <= _DEVIATION_MAX_COUNT)
            & (above > bound)
            & (below > bound)
        )
        zero = sums[_SPREAD] == 0.0
    return candidate, certified, zero


# ---------------------------------------------------------------------------
# 횡단면 구간 커널(columnar-tape spec D3 rank·winsorize·median 행, D5, T3-05)
#
# 구간 g 는 `values[offsets[g] : offsets[g + 1]]` 이고, 구간 안 순서는 객체 경로 동료 목록의
# 순서(관측 순서)다. 아래 커널은 그 목록을 받은 객체 경로 공식과 비트가 같다. 스칼라 API
# `cross_sectional_rank`·`cross_sectional_zscore` 는 입력 전체를 구간 하나로 감싸 같은 커널을
# 부른다.
#
# - 정렬: 객체 경로는 Python `sorted` 다(rank 는 키 (값, 입력 순서), winsorize·median 은 값만).
#   둘 다 안정 정렬이라 같은 값(`==`, −0.0 과 0.0 포함)은 입력 순서를 지킨다. 여기서는 구간 번호를
#   1차 키로 둔 `np.lexsort`(안정)로 구간 안에서만 정렬한다. 정렬 키는 `값 + 0.0` 이다 — −0.0 이
#   +0.0 이 되어 두 0 이 같은 키라, 정렬 구현이 부호 비트를 보더라도 같은 값은 입력 순서로 남는다.
#   값 자체는 원래 열에서 읽어 부호를 잃지 않는다.
# - NaN 은 `<` 로 순서가 정해지지 않아 `sorted` 결과가 입력 배치에 따라 달라진다. 객체 경로의 입력은
#   유한값 검사를 통과한 값이라 NaN 이 없고, 정렬 커널은 NaN 을 `ValueError` 로 거절한다. ±inf 는
#   순서가 정해져 그대로 다룬다.
# - 정렬은 구간을 모은 `_SORT_CHUNK` 원소 조각마다, 원소 연산은 `_KERNEL_CHUNK` 원소 조각마다 하고
#   조각마다 checkpoint 를 부른다. 구간 하나가 정렬 조각보다 길면 그 구간의 정렬 한 번 안에서는
#   부를 수 없다(정확 경로의 긴 칸과 같은 한계).
# ---------------------------------------------------------------------------


def segment_rank(
    values: _Floats,
    offsets: NDArray[np.intp],
    *,
    checkpoint: Callable[[], None] = _no_checkpoint,
) -> _Floats:
    """구간마다 백분위 순위 `(평균 순위 − 1) / max(n − 1, 1)`. 결과는 `values` 와 같은 자리다.

    평균 순위는 같은 값(`==`) 묶음이 정렬 위치 [p, e) 를 차지할 때 `(p + 1 + e) / 2` 다(정수 합을 한
    번 나눠 정확하다). 그 뒤 1 을 빼고 분모로 한 번 나눈다 — 객체 경로와 같은 IEEE 연산 순서다.
    """
    bounds = _segment_bounds(values, offsets, "segment rank")
    _require_ordered(values, "segment rank")
    ranks = np.empty(values.shape[0], np.float64)
    for low, high, local, _ in _segment_spans(bounds, checkpoint):
        part = values[low:high]
        order, segment = _segment_order(part, local)
        ordered = part[order]
        count = high - low
        # 정렬 위치마다 그 값 묶음의 시작·끝(반열린)을 구간 안 위치로 센다
        fresh = np.ones(count, bool)
        fresh[1:] = (segment[1:] != segment[:-1]) | (ordered[1:] != ordered[:-1])
        run_start = np.flatnonzero(fresh)
        run_stop = np.append(run_start[1:], count)
        run = np.cumsum(fresh) - 1
        first = local[:-1][segment]
        average = ((run_start[run] - first) + 1 + (run_stop[run] - first)).astype(np.float64) / 2.0
        denominator = np.maximum(np.diff(local) - 1, 1).astype(np.float64)[segment]
        ranks[low + order] = (average - 1.0) / denominator
    return ranks


def segment_zscore(
    values: _Floats,
    offsets: NDArray[np.intp],
    *,
    checkpoint: Callable[[], None] = _no_checkpoint,
) -> _Floats:
    """구간마다 `(x − mean) / pstdev`. 편차가 0 인 구간(원소 1개 포함)은 모두 +0.0 이다.

    중심과 편차는 정확 커널(`segment_mean`·`segment_pstdev`)이라 `statistics.mean`·`pstdev` 와
    비트가 같고, 빼기·나누기는 객체 경로와 같은 IEEE 연산 한 번씩이다.
    """
    bounds = _segment_bounds(values, offsets, "segment zscore")
    centers = segment_mean(values, bounds, checkpoint=checkpoint).values
    deviations = segment_pstdev(values, bounds, checkpoint=checkpoint).values
    scores = np.empty(values.shape[0], np.float64)
    for part, segment in _element_segments(bounds, checkpoint):
        deviation = deviations[segment]
        with np.errstate(all="ignore"):
            scaled = (values[part] - centers[segment]) / deviation
        scores[part] = np.where(deviation == 0.0, 0.0, scaled)
    return scores


def segment_demean(
    values: _Floats,
    offsets: NDArray[np.intp],
    *,
    checkpoint: Callable[[], None] = _no_checkpoint,
) -> _Floats:
    """구간마다 `x − statistics.mean(구간)`(횡단면 demean·그룹 neutralize). IEEE 빼기 한 번이다."""
    bounds = _segment_bounds(values, offsets, "segment demean")
    centers = segment_mean(values, bounds, checkpoint=checkpoint).values
    centered = np.empty(values.shape[0], np.float64)
    for part, segment in _element_segments(bounds, checkpoint):
        with np.errstate(all="ignore"):
            centered[part] = values[part] - centers[segment]
    return centered


def segment_winsorize(
    values: _Floats,
    offsets: NDArray[np.intp],
    lower_probability: float,
    upper_probability: float,
    *,
    checkpoint: Callable[[], None] = _no_checkpoint,
) -> _Floats:
    """구간마다 `min(max(x, lo), hi)`. lo·hi 는 안정 정렬한 구간의 `quantile` 이다.

    `quantile` 처럼 원소가 1개면 그 원소, 아니면 `p × (n − 1)` 의 정수부(`int()`)와 그다음 원소를
    `a × (1 − w) + b × w` 로 잇는다. Python `max(x, lo)` 는 `lo > x` 일 때만, `min(·, hi)` 는
    `hi < ·` 일 때만 바꿔 같으면 앞 인자가 남는다(±0 의 부호) — `np.maximum` 대신 `where` 로
    같게 한다.
    분위는 검증기(`factor.graph.winsor_bounds`)처럼 [0, 1] 이어야 한다.
    """
    bounds = _segment_bounds(values, offsets, "segment winsorize")
    _require_ordered(values, "segment winsorize")
    probabilities = (float(lower_probability), float(upper_probability))
    if not all(0.0 <= probability <= 1.0 for probability in probabilities):
        raise ValueError(
            "segment winsorize needs quantiles inside [0, 1] — "
            f"lower={lower_probability!r} upper={upper_probability!r}"
        )
    clipped = np.empty(values.shape[0], np.float64)
    for low, high, local, _ in _segment_spans(bounds, checkpoint):
        part = values[low:high]
        order, segment = _segment_order(part, local)
        ordered = part[order]
        lower, upper = (
            _segment_quantile(ordered, local, probability)[segment] for probability in probabilities
        )
        raised = np.where(lower > part, lower, part)
        clipped[low:high] = np.where(upper < raised, upper, raised)
    return clipped


def segment_median(
    values: _Floats,
    offsets: NDArray[np.intp],
    *,
    checkpoint: Callable[[], None] = _no_checkpoint,
) -> _Floats:
    """구간마다 `statistics.median` — 안정 정렬 뒤 홀수는 가운데 원소, 짝수는 `(a + b) / 2`.

    numpy `median` 은 정렬과 평균 방식이 달라 쓰지 않는다. 빈 구간은 중앙값이 정의되지 않아
    (`statistics.median` 은 예외) NaN 이다.
    """
    bounds = _segment_bounds(values, offsets, "segment median")
    _require_ordered(values, "segment median")
    medians = np.full(bounds.shape[0] - 1, np.nan)
    for low, high, local, first in _segment_spans(bounds, checkpoint):
        part = values[low:high]
        order, _ = _segment_order(part, local)
        ordered = part[order]
        sizes = np.diff(local)
        present = np.flatnonzero(sizes > 0)
        sizes = sizes[present]
        middle = local[:-1][present] + sizes // 2
        odd = sizes % 2 == 1
        upper = ordered[middle]
        lower = ordered[np.where(odd, middle, middle - 1)]
        with np.errstate(all="ignore"):
            even = (lower + upper) / 2.0
        medians[first + present] = np.where(odd, upper, even)
    return medians


def _require_ordered(values: _Floats, kernel: str) -> None:
    """정렬 커널 입력에 NaN 이 없는지 본다(머리 주석)."""
    missing = np.flatnonzero(np.isnan(values))
    if missing.size:
        raise ValueError(
            f"{kernel} cannot order NaN — first_index={int(missing[0])} count={missing.size} "
            f"rows={values.shape[0]}"
        )


def _segment_spans(
    bounds: NDArray[np.intp], checkpoint: Callable[[], None]
) -> Iterator[tuple[int, int, NDArray[np.intp], int]]:
    """구간을 차례로 모은 조각마다 (원소 시작, 원소 끝, 조각 안 구간 경계, 첫 구간 번호).

    조각은 원소가 `_SORT_CHUNK` 이하가 되게 구간을 모은다. 구간 하나가 그보다 길면 그 구간만
    담는다.
    """
    groups = bounds.shape[0] - 1
    first = 0
    while first < groups:
        checkpoint()
        low = int(bounds[first])
        last = int(np.searchsorted(bounds, low + _SORT_CHUNK, side="right")) - 1
        last = min(max(last, first + 1), groups)
        yield low, int(bounds[last]), bounds[first : last + 1] - low, first
        first = last


def _segment_order(
    part: _Floats, local: NDArray[np.intp]
) -> tuple[NDArray[np.intp], NDArray[np.intp]]:
    """조각 안에서 구간마다 값으로 안정 정렬한 순열과, 원소(같은 자리)마다의 조각 안 구간 번호."""
    segment = np.repeat(np.arange(local.shape[0] - 1), np.diff(local))
    # −0.0 + 0.0 은 +0.0 이라 두 0 이 같은 키다(머리 주석). 구간 번호가 1차 키다
    order = np.lexsort((part + 0.0, segment))
    return order.astype(np.intp), segment


def _segment_quantile(ordered: _Floats, local: NDArray[np.intp], probability: float) -> _Floats:
    """조각 안 구간마다 `quantile(정렬된 구간, probability)`. 빈 구간 칸은 쓰지 않는 0.0 이다."""
    sizes = np.diff(local)
    result = np.zeros(sizes.shape[0], np.float64)
    present = np.flatnonzero(sizes > 0)
    count = sizes[present]
    start = local[:-1][present]
    position = probability * (count - 1).astype(np.float64)
    # `int()` 는 0 쪽으로 자른다. 분위가 [0, 1] 이라 position ≥ 0 이고 정수부는 n − 1 이하다
    lower_index = np.trunc(position).astype(np.intp)
    upper_index = np.minimum(lower_index + 1, count - 1)
    weight = position - lower_index
    with np.errstate(all="ignore"):
        blended = (
            ordered[start + lower_index] * (1.0 - weight) + ordered[start + upper_index] * weight
        )
    result[present] = np.where(count == 1, ordered[start], blended)
    return result


def _element_segments(
    bounds: NDArray[np.intp], checkpoint: Callable[[], None]
) -> Iterator[tuple[slice, NDArray[np.intp]]]:
    """원소를 `_KERNEL_CHUNK` 조각으로 나눠 (조각, 원소마다의 구간 번호). 조각마다 checkpoint."""
    total = int(bounds[-1])
    for low in range(0, total, _KERNEL_CHUNK):
        checkpoint()
        part = slice(low, min(low + _KERNEL_CHUNK, total))
        # 원소 i 의 구간은 bounds[g] ≤ i < bounds[g + 1] 인 g 다(빈 구간은 건너뛴다)
        segment = np.searchsorted(bounds, np.arange(part.start, part.stop), side="right") - 1
        yield part, segment
