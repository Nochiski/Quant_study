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
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s11, rules_s12
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from fin_map import FIN_MAP
from stage import manifest as stage_manifest

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
FIN_STD = rules_s12.FIN_STD

N_SRC = 226
N_OUT = 220
N_REJECT = 6
N_PREV_FILLED = 178                    # 직전 회계연도 같은 보고서를 찾은 행 (나머지 42 는 첫 해)
N_ORIG_FILING = 8                      # 원본 공시일을 승계한 정정본 행 (09-30 규칙)
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
                                             "EG8_fin_std", "EG6_fin_std", "EG4", "EG5a"]
    assert {g.name: g.status.value for g in built.gates if g.name != "EG5a"} == {
        "EG0": "pass", "EG7": "pass", "EG1": "pass", "EG2": "pass", "EG3": "pass",
        "EG3_fin_std": "pass", "EG8_fin_std": "pass", "EG6_fin_std": "skip", "EG4": "pass"}
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


def test_PIT_축은_접수일이고_재무표_무관_정정만_원본일을_잇는다(
        rows: dict[str, dict[str, object]]) -> None:
    # 절단본 실측(09-30): 220 중 8행이 재무표를 안 건드린 정정본 — 공개일만 원본 접수일로 당겨진다
    moved = [r for r in rows.values() if r["available_date"] != r["rcept_dt"]]
    assert len(moved) == N_ORIG_FILING
    for r in rows.values():
        assert r["available_basis"] == "derived"
        assert r["period_end"] <= r["available_date"] <= r["rcept_dt"]


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
                                             "consensus", "no_is_statement", "unmapped")
    assert rules_s12.REVENUE_BASIS_NULL_REASONS == ("no_is_statement", "unmapped")


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


def _stage_available(rcept: str, rcept_dt: date) -> date:
    """가짜 stage `stg_disclosure.available_date` — stage E08 규칙(원천 rcept_dt 와 접수번호 앞
    8자리 중 늦은 쪽)을 그대로 흉내 낸다. 정상 접수는 rcept_dt 와 같다."""
    return max(rcept_dt, date(int(rcept[:4]), int(rcept[4:6]), int(rcept[6:8])))


def _fin_row(corp: str, year: str, reprt: str, rcept: str, *, sj: str, account_id: str,
             account_nm: str, amount: float | None, is_krw: bool = True, currency: str = "KRW",
             ord_: int = 1, observed: date = OBSERVED,
             account_std: bool = True) -> dict[str, object]:
    # `account_std` = stage 의 `account_id <> '-표준계정코드 미사용-'`(rules_dart). 비표준 이름
    # 폴백(capex 자산별 합)을 시험하려면 이 축을 손으로 내려야 한다.
    # `account_nm_norm` 도 stage 파생 컬럼(T-E)이라 같은 규칙(공백 제거)으로 여기서 만든다.
    return {"corp_code": corp, "bsns_year": year, "reprt_code": reprt, "fs_div": "CFS",
            "sj_div": sj, "account_id": account_id, "account_detail": "",
            "ord": ord_, "account_nm": account_nm,
            "account_nm_norm": re.sub(r"\s+", "", account_nm),
            "thstrm_amount": amount, "account_std": account_std, "is_krw": is_krw,
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
                duplicate_disclosure: bool = False,
                report_names: dict[str, str] | None = None,
                extra_disclosures: list[tuple[str, str, str, date]] | None = None,
                corrections: list[dict[str, object]] | None = None,
                **bl: object) -> build.BuildResult:
    """corps = [(corp_code, acc_mt)] · reports = [(rcept, corp, year, reprt, rcept_dt,
    period_to, doc_acode)] — period_to 가 None 이면 문서가 없는 그룹이다.
    `duplicate_disclosure` 는 첫 접수의 재수집 판본을 `stg_disclosure` 에 하나 더 실는다.
    정정 판본 시험용: `report_names` 는 접수번호별 공시명(기본 '사업보고서 (YYYY.12)'),
    `extra_disclosures` = [(rcept, corp, report_nm, rcept_dt)] 는 재무 행이 없는 공시(원본·중간 정정),
    `corrections` 는 `stg_doc_correction` 행(기본 = 첫 접수 한 행)."""
    names = report_names or {}
    tree = make_stage_tree(tmp_path, "stg_corp_map",
                           [{"corp_code": c, "ticker": f"{i:06d}", "corp_name_current": c}
                            for i, (c, _) in enumerate(corps)])
    make_stage_tree(tmp_path, "stg_company",
                    [{"corp_code": c, "acc_mt": m, "induty_code_current": "26",
                      "observed_date": date(2026, 1, 1)} for c, m in corps])
    listed = [(r, c, names.get(r, f"사업보고서 ({y}.12)"), dt)
              for r, c, y, _rc, dt, _pt, _ac in reports] + list(extra_disclosures or [])
    disclosures = [{"rcept_no": r, "rcept_dt": dt, "corp_code": c, "report_nm": nm,
                    "is_correction": nm.startswith(("[기재정정]", "[첨부정정]")),
                    "rm_corrected_later": False, "available_date": _stage_available(r, dt),
                    "observed_date": OBSERVED}
                   for r, c, nm, dt in listed]
    if duplicate_disclosure:
        disclosures.append({**disclosures[0], "observed_date": date(2026, 9, 3)})
    make_stage_tree(tmp_path, "stg_disclosure", disclosures,
                    partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_doc_correction",
                    corrections if corrections is not None else
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
    # 이 손 트리는 2행 중 1행이 `inferred` 라 seed 임계(0.2)를 구조적으로 넘는다 — 여기서 보는
    # 것은 후보 규칙이지 최근 구간 비율이 아니므로 임계를 등재 해제한다(DEFECT-F01 게이트).
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20210330000002", "period_end_basis", "inferred"),
                    fin_std_inferred_recent_ratio_max=None)
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


