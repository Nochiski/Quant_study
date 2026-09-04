"""parsers_doc — 디코딩·정제·파싱·추출 (DOC_DESIGN v1.1 §1.3~§1.7, §2.2·§2.3)."""
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest
from stage import doc_vocab
from stage import parsers_doc as pd_

V = doc_vocab.load_vocab()
XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'


def test_decode_prefers_strict_utf8_then_cp949_then_replace() -> None:
    u = pd_.decode_bytes("<P>매출</P>".encode())
    assert (u.text, u.enc, u.n_repl) == ("<P>매출</P>", "utf-8", 0)
    c = pd_.decode_bytes("<P>매출</P>".encode("cp949"))
    assert (c.text, c.enc, c.n_repl) == ("<P>매출</P>", "cp949", 0)
    bad = pd_.decode_bytes(b"<P>\xff\xfe</P>")
    assert bad.enc == "cp949~" and bad.n_repl >= 1 and bad.text.startswith("<P>")


def test_decode_strips_utf8_bom() -> None:
    d = pd_.decode_bytes("﻿<P>x</P>".encode())
    assert d.text == "<P>x</P>" and d.enc == "utf-8"


def test_sanitize_cr_entity_becomes_newline_and_other_entities_are_kept_as_text() -> None:
    s = pd_.sanitize("<P>a&cr;b &nbsp;c</P>", V.tags)
    assert s.text == "<P>a&#10;b [&amp;nbsp;]c</P>"       # 규칙 3 이 다시 건드리지 않는 표기
    assert s.n_ent_other == 1 and s.other_entities == ("nbsp",) and s.n_bare_amp == 0


def test_sanitize_escapes_bare_ampersand_but_keeps_standard_entities() -> None:
    s = pd_.sanitize("<P>R&D &amp; A&#10;B</P>", V.tags)
    assert s.text == "<P>R&amp;D &amp; A&#10;B</P>" and s.n_bare_amp == 1


def test_sanitize_escapes_angle_brackets_that_are_not_whitelisted_tags() -> None:
    s = pd_.sanitize("<TD><당기말></TD><P><MS AR 사업></P><P><CP></P>", V.tags)
    assert s.text == "<TD>&lt;당기말></TD><P>&lt;MS AR 사업></P><P>&lt;CP></P>"
    assert s.n_bare_lt == 3 and s.unknown_tags == ("CP", "MS")   # 한글 시작은 토큰이 아니다


def test_sanitize_repairs_doubled_attribute_quote_but_leaves_empty_attributes() -> None:
    s = pd_.sanitize('<TE ENG=""Maximum exposure"><TU AUNIT="" WIDTH="5"/>', V.tags)
    assert s.text == '<TE ENG="Maximum exposure"><TU AUNIT="" WIDTH="5"/>'
    assert s.n_attr_repair == 1


def test_sanitize_repairs_doubled_closing_quote_too() -> None:
    # Y2040 실측(한진 2026 사업보고서): `ATITLE="...shares"" VALIGN=` — 닫는 따옴표가 겹친 형태
    s = pd_.sanitize('<TD ATITLE="preferred shares"" VALIGN="MIDDLE" W=""><P>a="b"" c</P>', V.tags)
    assert s.text == '<TD ATITLE="preferred shares" VALIGN="MIDDLE" W=""><P>a="b" c</P>'
    assert s.n_attr_repair == 2          # 본문 텍스트의 같은 꼴도 바뀌지만 D10 이 잡는다


