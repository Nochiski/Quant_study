"""게이트 SKIP 허용표(K1-7a) — 표 안 SKIP 은 통과, 표 밖 SKIP 은 그 층 판정에서 실패.

로드맵 §8 K1-7 · DECISIONS §6-6 · N-42 Q4. 층별 실제 연결(equity·fi·model run_all, fi 의 stage 입력
가드)은 각 층 테스트 파일에서 보고, 여기서는 허용표 자체와 판정 규칙, 확인 스크립트를 본다.
음성 대조: 같은 게이트·같은 표에서 사유만 표 밖으로 바꾸면 FAIL(기록형은 경고)이 난다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from model import gates as mgates
from stage import skip_allow
from stage.gates import GateResult, GateStatus

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gate_skips.py"


def _skip(name: str, detail: str, **metrics: object) -> GateResult:
    return GateResult(name, GateStatus.SKIP, detail, dict(metrics))


# ── 허용표 모양 ───────────────────────────────────────────────────────────────
def test_every_entry_has_a_known_layer_a_reason_and_a_one_line_why() -> None:
    assert skip_allow.ALLOW
    for a in skip_allow.ALLOW:
        assert a.layer in skip_allow.LAYERS, a
        assert a.gate and a.reason and " " not in a.reason, a
        assert a.why.strip() and "\n" not in a.why, a


def test_entries_are_unique() -> None:
    keys = [(a.layer, a.gate, a.reason, a.tables) for a in skip_allow.ALLOW]
    assert len(keys) == len(set(keys))


def test_seed_entries_named_in_the_plan_are_allowed() -> None:
    """계획 §3 K1-7a 예시 — EG5a 첫 빌드, 원래 기준선이 없는 EG1(선언표)·EG2(차원 표)."""
    assert skip_allow.find("equity", "EG5a", "no_previous_build", "price_daily")
    assert skip_allow.find("equity", "EG1", "declaration_table", "universe_policy")
    assert skip_allow.find("equity", "EG2", "dimension_table", "security")


# ── 사유 코드 ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("detail, code", [
    ("inputs_changed", "inputs_changed"),
    ("no_fixtures — 골든 픽스처 없음", "no_fixtures"),
    ("no_previous — 같은 spec 의 성공 판이 없다", "no_previous"),
    ("upstream_failed — FG0", "upstream_failed"),
    ("ledger_unavailable:kiwoom", "ledger_unavailable"),
    ("write_mode=upsert", "write_mode=upsert"),
    ("", ""),
])
def test_reason_code_is_the_first_word_before_a_colon(detail: str, code: str) -> None:
    assert skip_allow.reason_code(detail) == code


# ── 판정 ──────────────────────────────────────────────────────────────────────
def test_allowed_skip_passes_through_unchanged() -> None:
    g = _skip("EG5a", "inputs_changed", previous_inputs={})
    assert skip_allow.judge("equity", g, table="price_daily") is g


def test_skip_outside_the_table_fails_and_keeps_the_measurements() -> None:
    """음성 대조 — EG5a 는 허용 사유가 셋이지만 no_baseline 은 아니다."""
    g = skip_allow.judge("equity", _skip("EG5a", "no_baseline", measured=0.97),
                         table="price_daily")
    assert g.status is GateStatus.FAIL and g.name == "EG5a"
    assert "skip_not_allowed" in g.detail and "no_baseline" in g.detail
    assert g.metrics["measured"] == 0.97
    assert g.metrics["skip_reason"] == "no_baseline" and g.metrics["skip_not_allowed"] is True


def test_same_reason_on_another_layer_is_not_allowed() -> None:
    """허용은 층마다 따로다 — equity 의 EG5a no_previous_build 가 model 에 번지지 않는다."""
    assert skip_allow.judge("model", _skip("EG5a", "no_previous_build")).status is GateStatus.FAIL


def test_table_scoped_entry_only_covers_its_tables() -> None:
    allowed = skip_allow.judge("equity", _skip("EG21", "no_coverage"), table="opinion_daily")
    assert allowed.status is GateStatus.SKIP
    other = skip_allow.judge("equity", _skip("EG21", "no_coverage"), table="price_daily")
    assert other.status is GateStatus.FAIL


def test_any_gate_entry_covers_upstream_failed() -> None:
    for layer, name in (("equity", "EG14"), ("factor_inputs", "FG3"), ("model", "MG2")):
        g = _skip(name, "upstream_failed")
        assert skip_allow.judge(layer, g) is g


def test_record_only_gate_warns_instead_of_failing() -> None:
    """기록형(MG5)의 표 밖 SKIP 은 판을 막지 않고 경고로 남는다 — model 기록 상태 'warn'."""
    g = skip_allow.judge("model", _skip("MG5", "not_measured — 셀 수 없음"),
                         record_only=mgates.RECORD_ONLY)
    assert g.status is GateStatus.PASS and g.metrics["warn"] is True
    assert g.metrics["skip_reason"] == "not_measured"
    assert mgates.status_of(g) == mgates.WARN


def test_pass_and_fail_are_never_touched() -> None:
    for st in (GateStatus.PASS, GateStatus.FAIL):
        g = GateResult("EG1", st, "x", {})
        assert skip_allow.judge("equity", g) is g


def test_unknown_layer_is_refused() -> None:
    with pytest.raises(ValueError, match="layer"):
        skip_allow.judge("ledger", _skip("L1", "no_request"))


# ── 판 기록(JSON) 판정 ────────────────────────────────────────────────────────
def test_violations_reads_recorded_gate_lists_and_dicts() -> None:
    listed = [{"name": "G4", "status": "skip", "detail": "no_fixtures"},
              {"name": "G9", "status": "skip", "detail": "ledger_unavailable:wise"},
              {"name": "G1", "status": "pass", "detail": ""}]
    assert skip_allow.violations("stage", listed) == ["G9:ledger_unavailable"]
    keyed = {"MG5": {"status": "skip", "detail": "no_previous — 첫 판"},
             "MG1": {"status": "skip", "detail": "odd — ?"}}
    assert skip_allow.violations("model", keyed) == ["MG1:odd"]
    assert skip_allow.violations("stage", None) == []


# ── 확인 스크립트(읽기 전용) ───────────────────────────────────────────────────
def _manifest(root: Path, table: str, builds: list[tuple[str, list[dict]]]) -> None:
    d = root / table
    d.mkdir(parents=True)
    d.joinpath("MANIFEST.json").write_text(json.dumps({
        "table": table, "current_build": builds[-1][0], "keep": 10,
        "builds": [{"build_id": b, "gates": g} for b, g in builds]}), encoding="utf-8")


def _g(name: str, status: str, detail: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, "metrics": {}}


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "ql"
    eq = home / "data" / "equity"
    _manifest(eq, "price_daily", [("m_1", [_g("EG5a", "skip", "no_previous_build")]),
                                  ("m_2", [_g("EG5a", "skip", "inputs_changed"),
                                           _g("EG21", "skip", "no_coverage")])])
    _manifest(eq, "security", [("m_2", [_g("EG2", "skip", "dimension_table")])])
    (eq / "_pinned").mkdir()
    st = home / "data" / "stage"
    _manifest(st, "stg_fin_wise", [("m_2", [_g("G4", "skip", "no_fixtures"),
                                            _g("G9", "skip", "no_cross_check")])])
    _manifest(st, "stg_price_daily", [("m_2", [_g("G4", "skip", "no_fixtures")])])
    fi_runs = home / "data" / "factor_inputs" / "_runs"
    fi_runs.mkdir(parents=True)
    fi_runs.joinpath("20261008_morning.json").write_text(json.dumps(
        {"build_id": "m_fi", "gates": [_g("FG4", "skip", "no_fixtures — 골든 픽스처 없음")]}))
    m_runs = home / "data" / "model" / "_runs"
    m_runs.mkdir(parents=True)
    m_runs.joinpath("20261008_morning.json").write_text(json.dumps(
        {"build_id": "m_mo", "specs": {"scope@1.0": {"gates": {
            "MG5": {"status": "skip", "detail": "no_previous — 첫 판"}}}},
         "excluded_specs": {}}))
    return home


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          check=False)


def test_script_lists_current_skips_and_flags_the_ones_outside(tmp_path: Path) -> None:
    home = _home(tmp_path)
    r = _run("--home", str(home))
    assert r.returncode == 1, r.stderr
    lines = [ln.split("\t") for ln in r.stdout.splitlines() if ln and not ln.startswith("#")]
    head, rows = lines[0], lines[1:]
    assert head == ["layer", "table", "gate", "reason", "n_builds", "allowed", "last_build"]
    got = {(x[0], x[1], x[2], x[3]): x[5] for x in rows}
    assert got == {
        ("equity", "price_daily", "EG5a", "inputs_changed"): "yes",
        ("equity", "price_daily", "EG21", "no_coverage"): "NO",
        ("equity", "security", "EG2", "dimension_table"): "yes",
        ("stage", "stg_fin_wise", "G4", "no_fixtures"): "yes",
        ("stage", "stg_fin_wise", "G9", "no_cross_check"): "yes",
        ("factor_inputs", "-", "FG4", "no_fixtures"): "NO",
        ("model", "scope@1.0", "MG5", "no_previous"): "yes",
    }
    # stage 는 fi 가 직접 읽는 표만 본다 — stg_price_daily 는 범위 밖
    assert not any(x[1] == "stg_price_daily" for x in rows)
    assert "허용표 밖 2" in r.stdout


def test_script_last_n_builds_counts_older_builds_too(tmp_path: Path) -> None:
    home = _home(tmp_path)
    r = _run("--home", str(home), "--last", "2")
    rows = [ln.split("\t") for ln in r.stdout.splitlines()
            if ln.startswith("equity\tprice_daily\tEG5a")]
    assert {x[3]: x[4] for x in rows} == {"no_previous_build": "1", "inputs_changed": "1"}


def test_script_is_clean_when_every_skip_is_allowed(tmp_path: Path) -> None:
    home = tmp_path / "ql"
    _manifest(home / "data" / "equity", "security",
              [("m_2", [_g("EG2", "skip", "dimension_table")])])
    r = _run("--home", str(home))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "허용표 밖 0" in r.stdout
