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
from enum import Enum
from html.parser import HTMLParser

from .doc_vocab import DocVocab

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
_ATTR_REPAIR_RE = re.compile(r'=""(?=[^\s"<>])([^"<>=]*)"')


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
    return Sanitized(t, n_ctrl, sum(others.values()), n_amp, n_lt, n_attr,
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
_MEMBER_RE = re.compile(r"^(\d{14})(?:_(\d{5}))?\.xml$")
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
_TARGET_RE = re.compile(r"1\.\s*정정대상\s*공시서류\s*[:：]?[ ]*([^\n]*?)"
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


def _calendar_ymd(d: re.Match[str]) -> str | None:
    """정규식 매치 → YYYYMMDD. 13월·45일 같은 오기(§1.7)는 None — G2 cast_failed 로 새지 않게."""
    y, m, day = int(d.group(1)), int(d.group(2)), int(d.group(3))
    try:
        return dt.date(y, m, day).strftime("%Y%m%d")
    except ValueError:
        return None


def correction_page(root: ET.Element) -> dict[str, str | None] | None:
    """`CORRECTION` 첫 장 → 원문 필드 (§1.7·§3.3). 요소 평문에 앵커 정규식 — P 줄·TD 셀 모두."""
    corr = root.find(".//CORRECTION")
    if corr is None:
        return None
    flat = _block_text(corr)
    tgt = _TARGET_RE.search(flat)
    rsn = _REASON_RE.search(flat)
    filed_raw: str | None = None
    filed: str | None = None
    m = _FILED_ANCHOR_RE.search(flat)
    if m is not None:
        d = _DATE_RE.search(m.group(1))
        filed_raw = (m.group(1) if d is None else m.group(1)[:d.end()]).strip() or None
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
    return {
        "page_found": "true",
        "target_raw": (tgt.group(1).strip() or None) if tgt else None,
        "filed_raw": filed_raw,
        "filed_date": filed,
        "filed_date_status": "parsed" if filed else "unparsed",
        "reason_raw": (_norm_cell(rsn.group(1)) or None) if rsn else None,
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


_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_PI_RE = re.compile(r"<\?.*?\?>", re.S)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class DocRows:
    meta: list[dict[str, str | None]] = field(default_factory=list)
    section: list[dict[str, str | None]] = field(default_factory=list)
    correction: list[dict[str, str | None]] = field(default_factory=list)
    parse_log: list[dict[str, str | None]] = field(default_factory=list)
    zip_error: str | None = None        # ZIP 자체를 못 열었을 때 (D0 — D2 분모에서 제외)


def text_equal(sanitized: str, root: ET.Element, lenient: bool) -> bool:
    """§1.12 텍스트 등식 — 트리 텍스트 = 정제 원문에서 주석·선언·태그를 지우고 unescape 한 것."""
    stripped = _ANY_TAG_RE.sub("", _PI_RE.sub("", _COMMENT_RE.sub("", sanitized)))
    expected = html.unescape(stripped)
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
    if len(out.correction) > 1:
        role_of = {m["member_name"]: str(m["member_role"]) for m in out.meta}
        out.correction.sort(key=lambda c: _ROLE_PRIORITY.get(role_of.get(str(c["member_name"]),
                                                                          "other"), 3))
        out.correction = out.correction[:1]
    return out
