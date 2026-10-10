"""scripts/v3_replay.sh — v3 날짜별 사본 복사 → compat 제자리 반영 → 대조기, 날짜별 JSON·요약 tsv (QL-G).

진짜 스크립트·진짜 compat·진짜 대조기를 돌린다. 연구 루트는 임시 폴더다: `test_compat_export` 의 합성 equity·stage 판
(09-23 아침 `m_` 판)을 `data/equity`·`data/stage` 에, 09-23 모델 판을 `data/model` 에, 그날 인계 이력을
`data/deliver/history/20260923_morning.json` 에, 판정 달력(주말만 휴장)을 `data/calendar` 에 둔다. `.venv/bin/python` 은
이 인터프리터로 넘기는 대역이다. v3 사본 폴더에는 09-23·09-24 사본(읽기 전용 0444 — 서버 보존본과 같다)이 있다.
  09-22  사본 없음                          → no_copy
  09-23  반영·대조(다음 거래일 사본 09-24)   → 대조 보고(사본이 얕아 compat 에만 있는 행이 많다 — rc 1)
  09-24  인계 이력 없음                      → reflect_failed:export
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import test_compat_export as tce
from compat.quant_db import SCHEMA_SQL_PATH

DB_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = DB_ROOT / "scripts" / "v3_replay.sh"
MARKET_INDICES_DDL = """
CREATE TABLE IF NOT EXISTS market_indices (
    index_code TEXT NOT NULL, trade_date TEXT NOT NULL, open INTEGER NOT NULL,
    high INTEGER NOT NULL, low INTEGER NOT NULL, close INTEGER NOT NULL, volume INTEGER,
    PRIMARY KEY (index_code, trade_date)
) WITHOUT ROWID;
"""
EQUITY_TABLES = ("price_daily", "price_adj_daily", "universe_daily", "security", "flow_daily", "fin_std")
STAGE_TABLES = ("stg_consensus_matrix", "stg_consensus_annual", "stg_fin_wise", "stg_master_daily")


@pytest.fixture(scope="module")
def root(tmp_path_factory) -> Path:
    """연구 루트 — 읽기만 해야 한다(테스트가 전·후 목록·mtime 을 대조한다)."""
    base = tmp_path_factory.mktemp("src")
    eq, st = tce._make_roots(base)
    r = base / "ql"
    (r / "data").mkdir(parents=True)
    shutil.move(str(eq), r / "data" / "equity")
    shutil.move(str(st), r / "data" / "stage")
    tce._write_model_run(r / "data" / "model", tce.D23_ISO, tce.MB_D23,
                         {tce.SCOPE: tce._scope_rows(tce.D23_ISO), tce.V2: tce._v2_rows(tce.D23_ISO)})
    hist = r / "data" / "deliver" / "history" / f"{tce.AS_OF}_morning.json"
    hist.parent.mkdir(parents=True)
    hist.write_text(json.dumps({"date": tce.D23_ISO, "basis": "morning",
                                "equity_builds": dict.fromkeys(EQUITY_TABLES, tce.EQ_BUILD),
                                "stage_builds": dict.fromkeys(STAGE_TABLES, tce.ST_BUILD)}), encoding="utf-8")
    cal = r / "data" / "calendar"
    cal.mkdir()
    days = (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365))
    hol = [x.strftime("%Y%m%d") for x in days if x.weekday() >= 5]
    (cal / "kis_holidays_2026.json").write_text(json.dumps({"year": 2026, "holidays": hol}), encoding="utf-8")
    py = r / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    py.chmod(0o755)
    return r


def _copy(path: Path) -> Path:
    """v3 사본 — compat 9표 DDL + compat 밖 표, WAL(실물 v3 와 같다), 읽기 전용."""
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8") + MARKET_INDICES_DDL)
    con.execute("INSERT INTO daily_prices VALUES ('005930', '2020-01-02', 1, 1, 1, 7, 1, 1, 7.0)")
    con.execute("INSERT INTO market_indices VALUES ('001', ?, 1, 1, 1, 2500, 1)", (tce.D23_ISO,))
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    for side in ("-wal", "-shm"):
        Path(f"{path}{side}").unlink(missing_ok=True)
    path.chmod(0o444)
    return path


@pytest.fixture
def copies(tmp_path: Path) -> Path:
    d = tmp_path / "v3_daily"
    d.mkdir()
    _copy(d / "quant_20260923.db")
    _copy(d / "quant_20260924.db")
    return d


def _state(d: Path) -> dict[str, tuple]:
    """폴더 아래 파일마다 (크기, mtime, sha256) — 쓰기 0 을 본다."""
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = (p.stat().st_size, p.stat().st_mtime_ns,
                                          hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("QL_")}
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=600)


def test_replays_each_day_into_isolated_out_without_touching_sources(root: Path, copies: Path,
                                                                     tmp_path: Path) -> None:
    before_copies, before_root = _state(copies), _state(root)
    out = tmp_path / "out"
    res = _run("--v3-copies", str(copies), "--dates", "20260922-20260924", "--out", str(out),
               "--root", str(root), "--full", cwd=tmp_path)
    assert res.returncode == 1, res.stdout + res.stderr          # ok 아닌 날이 있다
    # X-1 — 원본 사본 폴더·연구 루트에 쓰기 0(사이드카 -shm·-wal 도 생기지 않는다)
    assert _state(copies) == before_copies
    assert _state(root) == before_root
    assert stat.S_IMODE((copies / "quant_20260923.db").stat().st_mode) == 0o444
    # 날짜별 출력
    docs = {d: json.loads((out / d / "v3_replay.json").read_text(encoding="utf-8"))
            for d in ("20260922", "20260923", "20260924")}
    assert docs["20260922"]["status"] == "no_copy"
    assert docs["20260924"]["status"] == "reflect_failed:export"
    full = docs["20260923"]
    assert (full["tool"], full["date"], full["basis"]) == ("compat.v3_replay", tce.D23_ISO, "morning")
    assert full["inputs"]["next_v3_db"].endswith("20260923/v3_next.db")
    assert full["inputs"]["compat_window"]["full"] is True
    assert list(full["tables"]) == ["daily_prices", "stocks", "investor_detail_flows", "consensus_revision_daily",
                                    "consensus_revision_compare", "consensus_annual", "financial_summary",
                                    "score_history", "score_history_v2"]
    assert full["tables"]["score_history"]["categories"]["score_universe"]["rows"] == len(tce.SCORE_CODES)
    assert full["rc"] == 1 and full["tables"]["daily_prices"]["other"]["only_in_compat"] > 0
    log = (out / "20260923" / "run.log").read_text(encoding="utf-8")
    assert "반영 표: daily_prices,stocks," in log and "v3_post 게이트 통과" in log
    assert "──── " in log and "] apply" in log
    failed = (out / "20260924" / "run.log").read_text(encoding="utf-8")
    assert "] export" in failed and "] apply" not in failed and "인계 이력(--builds-from)이 없다" in failed
    # 요약·해시
    rows = [ln.split("\t") for ln in (out / "summary.tsv").read_text(encoding="utf-8").splitlines()]
    assert [r[:3] for r in rows[1:]] == [["20260922", "no_copy", "1"], ["20260923", "other", "1"],
                                         ["20260924", "reflect_failed:export", "1"]]
    hashes = [ln.split("\t") for ln in (out / "hashes.tsv").read_text(encoding="utf-8").splitlines()[1:]]
    assert len(hashes) == 3 and all(b == a for _, b, a in hashes)    # 09-23 은 다음 거래일 사본까지 둘
    # db 사본은 대조 뒤 지운다(--keep-db 없음)
    assert not list(out.rglob("*.db*"))


def test_keep_db_leaves_reflected_copy(root: Path, copies: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    res = _run("--v3-copies", str(copies), "--dates", "20260923", "--out", str(out), "--root", str(root),
               "--full", "--keep-db", cwd=tmp_path)
    assert res.returncode == 1, res.stdout + res.stderr
    kept = {p.name for p in (out / "20260923").glob("*.db")}
    assert kept == {"v3.db", "compat.db", "v3_next.db"}
    con = sqlite3.connect(f"{(out / '20260923' / 'compat.db').as_uri()}?mode=ro", uri=True)
    n = con.execute("SELECT count(*) FROM _compat_meta WHERE date = ? AND status = 'ok'", (tce.D23_ISO,)).fetchone()
    con.close()
    assert n == (1,)                                               # apply 가 사본에 반영 기록을 옮겼다


@pytest.mark.parametrize("case", ["inside_root", "inside_copies", "ancestor", "not_empty", "dangling_link",
                                  "bad_dates", "no_python"])
def test_refuses_unsafe_out_and_bad_args(root: Path, copies: Path, tmp_path: Path, case: str) -> None:
    out = tmp_path / "out"
    args = {"--v3-copies": str(copies), "--dates": "20260923", "--out": str(out), "--root": str(root)}
    if case == "inside_root":
        args["--out"] = str(root / "data" / "replay")
    elif case == "inside_copies":
        args["--out"] = str(copies / "replay")
    elif case == "ancestor":
        args["--out"] = str(root.parent)
    elif case == "not_empty":
        out.mkdir()
        (out / "x").write_text("x", encoding="utf-8")
    elif case == "dangling_link":
        (tmp_path / "link").symlink_to(root / "data" / "nowhere")
        args["--out"] = str(tmp_path / "link" / "out")
    elif case == "bad_dates":
        args["--dates"] = "2026-09-23"
    else:
        args["--root"] = str(tmp_path)
        (tmp_path / "data").mkdir()
    before = _state(root)
    res = _run(*[x for kv in args.items() for x in kv], cwd=tmp_path)
    assert res.returncode == 2, res.stdout + res.stderr
    assert res.stderr.startswith("v3_replay:")
    assert _state(root) == before
    assert not (root / "data" / "replay").exists() and not (copies / "replay").exists()