# ── 정정본의 원본 공시일 승계 (09-30, `docs/research/2026-09-30-gpt-layer-debate.md` ③) ──────
# DART API 는 정정이 있으면 정정본만 돌려준다. 원본부터 그 판까지의 정정이 모두 재무표를 건드리지
# 않았으면 재무 수치는 원본과 같으므로 공개일은 원본 접수일이다. 하나라도 건드렸거나 판단할 수
# 없으면(정정 첫 장 미해석·원본 제출일 불일치) 정정 접수일을 지킨다.
_ORIG = ("20210330000001", date(2021, 3, 30))
_NONFIN = "VIII. 임원 및 직원 등에 관한 사항 1. 임원 및 직원의 현황"
_FIN_SECTION = "III. 재무에 관한 사항 8. 기타 재무에 관한 사항"
_EMPTY_ITEMS = "[]"          # 정정 첫 장에서 항목 표를 하나도 못 뽑았다(stage 원문 그대로)


def _correction_row(make_stage_tree, tmp_path: Path,
                    chain: list[tuple[str, date, str | None, date]],
                    reason: str = "기재정정") -> dict[str, object]:
    """chain = [(정정 접수번호, 접수일, 정정 항목, 첫 장의 원본 제출일)] — 정정 항목 None 은 첫 장
    미해석, `_EMPTY_ITEMS` 는 빈 항목 표다. 마지막이 API 가 돌려준 판(재무 행의 접수번호)이다.
    원본은 `_ORIG`.
    `reason` 은 정정 첫 장의 정정 사유(`reason_raw`)다."""
    corps = [("00000001", "12")]
    last, last_dt = chain[-1][0], chain[-1][1]
    reports = [(last, "00000001", "2020", "11011", last_dt, date(2020, 12, 31), "11011")]
    fin = [_fin_row("00000001", "2020", "11011", last, sj="IS",
                    account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0)]
    corr_nm = "[기재정정]사업보고서 (2020.12)"
    extra = [(_ORIG[0], "00000001", "사업보고서 (2020.12)", _ORIG[1])] + [
        (r, "00000001", corr_nm, dt) for r, dt, _it, _fd in chain[:-1]]
    corrections = [{"rcept_no": r, "page_found": True, "filed_date": fd,
                    "filed_date_status": "parsed", "reason_raw": reason,
                    "items": it if it in (None, _EMPTY_ITEMS)
                    else json.dumps([{"항목": it}], ensure_ascii=False)}
                   for r, _dt, it, fd in chain]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin, (last, "rcept_no", last),
                    report_names={r: corr_nm for r, *_ in chain}, extra_disclosures=extra,
                    corrections=corrections)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert r.out_dir is not None
    out = _rows(r.out_dir)
    assert len(out) == 1
    return out[0]


def test_재무표를_안_건드린_정정은_원본_공시일을_이어받는다(make_stage_tree, tmp_path: Path) -> None:
    row = _correction_row(make_stage_tree, tmp_path,
                          [("20210615000002", date(2021, 6, 15), _NONFIN, _ORIG[1])])
    assert row["rcept_no"] == "20210615000002"
    assert row["rcept_dt"] == date(2021, 6, 15)            # 판의 접수일은 그대로다
    assert row["available_date"] == _ORIG[1]              # 공개일만 원본 접수일로 당겨진다
    assert row["available_basis"] == "derived"


def test_재무에_관한_사항을_고친_정정은_정정일을_지킨다(make_stage_tree, tmp_path: Path) -> None:
    row = _correction_row(make_stage_tree, tmp_path,
                          [("20210615000002", date(2021, 6, 15), _FIN_SECTION, _ORIG[1])])
    assert row["available_date"] == date(2021, 6, 15)
    assert row["available_basis"] == "derived"


def test_중간_정정이_재무표를_고쳤으면_정정일을_지킨다(make_stage_tree, tmp_path: Path) -> None:
    row = _correction_row(make_stage_tree, tmp_path, [
        ("20210510000002", date(2021, 5, 10), "III. 재무에 관한 사항 2. 연결재무제표", _ORIG[1]),
        ("20210615000003", date(2021, 6, 15), _NONFIN, _ORIG[1])])
    assert row["rcept_no"] == "20210615000003"
    assert row["available_date"] == date(2021, 6, 15)
    assert row["available_basis"] == "derived"


@pytest.mark.parametrize("items, filed, reason", [
    (None, _ORIG[1], "기재정정"),             # 정정 첫 장 미해석 → 판단 불가
    (_NONFIN, date(2021, 2, 1), "기재정정"),  # 첫 장의 원본 제출일이 연결된 원본과 어긋난다
    # C-11(N-25 Q1) — 항목 표가 비었다(서술형·표만 있는 첫 장) → 판단 불가. 예전엔 FALSE 로 읽혀
    # 재작성 값에 원본 공시일이 붙었다(20181129000580 개발비 판단오류 재작성 등)
    (_EMPTY_ITEMS, _ORIG[1], "기재정정"),
    # C-11 — 항목은 비재무(배당 지표)인데 사유가 재무제표 재작성이다(00287812 FY2015, 2018-09-07)
    (_NONFIN, _ORIG[1], "2015~2017 연결재무제표 재작성 및 감사보고서 재발행"),
])
def test_판단할_수_없으면_정정일을_지킨다(make_stage_tree, tmp_path: Path,
                                     items: str | None, filed: date, reason: str) -> None:
    row = _correction_row(make_stage_tree, tmp_path,
                          [("20210615000002", date(2021, 6, 15), items, filed)], reason=reason)
    assert row["available_date"] == date(2021, 6, 15)
    assert row["available_basis"] == "derived"


