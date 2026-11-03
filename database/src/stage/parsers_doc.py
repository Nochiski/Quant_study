"""문서층 파서 — DART 보고서 XML 1건을 stage 행으로 (DOC_DESIGN v1.1 §2.2·§2.3, 실측 §1).

순수 함수만: 바이트 → 텍스트(§1.3) → 정제 5규칙(§2.3) → expat/관대 트리 → 헤더·목차·정정 첫 장·
표 계상. 출력 값은 전부 str|None — 캐스팅은 rules_doc 이, I/O 는 doc_prepass 가 한다.
"""
from __future__ import annotations

import datetime as dt
import html
import io
import json
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from html.parser import HTMLParser

from .doc_vocab import DocVocab

# 정제·파싱 규칙이 바뀌면 올린다 — 프리패스 summary.json 에 기록.
# p1.5(2026-09-28): 정정신고 첫 장 2025년 서식. 값이 앵커와 **다른 셀·다른 줄**로 갈라졌고
#   (`2. …최초제출일 :` 다음 줄에 `2024.03.20`), `3. 정정사유` 앵커가 사라져 정정사유가
#   정정사항 표의 항목별 열이 됐다 — 세 원문 필드가 2025년 96.6% · 2026년 99.1% NULL 이었다.
# p1.6(2026-11-01): 잠정실적 공정공시(xforms HTML) 서식 표 → stg_doc_prelim(N-64). html 갈래의 meta ·
#   parse_log 행은 그대로이고, 제목이 '영업(잠정)실적' 인 문서에만 잠정 행 1개가 더 생긴다.
# p1.7(2026-11-03): 잠정 서식 단위 '조원'(시총 최상위 종목 서식, 소수 둘째 자리). p1.6 은 unit:조원 실패 —
#   11-03 백필 877건 중 6건. 다른 표 · 다른 문서 산출 불변(바뀌는 문서는 `--scan '단위\s*:\s*조원'` 로 찾는다).
PARSER_VERSION = "p1.7"
STD_ENTITIES = frozenset({"amp", "lt", "gt", "quot", "apos"})


@dataclass(frozen=True)
class Decoded:
    text: str
    enc: str            # utf-8 | cp949 | cp949~ (치환 있음)
    n_repl: int


def decode_bytes(raw: bytes) -> Decoded:
    """§1.3 — 선언 무시, 바이트로 판정: utf-8 strict → cp949 strict → cp949 replace(치환 수 기록).

    BOM(U+FEFF)은 떼어낸다 — 텍스트 등식(§1.12)이 BOM 으로 깨지지 않게.
    """
    for enc in ("utf-8", "cp949"):
        try:
            return Decoded(raw.decode(enc).lstrip("﻿"), enc, 0)
        except UnicodeDecodeError:
            continue
    text = raw.decode("cp949", errors="replace").lstrip("﻿")
    return Decoded(text, "cp949~", text.count("�"))


_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ENT_RE = re.compile(r"&([A-Za-z][\w.-]*);")
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_TOKEN_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9:_.-]*)")     # §1.6 Name 토큰 정의
# 규칙 5 — 속성 따옴표 복구(Y510·Y2040 실측 세 꼴). 값 클래스가 `=` 를 막아 빈 속성은 안 건드린다.
#  a) `=""값"` · `=""값""` · `="" 값""` · `="" 값"` → `="값"`   b) `="값""` → `="값"`
#  c) 값 안의 `"` — 닫는 따옴표는 뒤에 `>`·`/>`·다음 속성(`NAME=`)이 오는 것뿐이고, 그 앞의 `"` 는
#     전부 값의 일부 → `&quot;` (`="JV "UZAUTO-INZI" LLC"` → `="JV &quot;UZAUTO-INZI&quot; LLC"`).
#     `&quot;` 는 트리에서 다시 `"` 가 되므로 본문에 잘못 걸려도 글자는 안 바뀐다(D10 이 보증).
_ATTR_REPAIR_RE = re.compile(r'=""([^"<>=]*)""?(?=[\s/>])')
_ATTR_REPAIR2_RE = re.compile(r'(?<==")([^"<>=]+)""(?=[\s/>])')
_ATTR_END = r'\s*(?:/?>|[A-Za-z][\w:.-]*=)'
_ATTR_VAL_RE = re.compile(rf'="((?:[^"<>]|"(?!{_ATTR_END}))+)"(?={_ATTR_END})')


@dataclass(frozen=True)
class Sanitized:
    text: str
    n_ctrl: int
    n_ent_other: int
    n_bare_amp: int
    n_bare_lt: int
    n_attr_repair: int
    unknown_tags: tuple[str, ...]       # 규칙 4 로 강등된 Name 토큰 (정렬, 중복 제거)
    other_entities: tuple[str, ...]     # 규칙 2 의 비표준 엔티티 이름 (cr 제외)


def _tag_regex(tags: frozenset[str]) -> re.Pattern[str]:
    alt = "|".join(sorted(map(re.escape, tags), key=len, reverse=True))
    return re.compile(rf"<(?!/?(?:{alt})(?=[\s/>])|!|\?)")


