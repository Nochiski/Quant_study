"""서버 DB 조회 화면 — DuckDB UI 를 서버에서 띄우고 맥북 브라우저로 본다(SSH 터널).

데이터와 계산은 서버에 그대로 두고 화면만 맥북으로 끌어온다. 원장·stage·equity 를 한 화면의
표 목록으로 보여 준다. 전부 **읽기 전용**이다.

  원장   raw_<소스>  — 빌드용 **스냅샷 사본**(sqlite, 읽기 전용 파일)을 붙인다. 수집 중인
                       원본(`data/raw/*.db`)은 붙이지 않는다: `wisereport.db` 는 delete 저널이라
                       긴 조회가 18:05 수집의 쓰기를 막을 수 있다. 스냅샷은 하루 늦지만 아무와도
                       부딪히지 않는다.
  stage  stage.<표>  — 각 표 MANIFEST 의 current_build 파티션을 가리키는 뷰
  equity equity.<표> — 같은 방식. `_reject/` 는 제외. 보조 뷰 매크로는 `equity_views` 로 붙인다
  목록   main.catalog — 층·표·현재 판·행수·규칙 판본 한 표

사용(맥북에서 한 줄 — 끝낼 때 Ctrl+C, 터널이 닫히면 서버 쪽도 같이 끝난다):
  ssh -t -L 4213:localhost:4213 kael-server \\
      'cd ~/quant-ledger && .venv/bin/python scripts/db_browser.py'
  → 브라우저에서 http://localhost:4213
또는 `database/scripts/db_browser_mac.sh`(터널 + 브라우저 열기).

주의: 서버 메모리 15GB 중 빌드가 7GB 가까이 쓴다. 저녁 21:00~23:30 · 아침 08:10~09:40 에는
무거운 조회를 피한다. 세션은 기본 메모리 3GB · 스레드 2 로 묶는다. 빌드가 끝나 현재 판이 바뀌거나
GC 가 옛 판을 지우면 뷰가 깨질 수 있다 — 화면을 다시 켜면 새 판으로 잡힌다.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import sys
import threading
import time
from pathlib import Path

import duckdb

LAYERS = ("stage", "equity")


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _ident(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def watch_stdin(stop: dict) -> None:
    """stdin 이 파이프(터미널 없이 `ssh host cmd`)면 연결이 끊겨 EOF 가 올 때 멈춘다.

    `ssh -t` 로 붙으면 연결이 끊길 때 SIGHUP 이 오지만, 터미널 없이 붙으면 서버 쪽 프로세스가
    아무 신호도 못 받고 남는다(09-28 시험 실측). /dev/null·터미널이면 감시하지 않는다.
    """
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (OSError, ValueError):
        return
    if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
        return

    def run() -> None:
        try:
            while sys.stdin.buffer.read1(1024):
                pass
        except Exception:
            pass
        stop["flag"] = True

    threading.Thread(target=run, daemon=True).start()


def current_files(table_dir: Path) -> tuple[dict, list[str]] | None:
    """MANIFEST 의 current_build 레코드와 그 판의 parquet 경로 목록(절대경로). 판이 없으면 None."""
    mf = table_dir / "MANIFEST.json"
    if not mf.is_file():
        return None
    m = json.loads(mf.read_text(encoding="utf-8"))
    cur = m.get("current_build")
    rec = next((b for b in m.get("builds", []) if b.get("build_id") == cur), None)
    if rec is None:
        return None
    files: list[str] = []
    for p in rec.get("partitions") or []:
        path = table_dir / p["path"]
        if "_reject" in path.parts:
            continue
        if path.is_dir():
            files += sorted(str(f) for f in path.glob("*.parquet"))
        elif path.is_file():
            files.append(str(path))
    return (rec, files) if files else None


def build_views(con: duckdb.DuckDBPyConnection, root: Path, snapshot: str | None) -> list[tuple]:
    """원장 스냅샷 ATTACH + stage·equity 뷰 + main.catalog. 반환 = catalog 행."""
    rows: list[tuple] = []
    # 원장 — 스냅샷 사본
    snaps = sorted((root / "data" / "snapshots").glob("snap_*"))
    snap_dir = None
    if snapshot:
        snap_dir = root / "data" / "snapshots" / snapshot
    elif snaps:
        snap_dir = snaps[-1]
    if snap_dir is not None and snap_dir.is_dir():
        con.execute("LOAD sqlite")
        for db in sorted(snap_dir.glob("*.db")):
            alias = "raw_" + db.stem
            con.execute(f"ATTACH {_q(str(db))} AS {_ident(alias)} (TYPE sqlite, READ_ONLY)")
            n_tables = con.execute(
                "SELECT count(*) FROM duckdb_tables() WHERE database_name = ?", [alias],
            ).fetchone()[0]
            rows.append(("원장", alias, snap_dir.name, None, None, f"sqlite 표 {n_tables}개"))
    # stage · equity — 현재 판 뷰
    for layer in LAYERS:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {layer}")
        base = root / "data" / layer
        dirs = (p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_"))
        for table_dir in sorted(dirs):
            got = current_files(table_dir)
            if got is None:
                continue
            rec, files = got
            lst = "[" + ", ".join(_q(f) for f in files) + "]"
            con.execute(
                f"CREATE OR REPLACE VIEW {layer}.{_ident(table_dir.name)} AS "
                f"SELECT * FROM read_parquet({lst}, hive_partitioning = true, "
                f"union_by_name = true)")
            rows.append((layer, table_dir.name, rec.get("build_id"), rec.get("n_rows"),
                         rec.get("rules_version"), rec.get("max_available_date")))
    cat = root / "data" / "equity" / "equity.duckdb"
    if cat.is_file():
        con.execute(f"ATTACH {_q(str(cat))} AS equity_views (READ_ONLY)")
    con.execute("CREATE OR REPLACE TABLE main.catalog (layer VARCHAR, name VARCHAR, build VARCHAR, "
                "n_rows BIGINT, rules_version VARCHAR, note VARCHAR)")
    con.executemany("INSERT INTO main.catalog VALUES (?, ?, ?, ?, ?, ?)",
                    [(*r[:5], None if r[5] is None else str(r[5])) for r in rows])
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="서버 DB 조회 화면(DuckDB UI, 읽기 전용)")
    ap.add_argument("--root", type=Path,
                    default=Path(os.environ.get("QL_HOME", "/home/kael/quant-ledger")))
    ap.add_argument("--port", type=int, default=4213)
    ap.add_argument("--memory", default="3GB")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--snapshot", help="원장 스냅샷 id (기본 = 가장 최근 스냅샷)")
    ap.add_argument("--no-ui", action="store_true", help="뷰만 만들고 목록을 찍고 끝낸다(점검용)")
    a = ap.parse_args(argv)

    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit = {_q(a.memory)}")
    con.execute(f"SET threads = {int(a.threads)}")
    rows = build_views(con, a.root, a.snapshot)
    by_layer: dict[str, int] = {}
    for r in rows:
        by_layer[r[0]] = by_layer.get(r[0], 0) + 1
    print("조회 준비: " + " · ".join(f"{k} {v}개" for k, v in by_layer.items()), flush=True)
    if a.no_ui:
        return 0

    print("화면 확장 준비 중(처음 한 번은 내려받느라 수십 초 걸린다)…", flush=True)
    con.execute("INSTALL ui")
    con.execute("LOAD ui")
    con.execute(f"SET ui_local_port = {int(a.port)}")
    con.execute("CALL start_ui_server()")
    print(f"화면 켜짐 — 맥북 브라우저에서 http://localhost:{a.port} (끝내려면 Ctrl+C)", flush=True)

    stop = {"flag": False}

    def _stop(*_: object) -> None:
        stop["flag"] = True

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _stop)
    watch_stdin(stop)
    while not stop["flag"]:
        time.sleep(1)
    try:
        con.execute("CALL stop_ui_server()")
    finally:
        con.close()
    print("화면 종료", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