def test_재제출본은_접수번호_날짜부터_보인다(make_stage_tree, tmp_path: Path) -> None:
    """J-41(N-26 4.2) — 박셀바이오 재제출본. 목록 API 가 2025-08-28 접수번호에 원래 제출일
    rcept_dt(2024-03-19)를 붙인다. 공개일은 stage 가 보정한 `stg_disclosure.available_date`
    (접수번호 날짜)다 — 원천 rcept_dt 를 쓰면 527일 look-ahead. 판의 `rcept_dt` 는 원천 그대로
    기간 판정·접수 지연 격리 축으로 남는다."""
    rcept = "20250828000446"
    corps = [("01335851", "12")]
    reports = [(rcept, "01335851", "2023", "11011", date(2024, 3, 19), date(2023, 12, 31),
                "11011")]
    fin = [_fin_row("01335851", "2023", "11011", rcept, sj="IS",
                    account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0)]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin, (rcept, "rcept_no", rcept))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    out = _rows(r.out_dir)                             # type: ignore[arg-type]
    assert len(out) == 1
    assert out[0]["rcept_dt"] == date(2024, 3, 19)
    assert out[0]["available_date"] == date(2025, 8, 28)


# ── 일일 재수집으로 원본·정정이 쌓인 재무 그룹 (G-21, N-25 Q2 — B-41 결정 A 와 같은 정책) ──────
# 백필 구간은 API 가 정정본만 줘서 '정정 값 + 정정일'인데, 일일 수집은 같은 그룹에 원본 뒤에 정정을
# 덧붙인다. 그룹의 **최신 판본(max rcept_no)** 값과 그 판의 공개일을 싣는다(오브젠 01472930 FY2025).
_G21_ORIG = ("20260319001177", date(2026, 3, 19))      # 원본 — 백필이 08-26 에 관측
_G21_CORR = ("20260928000253", date(2026, 9, 28))      # [기재정정] — 일일 수집이 09-28 에 덧붙였다
_G21_SEEN = (date(2026, 8, 26), date(2026, 9, 28))


def _g21_row(make_stage_tree, tmp_path: Path, fin: list[dict[str, object]],
             corrections: list[dict[str, object]] | None = None) -> dict[str, object]:
    reports = [(rc, "01472930", "2025", "11011", dt, date(2025, 12, 31), "11011")
               for rc, dt in (_G21_ORIG, _G21_CORR)]
    r = _hand_build(make_stage_tree, tmp_path, [("01472930", "12")], reports, fin,
                    (_G21_CORR[0], "rcept_no", _G21_CORR[0]),
                    report_names={_G21_CORR[0]: "[기재정정]사업보고서 (2025.12)"},
                    corrections=corrections)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    out = _rows(r.out_dir)                             # type: ignore[arg-type]
    assert len(out) == 1
    return out[0]


def _g21_fin(sj: str, account_id: str, account_nm: str, amounts: tuple[float, float],
             ords: tuple[int, int] = (1, 1)) -> list[dict[str, object]]:
    return [_fin_row("01472930", "2025", "11011", rc, sj=sj, account_id=account_id,
                     account_nm=account_nm, amount=amt, ord_=o, observed=seen)
            for (rc, _dt), amt, o, seen in zip((_G21_ORIG, _G21_CORR), amounts, ords, _G21_SEEN,
                                               strict=True)]


def test_원본과_정정이_쌓이면_최신_판본_값과_정정일을_싣는다(make_stage_tree,
                                                          tmp_path: Path) -> None:
    row = _g21_row(make_stage_tree, tmp_path,
                   _g21_fin("IS", "ifrs-full_Revenue", "매출액", (100.0, 90.0)))
    assert row["rcept_no"] == _G21_CORR[0]
    assert _num(row["revenue"]) == Decimal("90")
    assert row["rcept_dt"] == _G21_CORR[1]
    assert row["available_date"] == _G21_CORR[1]       # 정정 첫 장 정보가 없다 → 정정일


def test_정정이_줄_순서를_바꿔도_값이_모호해지지_않는다(make_stage_tree, tmp_path: Path) -> None:
    """이오플로우 01274310 FY2024 — 정정이 `ifrs-full_Equity` 의 ord 를 24 → 26 으로 옮겼다.
    자연키에 ord 가 들어 있어 두 판의 줄이 다 살아남으면 `pick` 이 갈려 total_equity 가 NULL 이
    된다."""
    row = _g21_row(make_stage_tree, tmp_path,
                   _g21_fin("BS", "ifrs-full_Equity", "자본총계", (576.0, 457.0), ords=(24, 26)))
    assert row["rcept_no"] == _G21_CORR[0]
    assert row["total_equity"] is not None and _num(row["total_equity"]) == Decimal("457")


def test_재무_무관_정정이_쌓이면_값은_정정본이고_공개일은_원본이다(make_stage_tree,
                                                                tmp_path: Path) -> None:
    """G-21 과 원본 공시일 승계(e1.22.0 · C-11)가 같은 판을 본다 — 최신 판본이 재무 무관 정정이고
    첫 장 원본 제출일이 확인되면 공개일은 원본 접수일이다(백필 구간과 같은 정책)."""
    corrections = [{"rcept_no": _G21_CORR[0], "page_found": True, "filed_date": _G21_ORIG[1],
                    "filed_date_status": "parsed", "reason_raw": "기재정정",
                    "items": json.dumps([{"항목": _NONFIN}], ensure_ascii=False)}]
    row = _g21_row(make_stage_tree, tmp_path,
                   _g21_fin("IS", "ifrs-full_Revenue", "매출액", (100.0, 100.0)), corrections)
    assert row["rcept_no"] == _G21_CORR[0]
    assert row["rcept_dt"] == _G21_CORR[1]
    assert row["available_date"] == _G21_ORIG[1]


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
    assert row["revenue_basis"] == "unmapped"
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


