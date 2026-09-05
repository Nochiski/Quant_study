"""doc_vocab — 사전 로더 (DOC_DESIGN v1.1 §2.4)."""
from stage import doc_vocab


def test_vocab_loads_41_tags_and_known_xbrl_classes() -> None:
    v = doc_vocab.load_vocab()
    assert len(v.tags) == 41 and "TABLE-GROUP" in v.tags and "SECTION-3" in v.tags
    assert v.xbrl_stmt("{XBRL}BS_S") == ("BS", "S")
    assert v.xbrl_stmt("{XBRL}IS_C1") == ("IS", "C")
    assert v.xbrl_stmt("{XBRL}NT_S_D810005") == ("NT", "S")
    assert v.xbrl_stmt("{XBRL}BS") == ("BS", None)          # 접미 없음 → scope 는 문맥(§1.9)
    assert v.xbrl_stmt("{XBRL}ZZ") == ("?", None)


def test_vocab_unit_scale_and_section_kind() -> None:
    v = doc_vocab.load_vocab()
    assert v.unit_scale("(단위 : 천원)") == ("천원", "1000")
    assert v.unit_scale("(단위:백만원, %)") == ("백만원", "1000000")
    assert v.unit_scale("단위 : 주") == ("주", None)
    assert v.unit_scale("아무 말") is None
    assert v.section_kind("D-0-3-3-0") == "notes" and v.section_kind("L-0-2-4-L1") == "biz"
    assert v.section_kind("D-0-7-0-0") == "shareholder" and v.section_kind("X-9") is None