@pytest.mark.parametrize("broken,fixed", [
    ('<TE ENG="" Hyosung Vietnam Co., Ltd."" VALIGN="M">v</TE>',
     '<TE ENG=" Hyosung Vietnam Co., Ltd." VALIGN="M">v</TE>'),
    ('<TE ENG="" KDB General Loan" VALIGN="M"/>', '<TE ENG=" KDB General Loan" VALIGN="M"/>'),
    ('<TH ENG="" Kookmin Bank Member">국민은행</TH>',
     '<TH ENG=" Kookmin Bank Member">국민은행</TH>'),
    ('<TE ENG=""Maximum exposure">v</TE>', '<TE ENG="Maximum exposure">v</TE>'),
    ('<TD ENG="NYU 1ST CO.,LTD.("Investor)" WIDTH="291">x</TD>',
     '<TD ENG="NYU 1ST CO.,LTD.(&quot;Investor)" WIDTH="291">x</TD>'),
    ('<TH ENG="JV "UZAUTO-INZI" LLC">JV "UZAUTO-INZI" LLC</TH>',
     '<TH ENG="JV &quot;UZAUTO-INZI&quot; LLC">JV "UZAUTO-INZI" LLC</TH>'),
    ('<TE ENG="x ("a" b) c"  as shown)" VALIGN="M">v</TE>',
     '<TE ENG="x (&quot;a&quot; b) c&quot;  as shown)" VALIGN="M">v</TE>'),
    ('<TE A="p" B="q"/>', '<TE A="p" B="q"/>'),
    ('<TU AUNIT="" WIDTH="5"/>', '<TU AUNIT="" WIDTH="5"/>'),          # 빈 속성은 그대로
    ('<TD W=""><P>x</P></TD>', '<TD W=""><P>x</P></TD>'),
    ('<TD A="x" B="y">a "b" c</TD>', '<TD A="x" B="y">a "b" c</TD>'),   # 정상 속성·본문 따옴표
])
def test_sanitize_attribute_quote_repair_forms_seen_in_2025_2026(broken: str, fixed: str) -> None:
    s = pd_.sanitize(broken, V.tags)
    assert s.text == fixed
    doc = f'<DOCUMENT><BODY><TITLE ATOC="Y">t</TITLE>{s.text}</BODY></DOCUMENT>'
    assert pd_.parse_tree(doc, V.tags).mode is pd_.ParseMode.OK


def test_sanitize_strips_control_characters() -> None:
    s = pd_.sanitize("<P>a\x0bb\x1fc</P>", V.tags)
    assert s.text == "<P>abc</P>" and s.n_ctrl == 2


@pytest.mark.parametrize("tag", ["SECTION-1", "TABLE-GROUP", "COVER-TITLE", "P"])
def test_sanitize_keeps_every_whitelisted_tag_untouched(tag: str) -> None:
    s = pd_.sanitize(f"<{tag} A=\"1\">x</{tag}>", V.tags)
    assert s.text == f"<{tag} A=\"1\">x</{tag}>" and s.n_bare_lt == 0


DOC_MIN = (f'<?xml version="1.0" encoding="utf-8"?><DOCUMENT {XSI} '
           'xsi:noNamespaceSchemaLocation="dart3.xsd"><BODY><SECTION-1>'
           '<TITLE ATOC="Y">I. 회사의 개요</TITLE><TABLE><TR><TD>x</TD></TR></TABLE>'
           '</SECTION-1></BODY></DOCUMENT>')


def _find(root: ET.Element, path: str) -> ET.Element:
    el = root.find(path)
    assert el is not None, path
    return el


def test_parse_tree_strict_ok_keeps_attributes() -> None:
    r = pd_.parse_tree(DOC_MIN, V.tags)
    assert r.mode is pd_.ParseMode.OK and r.root is not None
    assert r.root.tag == "DOCUMENT" and _find(r.root, ".//TITLE").attrib["ATOC"] == "Y"


def test_parse_tree_lenient_recovers_mismatched_tags_and_uppercases_attributes() -> None:
    broken = DOC_MIN.replace("<TD>x</TD></TR>", "<TD><SPAN>x</TD></TR>")   # SPAN 미종료
    r = pd_.parse_tree(broken, V.tags)
    assert r.mode is pd_.ParseMode.LENIENT and r.root is not None
    assert _find(r.root, ".//TITLE").attrib["ATOC"] == "Y"          # 속성명 대문자 유지
    assert "".join(_find(r.root, ".//TD").itertext()) == "x"


