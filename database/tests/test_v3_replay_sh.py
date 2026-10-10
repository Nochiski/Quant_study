"""scripts/v3_replay.sh — v3 날짜별 사본 복사 → compat 제자리 반영 → 대조기, 날짜별 JSON·요약 tsv (QL-G).

진짜 스크립트·진짜 compat·진짜 대조기를 돌린다. 연구 루트는 임시 폴더다: `test_compat_export` 의 합성 equity·stage 판
(09-23 아침 `m_` 판)을 `data/equity`·`data/stage` 에, 09-23 모델 판을 `data/model` 에, 그날 인계 이력을
`data/deliver/history/20260923_morning.json` 에, 판정 달력(주말만 휴장)을 `data/calendar` 에 둔다. `.venv/bin/python` 은
이 인터프리터로 넘기는 대역이다. v3 사본 폴더에는 09-23·09-24 사본(읽기 전용 0444 — 서버 보존본과 같다)이 있다.
  09-22  사본 없음                          → no_copy
  09-23  반영·대조(다음 거래일 사본 09-24)   → 대조 보고(사본이 얕아 compat 에만 있는 행이 많다 — rc 1)
  09-24  인계 이력 없음                      → reflect_failed:export
드리프트(리뷰 MINOR-2)는 가짜 python 이 compat 하위 명령의 인자를 받아 적게 하고 v3_replay.sh 와 v3_post.sh 를 같은
날로 돌려 단계·인자 목록을 맞댄다(경로 값과 알려진 차이 셋을 정규화한 뒤).
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
    # V3R_CWD_LOG 를 주면 자기 cwd 를 받아 적는다(MINOR-1 — DuckDB 는 넘칠 때 cwd 의 .tmp 에 쓴다)
    py.write_text(f'#!/bin/sh\n[ -n "${{V3R_CWD_LOG:-}}" ] && pwd -P >> "$V3R_CWD_LOG"\n'
                  f'exec "{sys.executable}" "$@"\n', encoding="utf-8")
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


def _run(*args: str, cwd: Path, **extra_env: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("QL_")} | extra_env
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


def test_running_from_the_research_root_writes_nothing_there(root: Path, copies: Path, tmp_path: Path) -> None:
    """리뷰 MINOR-1 — DuckDB 는 메모리를 넘칠 때 cwd 의 `.tmp` 를 임시 폴더로 쓴다(TMPDIR 무시, 닫을 때 지운다). 작은
    픽스처로는 넘치지 않으므로 python 들이 어느 cwd 에서 돌았는지를 직접 본다 — 전부 출력 루트 아래여야 한다."""
    before = _state(root)
    log = tmp_path / "cwd.log"
    res = _run("--v3-copies", str(copies), "--dates", "20260923", "--out", str(tmp_path / "out"),
               "--root", ".", "--full", cwd=root, V3R_CWD_LOG=str(log))
    assert res.returncode == 1, res.stdout + res.stderr
    assert _state(root) == before
    cwds = set(log.read_text(encoding="utf-8").split())
    assert cwds == {str((tmp_path / "out" / "tmp").resolve())}
    assert not (root / ".tmp").exists()
    assert json.loads((tmp_path / "out" / "20260923" / "v3_replay.json").read_text(encoding="utf-8"))["tool"]


def test_allow_current_builds_falls_back_and_is_recorded(root: Path, copies: Path, tmp_path: Path) -> None:
    """T-45 — 인계 이력의 판이 보관 판 밖이면 기본은 반영 실패, `--allow-current-builds` 면 현판으로 대체하고 JSON 에 남긴다."""
    r2 = tmp_path / "ql"
    shutil.copytree(root, r2, symlinks=True)
    hist = r2 / "data" / "deliver" / "history" / f"{tce.AS_OF}_morning.json"
    doc = json.loads(hist.read_text(encoding="utf-8"))
    doc["equity_builds"]["fin_std"] = "m_20250101T000000_000000Z"           # GC 로 사라진 판
    hist.write_text(json.dumps(doc), encoding="utf-8")
    strict = _run("--v3-copies", str(copies), "--dates", "20260923", "--out", str(tmp_path / "a"),
                  "--root", str(r2), "--full", cwd=tmp_path)
    assert strict.returncode == 1
    a = json.loads((tmp_path / "a" / "20260923" / "v3_replay.json").read_text(encoding="utf-8"))
    assert a["status"] == "reflect_failed:export"
    loose = _run("--v3-copies", str(copies), "--dates", "20260923", "--out", str(tmp_path / "b"),
                 "--root", str(r2), "--full", "--allow-current-builds", cwd=tmp_path)
    assert loose.returncode == 1, loose.stdout + loose.stderr
    b = json.loads((tmp_path / "b" / "20260923" / "v3_replay.json").read_text(encoding="utf-8"))
    assert b["tool"] == "compat.v3_replay" and b["inputs"]["builds_fallback"] == ["fin_std"]
    assert "allow_current_builds=current" in (tmp_path / "b" / "run.txt").read_text(encoding="utf-8")


# ── 드리프트 — v3_post.sh 와 같은 compat 단계·인자(리뷰 MINOR-2) ─────────────────────
_PATH_FLAGS = {"--v3-db", "--out", "--staging", "--equity-root", "--stage-root", "--model-root", "--target",
               "--calendar-dir", "--commit-flag"}
# 알려진 차이 — 실행기에만: export --allow-older·--calendar-dir, apply --allow-older / v3_post.sh 에만: apply --commit-flag
_KNOWN = {("export", "--allow-older"), ("export", "--calendar-dir"), ("apply", "--allow-older"),
          ("apply", "--commit-flag")}


def _argv_python(path: Path, log: Path, extra: str = "") -> None:
    """compat 하위 명령은 인자만 받아 적고 성공한다(v3-tables 는 표 목록을 낸다). 그 밖(대조기 등)은 진짜 python."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""#!/bin/bash
