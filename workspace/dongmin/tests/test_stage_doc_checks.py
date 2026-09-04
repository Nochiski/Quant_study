"""doc_checks — 빌드 후 기록형 검사 D6·D8·D12 (DOC_DESIGN v1.1 §4)."""
import json
from pathlib import Path

from stage import build, doc_checks, rules
from test_stage_doc_build import _prepared


def test_checks_report_correction_page_xbrl_share_and_vocab_coverage(tmp_path: Path) -> None:
    snap, stage_root = _prepared(tmp_path)
    for name in ("stg_doc_meta", "stg_doc_correction", "stg_doc_parse_log"):
        assert build.build_table(rules.RULES[name], snap, stage_root).ok
    rep = doc_checks.run(stage_root)
    assert rep.d6 == doc_checks.D6Report(n_main_parsed=1, n_with_page=1, n_corr_rows=1,
                                         n_filed_parsed=1)
    assert rep.d8_by_year == {"2020": doc_checks.D8YearReport(n_main_annual=1, n_xbrl_ge4=0)}
    assert rep.d12 == doc_checks.D12Report(n_members=2, unknown_tag_members=0,
                                           lenient_members=0)
    assert rep.status == "ok"
    assert json.loads(json.dumps(rep.as_dict()))["d6"]["n_corr_rows"] == 1
