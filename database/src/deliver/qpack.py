"""삼성증권 Q.Pack 문법 시트 작성기 (MODEL_EXCEL_SPEC C·D).

  1행   정렬 마커(연한 파랑 BDD7EE, 가운데) — 'sort' · 정렬 키 열 '▲' · 나머지 's'
  2~4행 제목 띠(파랑 4F81BD, 흰 글씨) — 시트명 / 모델·버전 / 기준일·Source·판 id
  5행   각주(회색 기울임). `defs_row` 면 열마다 정의 문자열
  6~7행 2단 헤더 — 6행 그룹(파랑, 종합·순위 그룹은 금색 FFC000), 7행 열 이름(회색 7F7F7F),
        핵심 열은 보라 7030A0, 얇은 테두리로 열마다 상자, 7행 높이 33.75·줄바꿈
  8행~  데이터(채움 없음 · 위 정렬 · 맑은 고딕 8 · 기본 높이 11.25)
  열 너비 — 값·헤더에 맞춘 최소 폭(Q.Pack 실측: 보이는 숫자 열 4.4~6.4, 종목명 13~15). 10-05 전엔
        숫자 열을 13 으로 고정해 Q.Pack 의 두 배 넘게 넓었다(사용자 지적).
  그룹 사이 빈 구분 열(너비 5 · 숨김 · 아웃라인 1) · 식별 열 끝에서 틀고정 · 7행 자동필터
  색 스케일 3색 백분위 10/50/90: 빨강 F8696B → 노랑 FFEB84 → 초록 63BE7B(높음 = 초록 = 좋음,
  2026-10-06 사용자 결정 N-15 — Q.Pack 의 '높음 = 빨강'과 반대). 순위 열은 반전(1위 = 초록).
  Δ순위 열(`zero_mid`)은 가운데 = 0, 끝점 ±|값| 90 백분위(N-25 Q8). 레벨 값은 무색.

**값만 쓴다.** '=' 로 시작하는 문자열도 수식이 되지 않게 문자열 형으로 고정한다.
"""
from __future__ import annotations

import math
import os
import unicodedata
import zipfile
from collections.abc import Mapping, Sequence
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from openpyxl.formatting.rule import ColorScaleRule, Rule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.indexed_list import IndexedList
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from .common import quantile

FONT_NAME = "맑은 고딕"
FONT_SIZE = 8
ROW_HEIGHT = 11.25
HEADER_HEIGHT = 33.75
DEFS_HEIGHT = ROW_HEIGHT    # 정의 행도 한 줄(Q.Pack 5행 각주와 같은 높이) — 전문은 메타 시트
SEP_WIDTH = 5.0
MIN_WIDTH = 4.4             # Q.Pack 실측 보이는 열 최소(Sector 4.43)
NUM_MAX_WIDTH = 12.0        # 숫자 열 상한 — 넘는 값은 없다(시총 억원 9자리도 9 안팎)
TXT_MAX_WIDTH = 9.0         # 너비를 지정하지 않은 문자 열 상한(지정하면 그 값이 상한)
FIRST_DATA_ROW = 8
# 표준(Normal) 스타일 글꼴 — 큐팩 원본 styles.xml 과 같은 Arial 10
# (글꼴 0번·'표준' 스타일). 엑셀은 행 번호·열 머리를 이 글꼴로 그린다.
# openpyxl 기본 Calibri 11 은 줄 높이(약 18px)가 11.25pt 행(15px)보다 커서
# 행 번호가 잘린다(10-06 사용자 지적).
NORMAL_FONT = Font(name="Arial", sz=10, family=2)

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

# spark = 셀 안 꺾은선(엑셀 스파크라인) 자리 — 값 없이 지정 너비 그대로.
# 그림은 저장 뒤 add_sparklines 가 넣는다.
KINDS = ("id", "txt", "num", "pct", "chg", "rank", "spark")
_THIN = Side(style="thin", color="808080")
_MEDIUM = Side(style="medium", color="1F3864")


