"""삼성증권 Q.Pack 문법 시트 작성기 (MODEL_EXCEL_SPEC C·D).

  1행   정렬 마커(연한 파랑 BDD7EE, 가운데) — 'sort' · 정렬 키 열 '▲' · 나머지 's'
  2~4행 제목 띠(파랑 4F81BD, 흰 글씨) — 시트명 / 모델·버전 / 기준일·Source·판 id
  5행   각주(회색 기울임). `defs_row` 면 열마다 정의 문자열
  6~7행 2단 헤더 — 6행 그룹(파랑, 종합·순위 그룹은 금색 FFC000), 7행 열 이름(회색 7F7F7F),
        핵심 열은 보라 7030A0, 얇은 테두리로 열마다 상자, 7행 높이 33.75·줄바꿈
  8행~  데이터(채움 없음 · 위 정렬 · 맑은 고딕 8 · 기본 높이 11.25)
  그룹 사이 빈 구분 열(너비 5 · 숨김 · 아웃라인 1) · 식별 열 끝에서 틀고정 · 7행 자동필터
  색 스케일 3색 백분위 10/50/90: 초록 63BE7B → 노랑 FFEB84 → 빨강 F8696B(높음 = 빨강).
  순위 열은 반전(1위 = 빨강). 레벨 값은 무색.

**값만 쓴다.** '=' 로 시작하는 문자열도 수식이 되지 않게 문자열 형으로 고정한다.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openpyxl.formatting.rule import ColorScaleRule, Rule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

FONT_NAME = "맑은 고딕"
FONT_SIZE = 8
ROW_HEIGHT = 11.25
HEADER_HEIGHT = 33.75
DEFS_HEIGHT = 56.25
NUM_WIDTH = 13.0
SEP_WIDTH = 5.0
FIRST_DATA_ROW = 8

C_STRIP = "BDD7EE"      # 1행 — accent1 60% 밝게
C_BAND = "4F81BD"       # 2~4행 제목 띠 · 6행 그룹 헤더 — accent1
C_HDR = "7F7F7F"        # 7행 열 이름 — 흰색 50% 어둡게
C_KEY = "7030A0"        # 핵심 열(보라)
C_GOLD = "FFC000"       # 종합·순위 그룹 — accent4
C_STYLE = "0070C0"      # 업종 시트 헤더(Q.Pack Style 시트)
C_NOTE = "595959"
C_NEW = "FFFF00"        # 형광 — '신규 진입' 한 종류만(D)
C_WHITE = "FFFFFF"
GREEN, YELLOW, RED = "63BE7B", "FFEB84", "F8696B"

KINDS = ("id", "txt", "num", "pct", "chg", "rank")
_THIN = Side(style="thin", color="808080")
_MEDIUM = Side(style="medium", color="1F3864")


def font(**kw: object) -> Font:
    return Font(name=FONT_NAME, size=FONT_SIZE, **kw)  # type: ignore[arg-type]


def fill(color: str) -> PatternFill:
    return PatternFill("solid", fgColor=color)


def scale_high_red() -> Rule:
    """백분위·변화율 열 — 높음 = 빨강."""
    return ColorScaleRule(start_type="percentile", start_value=10, start_color=GREEN,
                          mid_type="percentile", mid_value=50, mid_color=YELLOW,
                          end_type="percentile", end_value=90, end_color=RED)


def scale_rank() -> Rule:
    """순위 열 — 1위(작은 값) = 빨강."""
    return ColorScaleRule(start_type="percentile", start_value=10, start_color=RED,
                          mid_type="percentile", mid_value=50, mid_color=YELLOW,
                          end_type="percentile", end_value=90, end_color=GREEN)


def clean(v: object) -> object:
    """셀 값 정리 — NaN·inf 는 빈칸, bool 은 'Y'/''(엑셀 TRUE/FALSE 대신)."""
    if isinstance(v, bool):
        return "Y" if v else ""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def put(ws: Worksheet, row: int, col: int, v: object) -> Any:
    """값만 쓴다 — '=' 로 시작하는 문자열도 수식이 아니라 문자열로 둔다. 반환 = openpyxl 셀."""
    cell: Any = ws.cell(row, col)
    val: Any = clean(v)
    cell.value = val
    if isinstance(val, str) and val.startswith("="):
        cell.data_type = "s"
    return cell


@dataclass(frozen=True)
class Col:
    """열 하나. kind: id(식별) · txt(문자) · num(레벨, 무색) · pct(백분위) · chg(변화) ·
    rank(순위)."""

    key: str
    label: str
    kind: str = "num"
    fmt: str | None = "#,##0.0"
    width: float | None = None
    key_col: bool = False       # 보라 헤더
    definition: str = ""
    item: bool = False          # 업종 시트 항목명(보라 글씨)
    color: bool | None = None   # None = kind 기본(pct·chg·rank 만 색)

    @property
    def colored(self) -> bool:
        return self.kind in ("pct", "chg", "rank") if self.color is None else self.color


@dataclass(frozen=True)
class Group:
    name: str
    cols: tuple[Col, ...]
    core: bool = False          # 종합·순위 그룹(금색)


@dataclass(frozen=True)
class Title:
    sheet: str
    model_line: str
    source_line: str
    note: str = ""


def _layout(groups: Sequence[Group]) -> list[tuple[int, Group | None, Col | None]]:
    out: list[tuple[int, Group | None, Col | None]] = []
    c = 1
    for gi, g in enumerate(groups):
        if gi > 0:
            out.append((c, None, None))
            c += 1
        for col in g.cols:
            out.append((c, g, col))
            c += 1
    return out


def title_block(ws: Worksheet, title: Title, ncol: int, sort_col: int | None) -> None:
    ws.sheet_view.showGridLines = False
    ws.sheet_format.defaultRowHeight = ROW_HEIGHT
    ws.sheet_format.customHeight = True
    for i in range(1, ncol + 1):
        marker = "sort" if i == 1 else ("▲" if i == sort_col else "s")
        cell = put(ws, 1, i, marker)
        cell.font = font()
        cell.fill = fill(C_STRIP)
        cell.alignment = Alignment(horizontal="center")
        for r in (2, 3, 4):
            ws.cell(r, i).fill = fill(C_BAND)
    for r, text, bold in ((2, title.sheet, True), (3, title.model_line, False),
                          (4, title.source_line, False)):
        cell = put(ws, r, 1, text)
        cell.font = font(bold=bold, color=C_WHITE)
        cell.alignment = Alignment(horizontal="left")
    if title.note:
        put(ws, 5, 1, title.note).font = font(italic=True, color=C_NOTE)


def write_table(wb: Workbook, title: Title, groups: Sequence[Group],
                rows: Sequence[Mapping[str, object]], *, sort_key: str | None = None,
                defs_row: bool = False, style: str = "qpack",
                n_freeze_groups: int = 1) -> list[tuple[str, str, str, str]]:
    """표 시트 하나. 반환 = 열 사전 [(시트, 그룹, 열, 정의)] — 메타 시트가 모은다.

    행 dict 의 `_fills`({열 key: 색})로 셀 채움을 줄 수 있다(신규 진입 형광).
    """
    ws = wb.create_sheet(title.sheet)
    layout = _layout(groups)
    ncol = layout[-1][0] if layout else 1
    sort_col = next((c for c, _, col in layout if col is not None and col.key == sort_key), None)
    title_block(ws, title, ncol, sort_col)

    hdr_side = _MEDIUM if style == "sector" else _THIN
    ident = {id(g) for g in groups[:n_freeze_groups]}     # 식별 그룹 = 틀고정 그룹(회색 헤더)
    first_in_group: set[int] = set()
    seen: set[str] = set()
    for c, g, col in layout:
        letter = get_column_letter(c)
        if g is None or col is None:
            dim = ws.column_dimensions[letter]
            dim.width, dim.hidden, dim.outlineLevel = SEP_WIDTH, True, 1
            continue
        if g.name not in seen:
            seen.add(g.name)
            first_in_group.add(c)
        ws.column_dimensions[letter].width = col.width or (
            NUM_WIDTH if col.kind not in ("id", "txt") else 9.0)
        is_id = col.kind == "id" or id(g) in ident
        if style == "sector":
            g_fill, l_fill = C_STYLE, C_STYLE
        else:
            g_fill = C_KEY if col.key_col else (C_HDR if is_id else (C_GOLD if g.core else C_BAND))
            l_fill = C_KEY if col.key_col else C_HDR
        on_gold = g_fill == C_GOLD
        h6 = put(ws, 6, c, g.name if c in first_in_group else None)
        h6.fill = fill(g_fill)
        h6.font = font(bold=True, color="000000" if on_gold else C_WHITE)
        h6.alignment = Alignment(horizontal="center", vertical="top", wrap_text=False)
        h6.border = Border(left=hdr_side, right=hdr_side, top=hdr_side)
        h7 = put(ws, 7, c, col.label)
        h7.fill = fill(l_fill)
        h7.font = font(bold=col.key_col or style == "sector", color=C_WHITE)
        h7.alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)
        h7.border = Border(left=hdr_side, right=hdr_side, bottom=hdr_side)
        if defs_row and col.definition and not is_id:
            d5 = put(ws, 5, c, col.definition)
            d5.font = font(italic=True, color=C_NOTE)
            d5.alignment = Alignment(vertical="top", wrap_text=True)
    ws.row_dimensions[7].height = HEADER_HEIGHT
    if defs_row:
        ws.row_dimensions[5].height = DEFS_HEIGHT

    for ri, row in enumerate(rows, start=FIRST_DATA_ROW):
        fills = row.get("_fills") or {}
        assert isinstance(fills, Mapping)
        for c, _g, col in layout:
            if col is None:
                continue
            v = row.get(col.key)
            cell = put(ws, ri, c, v)
            cell.font = font(color=C_KEY) if col.item else font()
            text_like = col.kind in ("id", "txt") or isinstance(cell.value, str)
            cell.alignment = Alignment(vertical="top",
                                       horizontal="left" if text_like else "right")
            if col.fmt and col.kind not in ("id", "txt") and isinstance(cell.value, int | float):
                cell.number_format = col.fmt
            if col.key in fills:
                cell.fill = fill(str(fills[col.key]))

    last = FIRST_DATA_ROW + len(rows) - 1
    if rows:
        for c, _g, col in layout:
            if col is None or not col.colored:
                continue
            letter = get_column_letter(c)
            rule = scale_rank() if col.kind == "rank" else scale_high_red()
            ws.conditional_formatting.add(f"{letter}{FIRST_DATA_ROW}:{letter}{last}", rule)
    n_freeze = sum(len(g.cols) for g in groups[:n_freeze_groups]) + max(0, n_freeze_groups - 1)
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, n_freeze + 1)
    ws.auto_filter.ref = f"A7:{get_column_letter(ncol)}{max(7, last)}"
    return [(title.sheet, g.name, col.label.replace("\n", " "), col.definition)
            for _c, g, col in layout if g is not None and col is not None]


def write_meta(wb: Workbook, title: Title, pairs: Sequence[tuple[str, object]],
               dictionary: Sequence[tuple[str, str, str, str]]) -> None:
    """메타 시트 — 키·값 표(8행~) 뒤에 열 사전(시트 · 그룹·열 · 정의)."""
    ws = wb.create_sheet(title.sheet)
    title_block(ws, title, 3, None)
    for letter, width in (("A", 18.0), ("B", 34.0), ("C", 110.0)):
        ws.column_dimensions[letter].width = width

    def header(r: int, labels: Sequence[str]) -> None:
        for i, text in enumerate(labels, start=1):
            cell = put(ws, r, i, text)
            cell.fill = fill(C_HDR)
            cell.font = font(bold=True, color=C_WHITE)
            cell.alignment = Alignment(horizontal="center", vertical="top")
            cell.border = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

    g6 = put(ws, 6, 1, "메타")
    g6.fill, g6.font = fill(C_BAND), font(bold=True, color=C_WHITE)
    for i in (2, 3):
        ws.cell(6, i).fill = fill(C_BAND)
    header(7, ("항목", "값", ""))
    r = FIRST_DATA_ROW
    for key, value in pairs:
        put(ws, r, 1, key).font = font(bold=True)
        cell = put(ws, r, 2, value)
        cell.font = font()
        cell.alignment = Alignment(vertical="top", horizontal="left")
        ws.cell(r, 1).alignment = Alignment(vertical="top")
        r += 1
    r += 1
    header(r, ("시트", "그룹 · 열", "정의"))
    r += 1
    for sheet, group, label, definition in dictionary:
        put(ws, r, 1, sheet).font = font()
        put(ws, r, 2, f"{group} · {label}").font = font()
        cell = put(ws, r, 3, definition)
        cell.font = font()
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        r += 1
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, 1)


__all__ = [
    "C_BAND", "C_GOLD", "C_HDR", "C_KEY", "C_NEW", "C_STRIP", "C_STYLE", "FIRST_DATA_ROW",
    "FONT_NAME", "GREEN", "RED", "YELLOW", "Col", "Group", "Title", "clean", "fill", "font",
    "put", "scale_high_red", "scale_rank", "title_block", "write_meta", "write_table",
]
