"""정확 평균 커널(columnar-tape T3-03)이 `statistics.mean`(CPython 3.11)과 비트가 같은지 본다.

로컬 수백만 건 차분은 PR 증거로 남기고, 여기서는 seed 를 고정한 2만 칸 차분과 경계 사례를 본다.
"""

import math
import statistics
from collections.abc import Callable

import numpy as np
import pytest

from strategy_workbench.domain.factor._statistics import ExactMeans, segment_mean, window_mean

_TINY = 5e-324  # 가장 작은 부분정규수


def _hex(value: float) -> str:
    # NaN 은 부호·payload 가 달라도 모두 같은 NaN 으로 본다(객체 경로도 NaN 이면 같은 결측 처리다)
    return "nan" if math.isnan(value) else float(value).hex()


def _expected(items: np.ndarray) -> str:
    return _hex(statistics.mean(items.tolist()))


def _window_mismatches(values: np.ndarray, window: int) -> list[int]:
    result = window_mean(values, window)
    return [
        start
        for start in range(values.shape[0] - window + 1)
        if _hex(result.values[start]) != _expected(values[start : start + window])
    ]


def _segment_mismatches(values: np.ndarray, offsets: np.ndarray) -> list[int]:
    result = segment_mean(values, offsets)
    return [
        group
        for group in range(offsets.shape[0] - 1)
        if offsets[group] < offsets[group + 1]
        and _hex(result.values[group]) != _expected(values[offsets[group] : offsets[group + 1]])
    ]


def _series(rng: np.random.Generator, kind: str, size: int) -> np.ndarray:
    if kind == "uniform":
        return rng.uniform(-1.0, 1.0, size)
    if kind == "lognormal":
        # 크기가 수십 자릿수에 걸치고 부호가 섞인다
        return rng.lognormal(0.0, 8.0, size) * rng.choice([-1.0, 1.0], size)
    if kind == "cancelling":
        # 큰 값이 서로 지워져 평균이 합보다 훨씬 작다
        base = rng.normal(0.0, 1e12, size)
        base[1::2] = -base[::2][: base[1::2].shape[0]] + rng.normal(0.0, 1.0, base[1::2].shape[0])
        return base
    if kind == "integers":
        return np.round(rng.normal(0.0, 1e6, size))
    if kind == "boundary":
        # 이웃 float 둘·짝을 섞어 반올림 경계(중점) 근처를 자주 밟는다
        anchor = rng.uniform(1.0, 2.0, size)
        step = rng.integers(-2, 3, size).astype(np.float64)
        return anchor + step * np.spacing(anchor)
    raise AssertionError(kind)


@pytest.mark.parametrize(
    ("kind", "window"),
    [
        (kind, window)
        for kind in ("uniform", "lognormal", "cancelling", "integers", "boundary")
        for window in (1, 2, 3, 7, 20, 63)
    ],
)
def test_window_mean_matches_statistics_mean_bit_for_bit(kind: str, window: int) -> None:
    # 30 조합 × 약 670 칸 ≈ 2만 칸
    # 문자열 hash 는 PYTHONHASHSEED 마다 달라 seed 로 쓰지 않는다
    rng = np.random.default_rng(sum(map(ord, kind)) * 1000 + window)
    values = _series(rng, kind, 670 + window)
    assert _window_mismatches(values, window) == []


@pytest.mark.parametrize("kind", ["uniform", "lognormal", "cancelling", "integers", "boundary"])
def test_segment_mean_matches_statistics_mean_bit_for_bit(kind: str) -> None:
    rng = np.random.default_rng(sum(map(ord, kind)))
    values = _series(rng, kind, 4000)
    cuts = np.sort(rng.integers(0, values.shape[0] + 1, size=400))
    offsets = np.concatenate(([0], cuts, [values.shape[0]])).astype(np.intp)
    assert _segment_mismatches(values, offsets) == []


@pytest.mark.parametrize(
    "items",
    [
        pytest.param([1.0], id="n=1"),
        pytest.param([3.0, 4.0], id="n=2 정수"),
        pytest.param([1.0, 1.0 + 2.0**-52], id="n=2 정확한 중점 → 짝수 쪽"),
        pytest.param([1.0, 1.0 + 3 * 2.0**-52], id="n=2 정확한 중점 → 홀수 이웃의 짝수 쪽"),
        pytest.param([2.0**53, 2.0**53 + 2.0], id="2^53 위 정수의 중점"),
        pytest.param([2.0**60, 1.0, -(2.0**60)], id="큰 값 상쇄 뒤 작은 값"),
        pytest.param([1e16, 1.0, -1e16, 1.0], id="혼합 부호 상쇄"),
        pytest.param([0.1, 0.2, 0.3], id="십진 소수"),
        pytest.param([-0.0], id="-0.0 하나"),
        pytest.param([-0.0, -0.0], id="-0.0 둘"),
        pytest.param([0.0, -0.0], id="±0"),
        pytest.param([1.0, -1.0], id="정확히 지워진 합"),
        pytest.param([_TINY], id="최소 부분정규수"),
        pytest.param([_TINY, 0.0], id="부분정규수 중점"),
        pytest.param([_TINY, _TINY, _TINY], id="부분정규수 셋"),
        pytest.param([2.2250738585072014e-308, -_TINY], id="정규 경계"),
        pytest.param([1e-300, 3e-300], id="작은 값 가드 안"),
        pytest.param([1e-289, 1e-289], id="작은 값 가드 바로 위"),
        pytest.param([1.7e308, 1.7e308], id="합이 넘치는 큰 값"),
        pytest.param([1e300, 2e300], id="큰 값 가드 위"),
        pytest.param([9e299, 9.9e299], id="큰 값 가드 바로 아래"),
        pytest.param([1.3e300, 1.4e300], id="분할 상수가 넘치는 구간"),
        pytest.param([1.7976931348623157e308, -1.7976931348623157e308, 1.0], id="최대값 상쇄"),
        pytest.param([math.inf, 1.0], id="+inf"),
        pytest.param([math.inf, -math.inf], id="inf 상쇄"),
        pytest.param([math.nan, 1.0], id="NaN"),
    ],
)
def test_edge_cases_match_statistics_mean(items: list[float]) -> None:
    values = np.array(items, np.float64)
    expected = _hex(statistics.mean(items))
    by_window = window_mean(values, values.shape[0])
    by_segment = segment_mean(values, np.array([0, values.shape[0]], np.intp))
    assert _hex(by_window.values[0]) == expected
    assert _hex(by_segment.values[0]) == expected
    # 0 의 부호까지 같다(−0.0 만 있는 칸도 statistics.mean 은 +0.0)
    assert math.copysign(1.0, by_window.values[0]) == math.copysign(1.0, statistics.mean(items))


