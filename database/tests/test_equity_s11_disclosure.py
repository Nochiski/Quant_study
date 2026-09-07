"""S11 `disclosure_version` — 절단본 위 실제 `build_table` 왕복 + 손 트리 부정 픽스처.

절단본 실측(법인 11개 · `stg_disclosure` 676행):
  모집단 = 정기보고서 접수 **625**(676 − 비정기 50 − 사업보고서제출기한연장신고서 1).
  사다리 5단 625 / 97 / 97 / 84 / 78 — L2 `rm` 정정 플래그 97 · L3 정정 접수 97(우연히 같다,
  서버는 20,579 vs 24,285) · L4 ZIP 정정신고 페이지 84 · L5 filed_date 파싱 78.
  링크 97건 전부 `unique`(법인 11개 안에서는 그룹당 원본이 하나뿐이라 후보가 갈리지 않는다) ·
  `date_check` exact 77 · mismatch 1 · no_zip 13 · unparsed 6 · n/a 528.
  `rm` 도달 82/82 = 1.0(정정본에 붙은 rm 15건은 구조적 미도달이라 분모 밖).

손 트리로만 낼 수 있는 것: 후보 2건(첨부추가 원본이 둘) → `multi_unresolved` / filed_date 로
갈리면 `multi_resolved` · 기간 라벨 없는 행 유지 · `rcept_dt` 결측 격리.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s11
from equity.baseline import Baseline, load
from equity.gates import GateStatus

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
DV = rules_s11.DISCLOSURE_VERSION

N_OUT = 625
N_REJECT = 0
LADDER = {"n_periodic": 625, "n_rm_corrected_later": 97, "n_is_correction": 97,
          "n_correction_zip": 84, "n_correction_filed_parsed": 78}
CANDIDATE_STATUS = {"n/a": 528, "unique": 97}
DATE_CHECK = {"exact": 77, "mismatch": 1, "n/a": 528, "no_zip": 13, "unparsed": 6}
KIND_COUNTS = {"annual": 184, "half": 150, "quarter": 291}


def _seed() -> Baseline:
    merged = {k: v for k, v in load(rules_s11.BASELINE_SEED).data.items()
              if not k.startswith("_") and k != "measured_at"}
    return Baseline(merged)


def _with(bl: Baseline, **kw: object) -> Baseline:
    return Baseline({**bl.data, DV.name: {**bl.table(DV.name), **kw}})


def _rows(out_dir: Path, where: str = "TRUE") -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        rel = con.execute("SELECT * EXCLUDE (v) FROM "
                          f"read_parquet('{out_dir / 'year=*' / '*.parquet'}', "
                          f"hive_partitioning=true) WHERE {where} ORDER BY rcept_no")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _rejects(out_dir: Path) -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        rel = con.execute("SELECT rcept_no, reject_reason FROM "
                          f"read_parquet('{out_dir / '_reject' / '*' / '*.parquet'}', "
                          "hive_partitioning=true) ORDER BY 1")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    root = tmp_path_factory.mktemp("s11") / "equity"
    return build.build_table(DV, STAGE_SLICE, root, _seed(), build_id="b_s11_dv")


@pytest.fixture(scope="module")
def rows(built: build.BuildResult) -> dict[str, dict[str, object]]:
    assert built.ok and built.out_dir is not None
    return {str(r["rcept_no"]): r for r in _rows(built.out_dir)}


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: build.BuildResult) -> None:
    assert built.ok, [(g.name, g.status.value, g.detail) for g in built.gates]
    assert [g.name for g in built.gates] == [
        "EG0", "EG7", "EG1", "EG2", "EG3", "EG3_disclosure_version",
        "EG6_disclosure_version", "EG8_disclosure_version", "EG4", "EG5a"]
    assert {g.name: g.status.value for g in built.gates if g.name != "EG5a"} == {
        "EG0": "pass", "EG7": "pass", "EG1": "pass", "EG2": "pass", "EG3": "pass",
        "EG3_disclosure_version": "pass", "EG6_disclosure_version": "pass",
        "EG8_disclosure_version": "pass", "EG4": "pass"}
    assert (built.n_rows, built.n_reject) == (N_OUT, N_REJECT)


def test_EG1_은_정기보고서_접수_전건과_같다(built: build.BuildResult) -> None:
    m = _gate(built, "EG1").metrics
    assert (m["lhs"], m["rhs"], m["delta"]) == (N_OUT, N_OUT, 0)


def test_사다리_5단은_stage_와_산출에서_같은_값이_나온다(built: build.BuildResult) -> None:
    m = _gate(built, "EG3_disclosure_version").metrics
    assert m["ladder_stage"] == LADDER
    assert m["ladder_out"] == LADDER
    assert m["ladder_mismatch"] == {}


def test_링크_상태와_날짜_확인축_분포(built: build.BuildResult) -> None:
    m = _gate(built, "EG3_disclosure_version").metrics
    assert m["n_by_candidate_status"] == CANDIDATE_STATUS
    assert m["n_by_date_check"] == DATE_CHECK
    assert m["n_by_kind"] == KIND_COUNTS
    assert m["n_linked_originals"] == 82
    assert m["n_corr_has_fin_item"] == 39


def test_E_G6a_와_E_G6b_비율(built: build.BuildResult) -> None:
    m = _gate(built, "EG6_disclosure_version").metrics
    assert (m["n_correction"], m["n_linked"]) == (97, 97)
    assert m["link_rate"] == 1.0
    assert (m["n_date_measured"], m["n_date_exact_or_1d"]) == (78, 77)
    assert round(float(str(m["date_exact_rate"])), 5) == 0.98718


def test_E_G7_도달률은_정정본을_분모에서_뺀다(built: build.BuildResult) -> None:
    m = _gate(built, "EG8_disclosure_version").metrics
    assert (m["n_rm_corrected_later"], m["n_rm_reached"]) == (82, 82)
    assert m["reach_rate"] == 1.0
    # 정정본에 붙은 rm 15건은 후보 술어가 원본 자격에서 뺀 행이라 도달 대상이 아니다
    assert m["n_rm_on_correction"] == 15
    assert m["n_linked_without_rm"] == 0


def test_정정_2회_그룹의_체인은_평평하게_접힌다(rows: dict[str, dict[str, object]]) -> None:
    origin = rows["20120330001734"]
    first = rows["20120330003470"]
    second = rows["20120426000475"]
    assert origin["group_key"] == first["group_key"] == second["group_key"]
    assert origin["group_key"] == "00254045|annual|(2011.12)"
    # 두 번째 정정도 첫 정정이 아니라 원본을 가리킨다
    assert first["orig_rcept_no"] == second["orig_rcept_no"] == "20120330001734"
    assert (first["prior_corr_count"], second["prior_corr_count"]) == (0, 1)
    assert (origin["n_corrections"], origin["first_correction_dt"]) == (2, date(2012, 3, 30))
    assert origin["candidate_status"] == "n/a" and origin["n_corrections"] == 2


def test_첨부추가는_원본_자격을_유지한다(rows: dict[str, dict[str, object]]) -> None:
    attach = rows["20230407002827"]
    assert attach["corr_prefix"] == "첨부추가"
    assert attach["is_correction"] is False
    assert attach["candidate_status"] == "n/a"
    assert attach["n_corrections"] == 2       # 이 첨부추가본을 원본으로 삼는 정정 2건
    assert rows["20230407003542"]["orig_rcept_no"] == "20230407002827"


def test_날짜가_어긋나도_링크는_유지된다(rows: dict[str, dict[str, object]]) -> None:
    r = rows["20240321001772"]
    assert r["date_check"] == "mismatch"
    assert r["candidate_status"] == "unique"
    assert r["orig_rcept_no"] == "20240320001629"
    assert r["link_basis"] == "parsed"
    # ZIP 정정신고 표의 최초제출일이 연도 오타(2023-03-20)라 원본 접수일 2024-03-20 과 다르다
    assert r["filed_date"] == date(2023, 3, 20)


def test_법정기한과_지연일수(rows: dict[str, dict[str, object]]) -> None:
    annual = rows["20190401004781"]
    assert (annual["legal_deadline"], annual["delay_days"]) == (date(2019, 3, 31), 1)
    half = rows["20180814001113"]
    assert (half["legal_deadline"], half["delay_days"]) == (date(2018, 8, 14), 0)


def test_PIT_축은_접수일이다(rows: dict[str, dict[str, object]]) -> None:
    for r in rows.values():
        assert r["available_date"] == r["rcept_dt"]
        assert r["available_basis"] == "derived"


def test_파티션은_접수연도_축이다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    years = sorted(p.name for p in built.out_dir.iterdir() if p.name.startswith("year="))
    assert years[0] == "year=2010" and years[-1] == "year=2026"
    con = duckdb.connect()
    try:
        n = con.execute(
            "SELECT count(*) FROM read_parquet("
            f"'{built.out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true) "
            "WHERE year <> CAST(substr(rcept_no, 1, 4) AS INTEGER)").fetchone()
        assert n is not None and int(str(n[0])) == 0
    finally:
        con.close()


# ── 선언 ↔ SQL 대조 ──────────────────────────────────────────────────────────

def test_모집단_어휘가_sql_리터럴과_같다() -> None:
    sql = rules_s11.SQL_PATH.read_text(encoding="utf-8")
    body = sql.split("WITH base AS", 1)[1]
    for p in rules_s11.PERIODIC_PREFIXES:
        assert f"LIKE '{p}%'" in body, p
    for t in rules_s11.EXCLUDED_TOKENS:
        assert f"NOT LIKE '%{t}%'" in body, t
    for c in rules_s11.CORRECTION_PREFIXES:
        assert f"'{c}'" in body, c
    for k in rules_s11.FIN_ITEM_KEYWORDS:
        assert f"LIKE '%{k}%'" in body, k


def test_EG1_우변은_sql_의_모집단_CTE_를_재사용한다() -> None:
    rhs = rules_s11.EG1_RHS_SQL
    assert rhs.endswith("SELECT count(DISTINCT rcept_no) FROM periodic")
    assert rhs.startswith(rules_s11.SQL_PATH.read_text(encoding="utf-8")[:40])
    # 마커는 정확히 한 번만 나온다 — 머리말 주석에 섞이면 모집단 정의가 잘린다
    assert rules_s11.SQL_PATH.read_text(encoding="utf-8").count("-- ==== eg1:") == 1


def test_격리_어휘와_상수_선언() -> None:
    assert DV.reject_reasons == ("rcept_dt_missing",)
    assert DV.consts == ("deadline_days_annual", "deadline_days_interim",
                         "date_check_near_days")
    assert DV.partition_class == "receipt_axis"
    assert DV.inputs == ("stg_disclosure", "stg_doc_correction", "stg_doc_index",
                         "stg_doc_meta")


def test_픽스처는_전부_positive_이고_키가_유일하다(built: build.BuildResult) -> None:
    fx = json.loads((Path(rules_s11.__file__).parent / "fixtures"
                     / "disclosure_version.json").read_text(encoding="utf-8"))
    assert len(fx) >= 15
    assert {f["fixture_class"] for f in fx} == {"positive"}
    assert all(set(f["key"]) == {"rcept_no"} for f in fx)
    assert _gate(built, "EG4").metrics["n_fixtures"] == len(fx)


# ── 손 트리 부정 픽스처 ───────────────────────────────────────────────────────

OBSERVED = date(2026, 9, 1)          # 손 트리의 재수집 관측일(판본 선택 축)


def _disclosure(rcept_no: str, rcept_dt: date | None, corp: str, report_nm: str,
                is_correction: bool, rm: bool = False,
                observed: date = OBSERVED) -> dict[str, object]:
    return {"rcept_no": rcept_no, "rcept_dt": rcept_dt, "corp_code": corp,
            "report_nm": report_nm, "is_correction": is_correction,
            "rm_corrected_later": rm, "observed_date": observed}


def _correction(rcept_no: str, filed: date | None, status: str = "parsed",
                page: bool = True, items: str = "") -> dict[str, object]:
    return {"rcept_no": rcept_no, "page_found": page, "filed_date": filed,
            "filed_date_status": status, "reason_raw": "단순 기재오류", "items": items}


def _hand_tree(make_stage_tree, tmp_path: Path, disclosures: list[dict[str, object]],
               corrections: list[dict[str, object]]) -> Path:
    """S11 이 요구하는 stage 4테이블을 손으로 깐다. 반환값은 stage_root."""
    tree = make_stage_tree(tmp_path, "stg_disclosure", disclosures,
                           partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_doc_correction", corrections,
                    partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_doc_index",
                    [{"rcept_no": d["rcept_no"], "zip_ok": True, "observed_date": OBSERVED}
                     for d in disclosures], partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_doc_meta",
                    [{"rcept_no": d["rcept_no"], "member_role": "main"} for d in disclosures],
                    partition_class="receipt_axis")
    return tree.stage_root


def _hand_fixture(tmp_path: Path, rcept_no: str, column: str, expect: object) -> Path:
    """손 트리에는 절단본 골든 픽스처를 쓸 수 없다 — EG4 는 픽스처 부재가 실패이므로 1건을 깐다."""
    path = tmp_path / "hand_fixture.json"
    path.write_text(json.dumps(
        [{"case": "hand", "key": {"rcept_no": rcept_no}, "column": column, "expect": expect,
          "source": "hand — 손 트리 부정 픽스처의 EG4 자리를 채운다"}], ensure_ascii=False),
        encoding="utf-8")
    return path


def _build_hand(make_stage_tree, tmp_path: Path, disclosures: list[dict[str, object]],
                corrections: list[dict[str, object]], **bl: object) -> build.BuildResult:
    stage_root = _hand_tree(make_stage_tree, tmp_path, disclosures, corrections)
    base = _with(_seed(), reach_rate_min=0.0, link_rate_min=0.0, **bl)
    return build.build_table(
        DV, stage_root, tmp_path / "equity", base, build_id="b_hand",
        fixtures_path=_hand_fixture(tmp_path, "20200101000001", "available_basis", "derived"),
        gate_thresholds={"EG7": 1.0})


def test_부정_후보_2건이면_multi_unresolved(make_stage_tree, tmp_path: Path) -> None:
    """원본이 둘(정본 + `[첨부추가]`)이고 filed_date 가 어느 쪽과도 안 맞으면 링크하지 않는다."""
    disclosures = [
        _disclosure("20200101000001", date(2020, 3, 30), "00000001",
                    "사업보고서 (2019.12)", False, rm=True),
        _disclosure("20200101000002", date(2020, 3, 31), "00000001",
                    "[첨부추가]사업보고서 (2019.12)", False),
        _disclosure("20200101000003", date(2020, 5, 20), "00000001",
                    "[기재정정]사업보고서 (2019.12)", True),
    ]
    r = _build_hand(make_stage_tree, tmp_path, disclosures,
                    [_correction("20200101000003", date(2020, 4, 15))])
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.out_dir is not None
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}
    corr = rows["20200101000003"]
    assert corr["candidate_status"] == "multi_unresolved"
    assert corr["orig_rcept_no"] is None
    assert corr["link_basis"] == "n/a"
    # 링크가 없으니 대조할 원본 접수일도 없다 → date_check 는 mismatch
    assert corr["date_check"] == "mismatch"
    assert rows["20200101000001"]["n_corrections"] == 0
    m = next(g for g in r.gates if g.name == "EG8_disclosure_version").metrics
    assert (m["n_rm_corrected_later"], m["n_rm_reached"], m["reach_rate"]) == (1, 0, 0.0)


def test_후보_2건이라도_filed_date_가_하나를_집으면_multi_resolved(
        make_stage_tree, tmp_path: Path) -> None:
    disclosures = [
        _disclosure("20200101000001", date(2020, 3, 30), "00000001",
                    "사업보고서 (2019.12)", False, rm=True),
        _disclosure("20200101000002", date(2020, 3, 31), "00000001",
                    "[첨부추가]사업보고서 (2019.12)", False),
        _disclosure("20200101000003", date(2020, 5, 20), "00000001",
                    "[기재정정]사업보고서 (2019.12)", True),
    ]
    r = _build_hand(make_stage_tree, tmp_path, disclosures,
                    [_correction("20200101000003", date(2020, 3, 31))])
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.out_dir is not None
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}
    corr = rows["20200101000003"]
    assert corr["candidate_status"] == "multi_resolved"
    assert corr["orig_rcept_no"] == "20200101000002"
    assert corr["date_check"] == "exact"
    assert rows["20200101000002"]["n_corrections"] == 1


def test_부정_기간_라벨이_없어도_행은_남는다(make_stage_tree, tmp_path: Path) -> None:
    """`no_label` 격리는 폐기했다(DESIGN §4-4) — 격리하면 그 접수가 모집단에서 사라진다."""
    disclosures = [
        _disclosure("20200101000001", date(2020, 3, 30), "00000001", "사업보고서", False),
        _disclosure("20200101000002", date(2020, 5, 20), "00000001",
                    "[기재정정]사업보고서", True),
    ]
    r = _build_hand(make_stage_tree, tmp_path, disclosures,
                    [_correction("20200101000002", date(2020, 3, 30))])
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (2, 0)
    assert r.out_dir is not None
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}
    assert all(x["group_key"] is None and x["group_key_basis"] == "no_label"
               for x in rows.values())
    # 라벨이 없으면 그룹을 못 만들므로 링크도 서지 않는다 — 행은 남고 상태만 none 이다
    assert rows["20200101000002"]["candidate_status"] == "none"
    assert rows["20200101000001"]["legal_deadline"] is None


def test_부정_접수일_결측은_격리된다(make_stage_tree, tmp_path: Path) -> None:
    disclosures = [
        _disclosure("20200101000001", date(2020, 3, 30), "00000001",
                    "사업보고서 (2019.12)", False),
        _disclosure("20200101000002", None, "00000001", "사업보고서 (2018.12)", False),
    ]
    r = _build_hand(make_stage_tree, tmp_path, disclosures,
                    [_correction("20200101000001", date(2020, 3, 30))])
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert r.out_dir is not None
    assert _rejects(r.out_dir) == [{"rcept_no": "20200101000002",
                                    "reject_reason": "rcept_dt_missing"}]
    # 격리 뒤에도 EG1 은 선다 — 모집단 2 − 격리 1 = 산출 1
    m = next(g for g in r.gates if g.name == "EG1").metrics
    assert (m["lhs"], m["rhs"], m["n_reject"], m["delta"]) == (1, 2, 1, 0)


def test_부정_비정기보고서는_모집단_밖(make_stage_tree, tmp_path: Path) -> None:
    disclosures = [
        _disclosure("20200101000001", date(2020, 3, 30), "00000001",
                    "사업보고서 (2019.12)", False),
        _disclosure("20200101000002", date(2020, 3, 30), "00000001",
                    "사업보고서제출기한연장신고서 (2019.12)", False),
        _disclosure("20200101000003", date(2020, 3, 30), "00000001",
                    "매매거래정지및정지해제(중요내용공시)", False),
    ]
    r = _build_hand(make_stage_tree, tmp_path, disclosures,
                    [_correction("20200101000001", date(2020, 3, 30))])
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 0)


def test_부정_재수집_판본이_모집단에_둘이면_한_행만_낸다(make_stage_tree,
                                                          tmp_path: Path) -> None:
    """stage `stg_disclosure` 는 append_only·key_unique=False 라 같은 접수가 여러 번 실린다.

    grain 이 `rcept_no` 이므로 모집단 CTE 가 접수번호당 1행으로 접어야 한다 — 안 접으면 산출이
    같은 행을 두 번 내고 EG1(count DISTINCT rcept_no)이 그만큼 어긋난다(서버 1차 빌드 delta −26).
    `stg_doc_index` 도 같은 규약이라 손 트리가 그 중복까지 함께 만든다.
    """
    disclosures = [
        _disclosure("20200101000001", date(2020, 3, 30), "00000001",
                    "사업보고서 (2019.12)", False, rm=True),
        # 같은 접수의 재수집 판본 — payload 는 같고 observed_date 만 늦다
        _disclosure("20200101000001", date(2020, 3, 30), "00000001",
                    "사업보고서 (2019.12)", False, rm=True, observed=date(2026, 9, 3)),
        _disclosure("20200101000003", date(2020, 5, 20), "00000001",
                    "[기재정정]사업보고서 (2019.12)", True),
    ]
    r = _build_hand(make_stage_tree, tmp_path, disclosures,
                    [_correction("20200101000003", date(2020, 3, 30))])
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (2, 0)          # 원본 1 + 정정 1, 중복 판본은 접혔다
    assert r.out_dir is not None
    rows = _rows(r.out_dir)
    assert [x["rcept_no"] for x in rows] == ["20200101000001", "20200101000003"]
    m = _gate(r, "EG3_disclosure_version").metrics
    assert m["n_population_dup_rcept"] == 1          # 접힌 재수집 판본 1건(기록형)
    assert m["ladder_stage"] == m["ladder_out"]      # 사다리는 두 축 모두 접수번호 축이다
    assert m["ladder_stage"]["n_periodic"] == 2
    # EG1 은 접수번호 축 항등 — 중복을 못 접으면 lhs 2 vs rhs 3 으로 깨진다
    eg1 = _gate(r, "EG1").metrics
    assert (eg1["lhs"], eg1["rhs"], eg1["delta"]) == (2, 2, 0)
    # 링크도 dedup 뒤 모집단으로 계산된다 — 후보가 판본만큼 늘면 multi_unresolved 가 됐을 것이다
    assert {x["rcept_no"]: x["candidate_status"] for x in rows} == {
        "20200101000001": "n/a", "20200101000003": "unique"}
    assert rows[1]["orig_rcept_no"] == "20200101000001"
    assert rows[0]["n_corrections"] == 1


def test_부정_링크_성립률_임계를_못_넘기면_폐기된다(make_stage_tree, tmp_path: Path) -> None:
    disclosures = [
        _disclosure("20200101000001", date(2020, 3, 30), "00000001",
                    "사업보고서 (2019.12)", False),
        _disclosure("20200101000002", date(2020, 3, 31), "00000001",
                    "[첨부추가]사업보고서 (2019.12)", False),
        _disclosure("20200101000003", date(2020, 5, 20), "00000001",
                    "[기재정정]사업보고서 (2019.12)", True),
    ]
    stage_root = _hand_tree(make_stage_tree, tmp_path, disclosures,
                            [_correction("20200101000003", date(2020, 4, 15))])
    bl = _with(_seed(), reach_rate_min=0.0, link_rate_min=0.9)
    r = build.build_table(
        DV, stage_root, tmp_path / "equity", bl, build_id="b_hand_fail",
        fixtures_path=_hand_fixture(tmp_path, "20200101000001", "available_basis", "derived"),
        gate_thresholds={"EG7": 1.0})
    assert not r.ok
    g = next(x for x in r.gates if x.name == "EG6_disclosure_version")
    assert g.status is GateStatus.FAIL
    assert g.metrics["link_rate"] == 0.0


def test_baseline_미등재면_임계를_걸지_않고_기록만_한다(built: build.BuildResult,
                                                     tmp_path: Path) -> None:
    """WORKFLOW §3-4 — 사다리 baseline 등재 전에는 E-G6a·E-G7 이 skip(no_baseline)."""
    bare = Baseline({DV.name: {"deadline_days_annual": 90,
                               "deadline_days_interim": 45,
                               "date_check_near_days": 7}})
    r = build.build_table(DV, STAGE_SLICE, tmp_path / "equity", bare, build_id="b_s11_bare")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    for name, metric in (("EG6_disclosure_version", "link_rate"),
                         ("EG8_disclosure_version", "reach_rate")):
        g = next(x for x in r.gates if x.name == name)
        assert g.status is GateStatus.SKIP and g.detail == "no_baseline"
        assert g.metrics[metric] == 1.0            # 판정은 안 해도 값은 남는다
