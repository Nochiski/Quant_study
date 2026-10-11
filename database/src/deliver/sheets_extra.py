"""매일 엑셀 새 시트 여섯 — Analyst · Events · Market · ETF · Flow · Short(DECISIONS §7 #10 · #11).

시트마다 원천을 읽어(`sources` — equity · stage 판 고정) 만들고, 원천 하나가 문제면 그 시트만
'데이터 없음: 사유' 한 줄로 두고 나머지 엑셀은 그대로 낸다(warn 로그) — 새 시트 때문에 매일 엑셀이
멈추지 않는다. 시트 기준일은 그 시트 원천의 최신 날짜(≤ 엑셀 D, 사용자 결정 Q-E2).
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
from openpyxl import Workbook

from . import (
    etf_data,
    sheet_analyst,
    sheet_etf,
    sheet_events,
    sheet_flow,
    sheet_market,
    sheet_short,
)
from .extra_common import Universe, base_text
from .qpack import XRefs, XSheet
from .sources import SourceMissing, Sources, connect

log = logging.getLogger(__name__)

ORDER = ("Analyst", "Events", "Market", "ETF", "Flow", "Short")
# Meta 열 사전 순서 — ETF 먼저(확정 샘플이 ETF 를 먼저 만들었다)
DEFS_ORDER = ("ETF", "Analyst", "Events", "Market", "Flow", "Short")
Defs = list[tuple[str, str, str]]


@dataclass
class Extra:
    defs: Defs = field(default_factory=list)                    # Meta 열 사전 줄(시트, 이름, 정의)
    sparks: dict[str, list[tuple[str, str]]] = field(default_factory=dict)   # 셀 안 막대
    failed: dict[str, str] = field(default_factory=dict)        # 시트 → '데이터 없음' 사유


def _etf(wb: Workbook, src: Sources, refs: XRefs, d: date, uni: Universe, ex: Extra,
         con: duckdb.DuckDBPyConnection, index: int) -> Defs:
    w = sheet_etf.write(wb, etf_data.load(con, src, d), refs, d, index=index)
    ex.sparks[sheet_etf.NAME] = w.sparks
    return w.defs


def _market(wb: Workbook, src: Sources, refs: XRefs, d: date, uni: Universe, ex: Extra,
            con: duckdb.DuckDBPyConnection, index: int) -> Defs:
    return sheet_market.write(wb, sheet_market.load(con, src, d), refs, d, index=index)


def _flow(wb: Workbook, src: Sources, refs: XRefs, d: date, uni: Universe, ex: Extra,
          con: duckdb.DuckDBPyConnection, index: int) -> Defs:
    return sheet_flow.write(wb, sheet_flow.load(con, src, d, uni), uni, refs, d, index=index)


def _short(wb: Workbook, src: Sources, refs: XRefs, d: date, uni: Universe, ex: Extra,
           con: duckdb.DuckDBPyConnection, index: int) -> Defs:
    return sheet_short.write(wb, sheet_short.load(con, src, d, uni), uni, refs, d, index=index)


def _analyst(wb: Workbook, src: Sources, refs: XRefs, d: date, uni: Universe, ex: Extra,
             con: duckdb.DuckDBPyConnection, index: int) -> Defs:
    return sheet_analyst.write(wb, sheet_analyst.load(con, src, d, uni), uni, refs, d,
                               index=index)


def _events(wb: Workbook, src: Sources, refs: XRefs, d: date, uni: Universe, ex: Extra,
            con: duckdb.DuckDBPyConnection, index: int) -> Defs:
    return sheet_events.write(wb, sheet_events.load(con, src, d, uni), uni, refs, d,
                              index=index)


Builder = Callable[[Workbook, Sources, XRefs, date, Universe, Extra, duckdb.DuckDBPyConnection,
                    int], Defs]
BUILDERS: dict[str, Builder] = {"Analyst": _analyst, "Events": _events, "Market": _market,
                                "ETF": _etf, "Flow": _flow, "Short": _short}


def missing_sheet(wb: Workbook, name: str, reason: str, refs: XRefs, d: date,
                  index: int) -> None:
    """원천 문제로 못 만든 시트 — 1~5행 띠 + '데이터 없음: 사유' 한 줄."""
    xs = XSheet(wb, name, {"A": 7.5, "B": 26}, refs, ncol=12, index=index)
    xs.texts({"A2": f"{name}: 데이터 없음", "A4": base_text(d, d)})
    xs.put(6, 1, f"데이터 없음: {reason}", text=True)
    xs.page("A1:L6")


def build(wb: Workbook, src: Sources, refs: XRefs, d: date, uni: Universe,
          *, index: int, tmp_root: Path) -> Extra:
    """`index` 자리부터 ORDER 순서로 시트를 넣는다. 반환 = Meta 열 사전 · 셀 안 막대 · 실패 사유.
    여섯 시트가 DuckDB 연결 하나(메모리 상한 하나)를 같이 쓰고, 메모리를 넘길 때의 임시 파일은
    `tmp_root/duckdb_<pid>` 에 두었다가 끝나면 지운다."""
    ex = Extra()
    defs: dict[str, Defs] = {}
    tmp = Path(tmp_root) / f"duckdb_{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    con = connect(tmp)
    try:
        for k, name in enumerate(ORDER):
            t = time.monotonic()
            try:
                defs[name] = BUILDERS[name](wb, src, refs, d, uni, ex, con, index + k)
                log.info("새 시트 %s %.1f초", name, time.monotonic() - t)
            except Exception as e:  # noqa: BLE001  # reason: 시트 하나 실패가 엑셀 전체를 막지 않는다(Q-E2 · 플랜 E4)
                reason = (str(e) if isinstance(e, SourceMissing)
                          else f"계산 오류({type(e).__name__}) — 로그 참고")
                log.warning("새 시트 %s 를 못 만들어 '데이터 없음'으로 둔다 — %s", name, reason,
                            exc_info=not isinstance(e, SourceMissing))
                if name in wb.sheetnames:
                    wb.remove(wb[name])
                ex.sparks.pop(name, None)
                ex.failed[name] = reason
                missing_sheet(wb, name, reason, refs, d, index + k)
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)
        with contextlib.suppress(OSError):      # 비었을 때만 지워진다(다른 실행이 쓰는 중이면 남긴다)
            tmp.parent.rmdir()
    ex.defs = [row for name in DEFS_ORDER for row in defs.get(name, [])]
    return ex


__all__ = ["DEFS_ORDER", "ORDER", "Extra", "build", "missing_sheet"]