def new_workbook() -> Workbook:
    """표준 글꼴을 `NORMAL_FONT` 로 둔 새 통합문서."""
    wb = Workbook()
    # openpyxl 에는 기본 글꼴을 바꾸는 공개 API 가 없다. 셀 스타일이 하나도
    # 없는 지금 글꼴 0번을 갈아 끼우고 표준 스타일을 다시 계산하게 한다.
    wb._fonts = IndexedList([copy(NORMAL_FONT)])
    wb._named_styles["Normal"].font = copy(NORMAL_FONT)
    return wb


def font(**kw: object) -> Font:
    return Font(name=FONT_NAME, size=FONT_SIZE, **kw)  # type: ignore[arg-type]


def fill(color: str) -> PatternFill:
    return PatternFill("solid", fgColor=color)


def scale_high_good() -> Rule:
    """백분위·변화율 열 — 높음 = 초록(좋음), 낮음 = 빨강(나쁨)."""
    return ColorScaleRule(start_type="percentile", start_value=10, start_color=RED,
                          mid_type="percentile", mid_value=50, mid_color=YELLOW,
                          end_type="percentile", end_value=90, end_color=GREEN)


def scale_zero_mid(values: Sequence[object]) -> Rule:
    """Δ순위 열 — 가운데 = 0(노랑), 음수 빨강 · 양수 초록. 끝점 = |값|의 90 백분위(선형 보간)를
    ±M 으로 대칭에 둔다 — 백분위 50 가운데는 유니버스가 바뀌는 날 중앙값이 0 에서 벗어나 소폭 하락이
    연두로 보였다(E-06, Q8). 값이 없거나 전부 0 이면 M = 1."""
    mags = sorted(abs(float(v)) for v in values
                  if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v))
    m = quantile(mags, 0.9) if mags else 0.0
    m = m if m > 0 else 1.0
    return ColorScaleRule(start_type="num", start_value=-m, start_color=RED,
                          mid_type="num", mid_value=0, mid_color=YELLOW,
                          end_type="num", end_value=m, end_color=GREEN)


def scale_rank() -> Rule:
    """순위 열 — 1위(작은 값) = 초록(좋음)."""
    return ColorScaleRule(start_type="percentile", start_value=10, start_color=GREEN,
                          mid_type="percentile", mid_value=50, mid_color=YELLOW,
                          end_type="percentile", end_value=90, end_color=RED)


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
    zero_mid: bool = False      # 색 가운데 = 0(Δ순위 — 부호와 색이 같은 쪽, E-06)

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


def _text_width(s: str, wide: float, narrow: float) -> float:
    """맑은 고딕 8 표시 폭(엑셀 열 너비 단위) 근사 — 한글 등 전각은 wide, 그 밖은 narrow."""
    return sum(wide if unicodedata.east_asian_width(ch) in ("W", "F") else narrow for ch in s)


