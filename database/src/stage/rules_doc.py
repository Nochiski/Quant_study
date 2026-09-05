"""문서층 stage 테이블 선언 — 원장 = 보고서 ZIP(doc_store 메타), 파서 = parsers_doc (DOC_DESIGN §3).

값은 프리패스 JSONL(전부 VARCHAR) 에서 오고 캐스팅·결측·게이트는 stage 규칙이 한다. 공통 골격:
available = stg_rcept_dt_map 참조(derived) · observed = doc_store.fetched_at · 판본 1개(append).
"""
from __future__ import annotations

from .model import (
    KIND_BOOL,
    KIND_DATE_YMD8,
    KIND_NUMERIC,
    KIND_TEXT,
    AvailableRule,
    ColumnRule,
    FileSource,
    SourceRef,
    TableRule,
)

_DOC = SourceRef("dart", "doc_store", "doc_zip")
_RCEPT_LOOKUP = AvailableRule("lookup", table="stg_rcept_dt_map", local_key="rcept_no",
                              lookup_key="rcept_no", lookup_value="rcept_dt")
_CNT = (12, 0)
_MS = (10, 1)


def _n(name: str, key: bool = False) -> ColumnRule:
    return ColumnRule(name, name, KIND_NUMERIC, *_CNT, key=key)


def _ms(name: str) -> ColumnRule:
    return ColumnRule(name, name, KIND_NUMERIC, *_MS)


def _t(name: str, key: bool = False, expected_len: int | None = None) -> ColumnRule:
    return ColumnRule(name, name, KIND_TEXT, key=key, expected_len=expected_len)


def _d(name: str) -> ColumnRule:
    return ColumnRule(name, name, KIND_DATE_YMD8)


def _b(name: str) -> ColumnRule:
    return ColumnRule(name, name, KIND_BOOL)


def _table(name: str, columns: tuple[ColumnRule, ...],
           natural_key: tuple[str, ...]) -> TableRule:
    src_cols = tuple(c.src for c in columns) + ("fetched_at",)
    return TableRule(
        name=name, sources=(_DOC,), columns=columns, natural_key=natural_key,
        partition_class="receipt_axis", partition_expr="substr(rcept_no, 1, 4)",
        partition_src="rcept_no", observed_src="fetched_at", write_mode="append_only", fanout=1,
        payload_exclude=("fetched_at",), lag_known=False, available=_RCEPT_LOOKUP,
        payload_columns=tuple(c.src for c in columns), key_unique=True,
        file_source=FileSource(name, src_cols),
    )


_COUNTERS = tuple(_n(c) for c in ("n_ctrl", "n_ent_other", "n_bare_amp", "n_bare_lt",
                                  "n_attr_repair"))

STG_DOC_META = _table("stg_doc_meta", (
    _t("rcept_no", key=True, expected_len=14), _t("member_name", key=True), _t("member_role"),
    _t("format"), _t("gen"), _t("xsd"), _t("formula_version"), _d("formula_date"),
    _t("doc_acode"), _t("doc_name"), _t("corp_cik"), _t("company_name_doc"), _t("byte_enc"),
    _n("n_repl"), *_COUNTERS, _t("parse_mode"), _n("n_elements"), _n("n_tables"),
    _n("n_form_tables"), _n("n_form_groups"), _n("n_xbrl_groups"), _n("n_free_tables"),
    _n("toc_n"), _d("period_from"), _d("period_to"), _b("has_correction_page"),
    _b("text_equal"), _t("xbrl_aclass"), _t("form_aclass"), _t("summary"), _t("cover"),
    _t("other_entities"), _n("bytes_xml"),
), ("rcept_no", "member_name"))

STG_DOC_SECTION = _table("stg_doc_section", (
    _t("rcept_no", key=True, expected_len=14), _t("member_name", key=True),
    _n("ordinal", key=True), _t("section_code"), _t("section_kind"), _t("atocid"), _n("level"),
    _t("title"), _t("path"), _n("elem_start"), _n("elem_end"), _n("n_form_tables"),
    _n("n_xbrl_groups"), _n("n_free_tables"), _n("n_paragraphs"), _n("n_chars"),
), ("rcept_no", "member_name", "ordinal"))

STG_DOC_CORRECTION = _table("stg_doc_correction", (
    _t("rcept_no", key=True, expected_len=14), _t("member_name"), _b("page_found"),
    _t("target_raw"), _t("filed_raw"), _d("filed_date"), _t("filed_date_status"),
    _t("reason_raw"), _n("n_items"), _t("items"), _n("corr_text_chars"),
), ("rcept_no",))

STG_DOC_PARSE_LOG = _table("stg_doc_parse_log", (
    _t("rcept_no", key=True, expected_len=14),
    ColumnRule("member_name", "member_name", KIND_TEXT, key=True, blank_is_value=True),
    _t("member_role"), _t("byte_enc"), _n("n_repl"), _n("bytes_xml"), _t("parse_mode"),
    _t("error"), _t("error_ctx"), _t("unknown_tags"), _t("lenient_unknown_tags"), *_COUNTERS,
    _ms("t_decode_ms"), _ms("t_sanitize_ms"), _ms("t_parse_ms"),
), ("rcept_no", "member_name"))

TABLES: tuple[TableRule, ...] = (STG_DOC_META, STG_DOC_SECTION, STG_DOC_CORRECTION,
                                 STG_DOC_PARSE_LOG)
