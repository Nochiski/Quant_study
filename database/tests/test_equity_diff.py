"""scripts/equity_diff.py — 같은 equity 표의 두 판을 grain 키로 대조하는 diff 게이트 (T-G).

실물 픽스처 빌드(느리다)에 기대지 않는다. `tmp_path` 에 `v=<build_id>/year=2025/part0.parquet`
+ `MANIFEST.json` 레이아웃을 손으로 세우고, 키 `(k1, k2)` 를 `--key` 로 넘겨 스크립트만 검증한다.
"""
import json
import sys
from pathlib import Path

import duckdb
import pytest

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# scripts/ 는 패키지가 아니라 위 sys.path 주입 뒤에야 import 된다.
import equity_diff as ed  # noqa: E402

TABLE = "faketbl"
# 컬럼 → duckdb 타입. `build_id` 는 운영 메타(비교에서 빠져야 한다)다.
SCHEMA: dict[str, str] = {
    "k1": "VARCHAR", "k2": "BIGINT", "val": "DOUBLE", "txt": "VARCHAR",
    "capex_basis": "VARCHAR", "available_date": "DATE", "build_id": "VARCHAR",
}


def _row(k1: str, k2: int, val, txt, basis: str, avail: str, bid: str) -> dict[str, object]:
    return {"k1": k1, "k2": k2, "val": val, "txt": txt, "capex_basis": basis,
            "available_date": avail, "build_id": bid}


def _before_rows() -> list[dict[str, object]]:
    return [
        _row("A", 1, 100.0, "x", "standard", "2025-01-02", "b1"),        # → 값→NULL
        _row("A", 2, None, "y", "unavailable", "2025-01-03", "b1"),      # → NULL→값 + basis 변경
        _row("A", 3, 5.0, "z", "standard", "2025-01-04", "b1"),         # → 값 변경
        _row("A", 4, 1.0, "w", "standard", "2025-01-05", "b1"),         # → 행 삭제
        _row("A", 5, 7.0, "v", "standard", "2025-01-06", "b1"),         # → 허용오차 안 변화
        _row("A", 6, 8.0, "u", "standard", "2025-01-07", "b1"),         # → available_date 변경
    ]


def _after_rows() -> list[dict[str, object]]:
    return [
        _row("A", 1, None, "x", "standard", "2025-01-02", "b2"),
        _row("A", 2, 42.0, "y", "ppe_parts", "2025-01-03", "b2"),
        _row("A", 3, 6.0, "z", "standard", "2025-01-04", "b2"),
        _row("A", 5, 7.0 + 1e-12, "v", "standard", "2025-01-06", "b2"),
        _row("A", 6, 8.0, "u", "standard", "2025-01-08", "b2"),
        _row("A", 7, 9.0, "t", "standard", "2025-01-09", "b2"),         # → 행 추가
    ]


