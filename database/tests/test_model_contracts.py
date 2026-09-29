"""모델 층 계약(src/model/contracts.py, M2 W0).

v3 원본·compat 과 어긋나지 않는지, v4 설계를 담을 수 있는지 본다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from compat.mappings import BY_TABLE
from model import contracts as c

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "src" / "compat" / "v3_schema.sql"


def _ddl_columns(table: str) -> list[str]:
    """compat DDL 의 컬럼 순서. 한 줄에 여러 컬럼을 적기도 해서(v2) 쉼표 단위로 나눈다."""
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\)[^;]*;", sql, re.S)
    assert m, table
    body = "\n".join(line.split("--")[0] for line in m.group(1).splitlines())
    body = re.sub(r"\b(PRIMARY|UNIQUE|FOREIGN)\s+KEY\s*\([^)]*\)", "", body)
    return [part.split()[0].strip('"') for part in body.split(",") if part.strip()]


def test_v3_v2_score_columns_match_compat_ddl() -> None:
    assert list(c.V3_SCORE_COLUMNS) == _ddl_columns("score_history")
    assert list(c.V2_SCORE_COLUMNS) == _ddl_columns("score_history_v2")
    assert len(c.V3_SCORE_COLUMNS) == 48 and len(c.V2_SCORE_COLUMNS) == 21


def test_flow_subjects_match_compat_investor_detail_flows() -> None:
    cols = BY_TABLE["investor_detail_flows"].columns
    assert c.FLOW_SUBJECTS == tuple(x for x in cols if x not in ("stock_code", "trade_date"))


def test_fin_summary_covers_what_v3_engine_reads() -> None:
    # v3 backend/scoring/factors/quality.py `_ANNUAL_SQL` · valuation.py 가 읽는 값 컬럼
    # (09-29 서버 원본 확인)
    v3_reads = {"revenue", "op", "ni", "eps", "bps", "per", "pbr", "roe", "roa", "debt_ratio",
                "fcf", "capex", "op_margin", "ni_margin", "dividend_yield", "shares",
                "ev_ebitda", "yoy", "gross_profit", "total_assets"}
    assert v3_reads <= set(c.FI_FIN_SUMMARY.column_names)
    compat_values = set(BY_TABLE["financial_summary"].columns) - {
        "stock_code", "period", "period_type", "data_type", "accounting_standard"}
    assert compat_values <= set(c.FI_FIN_SUMMARY.column_names)


@pytest.mark.parametrize("t", list(c.FI_TABLES.values()), ids=list(c.FI_TABLES))
def test_table_contract_is_well_formed(t: c.TableContract) -> None:
    names = t.column_names
    assert len(names) == len(set(names)), t.name
    assert set(t.grain) <= set(names), t.name
    assert all(col.dtype in c.DTYPES for col in t.columns), t.name
    assert all(col.unit in c.UNITS for col in t.columns), t.name
    assert t.readers and set(t.readers) <= set(c.ALL_ENGINES), t.name
    assert t.name.startswith("fi_") and t.window and t.source


def _v4_dict() -> dict:
    """D-13'(09-26 사용자 확정) 을 레지스트리 dict 로 — 계약이 v4 를 담을 수 있다는 증거."""
    return {
        "model_id": "v4_rank", "version": "0.1", "engine": "v4_rank",
        "buckets": {"low_risk": .25, "value": .25, "quality": .20, "pull": .10,
                    "revision": .10, "aux": .10},
        "indicators": [
            {"key": "VOL60", "bucket": "low_risk", "direction": -1},
            {"key": "EP_TTM", "bucket": "value"}, {"key": "DY0", "bucket": "value"},
            {"key": "OPM_TTM", "bucket": "quality"}, {"key": "FCF_A", "bucket": "quality"},
            {"key": "M_PULL_C", "bucket": "pull"},
            {"key": "REV_OP_1M", "bucket": "revision"}, {"key": "REV_OP_3M", "bucket": "revision"},
            {"key": "REV_NI_1M", "bucket": "revision"}, {"key": "REV_NI_3M", "bucket": "revision"},
            {"key": "CRDT_CHG", "bucket": "aux", "direction": -1},
            {"key": "FRGN60", "bucket": "aux"},
            {"key": "R12_1", "bucket": "momentum_display", "role": "display"},
            {"key": "EP_FWD", "bucket": "value", "role": "display"},
        ],
        "universe": {"sec_types": ["common"], "coverage_grace_days": 5},
        "output": {"top_n": 30, "sector_level": "L1", "max_per_sector": 9},
        "sector_neutral": "L1",
        "gates": [{"key": "M_PULL_C", "rule": "exclude_bottom_pct", "value": 0.30}],
    }