# ── DEFECT-F01: period_end 추정 폴백 가시화 (감사 09-19) ──────────────────────


def _recent_reports() -> tuple[list[tuple[str, str | None]],
                               list[tuple[str, str, str, str, date, date | None, str | None]],
                               list[dict[str, object]]]:
    """같은 접수일의 그룹 둘 — 하나는 문서 정본, 하나는 문서가 없어 추정 폴백."""
    corps = [("00000001", "12"), ("00000002", "12")]
    reports = [
        ("20260330000001", "00000001", "2025", "11011", date(2026, 3, 30),
         date(2025, 12, 31), "11011"),
        # 문서 짝이 없다 — `corp.fiscal_month` 추정으로 폴백한다(period_end_basis='inferred')
        ("20260330000002", "00000002", "2025", "11011", date(2026, 3, 30), None, None),
    ]
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=100.0),
        _fin_row("00000002", "2025", "11011", "20260330000002", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=200.0),
    ]
    return corps, reports, fin


def test_최근_구간_추정_폴백_비율이_기록된다(make_stage_tree, tmp_path: Path) -> None:
    """DEFECT-F01 — 문서층이 뒤처지면 신규 그룹이 `period_end` 정본을 잃고 `corp.fiscal_month`
    추정으로 폴백한다. 결산월이 어긋나거나 없는 법인은 후보가 0개가 되어 행째로 사라지는데
    (09-19 실측 8/31 이후 그룹 14 → 10) 기존 게이트는 전부 못 잡는다: EG7 격리 비율 1.28% <
    임계 3% · `n_by_period_end_basis` 는 임계 없음 · `n_period_end_not_document` 는 정의상 0.
    상수가 없으면 **기록만** 하고 판정하지 않는다(GATES §0-3).
    """
    corps, reports, fin = _recent_reports()
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "period_end_basis", "document"),
                    fin_std_inferred_recent_ratio_max=None)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    m = _gate(r, "EG3_fin_std").metrics
    assert m["n_recent_rows"] == 2
    assert m["n_period_end_inferred_recent"] == 1
    assert m["ratio_period_end_inferred_recent"] == 0.5
    assert m["recent_from"] == "2026-02-28"          # max(available_date) − 30일
    assert m["fin_std_inferred_recent_ratio_max"] is None
    assert m["n_period_end_inferred_recent_over_max"] == 0


def test_추정_폴백이_임계를_넘으면_EG3_fin_std가_폐기한다(make_stage_tree,
                                                        tmp_path: Path) -> None:
    corps, reports, fin = _recent_reports()
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "period_end_basis", "document"),
                    fin_std_inferred_recent_ratio_max=0.2, inferred_recent_days=30)
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG3_fin_std")
    assert g.status is GateStatus.FAIL
    assert g.metrics["n_period_end_inferred_recent_over_max"] == 1
    assert "n_period_end_inferred_recent_over_max" in g.detail


def test_추정_폴백이_임계_아래면_통과한다(make_stage_tree, tmp_path: Path) -> None:
    corps, reports, fin = _recent_reports()
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "period_end_basis", "document"),
                    fin_std_inferred_recent_ratio_max=0.6, inferred_recent_days=30)
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert _gate(r, "EG3_fin_std").metrics["n_period_end_inferred_recent_over_max"] == 0


# ── DQ-8: 유형자산 취득을 자산별로 나눠 적는 회사 (2026-09-26 실측) ─────────────

# DART 현금흐름표는 유형자산 취득을 집계 한 줄(2,218사) 또는 자산별 줄(`dart_PurchaseOf*` 9종 +
# 비표준 이름)로 적고 **같은 회사가 둘 다 적는 경우는 0건**이다. FY2025 연간 2,631사 중 capex
# 결측 311사이고 그중 211사가 자산별 합으로 살아난다.
CAPEX_AGG_ID = "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
NONSTD_ID = "-표준계정코드 미사용-"          # stage 가 account_std=false 로 적는 자리


def _capex_corp() -> tuple[list[tuple[str, str | None]],
                           list[tuple[str, str, str, str, date, date | None, str | None]]]:
    """capex 시험용 1법인 1사업보고서(2025 연간)."""
    return ([("00000001", "12")],
            [("20260330000001", "00000001", "2025", "11011", date(2026, 3, 30),
              date(2025, 12, 31), "11011")])


def test_capex_자산별_줄만_있으면_합이_capex가_된다(make_stage_tree, tmp_path: Path) -> None:
    """집계 줄이 없는 회사 — 자산별 줄(표준 4 + 비표준 이름 1)을 더해 capex 를 만든다.

    `건설중인자산의 취득` 은 표준 태그(`dart_PurchaseOfConstructionInProgress`)의 계정명이자
    비표준 이름 목록에도 있는 이름이라, 같은 tier 안에서 이름 쪽에 한 번 더 걸리면 50 이 두 번
    더해진다. kind `nm_nonstd` 가 표준계정코드 미사용 행만 보게 해서 그것을 막는다.
    """
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_PurchaseOfLand", account_nm="토지의 취득",
                 amount=100.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_PurchaseOfMachinery", account_nm="기계장치의 취득",
                 amount=200.0, ord_=2),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_PurchaseOfConstructionInProgress",
                 account_nm="건설중인자산의 취득", amount=50.0, ord_=3),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_PurchaseOfVehicles", account_nm="차량운반구의 취득",
                 amount=0.0, ord_=4),
        # 표준계정코드 미사용 행 — 이름으로만 잡힌다
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="공구와기구의 취득", amount=30.0,
                 ord_=5, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "ppe_parts"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert _num(row["capex_ytd"]) == Decimal("380")
    assert row["capex_basis"] == "ppe_parts"
    m = _gate(r, "EG3_fin_std").metrics
    assert m["n_capex_basis_outside_vocab"] == 0
    assert m["n_by_capex_basis"] == {"ppe_parts": 1}


