"""종목·지수 이름 검색 순위의 owner — 정확 일치 단계와 같은 단계 안의 증권 종류 차례."""

from __future__ import annotations

from collections.abc import Sequence

from strategy_workbench.application.stock_chart.ports.outgoing.chart_prices import (
    PRACTICE_CODE_PREFIX,
    STOCK_GROUPS,
    ChartInstrument,
    InstrumentKind,
)

# 이름 포함 검색의 같은 순위 안 순서: 지수 → 포트 어휘 차례(보통주 → 우선주 → ETF) → 그 밖.
_GROUP_ORDER = {group: rank for rank, group in enumerate(STOCK_GROUPS)}


def rank_instruments(
    instruments: Sequence[ChartInstrument], query: str
) -> tuple[ChartInstrument, ...]:
    """검색 순위: 지수명 정확 → 티커 정확 → 이름(정식 이름 포함) 정확 → 이름 포함. 대소문자 무시."""
    needle = query.strip().casefold()
    if not needle:
        return ()

    def names(item: ChartInstrument) -> tuple[str, ...]:
        return tuple(name.casefold() for name in (item.name, *item.aliases))

    def tier(item: ChartInstrument) -> int | None:
        if item.ref.kind is InstrumentKind.INDEX and item.name.casefold() == needle:
            return 0
        code = item.ref.code.removeprefix(PRACTICE_CODE_PREFIX)
        if item.ref.kind is InstrumentKind.STOCK and code.casefold() == needle:
            return 1
        if needle in names(item):
            return 2
        if any(needle in name for name in names(item)):
            return 3
        return None

    ranked = [(t, item) for item in instruments if (t := tier(item)) is not None]
    ranked.sort(
        key=lambda pair: (
            pair[0],
            pair[1].ref.kind is not InstrumentKind.INDEX,
            _GROUP_ORDER.get(pair[1].group or "", len(_GROUP_ORDER)),
            pair[1].name,
            pair[1].ref.code,
        )
    )
    return tuple(item for _, item in ranked)
