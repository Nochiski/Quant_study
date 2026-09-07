"""S15 지분·감사 3테이블 — 절단본 위 실제 체인 왕복 + 손 트리 부정 픽스처.

체인: `trading_calendar` → `security` → `security_span` → `corp` → `corp_ticker`
→ `disclosure_version` → `holder_daily` · `ownership_snapshot` · `audit_opinion`.
S15 세 테이블이 직접 읽는 앞선 산출은 `corp` 뿐이지만(법인 대조), 4B 가 1단계·4A 와 같은
카탈로그 위에 서는지는 체인 전체를 세워야 확인된다(WORKFLOW §3-1 S15 선행 = S01).

절단본 실측(법인 11개):
  `holder_daily` **4,329행 · 격리 0** = elestock 4,197 + majorstock 132(둘 다 (rcept_no,
  repror) 유일). 접수연도 2024/188 · 2025/1,152 · 2026/2,989 — elestock 롤링 2년 창
  2024-08-26 ~ 2026-08-26.
  `ownership_snapshot` **826행 · 격리 0** = `stg_hyslr` 995 − 집계행 169. 접수연도 2016~2026.
    이름 축만으로는 733개로 접힌다(충돌 93행) → grain 에 `stock_knd` 를 넣었다.
  `audit_opinion` **226행 · 격리 0** = `stg_audit` 291 → grain 226(접힘 65, 52그룹).
    접힌 그룹의 의견은 갈리지 않는다(`n_class_conflict_grain` 0), 감사인은 8그룹에서 갈린다.
    등급 적정 207 · 의견거절 4 · 한정 2 · NULL 13.

손 트리로만 낼 수 있는 것: 재수집 판본 접힘 · 접수일 결측 격리 · 지분율 범위 밖 격리 ·
집계행 배제 · 같은 이름의 종류주 2행 유지 · 같은 grain 의 의견 충돌 감시 · 부적정 선매칭.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest
from equity import build, rules_s01, rules_s02, rules_s11, rules_s15
from equity.baseline import Baseline, load
from equity.gates import GateStatus

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
HOLDER = rules_s15.HOLDER_DAILY
OWNERSHIP = rules_s15.OWNERSHIP_SNAPSHOT
AUDIT = rules_s15.AUDIT_OPINION

CHAIN = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
         rules_s01.CORP, rules_s01.CORP_TICKER, rules_s11.DISCLOSURE_VERSION)

N_OUT = {"holder_daily": 4329, "ownership_snapshot": 826, "audit_opinion": 226}
GATE_ORDER = {
    "holder_daily": ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_holder_daily", "EG4", "EG5a"],
    "ownership_snapshot": ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_ownership_snapshot",
                           "EG4", "EG5a"],
    "audit_opinion": ["EG0", "EG7", "EG1", "EG2", "EG3", "EG3_audit_opinion", "EG4", "EG5a"],
}
OBSERVED = date(2026, 9, 1)          # 손 트리의 재수집 관측일(판본 선택 축)


def _seed() -> Baseline:
    merged: dict[str, object] = {}
    seeds = Path(rules_s15.__file__).parent
    for p in (rules_s01.BASELINE_SEED, seeds / "baseline_seed_s02.json",
              rules_s11.BASELINE_SEED, rules_s15.BASELINE_SEED):
        merged.update({k: v for k, v in load(p).data.items()
                       if not k.startswith("_") and k != "measured_at"})
    return Baseline(merged)


def _with(bl: Baseline, table: str, **kw: object) -> Baseline:
    return Baseline({**bl.data, table: {**bl.table(table), **kw}})


def _rows(out_dir: Path, order: str) -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        rel = con.execute("SELECT * EXCLUDE (v) FROM "
                          f"read_parquet('{out_dir / 'year=*' / '*.parquet'}', "
                          f"hive_partitioning=true) ORDER BY {order}")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _rejects(out_dir: Path, cols: str) -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        rel = con.execute(f"SELECT {cols}, reject_reason FROM "
                          f"read_parquet('{out_dir / '_reject' / '*' / '*.parquet'}', "
                          "hive_partitioning=true) ORDER BY 1")
        names = [d[0] for d in rel.description]
        return [dict(zip(names, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _gate(r: build.BuildResult, name: str):
    return next(g for g in r.gates if g.name == name)


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> dict[str, build.BuildResult]:
    """체인 6테이블 + S15 3테이블을 절단본 위에 실제로 빌드한다."""
    root = tmp_path_factory.mktemp("s15") / "equity"
    bl = _seed()
    for rule in CHAIN:
        r = build.build_table(rule, STAGE_SLICE, root, bl, build_id=f"b_{rule.name}")
        assert r.ok, (rule.name, [(g.name, g.status.value, g.detail) for g in r.gates])
    return {rule.name: build.build_table(rule, STAGE_SLICE, root, bl,
                                         build_id=f"b_s15_{rule.name}")
            for rule in rules_s15.TABLES}


@pytest.fixture(scope="module")
def holder_rows(chain: dict[str, build.BuildResult]) -> dict[tuple[str, str, str], dict]:
    built = chain["holder_daily"]
    assert built.out_dir is not None
    return {(str(r["rcept_no"]), str(r["repror"]), str(r["src"])): r
            for r in _rows(built.out_dir, "rcept_no, repror, src")}


@pytest.fixture(scope="module")
def ownership_rows(chain: dict[str, build.BuildResult]) -> dict[tuple[str, ...], dict]:
    built = chain["ownership_snapshot"]
    assert built.out_dir is not None
    return {(str(r["corp_code"]), str(r["bsns_year"]), str(r["nm"]), str(r["stock_knd"])): r
            for r in _rows(built.out_dir, "corp_code, bsns_year, nm, stock_knd")}


@pytest.fixture(scope="module")
def audit_rows(chain: dict[str, build.BuildResult]) -> dict[tuple[str, ...], dict]:
    built = chain["audit_opinion"]
    assert built.out_dir is not None
    return {(str(r["corp_code"]), str(r["bsns_year"]), str(r["bsns_year_label"])): r
            for r in _rows(built.out_dir, "corp_code, bsns_year, bsns_year_label")}


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["holder_daily", "ownership_snapshot", "audit_opinion"])
def test_절단본_체인_빌드가_전_게이트를_통과한다(chain: dict[str, build.BuildResult],
                                                 name: str) -> None:
    built = chain[name]
    assert built.ok, [(g.name, g.status.value, g.detail) for g in built.gates]
    assert [g.name for g in built.gates] == GATE_ORDER[name]
    assert {g.name: g.status.value for g in built.gates if g.name != "EG5a"} == {
        n: "pass" for n in GATE_ORDER[name] if n != "EG5a"}
    assert (built.n_rows, built.n_reject) == (N_OUT[name], 0)


@pytest.mark.parametrize(("name", "rhs"), [("holder_daily", 4329),
                                           ("ownership_snapshot", 826),
                                           ("audit_opinion", 226)])
def test_EG1_등식은_자연키_distinct_우변과_선다(chain: dict[str, build.BuildResult],
                                                name: str, rhs: int) -> None:
    m = _gate(chain[name], "EG1").metrics
    assert (m["lhs"], m["rhs"], m["n_reject"], m["delta"]) == (N_OUT[name], rhs, 0, 0)


@pytest.mark.parametrize("name", ["holder_daily", "ownership_snapshot", "audit_opinion"])
def test_같은_inputs_재빌드는_파티션_해시가_같다(chain: dict[str, build.BuildResult],
                                                 name: str) -> None:
    rule = {r.name: r for r in rules_s15.TABLES}[name]
    root = chain[name].out_dir.parent.parent          # type: ignore[union-attr]
    again = build.build_table(rule, STAGE_SLICE, root, _seed(), build_id=f"b_s15_{name}_2")
    assert again.ok, [(g.name, g.status.value, g.detail) for g in again.gates]
    eg5 = _gate(again, "EG5a")
    assert eg5.status is GateStatus.PASS and eg5.metrics["n_changed_partitions"] == 0


@pytest.mark.parametrize(("name", "years"), [("holder_daily", ("year=2024", "year=2026")),
                                             ("ownership_snapshot", ("year=2016", "year=2026")),
                                             ("audit_opinion", ("year=2016", "year=2026"))])
def test_파티션은_접수연도_축이다(chain: dict[str, build.BuildResult], name: str,
                                  years: tuple[str, str]) -> None:
    out_dir = chain[name].out_dir
    assert out_dir is not None
    got = sorted(p.name for p in out_dir.iterdir() if p.name.startswith("year="))
    assert (got[0], got[-1]) == years
    con = duckdb.connect()
    try:
        n = con.execute("SELECT count(*) FROM read_parquet("
                        f"'{out_dir / 'year=*' / '*.parquet'}', hive_partitioning=true) "
                        "WHERE year <> CAST(substr(rcept_no, 1, 4) AS INTEGER)").fetchone()
        assert n is not None and int(str(n[0])) == 0
    finally:
        con.close()


def test_holder_두_원천의_행수와_왕복(chain: dict[str, build.BuildResult]) -> None:
    m = _gate(chain["holder_daily"], "EG3_holder_daily").metrics
    assert m["n_by_src"] == {"elestock": 4197, "majorstock": 132}
    assert m["n_src_roundtrip_miss"] == 0
    assert m["n_exclusive_column_leak"] == 0
    assert m["n_stage_dup_natural_key"] == 0 and m["n_source_rows_gt1"] == 0
    assert m["n_corp_unmatched"] == 0
    # elestock 은 롤링 2년 창이라 재수집이 불가능하다(DART_DESIGN P3e) — 창 경계를 기록한다
    assert (m["rcept_dt_min"], m["rcept_dt_max"]) == ("2024-08-26", "2026-08-26")
    assert (m["rate_pct_min"], m["rate_pct_max"]) == (0.0, 100.0)
    assert m["n_rate_above_pct_max"] == 0


def test_holder_지분_전은_후에서_증감을_뺀_값이다(
        holder_rows: dict[tuple[str, str, str], dict]) -> None:
    r = holder_rows[("20240911000273", "콜마홀딩스", "majorstock")]
    assert (str(r["rate_pct"]), str(r["rate_change_pct"]), str(r["rate_prev_pct"])) == (
        "26.57000000000", "-0.25000000000", "26.82000000000")
    assert (str(r["qty_shr"]), str(r["qty_prev_shr"])) == ("6269759", "6329459")
    # 원천 전용 컬럼은 반대편에서 NULL 이다
    assert r["report_tp"] == "일반" and r["ofcps"] is None
    e = holder_rows[("20240826000444", "박태훈", "elestock")]
    assert e["ofcps"] == "상무" and e["report_tp"] is None and e["ctr_qty_shr"] is None
    assert str(e["qty_prev_shr"]) == "808"


def test_ownership_집계행은_모집단_밖이고_종류주는_두_행이다(
        chain: dict[str, build.BuildResult],
        ownership_rows: dict[tuple[str, ...], dict]) -> None:
    m = _gate(chain["ownership_snapshot"], "EG3_ownership_snapshot").metrics
    assert (m["n_stage_rows"], m["n_stage_aggregate_rows"], m["n_stage_detail_rows"]) == (
        995, 169, 826)
    assert m["n_aggregate_leaked"] == 0 and m["n_grain_folded"] == 0
    assert m["n_by_stock_knd"]["보통주"] == 661 and m["n_by_stock_knd"]["우선주"] == 86
    common = ownership_rows[("00110893", "2015", "양홍석", "보통주")]
    pref = ownership_rows[("00110893", "2015", "양홍석", "우선주")]
    assert (str(common["trmend_rate_pct"]), str(pref["trmend_rate_pct"])) == ("6.92", "0.00")
    # 지분율이 0.00 으로 반올림돼도 수량은 남는다 — 이름 축으로 접으면 사라지는 값이다
    assert str(pref["trmend_qty_shr"]) == "130"
    assert common["relate"] == "본인" and common["stlm_dt"] == date(2015, 12, 31)


def test_ownership_집계행_대조는_기록형이다(chain: dict[str, build.BuildResult]) -> None:
    m = _gate(chain["ownership_snapshot"], "EG3_ownership_snapshot").metrics
    # 산출(집계 제외)의 보통주 지분율 합 vs 원장 '계' 행 — 원장 소수 2자리 반올림이 쌓인다
    assert (m["agg_reconcile_n"], m["agg_reconcile_n_close"]) == (76, 75)
    assert m["agg_rate_tol_pct"] == 0.5


def test_audit_grain_접힘과_의견_충돌_감시(chain: dict[str, build.BuildResult]) -> None:
    m = _gate(chain["audit_opinion"], "EG3_audit_opinion").metrics
    assert (m["n_stage_rows"], m["n_grain_folded"]) == (291, 65)
    assert (m["n_source_rows_gt1"], m["n_source_rows_max"]) == (52, 6)
    # 접힌 행들의 의견·등급은 갈리지 않는다 → 어느 행을 남겨도 의견은 같다
    assert m["n_class_conflict_grain"] == 0 and m["n_opinion_conflict_grain"] == 0
    # 감사인은 8그룹에서 갈린다(연결/별도 감사보고서) — 기록만 한다
    assert m["n_adtor_conflict_grain"] == 8
    assert m["n_class_recomputed_mismatch"] == 0
    assert m["n_by_class"] == {"None": 13, "의견거절": 4, "적정": 207, "한정": 2}


def test_audit_한_접수의_세_기수가_각각_남는다(
        audit_rows: dict[tuple[str, ...], dict]) -> None:
    cur = audit_rows[("00450931", "2015", "제20기(당기)")]
    prev = audit_rows[("00450931", "2015", "제19기(전기)")]
    prev2 = audit_rows[("00450931", "2015", "제18기(전전기)")]
    assert cur["rcept_no"] == prev["rcept_no"] == prev2["rcept_no"] == "20160525000313"
    assert (cur["adt_opinion_class"], prev["adt_opinion_class"],
            prev2["adt_opinion_class"]) == ("한정", "의견거절", "적정")
    assert (cur["adtor"], prev["adtor"]) == ("다산회계법인", "신아회계법인")


@pytest.mark.parametrize("name", ["holder_daily", "ownership_snapshot", "audit_opinion"])
def test_PIT_축은_접수일이고_basis_는_derived(chain: dict[str, build.BuildResult],
                                              name: str) -> None:
    m = _gate(chain[name], "EG2").metrics
    assert (m["n_available_null"], m["n_basis_outside_vocab"],
            m["n_available_before_content"]) == (0, 0, 0)
    assert m["basis_vocab"] == ["derived"]


# ── 선언 ↔ SQL 대조 ──────────────────────────────────────────────────────────

def test_어휘가_sql_리터럴과_같다() -> None:
    holder = rules_s15.HOLDER_SQL.read_text(encoding="utf-8")
    for s in rules_s15.SRC_VOCAB:
        assert f"'{s}'" in holder, s
    own = rules_s15.OWNERSHIP_SQL.read_text(encoding="utf-8")
    assert "'aggregate'" in own and "c.pct_min" in own and "c.pct_max" in own
    # 집계 어휘는 stage `_row_kind` 가 정본이고 게이트가 그것으로 독립 재판정한다
    assert rules_s15.AGGREGATE_LABELS == ("합계", "계", "총계", "소계")


def test_선언_grain_격리어휘_입력() -> None:
    assert HOLDER.grain == ("rcept_no", "repror", "src")
    assert HOLDER.reject_reasons == ("rcept_dt_missing",)
    assert HOLDER.inputs == ("stg_holder_elestock", "stg_holder_majorstock", "corp")
    assert HOLDER.consts == ()
    # DESIGN §4-5 초안 grain 에 `stock_knd` 를 더했다(GATES §9 S15 정정)
    assert OWNERSHIP.grain == ("corp_code", "bsns_year", "reprt_code", "nm", "stock_knd")
    assert OWNERSHIP.reject_reasons == ("rcept_dt_missing", "pct_out_of_range")
    assert OWNERSHIP.consts == ("pct_min", "pct_max")
    assert AUDIT.grain == ("corp_code", "bsns_year", "reprt_code", "bsns_year_label")
    assert AUDIT.reject_reasons == ("rcept_dt_missing",)
    for rule in rules_s15.TABLES:
        assert rule.partition_class == "receipt_axis"
        assert rule.available_basis == ("derived",)
        assert "corp" in rule.inputs


def test_EG1_우변은_자연키_distinct_다() -> None:
    """초안(GATES §3-⑮⑰)의 stage 행수 1:1 은 성립하지 않는다 — §9 S15 정정."""
    assert "DISTINCT rcept_no, repror" in rules_s15.HOLDER_EG1_RHS
    assert "count(*) FROM stg_holder_elestock" not in rules_s15.HOLDER_EG1_RHS
    assert "DISTINCT corp_code, bsns_year, reprt_code, nm, stock_knd" in (
        rules_s15.OWNERSHIP_EG1_RHS)
    assert "row_kind IS DISTINCT FROM 'aggregate'" in rules_s15.OWNERSHIP_EG1_RHS
    assert "DISTINCT corp_code, bsns_year, reprt_code, bsns_year_label" in (
        rules_s15.AUDIT_EG1_RHS)


@pytest.mark.parametrize(("name", "keys", "n_min"), [
    ("holder_daily", {"rcept_no", "repror", "src"}, 10),
    ("ownership_snapshot", {"corp_code", "bsns_year", "reprt_code", "nm", "stock_knd"}, 8),
    ("audit_opinion", {"corp_code", "bsns_year", "reprt_code", "bsns_year_label"}, 10)])
def test_픽스처는_전부_positive_이고_키가_grain_이다(chain: dict[str, build.BuildResult],
                                                    name: str, keys: set[str],
                                                    n_min: int) -> None:
    fx = json.loads((Path(rules_s15.__file__).parent / "fixtures"
                     / f"{name}.json").read_text(encoding="utf-8"))
    assert len(fx) >= n_min
    assert {f["fixture_class"] for f in fx} == {"positive"}
    assert all(set(f["key"]) == keys for f in fx)
    assert _gate(chain[name], "EG4").metrics["n_fixtures"] == len(fx)


# ── 손 트리 부정 픽스처 ───────────────────────────────────────────────────────

def _corp_tree(make_stage_tree, tmp_path: Path, corps: tuple[str, ...]) -> Path:
    tree = make_stage_tree(tmp_path, "stg_corp_map",
                           [{"corp_code": c, "ticker": f"00000{i}", "corp_name_current": c}
                            for i, c in enumerate(corps)])
    make_stage_tree(tmp_path, "stg_company",
                    [{"corp_code": c, "acc_mt": "12", "induty_code_current": "26",
                      "observed_date": date(2026, 1, 1)} for c in corps])
    return tree.stage_root


def _fixture_file(tmp_path: Path, name: str, key: dict[str, object], column: str,
                  expect: object) -> Path:
    """손 트리에는 절단본 골든 픽스처를 못 쓴다 — EG4 는 픽스처 부재가 실패라 1건을 깐다."""
    path = tmp_path / f"fx_{name}.json"
    path.write_text(json.dumps(
        [{"case": "hand", "key": key, "column": column, "expect": expect,
          "source": "hand — 손 트리 부정 픽스처의 EG4 자리를 채운다"}], ensure_ascii=False),
        encoding="utf-8")
    return path


def _build_hand(rule, stage_root: Path, tmp_path: Path, key: dict[str, object], column: str,
                expect: object) -> build.BuildResult:
    equity_root = tmp_path / "equity"
    bl = _seed()
    corp = build.build_table(
        rules_s01.CORP, stage_root, equity_root, bl, build_id="b_hand_corp",
        fixtures_path=_fixture_file(tmp_path, "corp", {"corp_code": "00000001"},
                                    "corp_code", "00000001"))
    assert corp.ok, [(g.name, g.status.value, g.detail) for g in corp.gates]
    return build.build_table(
        rule, stage_root, equity_root, bl, build_id=f"b_hand_{rule.name}",
        fixtures_path=_fixture_file(tmp_path, rule.name, key, column, expect),
        gate_thresholds={"EG7": 1.0})


def _elestock(rcept_no: str, repror: str, rcept_dt: date | None, corp: str = "00000001",
              qty: float = 1000.0, qty_irds: float = 100.0, rate: float = 1.5,
              rate_irds: float = 0.5, observed: date = OBSERVED) -> dict[str, object]:
    return {"rcept_no": rcept_no, "repror": repror, "rcept_dt": rcept_dt, "corp_code": corp,
            "isu_exctv_ofcps": "상무", "isu_exctv_rgist_at": "비등기임원",
            "isu_main_shrholdr": "-", "sp_stock_lmp_cnt_shr": qty,
            "sp_stock_lmp_irds_cnt_shr": qty_irds, "sp_stock_lmp_rate_pct": rate,
            "sp_stock_lmp_irds_rate_pct": rate_irds, "observed_date": observed}


def _majorstock(rcept_no: str, repror: str, rcept_dt: date,
                corp: str = "00000001") -> dict[str, object]:
    return {"rcept_no": rcept_no, "repror": repror, "rcept_dt": rcept_dt, "corp_code": corp,
            "report_tp": "일반", "report_resn": "장내매수", "stkqy_shr": 5000.0,
            "stkqy_irds_shr": 500.0, "stkrt_pct": 6.0, "stkrt_irds_pct": 0.6,
            "ctr_stkqy_shr": 0.0, "ctr_stkrt_pct": 0.0, "observed_date": OBSERVED}


def _hyslr(rcept_no: str, nm: str, stock_knd: str, rate: float, *, row_kind: str = "detail",
           corp: str = "00000001", year: str = "2020", row_hash: str = "h0",
           observed: date = OBSERVED, available: date | None = date(2021, 3, 30),
           ) -> dict[str, object]:
    return {"row_hash": row_hash, "corp_code": corp, "bsns_year": year, "reprt_code": "11011",
            "nm": nm, "stock_knd": stock_knd, "rcept_no": rcept_no, "relate": "본인",
            "stlm_dt": date(2020, 12, 31), "bsis_posesn_stock_co_shr": 100.0,
            "bsis_posesn_stock_qota_rt_pct": rate, "trmend_posesn_stock_co_shr": 100.0,
            "trmend_posesn_stock_qota_rt_pct": rate, "rm": "-", "row_kind": row_kind,
            "available_date": available, "observed_date": observed}


def _audit(rcept_no: str, label: str, opinion: str | None, *, adtor: str = "삼일회계법인",
           opinion_class: str | None = "적정", corp: str = "00000001", year: str = "2020",
           row_hash: str = "h0", available: date | None = date(2021, 3, 30),
           ) -> dict[str, object]:
    return {"row_hash": row_hash, "corp_code": corp, "bsns_year": year, "reprt_code": "11011",
            "bsns_year_label": label, "rcept_no": rcept_no, "stlm_dt": date(2020, 12, 31),
            "adtor": adtor, "adt_opinion": opinion, "adt_reprt_spcmnt_matter": "",
            "emphs_matter": "", "core_adt_matter": "", "adt_opinion_class": opinion_class,
            "available_date": available, "observed_date": OBSERVED}


def test_부정_holder_재수집_판본이_둘이면_한_행만_낸다(make_stage_tree,
                                                        tmp_path: Path) -> None:
    """`stg_holder_*` 는 append_only·key_unique=False 라 같은 (rcept_no, repror) 가 쌓인다.

    안 접으면 grain 이 깨지고(EG3-P01) EG1 좌변이 우변보다 커진다 — S11 서버 1차 빌드가
    같은 함정으로 delta −26 을 냈다.
    """
    stage_root = _corp_tree(make_stage_tree, tmp_path, ("00000001",))
    make_stage_tree(tmp_path, "stg_holder_elestock", [
        _elestock("20250101000001", "김철수", date(2025, 1, 2), observed=date(2026, 8, 1)),
        _elestock("20250101000001", "김철수", date(2025, 1, 2), observed=date(2026, 9, 1)),
        _elestock("20250101000002", "이영희", date(2025, 1, 3)),
    ], partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_holder_majorstock",
                    [_majorstock("20250101000003", "국민연금공단", date(2025, 1, 4))],
                    partition_class="receipt_axis")
    r = _build_hand(HOLDER, stage_root, tmp_path,
                    {"rcept_no": "20250101000001", "repror": "김철수", "src": "elestock"},
                    "n_source_rows", "2")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (3, 0)
    assert r.out_dir is not None
    rows = {(str(x["rcept_no"]), str(x["src"])): x
            for x in _rows(r.out_dir, "rcept_no, repror, src")}
    assert rows[("20250101000001", "elestock")]["n_source_rows"] == 2
    m = _gate(r, "EG1").metrics
    assert (m["lhs"], m["rhs"], m["delta"]) == (3, 3, 0)
    assert _gate(r, "EG3_holder_daily").metrics["n_stage_dup_natural_key"] == 1


def test_부정_holder_접수일_결측은_격리된다(make_stage_tree, tmp_path: Path) -> None:
    stage_root = _corp_tree(make_stage_tree, tmp_path, ("00000001",))
    make_stage_tree(tmp_path, "stg_holder_elestock", [
        _elestock("20250101000001", "김철수", date(2025, 1, 2)),
        _elestock("20250101000002", "이영희", None),
    ], partition_class="receipt_axis")
    make_stage_tree(tmp_path, "stg_holder_majorstock",
                    [_majorstock("20250101000003", "국민연금공단", date(2025, 1, 4))],
                    partition_class="receipt_axis")
    r = _build_hand(HOLDER, stage_root, tmp_path,
                    {"rcept_no": "20250101000001", "repror": "김철수", "src": "elestock"},
                    "available_basis", "derived")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (2, 1)
    assert _rejects(r.out_dir, "rcept_no, repror") == [                # type: ignore[arg-type]
        {"rcept_no": "20250101000002", "repror": "이영희",
         "reject_reason": "rcept_dt_missing"}]
    m = _gate(r, "EG1").metrics
    assert (m["lhs"], m["rhs"], m["n_reject"], m["delta"]) == (2, 3, 1, 0)


def test_부정_ownership_집계행은_모집단_밖이고_종류주는_남는다(make_stage_tree,
                                                              tmp_path: Path) -> None:
    """`nm='계'` 집계행을 실으면 무필터 SUM 이 2중 계상된다 — 모집단에서 뺀다.

    같은 이름의 보통주·우선주는 **둘 다** 남아야 한다(DESIGN §4-5 초안 grain 정정).
    """
    stage_root = _corp_tree(make_stage_tree, tmp_path, ("00000001",))
    make_stage_tree(tmp_path, "stg_hyslr", [
        _hyslr("20210330000001", "양홍석", "보통주", 6.92, row_hash="h1"),
        _hyslr("20210330000001", "양홍석", "우선주", 0.0, row_hash="h2"),
        _hyslr("20210330000001", "계", "보통주", 6.92, row_kind="aggregate", row_hash="h3"),
    ], partition_class="receipt_axis")
    r = _build_hand(OWNERSHIP, stage_root, tmp_path,
                    {"corp_code": "00000001", "bsns_year": "2020", "reprt_code": "11011",
                     "nm": "양홍석", "stock_knd": "우선주"}, "relate", "본인")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (2, 0)
    rows = {str(x["stock_knd"]) for x in _rows(r.out_dir,                # type: ignore[arg-type]
                                               "stock_knd")}
    assert rows == {"보통주", "우선주"}
    m = _gate(r, "EG3_ownership_snapshot").metrics
    assert (m["n_stage_rows"], m["n_stage_aggregate_rows"], m["n_aggregate_leaked"]) == (3, 1, 0)
    assert _gate(r, "EG1").metrics["rhs"] == 2


def test_부정_ownership_지분율이_범위_밖이면_격리된다(make_stage_tree,
                                                      tmp_path: Path) -> None:
    stage_root = _corp_tree(make_stage_tree, tmp_path, ("00000001",))
    make_stage_tree(tmp_path, "stg_hyslr", [
        _hyslr("20210330000001", "정상주주", "보통주", 100.0, row_hash="h1"),
        _hyslr("20210330000001", "오버플로", "보통주", 100.01, row_hash="h2"),
        _hyslr("20210330000001", "음수", "보통주", -1.0, row_hash="h3"),
    ], partition_class="receipt_axis")
    r = _build_hand(OWNERSHIP, stage_root, tmp_path,
                    {"corp_code": "00000001", "bsns_year": "2020", "reprt_code": "11011",
                     "nm": "정상주주", "stock_knd": "보통주"}, "relate", "본인")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    # 상한 100 은 포함 — 경계값은 격리하지 않는다
    assert (r.n_rows, r.n_reject) == (1, 2)
    assert [x["reject_reason"] for x in _rejects(r.out_dir, "nm")] == [   # type: ignore[arg-type]
        "pct_out_of_range", "pct_out_of_range"]
    assert _gate(r, "EG3_ownership_snapshot").metrics["n_pct_out_of_range_kept"] == 0


def test_부정_ownership_접수일_결측은_격리된다(make_stage_tree, tmp_path: Path) -> None:
    stage_root = _corp_tree(make_stage_tree, tmp_path, ("00000001",))
    make_stage_tree(tmp_path, "stg_hyslr", [
        _hyslr("20210330000001", "정상주주", "보통주", 10.0, row_hash="h1"),
        _hyslr("20210330000002", "접수일없음", "보통주", 10.0, row_hash="h2", available=None),
    ], partition_class="receipt_axis")
    r = _build_hand(OWNERSHIP, stage_root, tmp_path,
                    {"corp_code": "00000001", "bsns_year": "2020", "reprt_code": "11011",
                     "nm": "정상주주", "stock_knd": "보통주"}, "available_basis", "derived")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert [x["reject_reason"] for x in _rejects(r.out_dir, "nm")] == [   # type: ignore[arg-type]
        "rcept_dt_missing"]


def test_부정_audit_같은_grain_의_의견이_갈리면_게이트가_잡는다(make_stage_tree,
                                                              tmp_path: Path) -> None:
    """grain 이 유일하지 않은 원장을 접어야 하므로, **접힌 행의 의견이 갈리는지**를 본다.

    갈리면 산출은 한 의견만 남기고 나머지는 사라진다 — 폐기하지 않고 기록형으로 남기는 이유는
    서버 전량에서 이 값이 0 인지 아직 모르기 때문이다(절단본 0).
    """
    stage_root = _corp_tree(make_stage_tree, tmp_path, ("00000001",))
    make_stage_tree(tmp_path, "stg_audit", [
        _audit("20210330000001", "-", "적정", row_hash="h1"),
        _audit("20210330000001", "-", "의견거절", opinion_class="의견거절", row_hash="h2"),
        _audit("20210330000001", "제20기(당기)", "적정", row_hash="h3"),
    ], partition_class="receipt_axis")
    r = _build_hand(AUDIT, stage_root, tmp_path,
                    {"corp_code": "00000001", "bsns_year": "2020", "reprt_code": "11011",
                     "bsns_year_label": "-"}, "n_source_rows", "2")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (2, 0)
    m = _gate(r, "EG3_audit_opinion").metrics
    assert (m["n_stage_rows"], m["n_grain_folded"]) == (3, 1)
    assert m["n_class_conflict_grain"] == 1 and m["n_opinion_conflict_grain"] == 1


def test_부정_audit_부적정은_적정으로_뒤집히지_않는다(make_stage_tree,
                                                     tmp_path: Path) -> None:
    """`LIKE '%적정%'` 을 먼저 쓰면 '부적정' 이 '적정' 이 된다(STAGE_SPEC §2-13 함정).

    산출은 stage 의 등급을 나르지만 게이트는 원문에서 `OPINION_CLASS_ORDER` 로 다시 분류해
    대조한다 — 두 구현이 갈리면 `n_class_recomputed_mismatch` 가 0 이 아니다.
    """
    stage_root = _corp_tree(make_stage_tree, tmp_path, ("00000001",))
    make_stage_tree(tmp_path, "stg_audit", [
        _audit("20210330000001", "제20기(당기)", "부적정", opinion_class="부적정", row_hash="h1"),
        # stage 등급이 함정에 빠진 행(원문은 부적정인데 등급이 적정) — 게이트가 잡아야 한다
        _audit("20210330000001", "제19기(전기)", "부적정 의견", opinion_class="적정",
               row_hash="h2"),
        _audit("20210330000001", "제18기(전전기)", None, opinion_class=None, row_hash="h3"),
    ], partition_class="receipt_axis")
    r = _build_hand(AUDIT, stage_root, tmp_path,
                    {"corp_code": "00000001", "bsns_year": "2020", "reprt_code": "11011",
                     "bsns_year_label": "제20기(당기)"}, "adt_opinion_class", "부적정")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (3, 0)
    m = _gate(r, "EG3_audit_opinion").metrics
    assert m["n_class_recomputed_mismatch"] == 1
    assert m["n_class_outside_vocab"] == 0
    assert m["n_by_class"] == {"None": 1, "부적정": 1, "적정": 1}


def test_부정_audit_접수일_결측은_격리된다(make_stage_tree, tmp_path: Path) -> None:
    stage_root = _corp_tree(make_stage_tree, tmp_path, ("00000001",))
    make_stage_tree(tmp_path, "stg_audit", [
        _audit("20210330000001", "제20기(당기)", "적정", row_hash="h1"),
        _audit("20210330000002", "제20기(당기)", "적정", row_hash="h2", year="2019",
               available=None),
    ], partition_class="receipt_axis")
    r = _build_hand(AUDIT, stage_root, tmp_path,
                    {"corp_code": "00000001", "bsns_year": "2020", "reprt_code": "11011",
                     "bsns_year_label": "제20기(당기)"}, "available_basis", "derived")
    assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    assert (r.n_rows, r.n_reject) == (1, 1)
    assert [x["reject_reason"] for x in _rejects(r.out_dir,              # type: ignore[arg-type]
                                                 "bsns_year")] == ["rcept_dt_missing"]