def test_parse_tree_lenient_keeps_child_tags_inside_title() -> None:
    # html.parser 는 <title> 을 RCDATA 로 다뤄 자식 태그를 평문화한다 — DART TITLE 은 그렇지 않다
    broken = DOC_MIN.replace("<TITLE ATOC=\"Y\">I. 회사의 개요</TITLE>",
                             "<TITLE ATOC=\"Y\"><SPAN>I.</SPAN> 회사의 개요</TITLE><P><SPAN>u</P>")
    r = pd_.parse_tree(broken, V.tags)
    assert r.mode is pd_.ParseMode.LENIENT and r.root is not None
    title = _find(r.root, ".//TITLE")
    assert title.find("SPAN") is not None and pd_.text_of(title) == "I. 회사의 개요"


def test_parse_tree_failed_when_no_document_root_or_empty_tree() -> None:
    r = pd_.parse_tree("<BODY><P>x</P>", V.tags)
    assert r.mode is pd_.ParseMode.FAILED and r.root is None and "DOCUMENT" in str(r.error)
    r2 = pd_.parse_tree("<DOCUMENT><BODY></BODY></DOCUMENT", V.tags)   # 잘린 문서, 목차·표 0
    assert r2.mode is pd_.ParseMode.FAILED


G1_HEAD = (f'<?xml version="1.0" encoding="utf-8"?><DOCUMENT {XSI} '
           'xsi:noNamespaceSchemaLocation="dart3.xsd"><DOCUMENT-HEADER AEXT-CLASS="Y">'
           '<DOCUMENT-NAME ACODE="11011">사업보고서</DOCUMENT-NAME>'
           '<FORMULA-VERSION ADATE="20200113">3.9</FORMULA-VERSION>'
           '<COMPANY-NAME AREGCIK="00138057">써니전자(주)</COMPANY-NAME>'
           '<SUMMARY><EXTRACTION ACODE="LINK_FLAG" AFEATURE="BOTH">C</EXTRACTION>'
           '<EXTRACTION ACODE="FIN_TYPE" AFEATURE="BOTH">A</EXTRACTION></SUMMARY>'
           '</DOCUMENT-HEADER><BODY><COVER><COVER-TITLE ATOC="Y">사 업 보 고 서</COVER-TITLE>'
           '<TABLE-GROUP ACLASS="COVER"><TABLE ACLASS="EXTRACTION"><TR>'
           '<TU AUNIT="PERIODFROM" AUNITVALUE="20190101">2019년 01월 01일</TU>'
           '<TU AUNIT="PERIODTO" AUNITVALUE="20191231">2019년 12월 31일</TU>'
           '<TE ACODE="CRP_NM">써니전자 주식회사</TE></TR></TABLE></TABLE-GROUP></COVER>'
           '{BODY}</BODY></DOCUMENT>')
G3_HEAD = (f'<?xml version="1.0" encoding="utf-8"?><DOCUMENT {XSI} '
           'xsi:noNamespaceSchemaLocation="dart4.xsd"><DOCUMENT-NAME ACODE="11013">분기보고서'
           '</DOCUMENT-NAME><FORMULA-VERSION SUBVER="1" ADATE="20231229">5.5</FORMULA-VERSION>'
           '<COMPANY-NAME AREGCIK="00151605">씨비아이(주)</COMPANY-NAME><SUMMARY>'
           '<EXTRACTION ACODE="IFRS_YN" AFEATURE="BOTH">Y</EXTRACTION></SUMMARY>'
           '<BODY ATOCID="61"><COVER><COVER-TITLE ATOC="Y" ATOCID="1">분기보고서</COVER-TITLE>'
           '</COVER>{BODY}</BODY></DOCUMENT>')


def _root(xml: str) -> ET.Element:
    r = pd_.parse_tree(pd_.sanitize(xml, V.tags).text, V.tags)
    assert r.root is not None, r.error
    return r.root


