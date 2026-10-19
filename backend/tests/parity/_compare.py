"""비트 비교기와 차이 보고기(columnar-tape spec 5절 L0~L3, 허용오차 0).

`==` 는 `0.0 == -0.0` 이고 `nan != nan` 이며 `1 == 1.0 == True` 라서 대조에 쓰지 않는다. 여기서는
타입이 정확히 같아야 하고, float 는 IEEE 비트(int64)가 같아야 한다. tuple·list 는 순서까지,
dict 는 키 순서까지, dataclass 는 필드마다 재귀로 본다.
"""

from __future__ import annotations

import dataclasses
import struct
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Difference:
    """차이 한 건. `path` 는 결과 안의 위치이고 값이 아니다."""

    layer: str
    key: str
    path: str
    expected: str
    actual: str


@dataclass(frozen=True)
class ParityReport:
    """대조 결과. `differences` 는 처음 `limit` 건이고 `total` 은 전체 개수다."""

    cases: int
    total: int
    differences: tuple[Difference, ...]

    @property
    def ok(self) -> bool:
        return self.total == 0


def float_bits(value: float) -> int:
    """float 의 IEEE 754 비트를 부호 있는 int64 로. ±0 과 NaN payload 를 가른다."""
    return struct.unpack("<q", struct.pack("<d", value))[0]


def _show(value: object) -> str:
    if type(value) is float:
        return f"{value!r} (bits=0x{float_bits(value) & 0xFFFFFFFFFFFFFFFF:016x})"
    text = repr(value)
    return text if len(text) <= 120 else f"{text[:117]}..."


def differences(expected: object, actual: object, path: str = "") -> Iterator[tuple[str, str, str]]:
    """두 값의 차이를 (경로, 기대, 실제)로 낸다. 같으면 아무것도 내지 않는다."""
    if type(expected) is not type(actual):
        yield path, type(expected).__name__, type(actual).__name__
        return
    if isinstance(expected, float) and isinstance(actual, float):
        if float_bits(expected) != float_bits(actual):
            yield path, _show(expected), _show(actual)
        return
    if isinstance(expected, Enum):
        if expected is not actual:
            yield path, _show(expected), _show(actual)
        return
    if dataclasses.is_dataclass(expected):
        for item in dataclasses.fields(expected):
            yield from differences(
                getattr(expected, item.name), getattr(actual, item.name), f"{path}.{item.name}"
            )
        return
    if isinstance(expected, (tuple, list)) and isinstance(actual, (tuple, list)):
        if len(expected) != len(actual):
            yield f"{path}.len", str(len(expected)), str(len(actual))
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            yield from differences(left, right, f"{path}[{index}]")
        return
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        if list(expected) != list(actual):
            yield f"{path}.keys", _show(list(expected)), _show(list(actual))
        for name in expected:
            if name in actual:
                yield from differences(expected[name], actual[name], f"{path}[{name!r}]")
        return
    if isinstance(expected, (set, frozenset)) and isinstance(actual, (set, frozenset)):
        if expected != actual:
            yield path, _show(sorted(expected - actual)), _show(sorted(actual - expected))
        return
    if expected != actual:
        yield path, _show(expected), _show(actual)


def compare(
    layer: str, pairs: Iterable[tuple[str, object, object]], *, limit: int = 20
) -> ParityReport:
    """사례마다 (키, 기대, 실제)를 대조해 처음 `limit` 건과 총 개수를 낸다."""
    cases = 0
    total = 0
    kept: list[Difference] = []
    for key, expected, actual in pairs:
        cases += 1
        for path, left, right in differences(expected, actual):
            total += 1
            if len(kept) < limit:
                kept.append(Difference(layer, key, path, left, right))
    return ParityReport(cases=cases, total=total, differences=tuple(kept))


def format_report(report: ParityReport, *, values: bool) -> str:
    """사람이 읽는 보고. `values=False` 면 기대·실제 값을 싣지 않는다(실원장, holdout-window.md)."""
    lines = [f"cases={report.cases} mismatches={report.total}"]
    for item in report.differences:
        line = f"  {item.layer} key={item.key} path={item.path or '<root>'}"
        if values:
            line += f" expected={item.expected} actual={item.actual}"
        lines.append(line)
    if report.total > len(report.differences):
        lines.append(f"  ... {report.total - len(report.differences)} more")
    return "\n".join(lines)
