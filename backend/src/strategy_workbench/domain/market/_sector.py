"""섹터맵: WICS 업종별 크기와 하루 등락.

크기와 등락은 같은 가중을 쓴다 — 유동주식수 × 전날 종가. 전체 시가총액이나 당일 종가로 가중하면
상승 종목이 과대 반영되고 칸 크기(유동)와 색(전체)이 서로 다른 시장을 말한다. 그날 거래가 없던
종목(거래정지 등)은 크기에는 넣고 등락에서는 뺀다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

# 원장 L2 이름 앞에 붙은 분류 이름(`WICS 반도체와반도체장비`). 화면 이름에는 붙이지 않는다.
WICS_PREFIX = "WICS "


@dataclass(frozen=True)
class SectorMember:
    """스냅샷 한 종목과 기준 장의 가격. 가격이 없거나 0인 종목은 묶음에서 빠진다."""

    ticker: str
    name: str
    l1_code: str
    l1_name: str
    l2_code: str
    l2_name: str
    float_shares: int
    prev_close: float | None
    close: float | None
    traded: bool


@dataclass(frozen=True)
class SectorGroup:
    code: str
    name: str
    size_weight_krw: float
    # 등락 가중에 들어간(거래 있는) 종목의 가중 합. 화면이 작은 칸을 합칠 때 같은 가중으로
    # 다시 묶는다.
    return_weight_krw: float
    return_pct: float | None
    securities: int
    untraded: int


@dataclass(frozen=True)
class SectorParent:
    group: SectorGroup
    children: tuple[SectorGroup, ...]


@dataclass(frozen=True)
class SecurityMove:
    ticker: str
    name: str
    size_weight_krw: float
    return_pct: float | None


@dataclass(frozen=True)
class SectorMap:
    snapshot_date: date
    session: date
    parents: tuple[SectorParent, ...]
    securities: int
    untraded: int


def wics_label(name: str) -> str:
    return name.removeprefix(WICS_PREFIX).strip()


def _weight(member: SectorMember) -> float | None:
    if member.prev_close is None or member.prev_close <= 0 or member.float_shares <= 0:
        return None
    return member.float_shares * member.prev_close


def _return(member: SectorMember) -> float | None:
    if not member.traded or member.close is None or member.prev_close is None:
        return None
    return (member.close / member.prev_close - 1) * 100


def _group(code: str, name: str, members: Sequence[SectorMember]) -> SectorGroup:
    size = 0.0
    return_weight = 0.0
    weighted_return = 0.0
    untraded = 0
    for member in members:
        weight = _weight(member)
        if weight is None:
            continue
        size += weight
        move = _return(member)
        if move is None:
            untraded += 1
            continue
        return_weight += weight
        weighted_return += weight * move
    return SectorGroup(
        code=code,
        name=wics_label(name),
        size_weight_krw=size,
        return_weight_krw=return_weight,
        return_pct=weighted_return / return_weight if return_weight > 0 else None,
        securities=len(members),
        untraded=untraded,
    )


def build_sector_map(
    snapshot_date: date, session: date, members: Iterable[SectorMember]
) -> SectorMap:
    """L1 → L2 묶음. 크기 내림차순. 가격이 없는 종목은 묶음에 넣지 않는다."""
    priced = [member for member in members if _weight(member) is not None]
    by_parent: dict[str, list[SectorMember]] = {}
    for member in priced:
        by_parent.setdefault(member.l1_code, []).append(member)
    parents: list[SectorParent] = []
    for l1_code, l1_members in by_parent.items():
        by_child: dict[str, list[SectorMember]] = {}
        for member in l1_members:
            by_child.setdefault(member.l2_code, []).append(member)
        children = sorted(
            (_group(code, group[0].l2_name, group) for code, group in by_child.items()),
            key=lambda child: -child.size_weight_krw,
        )
        parents.append(
            SectorParent(_group(l1_code, l1_members[0].l1_name, l1_members), tuple(children))
        )
    parents.sort(key=lambda parent: -parent.group.size_weight_krw)
    return SectorMap(
        snapshot_date=snapshot_date,
        session=session,
        parents=tuple(parents),
        securities=len(priced),
        untraded=sum(parent.group.untraded for parent in parents),
    )


def sector_securities(members: Iterable[SectorMember], l2_code: str) -> tuple[SecurityMove, ...]:
    """한 세부 업종의 종목. 크기 내림차순. 거래가 없던 종목은 등락이 None이다."""
    moves = [
        SecurityMove(member.ticker, member.name, weight, _return(member))
        for member in members
        if member.l2_code == l2_code and (weight := _weight(member)) is not None
    ]
    return tuple(sorted(moves, key=lambda move: (-move.size_weight_krw, move.ticker)))