if [ "$1" = "-m" ] && [ "$2" = "compat" ]; then
  echo "$*" >> "{log}"
  {extra}
  [ "$3" = "v3-tables" ] && echo "daily_prices,stocks,score_history"
  exit 0
fi
exec "{sys.executable}" "$@"
""", encoding="utf-8")
    path.chmod(0o755)


def _calls(log: Path) -> dict[str, list[tuple[str, str | None]]]:
    out: dict[str, list[tuple[str, str | None]]] = {}
    for line in log.read_text(encoding="utf-8").splitlines():
        toks = line.split()
        assert toks[:2] == ["-m", "compat"], line
        rest, args, i = toks[3:], [], 0
        while i < len(rest):
            flag, val = rest[i], None
            if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                val, i = rest[i + 1], i + 1
            i += 1
            args.append((flag, "<path>" if flag in _PATH_FLAGS else
                         Path(val).name if flag == "--builds-from" and val else val))
        out[toks[2]] = args
    return out


def _drift_root(tmp_path: Path, log: Path, extra: str = "") -> Path:
    r = tmp_path / "droot"
    cal = r / "data" / "calendar"
    cal.mkdir(parents=True)
    days = (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365))
    hol = [x.strftime("%Y%m%d") for x in days if x.weekday() >= 5]
    (cal / "kis_holidays_2026.json").write_text(json.dumps({"year": 2026, "holidays": hol}), encoding="utf-8")
    _argv_python(r / ".venv" / "bin" / "python", log, extra)
    return r


def test_runner_calls_compat_like_v3_post(copies: Path, tmp_path: Path) -> None:
    runner_log, post_log = tmp_path / "argv_runner.log", tmp_path / "argv_post.log"
    droot = _drift_root(tmp_path, runner_log)
    res = _run("--v3-copies", str(copies), "--dates", "20260923", "--out", str(tmp_path / "out"),
               "--root", str(droot), cwd=tmp_path)
    assert res.returncode == 1                                     # 대조기는 진짜라 반영 기록이 없어 입력 오류
    home = tmp_path / "home"
    (home / "scripts").mkdir(parents=True)
    shutil.copy(DB_ROOT / "scripts" / "v3_post.sh", home / "scripts" / "v3_post.sh")
    (home / "scripts" / "notify.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (home / "scripts" / "notify.sh").chmod(0o755)
    _argv_python(home / ".venv" / "bin" / "python", post_log)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "flock").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fakebin / "flock").chmod(0o755)
    v3 = tmp_path / "v3.db"
    shutil.copy(copies / "quant_20260923.db", v3)
    env = {k: v for k, v in os.environ.items() if not k.startswith("QL_")}
    env.update(QL_HOME=str(home), QL_V3_LOCK_FILE=str(tmp_path / "lock"), PATH=f"{fakebin}:{env['PATH']}")
    post = subprocess.run(["bash", str(home / "scripts" / "v3_post.sh"), "--date", tce.AS_OF, "--basis", "morning",
                           "--v3-db", str(v3), "--builds-from",
                           str(droot / "data" / "deliver" / "history" / f"{tce.AS_OF}_morning.json")],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert post.returncode == 0, post.stdout + post.stderr
    runner, posted = _calls(runner_log), _calls(post_log)
    assert list(runner) == list(posted) == ["stage", "v3-tables", "export", "apply"]
    assert ("--allow-older", None) in runner["export"] and ("--calendar-dir", "<path>") in runner["export"]
    assert ("--allow-older", None) in runner["apply"] and ("--commit-flag", "<path>") in posted["apply"]
    norm = {name: {s: [a for a in args if (s, a[0]) not in _KNOWN] for s, args in calls.items()}
            for name, calls in (("runner", runner), ("post", posted))}
    assert norm["runner"] == norm["post"]
    assert ("--builds-from", f"{tce.AS_OF}_morning.json") in runner["export"]
    assert all(flag != "--builds-from-missing" for flag, _ in runner["export"])


def test_allow_current_builds_passes_builds_from_missing(copies: Path, tmp_path: Path) -> None:
    log = tmp_path / "argv.log"
    droot = _drift_root(tmp_path, log)
    _run("--v3-copies", str(copies), "--dates", "20260923", "--out", str(tmp_path / "out"), "--root", str(droot),
         "--allow-current-builds", cwd=tmp_path)
    assert ("--builds-from-missing", "current") in _calls(log)["export"]


def test_a_step_that_touches_the_original_copy_is_rc_2(copies: Path, tmp_path: Path) -> None:
    """원본 sha 대조 — 반영 단계가 원본 사본을 건드리면(가짜 python 이 stage 에서 한 바이트 덧붙인다) X-1 위반 rc 2."""
    orig = copies / "quant_20260923.db"
    droot = _drift_root(tmp_path, tmp_path / "argv.log",
                        extra=f'[ "$3" = "stage" ] && chmod u+w "{orig}" && printf x >> "{orig}"')
    res = _run("--v3-copies", str(copies), "--dates", "20260923", "--out", str(tmp_path / "out"),
               "--root", str(droot), cwd=tmp_path)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "X-1 위반" in res.stderr
    row = (tmp_path / "out" / "hashes.tsv").read_text(encoding="utf-8").splitlines()[1].split("\t")
    assert row[1] != row[2]
