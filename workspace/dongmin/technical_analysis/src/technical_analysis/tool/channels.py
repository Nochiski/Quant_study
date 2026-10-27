"""사용자 지정 피보나치 채널 — 세 날짜 + 저가/고가를 종목별로 저장하고 매번 최신 봉까지 다시 그린다.

저장 파일은 개인 판단 자료라 공개 저장소 밖에 둔다(기본 `~/quant-data/technical-analysis/channels.json`,
환경 변수 `TA_CHANNELS` 로 바꿀 수 있다).
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

import numpy as np

from ..fib_channel import Channel

Side = Literal["low", "high"]
_POINT = re.compile(r"^(\d{4}-\d{2}-\d{2}):(low|high)$")


def store_path() -> Path:
    return Path(os.environ.get("TA_CHANNELS", "~/quant-data/technical-analysis/channels.json")).expanduser()


@dataclass(frozen=True)
class PointSpec:
    date: str  # YYYY-MM-DD
    side: Side


def parse_point(text: str) -> PointSpec:
    """'2019-08-06:low' 꼴을 읽는다."""
    m = _POINT.match(text.strip())
    if not m:
        raise ValueError(f"점은 '날짜:low' 또는 '날짜:high' 꼴로 적는다(예: 2019-08-06:low): got {text!r}")
    side: Side = "low" if m.group(2) == "low" else "high"
    return PointSpec(date=m.group(1), side=side)


class ResolveStatus(Enum):
    OK = "ok"
    NOT_A_TRADING_DAY = "not_a_trading_day"
    BAD_ORDER = "bad_order"


@dataclass(frozen=True)
class ResolveResult:
    status: ResolveStatus
    channel: Channel | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is ResolveStatus.OK


def resolve(specs: list[PointSpec], dates: np.ndarray, high: np.ndarray, low: np.ndarray) -> ResolveResult:
    """세 점을 봉 위치와 저가/고가로 바꾼다. 첫 점이 둘째 점보다 앞서야 한다."""
    pos = {str(d): i for i, d in enumerate(dates.astype("datetime64[D]"))}
    missing = [s.date for s in specs if s.date not in pos]
    if missing:
        return ResolveResult(ResolveStatus.NOT_A_TRADING_DAY, detail=f"거래일이 아닌 날짜: {', '.join(missing)}")
    xs = [pos[s.date] for s in specs]
    ys = [float(low[x] if s.side == "low" else high[x]) for s, x in zip(specs, xs, strict=True)]
    if xs[0] >= xs[1]:
        return ResolveResult(ResolveStatus.BAD_ORDER, detail=f"첫 점({specs[0].date})이 둘째 점({specs[1].date})보다 앞서야 한다")
    return ResolveResult(ResolveStatus.OK, Channel(xs[0], ys[0], xs[1], ys[1], xs[2], ys[2]))


@dataclass(frozen=True)
class StoredChannel:
    name: str
    points: list[PointSpec]
    saved_at: str


def load_all(path: Path) -> dict[str, StoredChannel]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {
        key: StoredChannel(
            name=v["name"],
            points=[PointSpec(p["date"], p["side"]) for p in v["points"]],
            saved_at=v.get("saved_at", ""),
        )
        for key, v in raw.items()
    }


def _write(path: Path, items: dict[str, StoredChannel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        k: {"name": v.name, "points": [{"date": p.date, "side": p.side} for p in v.points], "saved_at": v.saved_at}
        for k, v in items.items()
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2))


def save(path: Path, key: str, name: str, specs: list[PointSpec]) -> None:
    items = load_all(path)
    items[key] = StoredChannel(name=name, points=specs, saved_at=datetime.now().isoformat(timespec="seconds"))
    _write(path, items)


def remove(path: Path, key: str) -> bool:
    items = load_all(path)
    if key not in items:
        return False
    del items[key]
    _write(path, items)
    return True