def test_capex_집계_줄이_있으면_자산별_줄을_더하지_않는다(make_stage_tree,
                                                        tmp_path: Path) -> None:
    """집계 줄(a tier)이 하나라도 있으면 `min(tier)` 이 거기서 끝난다 — 이중계상 불가."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=CAPEX_AGG_ID, account_nm="유형자산의 취득",
                 amount=1000.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_PurchaseOfLand", account_nm="토지의 취득",
                 amount=400.0, ord_=2),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_PurchaseOfMachinery", account_nm="기계장치의 취득",
                 amount=600.0, ord_=3),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "standard"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert _num(row["capex_ytd"]) == Decimal("1000")
    assert row["capex_basis"] == "standard"


def test_capex_사용권자산은_유형자산_취득이_아니다(make_stage_tree, tmp_path: Path) -> None:
    """리스(사용권자산)·무형자산은 집계 줄의 정의(PPE) 밖이라 합에 넣지 않는다.

    0 규칙(F-A3)은 여기서 서지 않는다 — 이 픽스처엔 영업·투자 소계가 없어 「현금흐름표가 있다」
    는 판정 자체가 안 선다. 소계가 있으면 같은 구성이 0 · `none_in_cf` 가 된다
    (test_capex_현금흐름표에_유형자산_취득_줄이_없으면_0이다).
    """
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_AdditionsToRightofuseAssets",
                 account_nm="사용권자산의 취득", amount=700.0, ord_=1),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "no_cf_statement"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert row["capex_ytd"] is None
    assert row["capex_basis"] == "no_cf_statement"


def test_capex_기준_어휘가_닫혀_있다(built: build.BuildResult,
                                    rows: dict[str, dict[str, object]]) -> None:
    """`capex_basis` 는 `revenue_basis` 와 같은 자리·같은 규약의 라벨이다(어휘 폐쇄)."""
    assert rules_s12.CAPEX_BASIS_VOCAB == ("standard", "ppe_parts", "ppe_incl_invprop",
                                          "none_in_cf", "no_cf_statement", "unmapped")
    assert rules_s12.CAPEX_BASIS_NULL_REASONS == ("no_cf_statement", "unmapped")
    m = _gate(built, "EG3_fin_std").metrics
    assert m["n_capex_basis_outside_vocab"] == 0
    assert set(m["n_by_capex_basis"]) <= set(rules_s12.CAPEX_BASIS_VOCAB)
    # 절단본 9법인은 집계 줄을 쓴다
    assert rows[SEC]["capex_basis"] == "standard"
    fields = {f.field_id: f for f in rules_s12.FIELDS_FIN}
    f = fields["financial.capex_basis"]
    assert f.columns == ("capex_basis",)
    assert f.value_type == "category" and f.unit == "" and f.scope == "internal"
    assert "capex_basis" in FIN_STD.columns


# ── F-A1·F-A2·F-A3: 합산 줄 · 이름 공백 정규화 · 취득 줄 부재 (2026-09-26 실측) ──

# F-A1 7사는 유형자산과 투자부동산을 **한 줄로** 적는다(정규화 이름 `유형자산및투자부동산의취득`·
# `투자부동산및유형자산의취득`, 유니버스 024110·030200 KT·000370·046890). 정의가 집계 줄(PPE)과
# 달라 basis 를 따로 둔다. F-A2 계정명 공백은 회사마다 임의라(CF 90,456행에 공백) 양쪽을 공백
# 제거 판으로 맞춘다. F-A3 현금흐름표는 있는데 유형자산 취득 줄이 아예 없는 92사 + 집계 줄이
# 값 공란인 9사는 "안 샀다" 는 뜻이므로 0 이다.

def test_capex_유형자산과_투자부동산을_한_줄로_적으면_기준이_다르다(make_stage_tree,
                                                                  tmp_path: Path) -> None:
    """합산 줄(F-A1) — 값은 채우고 `ppe_incl_invprop` 로 정의 차이를 표시한다.

    이름에 공백이 섞여 있어도 잡힌다(F-A2) — `유형자산 및 투자부동산의 취득`.
    """
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="ifrs-full_CashFlowsFromUsedInOperatingActivities",
                 account_nm="영업활동현금흐름", amount=9000.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="유형자산 및 투자부동산의 취득",
                 amount=500.0, ord_=2, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "ppe_incl_invprop"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert _num(row["capex_ytd"]) == Decimal("500")
    assert row["capex_basis"] == "ppe_incl_invprop"
    m = _gate(r, "EG3_fin_std").metrics
    assert m["n_capex_basis_outside_vocab"] == 0
    assert m["n_by_capex_basis"] == {"ppe_incl_invprop": 1}


def test_capex_자산별_줄이_합산_줄보다_앞선다(make_stage_tree, tmp_path: Path) -> None:
    """tier d(자산별) < tier e(합산) — 투자부동산이 섞인 줄은 PPE 만 있는 합에 진다."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_PurchaseOfLand", account_nm="토지의 취득",
                 amount=100.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_PurchaseOfMachinery", account_nm="기계장치의 취득",
                 amount=200.0, ord_=2),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="투자부동산 및 유형자산의 취득",
                 amount=500.0, ord_=3, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "ppe_parts"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert _num(row["capex_ytd"]) == Decimal("300")
    assert row["capex_basis"] == "ppe_parts"


