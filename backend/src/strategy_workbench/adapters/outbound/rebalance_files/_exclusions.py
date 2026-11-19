"""매매 제외 목록을 작업 폴더의 JSON 파일 하나에서 읽는다(S07, 2026-11-19 사용자 결정).

`<폴더>/exclusions.json`은 사람이 고치는 설정이다. 파일이 없으면 제외 종목이 없고, 있는데 형식이
틀리면 주문안을 만들지 않는다(목록을 조용히 건너뛰면 뺀 종목을 다시 산다).

```json
{
  "schema": "weekly-rebalance-exclusions/1",
  "exclusions": [
    {"symbol": "089860", "reason": "공개매수 대상(11-06 신고)", "until": "2027-11-06"}
  ]
}
```

`until`은 그 신호일까지 뺀다는 뜻이다(그날 포함).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    ExclusionsUnavailableError,
)

EXCLUSIONS_FILE = "exclusions.json"
EXCLUSIONS_SCHEMA = "weekly-rebalance-exclusions/1"
_SYMBOL = re.compile(r"[0-9A-Z]{6}")


class JsonExclusionList:
    def __init__(self, folder: Path) -> None:
        self._path = folder / EXCLUSIONS_FILE

    def active(self, on: date) -> Mapping[str, str]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise ExclusionsUnavailableError(f"{EXCLUSIONS_FILE} unreadable — {error}") from error
        entries = _entries(text)
        return {symbol: reason for symbol, reason, until in entries if on <= until}


def _entries(text: str) -> list[tuple[str, str, date]]:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ExclusionsUnavailableError(f"{EXCLUSIONS_FILE} is not JSON — {error}") from error
    if not isinstance(document, dict) or document.get("schema") != EXCLUSIONS_SCHEMA:
        found = document.get("schema") if isinstance(document, dict) else type(document).__name__
        raise ExclusionsUnavailableError(
            f"{EXCLUSIONS_FILE} schema mismatch — expected={EXCLUSIONS_SCHEMA} got={found!r}"
        )
    items = document.get("exclusions")
    if not isinstance(items, list):
        raise ExclusionsUnavailableError(f"{EXCLUSIONS_FILE} needs an 'exclusions' list")
    entries: list[tuple[str, str, date]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ExclusionsUnavailableError(f"{EXCLUSIONS_FILE} entry {index} is not an object")
        symbol, reason, until = item.get("symbol"), item.get("reason"), item.get("until")
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise ExclusionsUnavailableError(
                f"{EXCLUSIONS_FILE} entry {index} symbol must be 6 characters — got={symbol!r}"
            )
        if symbol in seen:
            raise ExclusionsUnavailableError(
                f"{EXCLUSIONS_FILE} entry {index} repeats symbol={symbol}"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ExclusionsUnavailableError(
                f"{EXCLUSIONS_FILE} entry {index} needs a reason — symbol={symbol}"
            )
        try:
            until_day = date.fromisoformat(until) if isinstance(until, str) else None
        except ValueError:
            until_day = None
        if until_day is None:
            raise ExclusionsUnavailableError(
                f"{EXCLUSIONS_FILE} entry {index} until must be YYYY-MM-DD — "
                f"symbol={symbol} got={until!r}"
            )
        seen.add(symbol)
        entries.append((symbol, reason.strip(), until_day))
    return entries
