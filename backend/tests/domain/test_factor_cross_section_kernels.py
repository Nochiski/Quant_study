"""횡단면 구간 커널(columnar-tape T3-05)이 객체 경로 공식과 비트가 같은지 본다.

기준은 T3-05 전의 스칼라 공식이다. 순위·표준화는 커널로 옮겼으므로 옛 Python 식을 여기 기준으로
남기고(`_reference_rank`·`_reference_zscore`), winsorize·중앙값은 객체 평가기가 지금도 부르는
`quantile`·`statistics.median` 을 그대로 기준으로 쓴다. 평가기 전체 대조는 `tests/parity` L1 이
한다.
"""

import math
import statistics
from collections.abc import Callable

import numpy as np
import pytest

from strategy_workbench.domain.factor import _statistics
from strategy_workbench.domain.factor._statistics import (
    cross_sectional_rank,
    cross_sectional_zscore,
    quantile,
    segment_demean,
    segment_median,
    segment_rank,
    segment_winsorize,
    segment_zscore,
)


def _reference_rank(values: list[float]) -> list[float]:
    """T3-05 전 `cross_sectional_rank`(`rank_items`) 그대로."""
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks: dict[int, float] = {}
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and ordered[end][1] == ordered[position][1]:
            end += 1
        average_rank = (position + 1 + end) / 2
        for index, _ in ordered[position:end]:
            ranks[index] = average_rank
        position = end
    denominator = max(len(ranks) - 1, 1)
    return [(ranks[index] - 1) / denominator for index in range(len(values))]


def _reference_zscore(values: list[float]) -> list[float]:
    """T3-05 전 `cross_sectional_zscore` 그대로."""
    if not values:
        return []
    center = statistics.mean(values)
    deviation = statistics.pstdev(values)
    if deviation == 0:
        return [0.0] * len(values)
    return [(value - center) / deviation for value in values]


def _reference_winsorize(values: list[float], lower: float, upper: float) -> list[float]:
    """객체 평가기 `_cross_sectional` 의 winsorize 갈래 그대로."""
    ordered = sorted(values)
    low, high = quantile(ordered, lower), quantile(ordered, upper)
    return [min(max(value, low), high) for value in values]


def _reference_demean(values: list[float]) -> list[float]:
    center = statistics.mean(values)
    return [value - center for value in values]


def _hex(value: float) -> str:
    return "nan" if math.isnan(value) else float(value).hex()


_KINDS = ("ties", "signed_zero", "normal", "integers", "extreme", "mixed")


def _values(rng: np.random.Generator, kind: str, size: int) -> np.ndarray:
    if kind == "ties":
        return rng.integers(-3, 4, size).astype(np.float64) / 2.0
    if kind == "signed_zero":
        return rng.choice(np.array([0.0, -0.0, 1.0, -1.0]), size)
    if kind == "normal":
        return rng.normal(0.0, 1.0, size)
    if kind == "integers":
        return np.round(rng.normal(0.0, 1e6, size))
    if kind == "extreme":
        return rng.choice(
            np.array([1.7e308, -1.7e308, 5e-324, -5e-324, 1e-310, 0.0, -0.0, 1e300]), size
        )
    pools = (
        rng.normal(0.0, 1.0, size),
        rng.choice(np.array([0.0, -0.0, 0.25]), size),
        rng.lognormal(0.0, 6.0, size) * rng.choice(np.array([-1.0, 1.0]), size),
    )
    return np.choose(rng.integers(0, 3, size), pools)


def _segments(rng: np.random.Generator, kind: str) -> tuple[np.ndarray, np.ndarray]:
    """구간 길이 0~12 와 가끔 긴 구간, 빈 구간을 섞은 (값, 경계)."""
    lengths = rng.integers(0, 13, 40)
    lengths[rng.integers(0, 40)] = 90
    offsets = np.concatenate(([0], np.cumsum(lengths))).astype(np.intp)
    return _values(rng, kind, int(offsets[-1])), offsets


Kernel = Callable[[np.ndarray, np.ndarray], np.ndarray]
Reference = Callable[[list[float]], list[float]]

_ELEMENTWISE: dict[str, tuple[Kernel, Reference]] = {
    "rank": (segment_rank, _reference_rank),
    "zscore": (segment_zscore, _reference_zscore),
    "demean": (segment_demean, _reference_demean),
    "winsorize": (
        lambda values, offsets: segment_winsorize(values, offsets, 0.2, 0.8),
        lambda values: _reference_winsorize(values, 0.2, 0.8),
    ),
    "winsorize_edges": (
        lambda values, offsets: segment_winsorize(values, offsets, 0.0, 1.0),
        lambda values: _reference_winsorize(values, 0.0, 1.0),
    ),
    "winsorize_default": (
        lambda values, offsets: segment_winsorize(values, offsets, 0.01, 0.99),
        lambda values: _reference_winsorize(values, 0.01, 0.99),
    ),
}