def test_member_role_and_html_detection() -> None:
    assert pd_.member_role("20200327001141.xml", "20200327001141") == "main"
    assert pd_.member_role("/20160329000533_00760.xml", "20160329000533") == "audit"
    assert pd_.member_role("20200327001141_00761.xml", "20200327001141") == "audit_cons"
    assert pd_.member_role("_20240430000953.xml", "20240430000953") == "main"   # 실측 1건
    assert pd_.member_role("readme.txt", "20200327001141") == "other"
    assert pd_.is_html("<html>\n <head>") and not pd_.is_html(G1_HEAD)


def test_generation_and_header_fields_g1() -> None:
    root = _root(G1_HEAD.replace("{BODY}", ""))
    assert pd_.generation(root) == ("dart3_hdr", "dart3.xsd")
    h = pd_.header_fields(root)
    assert h["doc_acode"] == "11011" and h["doc_name"] == "사업보고서"
    assert h["formula_version"] == "3.9" and h["formula_date"] == "20200113"
    assert h["corp_cik"] == "00138057" and h["company_name_doc"] == "써니전자(주)"
    assert json.loads(h["summary"] or "{}") == {"LINK_FLAG": "C", "FIN_TYPE": "A"}
    assert h["period_from"] == "20190101" and h["period_to"] == "20191231"
    assert json.loads(h["cover"] or "{}") == {"CRP_NM": "써니전자 주식회사"}


def test_generation_and_header_fields_g3_flat() -> None:
    root = _root(G3_HEAD.replace("{BODY}", ""))
    assert pd_.generation(root) == ("dart4", "dart4.xsd")
    h = pd_.header_fields(root)
    assert h["doc_acode"] == "11013" and h["formula_version"] == "5.5"
    assert h["period_from"] is None and h["cover"] == "{}"


def test_generation_is_read_from_lenient_tree_too() -> None:
    broken = G1_HEAD.replace("{BODY}", "<SECTION-1><TITLE ATOC=\"Y\">I</TITLE><P><SPAN>u</P>"
                             "</SECTION-1>")
    r = pd_.parse_tree(broken, V.tags)
    assert r.mode is pd_.ParseMode.LENIENT and r.root is not None
    assert pd_.generation(r.root) == ("dart3_hdr", "dart3.xsd")


BODY_TOC = ('<SECTION-1><TITLE ATOC="Y">I. 회사의 개요</TITLE>'
            '<SECTION-2><TITLE ATOC="Y" AASSOCNOTE="D-0-1-1-0">1. 회사의 개요</TITLE>'
            '<P>당사는&cr;회사입니다.</P><TABLE><TR><TD>a</TD></TR></TABLE></SECTION-2>'
            '</SECTION-1>'
            '<SECTION-1><TITLE ATOC="Y" AASSOCNOTE="D-0-3-0-0">III. 재무에 관한 사항</TITLE>'
            '<INSERTION><LIBRARY><SECTION-2>'
            '<TITLE ATOC="Y" AASSOCNOTE="D-0-3-2-0">2. 연결재무제표</TITLE>'
            '<TABLE-GROUP ACLASS="{XBRL}BS"><TABLE><TR><TD>h</TD></TR></TABLE>'
            '<TABLE><TR><TD>b</TD></TR></TABLE></TABLE-GROUP></SECTION-2></LIBRARY></INSERTION>'
            '</SECTION-1>')


def test_toc_rows_carry_code_kind_level_path_and_counts() -> None:
    root = _root(G1_HEAD.replace("{BODY}", BODY_TOC))
    rows = pd_.toc_rows(root, V, formula_date="20200113")
    assert [r["title"] for r in rows] == ["사 업 보 고 서", "I. 회사의 개요", "1. 회사의 개요",
                                          "III. 재무에 관한 사항", "2. 연결재무제표"]
    assert [r["ordinal"] for r in rows] == ["0", "1", "2", "3", "4"]
    r1, r2, r4 = rows[1], rows[2], rows[4]
    assert r1["section_code"] is None and r1["level"] == "1"
    assert r1["section_kind"] == "overview"
    assert r2["section_code"] == "D-0-1-1-0" and r2["level"] == "2"
    assert r2["path"] == "SECTION-1/SECTION-2"
    assert r2["n_free_tables"] == "1" and r2["n_paragraphs"] == "1"
    assert int(r2["n_chars"] or "0") > 0
    assert int(r2["elem_start"] or "0") < int(r2["elem_end"] or "0")
    assert int(r1["elem_end"] or "0") >= int(r2["elem_end"] or "0")     # 상위 절이 하위를 감싼다
    assert r4["path"] == "SECTION-1/INSERTION/LIBRARY/SECTION-2" and r4["n_xbrl_groups"] == "1"
    assert r4["n_free_tables"] == "0" and r4["section_kind"] == "fin"