def test_영업활동_현금흐름_이름은_공백을_무시한다(make_stage_tree, tmp_path: Path) -> None:
    """F-A2 — `영업활동으로 인한 순현금흐름`(146320 실측)이 공백 때문에 결측이었다."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=1000.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="영업활동으로 인한 순현금흐름",
                 amount=900.0, ord_=2, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "period_end_basis", "document"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    assert _num(rows["20260330000001"]["cf_operating_ytd"]) == Decimal("900")


def test_영업활동으로부터_창출된_현금흐름은_영업현금흐름이_아니다(make_stage_tree,
                                                              tmp_path: Path) -> None:
    """이자·법인세 **차감 전** 소계다 — 공백을 떼도 이 이름은 목록에 없다(F-A2 경계)."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=1000.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="영업활동으로부터 창출된 현금흐름",
                 amount=700.0, ord_=2, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "no_cf_statement"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert row["cf_operating_ytd"] is None
    # 현금흐름표 소계가 하나도 안 잡혔으므로 「표가 있다」 판정이 서지 않는다 → 0 규칙 밖
    assert row["capex_ytd"] is None
    assert row["capex_basis"] == "no_cf_statement"


def test_capex_현금흐름표에_유형자산_취득_줄이_없으면_0이다(make_stage_tree,
                                                          tmp_path: Path) -> None:
    """F-A3 — 리스·무형만 사고 유형자산은 안 산 회사(92사). 결측이 아니라 0 이다."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="ifrs-full_CashFlowsFromUsedInOperatingActivities",
                 account_nm="영업활동현금흐름", amount=900.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_AdditionsToRightofuseAssets",
                 account_nm="사용권자산의 취득", amount=300.0, ord_=2),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="무형자산의 취득", amount=200.0,
                 ord_=3, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "none_in_cf"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert _num(row["capex_ytd"]) == Decimal("0")
    assert row["capex_basis"] == "none_in_cf"
    m = _gate(r, "EG3_fin_std").metrics
    assert m["n_capex_basis_outside_vocab"] == 0
    assert m["n_by_capex_basis"] == {"none_in_cf": 1}


def test_capex_집계_줄이_값_공란이어도_0이다(make_stage_tree, tmp_path: Path) -> None:
    """F-A3 — 줄은 있는데 금액이 비었다(9사). 값 없는 줄은 「샀다」 는 증거가 아니다."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="ifrs-full_CashFlowsFromUsedInOperatingActivities",
                 account_nm="영업활동현금흐름", amount=900.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=CAPEX_AGG_ID, account_nm="유형자산의 취득",
                 amount=None, ord_=2),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "none_in_cf"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert _num(row["capex_ytd"]) == Decimal("0")
    assert row["capex_basis"] == "none_in_cf"


def test_capex_현금흐름표가_없으면_결측이다(make_stage_tree, tmp_path: Path) -> None:
    """0 규칙은 **현금흐름표가 있는** 그룹에만 선다 — 표가 없으면 안 샀다는 증거도 없다."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=1000.0, ord_=1),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "no_cf_statement"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert row["capex_ytd"] is None
    assert row["capex_basis"] == "no_cf_statement"


# ── F-A4: 부호 혼재 · 평이한 이름 전부 · 공백 정규화가 만든 pick 모호 (서버 재빌드 실측) ──

# m_20260926T130415 재빌드에서 나온 세 가지다.
# ① 같은 회사 한 표 안에서 표준 태그 줄은 +, 비표준 줄은 − 로 적힌다(00402989 FY2016: 건물 +34.79억
#    · 기계장치 +20.42 …)은 양수인데 비표준 줄(공구와 기구 −12.59 · 시설물 −9.11)은 음수다.
#    그래서 자산별 합은 **크기의 합**이다.
# ② 표준계정코드를 하나도 안 단 회사는 모든 자산 종류를 평이한 이름으로 적는다(00159971 FY2015:
#    기계장치의취득 −123.97 · 건설중자산의취득 −121.41 · 구축물의취득 −26.26 …).
# ③ 공백을 떼자 `영업활동으로인한 현금흐름` 과 `영업활동순현금흐름` 이 함께 걸려 pick 이 갈렸다
#    (00364795 FY2018 · 00926522 2019 2행이 값에서 NULL 로 퇴행). 공백 없는 원문이 이긴다.

def test_capex_자산별_줄은_부호가_섞여도_크기의_합이다(make_stage_tree, tmp_path: Path) -> None:
    """표준 태그 줄 +100 · 비표준 줄 −30 → 130. 부호가 아니라 **취득 규모**를 더한다."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="dart_PurchaseOfLand", account_nm="토지의 취득",
                 amount=100.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="공구와 기구의 취득", amount=-30.0,
                 ord_=2, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "ppe_parts"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert _num(row["capex_ytd"]) == Decimal("130")
    assert row["capex_basis"] == "ppe_parts"


def test_capex_표준태그_없는_회사의_평이한_이름도_전부_센다(make_stage_tree,
                                                          tmp_path: Path) -> None:
    """`기계장치의취득`·`토지의취득`·`건물의취득` — 자산 종류 × 접미어를 펼친 목록이 잡는다."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=1000.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="기계장치의취득", amount=-123.0,
                 ord_=2, account_std=False),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="토지의취득", amount=-24.0,
                 ord_=3, account_std=False),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="건물의취득", amount=-13.0,
                 ord_=4, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "ppe_parts"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert _num(row["capex_ytd"]) == Decimal("160")
    assert row["capex_basis"] == "ppe_parts"


def test_pick_은_공백_없는_원문을_먼저_고른다(make_stage_tree, tmp_path: Path) -> None:
    """공백 정규화 전에 쓰이던 값을 그대로 지킨다 — 공백 변형은 동점자일 때만 본다(F-A4)."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="IS",
                 account_id="ifrs-full_Revenue", account_nm="매출액", amount=1000.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="영업활동으로인한현금흐름", amount=83.0,
                 ord_=2, account_std=False),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="영업활동으로 인한 현금흐름", amount=-1230.0,
                 ord_=3, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "period_end_basis", "document"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    assert _num(rows["20260330000001"]["cf_operating_ytd"]) == Decimal("83")


