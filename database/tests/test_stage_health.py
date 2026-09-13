"""stage 건전성 C1~C5 (v1 플랜 P4 Task 4.3 / v2 Task B.1).

판정은 MANIFEST 와 `_failed/` 만 읽는다 — 원장·스냅샷·parquet 를 열지 않으므로 비용이 0이고
빌드 직후 어느 시점에 돌려도 같은 답이 나온다. C4 의 "재현성" 도 재빌드가 아니라 **직전 판 대비
소스 계수(Σn_src·n_dedup·n_reject)가 그대로인 표는 content_hash 도 그대로여야 한다** 는 술어다.

`--basis` 접두어 규약(e_/m_/b_)은 C1 의 판정 근거라 여기서 함께 검증한다.
"""
import json
from pathlib import Path

from stage import health, manifest, model

DATE = "20260911"
# KST 2026-09-11 08:10 — 아침 확정판(UTC 로는 전날 23:10 이다)
PREV_BID = "m_20260910T231000_000000Z"
# KST 2026-09-11 18:15 — 저녁 잠정판
CUR_BID = "e_20260911T091500_000000Z"
CUR_BUILT_AT = "2026-09-11T09:35:00+00:00"
TABLES = {"stg_a": "append_only", "stg_b": "upsert"}


def _commit(table_root: Path, build_id: str, *, n_rows: int, content_hash: str, n_src: int,
            built_at_utc: str, n_dedup: int = 0, n_reject: int = 0) -> None:
    manifest.commit(table_root, manifest.BuildRecord(
        build_id=build_id, snapshot_id="snap_t", rules_version="2.2.3",
        built_at_utc=built_at_utc, n_rows=n_rows, content_hash=content_hash,
        gates=[{"name": "G1", "status": "pass", "detail": "",
                "metrics": {"n_src": n_src, "fanout": 1, "n_dedup": n_dedup,
                            "n_reject": n_reject, "n_stage": n_rows}}]))


def _tree(tmp_path: Path, make_stage_tree, table: str) -> Path:
    """표 하나에 아침 판 → 저녁 판 순으로 두 판을 쌓는다. 계수·해시는 기본값이 통과하게."""
    t = make_stage_tree(tmp_path, table, [{"k": "1"}], build_id="b_seed")
    _commit(t.table_root, PREV_BID, n_rows=10, content_hash="10:aa", n_src=10,
            built_at_utc="2026-09-10T23:30:00+00:00")
    _commit(t.table_root, CUR_BID, n_rows=12, content_hash="12:bb", n_src=12,
            built_at_utc=CUR_BUILT_AT)
    return t.stage_root


def _run(stage_root: Path, *, tables: dict[str, str] | None = None,
         skip: tuple[str, ...] = (), started_at: str | None = None,
         budget_s: int = health.BUDGET_S_DEFAULT) -> health.StageHealth:
    return health.check_stage(stage_root, basis="evening", date_kst=DATE,
                              tables=tables or TABLES, skip=skip, started_at=started_at,
                              budget_s=budget_s)


def _check(r: health.StageHealth, name: str) -> health.Check:
    return next(c for c in r.checks if c.name == name)


def _all(tmp_path: Path, make_stage_tree) -> Path:
    roots = [_tree(tmp_path, make_stage_tree, t) for t in TABLES]
    return roots[0]


# ── C1 오늘 판·basis 일치 ───────────────────────────────────────────────────


def test_every_check_passes_on_a_clean_two_build_tree(tmp_path: Path, make_stage_tree) -> None:
    r = _run(_all(tmp_path, make_stage_tree))
    assert r.ok, [(c.name, c.detail) for c in r.checks if c.status is health.Status.FAIL]
    assert [c.name for c in r.checks] == ["C1", "C2", "C3", "C4", "C5"]
    assert "pass 5/5" in r.summary() and r.basis == "evening" and r.date == DATE