def test_toc_rows_legacy_d_0_11_is_fin_before_2015_03_and_other_after() -> None:
    body = ('<SECTION-1><TITLE ATOC="Y" AASSOCNOTE="D-0-11-0-0">XI. 재무제표 등</TITLE>'
            '</SECTION-1>')
    root = _root(G1_HEAD.replace("{BODY}", body))
    assert pd_.toc_rows(root, V, formula_date="20140217")[1]["section_kind"] == "fin_legacy"
    assert pd_.toc_rows(root, V, formula_date="20150303")[1]["section_kind"] == "other"


CORR_G1 = ('<INSERTION><LIBRARY><CORRECTION><TITLE ATOC="Y">정 정 신 고 (보고)</TITLE>'
           '<TABLE><TR><TD>2020년 03월 30일</TD></TR></TABLE>'
           '<P>1. 정정대상 공시서류 : 사업보고서</P>'
           '<P>2. 정정대상 공시서류의 최초제출일 : 2020년 03월 30일</P>'
           '<P>3. 정정사유 : 당기순이익 기재 오류</P><P>4. 정정사항</P>'
           '<TABLE><THEAD><TR><TH>항  목</TH><TH>정정사유</TH><TH>정 정 전</TH><TH>정 정 후</TH>'
           '</TR></THEAD><TBODY><TR><TD>포괄손익계산서</TD><TD>기재 오류</TD>'
           '<TD>266,596,241</TD><TD>266,596,242</TD></TR>'
           '</TBODY></TABLE></CORRECTION></LIBRARY></INSERTION>')
CORR_G3_TD = ('<LIBRARY><CORRECTION><TITLE ATOC="Y">정 정 신 고 (보고)</TITLE>'
              '<TABLE><TR><TD>2025 년 3 월 14 일</TD></TR>'
              '<TR><TD>1. 정정대상 공시서류 : 사업보고서</TD></TR>'
              '<TR><TD>2. 정정대상 공시서류의 최초제출일 : 2025 년 3 월 12 일</TD></TR>'
              '<TR><TD>3. 정정사항</TD></TR></TABLE>'
              '<TABLE><TR><TH>항 목</TH><TH>정정요구ㆍ명령관련 여부</TH><TH>정정사유</TH>'
              '<TH>정 정 전</TH><TH>정 정 후</TH></TR><TR><TD>V. 감사의견</TD><TD>-</TD>'
              '<TD>착오</TD><TD>해당없음</TD><TD>있음</TD></TR></TABLE></CORRECTION></LIBRARY>')


def test_correction_page_from_p_lines_g1() -> None:
    root = _root(G1_HEAD.replace("{BODY}", CORR_G1))
    c = pd_.correction_page(root)
    assert c is not None
    assert c["target_raw"] == "사업보고서" and c["filed_raw"] == "2020년 03월 30일"
    assert c["filed_date"] == "20200330" and c["filed_date_status"] == "parsed"
    assert c["reason_raw"] == "당기순이익 기재 오류" and c["n_items"] == "1"
    items = json.loads(c["items"] or "[]")
    assert items[0] == {"항목": "포괄손익계산서", "정정사유": "기재 오류", "정정전": "266,596,241",
                        "정정후": "266,596,242"}                    # 헤더 키는 공백 제거