# ── T-H 1단계: NULL 사유 어휘 · 예상 대상 기준 그룹별 커버리지 (2026-09-28) ────

# DQ-6(금융 템플릿 순이익 전건 NULL)·DQ-8(capex 311사 NULL)이 전 게이트를 통과한 이유는 분모가
# 「만들어진 행」이었기 때문이다. 여기서 보는 것은 둘이다: ① `unavailable` 한 라벨이 「표가 없다」
# 와 「표는 있는데 못 잡았다」 를 구별하는가 ② 그룹별 유효 비율이 기록되고 직전 판보다 무너지면
# 폐기되는가.

def test_capex_현금흐름표가_있는데_못_잡으면_unmapped_이다(make_stage_tree,
                                                          tmp_path: Path) -> None:
    """0 규칙의 분모 밖 — 값 있는 유형자산 취득 줄이 있는데 대응표가 못 잡은 그룹.

    `유형자산취득에 따른 현금유출` 은 목록에 없는 이름이지만 '유형자산'·'취득' 을 함께 갖고
    금액이 있어 `capex_zero` 가 그룹을 제외한다(안 산 것이 아니다). 그래서 값은 NULL 이고
    사유는 `no_cf_statement` 가 아니라 `unmapped` 다 — 고칠 대상이 대응표라는 뜻이다.
    """
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id="ifrs-full_CashFlowsFromUsedInOperatingActivities",
                 account_nm="영업활동현금흐름", amount=900.0, ord_=1),
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="CF",
                 account_id=NONSTD_ID, account_nm="유형자산취득에 따른 현금유출",
                 amount=300.0, ord_=2, account_std=False),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "capex_basis", "unmapped"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert row["capex_ytd"] is None
    assert row["capex_basis"] == "unmapped"
    m = _gate(r, "EG3_fin_std").metrics
    assert m["n_capex_basis_outside_vocab"] == 0
    assert m["n_by_capex_basis"] == {"unmapped": 1}