def test_c1_flags_a_table_left_on_yesterdays_build(tmp_path: Path, make_stage_tree) -> None:
    root = _all(tmp_path, make_stage_tree)
    _commit(root / "stg_b", "e_20260910T091500_000000Z", n_rows=12, content_hash="12:bb",
            n_src=12, built_at_utc="2026-09-10T09:35:00+00:00")     # 어제 저녁 판이 현재 판
    c1 = _check(_run(root), "C1")
    assert c1.status is health.Status.FAIL
    assert c1.metrics["stale"] == ["stg_b"] and "stg_b" in c1.detail


def test_c1_flags_a_table_built_on_another_basis(tmp_path: Path, make_stage_tree) -> None:
    """아침 판 위에 저녁 판정을 돌리면 basis 불일치로 걸린다 — 판이 안 바뀐 것을 못 보면 안 된다."""
    root = _all(tmp_path, make_stage_tree)
    _commit(root / "stg_b", "m_20260910T231500_000000Z", n_rows=12, content_hash="12:bb",
            n_src=12, built_at_utc=CUR_BUILT_AT)
    c1 = _check(_run(root), "C1")
    assert c1.status is health.Status.FAIL
    assert c1.metrics["basis_mismatch"] == ["stg_b"]


def test_c1_reports_a_table_with_no_manifest_as_missing(tmp_path: Path, make_stage_tree) -> None:
    root = _all(tmp_path, make_stage_tree)
    c1 = _check(_run(root, tables={**TABLES, "stg_never": "append_only"}), "C1")
    assert c1.status is health.Status.FAIL and c1.metrics["missing"] == ["stg_never"]


def test_skipped_tables_are_excluded_from_c1(tmp_path: Path, make_stage_tree) -> None:
    """문서층 4표는 프리패스 캐시가 없으면 run_stage_all 이 건너뛴다 — 의도된 동결이라 FAIL 이 아니다."""
    root = _all(tmp_path, make_stage_tree)
    r = _run(root, tables={**TABLES, "stg_doc_meta": "append_only"}, skip=("stg_doc_meta",))
    c1 = _check(r, "C1")
    assert c1.status is health.Status.PASS and c1.metrics["skipped"] == ["stg_doc_meta"]
    assert r.ok


# ── C2 오늘 `_failed` 0 ─────────────────────────────────────────────────────


def test_c2_counts_only_failed_reports_from_today(tmp_path: Path, make_stage_tree) -> None:
    root = _all(tmp_path, make_stage_tree)
    failed = root / "_failed"
    failed.mkdir()
    (failed / "e_20260901T091500_000000Z.json").write_text("{}", encoding="utf-8")  # 지난달
    assert _check(_run(root), "C2").status is health.Status.PASS
    (failed / "e_20260911T092000_000000Z.json").write_text("{}", encoding="utf-8")
    c2 = _check(_run(root), "C2")
    assert c2.status is health.Status.FAIL
    assert c2.metrics["today"] == ["e_20260911T092000_000000Z"]


def test_c2_with_started_at_ignores_failures_from_an_earlier_run_the_same_day(
        tmp_path: Path, make_stage_tree) -> None:
    """09-12 12:49 실측: 10:11 실행이 남긴 폐기 파일이 12:00 재실행의 C2 를 깨뜨렸다. 체인 시작 시각을 주면
    그 이후의 폐기만 센다 — 같은 날 앞선 실행의 폐기는 고쳐서 다시 지은 것이라 이번 판의 문제가 아니다."""
    root = _all(tmp_path, make_stage_tree)
    failed = root / "_failed"
    failed.mkdir()
    (failed / "e_20260911T012000_000000Z.json").write_text("{}", encoding="utf-8")   # 오늘 10:20 KST, 이전 실행
    c2 = _check(_run(root, started_at="2026-09-11T02:00:00+00:00"), "C2")             # 체인 시작 11:00 KST
    assert c2.status is health.Status.PASS and c2.metrics["today"] == []
    (failed / "e_20260911T092000_000000Z.json").write_text("{}", encoding="utf-8")   # 18:20 KST, 이번 체인
    c2 = _check(_run(root, started_at="2026-09-11T02:00:00+00:00"), "C2")
    assert c2.status is health.Status.FAIL and c2.metrics["today"] == ["e_20260911T092000_000000Z"]
    assert "체인 시작" in c2.detail