def _mismatches(
    kernel: Kernel, reference: Reference, values: np.ndarray, offsets: np.ndarray
) -> int:
    result = kernel(values, offsets)
    wrong = 0
    for group in range(offsets.shape[0] - 1):
        low, high = int(offsets[group]), int(offsets[group + 1])
        if low == high:
            continue
        expected = reference(values[low:high].tolist())
        wrong += sum(
            _hex(got) != _hex(want)
            for got, want in zip(result[low:high].tolist(), expected, strict=True)
        )
    return wrong


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("name", sorted(_ELEMENTWISE))
def test_segment_kernels_match_the_object_formulas_bit_for_bit(name: str, kind: str) -> None:
    kernel, reference = _ELEMENTWISE[name]
    rng = np.random.default_rng([sorted(_ELEMENTWISE).index(name), _KINDS.index(kind)])
    wrong = sum(_mismatches(kernel, reference, *_segments(rng, kind)) for _ in range(25))
    assert wrong == 0


@pytest.mark.parametrize("kind", _KINDS)
def test_segment_median_matches_statistics_median(kind: str) -> None:
    rng = np.random.default_rng([31, _KINDS.index(kind)])
    for _ in range(25):
        values, offsets = _segments(rng, kind)
        result = segment_median(values, offsets)
        for group in range(offsets.shape[0] - 1):
            low, high = int(offsets[group]), int(offsets[group + 1])
            expected = "nan" if low == high else _hex(statistics.median(values[low:high].tolist()))
            assert _hex(result[group]) == expected, (kind, values[low:high].tolist())


@pytest.mark.parametrize(
    ("items", "median_hex"),
    [
        # 안정 정렬: 같은 0 은 입력 순서로 남아 가운데 원소의 부호가 정해진다
        ([0.0, -0.0, 1.0], (-0.0).hex()),
        ([-0.0, 0.0, 1.0], (0.0).hex()),
        ([0.0, -0.0, -1.0], (0.0).hex()),
        ([-0.0, 0.0, -1.0], (-0.0).hex()),
        # 짝수: 가운데 둘의 평균((a + b) / 2)이다. 위 원소나 아래 원소 하나가 아니다
        ([1.0, 2.0, 3.0, 10.0], (2.5).hex()),
        ([4.0, 1.0], (2.5).hex()),
        ([1.7e308, 1.7e308], math.inf.hex()),
        ([-0.0, -0.0], (-0.0).hex()),
        ([5e-324, 5e-324], (5e-324).hex()),
        ([5e-324, 1e-323], (1e-323).hex()),
    ],
)
def test_median_ties_signed_zeros_and_even_sizes(items: list[float], median_hex: str) -> None:
    assert _hex(statistics.median(items)) == median_hex
    values = np.array(items)
    assert _hex(segment_median(values, np.array([0, len(items)]))[0]) == median_hex


@pytest.mark.parametrize(
    ("items", "lower", "upper"),
    [
        # 분위 경계가 정확히 원소 자리(position 정수)와 그 사이(가중 보간)
        ([1.0, 2.0, 3.0, 4.0, 5.0], 0.25, 0.75),
        ([5.0, 4.0, 3.0, 2.0, 1.0], 0.2, 0.8),
        ([3.0, 1.0, 2.0], 0.5, 0.5),
        ([-0.0, 0.0, 0.0, -0.0], 0.0, 1.0),
        ([0.0, -0.0], 0.3, 0.7),
        ([7.0], 0.1, 0.9),
        ([1.0, 1.0, 2.0, 9.0, 9.0, 9.0, 100.0], 0.1, 0.9),
    ],
)
def test_winsorize_boundaries_and_signed_zeros(
    items: list[float], lower: float, upper: float
) -> None:
    values = np.array(items)
    result = segment_winsorize(values, np.array([0, len(items)]), lower, upper)
    assert [_hex(value) for value in result] == [
        _hex(value) for value in _reference_winsorize(items, lower, upper)
    ]


