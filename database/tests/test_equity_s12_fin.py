"""S12 `fin_std` — 절단본 위 실제 `build_table` 왕복(`corp` → `disclosure_version` → `fin_std`)
+ 손 트리 부정 픽스처.

절단본 실측(`stg_fin` 41,740행 · 법인 9개):
  모집단 = (corp_code, bsns_year, reprt_code, fs_div) 중 표준계정 행이 있는 그룹 **226**
  → 산출 **220** · 격리 **6**(전부 `non_krw` = 중국원양자원 00722500, HKD 848행).
  `period_end_basis` document 220 · inferred 1(격리된 00722500 2016 반기, 문서 없음).
  `report_code` 11011 57 · 11012 56 · 11013 56 · 11014 51 — `doc_acode` 는 1·3분기를 둘 다
  11013 으로 적으므로 개월 수(3 vs 9)가 11014 51 건을 갈라낸다.
  접수 지연 p50 45일 · p99 216일 · **max 445일**(`api_restated` 축의 증거).

손계산(삼성전자 2018 사업보고서 20190401004781, 단위 원):
  매출 243,771,415,000,000 · 영업이익 58,886,669,000,000 · 매출총이익 111,377,004,000,000 ·
  순이익 44,344,857,000,000 · 자산총계 339,357,244,000,000.
  4분기 파생 영업이익 = 58,886,669 − (15,642,170 + 14,869,035 + 17,574,865) = 10,800,599(백만원).
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s11, rules_s12
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from fin_map import FIN_MAP

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
FIN_STD = rules_s12.FIN_STD

N_SRC = 226
N_OUT = 220
N_REJECT = 6
N_PREV_FILLED = 178                    # 직전 회계연도 같은 보고서를 찾은 행 (나머지 42 는 첫 해)
REPORT_CODES = {"11011": 57, "11012": 56, "11013": 56, "11014": 51}
SEC = "20190401004781"                 # 삼성전자 2018 사업보고서
SEC_2Q = "20180814001113"
SEC_1Q = "20180515001699"
SEC_3Q = "20181114001530"


def _seed() -> Baseline:
    merged: dict[str, object] = {}
    for p in (rules_s01.BASELINE_SEED, rules_s11.BASELINE_SEED, rules_s12.BASELINE_SEED):
        merged.update({k: v for k, v in load(p).data.items()
                       if not k.startswith("_") and k != "measured_at"})
    return Baseline(merged)


def _with(bl: Baseline, **kw: object) -> Baseline:
    return Baseline({**bl.data, FIN_STD.name: {**bl.table(FIN_STD.name), **kw}})


def _rows(out_dir: Path) -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        rel = con.execute("SELECT * EXCLUDE (v) FROM "
                          f"read_parquet('{out_dir / 'year=*' / '*.parquet'}', "
                          "hive_partitioning=true) ORDER BY rcept_no")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _rejects(out_dir: Path) -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        rel = con.execute("SELECT corp_code, bsns_year, report_code, period_end, "
                          "period_end_basis, reject_reason FROM "
                          f"read_parquet('{out_dir / '_reject' / '*' / '*.parquet'}', "
                          "hive_partitioning=true) ORDER BY 1, 2, 3")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


def _num(v: object) -> Decimal:
    return Decimal(str(v))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> build.BuildResult:
    root = tmp_path_factory.mktemp("s12") / "equity"
    bl = _seed()
    for rule in (rules_s01.CORP, rules_s11.DISCLOSURE_VERSION):
        r = build.build_table(rule, STAGE_SLICE, root, bl, build_id=f"b_{rule.name}")
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    return build.build_table(FIN_STD, STAGE_SLICE, root, bl, build_id="b_s12_fin")


@pytest.fixture(scope="module")
def rows(built: build.BuildResult) -> dict[str, dict[str, object]]:
    assert built.ok and built.out_dir is not None
    return {str(r["rcept_no"]): r for r in _rows(built.out_dir)}


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_빌드가_전_게이트를_통과한다(built: build.BuildResult) -> None:
    assert built.ok, [(g.name, g.status.value, g.detail) for g in built.gates]
    assert [g.name for g in built.gates] == ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_fin_std",
                                             "EG6_fin_std", "EG4", "EG5a"]
    assert {g.name: g.status.value for g in built.gates if g.name != "EG5a"} == {
        "EG0": "pass", "EG7": "pass", "EG1": "pass", "EG2": "pass", "EG3": "pass",
        "EG3_fin_std": "pass", "EG6_fin_std": "skip", "EG4": "pass"}
    assert (built.n_rows, built.n_reject) == (N_OUT, N_REJECT)


def test_EG1_은_표준계정_있는_보고서_그룹_수와_같다(built: build.BuildResult) -> None:
    m = _gate(built, "EG1").metrics
    assert (m["lhs"], m["rhs"], m["n_reject"], m["delta"]) == (N_OUT, N_SRC, N_REJECT, 0)


def test_비KRW_그룹은_격리된다(built: build.BuildResult) -> None:
    assert built.out_dir is not None
    rej = _rejects(built.out_dir)
    assert len(rej) == N_REJECT
    assert {r["reject_reason"] for r in rej} == {"non_krw"}
    assert {r["corp_code"] for r in rej} == {"00722500"}
    # 문서 없는 그룹의 후보 규칙(inferred)이 실제로 돌았다는 증거 — 반기 말일을 스스로 찾았다
    inferred = [r for r in rej if r["period_end_basis"] == "inferred"]
    assert len(inferred) == 1
    assert (inferred[0]["bsns_year"], inferred[0]["report_code"],
            inferred[0]["period_end"]) == ("2016", "11012", date(2016, 6, 30))


def test_보고서_종류_분포와_1Q_3Q_판정(built: build.BuildResult,
                                      rows: dict[str, dict[str, object]]) -> None:
    m = _gate(built, "EG3_fin_std").metrics
    assert m["n_by_report_code"] == REPORT_CODES
    assert m["n_report_code_month_mismatch"] == 0
    # 문서 doc_acode 는 1분기·3분기 둘 다 11013 — 개월 수가 가른다
    assert rows[SEC_1Q]["report_code"] == "11013"
    assert rows[SEC_1Q]["period_end"] == date(2018, 3, 31)
    assert rows[SEC_3Q]["report_code"] == "11014"
    assert rows[SEC_3Q]["period_end"] == date(2018, 9, 30)
    assert rows[SEC_3Q]["period_start"] == date(2018, 1, 1)


def test_삼성전자_2018_사업보고서_손검산(rows: dict[str, dict[str, object]]) -> None:
    r = rows[SEC]
    assert _num(r["revenue"]) == Decimal("243771415000000")
    assert _num(r["op_profit"]) == Decimal("58886669000000")
    assert _num(r["gross_profit"]) == Decimal("111377004000000")
    assert _num(r["net_income"]) == Decimal("44344857000000")
    assert _num(r["total_asset"]) == Decimal("339357244000000")
    assert _num(r["total_equity"]) == Decimal("247753177000000")
    assert _num(r["eps_basic"]) == Decimal("6461")
    # 매출총이익 = 매출 − 매출원가 (원 단위 항등)
    assert _num(r["gross_profit"]) == _num(r["revenue"]) - _num(r["cost_of_sales"])
    assert r["revenue_basis"] == "standard"
    assert r["period_end_basis"] == "document"
    assert r["vintage_kind"] == "api_restated"
    assert r["restated_unknown"] is True
    assert r["currency"] == "KRW"


def test_손익은_분기_3개월_사업보고서_12개월(rows: dict[str, dict[str, object]]) -> None:
    """반기보고서의 `thstrm_amount` 는 2분기 3개월 값이다(DEFECT-C02)."""
    q1 = _num(rows[SEC_1Q]["op_profit"])
    q2 = _num(rows[SEC_2Q]["op_profit"])
    q3 = _num(rows[SEC_3Q]["op_profit"])
    assert (q1, q2, q3) == (Decimal("15642170000000"), Decimal("14869035000000"),
                            Decimal("17574865000000"))
    # 원장의 반기 누계(thstrm_add_amount) 30,511,205 = 1Q + 2Q — 3개월 해석의 증거
    assert q1 + q2 == Decimal("30511205000000")


def test_4분기_파생은_연간에서_3분기를_뺀다(rows: dict[str, dict[str, object]]) -> None:
    r = rows[SEC]
    q_sum = (_num(rows[SEC_1Q]["op_profit"]) + _num(rows[SEC_2Q]["op_profit"])
             + _num(rows[SEC_3Q]["op_profit"]))
    assert _num(r["op_profit_q4_derived"]) == _num(r["op_profit"]) - q_sum
    assert _num(r["op_profit_q4_derived"]) == Decimal("10800599000000")
    assert _num(r["revenue_q4_derived"]) == Decimal("59265050000000")
    assert r["q4_derived_n_rows"] == 4
    assert r["q4_derived_available_date"] == date(2019, 4, 1)
    # 분기 행에는 q4 파생이 붙지 않는다
    assert rows[SEC_2Q]["op_profit_q4_derived"] is None
    assert rows[SEC_2Q]["q4_derived_n_rows"] is None


def test_구성_분기가_모자라면_부분합을_만들지_않는다(rows: dict[str, dict[str, object]]) -> None:
    """절단본에 삼성전자 2015 분기 보고서가 없다 — 부분합 금지로 q4 는 전부 NULL."""
    r = rows["20160330003536"]
    assert r["bsns_year"] == "2015" and r["report_code"] == "11011"
    assert r["q4_derived_n_rows"] == 1
    assert all(r[c] is None for c in rules_s12.Q4_COLUMNS)


def test_현금흐름은_누계이고_분기_파생이_따로_붙는다(rows: dict[str, dict[str, object]]) -> None:
    ytd = {k: _num(rows[k]["cf_operating_ytd"]) for k in (SEC_1Q, SEC_2Q, SEC_3Q, SEC)}
    assert ytd[SEC_1Q] < ytd[SEC_2Q] < ytd[SEC_3Q] < ytd[SEC]
    # 1분기는 누계 자체가 분기값
    assert _num(rows[SEC_1Q]["cf_operating_q"]) == ytd[SEC_1Q]
    assert rows[SEC_1Q]["cf_q_n_rows"] == 1
    assert _num(rows[SEC_2Q]["cf_operating_q"]) == ytd[SEC_2Q] - ytd[SEC_1Q]
    assert _num(rows[SEC_3Q]["cf_operating_q"]) == ytd[SEC_3Q] - ytd[SEC_2Q]
    assert _num(rows[SEC]["cf_operating_q"]) == ytd[SEC] - ytd[SEC_3Q]
    assert rows[SEC_3Q]["cf_q_n_rows"] == 2
    assert rows[SEC_3Q]["cf_q_available_date"] == rows[SEC_3Q]["rcept_dt"]


def test_PIT_축은_접수일이다(rows: dict[str, dict[str, object]]) -> None:
    for r in rows.values():
        assert r["available_date"] == r["rcept_dt"]
        assert r["available_basis"] == "derived"
        assert r["period_end"] <= r["rcept_dt"]


def test_계정_커버율이_기록된다(built: build.BuildResult) -> None:
    m = _gate(built, "EG3_fin_std").metrics
    cov = m["coverage_by_account"]
    assert isinstance(cov, dict)
    assert set(cov) == set(rules_s12.ACCOUNTS)
    assert cov["revenue"] == cov["total_asset"] == cov["cf_operating_ytd"] == 1.0
    # DESIGN §4-4 추가 3계정은 완전일치만 쓰므로 커버율이 낮다 — 사실을 기록하고 넘어간다
    assert 0.0 < float(str(cov["borrowings"])) < 0.1
    assert 0.0 < float(str(cov["depreciation"])) < 0.1
    assert 0.0 < float(str(cov["interest_expense"])) < 0.1
    assert m["n_accounts_declared"] == 24
    assert m["n_group_without_account_std"] == 0


def test_접수_지연은_api_restated_축을_드러낸다(built: build.BuildResult) -> None:
    m = _gate(built, "EG3_fin_std").metrics
    assert m["rcept_lag_p50"] == 45.0
    assert m["rcept_lag_max"] == 445.0      # 정정본의 접수번호를 API 가 돌려준다


def test_EG6_은_비12월_결산_표본이_없어_skip(built: build.BuildResult) -> None:
    g = _gate(built, "EG6_fin_std")
    assert g.status is GateStatus.SKIP and g.detail == "no_coverage"
    assert (g.metrics["n_dec_fy"], g.metrics["n_dec_fy_nonmatch"]) == (N_OUT, 0)
    assert g.metrics["n_nondec_fy"] == 0


def test_절단본_직전_기준은_전부_증언되고_끊긴_구간이_없다(built: build.BuildResult,
                                                        rows: dict[str, dict[str, object]]
                                                        ) -> None:
    """절단본 9법인은 전부 `standard` 라 **전환 사례가 없다** — 그래서 전환은 합성으로 짓는다.

    여기서 보는 것은 조회 축이 실제로 붙느냐다: 220행 중 178행이 직전 회계연도를 찾았고
    (첫 해 42행은 직전이 없어 NULL), 게이트의 두 방향 검산이 모두 0이다.
    """
    m = _gate(built, "EG3_fin_std").metrics
    assert m["n_by_revenue_basis"] == {"standard": N_OUT}
    assert m["n_by_revenue_basis_prev"] == {"None": N_OUT - N_PREV_FILLED,
                                            "standard": N_PREV_FILLED}
    for k in ("n_revenue_basis_prev_outside_vocab", "n_revenue_basis_prev_unwitnessed",
              "n_revenue_basis_prev_missing", "n_revenue_basis_changed",
              "n_corp_revenue_basis_changed"):
        assert m[k] == 0, k
    # 삼성전자 2018 사업보고서의 직전은 2017 사업보고서다
    assert rows[SEC]["revenue_basis"] == "standard"
    assert rows[SEC]["revenue_basis_prev"] == "standard"


# ── 선언 ↔ SQL ↔ fin_map 대조 ─────────────────────────────────────────────────

def test_계정_대응표는_fin_map_에서_유도된다() -> None:
    assert len(rules_s12.ACCOUNTS) == 24
    assert set(FIN_MAP) | set(rules_s12.EXTRA_ACCOUNTS) == set(rules_s12.ACCOUNTS)
    assert set(FIN_MAP) & set(rules_s12.EXTRA_ACCOUNTS) == set()
    assert "gross_profit" in FIN_MAP        # 추가 계정이 아니라 FIELD_MAP 판정만 바뀐다
    metrics = {r[0] for r in rules_s12.acct_rows()}
    assert metrics == set(rules_s12.ACCOUNTS)


def test_sql_의_acct_블록은_생성기_문자열과_같다() -> None:
    sql = rules_s12.SQL_PATH.read_text(encoding="utf-8")
    assert rules_s12.acct_values_sql() in sql


def test_cf_분기_직전_보고서_대응표가_sql_과_같다() -> None:
    """`CF_PRIOR_REPORT` 가 정본이고 `.sql` 의 CASE 가 그것을 그대로 옮긴다(S05 대응표 규약)."""
    sql = rules_s12.SQL_PATH.read_text(encoding="utf-8")
    assert rules_s12.CF_PRIOR_REPORT == {"11012": "11013", "11014": "11012", "11011": "11014"}
    for cur, prior in rules_s12.CF_PRIOR_REPORT.items():
        assert f"WHEN '{cur}' THEN '{prior}'" in sql, (cur, prior)
    # 1분기(11013)는 누계 자체가 분기값이라 대응표에 없다
    assert "11013" not in rules_s12.CF_PRIOR_REPORT


def test_판본_어휘는_4C_확장분까지_선언한다() -> None:
    assert rules_s12.VINTAGE_KIND_VOCAB[0] == rules_s12.VINTAGE_KIND == "api_restated"
    assert set(rules_s12.VINTAGE_KIND_VOCAB) == {"api_restated", "original", "corrected"}


def test_파생_컬럼_이름_규약() -> None:
    assert rules_s12.Q4_COLUMNS[0] == "revenue_q4_derived"
    assert rules_s12.CF_Q_COLUMNS == ("cf_operating_q", "cf_investing_q", "cf_financing_q",
                                      "capex_q")
    declared = list(FIN_STD.columns)
    for c in (*rules_s12.ACCOUNTS, *rules_s12.Q4_COLUMNS, *rules_s12.CF_Q_COLUMNS):
        assert c in declared, c
    assert declared[:5] == ["corp_code", "period_end", "report_code", "fs_div", "vintage_kind"]
    assert FIN_STD.grain == ("corp_code", "period_end", "report_code", "fs_div", "vintage_kind")


def test_매출_기준_두_축이_필드로_나간다() -> None:
    """`financial.revenue` 의 소비 규약이 읽으라고 말하는 라벨은 **선언되어 있어야** 한다.

    값만 내보내고 근거를 안 내보내면 "standard 끼리만 비교하라"는 규약이 소비자가 지킬 수
    없는 약속이 된다. 두 라벨은 `dataset_profile`(S19) 행으로 나가고 어휘가 닫혀 있다.
    """
    fields = {f.field_id: f for f in rules_s12.FIELDS_FIN}
    revenue = fields["financial.revenue"]
    # 금융업 매출 규칙이 확정돼 V03·Q03·G01 의 partial_support 가 풀린다
    assert revenue.requires_confirmation is False
    assert "revenue_basis_prev" in revenue.evidence
    for field_id, column in (("financial.revenue_basis", "revenue_basis"),
                             ("financial.revenue_basis_prev", "revenue_basis_prev")):
        f = fields[field_id]
        assert f.columns == (column,)
        assert f.value_type == "category" and f.unit == ""
        assert f.scope == "internal"
        assert f.requires_confirmation is False
        assert f.point_in_time is True
        assert column in FIN_STD.columns
    assert rules_s12.REVENUE_BASIS_VOCAB == ("standard", "banking_gross", "insurance_gross",
                                             "consensus", "unavailable")


def test_직전_회계연도_술어는_sql_과_같은_축이다() -> None:
    """게이트는 `.sql` 의 `basis_prev` 를 베끼지 않고 **되풀이 계산**한다 — 축은 하나여야 한다."""
    sql = rules_s12.SQL_PATH.read_text(encoding="utf-8")
    assert "basis_prev AS (" in sql
    # `.sql` 도 게이트도 (법인 · 해소된 report_code · fs_div · bsns_year − 1) 로 잇는다
    assert "bp.fy = TRY_CAST(j.bsns_year AS INTEGER) - 1" in sql
    assert "TRY_CAST(o.bsns_year AS INTEGER) - 1" in rules_s12.PREV_FY_PREDICATE
    for token in ("p.corp_code = o.corp_code", "p.report_code = o.report_code",
                  "p.fs_div = o.fs_div"):
        assert token in rules_s12.PREV_FY_PREDICATE, token


def test_격리_어휘와_상수_선언() -> None:
    assert FIN_STD.reject_reasons == ("non_krw", "period_unresolved",
                                      "rcept_lag_out_of_range", "duplicate_vintage")
    assert FIN_STD.consts == ("period_end_lag_max_days", "rcept_lag_p99_days",
                              "quarter_months", "half_months",
                              "three_quarter_months")
    assert FIN_STD.inputs == ("stg_fin", "stg_doc_meta", "stg_disclosure",
                              "disclosure_version", "corp")


def test_픽스처는_전부_positive_이고_키가_유일하다(built: build.BuildResult) -> None:
    fx = json.loads((Path(rules_s12.__file__).parent / "fixtures"
                     / "fin_std.json").read_text(encoding="utf-8"))
    assert len(fx) >= 20
    assert {f["fixture_class"] for f in fx} == {"positive"}
    assert all(set(f["key"]) == {"rcept_no"} for f in fx)
    assert _gate(built, "EG4").metrics["n_fixtures"] == len(fx)


# ── 손 트리 부정 픽스처 ───────────────────────────────────────────────────────

OBSERVED = date(2026, 9, 1)          # 손 트리의 재수집 관측일(판본 선택 축)


def _fin_row(corp: str, year: str, reprt: str, rcept: str, *, sj: str, account_id: str,
             account_nm: str, amount: float, is_krw: bool = True, currency: str = "KRW",
             ord_: int = 1, observed: date = OBSERVED) -> dict[str, object]:
    return {"corp_code": corp, "bsns_year": year, "reprt_code": reprt, "fs_div": "CFS",
            "sj_div": sj, "account_id": account_id, "account_detail": "",
            "ord": ord_, "account_nm": account_nm,
            "thstrm_amount": amount, "account_std": True, "is_krw": is_krw,
            "currency": currency, "rcept_no": rcept, "observed_date": observed}


def _fixture_file(tmp_path: Path, name: str, key: str, column: str, expect: object) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(
        [{"case": "hand", "key": {"rcept_no": key}, "column": column, "expect": expect,
          "source": "hand — 손 트리 부정 픽스처의 EG4 자리를 채운다"}], ensure_ascii=False),
        encoding="utf-8")
    return path


def _hand_build(make_stage_tree, tmp_path: Path, corps: list[tuple[str, str | None]],
                reports: list[tuple[str, str, str, str, date, date | None, str | None]],
                fin: list[dict[str, object]], fixture: tuple[str, str, object],
                duplicate_disclosure: bool = False, **bl: object) -> build.BuildResult:
    """corps = [(corp_code, acc_mt)] · reports = [(rcept, corp, year, reprt, rcept_dt,
    period_to, doc_acode)] — period_to 가 None 이면 문서가 없는 그룹이다.
    `duplicate_disclosure` 는 첫 접수의 재수집 판본을 `stg_disclosure` 에 하나 더 실는다."""
    tree = make_stage_tree(tmp_path, "stg_corp_map",
                           [{"corp_code": c, "ticker": f"00000{i}", "corp_name_current": c}
                            for i, (c, _) in enumerate(corps)])
    make_stage_tree(tmp_path, "stg_company",
                    [{"corp_code": c, "acc_mt": m, "induty_code_current": "26",
                      "observed_date": date(2026, 1, 1)} for c, m in corps])
    disclosures = [{"rcept_no": r, "rcept_dt": dt, "corp_code": c,
                    "report_nm": f"사업보고서 ({y}.12)", "is_correction": False,
                    "rm_corrected_later": False, "observed_date": OBSERVED}
                   for r, c, y, _rc, dt, _pt, _ac in reports]
    if duplicate_disclosure:
        disclosures.append({**disclosures[0], "observed_date": date(2026, 9, 3)})
    make_stage_tree(tmp_path, "stg_disclosure", disclosures,
                    partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_doc_correction",
                    [{"rcept_no": reports[0][0], "page_found": True,
                      "filed_date": reports[0][4], "filed_date_status": "parsed",
                      "reason_raw": "", "items": ""}], partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_doc_index",
                    [{"rcept_no": r, "zip_ok": True, "observed_date": OBSERVED}
                     for r, *_ in reports], partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_doc_meta",
                    [{"rcept_no": r, "member_role": "main", "doc_acode": ac,
                      "period_from": None if pt is None else date(pt.year, 1, 1),
                      "period_to": pt}
                     for r, _c, _y, _rc, _dt, pt, ac in reports],
                    partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_fin", fin, partition_class="receipt_axis")

    equity_root = tmp_path / "equity"
    base = _with(_seed(), **bl)
    for rule, fx in ((rules_s01.CORP, ("corp", "corp_code", corps[0][0])),
                     (rules_s11.DISCLOSURE_VERSION,
                      ("dv", "available_basis", "derived"))):
        path = tmp_path / f"fx_{rule.name}.json"
        key_col, col, expect = fx[0], fx[1], fx[2]
        payload = ([{"case": "hand", "key": {"corp_code": corps[0][0]}, "column": col,
                     "expect": expect, "source": "hand"}] if key_col == "corp"
                   else [{"case": "hand", "key": {"rcept_no": reports[0][0]}, "column": col,
                          "expect": expect, "source": "hand"}])
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        r = build.build_table(rule, tree.stage_root, equity_root, base,
                              build_id=f"b_hand_{rule.name}", fixtures_path=path,
                              gate_thresholds={"EG7": 1.0})
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    key, column, expect = fixture
    return build.build_table(
        FIN_STD, tree.stage_root, equity_root, base, build_id="b_hand_fin",
        fixtures_path=_fixture_file(tmp_path, "fx_fin_std", key, column, expect),
        gate_thresholds={"EG7": 1.0})


def test_부정_결산월도_문서도_없으면_period_unresolved(make_stage_tree, tmp_path: Path) -> None:
    corps = [("00000001", "12"), ("00000002", None)]
    reports = [
        ("20210330000001", "00000001", "2020", "11011", date(2021, 3, 30),
         date(2020, 12, 31), "11011"),
        ("20210330000002", "00000002", "2020", "11011", date(2021, 3, 30), None, None),
    ]
    fin = [
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0),
        _fin_row("00000002", "2020", "11011", "20210330000002", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=200.0),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20210330000001", "period_end_basis", "document"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert r.out_dir is not None
    rej = _rejects(r.out_dir)
    assert [x["reject_reason"] for x in rej] == ["period_unresolved"]
    assert rej[0]["corp_code"] == "00000002"
    assert rej[0]["period_end"] is None and rej[0]["period_end_basis"] is None


def test_결산월이_있으면_문서_없이도_후보_규칙이_기간을_찾는다(make_stage_tree,
                                                             tmp_path: Path) -> None:
    corps = [("00000001", "12"), ("00000002", "12")]
    reports = [
        ("20210330000001", "00000001", "2020", "11011", date(2021, 3, 30),
         date(2020, 12, 31), "11011"),
        ("20210330000002", "00000002", "2020", "11011", date(2021, 3, 30), None, None),
    ]
    fin = [
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0),
        _fin_row("00000002", "2020", "11011", "20210330000002", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=200.0),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20210330000002", "period_end_basis", "inferred"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (2, 0)
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    inferred = rows["20210330000002"]
    assert inferred["period_end"] == date(2020, 12, 31)
    assert inferred["period_end_basis"] == "inferred"
    assert inferred["report_code"] == "11011"     # 문서가 없으면 API 축을 쓴다
    assert inferred["period_start"] is None


def test_부정_비KRW_그룹은_격리된다(make_stage_tree, tmp_path: Path) -> None:
    corps = [("00000001", "12"), ("00000002", "12")]
    reports = [
        ("20210330000001", "00000001", "2020", "11011", date(2021, 3, 30),
         date(2020, 12, 31), "11011"),
        ("20210330000002", "00000002", "2020", "11011", date(2021, 3, 30),
         date(2020, 12, 31), "11011"),
    ]
    fin = [
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0),
        _fin_row("00000002", "2020", "11011", "20210330000002", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=200.0,
                 is_krw=False, currency="HKD"),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20210330000001", "currency", "KRW"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert r.out_dir is not None
    rej = _rejects(r.out_dir)
    assert [(x["corp_code"], x["reject_reason"]) for x in rej] == [("00000002", "non_krw")]


def test_부정_같은_grain_으로_접히면_둘_다_격리된다(make_stage_tree, tmp_path: Path) -> None:
    """서로 다른 (bsns_year, reprt_code) 가 같은 period_end·report_code 로 풀리는 경우.

    어느 쪽이 옳은지 규칙이 못 고르므로 둘 다 격리한다 — 하나를 고르면 EG3-P01(키 유일)은
    통과하지만 어떤 판본이 남았는지가 비결정이 된다.
    """
    corps = [("00000001", "12"), ("00000002", "12")]
    reports = [
        ("20210330000003", "00000002", "2020", "11011", date(2021, 3, 30),
         date(2020, 12, 31), "11011"),        # 충돌 없는 대조군
        ("20210330000001", "00000001", "2020", "11011", date(2021, 3, 30),
         date(2020, 12, 31), "11011"),
        ("20220330000002", "00000001", "2021", "11011", date(2022, 3, 30),
         date(2020, 12, 31), "11011"),        # 문서가 같은 기간을 가리킨다
    ]
    fin = [
        _fin_row("00000002", "2020", "11011", "20210330000003", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=300.0),
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0),
        _fin_row("00000001", "2021", "11011", "20220330000002", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=200.0),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20210330000003", "period_end", "2020-12-31"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 2)
    assert r.out_dir is not None
    rej = _rejects(r.out_dir)
    assert {x["corp_code"] for x in rej} == {"00000001"}
    assert {x["reject_reason"] for x in rej} == {"duplicate_vintage"}
    assert dict(_gate(r, "EG7").metrics["reject_by_reason"]) == {"duplicate_vintage": 2}


def test_부정_접수지연_상한_밖은_격리된다(make_stage_tree, tmp_path: Path) -> None:
    corps = [("00000001", "12"), ("00000002", "12")]
    reports = [
        ("20210330000001", "00000001", "2020", "11011", date(2021, 3, 30),
         date(2020, 12, 31), "11011"),
        ("20260330000002", "00000002", "2020", "11011", date(2026, 3, 30),
         date(2020, 12, 31), "11011"),        # 1,915일 지연 → 상한 1,826 밖
    ]
    fin = [
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0),
        _fin_row("00000002", "2020", "11011", "20260330000002", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=200.0),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20210330000001", "revenue_basis", "standard"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert r.out_dir is not None
    assert [(x["corp_code"], x["reject_reason"]) for x in _rejects(r.out_dir)] == [
        ("00000002", "rcept_lag_out_of_range")]


def test_부정_재수집_판본이_있어도_grain_과_합계가_흔들리지_않는다(make_stage_tree,
                                                                    tmp_path: Path) -> None:
    """`stg_fin`·`stg_disclosure` 는 append_only·key_unique=False 라 같은 자연키가 여러 번 실린다.

    ① `stg_disclosure` 중복을 안 접으면 `head` 조인이 grp 행을 늘려 grain 이 깨지고
    ② `stg_fin` 중복을 안 접으면 `sum` 집계(lease_liab)가 이중계상된다.
    """
    corps = [("00000001", "12")]
    reports = [("20210330000001", "00000001", "2020", "11011", date(2021, 3, 30),
                date(2020, 12, 31), "11011")]
    row = dict(_fin_row("00000001", "2020", "11011", "20210330000001", sj="BS",
                        account_id="ifrs-full_CurrentLeaseLiabilities", account_nm="리스부채",
                        amount=40.0))
    fin = [
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0),
        row,
        {**row, "observed_date": date(2026, 9, 3)},     # 같은 자연키의 재수집 판본
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="BS",
                 account_id="ifrs-full_NoncurrentLeaseLiabilities", account_nm="리스부채",
                 amount=60.0, ord_=2),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20210330000001", "lease_liab", "100.0"), duplicate_disclosure=True)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 0)            # 접수 중복이 grain 을 늘리지 않는다
    rows = _rows(r.out_dir)                            # type: ignore[arg-type]
    # 유동 40 + 비유동 60 = 100. 판본을 안 접으면 140 이 된다(이중계상).
    assert _num(rows[0]["lease_liab"]) == Decimal("100")
    m = _gate(r, "EG3_fin_std").metrics
    assert m["n_stg_fin_dup_natural_key"] == 1
    assert m["n_disclosure_dup_rcept"] == 1
    eg1 = _gate(r, "EG1").metrics
    assert (eg1["lhs"], eg1["rhs"], eg1["delta"]) == (1, 1, 0)


def test_부정_pick_이_갈리면_값을_만들지_않는다(make_stage_tree, tmp_path: Path) -> None:
    """같은 우선순위에 서로 다른 값이 둘이면 모호(ambiguous) — 합치거나 고르지 않는다."""
    corps = [("00000001", "12")]
    reports = [("20210330000001", "00000001", "2020", "11011", date(2021, 3, 30),
                date(2020, 12, 31), "11011")]
    fin = [
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0),
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs_Revenue", account_nm="수익(매출액)", amount=999.0),
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="BS",
                 account_id="ifrs-full_Assets", account_nm="자산총계", amount=500.0),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20210330000001", "revenue", None))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20210330000001"]
    assert row["revenue"] is None
    assert row["revenue_basis"] == "unavailable"
    assert _num(row["total_asset"]) == Decimal("500")


def test_금융업_매출_대체는_요구_태그가_있을_때만_발동한다(make_stage_tree,
                                                        tmp_path: Path) -> None:
    corps = [("00000001", "12")]
    reports = [("20210330000001", "00000001", "2020", "11011", date(2021, 3, 30),
                date(2020, 12, 31), "11011")]
    fin = [
        # Revenue 태그가 없고 이자수익이 있다 → banking_gross 합산
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs-full_RevenueFromInterest", account_nm="이자수익",
                 amount=700.0),
        _fin_row("00000001", "2020", "11011", "20210330000001", sj="IS",
                 account_id="ifrs-full_FeeAndCommissionIncome", account_nm="수수료수익",
                 amount=300.0),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20210330000001", "revenue_basis", "banking_gross"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    assert _num(rows["20210330000001"]["revenue"]) == Decimal("1000")
    assert rows["20210330000001"]["revenue_basis"] == "banking_gross"


# 삼성카드(00126292) 서버 실측 — 2023·2024 는 `standard`, 2025 부터 `banking_gross` 다.
# 세 값을 그대로 써서 「가짜 −12%」가 어떤 숫자에서 나오는지 시험이 직접 보이게 한다.
SC_2023 = Decimal("4004222629932")
SC_2024 = Decimal("4383209688411")
SC_2025_INTEREST = Decimal("2842720000000")
SC_2025_FEE = Decimal("1000000000000")
SC_2025 = SC_2025_INTEREST + SC_2025_FEE          # banking_gross 합산 = 3,842,720,000,000


def test_기준이_바뀌면_직전_기준이_남아_가짜_성장률을_막는다(make_stage_tree,
                                                        tmp_path: Path) -> None:
    """한 법인 안에서 `revenue_basis` 가 바뀌면 매출 시계열이 끊긴다(BLOCKED_FACTORS §5-1).

    절단본 9법인은 전부 `standard` 라 전환 사례가 없어 손 트리로 짓는다. 법인 1 은 삼성카드
    실측을 그대로 옮긴 2023·2024 `standard` → 2025 `banking_gross` 이고, 법인 2 는 2021 이
    비어 직전 회계연도가 없는 경우다. equity 는 **버리지 않는다** — 두 라벨을 싣고 성장률을
    결측 처리할지는 팩터층이 정한다(WORKFLOW §0-2).
    """
    corps = [("00000001", "12"), ("00000002", "12")]
    reports = [
        ("20240330000001", "00000001", "2023", "11011", date(2024, 3, 30),
         date(2023, 12, 31), "11011"),
        ("20250330000001", "00000001", "2024", "11011", date(2025, 3, 30),
         date(2024, 12, 31), "11011"),
        ("20260330000001", "00000001", "2025", "11011", date(2026, 3, 30),
         date(2025, 12, 31), "11011"),
        ("20210330000002", "00000002", "2020", "11011", date(2021, 3, 30),
         date(2020, 12, 31), "11011"),
        ("20230330000002", "00000002", "2022", "11011", date(2023, 3, 30),
         date(2022, 12, 31), "11011"),
    ]
    fin = [
        _fin_row("00000001", "2023", "11011", "20240330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=float(SC_2023)),
        _fin_row("00000001", "2024", "11011", "20250330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=float(SC_2024)),
        # 2025 는 표준 태그가 사라지고 이자·수수료 수익만 남는다 → banking_gross 합산
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="IS",
                 account_id="ifrs-full_RevenueFromInterest", account_nm="이자수익",
                 amount=float(SC_2025_INTEREST)),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="IS",
                 account_id="ifrs-full_FeeAndCommissionIncome", account_nm="수수료수익",
                 amount=float(SC_2025_FEE)),
        _fin_row("00000002", "2020", "11011", "20210330000002", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0),
        _fin_row("00000002", "2022", "11011", "20230330000002", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=120.0),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "revenue_basis_prev", "standard"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]

    # ① 첫 해는 직전이 없다
    assert rows["20240330000001"]["revenue_basis_prev"] is None
    # ② 기준이 같은 해 — 성장률을 쓸 수 있다
    assert rows["20250330000001"]["revenue_basis"] == "standard"
    assert rows["20250330000001"]["revenue_basis_prev"] == "standard"
    # ③ 기준이 바뀐 해 — 두 라벨이 갈리고, 갈린 사실이 행에 실려 나간다
    flip = rows["20260330000001"]
    assert flip["revenue_basis"] == "banking_gross"
    assert flip["revenue_basis_prev"] == "standard"
    assert _num(flip["revenue"]) == SC_2025
    # 그대로 나눴다면 나왔을 「가짜 −12%」 — 이 계산을 막는 재료가 위 두 라벨이다
    naive = SC_2025 / SC_2024 - 1
    assert Decimal("-0.13") < naive < Decimal("-0.12")
    # ④ 회계연도가 빈 해는 직전을 찾지 않는다(2021 이 없다)
    assert rows["20210330000002"]["revenue_basis_prev"] is None
    assert rows["20230330000002"]["revenue_basis_prev"] is None

    m = _gate(r, "EG3_fin_std").metrics
    assert m["n_revenue_basis_changed"] == 1
    assert m["n_corp_revenue_basis_changed"] == 1
    for k in ("n_revenue_basis_prev_outside_vocab", "n_revenue_basis_prev_unwitnessed",
              "n_revenue_basis_prev_missing"):
        assert m[k] == 0, k