# ── C3 append_only 행수 비감소 ──────────────────────────────────────────────


def test_c3_flags_an_append_only_table_that_lost_rows(tmp_path: Path, make_stage_tree) -> None:
    root = _all(tmp_path, make_stage_tree)
    _commit(root / "stg_a", CUR_BID, n_rows=9, content_hash="9:cc", n_src=9,
            built_at_utc=CUR_BUILT_AT)          # 직전 10행 → 9행
    c3 = _check(_run(root), "C3")
    assert c3.status is health.Status.FAIL
    assert c3.metrics["decreased"] == [{"table": "stg_a", "previous": 10, "current": 9}]


def test_c3_ignores_row_loss_on_tables_that_are_not_append_only(tmp_path: Path,
                                                                make_stage_tree) -> None:
    root = _all(tmp_path, make_stage_tree)
    _commit(root / "stg_b", CUR_BID, n_rows=9, content_hash="9:cc", n_src=9,
            built_at_utc=CUR_BUILT_AT)          # upsert 표는 행이 줄 수 있다
    assert _check(_run(root), "C3").status is health.Status.PASS


# ── C4 무비용 재현성 ────────────────────────────────────────────────────────


def test_c4_requires_an_unchanged_hash_when_source_counters_did_not_move(
        tmp_path: Path, make_stage_tree) -> None:
    root = _all(tmp_path, make_stage_tree)
    _commit(root / "stg_a", CUR_BID, n_rows=10, content_hash="10:zz", n_src=10,
            built_at_utc=CUR_BUILT_AT)          # 계수는 직전과 같은데 해시만 달라졌다
    c4 = _check(_run(root), "C4")
    assert c4.status is health.Status.FAIL
    assert c4.metrics["mismatched"] == [{"table": "stg_a", "previous": "10:aa",
                                         "current": "10:zz"}]


def test_c4_passes_when_the_frozen_table_reproduces_the_same_hash(tmp_path: Path,
                                                                  make_stage_tree) -> None:
    root = _all(tmp_path, make_stage_tree)
    _commit(root / "stg_a", CUR_BID, n_rows=10, content_hash="10:aa", n_src=10,
            built_at_utc=CUR_BUILT_AT)
    c4 = _check(_run(root), "C4")
    assert c4.status is health.Status.PASS and c4.metrics["n_frozen"] == 1


def test_c4_skips_tables_whose_source_grew(tmp_path: Path, make_stage_tree) -> None:
    """계수가 움직인 표는 해시가 달라야 정상이라 판정 대상이 아니다."""
    c4 = _check(_run(_all(tmp_path, make_stage_tree)), "C4")
    assert c4.metrics["n_frozen"] == 0 and c4.status is health.Status.PASS


# ── C5 소요 예산 ────────────────────────────────────────────────────────────


def test_c5_measures_from_the_first_build_id_to_the_last_commit(tmp_path: Path,
                                                                make_stage_tree) -> None:
    c5 = _check(_run(_all(tmp_path, make_stage_tree)), "C5")
    assert c5.status is health.Status.PASS
    assert c5.metrics["elapsed_s"] == 1200.0      # 09:15:00 빌드 id → 09:35:00 커밋


def test_c5_records_over_budget_without_failing(tmp_path: Path, make_stage_tree) -> None:
    c5 = _check(_run(_all(tmp_path, make_stage_tree), budget_s=600), "C5")
    # 2026-09-12 계약 변경: 예산 초과는 기록형(over_budget) — 느린 판을 버리지 않는다
    assert c5.status is health.Status.PASS and c5.metrics["budget_s"] == 600
    assert c5.metrics["over_budget"] is True and "초과" in c5.detail


def test_c5_uses_started_at_when_the_chain_reports_the_snapshot_start(tmp_path: Path,
                                                                     make_stage_tree) -> None:
    """스냅샷(≈4분)은 빌드 id 보다 앞선다 — 체인이 실제 시작 시각을 주면 그걸 쓴다."""
    c5 = _check(_run(_all(tmp_path, make_stage_tree),
                     started_at="2026-09-11T09:11:00+00:00"), "C5")
    assert c5.metrics["elapsed_s"] == 1440.0