def test_correction_page_from_td_cells_g3_and_anchored_date() -> None:
    root = _root(G3_HEAD.replace("{BODY}", CORR_G3_TD))
    c = pd_.correction_page(root)
    assert c is not None and c["filed_date"] == "20250312"     # 첫 날짜(제출일 표)가 아니라 앵커 뒤
    assert c["reason_raw"] is None
    assert json.loads(c["items"] or "[]")[0]["정정요구ㆍ명령관련여부"] == "-"


def test_correction_page_absent_unparsed_and_calendar_invalid_dates() -> None:
    assert pd_.correction_page(_root(G1_HEAD.replace("{BODY}", ""))) is None
    bad = CORR_G1.replace("2020년 03월 30일</P>", "미상</P>", 1)
    c = pd_.correction_page(_root(G1_HEAD.replace("{BODY}", bad)))
    assert c is not None and c["filed_date"] is None and c["filed_date_status"] == "unparsed"
    assert c["filed_raw"] == "미상"
    typo = CORR_G1.replace("2020년 03월 30일</P>", "2020년 13월 45일</P>", 1)   # §1.7 오기
    c2 = pd_.correction_page(_root(G1_HEAD.replace("{BODY}", typo)))
    assert c2 is not None and c2["filed_date"] is None and c2["filed_date_status"] == "unparsed"
    assert c2["filed_raw"] == "2020년 13월 45일"


BODY_TABLES = ('<SECTION-1><TITLE ATOC="Y">I</TITLE>'
               '<TABLE-GROUP ACLASS="TOT_STK"><TABLE ACLASS="EXTRACTION"><TR>'
               '<TE ACODE="ISU_STK1">1</TE></TR></TABLE></TABLE-GROUP>'
               '<TABLE><TR><TD>free</TD></TR></TABLE>'
               '<TABLE-GROUP ACLASS="{XBRL}BS_S"><TABLE><TR><TD>h</TD></TR></TABLE>'
               '<TABLE><TR><TD>b</TD></TR></TABLE></TABLE-GROUP>'
               '<TABLE-GROUP ACLASS="{XBRL}IS_S1"><TABLE><TR><TD>h</TD></TR></TABLE>'
               '</TABLE-GROUP></SECTION-1>')


def test_table_census_counts_by_kind_and_lists_class_names() -> None:
    root = _root(G1_HEAD.replace("{BODY}", BODY_TABLES))
    c = pd_.table_census(root)
    assert c["n_tables"] == "6" and c["n_form_groups"] == "2"        # TOT_STK + COVER(헤더 픽스처)
    assert c["n_form_tables"] == "2" and c["n_xbrl_groups"] == "2" and c["n_free_tables"] == "1"
    assert json.loads(c["xbrl_aclass"] or "[]") == ["{XBRL}BS_S", "{XBRL}IS_S1"]
    assert json.loads(c["form_aclass"] or "[]") == ["COVER", "TOT_STK"]
    assert str(c["n_elements"]).isdigit() and c["toc_n"] == "2"      # COVER-TITLE + I


def test_text_equal_survives_gt_inside_attributes_and_crlf_line_ends() -> None:
    # 전량 실측(D10 위반 23건): 속성값 안의 `>` 와 CRLF 줄끝은 트리가 아니라 검증기 쪽 문제였다
    xml = DOC_MIN.replace("<TD>x</TD>", '<TD ATITLE="5. 실적-<Life Science>" W="1">x</TD>')
    xml = xml.replace("<TITLE ATOC=\"Y\">I. 회사의 개요</TITLE>",
                      "<TITLE ATOC=\"Y\">I. 회사의\r\n개요&#13;\n(2010)</TITLE>")   # 참조 CR 유지
    san = pd_.sanitize(xml, V.tags)
    r = pd_.parse_tree(san.text, V.tags)
    assert r.mode is pd_.ParseMode.OK and r.root is not None
    assert pd_.text_equal(san.text, r.root, lenient=False)
    assert pd_.text_of(_find(r.root, ".//TD")) == "x"