def _lit(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return repr(v)


def _write_parquet(path: Path, rows: list[dict[str, object]],
                   schema: dict[str, str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = schema or SCHEMA
    selects = [" SELECT " + ", ".join(f"CAST({_lit(r[c])} AS {t}) AS {c}"
                                     for c, t in cols.items()) for r in rows]
    con = duckdb.connect()
    try:
        con.execute(f"COPY ({' UNION ALL '.join(selects)}) TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()


def _record(build_id: str, rows: int, *, content_hash: str, part_hash: str,
            rules_version: str = "e1.20.0") -> dict[str, object]:
    return {"build_id": build_id, "snapshot_id": "", "rules_version": rules_version,
            "built_at_utc": "2026-09-26T00:00:00+00:00", "n_rows": rows,
            "content_hash": content_hash, "basis": "manual", "inputs": {}, "gates": [],
            "partitions": [{"path": f"v={build_id}/year=2025", "n_rows": rows,
                            "content_hash": part_hash}]}


def _write_manifest(table_root: Path, records: list[dict[str, object]],
                    current: str | None = None) -> None:
    table_root.mkdir(parents=True, exist_ok=True)
    (table_root / "MANIFEST.json").write_text(json.dumps(
        {"table": table_root.name, "current_build": current or str(records[-1]["build_id"]),
         "keep": 3, "builds": records}, ensure_ascii=False), encoding="utf-8")


def _tree(tmp_path: Path, *, before=None, after=None,
          records: list[dict[str, object]] | None = None) -> Path:
    """`<root>/faketbl/v=b1|b2/year=2025/part0.parquet` + MANIFEST 를 세우고 root 를 준다."""
    root = tmp_path / "data" / "equity"
    table_root = root / TABLE
    b_rows = _before_rows() if before is None else before
    a_rows = _after_rows() if after is None else after
    _write_parquet(table_root / "v=b1" / "year=2025" / "part0.parquet", b_rows)
    _write_parquet(table_root / "v=b2" / "year=2025" / "part0.parquet", a_rows)
    _write_manifest(table_root, records or [
        _record("b1", len(b_rows), content_hash="h1", part_hash="p1"),
        _record("b2", len(a_rows), content_hash="h2", part_hash="p2")])
    return root


def _run(root: Path, *args: str, out: Path | None = None) -> tuple[int, dict]:
    out = out or (root.parent / "report.json")
    rc = ed.main(["--root", str(root), "--table", TABLE, "--key", "k1,k2",
                  "--out", str(out), *args])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _counter(rep: dict, column: str, kind: str) -> dict:
    return rep["columns"][column][kind]


# ── 변경 유형별 집계 ──────────────────────────────────────────────────────────
def test_값에서_NULL_로_바뀐_셀을_센다(tmp_path: Path) -> None:
    rc, rep = _run(_tree(tmp_path))
    assert rc == 0
    c = _counter(rep, "val", "value_to_null")
    assert c["total"] == 1
    assert c["examples"][0]["key"] == {"k1": "A", "k2": 1}
    assert c["examples"][0]["before"] == 100.0
    assert c["examples"][0]["after"] is None


def test_NULL_에서_값으로_바뀐_셀을_센다(tmp_path: Path) -> None:
    _, rep = _run(_tree(tmp_path))
    c = _counter(rep, "val", "null_to_value")
    assert c["total"] == 1
    assert c["examples"][0]["key"] == {"k1": "A", "k2": 2}
    assert c["examples"][0]["after"] == 42.0


def test_값_변경은_허용오차_밖만_센다(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _, rep = _run(root)                                  # 기본 --tol 1e-9 상대
    c = _counter(rep, "val", "value_changed")
    assert c["total"] == 1                               # (A,3) 5→6 만. (A,5) 는 1e-12 상대
    assert c["examples"][0]["key"] == {"k1": "A", "k2": 3}
    # 컬럼별 허용오차 — val 만 50% 로 풀면 5→6(상대 0.167)도 변경이 아니다
    _, loose = _run(root, "--tol", "val=0.5", out=root.parent / "loose.json")
    assert _counter(loose, "val", "value_changed")["total"] == 0


def test_행_추가와_삭제를_센다(tmp_path: Path) -> None:
    _, rep = _run(_tree(tmp_path))
    assert rep["rows"]["rows_added"]["total"] == 1
    assert rep["rows"]["rows_removed"]["total"] == 1
    assert rep["rows"]["rows_removed"]["examples"][0]["key"] == {"k1": "A", "k2": 4}
    assert rep["rows"]["rows_added"]["examples"][0]["key"] == {"k1": "A", "k2": 7}
    assert rep["rows"]["before"] == 6
    assert rep["rows"]["after"] == 6
    assert rep["rows"]["matched"] == 5


def test_basis_와_available_date_변경을_따로_센다(tmp_path: Path) -> None:
    _, rep = _run(_tree(tmp_path))
    assert _counter(rep, "capex_basis", "value_changed")["total"] == 1
    assert rep["groups"]["basis"]["columns"] == ["capex_basis"]
    assert rep["groups"]["basis"]["changed"] == 1
    assert rep["groups"]["available_date"]["columns"] == ["available_date"]
    assert rep["groups"]["available_date"]["changed"] == 1


def test_한쪽에만_있는_컬럼은_비교하지_않고_보고만_한다(tmp_path: Path) -> None:
    """새 컬럼(예 e1.18.0 의 capex_basis)을 「전 행 NULL→값」으로 세면 숫자가 의미를 잃는다.
    비교에서 빼되 expect 의 where 는 그 컬럼을 쓸 수 있어야 한다(after 쪽 스코프)."""
    root = tmp_path / "data" / "equity"
    old_schema = {k: v for k, v in SCHEMA.items() if k != "capex_basis"}
    before = [{k: v for k, v in r.items() if k != "capex_basis"} for r in _before_rows()]
    _write_parquet(root / TABLE / "v=b1" / "year=2025" / "part0.parquet", before,
                   schema=old_schema)
    _write_parquet(root / TABLE / "v=b2" / "year=2025" / "part0.parquet", _after_rows())
    _write_manifest(root / TABLE, [
        _record("b1", len(before), content_hash="h1", part_hash="p1"),
        _record("b2", 6, content_hash="h2", part_hash="p2")])
    exp = _expect(tmp_path / "e.json", [
        {"column": "val", "kind": "null_to_value", "where": "capex_basis = 'ppe_parts'",
         "note": "새 컬럼도 where 에서는 보인다"}])
    _, rep = _run(root, "--expect", str(exp))
    assert rep["columns_only_in_after"] == ["capex_basis"]
    assert "capex_basis" not in rep["columns"]
    assert _counter(rep, "val", "null_to_value")["explained"] == 1


def test_운영_메타_컬럼은_비교에서_뺀다(tmp_path: Path) -> None:
    _, rep = _run(_tree(tmp_path))
    assert "build_id" in rep["excluded_columns"]
    assert "v" in rep["excluded_columns"]                 # 하이브 판 키
    assert "build_id" not in rep["columns"]
    assert "v" not in rep["columns"]
    assert "year" in rep["excluded_columns"]               # 하이브 파티션 키


# ── expect 선언과 게이트 ─────────────────────────────────────────────────────
def _expect(path: Path, entries: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"expect": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def test_expect_가_where_로_설명하고_남은_값에서_NULL_은_게이트를_깬다(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    exp = _expect(tmp_path / "e.json", [
        {"column": "val", "kind": "null_to_value", "where": "capex_basis = 'ppe_parts'",
         "note": "capex 자산별 합 복구"},
        {"column": "capex_basis", "kind": "value_changed", "where": "capex_basis = 'ppe_parts'",
         "note": "같은 규칙의 basis 전환"}])
    rc, rep = _run(root, "--expect", str(exp), "--gate")
    assert _counter(rep, "val", "null_to_value") == {
        "total": 1, "explained": 1, "unexplained": 0,
        "examples": _counter(rep, "val", "null_to_value")["examples"]}
    assert _counter(rep, "capex_basis", "value_changed")["unexplained"] == 0
    # 남은 값→NULL 1 · 행 삭제 1 · available_date 변경 1 → 게이트 rc 2
    assert rc == 2
    assert _counter(rep, "val", "value_to_null")["unexplained"] == 1
    assert rep["gate"]["rc"] == 2
    assert rep["gate"]["unexplained_value_to_null"] == 1
    assert rep["gate"]["unexplained_rows_removed"] == 1
    assert rep["gate"]["unexplained_available_date"] == 1


def test_expect_가_게이트_3축을_모두_설명하면_통과한다(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    exp = _expect(tmp_path / "e.json", [
        {"column": "val", "kind": "value_to_null", "where": "k2 = 1", "note": "규칙에서 제외"},
        {"kind": "rows_removed", "where": "before_k2 = 4", "note": "격리로 옮긴 행"},
        {"column": "available_date", "kind": "value_changed", "note": "접수일 원천 교체"}])
    rc, rep = _run(root, "--expect", str(exp), "--gate")
    assert rep["gate"]["unexplained_value_to_null"] == 0
    assert rep["gate"]["unexplained_rows_removed"] == 0
    assert rep["gate"]["unexplained_available_date"] == 0
    assert rc == 0
    # 미설명 값변경·NULL→값은 보고만 한다 — 게이트를 깨지 않는다
    assert _counter(rep, "val", "value_changed")["unexplained"] == 1
    assert _counter(rep, "val", "null_to_value")["unexplained"] == 1


def test_변경이_0_인_선언의_where_오타도_잡는다(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    exp = _expect(tmp_path / "e.json", [
        # txt 는 두 판이 같아 카운터가 0 이다 — 그래도 술어는 검사해야 한다
        {"column": "txt", "kind": "value_changed", "where": "없는컬럼 = 1", "note": "오타"}])
    rc = ed.main(["--root", str(root), "--table", TABLE, "--key", "k1,k2",
                  "--out", str(tmp_path / "x.json"), "--expect", str(exp)])
    assert rc == 1


def test_게이트_없이는_변경이_있어도_rc_0(tmp_path: Path) -> None:
    rc, _ = _run(_tree(tmp_path))
    assert rc == 0


# ── 결정성 ──────────────────────────────────────────────────────────────────
def test_결정성_파티션_해시가_같으면_0_다르면_2(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    rc = ed.main(["--root", str(root), "--table", TABLE, "--determinism", "b1", "b2",
                  "--out", str(tmp_path / "d1.json")])
    assert rc == 2
    rep = json.loads((tmp_path / "d1.json").read_text(encoding="utf-8"))
    assert rep["equal"] is False
    assert rep["partitions"][0]["equal"] is False

    same = _tree(tmp_path / "same", records=[
        _record("b1", 6, content_hash="hX", part_hash="pX"),
        _record("b2", 6, content_hash="hX", part_hash="pX")])
    rc2 = ed.main(["--root", str(same), "--table", TABLE, "--determinism", "b1", "b2",
                   "--out", str(tmp_path / "d2.json")])
    assert rc2 == 0
    rep2 = json.loads((tmp_path / "d2.json").read_text(encoding="utf-8"))
    assert rep2["equal"] is True


# ── 기본 판 선택 · 키 위생 · grain 선언 ──────────────────────────────────────
def test_기본값은_current_build_와_디스크에_남은_직전_판(tmp_path: Path) -> None:
    root = _tree(tmp_path, records=[
        # b0 은 MANIFEST 에만 있고 디스크에서 GC 된 판 — before 후보가 아니다
        _record("b0", 3, content_hash="h0", part_hash="p0"),
        _record("b1", 6, content_hash="h1", part_hash="p1"),
        _record("b2", 6, content_hash="h2", part_hash="p2")])
    out = tmp_path / "def.json"
    rc = ed.main(["--root", str(root), "--table", TABLE, "--key", "k1,k2", "--out", str(out)])
    assert rc == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert (rep["before"], rep["after"]) == ("b1", "b2")


def test_직전_판이_없으면_분명히_거절한다(tmp_path: Path) -> None:
    root = tmp_path / "data" / "equity"
    _write_parquet(root / TABLE / "v=b2" / "year=2025" / "part0.parquet", _after_rows())
    _write_manifest(root / TABLE, [_record("b2", 6, content_hash="h2", part_hash="p2")])
    rc = ed.main(["--root", str(root), "--table", TABLE, "--key", "k1,k2",
                  "--out", str(tmp_path / "x.json")])
    assert rc == 1


def test_키_중복과_NULL_을_먼저_알린다(tmp_path: Path, capsys) -> None:
    dup = _after_rows() + [_row("A", 7, 1.0, "dup", "standard", "2025-01-09", "b2")]
    dup.append(_row("A", None, 1.0, "nullkey", "standard", "2025-01-09", "b2"))
    root = _tree(tmp_path, after=dup)
    out = tmp_path / "dup.json"
    rc = ed.main(["--root", str(root), "--table", TABLE, "--key", "k1,k2", "--out", str(out)])
    assert rc == 1                                        # 팬아웃된 조인은 숫자를 못 믿는다
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["key_issues"]["after"]["duplicate_keys"] == 1
    assert rep["key_issues"]["after"]["null_keys"] == 1
    assert rep["key_issues"]["before"]["duplicate_keys"] == 0
    assert "키" in capsys.readouterr().out


def test_grain_은_규칙_모듈에서_읽는다() -> None:
    pytest.importorskip("duckdb")
    assert ed.declared_grain("fin_std") == (
        "corp_code", "period_end", "report_code", "fs_div", "vintage_kind")


# ── 출력 ────────────────────────────────────────────────────────────────────
def test_요약표를_stdout_에_JSON_을_기본_경로에_낸다(tmp_path: Path, capsys) -> None:
    root = _tree(tmp_path)
    rc = ed.main(["--root", str(root), "--table", TABLE, "--key", "k1,k2"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "| 컬럼 |" in text and "val" in text and TABLE in text
    default_out = root.parent / "logs" / "equity_diff" / f"{TABLE}_b1_b2.json"
    assert default_out.exists()
    assert json.loads(default_out.read_text(encoding="utf-8"))["after"] == "b2"


def test_YAML_expect_도_읽는다(tmp_path: Path) -> None:
    pytest.importorskip("yaml", reason="YAML expect 는 PyYAML 이 있을 때만 — JSON 은 항상 된다")
    root = _tree(tmp_path)
    exp = tmp_path / "e.yaml"
    exp.write_text("expect:\n"
                   "  - column: val\n"
                   "    kind: value_to_null\n"
                   "    where: \"k2 = 1\"\n"
                   "    note: 규칙에서 제외\n", encoding="utf-8")
    _, rep = _run(root, "--expect", str(exp))
    assert _counter(rep, "val", "value_to_null")["explained"] == 1


@pytest.mark.parametrize(("sql_type", "numeric"), [
    ("INTEGER", True), ("BIGINT", True), ("UBIGINT", True), ("HUGEINT", True), ("DOUBLE", True),
    ("FLOAT", True), ("DECIMAL(38,4)", True), ("DECIMAL(18, 3)", True),
    ("INTEGER[]", False), ("BIGINT[]", False), ("DOUBLE[]", False),
    ("STRUCT(n VARCHAR)", False), ("MAP(VARCHAR, INTEGER)", False), ("VARCHAR", False),
    ("DATE", False), ("BOOLEAN", False), ("INTERVAL", False),
])
def test_수치_판정은_스칼라_타입만이다(sql_type: str, numeric: bool) -> None:
    """옛 식은 앞만 맞춰 `INTEGER[]` 를 수치로 보고 DOUBLE 캐스트했다(서버 10-08 stg_wise_coverage 판정 불가)."""
    assert ed.is_numeric(sql_type) is numeric


def test_목록_구조체_열은_동등_비교로_센다(tmp_path: Path) -> None:
    schema = {"k1": "VARCHAR", "k2": "BIGINT", "fails": "INTEGER[]", "mk": "STRUCT(a VARCHAR)"}
    before = [{"k1": "A", "k2": 1, "fails": [1], "mk": {"a": None}},
              {"k1": "A", "k2": 2, "fails": [2], "mk": {"a": None}},
              {"k1": "A", "k2": 3, "fails": [3], "mk": {"a": None}}]
    after = [{"k1": "A", "k2": 1, "fails": [1], "mk": {"a": None}},
             {"k1": "A", "k2": 2, "fails": None, "mk": {"a": "x"}},
             {"k1": "A", "k2": 3, "fails": [3, 4], "mk": {"a": None}}]
    root = tmp_path / "data" / "equity"
    for bid, rows in (("b1", before), ("b2", after)):
        path = root / TABLE / f"v={bid}" / "year=2025" / "part0.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        try:
            con.execute(f"CREATE TABLE t ({', '.join(f'{c} {ty}' for c, ty in schema.items())})")
            con.executemany("INSERT INTO t VALUES (?, ?, ?, ?)",
                            [[r[c] for c in schema] for r in rows])
            con.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")
        finally:
            con.close()
    _write_manifest(root / TABLE, [_record("b1", 3, content_hash="h1", part_hash="p1"),
                                   _record("b2", 3, content_hash="h2", part_hash="p2")])
    rc, rep = _run(root)
    assert rc == 0
    assert _counter(rep, "fails", "value_to_null")["total"] == 1
    assert _counter(rep, "fails", "value_changed")["total"] == 1
    assert _counter(rep, "mk", "value_changed")["total"] == 1