def test_ties_and_extremes_take_the_exact_path() -> None:
    tie = window_mean(np.array([1.0, 1.0 + 2.0**-52]), 2)
    huge = window_mean(np.array([1e300, 3e300]), 2)
    tiny = window_mean(np.array([1e-300, 3e-300]), 2)
    zero = window_mean(np.array([-0.0, 0.0, 1.0, -1.0]), 2)
    assert tie.exact_path.tolist() == [True]
    assert huge.exact_path.tolist() == [True]
    assert tiny.exact_path.tolist() == [True]
    # 모든 TwoSum 오차가 0 이고 합이 0 이면 정확한 0 이라 statistics.mean 을 부르지 않는다
    assert zero.exact_path.tolist() == [False, False, False]
    assert [_hex(value) for value in zero.values] == [
        "0x0.0p+0",
        "0x1.0000000000000p-1",
        "0x0.0p+0",
    ]


def test_certified_path_covers_almost_every_window() -> None:
    # 정확 경로가 늘 도는 커널도 값은 맞으므로, 빠른 경로가 실제로 쓰이는지 따로 본다
    values = np.random.default_rng(3).normal(size=20_000)
    result = window_mean(values, 63)
    assert result.exact_path.mean() < 0.01


def test_wanted_mask_skips_windows_and_keeps_neighbours_exact() -> None:
    values = np.array([1.0, 2.0, math.nan, 4.0, 5.0, 6.0, 7.5])
    wanted = np.array([True, False, False, True, True, True])
    result = window_mean(values, 2, wanted=wanted)
    assert result.values.tolist() == [1.5, 0.0, 0.0, 4.5, 5.5, 6.75]
    assert result.exact_path.tolist() == [False] * 6


def test_starts_slice_matches_the_full_series() -> None:
    values = np.random.default_rng(5).normal(size=1000)
    full = window_mean(values, 20)
    part = window_mean(values, 20, starts=slice(300, 700))
    assert part.values.tobytes() == full.values[300:700].tobytes()


def test_segment_mean_marks_empty_segments_undefined() -> None:
    values = np.array([1.0, 2.0, 4.0])
    result = segment_mean(values, np.array([0, 0, 2, 2, 3], np.intp))
    assert [_hex(value) for value in result.values] == [
        "nan",
        "0x1.8000000000000p+0",
        "nan",
        "0x1.0000000000000p+2",
    ]
    assert result.exact_path.tolist() == [False] * 4


def test_segment_mean_handles_one_long_segment_with_blocks() -> None:
    values = np.random.default_rng(11).normal(size=50_000)
    result = segment_mean(values, np.array([0, 50_000], np.intp))
    assert _hex(result.values[0]) == _expected(values)


def test_checkpoint_runs_inside_long_kernels_and_can_cancel() -> None:
    calls = 0

    def count() -> None:
        nonlocal calls
        calls += 1

    values = np.random.default_rng(2).normal(size=1 << 17)
    window_mean(values, 20, checkpoint=count)
    # 창 offset 반복(접두·접미 2 × 20)과 인증 조각(2^16 칸)마다 부른다
    assert calls >= 40

    class Cancelled(Exception):
        pass

    def cancel() -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        segment_mean(values, np.array([0, values.shape[0]], np.intp), checkpoint=cancel)


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda: window_mean(np.zeros(3), 0), ValueError),
        (
            lambda: window_mean(
                np.zeros(3, np.float32),  # pyright: ignore[reportArgumentType]  # reason: 잘못된 dtype 거절을 본다
                1,
            ),
            TypeError,
        ),
        (lambda: window_mean(np.zeros(3), 2, starts=slice(0, 3)), ValueError),
        (lambda: window_mean(np.zeros(3), 2, wanted=np.ones(3, bool)), ValueError),
        (
            lambda: window_mean(
                np.zeros(3),
                2,
                wanted=np.ones(2, np.int8),  # pyright: ignore[reportArgumentType]  # reason: 잘못된 dtype 거절을 본다
            ),
            ValueError,
        ),
        (lambda: segment_mean(np.zeros(3), np.array([0, 2], np.intp)), ValueError),
        (lambda: segment_mean(np.zeros(3), np.array([0, 2, 1, 3], np.intp)), ValueError),
    ],
)
def test_kernel_rejects_malformed_inputs(
    call: Callable[[], ExactMeans], error: type[Exception]
) -> None:
    with pytest.raises(error):
        call()


def test_result_type_is_the_named_value() -> None:
    result = window_mean(np.array([1.0, 2.0]), 1)
    assert isinstance(result, ExactMeans)
    assert result.values.dtype == np.float64
    assert result.exact_path.dtype == np.bool_