def sanitize(text: str, tags: frozenset[str]) -> Sanitized:
    """§2.3 정제 5규칙, 순서 고정. 글자는 바꾸지 않는다(§1.12 텍스트 등식이 이를 검증)."""
    t, n_ctrl = _CTRL_RE.subn("", text)
    others: Counter[str] = Counter()

    def ent(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in STD_ENTITIES:
            return m.group(0)
        if name == "cr":
            return "&#10;"
        others[name] += 1
        return f"[&amp;{name};]"     # 규칙 3 의 lookahead 를 통과하는 표기 (n_bare_amp 오염 방지)

    t = _ENT_RE.sub(ent, t)
    t, n_amp = _BARE_AMP_RE.subn("&amp;", t)
    tag_re = _tag_regex(tags)
    unknown: Counter[str] = Counter()
    for m in tag_re.finditer(t):
        tok = _TOKEN_RE.match(t, m.start())
        if tok is not None:
            unknown[tok.group(2)] += 1
    t, n_lt = tag_re.subn("&lt;", t)
    t, n_attr = _ATTR_REPAIR_RE.subn(r'="\1"', t)
    t, n_attr2 = _ATTR_REPAIR2_RE.subn(r'\1"', t)
    n_attr3 = 0

    def inner_quotes(m: re.Match[str]) -> str:
        nonlocal n_attr3
        v = m.group(1)
        if '"' not in v:
            return m.group(0)
        n_attr3 += 1
        return '="' + v.replace('"', "&quot;") + '"'

    t = _ATTR_VAL_RE.sub(inner_quotes, t)
    return Sanitized(t, n_ctrl, sum(others.values()), n_amp, n_lt, n_attr + n_attr2 + n_attr3,
                     tuple(sorted(unknown)), tuple(sorted(others)))


class ParseMode(Enum):
    OK = "ok"
    LENIENT = "lenient"
    FAILED = "failed"
    HTML = "html"


@dataclass
class TreeResult:
    mode: ParseMode
    root: ET.Element | None
    error: str | None = None
    error_ctx: str | None = None
    lenient_unknown_tags: tuple[str, ...] = ()


class _LenientBuilder(HTMLParser):
    """§2.2 lenient — html.parser 로 트리 재구성. 태그명·속성명 대문자화, 화이트리스트 밖 태그는
    요소를 만들지 않고 자식을 부모에 붙인다. 종료 태그는 스택을 거슬러 닫는다.

    html.parser 는 <title>·<textarea>·<script> 안을 CDATA/RCDATA 로 읽어 자식 태그를 평문으로 만든다
    — DART 의 TITLE 은 그런 요소가 아니므로 set_cdata_mode 를 무력화한다(목록 상수는 typeshed 에서
    Final 이라 재선언할 수 없다).
    """

    def set_cdata_mode(self, elem: str, escapable: bool = False) -> None:
        return

    def __init__(self, tags: frozenset[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.tags = tags
        self.root = ET.Element("ROOT")
        self.stack = [self.root]
        self.unknown: Counter[str] = Counter()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.upper()
        if name not in self.tags:
            self.unknown[name] += 1
            return
        el = ET.SubElement(self.stack[-1], name, {k.upper(): (v or "") for k, v in attrs})
        self.stack.append(el)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        name = tag.upper()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == name:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        el = self.stack[-1]
        if len(el):
            el[-1].tail = (el[-1].tail or "") + data
        else:
            el.text = (el.text or "") + data


def _error_ctx(text: str, err: ET.ParseError) -> str:
    m = re.search(r"line (\d+), column (\d+)", str(err))
    if m is None:
        return ""
    ln, col = int(m.group(1)), int(m.group(2))
    lines = text.split("\n")
    if ln - 1 >= len(lines):
        return ""
    s = lines[ln - 1]
    return s[max(0, col - 60):col + 40]


def _has_content(root: ET.Element) -> bool:
    has_toc = any(e.attrib.get("ATOC") == "Y" for e in root.iter("TITLE"))
    return has_toc or root.find(".//TABLE") is not None


def parse_tree(text: str, tags: frozenset[str]) -> TreeResult:
    """정제 텍스트 → 트리. expat 성공 = ok, ParseError 면 관대 파서 = lenient, 아니면 failed."""
    try:
        root = ET.fromstring(text)
        return TreeResult(ParseMode.OK, root)
    except ET.ParseError as e:
        strict_err, ctx = str(e), _error_ctx(text, e)
    b = _LenientBuilder(tags)
    try:
        b.feed(text)
        b.close()
    except Exception as e:  # noqa: BLE001  # reason: html.parser 예외는 도메인 실패 → failed 상태
        return TreeResult(ParseMode.FAILED, None, f"strict: {strict_err}; lenient: {e!r}", ctx)
    doc = next((c for c in b.root if c.tag == "DOCUMENT"), None)
    if doc is None:
        return TreeResult(ParseMode.FAILED, None,
                          f"strict: {strict_err}; lenient: no DOCUMENT root", ctx)
    if not _has_content(doc):
        return TreeResult(ParseMode.FAILED, None,
                          f"strict: {strict_err}; lenient: empty tree (toc=0, tables=0)", ctx)
    return TreeResult(ParseMode.LENIENT, doc, strict_err, ctx, tuple(sorted(b.unknown)))


_HTML_RE = re.compile(r"<html[\s>]", re.I)
_MEMBER_RE = re.compile(r"^_?(\d{14})(?:_(\d{5}))?\.xml$")   # `_` 접두 1건(Y2040 실측) 허용
_ROLE_BY_SUFFIX = {None: "main", "00760": "audit", "00761": "audit_cons"}


def member_role(member_name: str, rcept_no: str) -> str:
    """ZIP 멤버 basename 으로 역할 판정 (§1.4 — 2015~2016 은 `/` 접두, 순서는 무작위)."""
    m = _MEMBER_RE.match(member_name.rsplit("/", 1)[-1])
    if m is None or m.group(1) != rcept_no:
        return "other"
    return _ROLE_BY_SUFFIX.get(m.group(2), "other")


def is_html(text: str) -> bool:
    return _HTML_RE.search(text[:4000]) is not None


def text_of(el: ET.Element | None) -> str:
    return "" if el is None else " ".join("".join(el.itertext()).split())


def generation(root: ET.Element) -> tuple[str, str | None]:
    """(gen, xsd) — dart4 / dart3_hdr / dart3_flat / unknown (§1.2).

    xsd 속성 키는 expat 이 `{ns}noNamespaceSchemaLocation`, 관대 트리가
    `XSI:NONAMESPACESCHEMALOCATION` 으로 주므로 키 끝만 대소문자 무시로 본다.
    """
    xsd = next((v for k, v in root.attrib.items()
                if k.upper().endswith("NONAMESPACESCHEMALOCATION")), None)
    if xsd == "dart4.xsd":
        return "dart4", xsd
    if xsd == "dart3.xsd":
        hdr = root.find("DOCUMENT-HEADER") is not None
        return ("dart3_hdr" if hdr else "dart3_flat"), xsd
    return "unknown", xsd


def header_fields(root: ET.Element) -> dict[str, str | None]:
    """헤더·SUMMARY·표지 TE/TU 를 평탄한 dict 로 (§1.6·§1.10). 없는 값은 None, JSON 은 문자열."""
    hdr = root.find("DOCUMENT-HEADER")
    src = hdr if hdr is not None else root
    name = src.find("DOCUMENT-NAME")
    fv = src.find("FORMULA-VERSION")
    comp = src.find("COMPANY-NAME")
    summ = src.find("SUMMARY")
    summary = ({e.attrib.get("ACODE", ""): text_of(e) for e in summ.iter("EXTRACTION")}
               if summ is not None else {})
    cover = root.find("BODY/COVER")
    cover_te = {e.attrib["ACODE"]: text_of(e)
                for e in (cover.iter("TE") if cover is not None else ()) if e.attrib.get("ACODE")}
    period: dict[str, str | None] = {"PERIODFROM": None, "PERIODTO": None}
    for tu in root.iter("TU"):
        unit = tu.attrib.get("AUNIT")
        if unit in period and period[unit] is None:
            period[unit] = tu.attrib.get("AUNITVALUE") or None
    return {
        "doc_acode": name.attrib.get("ACODE") if name is not None else None,
        "doc_name": text_of(name) or None,
        "formula_version": text_of(fv) or None,
        "formula_date": (fv.attrib.get("ADATE") if fv is not None else None) or None,
        "corp_cik": comp.attrib.get("AREGCIK") if comp is not None else None,
        "company_name_doc": text_of(comp) or None,
        "summary": json.dumps(summary, ensure_ascii=False),
        "cover": json.dumps(cover_te, ensure_ascii=False),
        "period_from": period["PERIODFROM"],
        "period_to": period["PERIODTO"],
    }


_SECTION_TAGS = ("SECTION-1", "SECTION-2", "SECTION-3")


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {c: p for p in root.iter() for c in p}


def _path(el: ET.Element, parents: dict[ET.Element, ET.Element]) -> str:
    out: list[str] = []
    cur: ET.Element | None = parents.get(el)
    while cur is not None and cur.tag != "BODY":
        out.append(cur.tag)
        cur = parents.get(cur)
    return "/".join(reversed(out))


def _is_xbrl_group(tg: ET.Element) -> bool:
    return tg.attrib.get("ACLASS", "").startswith("{XBRL}")


def _xbrl_tables(root: ET.Element) -> set[ET.Element]:
    return {t for tg in root.iter("TABLE-GROUP") if _is_xbrl_group(tg) for t in tg.iter("TABLE")}


def _section_kind(code: str | None, vocab: DocVocab, formula_date: str | None) -> str | None:
    if code is None:
        return None
    kind = vocab.section_kind(code)
    if code == "D-0-11-0-0" and kind == "fin_legacy" and (formula_date or "") >= "20150303":
        return "other"                      # 2015-03 서식 개정 뒤 XI 은 "그 밖의 사항" (§1.2)
    return kind


def _counts(scope: ET.Element, xbrl: set[ET.Element]) -> dict[str, str | None]:
    n_form = n_x = n_free = n_p = n_el = 0
    for e in scope.iter():
        n_el += 1
        if e.tag == "TABLE":
            if e.attrib.get("ACLASS") == "EXTRACTION":
                n_form += 1
            elif e in xbrl:
                n_x += 1
            else:
                n_free += 1
        elif e.tag == "P" and text_of(e):
            n_p += 1
    groups = sum(1 for tg in scope.iter("TABLE-GROUP") if _is_xbrl_group(tg))
    return {"n_form_tables": str(n_form), "n_xbrl_groups": str(groups),
            "n_free_tables": str(n_free), "n_paragraphs": str(n_p),
            "n_chars": str(len(text_of(scope))), "_n_el": str(n_el)}


def toc_rows(root: ET.Element, vocab: DocVocab,
             formula_date: str | None) -> list[dict[str, str | None]]:
    """`TITLE[@ATOC="Y"]`(표지 COVER-TITLE 포함) 마다 1행 — stg_doc_section (§3.2)."""
    parents = _parent_map(root)
    xbrl = _xbrl_tables(root)
    index = {el: i for i, el in enumerate(root.iter())}
    rows: list[dict[str, str | None]] = []
    ordinal = 0
    for el, i in index.items():
        if el.tag not in ("TITLE", "COVER-TITLE") or el.attrib.get("ATOC") != "Y":
            continue
        scope = parents.get(el)
        if scope is None:
            continue
        code = el.attrib.get("AASSOCNOTE") or ("COVER" if el.tag == "COVER-TITLE" else None)
        level = next((str(k + 1) for k, t in enumerate(_SECTION_TAGS) if scope.tag == t), None)
        kind = _section_kind(code, vocab, formula_date)
        if kind is None and level == "1":
            kind = next((_section_kind(c.attrib.get("AASSOCNOTE"), vocab, formula_date)
                         for c in scope.iter("TITLE")
                         if c is not el and c.attrib.get("AASSOCNOTE")), None)
        counts = _counts(scope, xbrl)
        n_el = int(counts.pop("_n_el") or "1")
        row: dict[str, str | None] = {
            "ordinal": str(ordinal), "section_code": code, "section_kind": kind,
            "atocid": el.attrib.get("ATOCID"), "level": level, "title": text_of(el),
            "path": _path(el, parents), "elem_start": str(i),
            "elem_end": str(index[scope] + n_el - 1),
        }
        row.update(counts)
        rows.append(row)
        ordinal += 1
    return rows


_FILED_ANCHOR_RE = re.compile(r"최초\s*제출일\s*[:：]?[ ]*([^\n]{0,40})")
_DATE_RE = re.compile(r"(\d{4})\s*[년.\-/월]\s*(\d{1,2})\s*[년월.\-/]\s*(\d{1,2})(?:\s*일)?")
# 콜론 앞은 **수평 공백만** 허용한다 — `\s*` 로 두면 앵커 셀과 값 셀이 갈린 2025 서식에서
# 줄바꿈을 삼켜 다음 줄 전체(예: `3. 정정사항`)를 대상 서류로 집는다.
# 갈린 값은 `_next_value_line` 이 잇는다.
_TARGET_RE = re.compile(r"1\.\s*정정대상\s*공시서류[^\S\n]*[:：]?[ ]*([^\n]*?)"
                        r"(?=[ ]*2\.\s*정정대상|\n|$)")
_REASON_RE = re.compile(r"3\.\s*정정사유\s*[:：]?\s*(.*?)(?=\s*4\.\s*정정사항|$)", re.S)
_BLOCK_TAGS = frozenset({"P", "TD", "TH", "TITLE", "TR", "TABLE"})
_HSPACE_RE = re.compile(r"[^\S\n]+")


def _norm_cell(s: str) -> str:
    return " ".join(s.split())


def _block_text(el: ET.Element) -> str:
    """블록 요소(P·TD·TH·TITLE·TR·TABLE) 경계마다 줄바꿈을 넣은 평문 — 앵커는 줄 안에서만."""
    parts: list[str] = []

    def walk(e: ET.Element) -> None:
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
            if c.tag in _BLOCK_TAGS:
                parts.append("\n")
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return _HSPACE_RE.sub(" ", "".join(parts))


_NEXT_ANCHOR_RE = re.compile(r"^[ ]*[2-4][ ]*[.．]")     # 다음 번호 항목 (`2.`·`3.`·`4.`)
_ITEM_HEADER_RE = re.compile(r"^[ ]*항[ ]*목")           # 정정사항 표 머리 (`항  목`)


def _next_value_line(flat: str, end: int) -> str | None:
    """앵커 줄 **다음**의 첫 비어 있지 않은 줄 — 2025 서식은 값을 다음 셀에 넣는다(§1.7).

    다음 번호 항목(`2.`·`3.`·`4.`)이나 정정사항 표 머리(`항  목`)로 시작하는 줄은 값이 아니라
    **다음 항목**이므로 집지 않는다 — 집으면 값이 빈 서식에서 항목 제목이 원문 필드로 샌다.
    연도 네 자리(`2024.03.20`)는 `[2-4][ ]*[.．]` 에 걸리지 않는다(`2` 다음이 `0`).
    """
    nl = flat.find("\n", end)
    if nl < 0:
        return None
    for line in flat[nl + 1:].split("\n"):
        s = line.strip()
        if not s:
            continue
        if _NEXT_ANCHOR_RE.match(s) or _ITEM_HEADER_RE.match(s):
            return None
        return s
    return None


def _calendar_ymd(d: re.Match[str]) -> str | None:
    """정규식 매치 → YYYYMMDD. 13월·45일·`20011년`(→ 0011년) 같은 오기(§1.7)는 None —
    G2 cast_failed 로 새지 않게. 연도는 1990~2099 만(Y2040 실측: 1건이 `110516` 으로 새었다).
    """
    y, m, day = int(d.group(1)), int(d.group(2)), int(d.group(3))
    if not 1990 <= y <= 2099:
        return None
    try:
        dt.date(y, m, day)
    except ValueError:
        return None
    return f"{y:04d}{m:02d}{day:02d}"


def correction_page(root: ET.Element) -> dict[str, str | None] | None:
    """`CORRECTION` 첫 장 → 원문 필드 (§1.7·§3.3). 요소 평문에 앵커 정규식 — P 줄·TD 셀 모두.

    2025년 서식(p1.5)은 값을 앵커와 **다른 셀**에 넣으므로 앵커 줄 나머지가 비었으면(제출일은
    날짜가 없으면) `_next_value_line` 으로 다음 줄을 잇고, 사라진 `3. 정정사유` 앵커는 정정사항
    표의 `정정사유` 열에서 유도한다. ≤2024 서식의 산출은 그대로다.
    """
    corr = root.find(".//CORRECTION")
    if corr is None:
        return None
    flat = _block_text(corr)
    tgt = _TARGET_RE.search(flat)
    target_raw = (tgt.group(1).strip() or None) if tgt else None
    if tgt is not None and target_raw is None:
        target_raw = _next_value_line(flat, tgt.end())      # 앵커 셀과 값 셀이 갈린 2025 서식
    rsn = _REASON_RE.search(flat)
    reason_raw = _norm_cell(rsn.group(1)) if rsn else ""
    filed_raw: str | None = None
    filed: str | None = None
    m = _FILED_ANCHOR_RE.search(flat)
    if m is not None:
        d = _DATE_RE.search(m.group(1))
        filed_raw = (m.group(1) if d is None else m.group(1)[:d.end()]).strip() or None
        if d is None:
            # 같은 줄에 날짜가 없다 → 다음 줄. 날짜가 있을 때만 바꾼다(`미상` 같은 원문은 지킨다).
            nxt = _next_value_line(flat, m.end())
            d = _DATE_RE.search(nxt) if nxt is not None else None
            if d is not None and nxt is not None:
                filed_raw = nxt[:d.end()].strip() or None
        if d is not None:
            filed = _calendar_ymd(d)
    items: list[dict[str, str]] = []
    for tbl in corr.iter("TABLE"):
        trs = list(tbl.iter("TR"))
        if not trs:
            continue
        hdr = [text_of(c).replace(" ", "") for c in trs[0]]     # 헤더 키는 공백 전부 제거
        if "항목" not in hdr:
            continue
        for tr in trs[1:]:
            cells = [_norm_cell(text_of(c)) for c in tr]
            items.append({hdr[i]: cells[i] for i in range(min(len(hdr), len(cells)))})
        break
    if not reason_raw:
        # 2025 서식에는 `3. 정정사유` 앵커가 없다 — 정정사항 표의 항목별 열에서 유도한다.
        seen = list(dict.fromkeys(v for it in items if (v := it.get("정정사유", ""))))
        reason_raw = " | ".join(seen)
    return {
        "page_found": "true",
        "target_raw": target_raw,
        "filed_raw": filed_raw,
        "filed_date": filed,
        "filed_date_status": "parsed" if filed else "unparsed",
        "reason_raw": reason_raw or None,
        "n_items": str(len(items)),
        "items": json.dumps(items, ensure_ascii=False),
        "corr_text_chars": str(len(flat)),
    }


def table_census(root: ET.Element) -> dict[str, str | None]:
    """문서 단위 표 계상 — stg_doc_meta 의 n_* 열 (§1.14 분류: 서식표 / XBRL / 자유표)."""
    xbrl = _xbrl_tables(root)
    xbrl_cls: list[str] = []
    form_cls: list[str] = []
    for tg in root.iter("TABLE-GROUP"):
        cls = tg.attrib.get("ACLASS", "")
        (xbrl_cls if cls.startswith("{XBRL}") else form_cls).append(cls)
    n_tables = n_form = n_free = 0
    for t in root.iter("TABLE"):
        n_tables += 1
        if t.attrib.get("ACLASS") == "EXTRACTION":
            n_form += 1
        elif t not in xbrl:
            n_free += 1
    toc_n = sum(1 for e in root.iter()
                if e.tag in ("TITLE", "COVER-TITLE") and e.attrib.get("ATOC") == "Y")
    return {
        "n_elements": str(sum(1 for _ in root.iter())),
        "n_tables": str(n_tables),
        "n_form_tables": str(n_form),
        "n_form_groups": str(len(form_cls)),
        "n_xbrl_groups": str(len(xbrl_cls)),
        "n_free_tables": str(n_free),
        "toc_n": str(toc_n),
        "xbrl_aclass": json.dumps(sorted(xbrl_cls), ensure_ascii=False),
        "form_aclass": json.dumps(sorted(set(form_cls)), ensure_ascii=False),
    }


# ── 잠정실적 공정공시 (N-64) ────────────────────────────────────────────────────
# '영업(잠정)실적(공정공시)' 원문은 보고서 XML 이 아니라 xforms HTML 서식이다(11-01 표본 20건). 표 둘 —
# 실적기간 표(첫 칸 '실적기간') · 실적내용 표(항목 행 '매출액 | 당해실적 | 당기 | 전기 | 증감 | 전환 | 전년동기 | …').
# 표 id(Table0/1 · RepeatTable0/1)는 서식마다 갈려 내용으로 찾는다. 정정 공시 앞의 정정표(XFormD8)는 두 꼴
# 어디에도 맞지 않아 저절로 빠진다. 단위를 못 읽거나 숫자를 하나라도 못 읽으면 값을 비우고 failed 로 둔다.
_PRELIM_TITLE_RE = re.compile(r"<title>[^<]*영업\(잠정\)실적", re.I)
_PRELIM_ITEMS = {"매출액": "revenue", "영업이익": "op_profit", "법인세비용차감전계속사업이익": "pretax",
                 "당기순이익": "net_income", "지배기업소유주지분순이익": "ni_owner"}
_PRELIM_PY = ("revenue", "op_profit", "net_income")       # 전년동기 당해값 — 확정 재무와 단위 검산용
_PRELIM_UNIT = {"원": 1, "천원": 10**3, "백만원": 10**6, "억원": 10**8, "조원": 10**12}
_PRELIM_UNIT_RE = re.compile(r"단위\s*:\s*([^,)\s]+)")
_PRELIM_NUM_RE = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_PRELIM_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


class _HtmlTables(HTMLParser):
    """HTML 표 → 표마다 행 목록(칸 텍스트, 공백 정규화). 서식에 표 안 표는 없어 평평하게 모은다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None and self.tables:
            self.tables[-1].append(self._row)
        self._row = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th"):
            self._close_cell()
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._close_cell()
        elif tag in ("tr", "table"):
            self._close_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _prelim_won(cell: str, mult: int) -> str | None:
    """'1,237,395' × 단위 → 원 단위 정수 문자열. '-'·빈칸은 NULL. 그 밖의 꼴(△·괄호·글자)은 ValueError."""
    s = cell.strip()
    if s in ("", "-"):
        return None
    if not _PRELIM_NUM_RE.fullmatch(s):
        raise ValueError(s)
    v = Decimal(s.replace(",", "")) * mult
    if v != v.to_integral_value():
        raise ValueError(s)
    return str(int(v))


def _prelim_period(row: list[str]) -> tuple[str, str] | None:
    """'당기실적 | 2026-07-01 | ~ | 2026-09-30' → ('20260701', '20260930')."""
    ds = [m.group(1) + m.group(2) + m.group(3) for c in row[1:] for m in [_PRELIM_DATE_RE.fullmatch(c)] if m]
    return (ds[0], ds[1]) if len(ds) == 2 else None


def prelim_row(rcept_no: str, member_name: str, text: str,
               fetched_at: str) -> dict[str, str | None] | None:
    """잠정실적 서식이면 stg_doc_prelim 1행, 아니면 None. 값은 원 단위 정수 문자열(당해실적 당기 · 전년동기)."""
    if not _PRELIM_TITLE_RE.search(text[:4000]):
        return None
    vals = {f"{k}_krw": None for k in _PRELIM_ITEMS.values()} | {f"{k}_py_krw": None for k in _PRELIM_PY}
    row: dict[str, str | None] = {
        "rcept_no": rcept_no, "member_name": member_name, "fetched_at": fetched_at, "fs_div": None,
        "period_from": None, "period_to": None, "py_from": None, "py_to": None, "unit_raw": None,
        **vals, "n_items": None, "unknown_items": None, "status": "failed", "error": None,
    }
    p = _HtmlTables()
    p.feed(text)
    p.close()
    periods = [t for t in p.tables if t and t[0] and t[0][0] == "실적기간"]
    results = [t for t in p.tables if any(len(r) >= 7 and r[1] == "당해실적" for r in t)]
    if len(periods) != 1:
        row["error"] = f"period_table:{len(periods)}"
        return row
    if len(results) != 1:
        row["error"] = f"result_table:{len(results)}"
        return row
    span = {r[0]: _prelim_period(r) for r in periods[0][1:] if r}
    cur, py = span.get("당기실적"), span.get("전년동기실적")
    if cur is None:
        row["error"] = "period:당기실적"
        return row
    row["period_from"], row["period_to"] = cur
    row["py_from"], row["py_to"] = py if py else (None, None)
    flat = "".join("".join(r) for r in results[0]).replace(" ", "")
    row["fs_div"] = "CFS" if "연결실적내용" in flat else "OFS" if "실적내용" in flat else None
    units = [m.group(1) for r in results[0] for c in r for m in [_PRELIM_UNIT_RE.search(c)] if m]
    row["unit_raw"] = units[0] if units else None
    if row["fs_div"] is None:
        row["error"] = "fs_div"
        return row
    if row["unit_raw"] not in _PRELIM_UNIT:
        row["error"] = f"unit:{row['unit_raw']}"
        return row
    mult = _PRELIM_UNIT[str(row["unit_raw"])]
    got: dict[str, str | None] = {}
    unknown: list[str] = []
    n_items = 0
    for r in results[0]:
        if len(r) < 7 or r[1] != "당해실적":
            continue
        key = _PRELIM_ITEMS.get(r[0].replace(" ", ""))
        if key is None:
            unknown.append(r[0])
            continue
        n_items += 1
        try:
            got[f"{key}_krw"] = _prelim_won(r[2], mult)
            if key in _PRELIM_PY:
                got[f"{key}_py_krw"] = _prelim_won(r[6], mult)
        except ValueError as e:
            row["error"] = f"number:{r[0]}={e}"
            return row
    row.update(got)
    row["n_items"] = str(n_items)
    row["unknown_items"] = json.dumps(unknown, ensure_ascii=False)
    row["status"] = "ok"
    return row


_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_PI_RE = re.compile(r"<\?.*?\?>", re.S)
_ANY_TAG_RE = re.compile(r"""<(?:[^>"']|"[^"]*"|'[^']*')*>""")   # 따옴표 안 `>` 는 태그 끝이 아니다
_EOL_RE = re.compile(r"\r\n?")


@dataclass
class DocRows:
    meta: list[dict[str, str | None]] = field(default_factory=list)
    section: list[dict[str, str | None]] = field(default_factory=list)
    correction: list[dict[str, str | None]] = field(default_factory=list)
    parse_log: list[dict[str, str | None]] = field(default_factory=list)
    prelim: list[dict[str, str | None]] = field(default_factory=list)   # 잠정실적 서식(html 갈래)
    zip_error: str | None = None        # ZIP 자체를 못 열었을 때 (D0 — D2 분모에서 제외)


def text_equal(sanitized: str, root: ET.Element, lenient: bool) -> bool:
    """§1.12 텍스트 등식 — 트리 텍스트 = 정제 원문에서 주석·선언·태그를 지우고 unescape 한 것.

    expat 은 XML 규격대로 원문의 CRLF·CR 을 LF 로 정규화하지만 `&#13;` 참조로 들어온 CR 은 남긴다
    (Y2040 실측: 각 23건·67건). 그래서 줄끝 정규화를 먼저, 참조 해석(unescape)을 나중에 한다.
    """
    stripped = _ANY_TAG_RE.sub("", _PI_RE.sub("", _COMMENT_RE.sub("", sanitized)))
    expected = html.unescape(_EOL_RE.sub("\n", stripped))
    got = "".join(root.itertext())
    if lenient:
        return " ".join(expected.split()) == " ".join(got.split())
    return expected.strip() == got.strip()


_META_KEYS = (
    "doc_acode", "doc_name", "formula_version", "formula_date", "corp_cik", "company_name_doc",
    "summary", "cover", "period_from", "period_to", "n_elements", "n_form_tables",
    "n_form_groups", "n_xbrl_groups", "n_free_tables", "toc_n", "xbrl_aclass", "form_aclass",
    "has_correction_page", "other_entities",
)


def _zero_counters() -> dict[str, str | None]:
    return {"n_ctrl": "0", "n_ent_other": "0", "n_bare_amp": "0", "n_bare_lt": "0",
            "n_attr_repair": "0"}


def _empty_meta() -> dict[str, str | None]:
    out: dict[str, str | None] = dict.fromkeys(_META_KEYS)
    out.update(_zero_counters())
    return out


def _ms(t0: float) -> str:
    return str(round((time.perf_counter() - t0) * 1000, 1))


def parse_member(rcept_no: str, member_name: str, raw: bytes, fetched_at: str,
                 vocab: DocVocab) -> DocRows:
    """멤버 1개 → 행. 실패는 parse_mode=failed 행으로 남고 예외는 올리지 않는다."""
    out = DocRows()
    role = member_role(member_name, rcept_no)
    t0 = time.perf_counter()
    dec = decode_bytes(raw)
    t_decode = _ms(t0)
    base: dict[str, str | None] = {
        "rcept_no": rcept_no, "member_name": member_name, "member_role": role,
        "fetched_at": fetched_at, "byte_enc": dec.enc, "n_repl": str(dec.n_repl),
        "bytes_xml": str(len(raw)),
    }
    if is_html(dec.text):
        n_tab = len(re.findall(r"<table[\s>]", dec.text, re.I))
        out.meta.append({**base, "format": "html", "gen": "html", "xsd": None,
                         "parse_mode": "html", "n_tables": str(n_tab), "text_equal": None,
                         **_empty_meta()})
        out.parse_log.append({**base, "parse_mode": "html", "error": None, "error_ctx": None,
                              "unknown_tags": "[]", "lenient_unknown_tags": "[]",
                              "t_decode_ms": t_decode, "t_sanitize_ms": "0", "t_parse_ms": "0",
                              **_zero_counters()})
        prelim = prelim_row(rcept_no, member_name, dec.text, fetched_at)
        if prelim is not None:
            out.prelim.append(prelim)
        return out
    t1 = time.perf_counter()
    san = sanitize(dec.text, vocab.tags)
    t_san = _ms(t1)
    counters = {"n_ctrl": str(san.n_ctrl), "n_ent_other": str(san.n_ent_other),
                "n_bare_amp": str(san.n_bare_amp), "n_bare_lt": str(san.n_bare_lt),
                "n_attr_repair": str(san.n_attr_repair)}
    t2 = time.perf_counter()
    tree = parse_tree(san.text, vocab.tags)
    t_parse = _ms(t2)
    out.parse_log.append({
        **base, "parse_mode": tree.mode.value, "error": tree.error, "error_ctx": tree.error_ctx,
        "unknown_tags": json.dumps(list(san.unknown_tags), ensure_ascii=False),
        "lenient_unknown_tags": json.dumps(list(tree.lenient_unknown_tags), ensure_ascii=False),
        "t_decode_ms": t_decode, "t_sanitize_ms": t_san, "t_parse_ms": t_parse, **counters,
    })
    if tree.root is None:
        return out
    root = tree.root
    gen, xsd = generation(root)
    hdr = header_fields(root)
    census = table_census(root)
    corr = correction_page(root)
    eq = text_equal(san.text, root, tree.mode is ParseMode.LENIENT)
    out.meta.append({**base, "format": "xml", "gen": gen, "xsd": xsd,
                     "parse_mode": tree.mode.value, **hdr, **census, **counters,
                     "has_correction_page": "true" if corr else "false",
                     "text_equal": "true" if eq else "false",
                     "other_entities": json.dumps(list(san.other_entities), ensure_ascii=False)})
    for r in toc_rows(root, vocab, hdr["formula_date"]):
        out.section.append({"rcept_no": rcept_no, "member_name": member_name,
                            "fetched_at": fetched_at, **r})
    if corr is not None:
        out.correction.append({"rcept_no": rcept_no, "member_name": member_name,
                               "fetched_at": fetched_at, **corr})
    return out


_ROLE_PRIORITY = {"main": 0, "audit_cons": 1, "audit": 2, "other": 3}


def parse_zip(rcept_no: str, zip_bytes: bytes, fetched_at: str, vocab: DocVocab) -> DocRows:
    """ZIP 1개 → 멤버별 행. ZIP 자체가 깨졌으면 zip_error + parse_log failed 1행(meta 없음).

    stg_doc_correction 은 접수번호당 1행이라 첫 장이 든 멤버가 둘이면 main 우선으로 접는다.
    """
    out = DocRows()
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        out.zip_error = f"BadZipFile: rcept_no={rcept_no} bytes={len(zip_bytes)} {e!r}"
        out.parse_log.append({
            "rcept_no": rcept_no, "member_name": "", "member_role": "other",
            "fetched_at": fetched_at, "byte_enc": None, "n_repl": None,
            "bytes_xml": str(len(zip_bytes)), "parse_mode": "failed", "error": out.zip_error,
            "error_ctx": None, "unknown_tags": "[]", "lenient_unknown_tags": "[]",
            "t_decode_ms": "0", "t_sanitize_ms": "0", "t_parse_ms": "0", **_zero_counters()})
        return out
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            r = parse_member(rcept_no, info.filename, zf.read(info), fetched_at, vocab)
            out.meta += r.meta
            out.section += r.section
            out.correction += r.correction
            out.parse_log += r.parse_log
            out.prelim += r.prelim
    if len(out.correction) > 1:
        role_of = {m["member_name"]: str(m["member_role"]) for m in out.meta}
        out.correction.sort(key=lambda c: _ROLE_PRIORITY.get(role_of.get(str(c["member_name"]),
                                                                          "other"), 3))
        out.correction = out.correction[:1]
    return out