def test_v4_design_fits_the_contract() -> None:
    spec = c.ModelSpec.from_dict(_v4_dict())
    assert spec.validate() == []
    assert spec.spec_id == "v4_rank@0.1"
    assert spec.universe.sec_types == ("common",)
    cols = c.score_columns(spec)
    assert cols[:len(c.SCORE_BASE_COLUMNS)] == c.SCORE_BASE_COLUMNS
    assert cols[len(c.SCORE_BASE_COLUMNS):] == ("low_risk_score", "value_score", "quality_score",
                                                "pull_score", "revision_score", "aux_score")


def test_v3_and_v2_use_fixed_schemas() -> None:
    v3 = c.ModelSpec("v3_zscore", "1.0", "v3_zscore",
                     {"momentum": .2, "revision": .2, "flow": .2, "quality": .2, "valuation": .2})
    v2 = c.ModelSpec("v2_percentrank", "1.0", "v2_percentrank",
                     {"momentum": .25, "growth": .25, "flow": .25, "value": .25})
    assert v3.validate() == [] and v2.validate() == []
    assert c.score_columns(v3) == c.V3_SCORE_COLUMNS
    assert c.score_columns(v2) == c.V2_SCORE_COLUMNS


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d["buckets"].update(aux=.2), "가중 합"),
    (lambda d: d.update(engine="v9"), "engine"),
    (lambda d: d["indicators"].append({"key": "X", "bucket": "nope"}), "buckets 에 없다"),
    (lambda d: d["indicators"].append({"key": "Y", "bucket": "aux", "role": "both"}), "role"),
    (lambda d: d["gates"].append({"key": "NOPE", "rule": "exclude_bottom_pct", "value": .1}),
     "선언된 indicator"),
    (lambda d: d["gates"].append({"key": "VOL60", "rule": "exclude_bottom_pct", "value": 30}),
     "(0, 1)"),
    (lambda d: d["buckets"].update(extra=0.0), "score 지표가 없다"),
    (lambda d: d.update(sector_neutral="L3"), "sector_neutral"),
])
def test_validate_reports_each_error(mutate, needle: str) -> None:
    d = _v4_dict()
    mutate(d)
    errs = c.ModelSpec.from_dict(d).validate()
    assert any(needle in e for e in errs), errs


def test_from_dict_rejects_unknown_keys() -> None:
    d = _v4_dict()
    d["weigths"] = {}
    with pytest.raises(ValueError, match="모르는 키"):
        c.ModelSpec.from_dict(d)


def test_factor_inputs_check_against_contract() -> None:
    good = {name: [dict.fromkeys(t.column_names)] for name, t in c.FI_TABLES.items()}
    fi = c.FactorInputs("2026-09-28", "morning", "m_x", good)
    assert fi.check() == []
    no_credit = {k: v for k, v in good.items() if k != "fi_credit"}
    assert c.FactorInputs("2026-09-28", "morning", "m_x", no_credit).check("v3_zscore") == []
    assert c.FactorInputs("2026-09-28", "morning", "m_x", no_credit).check() == ["fi_credit 없음"]
    bad = dict(good, fi_prices=[{"ticker": "005930", "date": None, "close": 1}])
    errs = c.FactorInputs("2026-09-28", "morning", "m_x", bad).check()
    assert len(errs) == 1 and errs[0].startswith("fi_prices 컬럼 불일치")
    with pytest.raises(KeyError):
        fi.rows("daily_prices")