def test_손익계산서가_없으면_매출_사유는_no_is_statement_이다(make_stage_tree,
                                                            tmp_path: Path) -> None:
    """재무상태표만 실린 그룹 — 매출 규칙이 못 잡은 것이 아니라 **볼 표가 없었다**."""
    corps, reports = _capex_corp()
    fin = [
        _fin_row("00000001", "2025", "11011", "20260330000001", sj="BS",
                 account_id="ifrs-full_Assets", account_nm="자산총계", amount=500.0, ord_=1),
    ]
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    ("20260330000001", "revenue_basis", "no_is_statement"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    rows = {str(x["rcept_no"]): x for x in _rows(r.out_dir)}   # type: ignore[arg-type]
    row = rows["20260330000001"]
    assert row["revenue"] is None
    assert row["revenue_basis"] == "no_is_statement"
    assert row["capex_basis"] == "no_cf_statement"
    m = _gate(r, "EG3_fin_std").metrics
    assert m["n_revenue_basis_outside_vocab"] == 0
    assert m["n_revenue_basis_conflict"] == 0


def test_템플릿_요구_태그는_fin_map_과_sql_에서_유도된다() -> None:
    """템플릿 축(`banking`·`insurance`)의 태그는 `.sql` `req` CTE 와 같은 것이어야 한다."""
    sql = rules_s12.SQL_PATH.read_text(encoding="utf-8")
    assert rules_s12.TEMPLATE_TAGS == (("banking", "RevenueFromInterest"),
                                       ("insurance", "InvestmentIncome"))
    assert rules_s12.TEMPLATE_VOCAB == ("banking", "insurance", "standard")
    for _name, tag in rules_s12.TEMPLATE_TAGS:
        assert f"concept = '{tag}'" in sql, tag
    # 게이트가 stg_fin 에서 되풀이 계산할 때 쓰는 태그 접두어 정규화도 `.sql` 과 같은 판이다
    assert rules_s12.CONCEPT_PREFIX_PATTERN == "^(ifrs-full_|ifrs_|dart_)"
    assert f"'{rules_s12.CONCEPT_PREFIX_PATTERN}'" in sql


def test_커버리지가_예상_대상_기준으로_그룹별로_기록된다(built: build.BuildResult) -> None:
    """분모는 **그룹의 예상 대상**(채택 + 격리)이다 — 「만들어진 행」이 아니다."""
    g = _gate(built, "EG8_fin_std")
    assert g.status is GateStatus.PASS
    m = g.metrics
    total = m["coverage_total"]
    assert (total["n_expected"], total["n_adopted"]) == (N_SRC, N_OUT)
    assert set(total["metrics"]) == set(rules_s12.COVERAGE_METRICS)
    cov = m["coverage_by_group"]
    assert isinstance(cov, dict) and cov
    for key, entry in cov.items():
        fs_div, template, report_code = key.split("|")
        assert fs_div in rules_s12.FS_DIV_VOCAB
        assert template in rules_s12.TEMPLATE_VOCAB
        assert report_code in rules_s12.REPORT_CODE_VOCAB
        assert entry["n_expected"] >= m["coverage_min_expected"]
        assert set(entry["metrics"]) == set(rules_s12.COVERAGE_METRICS)
        for metric, cell in entry["metrics"].items():
            assert 0 <= cell["n_valid"] <= entry["n_adopted"], (key, metric)
            assert cell["ratio"] == round(cell["n_valid"] / entry["n_expected"], 6)
    # 절단본 실측 그룹 12 — CFS·OFS × 보고서 종류 넷 8 개에 **은행 템플릿 1법인**(이자수익
    # 태그, CFS 4 종류 × 3행)이 더해진다. 그중 예상 대상 20 이상은 CFS·제조업 넷뿐이고
    # 나머지 8(OFS 28행 · banking 12행)은 표본이 작아 기록하지 않는다.
    assert (m["n_groups"], m["n_groups_recorded"]) == (12, 4)
    assert {k: v["n_expected"] for k, v in cov.items()} == {
        "CFS|standard|11011": 48, "CFS|standard|11012": 47,
        "CFS|standard|11013": 49, "CFS|standard|11014": 42}
    # 격리 6(non_krw)은 전부 예상 대상 분모에 들어 있다 — 값이 나와야 했던 대상이 사라진 것도
    # 커버리지 손실이기 때문이다
    assert sum(v["n_expected"] - v["n_adopted"] for v in cov.values()) == N_REJECT
    # 직전 판이 없으므로 기록만 한다
    assert m["coverage_compared"] is False
    assert m["coverage_fail"] == [] and m["coverage_warn"] == []


def _banking_tree(n: int) -> tuple[list[tuple[str, str | None]],
                                   list[tuple[str, str, str, str, date, date | None,
                                              str | None]],
                                   list[dict[str, object]]]:
    """은행 템플릿(이자수익 태그) 법인 n 개 — 순이익 계정은 **하나도 없다**(DQ-6 재현)."""
    corps = [(f"{i + 1:08d}", "12") for i in range(n)]
    reports = [(f"202603300{i + 1:05d}", c, "2025", "11011", date(2026, 3, 30),
                date(2025, 12, 31), "11011") for i, (c, _) in enumerate(corps)]
    fin = [_fin_row(c, "2025", "11011", f"202603300{i + 1:05d}", sj="IS",
                    account_id="ifrs-full_RevenueFromInterest", account_nm="이자수익",
                    amount=700.0 + i)
           for i, (c, _) in enumerate(corps)]
    return corps, reports, fin


_BANKING_GROUP = "CFS|banking|11011"
_BANKING_N = 21                      # 기록·판정 하한 20 을 넘기는 최소 크기


def _previous_coverage(equity_root: Path, coverage: dict[str, object]) -> None:
    """직전 커밋 빌드를 손으로 심는다 — 게이트가 판 사이를 견주는 유일한 통로다."""
    root = equity_root / FIN_STD.name
    root.mkdir(parents=True, exist_ok=True)
    stage_manifest.commit(root, stage_manifest.BuildRecord(
        build_id="b_hand_fin_prev", snapshot_id="", rules_version="e0.0.0-prev",
        built_at_utc="2026-09-27T00:00:00+00:00", n_rows=_BANKING_N, content_hash="0:prev",
        partitions=[], gates=[{"name": "EG8_fin_std", "status": "pass", "detail": "",
                               "metrics": {"coverage_by_group": coverage}}]))


def test_직전_판보다_순이익_커버리지가_무너지면_폐기한다(make_stage_tree,
                                                      tmp_path: Path) -> None:
    """DQ-6 재현 — 은행 템플릿의 순이익이 전건 NULL 이 되는 판은 통과하면 안 된다.

    직전 판에서 0.9 이던 그룹이 0.0 이 되면 낙폭 0.9 ≥ 0.5 이고 0 이기도 하다. 경보에는
    영향 건수와 대표 사례 3건이 같이 실린다(플랜 §11 공통 원칙).
    """
    corps, reports, fin = _banking_tree(_BANKING_N)
    _previous_coverage(tmp_path / "equity", {
        _BANKING_GROUP: {"n_expected": _BANKING_N, "n_adopted": _BANKING_N,
                         "metrics": {"net_income": {"n_valid": 19, "ratio": 0.9},
                                     "revenue": {"n_valid": _BANKING_N, "ratio": 1.0}}}})
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    (reports[0][0], "revenue_basis", "banking_gross"))
    assert r.status is build.BuildStatus.GATE_FAILED
    g = _gate(r, "EG8_fin_std")
    assert g.status is GateStatus.FAIL
    assert g.metrics["coverage_compared"] is True
    assert g.metrics["previous_build"] == "b_hand_fin_prev"
    fails = g.metrics["coverage_fail"]
    assert [(e["group"], e["metric"]) for e in fails] == [(_BANKING_GROUP, "net_income")]
    e = fails[0]
    assert (e["ratio_before"], e["ratio_after"]) == (0.9, 0.0)
    assert (e["n_expected"], e["n_valid"]) == (_BANKING_N, 0)
    assert e["n_affected_est"] == 19
    assert len(e["examples"]) == 3
    for key in e["examples"]:
        assert key.endswith("|2025-12-31|11011|CFS")
    # 매출은 그대로라 경보도 안 난다
    assert g.metrics["coverage_warn"] == []
    # 뒤 게이트는 돌지 않는다(첫 FAIL 이후 skip 규약)
    assert {x.name: x.status.value for x in r.gates if x.name in ("EG6_fin_std", "EG4")} == {
        "EG6_fin_std": "skip", "EG4": "skip"}


def test_직전_판이_없으면_같은_판이_기록만_하고_통과한다(make_stage_tree,
                                                      tmp_path: Path) -> None:
    """같은 데이터라도 견줄 판이 없으면 판정하지 않는다 — 첫 빌드를 폐기하면 못 짓는다."""
    corps, reports, fin = _banking_tree(_BANKING_N)
    r = _hand_build(make_stage_tree, tmp_path, corps, reports, fin,
                    (reports[0][0], "revenue_basis", "banking_gross"))
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    g = _gate(r, "EG8_fin_std")
    assert g.status is GateStatus.PASS
    assert g.metrics["coverage_compared"] is False
    assert g.metrics["coverage_fail"] == []
    entry = g.metrics["coverage_by_group"][_BANKING_GROUP]
    assert entry["n_expected"] == _BANKING_N
    assert entry["metrics"]["net_income"] == {"n_valid": 0, "ratio": 0.0}
    assert entry["metrics"]["revenue"] == {"n_valid": _BANKING_N, "ratio": 1.0}