def _shown(v: object, fmt: str | None) -> str:
    """셀에 보이는 문자열 근사 — 숫자는 서식(#,##0.0 등)대로, 그 밖은 str."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        return "" if v is None else str(v)
    if isinstance(v, float) and not math.isfinite(v):
        return ""
    if not fmt or fmt == "General":
        return str(v)
    pct = fmt.endswith("%")
    dec = len(fmt.split(".")[1].rstrip("%")) if "." in fmt else 0
    x = v * 100 if pct else v
    s = f"{x:,.{dec}f}" if "," in fmt else f"{x:.{dec}f}"
    return s + ("%" if pct else "")


def fit_width(col: Col, rows: Sequence[Mapping[str, object]]) -> float:
    """열 너비 = 값·헤더가 들어가는 최소 폭(Q.Pack 처럼 촘촘히). 숫자는 '####' 가 안 나게 넉넉히 재고,
    헤더는 낱말(공백·줄바꿈으로 나뉨)이 한 줄에 온전히 들어가게 잰다 — 7행 3줄 안에서 낱말 사이로만
    접혀 '유니버/스' 처럼 가운데서 끊기지 않는다(10-05 렌더 확인). 상한 = 지정 너비 또는 종류별 기본."""
    if col.kind == "spark":
        return col.width or MIN_WIDTH
    numeric = col.kind not in ("id", "txt")
    wide, narrow, pad = (1.6, 0.85, 1.0) if numeric else (1.55, 0.8, 0.7)
    need_hdr = max((_text_width(w, 1.55, 0.8) for w in col.label.split()), default=0.0) + 0.7
    need_val = max((_text_width(_shown(r.get(col.key), col.fmt), wide, narrow) for r in rows),
                   default=0.0) + pad
    cap = col.width or (NUM_MAX_WIDTH if numeric else TXT_MAX_WIDTH)
    return round(min(cap, max(MIN_WIDTH, need_hdr, need_val)), 2)


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


def tighten_rows(ws: Worksheet) -> None:
    """높이를 정하지 않은 행에 11.25 를 박는다(이미 정한 7행 33.75 등은 그대로).

    시트 기본 높이(sheetFormatPr)만 두면 엑셀·LibreOffice 가 통합 문서 기본 글꼴(Calibri 11)로 행을
    다시 잡아 Q.Pack 보다 1.4배 높게 그린다 — 10-05 렌더: 한 쪽 29행 vs Q.Pack 37행, 행마다 박으면 37행.
    """
    for r in range(1, ws.max_row + 1):
        if ws.row_dimensions[r].height is None:
            ws.row_dimensions[r].height = ROW_HEIGHT


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
        ws.column_dimensions[letter].width = fit_width(col, rows)
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
            if col.zero_mid:
                rule = scale_zero_mid([r.get(col.key) for r in rows])
            else:
                rule = scale_rank() if col.kind == "rank" else scale_high_good()
            ws.conditional_formatting.add(f"{letter}{FIRST_DATA_ROW}:{letter}{last}", rule)
    n_freeze = sum(len(g.cols) for g in groups[:n_freeze_groups]) + max(0, n_freeze_groups - 1)
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, n_freeze + 1)
    ws.auto_filter.ref = f"A7:{get_column_letter(ncol)}{max(7, last)}"
    tighten_rows(ws)
    return [(title.sheet, g.name, col.label.replace("\n", " "), col.definition)
            for _c, g, col in layout if g is not None and col is not None]


def write_meta(wb: Workbook, title: Title, pairs: Sequence[tuple[str, object]],
               dictionary: Sequence[tuple[str, str, str, str]]) -> None:
    """메타 시트 — 키·값 표(8행~) 뒤에 열 사전(시트 · 그룹·열 · 정의)."""
    ws = wb.create_sheet(title.sheet)
    title_block(ws, title, 3, None)
    def_width = 110.0
    for letter, width in (("A", 18.0), ("B", 34.0), ("C", def_width)):
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
        # 정의가 접히는 줄 수만큼만 높인다(자동 높이는 기본 글꼴 기준이라 헐겁다 — tighten_rows)
        lines = math.ceil(_text_width(str(definition or ""), 1.55, 0.8) / (def_width - 1))
        ws.row_dimensions[r].height = ROW_HEIGHT * max(1, lines)
        r += 1
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, 1)
    tighten_rows(ws)


# ── 셀 안 꺾은선(엑셀 스파크라인) ───────────────────────────────────────────
# openpyxl 은 스파크라인을 읽지도 쓰지도 못한다. 저장한 xlsx 의 시트 XML 끝 extLst 에 x14
# sparklineGroups 를 넣는다(엑셀 2010+ 확장. LibreOffice 도 읽는다 — 10-06 왕복 확인).
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_SPARK_URI = "{05C60535-1F16-4fd2-B633-F4F36F0B64E0}"
# 선 두께(pt) — 엑셀이 고를 수 있는 가장 얇은 값. 1pt 는 11.25pt 행에서 굵어 보였다(10-06 지적).
SPARK_LINE_WEIGHT = 0.25


@dataclass(frozen=True)
class SparkGroup:
    """같은 선 색의 스파크라인 묶음. cells = (원자료 범위, 그릴 셀) — 예 ("'시트'!B8:W8", "M8")."""

    color: str
    cells: tuple[tuple[str, str], ...]


def sparkline_xml(groups: Sequence[SparkGroup]) -> str:
    body = []
    for g in groups:
        c = f'rgb="FF{g.color}"'
        colors = "".join(f"<x14:{k} {c}/>" for k in (
            "colorSeries", "colorNegative", "colorAxis", "colorMarkers", "colorFirst",
            "colorLast", "colorHigh", "colorLow"))
        lines = "".join(f"<x14:sparkline><xm:f>{escape(ref)}</xm:f><xm:sqref>{escape(cell)}"
                        "</xm:sqref></x14:sparkline>" for ref, cell in g.cells)
        body.append('<x14:sparklineGroup displayEmptyCellsAs="gap" '
                    f'lineWeight="{SPARK_LINE_WEIGHT}">'
                    f"{colors}<x14:sparklines>{lines}</x14:sparklines></x14:sparklineGroup>")
    return (f'<ext uri="{_SPARK_URI}" '
            'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
            '<x14:sparklineGroups xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main">'
            + "".join(body) + "</x14:sparklineGroups></ext>")


def _sheet_part(z: zipfile.ZipFile, title: str) -> str:
    wb = ElementTree.fromstring(z.read("xl/workbook.xml"))
    rid = next((s.get(f"{{{_NS_REL}}}id") for s in wb.iter(f"{{{_NS_MAIN}}}sheet")
                if s.get("name") == title), None)
    rels = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = next((r.get("Target") for r in rels.iter(f"{{{_NS_PKG}}}Relationship")
                   if rid is not None and r.get("Id") == rid), None)
    if target is None:
        raise ValueError(f"시트 {title!r} 를 xlsx 에서 찾지 못했다")
    return target.lstrip("/") if target.startswith("/") else f"xl/{target}"


def add_sparklines(path: Path, sheet: str, groups: Sequence[SparkGroup]) -> int:
    """저장된 xlsx 의 `sheet` 에 스파크라인을 넣고 그린 셀 수를 돌려준다(파일은 원자적 교체)."""
    groups = [g for g in groups if g.cells]
    if not groups:
        return 0
    path = Path(path)
    tmp = path.with_name(f".{path.name}.spark.tmp")
    with zipfile.ZipFile(path) as zin:
        part = _sheet_part(zin, sheet)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == part:
                    xml = data.decode("utf-8")
                    ext = sparkline_xml(groups)
                    if "</extLst>" in xml:
                        xml = xml.replace("</extLst>", ext + "</extLst>", 1)
                    else:
                        xml = xml.replace("</worksheet>", f"<extLst>{ext}</extLst></worksheet>", 1)
                    data = xml.encode("utf-8")
                zout.writestr(item, data)
    os.replace(tmp, path)
    return sum(len(g.cells) for g in groups)


__all__ = [
    "C_BAND", "C_GOLD", "C_HDR", "C_KEY", "C_NEW", "C_STRIP", "C_STYLE", "FIRST_DATA_ROW",
    "FONT_NAME", "GREEN", "RED", "YELLOW", "Col", "Group", "Title", "clean", "fill", "font",
    "SparkGroup", "add_sparklines", "new_workbook", "put", "scale_high_good", "scale_rank",
    "scale_zero_mid", "sparkline_xml", "title_block", "write_meta", "write_table",
]