@pytest.mark.parametrize(
    "items",
    [
        [0.0, -0.0],
        [-0.0, 0.0, -0.0],
        [3.0, 1.0, 3.0, 1.0, 2.0],
        [math.inf, -math.inf, 0.0, math.inf],
        [1.0],
        [2.0, 2.0, 2.0],
    ],
)
def test_rank_ties_share_the_average_rank(items: list[float]) -> None:
    result = segment_rank(np.array(items), np.array([0, len(items)]))
    assert [_hex(value) for value in result] == [_hex(value) for value in _reference_rank(items)]


def test_zscore_exact_paths_and_constant_segments() -> None:
    """편차 0(상수·원소 1개)은 +0.0, 원소가 크거나 작은 칸은 정확 경로가 statistics 와 같다."""
    cases = [
        [4.0, 4.0, 4.0],
        [-0.0],
        [1e200, -1e200, 3.0],
        [5e-324, 0.0, -5e-324],
        [1e-300, 2e-300, 4e-300],
        [1.0, 1.0 + 2.0**-52, 1.0 - 2.0**-53],
    ]
    for items in cases:
        result = segment_zscore(np.array(items), np.array([0, len(items)]))
        assert [_hex(value) for value in result] == [
            _hex(value) for value in _reference_zscore(items)
        ], items


def test_scalar_api_wraps_the_segment_kernels() -> None:
    """포트폴리오 컴파일러·신호 평가가 부르는 스칼라 API 결과가 옛 식과 같다(spec D5)."""
    rng = np.random.default_rng(5)
    for kind in _KINDS:
        for size in (0, 1, 2, 3, 7, 40, 300):
            items = _values(rng, kind, size).tolist()
            assert [_hex(value) for value in cross_sectional_rank(items)] == [
                _hex(value) for value in _reference_rank(items)
            ]
            assert [_hex(value) for value in cross_sectional_zscore(items)] == [
                _hex(value) for value in _reference_zscore(items)
            ]
    assert cross_sectional_rank([]) == [] and cross_sectional_zscore([]) == []
    assert all(type(value) is float for value in cross_sectional_rank([2.0, 1.0]))
    assert all(type(value) is float for value in cross_sectional_zscore([2.0, 1.0]))


@pytest.mark.parametrize("kernel", [segment_rank, segment_median])
def test_sorting_kernels_reject_nan(kernel: Kernel) -> None:
    with pytest.raises(ValueError, match="cannot order NaN"):
        kernel(np.array([1.0, math.nan]), np.array([0, 2]))
    with pytest.raises(ValueError, match="cannot order NaN"):
        cross_sectional_rank([1.0, math.nan])


@pytest.mark.parametrize(("lower", "upper"), [(-0.1, 0.5), (0.2, 1.5), (math.nan, 0.5)])
def test_winsorize_rejects_quantiles_outside_the_unit_interval(lower: float, upper: float) -> None:
    with pytest.raises(ValueError, match="quantiles inside"):
        segment_winsorize(np.array([1.0, 2.0]), np.array([0, 2]), lower, upper)


def test_chunked_kernels_match_one_chunk_and_checkpoint_per_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """조각을 작게 잘라도 결과가 같고(구간 경계를 넘지 않는다), 조각마다 checkpoint 를 부른다."""
    rng = np.random.default_rng(9)
    values, offsets = _segments(rng, "mixed")
    whole = {
        "rank": segment_rank(values, offsets),
        "zscore": segment_zscore(values, offsets),
        "median": segment_median(values, offsets),
        "winsorize": segment_winsorize(values, offsets, 0.1, 0.9),
    }
    monkeypatch.setattr(_statistics, "_KERNEL_CHUNK", 16)
    monkeypatch.setattr(_statistics, "_SORT_CHUNK", 16)
    calls: list[int] = []
    pieces = {
        "rank": segment_rank(values, offsets, checkpoint=lambda: calls.append(1)),
        "zscore": segment_zscore(values, offsets),
        "median": segment_median(values, offsets),
        "winsorize": segment_winsorize(values, offsets, 0.1, 0.9),
    }
    for name, result in whole.items():
        assert [_hex(value) for value in pieces[name]] == [_hex(value) for value in result], name
    # 긴 구간(90 > 16) 하나는 조각 하나이고, 나머지는 16 원소 이하로 묶인다
    assert len(calls) >= int(offsets[-1]) // 16


def test_kernels_reject_malformed_offsets() -> None:
    with pytest.raises(ValueError, match="offsets"):
        segment_rank(np.array([1.0, 2.0]), np.array([0, 1]))
    with pytest.raises(TypeError, match="float64"):
        segment_median(np.array([1, 2]), np.array([0, 2]))