# ── 보고 파일 ───────────────────────────────────────────────────────────────


def test_report_serializes_status_and_metrics_as_json(tmp_path: Path, make_stage_tree) -> None:
    r = _run(_all(tmp_path, make_stage_tree))
    d = json.loads(json.dumps(r.as_dict(), ensure_ascii=False))
    assert d["date"] == DATE and d["basis"] == "evening" and d["ok"] is True
    assert [c["name"] for c in d["checks"]] == ["C1", "C2", "C3", "C4", "C5"]
    assert d["checks"][0]["status"] == "pass"


# ── basis ↔ 빌드 id 접두어 (C1 의 판정 근거) ────────────────────────────────


def test_make_build_id_uses_the_basis_prefix() -> None:
    assert model.make_build_id("evening").startswith("e_")
    assert model.make_build_id("morning").startswith("m_")
    assert model.make_build_id("manual").startswith("b_")


def test_basis_of_build_id_defaults_to_manual_for_legacy_ids() -> None:
    assert model.basis_of_build_id(CUR_BID) == "evening"
    assert model.basis_of_build_id(PREV_BID) == "morning"
    assert model.basis_of_build_id("b_20260905T105922_786120Z") == "manual"
    assert model.basis_of_build_id("b_stage_0001") == "manual"


def test_build_record_derives_basis_from_the_build_id(tmp_path: Path) -> None:
    """MANIFEST 에 basis 를 남긴다 — 구 레코드(필드 없음)는 접두어로 되살린다."""
    root = tmp_path / "stg_x"
    root.mkdir()
    _commit(root, CUR_BID, n_rows=1, content_hash="1:a", n_src=1, built_at_utc=CUR_BUILT_AT)
    m = manifest.load(root / "MANIFEST.json")
    assert m.builds[-1].basis == "evening"
    assert json.loads((root / "MANIFEST.json").read_text(encoding="utf-8")
                      )["builds"][-1]["basis"] == "evening"
    old = {"table": "stg_x", "current_build": "b_0", "keep": 3, "builds": [
        {"build_id": "b_0", "snapshot_id": "s0", "rules_version": "2.2.3",
         "built_at_utc": "2026-09-01T00:00:00+00:00", "n_rows": 1, "content_hash": "1:0"}]}
    (root / "MANIFEST.json").write_text(json.dumps(old), encoding="utf-8")
    assert manifest.load(root / "MANIFEST.json").builds[0].basis == "manual"


# ── built_on: 아침 확정판은 대상일(T-1)과 빌드일(T)이 다르다 ──────────────────────
def test_C1_아침_확정판은_대상일이_전_거래일이어도_빌드일_기준으로_PASS(tmp_path: Path,
                                                                    make_stage_tree) -> None:
    """08:10 체인은 D=T-1(전 거래일) 로 판정을 부르지만 판은 T 아침에 커밋된다. C1·C2·C5 는
    빌드일(`built_on`)로 보고, 리포트의 `date` 만 D 로 남아야 한다."""
    for t in TABLES:
        tree = make_stage_tree(tmp_path, t, [{"k": "1"}], build_id="b_seed")
        _commit(tree.table_root, PREV_BID, n_rows=10, content_hash="10:aa", n_src=10,
                built_at_utc="2026-09-10T23:30:00+00:00")   # KST 09-11 08:30 아침 판
    root = tree.stage_root
    r = health.check_stage(root, basis="morning", date_kst="20260910", built_on="20260911",
                           tables=TABLES, started_at="2026-09-10T23:10:00+00:00")
    assert _check(r, "C1").status is health.Status.PASS, _check(r, "C1").detail
    assert _check(r, "C5").status is health.Status.PASS, _check(r, "C5").detail
    assert r.date == "20260910" and r.built_on == "20260911"
    assert r.as_dict()["built_on"] == "20260911"


def test_built_on_생략이면_대상일과_같다(tmp_path: Path, make_stage_tree) -> None:
    root = _all(tmp_path, make_stage_tree)
    r = _run(root)
    assert r.built_on == DATE
    assert _check(r, "C1").status is health.Status.PASS
