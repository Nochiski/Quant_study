"""QL-F — v3 quant.db 반영(스테이징 → 게이트 → 9표 한 트랜잭션) `compat.v3_post`.

정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 QL-F · T-16 · T-27, 로드맵 §8 K3-2·K3-3.

본 파일(v3 quant.db)은 compat 이 쓰는 `v3_schema.sql`(v3 실제 DDL 합본) + compat 이 쓰지 않는 v3 표
`market_indices`(v3 `backend/db/schema.py:207-216` 그대로)로 만든다. WAL 모드(v3 `connection.py` 와 같다).
스테이징은 `snapshot` 으로 뜬 뒤 'compat 이 쓴 모양'(창 행 교체 + `_compat_meta` 1행)을 직접 만든다 —
compat 자체를 돌리는 왕복은 맨 아래 통합 테스트가 맡는다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from compat import CompatError, export, v3_post
from compat.mappings import MAPPINGS
from compat.quant_db import SCHEMA_SQL_PATH, ExportResult, TableResult, _write_meta
from compat.v3_post import (
    SCORE_TABLES,
    TABLES,
    V3PostGateError,
    apply,
    gate,
    snapshot,
    tables_for,
)

D = "20261008"
D_ISO = "2026-10-08"
FROM_ISO = "2026-09-24"          # compat 증분 창 14달력일
BEFORE = "2026-09-23"            # 창 밖(바로 앞날)
OTHER_SCORE_DATE = "2026-10-07"  # 점수 표의 다른 날

MARKET_INDICES_DDL = """
CREATE TABLE IF NOT EXISTS market_indices (
    index_code  TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        INTEGER NOT NULL,
    high        INTEGER NOT NULL,
    low         INTEGER NOT NULL,
    close       INTEGER NOT NULL,
    volume      INTEGER,
    PRIMARY KEY (index_code, trade_date)
) WITHOUT ROWID;
"""


def _score(n_cols: int, code: str, d_iso: str, total: float) -> tuple:
    row: list = [None] * n_cols
    row[0], row[1], row[6] = code, d_iso, total
    return tuple(row)


def _ncols(con: sqlite3.Connection, table: str) -> int:
    return len(con.execute(f"PRAGMA table_info({table})").fetchall())


def _make_main(path: Path) -> None:
    """v3 가 쓰던 옛 값이 든 본 파일."""
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8") + MARKET_INDICES_DDL)
        con.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?)", [
            ("005930", BEFORE, 1, 1, 1, 100, 10, 1, 100.0),        # 창 밖
            ("005930", FROM_ISO, 1, 1, 1, 101, 10, 1, 101.0),      # 창 첫날
            ("005930", D_ISO, 1, 1, 1, 102, 10, 1, 102.0),
            ("999990", "2026-10-01", 1, 1, 1, 50, 10, 1, 50.0),    # v3 에만 있는 종목(창 안)
        ])
        con.executemany("INSERT INTO investor_detail_flows (stock_code, trade_date, individual) "
                        "VALUES (?,?,?)", [("005930", BEFORE, -1), ("005930", D_ISO, -2)])
        con.executemany("INSERT INTO stocks (stock_code, stock_name, market, is_active, updated_at) "
                        "VALUES (?,?,?,?,?)", [("005930", "삼성전자", "KOSPI", 1, "old"),
                                               ("900000", "폐지예정", "KOSDAQ", 1, "old")])
        con.execute("INSERT INTO consensus_revision_daily (stock_code, base_date, op) "
                    "VALUES ('005930', '2026-09-01', 1.0)")
        con.execute("INSERT INTO consensus_revision_compare (stock_code, op_1w) "
                    "VALUES ('005930', 1.0)")
        con.execute("INSERT INTO consensus_annual (stock_code, period, period_type, op) "
                    "VALUES ('005930', '2026/12', 'annual', 1.0)")
        con.execute("INSERT INTO financial_summary (stock_code, period, period_type, op) "
                    "VALUES ('005930', '2025/12', 'annual', 1)")
        for table in ("score_history", "score_history_v2"):
            n = _ncols(con, table)
            con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * n)})", [
                _score(n, "005930", OTHER_SCORE_DATE, 1.0),
                _score(n, "005930", D_ISO, 2.0),
                _score(n, "777770", D_ISO, 3.0)])       # v3 가 쓰던 D 행 — 이번 판에 없는 종목
        con.executemany("INSERT INTO market_indices VALUES (?,?,?,?,?,?,?)", [
            ("001", BEFORE, 1, 1, 1, 2500, 1), ("001", D_ISO, 1, 1, 1, 2600, 1)])
        con.commit()
    finally:
        con.close()


def _tr(table: str, *, n_rows: int = 2, skipped: int = 0, n_on_date: int = 1) -> TableResult:
    """compat 표 결과 — daily_prices 는 이번에 쓴 D 행 수(`n_on_date`, T-31 ③)를 싣는다."""
    return TableResult(n_rows=n_rows, n_skipped=skipped, sources={},
                       metrics={"n_on_date": n_on_date} if table == "daily_prices" else {})


def _meta(con: sqlite3.Connection, d_iso: str, basis: str, exported_at: str,
          tables: tuple[str, ...] = TABLES, **kw) -> None:
    """compat 실행 기록 1행(`quant_db._write_meta` 그대로)."""
    _write_meta(con, ExportResult(
        date=d_iso, basis=basis, target="x", exported_at=exported_at,
        window={"days": 14, "full": False, "from_date": FROM_ISO, "to_date": d_iso},
        consensus_asof=d_iso, tables={t: _tr(t, **kw) for t in tables}))


def _main_record(main: Path, d_iso: str, basis: str, exported_at: str) -> None:
    """본 파일에 앞선 ok 반영 기록을 둔다(v3_post 가 COMMIT 때 옮기는 그 행)."""
    con = sqlite3.connect(str(main), isolation_level=None)
    try:
        _meta(con, d_iso, basis, exported_at)
    finally:
        con.close()


def _fake_compat(staging: Path, *, skipped: dict[str, int] | None = None,
                 tables: tuple[str, ...] = TABLES, status: str = "ok",
                 date_iso: str = D_ISO, basis: str = "evening",
                 drop_scores: bool = False, n_on_date: int = 1) -> None:
    """스테이징에 compat 이 쓴 모양을 만든다(창 행 교체 · 점수 날짜 교체 · 스냅샷 · `_compat_meta` 1행).

    창 밖 날짜(BEFORE)와 점수 표의 다른 날(OTHER_SCORE_DATE)도 일부러 바꿔 둔다 — 반영이 창 밖을
    옮기면 본 파일에 이 값이 나타난다.
    """
    con = sqlite3.connect(str(staging), isolation_level=None)
    try:
        con.execute("INSERT OR REPLACE INTO daily_prices VALUES "
                    "('005930', ?, 9, 9, 9, 201, 20, 2, 201.0)", (FROM_ISO,))
        con.execute("INSERT OR REPLACE INTO daily_prices VALUES "
                    "('005930', ?, 9, 9, 9, 202, 20, 2, 202.0)", (D_ISO,))
        con.execute("INSERT OR REPLACE INTO daily_prices VALUES "
                    "('000660', ?, 9, 9, 9, 300, 20, 2, 300.0)", (D_ISO,))
        con.execute("UPDATE daily_prices SET close = -1 WHERE trade_date = ?", (BEFORE,))
        con.execute("INSERT OR REPLACE INTO investor_detail_flows (stock_code, trade_date, "
                    "individual) VALUES ('005930', ?, 22)", (D_ISO,))
        con.execute("UPDATE investor_detail_flows SET individual = -99 WHERE trade_date = ?",
                    (BEFORE,))
        con.execute("INSERT OR REPLACE INTO stocks (stock_code, stock_name, market, is_active, "
                    "updated_at) VALUES ('005930', '삼성전자', 'KOSPI', 1, 'new')")
        con.execute("INSERT OR REPLACE INTO stocks (stock_code, stock_name, market, is_active, "
                    "updated_at) VALUES ('000660', 'SK하이닉스', 'KOSPI', 1, 'new')")
        con.execute("UPDATE stocks SET is_active = 0 WHERE updated_at <> 'new'")
        con.execute("INSERT OR REPLACE INTO consensus_revision_daily (stock_code, base_date, op) "
                    "VALUES ('005930', '2026-10-07', 2.0)")
        con.execute("UPDATE consensus_revision_compare SET op_1w = 2.0")
        con.execute("UPDATE consensus_annual SET op = 2.0")
        con.execute("UPDATE financial_summary SET op = 2")
        for table in ("score_history", "score_history_v2"):
            n = _ncols(con, table)
            con.execute(f"DELETE FROM {table} WHERE score_date = ?", (D_ISO,))
            if not drop_scores:
                con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * n)})", [
                    _score(n, "005930", D_ISO, 20.0), _score(n, "000660", D_ISO, 30.0)])
            con.execute(f"UPDATE {table} SET composite_score = -5 WHERE score_date = ?"
                        if table == "score_history" else
                        f"UPDATE {table} SET total_score = -5 WHERE score_date = ?",
                        (OTHER_SCORE_DATE,))
        skipped = skipped or {}
        _write_meta(con, ExportResult(
            date=date_iso, basis=basis, target=str(staging),
            exported_at="2026-10-08T07:30:00.000000+00:00",
            window={"days": 14, "full": False, "from_date": FROM_ISO, "to_date": date_iso},
            consensus_asof="2026-10-07", status=status,
            failed_table=None if status == "ok" else "stocks",
            tables={t: _tr(t, skipped=skipped.get(t, 0), n_on_date=n_on_date) for t in tables}))
    finally:
        con.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "v3" / "quant.db"
    main.parent.mkdir()
    _make_main(main)
    return main, tmp_path / "stg" / "staging.db"


# ── 선언 ─────────────────────────────────────────────────────────────────────
def test_tables_are_exactly_the_nine_compat_tables() -> None:
    """반영 대상 = compat 매핑이 선언한 9표 전부(정본은 mappings). market_* 등은 없다(T-27)."""
    assert set(TABLES) == {m.v3_table for m in MAPPINGS}
    assert len(TABLES) == 9
    assert "market_indices" not in TABLES


# ── 스테이징 사본 ────────────────────────────────────────────────────────────
def test_snapshot_copies_main_and_leaves_main_bytes(files) -> None:
    main, stg = files
    before = _sha(main)
    snapshot(main, stg)
    assert _sha(main) == before
    for table in (*TABLES, "market_indices"):
        q = f"SELECT * FROM {table} ORDER BY 1, 2"
        assert _rows(stg, q) == _rows(main, q)


def test_snapshot_replaces_a_stale_staging_and_its_wal(files) -> None:
    """앞 실행이 남긴 스테이징·-wal 위에 새 사본을 뜬다 — 옛 -wal 이 새 사본에 섞이면 안 된다."""
    main, stg = files
    stg.parent.mkdir(parents=True)
    stg.write_bytes(b"garbage")
    Path(f"{stg}-wal").write_bytes(b"stale wal")
    snapshot(main, stg)
    assert not Path(f"{stg}-wal").exists() or Path(f"{stg}-wal").read_bytes() != b"stale wal"
    assert _rows(stg, "SELECT count(*) FROM daily_prices") == [(4,)]


@pytest.mark.parametrize("how", ["same", "dotdot", "symlink", "hardlink", "wal"])
def test_staging_on_main_path_is_refused_and_main_survives(files, how) -> None:
    """MAJOR-1 — 스테이징이 본 파일(링크·사이드카 포함)이면 지우기 전에 거부한다. 가드가 없으면 본 파일이
    사라진다."""
    main, _ = files
    if how == "same":
        stg = main
    elif how == "dotdot":
        stg = main.parent / ".." / main.parent.name / main.name
    elif how == "symlink":
        stg = main.parent / "link.db"
        stg.symlink_to(main)
    elif how == "hardlink":
        stg = main.parent / "hard.db"
        stg.hardlink_to(main)
    else:                                   # 본 파일의 -wal 경로 — 커밋된 데이터가 남아 있을 수 있다
        stg = Path(f"{main}-wal")
    before = _sha(main)
    with pytest.raises(CompatError, match="같은 파일"):
        snapshot(main, stg)
    assert main.exists() and _sha(main) == before
    with pytest.raises(CompatError, match="같은 파일"):
        apply(stg, main, D, "evening")
    assert _sha(main) == before


def test_snapshot_refuses_when_disk_is_short(files, monkeypatch) -> None:
    """MINOR-1 — 여유가 본 파일 크기 × 2 보다 작으면 스테이징을 뜨지 않는다(본 파일 무변경)."""
    main, stg = files
    need = main.stat().st_size * v3_post.DISK_FACTOR

    class _Usage:
        free = need - 1

    monkeypatch.setattr(v3_post.shutil, "disk_usage", lambda _p: _Usage)
    before = _sha(main)
    with pytest.raises(CompatError, match="디스크 여유 부족"):
        snapshot(main, stg)
    assert not stg.exists() and _sha(main) == before


def test_snapshot_failure_removes_partial_staging(files, monkeypatch) -> None:
    """MINOR-1 — 백업이 도중에 실패하면 부분 사본을 남기지 않는다."""
    main, stg = files

    class _Src:
        def backup(self, dst: sqlite3.Connection) -> None:
            dst.execute("CREATE TABLE half (a)")
            dst.commit()
            raise sqlite3.OperationalError("disk I/O error")

        def close(self) -> None:
            pass

    monkeypatch.setattr(v3_post, "_ro", lambda _p: _Src())
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        snapshot(main, stg)
    assert not stg.exists()
    assert not Path(f"{stg}-wal").exists() and not Path(f"{stg}-journal").exists()


def test_snapshot_refuses_missing_main(tmp_path: Path) -> None:
    """본 파일 경로 오타로 빈 sqlite 를 새로 만들고 그걸 반영 기준으로 삼지 않는다."""
    with pytest.raises(FileNotFoundError):
        snapshot(tmp_path / "nope.db", tmp_path / "stg.db")
    assert not (tmp_path / "nope.db").exists()


# ── 한 트랜잭션 반영 ─────────────────────────────────────────────────────────
def test_apply_moves_nine_tables_window_only(files) -> None:
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg)
    report = apply(stg, main, D, "evening")
    assert report.ok

    # 창 안 = 스테이징과 같다(옛 v3 종목 999990 행은 스테이징에 남아 있었으므로 그대로 산다)
    q = "SELECT * FROM daily_prices WHERE trade_date >= ? ORDER BY 1, 2"
    assert _rows(main, q, (FROM_ISO,)) == _rows(stg, q, (FROM_ISO,))
    assert _rows(main, "SELECT close FROM daily_prices WHERE stock_code='005930' "
                       "AND trade_date=?", (D_ISO,)) == [(202,)]
    assert _rows(main, "SELECT close FROM daily_prices WHERE stock_code='999990'") == [(50,)]
    # 창 밖은 본 파일 값 그대로 — 스테이징에서 바꿔 둔 -1 이 넘어오지 않는다
    assert _rows(main, "SELECT close FROM daily_prices WHERE trade_date=?", (BEFORE,)) == [(100,)]
    assert _rows(main, "SELECT individual FROM investor_detail_flows WHERE trade_date=?",
                 (BEFORE,)) == [(-1,)]
    assert _rows(main, "SELECT individual FROM investor_detail_flows WHERE trade_date=?",
                 (D_ISO,)) == [(22,)]
    # 점수 두 표 — D 행은 이번 판과 정확히 같다(v3 옛 종목 777770 은 사라짐), 다른 날은 그대로
    for table, total in (("score_history", "composite_score"), ("score_history_v2", "total_score")):
        assert _rows(main, f"SELECT stock_code, {total} FROM {table} WHERE score_date=? "
                           "ORDER BY 1", (D_ISO,)) == [("000660", 30.0), ("005930", 20.0)]
        assert _rows(main, f"SELECT {total} FROM {table} WHERE score_date=?",
                     (OTHER_SCORE_DATE,)) == [(1.0,)]
    # 스냅샷 표 — 표 전체가 스테이징과 같다
    for table in ("stocks", "consensus_revision_daily", "consensus_revision_compare",
                  "consensus_annual", "financial_summary"):
        q = f"SELECT * FROM {table} ORDER BY 1, 2"
        assert _rows(main, q) == _rows(stg, q), table
    assert _rows(main, "SELECT stock_code, is_active FROM stocks ORDER BY 1") == [
        ("000660", 1), ("005930", 1), ("900000", 0)]
    # compat 이 쓰지 않는 표는 손대지 않는다(T-27)
    assert _rows(main, "SELECT * FROM market_indices ORDER BY 1, 2") == [
        ("001", BEFORE, 1, 1, 1, 2500, 1), ("001", D_ISO, 1, 1, 1, 2600, 1)]
    # 이번 compat 기록 1행이 본 파일에도 남는다(어느 판을 반영했는지)
    assert _rows(main, "SELECT date, basis, status FROM _compat_meta") == [
        (D_ISO, "evening", "ok")]


def test_apply_keeps_rows_written_to_untouched_tables_after_staging(files) -> None:
    """스테이징을 뜬 뒤 v3 가 compat 밖 표(market_indices — insight)에 쓴 행은 반영 뒤에도 남는다(T-27)."""
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg)
    con = sqlite3.connect(str(main))
    con.execute("INSERT INTO market_indices VALUES ('101', ?, 1, 1, 1, 800, 1)", (D_ISO,))
    con.commit()
    con.close()
    apply(stg, main, D, "evening")
    assert _rows(main, "SELECT close FROM market_indices WHERE index_code='101'") == [(800,)]


def test_apply_is_one_transaction(files) -> None:
    """마지막 표(score_history_v2) 쓰기가 실패하면 앞의 8표도 반영되지 않는다(G9).

    표마다 따로 커밋하면 실패 전 표가 바뀐 채 남고 소비자가 그 반쪽 상태를 읽는다.
    """
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg)
    con = sqlite3.connect(str(main))
    con.execute("CREATE TRIGGER boom BEFORE INSERT ON score_history_v2 "
                "BEGIN SELECT RAISE(ABORT, 'boom'); END")
    con.commit()
    dump = {t: _rows(main, f"SELECT * FROM {t} ORDER BY 1, 2") for t in TABLES}
    con.close()
    assert TABLES[-1] == "score_history_v2"
    with pytest.raises(sqlite3.DatabaseError, match="boom"):
        apply(stg, main, D, "evening")
    for table, rows in dump.items():
        assert _rows(main, f"SELECT * FROM {table} ORDER BY 1, 2") == rows, table
    assert _rows(main, "SELECT count(*) FROM sqlite_master WHERE name='_compat_meta'") == [(0,)]


def test_apply_refuses_missing_main(files, tmp_path: Path) -> None:
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg)
    with pytest.raises(FileNotFoundError):
        apply(stg, tmp_path / "nope.db", D, "evening")
    assert not (tmp_path / "nope.db").exists()


def test_second_apply_appends_meta_and_replaces_window_again(files) -> None:
    """다음 날 아침 KRX 확정 재반영 — 본 파일에 이미 `_compat_meta` 가 있어도 새 기록 1행만 더한다."""
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg)
    apply(stg, main, D, "evening")
    snapshot(main, stg)
    con = sqlite3.connect(str(stg), isolation_level=None)
    con.execute("UPDATE daily_prices SET close = 999 WHERE trade_date = ?", (D_ISO,))
    _write_meta(con, ExportResult(
        date=D_ISO, basis="morning", target=str(stg),
        exported_at="2026-10-08T23:30:00.000000+00:00",
        window={"days": 14, "full": False, "from_date": FROM_ISO, "to_date": D_ISO},
        consensus_asof="2026-10-08",
        tables={t: _tr(t, n_rows=1) for t in TABLES if t not in SCORE_TABLES}))
    con.close()
    apply(stg, main, D, "morning")
    assert _rows(main, "SELECT basis FROM _compat_meta ORDER BY exported_at") == [
        ("evening",), ("morning",)]
    assert _rows(main, "SELECT DISTINCT close FROM daily_prices WHERE trade_date=?",
                 (D_ISO,)) == [(999,)]


# ── 게이트 ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("kw", "needle"), [
    ({"skipped": {"daily_prices": 1}}, "필수 열"),                  # G11 — 5% 허용을 0 으로
    ({"skipped": {"stocks": 3}}, "필수 열"),
    ({"drop_scores": True}, "점수 행 0"),
    ({"status": "failed"}, "status=failed"),
    ({"tables": TABLES[:-1]}, "score_history_v2"),                   # 9표를 한 실행으로
    ({"date_iso": "2026-10-07"}, "날짜"),
    ({"basis": "morning"}, "basis"),
    ({"n_on_date": 0}, "신선도"),                                    # T-31 ③ — 이번에 쓴 D 행 0
])
def test_gate_failure_leaves_main_unchanged(files, kw, needle) -> None:
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg, **kw)
    before = _sha(main)
    with pytest.raises(V3PostGateError) as e:
        apply(stg, main, D, "evening")
    assert needle in str(e.value)
    assert _sha(main) == before
    assert not Path(f"{main}-wal").exists() or Path(f"{main}-wal").stat().st_size == 0


def test_gate_without_compat_record_fails(files) -> None:
    """compat 이 스테이징에 기록을 남기지 않았다(안 돌았다) — 옛 v3 행만으로 통과시키지 않는다."""
    main, stg = files
    snapshot(main, stg)
    report = gate(stg, main, D, "evening")
    assert not report.ok
    assert any("compat 기록" in f for f in report.failures)


def test_gate_counts_rows_in_reflection_scope(files) -> None:
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg)
    report = gate(stg, main, D, "evening")
    assert report.ok, report.failures
    assert report.window == (FROM_ISO, D_ISO)
    assert report.counts["daily_prices"] == 4          # 창 안: 005930 ×2 · 000660 · 999990
    assert report.counts["score_history"] == 2
    assert report.counts["stocks"] == 3


def test_gate_freshness_ignores_old_d_rows_already_in_main(files) -> None:
    """스테이징은 본 파일 사본이라 옛 D 행(본 파일의 005930 10-08)이 이미 있다 — 그래도 이번 compat 이
    D 행을 안 썼으면(n_on_date 0) 막는다."""
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg, n_on_date=0)
    report = gate(stg, main, D, "evening")
    assert report.counts["daily_prices"] > 0
    assert any("신선도" in f for f in report.failures)


# ── T-34 아침 재반영 범위 ────────────────────────────────────────────────────
def test_morning_after_evening_reflects_seven_tables_and_keeps_evening_scores(files) -> None:
    main, stg = files
    _main_record(main, D_ISO, "evening", "2026-10-08T07:30:00.000000+00:00")
    assert tables_for(main, D, "morning") == tuple(t for t in TABLES if t not in SCORE_TABLES)
    assert tables_for(main, D, "evening") == TABLES
    snapshot(main, stg)
    # 아침 compat 은 7표만 쓴다(점수 표는 compat 이 아예 안 고른다 — 아침 모델 판이 없어도 된다)
    con = sqlite3.connect(str(stg), isolation_level=None)
    con.execute("UPDATE daily_prices SET close = 555 WHERE trade_date = ?", (D_ISO,))
    con.execute("UPDATE score_history SET composite_score = 99 WHERE score_date = ?", (D_ISO,))
    _meta(con, D_ISO, "morning", "2026-10-08T23:30:00.000000+00:00",
          tables=tables_for(main, D, "morning"))
    con.close()
    before = _rows(main, "SELECT * FROM score_history ORDER BY 1, 2")
    report = apply(stg, main, D, "morning")
    assert report.tables == tables_for(main, D, "morning")
    assert _rows(main, "SELECT DISTINCT close FROM daily_prices WHERE trade_date=?",
                 (D_ISO,)) == [(555,)]
    assert _rows(main, "SELECT * FROM score_history ORDER BY 1, 2") == before   # 저녁 점수 그대로


def test_morning_without_evening_record_needs_score_tables(files) -> None:
    """장 마감 반영이 없던 날(T-7 대체 발송) — 아침이 점수 두 표까지 9표를 반영한다."""
    main, stg = files
    assert tables_for(main, D, "morning") == TABLES
    snapshot(main, stg)
    _fake_compat(stg, basis="morning", tables=tuple(t for t in TABLES if t not in SCORE_TABLES))
    with pytest.raises(V3PostGateError, match="score_history"):
        apply(stg, main, D, "morning")


def test_failed_evening_record_does_not_count(files) -> None:
    main, stg = files
    con = sqlite3.connect(str(main), isolation_level=None)
    _write_meta(con, ExportResult(
        date=D_ISO, basis="evening", target="x", exported_at="2026-10-08T07:30:00+00:00",
        window={}, consensus_asof=D_ISO, status="failed", failed_table="stocks"))
    con.close()
    assert tables_for(main, D, "morning") == TABLES


# ── T-35 순서 가드 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(("newer", "run"), [
    (("2026-10-09", "evening"), "evening"),        # 다음 날 반영이 이미 있다
    ((D_ISO, "morning"), "evening"),               # 같은 날 아침 확정이 이미 있다
    (("2026-10-09", "evening"), "morning"),
])
def test_late_older_run_is_refused_and_main_unchanged(files, newer, run) -> None:
    main, stg = files
    _main_record(main, *newer, "2026-10-09T23:59:00.000000+00:00")
    snapshot(main, stg)
    _fake_compat(stg, basis=run, tables=tables_for(main, D, run))
    before = _sha(main)
    with pytest.raises(V3PostGateError, match="T-35"):
        apply(stg, main, D, run)
    assert _sha(main) == before
    # 재생은 --allow-older 로 통과한다
    assert apply(stg, main, D, run, allow_older=True).ok


def test_same_date_and_basis_rerun_is_allowed(files) -> None:
    main, stg = files
    _main_record(main, D_ISO, "evening", "2026-10-08T07:00:00.000000+00:00")
    snapshot(main, stg)
    _fake_compat(stg)
    assert apply(stg, main, D, "evening").ok


# ── COMMIT 표식 ──────────────────────────────────────────────────────────────
def test_commit_flag_only_after_commit(files, tmp_path: Path) -> None:
    main, stg = files
    flag = tmp_path / "committed"
    snapshot(main, stg)
    _fake_compat(stg, skipped={"daily_prices": 1})
    with pytest.raises(V3PostGateError):
        apply(stg, main, D, "evening", commit_flag=flag)
    assert not flag.exists()
    snapshot(main, stg)
    _fake_compat(stg)
    apply(stg, main, D, "evening", commit_flag=flag)
    assert flag.exists()


# ── 그림자 ───────────────────────────────────────────────────────────────────
def test_shadow_runs_gate_and_never_writes_main(files) -> None:
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg)
    before = _sha(main)
    report = apply(stg, main, D, "evening", shadow=True)
    assert report.ok
    assert _sha(main) == before
    assert _rows(main, "SELECT count(*) FROM sqlite_master WHERE name='_compat_meta'") == [(0,)]


def test_shadow_gate_failure_also_raises(files) -> None:
    main, stg = files
    snapshot(main, stg)
    _fake_compat(stg, skipped={"daily_prices": 1})
    before = _sha(main)
    with pytest.raises(V3PostGateError):
        apply(stg, main, D, "evening", shadow=True)
    assert _sha(main) == before


# ── 통합: 진짜 compat export 를 스테이징에 → 반영 ──────────────────────────────
def test_real_compat_export_into_staging_then_apply(tmp_path: Path) -> None:
    import test_compat_export as tce
    eq_root, st_root = tce._make_roots(tmp_path / "roots")
    model_root = tmp_path / "model"
    tce._write_model_run(model_root, tce.D23_ISO, tce.MB_D23,
                         {tce.SCOPE: tce._scope_rows(tce.D23_ISO),
                          tce.V2: tce._v2_rows(tce.D23_ISO)})
    main = tmp_path / "v3" / "quant.db"
    main.parent.mkdir()
    con = sqlite3.connect(str(main))
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8") + MARKET_INDICES_DDL)
    # 창(730일) 밖 옛 행과 compat 이 안 쓰는 표 — 반영 뒤에도 그대로여야 한다
    con.execute("INSERT INTO daily_prices VALUES ('005930', '2020-01-02', 1, 1, 1, 7, 1, 1, 7.0)")
    con.execute("INSERT INTO market_indices VALUES ('001', '2026-09-23', 1, 1, 1, 2500, 1)")
    con.commit()
    con.close()
    stg = tmp_path / "staging.db"

    snapshot(main, stg)
    res = export(equity_root=eq_root, stage_root=st_root, date=tce.AS_OF, basis="morning",
                 target=stg, full=True, in_place=True, model_root=model_root)
    assert res.window["from_date"] == "2024-09-23"
    report = apply(stg, main, tce.AS_OF, "morning")

    assert report.ok, report.failures
    assert report.window == ("2024-09-23", "2026-09-23")
    for table in TABLES:
        assert report.counts[table] > 0, table
    assert _rows(main, "SELECT count(*) FROM score_history WHERE score_date=?",
                 (tce.D23_ISO,)) == [(len(tce.SCORE_CODES),)]
    assert _rows(main, "SELECT count(*) FROM daily_prices WHERE trade_date >= '2024-09-23'") == [
        (res.tables["daily_prices"].n_rows,)]
    assert _rows(main, "SELECT close FROM daily_prices WHERE trade_date='2020-01-02'") == [(7,)]
    assert _rows(main, "SELECT close FROM market_indices") == [(2500,)]
    meta = _rows(main, "SELECT date, basis, status, \"window\" FROM _compat_meta")
    assert len(meta) == 1 and meta[0][:3] == (tce.D23_ISO, "morning", "ok")
    assert json.loads(meta[0][3])["from_date"] == "2024-09-23"
