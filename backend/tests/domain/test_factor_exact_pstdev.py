"""정확 pstdev 커널(columnar-tape T3-04)이 `statistics.pstdev`(CPython 3.11)와 비트가 같은지 본다.

로컬 수백만 건 차분은 PR 증거로 남기고, 여기서는 seed 를 고정한 2만 칸 차분, 반올림 경계를
노린 정수 구성 칸, 인증 변이의 반례, 경계 사례를 본다.
"""

import math
import random
import statistics
from collections.abc import Callable
from math import isqrt

import numpy as np
import pytest

from strategy_workbench.domain.factor._statistics import (
    ExactDeviations,
    segment_pstdev,
    window_pstdev,
)

_TINY = 5e-324  # 가장 작은 부분정규수


def _hex(value: float) -> str:
    # NaN 은 부호·payload 가 달라도 모두 같은 NaN 으로 본다. ±0 은 hex 가 가른다
    return "nan" if math.isnan(value) else float(value).hex()


def _expected(items: np.ndarray) -> str:
    return _hex(statistics.pstdev(items.tolist()))


def _series(rng: np.random.Generator, kind: str, size: int) -> np.ndarray:
    if kind == "uniform":
        return rng.uniform(-1.0, 1.0, size)
    if kind == "returns":
        return rng.standard_t(4, size) * 0.02
    if kind == "lognormal":
        # 크기가 수십 자릿수에 걸치고 부호가 섞인다
        return rng.lognormal(0.0, 8.0, size) * rng.choice([-1.0, 1.0], size)
    if kind == "cancelling":
        # 큰 값이 서로 지워져 퍼짐이 크기보다 훨씬 작다
        base = rng.normal(0.0, 1e12, size)
        base[1::2] = -base[::2][: base[1::2].shape[0]] + rng.normal(0.0, 1.0, base[1::2].shape[0])
        return base
    if kind == "integers":
        return np.round(rng.normal(0.0, 1e6, size))
    if kind == "price":
        # 평균이 퍼짐보다 10^8 배 크다 — 이동값이 상쇄를 막아야 한다
        return 1e8 + rng.normal(0.0, 1.0, size)
    if kind == "neighbours":
        # 이웃 float 둘·셋이 섞여 퍼짐이 ulp 몇 개뿐이다
        anchor = rng.uniform(1.0, 2.0, size)
        return anchor + rng.integers(-2, 3, size).astype(np.float64) * np.spacing(anchor)
    if kind == "runs":
        # 같은 값이 이어지는 구간(정지 종목)과 ±0
        values = np.repeat(rng.normal(0.0, 1.0, size // 6 + 1), 6)[:size]
        values[rng.random(size) < 0.1] = -0.0
        values[rng.random(size) < 0.1] = 0.0
        return values
    raise AssertionError(kind)


_KINDS = (
    "uniform",
    "returns",
    "lognormal",
    "cancelling",
    "integers",
    "price",
    "neighbours",
    "runs",
)


@pytest.mark.parametrize(
    ("kind", "window"),
    [(kind, window) for kind in _KINDS for window in (1, 2, 3, 7, 20, 63)],
)
def test_window_pstdev_matches_statistics_pstdev_bit_for_bit(kind: str, window: int) -> None:
    # 48 조합 × 약 420 칸 ≈ 2만 칸. 문자열 hash 는 PYTHONHASHSEED 마다 달라 seed 로 쓰지 않는다
    rng = np.random.default_rng(sum(map(ord, kind)) * 1000 + window)
    values = _series(rng, kind, 420 + window)
    result = window_pstdev(values, window)
    wrong = [
        start
        for start in range(values.shape[0] - window + 1)
        if _hex(result.values[start]) != _expected(values[start : start + window])
    ]
    assert wrong == []


@pytest.mark.parametrize("kind", _KINDS)
def test_segment_pstdev_matches_statistics_pstdev_bit_for_bit(kind: str) -> None:
    rng = np.random.default_rng(sum(map(ord, kind)))
    values = _series(rng, kind, 3000)
    cuts = np.sort(rng.integers(0, values.shape[0] + 1, size=300))
    offsets = np.concatenate(([0], cuts, [values.shape[0]])).astype(np.intp)
    result = segment_pstdev(values, offsets)
    wrong = [
        group
        for group in range(offsets.shape[0] - 1)
        if offsets[group] < offsets[group + 1]
        and _hex(result.values[group]) != _expected(values[offsets[group] : offsets[group + 1]])
    ]
    assert wrong == []


# 정확한 분산을 정수로 다루려고 모든 float 를 2^1074 배 한 정수로 본다(가장 작은 부분정규수가 1)
_SCALE_BITS = 1074
_SCALE = 1 << _SCALE_BITS


def _scaled(value: float) -> int:
    numerator, denominator = value.as_integer_ratio()
    return numerator << (_SCALE_BITS - (denominator.bit_length() - 1))


def _from_scaled(units: int) -> float | None:
    try:
        value = units / _SCALE
    except OverflowError:
        return None
    return None if math.isinf(value) else value


def _rest(rng: random.Random, count: int, size: float) -> list[float]:
    """경계 칸의 나머지 원소. 같은 크기·큰 평균·이웃 float·±0·정수·넓은 크기·같은 값을 섞는다."""
    kind = rng.random()
    if kind < 0.25:
        return [size * rng.uniform(-1.0, 1.0) for _ in range(count)]
    if kind < 0.4:
        center = size * 2.0 ** rng.uniform(5.0, 40.0) * rng.choice((-1.0, 1.0))
        return [center + size * rng.uniform(-0.5, 0.5) for _ in range(count)]
    if kind < 0.5:
        center = size * rng.uniform(0.5, 2.0)
        return [center + rng.randint(-3, 3) * math.ulp(center) for _ in range(count)]
    if kind < 0.58:
        return [rng.choice((0.0, -0.0)) for _ in range(count)]
    if kind < 0.66:
        limit = max(1.0, min(size, 2.0**60))
        return [float(round(rng.uniform(-limit, limit))) for _ in range(count)]
    if kind < 0.8:
        return [
            size * 2.0 ** rng.uniform(-60.0, 0.0) * rng.choice((-1.0, 1.0)) for _ in range(count)
        ]
    return [size * rng.uniform(-1.0, 1.0)] * count


def _boundary_cell(rng: random.Random) -> list[float] | None:
    """정확한 n²V 를 후보 s 의 반올림 경계 n²·((s + 이웃)/2)² + t 로 정수 구성한다.

    나머지 원소 위에 ±y 짝을 얹는다. 짝은 합에 0, 제곱합에 2y² 를 더하므로
    n²V = n·(R2 + 2Σy²) − R1² 이고, Σy² 를 탐욕으로 맞춘다(짝마다 상대 2^-52 씩 정밀해진다).
    t 는 경계 간격의 2^0 ~ 2^-120 배이고 약 10% 는 0 이다(짝으로는 정확히 닿지 않을 수 있다).
    인증 상한이 빡빡하면(상한 0·E_sum 누락·간격 대칭화) 경계 바로 앞의 칸을 잘못 확정한다 —
    무작위 분포로는 그 칸을 거의 만들지 못한다(#584 리뷰 P2-1 과 같은 기준).
    """
    if rng.random() < 0.45:
        # 짝만: 크기가 수십 binade 에 걸쳐 TwoSum 오차가 흡수됐다 지워진다(E_sum 이 필요한 칸)
        pairs, rest_count = rng.choice((4, 6, 8, 8, 12, 16)), rng.choice((0, 0, 0, 1))
    else:
        pairs, rest_count = rng.choice((3, 4, 5, 6, 8)), rng.choice((1, 2, 3, 5, 9, 40))
    count = 2 * pairs + rest_count
    band = rng.random()
    if band < 0.6:
        exponent = rng.randint(-40, 40)
    elif band < 0.7:
        exponent = rng.randint(-432, -400)  # 후보 하한 1e-130 ≈ 2^-431.9 근처
    elif band < 0.8:
        exponent = rng.randint(-419, -380)  # 원소 하한 1e-126 ≈ 2^-418.6 근처
    elif band < 0.9:
        exponent = rng.randint(440, 479)  # 상한 1e144 ≈ 2^478.4 근처
    else:
        exponent = rng.randint(-470, 470)
    size = math.ldexp(1.0, exponent)
    rest = [value for value in _rest(rng, rest_count, size) if math.isfinite(value)]
    rest += [0.0] * (rest_count - len(rest))
    first = sum(_scaled(value) for value in rest)
    rest_w = count * sum(_scaled(value) ** 2 for value in rest) - first * first
    floor_s = _from_scaled(isqrt(max(rest_w, 0)) // count)
    if floor_s is None:
        return None
    # 후보 s 는 나머지보다 퍼짐이 크게 고른다(짝의 제곱합이 양수여야 한다). 약 1/4 은 2의 거듭제곱
    spread = max(floor_s, size) * 2.0 ** rng.uniform(0.2, 3.0)
    mantissa = 1.0 if rng.random() < 0.25 else rng.uniform(1.0, 2.0)
    candidate = math.ldexp(mantissa, math.frexp(spread)[1])
    neighbour = math.nextafter(candidate, rng.choice((math.inf, -math.inf)))
    if not math.isfinite(candidate) or not math.isfinite(neighbour) or neighbour == 0.0:
        return None
    low, high = _scaled(candidate), _scaled(neighbour)
    edge = (low + high) ** 2  # (2·경계)²
    gap = abs(edge - 4 * low * low) * count * count
    offset = 0
    if rng.random() >= 0.1:
        shift = rng.uniform(0.0, 120.0)
        whole = int(shift)
        offset = (gap * int(2.0**53 * 2.0 ** -(shift - whole))) >> (53 + whole)
        offset *= rng.choice((-1, 1))
    need = (count * count * edge + offset - 4 * rest_w) // (8 * count)  # Σy² (2^-2148 단위)
    if need <= 0:
        return None
    values = list(rest)
    for index in range(pairs):
        root = isqrt(need)
        if index < pairs - 3 and rng.random() < 0.7:
            # 앞쪽 짝은 남은 몫의 일부만 맡아 크기를 섞는다
            root = isqrt(need * rng.randint(1, 15) // 16)
        drop = max(0, root.bit_length() - 53)
        y = (root >> drop) << drop
        need -= y * y
        value = _from_scaled(y)
        if value is None:
            return None
        values += [value, -value]
    rng.shuffle(values)
    if rest and rng.random() < 0.3:
        # 구간 형태의 이동값(첫 원소)이 평균에서 먼 칸: S1 이 커서 c1 오차가 W 에 크게 실린다
        far = max(range(len(values)), key=lambda index: abs(values[index]))
        values[0], values[far] = values[far], values[0]
    return values


def _tie_cell(rng: random.Random) -> list[float] | None:
    """짝수 n 의 정확한 동점: a 와 b 를 k 개씩 두면 √V = |a − b|/2 가 두 float 의 한가운데다."""
    pairs = rng.choice((1, 1, 1, 2, 3, 10))
    exponent = rng.randint(-60, 60) if rng.random() < 0.8 else rng.randint(-430, 470)
    a = math.ldexp(rng.uniform(1.0, 2.0), exponent) * rng.choice((-1.0, 1.0))
    b = -a - math.ulp(a) * rng.choice((1.0, 3.0, 5.0)) * math.copysign(1.0, a)
    if not math.isfinite(b):
        return None
    values = [a] * pairs + [b] * pairs
    rng.shuffle(values)
    return values


def _boundary_cells(seed: int, cells: int) -> list[list[float]]:
    rng = random.Random(seed)
    out: list[list[float]] = []
    while len(out) < cells:
        cell = _boundary_cell(rng) if rng.random() < 0.85 else _tie_cell(rng)
        if cell is not None:
            out.append(cell)
    return out


def test_segment_pstdev_is_exact_next_to_rounding_boundaries() -> None:
    cells = _boundary_cells(20261020, 4000)
    values = np.array([value for cell in cells for value in cell], np.float64)
    offsets = np.concatenate(([0], np.cumsum([len(cell) for cell in cells]))).astype(np.intp)
    result = segment_pstdev(values, offsets)
    wrong = [
        index
        for index, cell in enumerate(cells)
        if _hex(result.values[index]) != _hex(statistics.pstdev(cell))
    ]
    assert wrong == []
    # 경계에서 상한보다 먼 칸은 벡터 경로가 정해야 이 차분이 인증 식을 실제로 시험한다.
    # 동점·가드 밖 크기·상한 안쪽 칸은 설계대로 정확 경로다(실측 약 79%)
    assert result.exact_path.mean() < 0.9


def test_window_pstdev_is_exact_next_to_rounding_boundaries() -> None:
    by_length: dict[int, list[list[float]]] = {}
    for cell in _boundary_cells(20261021, 3000):
        by_length.setdefault(len(cell), []).append(cell)
    wrong: list[tuple[int, int]] = []
    for count, cells in by_length.items():
        # 같은 길이 칸을 이어 붙이고 칸 경계에서 시작하는 창만 원한다(이동값은 블록 끝 원소)
        values = np.array([value for cell in cells for value in cell], np.float64)
        wanted = np.zeros(values.shape[0] - count + 1, bool)
        wanted[::count] = True
        result = window_pstdev(values, count, wanted=wanted)
        wrong += [
            (count, index)
            for index, cell in enumerate(cells)
            if _hex(result.values[index * count]) != _hex(statistics.pstdev(cell))
        ]
    assert wrong == []


@pytest.mark.parametrize(
    "hexes",
    [
        # 경계 생성기가 찾은 인증 변이의 반례(로컬 퍼저). 기대값은 statistics.pstdev 다
        pytest.param(
            ["0x1.d734c828ce9dbp-82", "-0x1.fffffffffffffp-29", "-0x1.7f684befff60dp-109"]
            + ["-0x1.d734c828ce9dbp-82", "-0x1.6a09e667f3bccp-55", "0x1.fffffffffffffp-29"]
            + ["0x1.7f684befff60dp-109", "0x1.6a09e667f3bccp-55"],
            id="E_sum(c2 누적 오차) 누락 1",
        ),
        pytest.param(
            ["-0x1.273076cd72fc4p-27", "-0x1.6a09e667f3bcfp+25", "0x1.deeea11683f48p+26"]
            + ["-0x1.deeea11683f48p+26", "0x1.273076cd72fc4p-27", "-0x1.6731114893ee6p-1"]
            + ["0x1.6a09e667f3bcfp+25", "0x1.6731114893ee6p-1"],
            id="E_sum(c2 누적 오차) 누락 2",
        ),
        pytest.param(
            ["-0x1.356911031d652p+38", "0x1.356911031d653p+38", "-0x1.356911031d652p+38"]
            + ["0x1.356911031d653p+38", "-0x1.356911031d652p+38", "0x1.356911031d653p+38"],
            id="상한 0",
        ),
        pytest.param(
            ["-0x1.deeea11683f48p+41", "0x1.dcd0a4cb4911bp-12", "-0x1.91ddb476f2526p+15"]
            + ["-0x1.dcd0a4cb4911bp-12", "0x1.f6ec5d8a4878fp-12", "0x1.91ddb476f2526p+15"]
            + ["0x1.deeea11683f48p+41"],
            id="위아래 간격 대칭화",
        ),
        pytest.param(["-0x1.fdc2d2e72fecep-342", "0x1.fdc2d2e72fed3p-342"], id="동점을 인증"),
        pytest.param(
            ["0x1.74e8d873c5f0cp+0", "0x1.afb4bddc03560p-49", "0x1.090e9d76f9313p-22"]
            + ["-0x1.afb4bddc03560p-49", "0x1.b01068a5742afp+4", "-0x1.090e9d76f9313p-22"]
            + ["-0x1.b01068a5742afp+4"],
            id="2·dh·dl 누락",
        ),
        pytest.param(["-0x1.6115e789bbc8bp+210", "0x1.6115e789bbc88p+210"], id="pe 누락"),
        pytest.param(["0x1.5adc6a7d60fe1p+206", "-0x1.5adc6a7d60fdcp+206"], id="dl 누락"),
        pytest.param(
            ["0x1.052645043340bp-60", "-0x1.d6f15c4a95252p-34", "-0x1.052645043340bp-60"]
            + ["0x1.6a09e667f3bccp-7", "-0x1.fffffffffffffp+19", "0x1.d6f15c4a95252p-34"]
            + ["-0x1.6a09e667f3bccp-7", "0x1.fffffffffffffp+19"],
            id="경계 제곱의 g²/4 누락",
        ),
        pytest.param(
            ["-0x1.7f684befff60dp-76", "0x1.7f684befff60dp-76", "-0x1.d734c828ce9dbp-49"]
            + ["-0x1.6a09e667f3bccp-22", "0x1.6a09e667f3bccp-22", "-0x1.fffffffffffffp+4"]
            + ["0x1.fffffffffffffp+4", "0x1.d734c828ce9dbp-49"],
            id="c1² 누락",
        ),
    ],
)
def test_certification_regressions(hexes: list[str]) -> None:
    items = [float.fromhex(text) for text in hexes]
    values = np.array(items, np.float64)
    expected = _hex(statistics.pstdev(items))
    # 두 형태는 이동값이 다르다(창은 끝 원소, 구간은 첫 원소). 두 순서 모두 본다
    for ordered in (values, values[::-1].copy()):
        assert _hex(window_pstdev(ordered, ordered.shape[0]).values[0]) == expected
        whole = np.array([0, ordered.shape[0]], np.intp)
        assert _hex(segment_pstdev(ordered, whole).values[0]) == expected


@pytest.mark.parametrize(
    "items",
    [
        pytest.param([1.0], id="n=1"),
        pytest.param([-0.0], id="-0.0 하나"),
        pytest.param([-0.0, -0.0, 0.0], id="±0"),
        pytest.param([2.5, 2.5, 2.5], id="같은 값"),
        pytest.param([1e300, 1e300], id="큰 같은 값"),
        pytest.param([_TINY, _TINY], id="부분정규 같은 값"),
        pytest.param([3.0, 4.0], id="n=2 정확한 제곱근"),
        pytest.param([1.0, 2.0, 3.0, 4.0], id="정수 넷"),
        pytest.param([1.0, -1.0 - 2.0**-52], id="n=2 동점 → 짝수 쪽"),
        pytest.param([1.0, -1.0 - 3 * 2.0**-52], id="n=2 동점 → 홀수 이웃의 짝수 쪽"),
        pytest.param([2.0**53 + 2.0, -1.0], id="2^53 위 정수의 동점"),
        pytest.param([0.1, 0.2, 0.3], id="십진 소수"),
        pytest.param([1e16, 1.0, -1e16, 1.0], id="큰 값 상쇄"),
        pytest.param([1e8 + 0.5, 1e8 - 0.25, 1e8], id="큰 평균 작은 퍼짐"),
        pytest.param([1.0, 1.0 + 2.0**-52], id="이웃 float"),
        pytest.param([_TINY, 0.0], id="부분정규 동점"),
        pytest.param([_TINY, 2 * _TINY, 0.0], id="부분정규 셋"),
        pytest.param([1e-127, 3e-127], id="원소 하한 아래"),
        pytest.param([1e-125, 3e-125], id="원소 하한 위"),
        pytest.param([1e-126 * (1 + 2.0**-52), 1e-126], id="하한 근처 이웃"),
        pytest.param([1e144, -1e144], id="원소 상한"),
        pytest.param([1.1e144, 0.0], id="원소 상한 위"),
        pytest.param([1.7e308, -1.7e308], id="넘치는 큰 값"),
        pytest.param([1.7976931348623157e308, 1.7976931348623157e308], id="최대값 같은 값"),
    ],
)
def test_edge_cases_match_statistics_pstdev(items: list[float]) -> None:
    values = np.array(items, np.float64)
    expected = _hex(statistics.pstdev(items))
    by_window = window_pstdev(values, values.shape[0])
    by_segment = segment_pstdev(values, np.array([0, values.shape[0]], np.intp))
    assert _hex(by_window.values[0]) == expected
    assert _hex(by_segment.values[0]) == expected


@pytest.mark.parametrize("items", [[math.inf, 1.0], [math.nan, 1.0], [math.inf, -math.inf]])
def test_non_finite_cells_defer_to_statistics_pstdev(items: list[float]) -> None:
    # 객체 경로는 비유한값을 넘기지 않는다(노드마다 유한값 검사). 넘어오면 같은 예외를 낸다
    with pytest.raises(Exception) as raised:  # noqa: PT011  # reason: 3.11 의 예외 타입을 그대로 받는다
        statistics.pstdev(items)
    with pytest.raises(raised.type):
        window_pstdev(np.array(items, np.float64), len(items))
    with pytest.raises(raised.type):
        segment_pstdev(np.array(items, np.float64), np.array([0, len(items)], np.intp))


def test_ties_guards_and_constants_take_the_expected_path() -> None:
    tie = window_pstdev(np.array([1.0, -1.0 - 2.0**-52]), 2)
    huge = window_pstdev(np.array([1e145, -1e145]), 2)
    tiny = window_pstdev(np.array([1e-127, 3e-127]), 2)
    constant = window_pstdev(np.array([-0.0, 0.0, 0.0, 2.5, 2.5]), 2)
    assert tie.exact_path.tolist() == [True]
    assert huge.exact_path.tolist() == [True]
    assert tiny.exact_path.tolist() == [True]
    # 모든 원소가 이동값과 같으면 정확한 0 이라 statistics.pstdev 를 부르지 않고 +0.0 이다
    assert constant.exact_path.tolist() == [False, False, False, False]
    assert [_hex(value) for value in constant.values] == [
        "0x0.0p+0",
        "0x0.0p+0",
        "0x1.4000000000000p+0",
        "0x0.0p+0",
    ]


def test_certified_path_covers_almost_every_window() -> None:
    # 정확 경로가 늘 도는 커널도 값은 맞으므로, 빠른 경로가 실제로 쓰이는지 따로 본다
    values = np.random.default_rng(3).standard_t(4, 20_000) * 0.02
    assert window_pstdev(values, 63).exact_path.mean() < 0.01


def test_shift_keeps_large_mean_windows_certified() -> None:
    # 평균이 퍼짐의 10^8 배면 이동값 없이 n·Σx² − (Σx)² 는 2^53 배 상쇄돼 모두 정확 경로로 간다
    values = 1e8 + np.random.default_rng(13).normal(size=20_000)
    assert window_pstdev(values, 20).exact_path.mean() < 0.01
    cuts = np.arange(0, values.shape[0] + 1, 50).astype(np.intp)
    assert segment_pstdev(values, cuts).exact_path.mean() < 0.01


def test_wanted_mask_skips_windows_and_keeps_neighbours_exact() -> None:
    values = np.array([1.0, 2.0, math.nan, 4.0, 5.0, 6.0, 7.5])
    wanted = np.array([True, False, False, True, True, True])
    result = window_pstdev(values, 2, wanted=wanted)
    assert result.values.tolist() == [0.5, 0.0, 0.0, 0.5, 0.5, 0.75]
    assert result.exact_path.tolist() == [False] * 6


def test_starts_slice_matches_the_full_series() -> None:
    values = np.random.default_rng(5).normal(size=1000)
    full = window_pstdev(values, 20)
    part = window_pstdev(values, 20, starts=slice(300, 700))
    assert part.values.tobytes() == full.values[300:700].tobytes()


def test_segment_pstdev_marks_empty_segments_undefined() -> None:
    values = np.array([1.0, 2.0, 4.0])
    result = segment_pstdev(values, np.array([0, 0, 2, 2, 3], np.intp))
    assert [_hex(value) for value in result.values] == [
        "nan",
        "0x1.0000000000000p-1",
        "nan",
        "0x0.0p+0",
    ]
    assert result.exact_path.tolist() == [False] * 4


def test_segment_pstdev_handles_one_long_segment_with_blocks() -> None:
    values = np.random.default_rng(11).normal(size=50_000)
    result = segment_pstdev(values, np.array([0, 50_000], np.intp))
    assert _hex(result.values[0]) == _expected(values)


def test_checkpoint_runs_inside_long_kernels_and_can_cancel() -> None:
    calls = 0

    def count() -> None:
        nonlocal calls
        calls += 1

    values = np.random.default_rng(2).normal(size=1 << 17)
    window_pstdev(values, 20, checkpoint=count)
    # 창 offset 반복(접두·접미 2 × 20)과 인증 조각(2^16 칸)마다 부른다
    assert calls >= 40

    class Cancelled(Exception):
        pass

    def cancel() -> None:
        raise Cancelled

    with pytest.raises(Cancelled):
        segment_pstdev(values, np.array([0, values.shape[0]], np.intp), checkpoint=cancel)


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda: window_pstdev(np.zeros(3), 0), ValueError),
        (
            lambda: window_pstdev(
                np.zeros(3, np.float32),  # pyright: ignore[reportArgumentType]  # reason: 잘못된 dtype 거절을 본다
                1,
            ),
            TypeError,
        ),
        (lambda: window_pstdev(np.zeros(3), 2, starts=slice(0, 3)), ValueError),
        (lambda: window_pstdev(np.zeros(3), 2, wanted=np.ones(3, bool)), ValueError),
        (lambda: segment_pstdev(np.zeros(3), np.array([0, 2], np.intp)), ValueError),
        (lambda: segment_pstdev(np.zeros(3), np.array([0, 2, 1, 3], np.intp)), ValueError),
    ],
)
def test_kernel_rejects_malformed_inputs(
    call: Callable[[], ExactDeviations], error: type[Exception]
) -> None:
    with pytest.raises(error):
        call()


def test_result_type_is_the_named_value() -> None:
    result = window_pstdev(np.array([1.0, 2.0]), 1)
    assert isinstance(result, ExactDeviations)
    assert result.values.dtype == np.float64
    assert result.exact_path.dtype == np.bool_