def _zip(members: dict[str, bytes], dirs: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for d in dirs:
            zf.writestr(d, b"")
        for n, b in members.items():
            zf.writestr(n, b)
    return buf.getvalue()


FULL_G1 = G1_HEAD.replace("{BODY}", CORR_G1 + BODY_TOC + BODY_TABLES)
AUDIT_XML = (f'<?xml version="1.0" encoding="utf-8"?><DOCUMENT {XSI} '
             'xsi:noNamespaceSchemaLocation="dart3.xsd"><DOCUMENT-HEADER>'
             '<DOCUMENT-NAME ACODE="00760">감사보고서</DOCUMENT-NAME><SUMMARY>'
             '<EXTRACTION ACODE="AUDIT_CIK">00260055</EXTRACTION></SUMMARY></DOCUMENT-HEADER>'
             '<BODY><SECTION-1><TITLE ATOC="Y">독립된 감사인의 감사보고서</TITLE></SECTION-1>'
             '</BODY></DOCUMENT>')
HTML_DOC = ("<html>\n <head><title>동화기업/주식분할결정</title></head>"
            "<body><table></table></body></html>")


def test_parse_zip_emits_rows_for_four_tables_and_skips_directory_entries() -> None:
    z = _zip({"20200327001141_00760.xml": AUDIT_XML.encode("cp949"),
              "20200327001141.xml": FULL_G1.encode("cp949")}, dirs=("sub/",))
    out = pd_.parse_zip("20200327001141", z, "2026-09-01T03:20:50", V)
    assert out.zip_error is None
    assert [m["member_role"] for m in out.meta] == ["audit", "main"]
    main = out.meta[1]
    assert main["parse_mode"] == "ok" and main["byte_enc"] == "cp949"
    assert main["gen"] == "dart3_hdr" and main["has_correction_page"] == "true"
    assert main["n_xbrl_groups"] == "3" and main["n_form_tables"] == "2"
    assert main["fetched_at"] == "2026-09-01T03:20:50" and main["text_equal"] == "true"
    assert len(out.section) == 1 + 7                 # 감사 1 + 본문(표지·정정·I·1·III·2·I)
    assert out.correction and out.correction[0]["rcept_no"] == "20200327001141"
    assert out.correction[0]["member_name"] == "20200327001141.xml"
    assert [r["parse_mode"] for r in out.parse_log] == ["ok", "ok"]
    assert all(float(r["t_parse_ms"] or "x") >= 0 for r in out.parse_log)
    assert json.loads(out.meta[0]["summary"] or "{}") == {"AUDIT_CIK": "00260055"}


def test_parse_zip_folds_duplicate_correction_pages_to_the_main_member() -> None:
    audit_with_corr = AUDIT_XML.replace("</BODY>", CORR_G1 + "</BODY>")
    z = _zip({"20200327001141_00760.xml": audit_with_corr.encode(),
              "20200327001141.xml": FULL_G1.encode()})
    out = pd_.parse_zip("20200327001141", z, "2026-09-01T03:20:50", V)
    assert len(out.correction) == 1 and out.correction[0]["member_name"] == "20200327001141.xml"


def test_parse_zip_html_member_and_bad_zip_are_values_not_exceptions() -> None:
    out = pd_.parse_zip("20240311901285", _zip({"20240311901285.xml": HTML_DOC.encode()}),
                        "2026-09-01T00:00:00", V)
    m = out.meta[0]
    assert m["parse_mode"] == "html" and m["format"] == "html" and m["gen"] == "html"
    assert out.section == [] and out.correction == [] and out.zip_error is None
    bad = pd_.parse_zip("20240311901285", b"not a zip", "2026-09-01T00:00:00", V)
    assert bad.meta == [] and bad.parse_log[0]["parse_mode"] == "failed"
    assert bad.zip_error is not None and "BadZipFile" in bad.zip_error
    assert "20240311901285" in bad.zip_error and bad.parse_log[0]["error"] == bad.zip_error
