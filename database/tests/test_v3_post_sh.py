"""scripts/v3_post.sh — 스테이징 → compat --in-place → 게이트 → 9표 한 트랜잭션 → daily_post (QL-F).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 QL-F. HOME 대신 `QL_HOME` 을 임시 폴더로 두고 진짜
스크립트·진짜 python(`.venv/bin/python` → 이 인터프리터)·진짜 compat 을 돌린다. compat 원천은
`test_compat_export` 의 합성 equity·stage 판과 모델 판(09-23 — 아침 `m_` 판·morning 모델 판, daily_post 를 부르는
장 마감 경로는 `e_` 판·evening 모델 판)이다. 대역은 `notify.sh`
(줄마다 "등급|제목|본문")와 맥에 없는 `flock`(인자를 flock.txt 에 적고 -n 은 FLOCK_N_RC, 대기형은
FLOCK_WAIT_RC 로 끝난다)뿐이다. 락 파일은 늘 `QL_V3_LOCK_FILE` 임시 경로라 운영 락
(/tmp/kael_v3_daily_all.lock)을 건드리지 않는다. 실물 flock 테스트는 flock 이 있을 때만(서버·CI 우분투) 돈다.
v3 본 파일은 compat 이 쓰는 9표 DDL(`v3_schema.sql`) + compat 밖 표 `market_indices` 다. 얕은 본 파일이라
compat 은 `--full` 로 돈다(730일 창).
장 마감 판(QL-D)은 판이 D'(09-22)까지이고 T(09-23) 가격·수급 행을 원장에서 만든다 — 임시 홈의
`data/raw/postclose.db`·`kiwoom.db`(실물 `daily.postclose`·`daily.kw_daily` 코드로 만든다)와 판정
달력 `data/calendar/kis_holidays_2026.json` 을 둔다(셸 기본 경로).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import pytest
import test_compat_export as tce
from compat import v3_restore
from compat.quant_db import SCHEMA_SQL_PATH, ExportResult, TableResult, _write_meta
from compat.v3_post import SCORE_TABLES, TABLES
from daily import kw_daily, postclose

DB_ROOT = Path(__file__).resolve().parents[1]
D = tce.AS_OF                       # 20260923
D_ISO = tce.D23_ISO
MARKET_INDICES_DDL = """
CREATE TABLE IF NOT EXISTS market_indices (
    index_code TEXT NOT NULL, trade_date TEXT NOT NULL, open INTEGER NOT NULL,
    high INTEGER NOT NULL, low INTEGER NOT NULL, close INTEGER NOT NULL, volume INTEGER,
    PRIMARY KEY (index_code, trade_date)
) WITHOUT ROWID;
"""
_FLOCK = """#!/usr/bin/env bash
echo "$*" >> "$FLOCK_LOG"
if [ "$1" = "-n" ]; then exit "${FLOCK_N_RC:-0}"; fi
exit "${FLOCK_WAIT_RC:-0}"
"""


class Run(NamedTuple):
    rc: int
    out: str
    notify: list[str]
    flock: list[str]
    elapsed: float


EVENING_BUILD = "e_20260923T120000_000000Z"     # 장 마감 판 equity·stage 판 접두(R5)


def _model_root(base: Path, basis: str = "morning") -> Path:
    """09-23 모델 판 — evening 이면 같은 판을 `_runs/<D>_evening.json` 으로 둔다(파일 규약만 다르다)."""
    root = base / "model"
    tce._write_model_run(root, D_ISO, tce.MB_D23, {tce.SCOPE: tce._scope_rows(D_ISO),
                                                   tce.V2: tce._v2_rows(D_ISO)})
    if basis == "evening":
        for src, dst in ((root / "_runs" / f"{D}_morning.json", root / "_runs" / f"{D}_evening.json"),
                         (root / "latest_morning.json", root / "latest_evening.json")):
            payload = json.loads(src.read_text(encoding="utf-8"))
            payload["basis"] = "evening"
            dst.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            src.unlink()
    return root


@pytest.fixture(scope="module")
def sources(tmp_path_factory) -> tuple[Path, Path, Path]:
    """(equity_root, stage_root, model_root) — 게이트를 통과하는 원천."""
    base = tmp_path_factory.mktemp("src")
    return (*tce._make_roots(base), _model_root(base))


def _evening_roots(base: Path, *, bad_volume: bool = False) -> tuple[Path, Path, Path]:
    """장 마감 판(`e_` 판 · evening 모델 판) — 판은 D'(09-22)까지다(QL-D 이음매): KRX 행·유니버스·수급에
    T(09-23)가 없고, 09-23 에만 있던 종목(스팩·필러 등)은 D' 로 옮긴다. T 의 가격·수급 행은 임시 홈의
    원장(`_ledgers`)에서 온다. `bad_volume` 이면 daily_prices 1행(005930 09-21)의 거래량이 비고 필러 30종목의
    D' 행을 더한다 — compat 5% 허용(1/36)은 통과, 제자리 게이트 0건은 실패."""
    fillers = tce._filler_tickers(tce.N_FILLER)
    prices = [r for r in tce._price_rows(bad_volume=bad_volume)
              if not (r["basis"] == "krx" and r["date"] == tce.D23)]
    if bad_volume:
        prices += [tce._price_row(t, tce.D22, 10_000, value=1_000_000, mktcap=1_000_000_000)
                   for t in fillers[:30]]
    uni = [tce._uni_row(t, d, "KOSPI", "common") for t in tce.REAL for d in (tce.D21, tce.D22)]
    uni.append(tce._uni_row(tce.SPAC, tce.D22, "KOSDAQ", "spac"))
    uni += [tce._uni_row(t, tce.D22, m, s) for t, m, s in tce.EXCLUDED]
    uni += [tce._uni_row(t, tce.D22, "KOSPI", "common") for t in fillers]
    roots = tce._make_roots(base, eq_build=EVENING_BUILD, price_rows=prices,
                            adj_rows=tce._adj_rows(price_rows=prices), universe_rows=uni,
                            flow_rows=[r for r in tce._flow_rows() if r["date"] != tce.D23])
    return (*roots, _model_root(base, "evening"))


@pytest.fixture(scope="module")
def evening_sources(tmp_path_factory) -> tuple[Path, Path, Path]:
    """장 마감 판 — daily_post 를 부르는 경로(`_evening_roots`)."""
    return _evening_roots(tmp_path_factory.mktemp("eve"))


@pytest.fixture(scope="module")
def evening_skip_sources(tmp_path_factory) -> tuple[Path, Path, Path]:
    """장 마감 판 + 필수 열이 빈 daily_prices 1행(`_evening_roots(bad_volume=True)`)."""
    return _evening_roots(tmp_path_factory.mktemp("eveskip"), bad_volume=True)


def _ledgers(raw: Path) -> tuple[Path, Path]:
    """장 마감 판 T 행 원장 두 개 — postclose 에 보통주 2종목의 T 행(기준가 = D' 종가 — 조정가가
    선다), 21:05 키움 원장은 표만(16:00 컷오프로 넘긴 종목이 없는 날)."""
    raw.mkdir(parents=True, exist_ok=True)
    pc, kw = raw / "postclose.db", raw / "kiwoom.db"
    con = postclose.connect(pc)
    try:
        for ticker, cur, pred in (("005930", "+70300", "+200"), ("000660", "-71000", "-100")):
            row = {"dt": D, "cur_prc": cur, "pred_pre": pred, "acc_trde_prica": "1000",
                   **{k: "-12" for k in kw_daily.FLOW_KEYS}}
            postclose.insert_first(con, list(postclose.COLS), ticker, [row],
                                   collected_at="2026-09-23T06:45:00",
                                   fetched_at="2026-09-23T06:41:00", price_valid=True)
    finally:
        con.close()
    con = sqlite3.connect(kw)
    try:
        kw_daily.ensure_table(con, kw_daily.TRS["ka10060"].table, list(postclose.COLS))
    finally:
        con.close()
    return pc, kw


@pytest.fixture(scope="module")
def skip_sources(tmp_path_factory) -> tuple[Path, Path, Path]:
    """daily_prices 36행 중 1행(005930 09-21)의 open 이 비었다 — compat 5% 허용(2.8%)은 통과, 게이트 0건은 실패."""
    base = tmp_path_factory.mktemp("skip")
    fillers = tce._filler_tickers(tce.N_FILLER)
    rows = tce._price_rows(bad_volume=True) + [
        tce._price_row(t, tce.D23, 10_000, value=1_000_000, mktcap=1_000_000_000)
        for t in fillers[:30]]
    return (*tce._make_roots(base, price_rows=rows, adj_rows=tce._adj_rows(price_rows=rows)),
            _model_root(base))


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "ql"
    (home / "scripts").mkdir(parents=True)
    (home / ".venv" / "bin").mkdir(parents=True)
    (home / "fakebin").mkdir()
    shutil.copy(DB_ROOT / "scripts" / "v3_post.sh", home / "scripts" / "v3_post.sh")
    (home / "src").symlink_to(DB_ROOT / "src")
    stubs = {home / ".venv/bin/python": f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
             home / "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             home / "fakebin/flock": _FLOCK}
    for p, body in stubs.items():
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    # 장 마감 판(QL-D) — 판정 달력(주말만 휴장)과 T 행 원장을 셸 기본 경로에
    cal = home / "data" / "calendar"
    cal.mkdir(parents=True)
    days = (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365))
    hol = [x.strftime("%Y%m%d") for x in days if x.weekday() >= 5]
    (cal / "kis_holidays_2026.json").write_text(json.dumps({"year": 2026, "holidays": hol}),
                                                encoding="utf-8")
    _ledgers(home / "data" / "raw")
    return home


def _main_db(tmp_path: Path) -> Path:
    main = tmp_path / "v3" / "quant.db"
    main.parent.mkdir(parents=True)
    con = sqlite3.connect(str(main))
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8") + MARKET_INDICES_DDL)
    con.execute("INSERT INTO daily_prices VALUES ('005930', '2020-01-02', 1, 1, 1, 7, 1, 1, 7.0)")
    # 수급 표도 가격 표처럼 이력이 730일 창 앞에서 시작한다(실물 v3 모양) — 첫 `--full` 이 빈 수급 표에 09-21 부터
    # 쓰면 다음 `--full` 이 제자리 하한(T-46 — 창 시작 < 이력 시작)에 걸려 이어 돌리는 시나리오가 막힌다
    con.execute("INSERT INTO investor_detail_flows (stock_code, trade_date, individual) "
                "VALUES ('005930', '2020-01-02', -1)")
    con.execute("INSERT INTO market_indices VALUES ('001', ?, 1, 1, 1, 2500, 1)", (D_ISO,))
    con.commit()
    con.close()
    return main


def _run(tmp_path: Path, src: tuple[Path, Path, Path], *args: str, stub_flock: bool = True,
         today: str | None = D, **env_extra: str) -> Run:
    home = tmp_path / "ql"
    env = dict(os.environ, QL_HOME=str(home), TMPDIR=str(tmp_path),
               QL_EQUITY_ROOT=str(src[0]), QL_STAGE_ROOT=str(src[1]), QL_MODEL_ROOT=str(src[2]),
               QL_V3_LOCK_FILE=str(tmp_path / "v3.lock"), FLOCK_LOG=str(tmp_path / "flock.txt"))
    if stub_flock:
        env["PATH"] = f"{home / 'fakebin'}:{os.environ['PATH']}"
    for k in ("QL_V3_DB", "QL_V3_POST_CMD", "QL_V3_POST_TODAY"):
        env.pop(k, None)
    if today is not None:
        env["QL_V3_POST_TODAY"] = today
    env.update(env_extra)
    t0 = time.monotonic()
    p = subprocess.run(["bash", str(home / "scripts" / "v3_post.sh"), *args], env=env,
                       capture_output=True, text=True, timeout=180, check=False)
    elapsed = time.monotonic() - t0

    def lines(path: Path) -> list[str]:
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    return Run(p.returncode, p.stdout + p.stderr, lines(home / "notify.txt"),
               lines(tmp_path / "flock.txt"), elapsed)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _q(path: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _levels(r: Run, level: str) -> list[str]:
    return [n for n in r.notify if n.startswith(level + "|")]


@pytest.fixture
def env(tmp_path: Path) -> tuple[Path, Path]:
    return _home(tmp_path), _main_db(tmp_path)


def _base(main: Path, *more: str, basis: str = "morning") -> list[str]:
    return ["--date", D, "--basis", basis, "--v3-db", str(main), "--full", *more]


def _evening(main: Path, *more: str) -> list[str]:
    return _base(main, *more, basis="evening")


# ── 제자리 반영 ──────────────────────────────────────────────────────────────
def test_in_place_reflects_nine_tables_and_keeps_other_tables(env, sources, tmp_path) -> None:
    home, main = env
    r = _run(tmp_path, sources, *_base(main))
    assert r.rc == 0, r.out
    assert _q(main, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [
        (len(tce.SCORE_CODES),)]
    assert _q(main, f"SELECT count(*) FROM score_history_v2 WHERE score_date='{D_ISO}'") == [
        (len(tce.SCORE_CODES),)]
    assert _q(main, "SELECT count(*) FROM stocks") == [(tce.N_STOCKS,)]
    assert _q(main, "SELECT close FROM daily_prices WHERE trade_date='2020-01-02'") == [(7,)]
    assert _q(main, "SELECT close FROM market_indices") == [(2500,)]
    assert _q(main, "SELECT basis, status FROM _compat_meta") == [("morning", "ok")]
    # 락은 v3 와 같은 파일을 비대기로 잡았다(경합 없음)
    assert r.flock == ["-n 9"]
    assert "daily_post 명령 없음 — 건너뜀(기록만)" in r.out
    assert [n.split("|")[1] for n in _levels(r, "info")] == [f"v3_post {D} morning 반영 완료"]
    assert not (home / "data/_v3_post/staging_morning.db").exists()       # 성공하면 지운다
    assert (home / f"logs/v3_post/{D}_morning.log").exists()


def test_rebase_null_rows_are_one_warn_line(env, tmp_path) -> None:
    """QL-E — 창 안 사건 종목(005930, D23 기준가 = D22 종가 ÷ 2)의 창 밖 본 파일 행(2020-01-02)에 equity 행이 없으면
    adj_close 를 NULL 로 다시 맞추고 warn 한 줄을 남긴다(기록형 — 정지 아님, rc 0). 그 행의 종가는 그대로다."""
    _, main = env
    base = tmp_path / "ev_src"
    rows = tce._price_rows()
    for r in rows:
        if r["ticker"] == "005930" and r["date"] == tce.D23 and r["basis"] == "krx":
            r["base_price_krw"] = 70_100 // 2
    src = (*tce._make_roots(base, price_rows=rows, adj_rows=tce._adj_rows(price_rows=rows)),
           _model_root(base))
    r = _run(tmp_path, src, *_base(main))
    assert r.rc == 0, r.out
    assert _q(main, "SELECT close, adj_close FROM daily_prices WHERE trade_date='2020-01-02'") == [(7, None)]
    assert "rebase=1 rebase_null=1" in r.out
    warns = _levels(r, "warn")
    assert len(warns) == 1 and f"v3_post {D} morning 창 밖 adj_close NULL 1행" in warns[0]
    assert [n.split("|")[1] for n in _levels(r, "info")] == [f"v3_post {D} morning 반영 완료"]


def test_daily_post_runs_after_reflection_when_today_is_d(env, evening_sources, tmp_path) -> None:
    home, main = env
    marker = tmp_path / "post.txt"
    probe = tmp_path / "probe.py"          # daily_post 대역 — 그 순간 본 파일의 점수 행 수를 적는다
    probe.write_text(
        "import sqlite3, sys\n"
        "n = sqlite3.connect(sys.argv[1]).execute('SELECT count(*) FROM score_history')"
        ".fetchone()[0]\n"
        "open(sys.argv[2], 'a').write(f'called {n}\\n')\n", encoding="utf-8")
    cmd = f"'{sys.executable}' '{probe}' '{main}' '{marker}'"
    r = _run(tmp_path, evening_sources, *_evening(main, "--v3-post-cmd", cmd))
    assert r.rc == 0, r.out
    # daily_post 는 반영이 끝난 본 파일을 본다
    assert marker.read_text(encoding="utf-8").split() == ["called", str(len(tce.SCORE_CODES))]
    assert "daily_post 완료" in r.out


def test_daily_post_not_called_when_local_date_is_not_d(env, evening_sources, tmp_path) -> None:
    """v3 잡은 date.today() 로 그날을 정한다 — 자정을 넘기면 부르지 않고 warn(rc 6). 반영은 됐다."""
    _, main = env
    marker = tmp_path / "post.txt"
    r = _run(tmp_path, evening_sources, *_evening(main, "--v3-post-cmd", f"echo x >> {marker}"),
             today="20260924")
    assert r.rc == 6, r.out
    assert not marker.exists()
    assert _q(main, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [(3,)]
    assert len(_levels(r, "warn")) == 1 and "미호출" in _levels(r, "warn")[0]
    assert _levels(r, "crit") == []


def test_daily_post_failure_is_warn_rc6(env, evening_sources, tmp_path) -> None:
    _, main = env
    r = _run(tmp_path, evening_sources, *_evening(main, "--v3-post-cmd", "exit 7"))
    assert r.rc == 6, r.out
    assert "daily_post 실패 rc=7" in _levels(r, "warn")[0]
    assert _levels(r, "crit") == []


def test_post_cmd_does_not_inherit_the_lock_fd(env, evening_sources, tmp_path) -> None:
    """MINOR-2 — daily_post 자식은 v3 락 fd 9 를 물려받지 않는다(물려받으면 자식이 남아 락을 계속 쥔다)."""
    _, main = env
    r = _run(tmp_path, evening_sources,
             *_evening(main, "--v3-post-cmd", "[ -e /dev/fd/9 ] && echo leaked; true"))
    assert r.rc == 0, r.out
    assert "daily_post 완료" in r.out
    assert "leaked" not in r.out.splitlines()          # 명령 문자열 줄이 아니라 자식의 출력 줄


def _seed_record(main: Path, d_iso: str, basis: str) -> None:
    """본 파일에 앞선 ok 반영 기록 1행(v3_post 가 COMMIT 때 옮기는 그 행)."""
    con = sqlite3.connect(str(main), isolation_level=None)
    try:
        _write_meta(con, ExportResult(
            date=d_iso, basis=basis, target=str(main), exported_at=f"{d_iso}T07:30:00+00:00",
            window={"days": 14, "full": False, "from_date": d_iso, "to_date": d_iso},
            consensus_asof=d_iso,
            tables={t: TableResult(1, 0, {}, {"n_on_date": 1} if t == "daily_prices" else {})
                    for t in TABLES}))
    finally:
        con.close()


def test_morning_after_evening_reflects_seven_tables_without_morning_model(env, sources,
                                                                          tmp_path) -> None:
    """T-34 — 같은 D 의 장 마감 반영이 있으면 아침은 점수 두 표를 빼고 7표만 반영한다. 아침 모델 판이 없어도
    가격 재반영은 된다(점수 표를 compat 이 아예 고르지 않는다)."""
    _, main = env
    _seed_record(main, D_ISO, "evening")
    con = sqlite3.connect(str(main))
    con.execute("INSERT INTO score_history (stock_code, score_date, composite_score) "
                "VALUES ('005930', ?, 42.0)", (D_ISO,))       # 저녁에 보낸 점수
    con.commit()
    con.close()
    r = _run(tmp_path, sources, *_base(main, "--model-root", str(tmp_path / "no-model")))
    assert r.rc == 0, r.out
    assert "반영 표: " + ",".join(t for t in TABLES if t not in SCORE_TABLES) in r.out
    assert _q(main, "SELECT stock_code, composite_score FROM score_history") == [("005930", 42.0)]
    assert _q(main, f"SELECT count(*) FROM daily_prices WHERE trade_date='{D_ISO}'") == [(2,)]


def test_late_older_run_is_refused_unless_allow_older(env, sources, tmp_path) -> None:
    """T-35 — 본 파일에 더 나중(09-24) 반영 기록이 있으면 늦게 깨어난 09-23 실행은 반영하지 않는다."""
    _, main = env
    _seed_record(main, "2026-09-24", "evening")
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main))
    assert r.rc == 2, r.out
    assert "T-35" in r.out
    assert _sha(main) == before
    assert len(_levels(r, "crit")) == 1
    r2 = _run(tmp_path, sources, *_base(main, "--allow-older"))     # 재생
    assert r2.rc == 0, r2.out
    assert _q(main, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [(3,)]



# ── 장 마감 판 원장 인자(QL-D) ───────────────────────────────────────────────
def test_evening_export_gets_the_ledger_env(env, evening_sources, tmp_path) -> None:
    """② compat export 에 QL_POSTCLOSE_DB·QL_KIWOOM_DB 가 넘어간다 — 기본 경로를 비우고 다른 곳에 둔
    원장으로 T 행이 선다. 원천 기록(`t_rows`)이 그 경로를 남긴다."""
    home, main = env
    moved = tmp_path / "ledgers"
    moved.mkdir()
    for name in ("postclose.db", "kiwoom.db"):
        shutil.move(str(home / "data" / "raw" / name), str(moved / name))
    r = _run(tmp_path, evening_sources, *_evening(main),
             QL_POSTCLOSE_DB=str(moved / "postclose.db"), QL_KIWOOM_DB=str(moved / "kiwoom.db"))
    assert r.rc == 0, r.out
    assert _q(main, "SELECT stock_code, open, close, volume FROM daily_prices "
                    f"WHERE trade_date='{D_ISO}' ORDER BY 1") == [
        ("000660", 71_000, 71_000, 1_000), ("005930", 70_300, 70_300, 1_000)]
    tables = json.loads(_q(main, "SELECT tables FROM _compat_meta")[0][0])
    info = tables["daily_prices"]["t_rows"]
    assert info["ledgers"] == {"postclose": str(moved / "postclose.db"),
                               "kiwoom_2105": str(moved / "kiwoom.db")}
    assert tables["daily_prices"]["metrics"]["n_on_date"] == 2      # 신선도 게이트(T-31 ③)


def test_allow_older_reaches_the_evening_export(env, evening_sources, tmp_path) -> None:
    """`--allow-older` 는 apply 뿐 아니라 ② export 에도 간다 — compat 장 마감 판이 같은 T-35 순서로
    먼저 멈추므로, export 에 안 넘기면 재생도 ② 에서 실패한다."""
    _, main = env
    _seed_record(main, "2026-09-24", "evening")
    before = _sha(main)
    r = _run(tmp_path, evening_sources, *_evening(main))
    assert r.rc == 2, r.out
    assert "실패(② compat export --in-place)" in _levels(r, "crit")[0]
    assert _sha(main) == before
    r2 = _run(tmp_path, evening_sources, *_evening(main, "--allow-older"))
    assert r2.rc == 0, r2.out
    assert _q(main, f"SELECT count(*) FROM daily_prices WHERE trade_date='{D_ISO}'") == [(2,)]

# ── T-38 점수 없는 저녁 반영(QL-F2) ─────────────────────────────────────────
SEVEN = tuple(t for t in TABLES if t not in SCORE_TABLES)


def _seed_scores(main: Path) -> None:
    """v3 가 쓰던 점수 행(D·전날) — 점수 없는 반영이 건드리면 지문이 바뀐다."""
    con = sqlite3.connect(str(main))
    for table, col in (("score_history", "composite_score"), ("score_history_v2", "total_score")):
        con.executemany(f"INSERT INTO {table} (stock_code, score_date, {col}) VALUES (?, ?, ?)",
                        [("005930", D_ISO, 1.5), ("777770", D_ISO, 2.5),
                         ("005930", "2026-09-22", 3.5)])
    con.commit()
    con.close()


def _score_sha(main: Path) -> str:
    rows = {t: _q(main, f"SELECT * FROM {t} ORDER BY 1, 2") for t in SCORE_TABLES}
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def _t_prices(main: Path) -> list[tuple]:
    return _q(main, "SELECT stock_code, open, close, volume FROM daily_prices "
                    f"WHERE trade_date='{D_ISO}' ORDER BY 1")


def _meta_tables(main: Path) -> list[dict]:
    return [json.loads(r[0]) for r in _q(main, "SELECT tables FROM _compat_meta ORDER BY exported_at")]


def test_no_scores_evening_reflects_seven_tables_without_model_board(env, evening_sources,
                                                                    tmp_path) -> None:
    """장 마감 판 실패일(T-38) — 장 마감 모델 판이 없어도 가격 등 7표를 반영한다. 점수 두 표는 본 파일 그대로다."""
    _, main = env
    _seed_scores(main)
    before = _score_sha(main)
    r = _run(tmp_path, evening_sources,
             *_evening(main, "--no-scores", "--model-root", str(tmp_path / "no-model")))
    assert r.rc == 0, r.out
    assert "반영 표: " + ",".join(SEVEN) in r.out
    assert "scores=no" in r.out
    assert _score_sha(main) == before
    assert _t_prices(main) == [("000660", 71_000, 71_000, 1_000), ("005930", 70_300, 70_300, 1_000)]
    assert _q(main, f"SELECT count(*) FROM investor_detail_flows WHERE trade_date='{D_ISO}'") == [
        (2,)]
    (tables,) = _meta_tables(main)
    assert set(tables) == set(SEVEN)                     # 기록에 반영한 표 목록
    assert "daily_post 명령 없음" in r.out
    assert [n.split("|")[1] for n in _levels(r, "info")] == [f"v3_post {D} evening 반영 완료"]


def _session_exception_ledgers(raw: Path) -> None:
    """세션 예외일(T-26) — 장 마감 수집을 하지 않아 postclose.db 에 T 행이 없다(D' 행만). 21:05 키움 원장에
    T 행이 있다(기준가 = D' 종가)."""
    for name in ("postclose.db", "kiwoom.db"):
        (raw / name).unlink()
    con = postclose.connect(raw / "postclose.db")
    try:
        row = {"dt": "20260922", "cur_prc": "+70100", "pred_pre": "+100", "acc_trde_prica": "9",
               **{k: "-1" for k in kw_daily.FLOW_KEYS}}
        postclose.insert_first(con, list(postclose.COLS), "005930", [row],
                               collected_at="2026-09-22T06:45:00",
                               fetched_at="2026-09-22T06:41:00", price_valid=True)
    finally:
        con.close()
    con = sqlite3.connect(raw / "kiwoom.db")
    try:
        table = kw_daily.TRS["ka10060"].table
        kw_daily.ensure_table(con, table, list(postclose.COLS))
        for ticker, cur, pred, vol in (("005930", "+70500", "+400", "2000"),
                                       ("000660", "-70900", "-200", "3000")):
            kw_daily.insert_rows(con, table, list(postclose.COLS), ticker,
                                 [{"dt": D, "cur_prc": cur, "pred_pre": pred, "acc_trde_prica": vol,
                                   **{k: "-7" for k in kw_daily.FLOW_KEYS}}],
                                 "ka10060", "2026-09-23T12:05:00")
        con.commit()
    finally:
        con.close()


def test_no_scores_on_session_exception_day_takes_t_rows_from_2105_ledger(env, evening_sources,
                                                                         tmp_path) -> None:
    """세션 예외일 — postclose.db 에 그날 행이 없으면 전 종목이 21:05 원장 경로를 탄다(QL-D 그대로)."""
    home, main = env
    _session_exception_ledgers(home / "data" / "raw")
    r = _run(tmp_path, evening_sources, *_evening(main, "--no-scores"))
    assert r.rc == 0, r.out
    assert _t_prices(main) == [("000660", 70_900, 70_900, 3_000), ("005930", 70_500, 70_500, 2_000)]
    info = _meta_tables(main)[0]["daily_prices"]["t_rows"]
    assert (info["postclose"], info["kiwoom_2105"]) == (0, 2)
    assert info["kiwoom_2105_tickers"] == ["000660", "005930"]


def test_no_scores_without_postclose_ledger_file_stops(env, evening_sources, tmp_path) -> None:
    """postclose.db 파일 자체가 없으면 멈춘다(QL-D P1 — 경로 실수를 '그날 행 없음'으로 읽지 않는다). 본 파일
    무변경."""
    home, main = env
    (home / "data" / "raw" / "postclose.db").unlink()
    before = _sha(main)
    r = _run(tmp_path, evening_sources, *_evening(main, "--no-scores"))
    assert r.rc == 2, r.out
    assert "T 행 원장 파일이 없다" in r.out
    assert _sha(main) == before
    crit = _levels(r, "crit")
    assert len(crit) == 1 and "② compat export --in-place" in crit[0]


def test_next_morning_after_no_scores_evening_fills_scores(env, evening_sources, sources,
                                                          tmp_path) -> None:
    """T-34 판정 변경(QL-F2) — 점수 없는 저녁 반영만 있던 날은 다음 날 아침이 아침 모델 판 점수로 9표를 반영한다."""
    _, main = env
    r = _run(tmp_path, evening_sources, *_evening(main, "--no-scores"))
    assert r.rc == 0, r.out
    assert _q(main, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [(0,)]
    r2 = _run(tmp_path, sources, *_base(main))
    assert r2.rc == 0, r2.out
    assert "반영 표: " + ",".join(TABLES) in r2.out
    for table in SCORE_TABLES:
        assert _q(main, f"SELECT count(*) FROM {table} WHERE score_date='{D_ISO}'") == [
            (len(tce.SCORE_CODES),)]
    assert _q(main, "SELECT basis FROM _compat_meta ORDER BY exported_at") == [
        ("evening",), ("morning",)]


def test_next_morning_after_scored_evening_still_drops_scores(env, evening_sources, sources,
                                                             tmp_path) -> None:
    """점수 포함 저녁 반영이 있던 날은 지금처럼 아침이 점수를 빼고 7표다(아침 모델 판 없어도 된다)."""
    _, main = env
    r = _run(tmp_path, evening_sources, *_evening(main))
    assert r.rc == 0, r.out
    before = _score_sha(main)
    r2 = _run(tmp_path, sources, *_base(main, "--model-root", str(tmp_path / "no-model")))
    assert r2.rc == 0, r2.out
    assert "반영 표: " + ",".join(SEVEN) in r2.out
    assert _score_sha(main) == before


def test_rc6_evening_then_no_scores_refill_then_morning_is_seven(env, evening_sources, sources,
                                                                 tmp_path) -> None:
    """⑥ 이 COMMIT 뒤 daily_post 만 실패(rc 6) → refill(늘 --no-scores) → 다음 날 아침 7표. 점수는 ⑥ 값 그대로."""
    _, main = env
    r = _run(tmp_path, evening_sources, *_evening(main, "--v3-post-cmd", "exit 7"))
    assert r.rc == 6, r.out
    evening = _score_sha(main)
    r = _run(tmp_path, evening_sources, *_evening(main, "--no-scores"))
    assert r.rc == 0, r.out
    assert _score_sha(main) == evening
    r = _run(tmp_path, sources, *_base(main, "--model-root", str(tmp_path / "no-model")))
    assert r.rc == 0, r.out
    assert "반영 표: " + ",".join(SEVEN) in r.out
    assert _score_sha(main) == evening
    assert [sorted(t) for t in _meta_tables(main)] == [sorted(TABLES), sorted(SEVEN),
                                                       sorted(SEVEN)]


def test_scored_then_no_scores_evening_then_morning_is_seven(env, evening_sources, sources,
                                                             tmp_path) -> None:
    """점수 포함 저녁(⑥) → 점수 없는 저녁(refill) → 다음 날 아침 7표 — 뒤 기록에 점수가 없어도 앞 기록이 센다."""
    _, main = env
    assert _run(tmp_path, evening_sources, *_evening(main)).rc == 0
    evening = _score_sha(main)
    r = _run(tmp_path, evening_sources, *_evening(main, "--no-scores"))
    assert r.rc == 0, r.out
    r = _run(tmp_path, sources, *_base(main, "--model-root", str(tmp_path / "no-model")))
    assert r.rc == 0, r.out
    assert "반영 표: " + ",".join(SEVEN) in r.out
    assert _score_sha(main) == evening


def test_no_scores_staging_does_not_replace_a_failed_scored_staging(env, evening_sources,
                                                                   tmp_path) -> None:
    """MINOR-1 — ⑥(점수 포함)이 실패해 보존한 스테이징을 점수 없는 refill 이 지우지 않는다(기본 경로가 다르다)."""
    home, main = env
    r = _run(tmp_path, evening_sources, *_evening(main, "--model-root", str(tmp_path / "no-model")))
    assert r.rc == 2, r.out
    kept = home / "data" / "_v3_post" / "staging_evening.db"
    before = _sha(kept)
    r = _run(tmp_path, evening_sources, *_evening(main, "--no-scores"))
    assert r.rc == 0, r.out
    assert kept.exists() and _sha(kept) == before
    assert "staging=data/_v3_post/staging_evening_noscores.db" in r.out
    r = _run(tmp_path, evening_sources, *_evening(main, "--no-scores", "--shadow"))
    assert r.rc == 0, r.out
    assert (home / "data" / "_v3_post" / "staging_evening_noscores_shadow.db").exists()


def test_no_scores_gate_failure_leaves_main_unchanged(env, evening_skip_sources, tmp_path) -> None:
    """점수 없는 반영도 필수 열 결측 0 게이트를 7표 기준으로 건다 — compat 5% 허용은 통과해도 막는다."""
    _, main = env
    _seed_scores(main)
    before = _sha(main)
    r = _run(tmp_path, evening_skip_sources, *_evening(main, "--no-scores"))
    assert r.rc == 2, r.out
    assert "+1 skipped" in r.out and "필수 열" in r.out
    assert _sha(main) == before
    crit = _levels(r, "crit")
    assert len(crit) == 1 and "③④ 게이트·반영" in crit[0]


def test_no_scores_is_evening_and_compat_only(env, sources, tmp_path) -> None:
    """`--no-scores` 는 장 마감(evening) 반영 전용이고 daily_post 를 부르지 않는다 — 어기면 인자 오류 rc 5."""
    _, main = env
    marker = tmp_path / "post.txt"
    before = _sha(main)
    for args, extra in (
            (_base(main, "--no-scores"), {}),                                  # 아침은 T-34 가 정한다
            (_evening(main, "--no-scores", "--v3-post-cmd", f"echo x >> {marker}"), {}),
            (_evening(main, "--no-scores"), {"QL_V3_POST_CMD": f"echo x >> {marker}"})):
        r = _run(tmp_path, sources, *args, **extra)
        assert r.rc == 5, r.out
        assert "T-38" in r.out                          # 모르는 인자가 아니라 이 규칙으로 멈췄다
        assert r.flock == []
    assert not marker.exists()
    assert _sha(main) == before
    assert not (tmp_path / "ql" / "data" / "_v3_post").exists()


# ── 실패하면 본 파일 무변경 ──────────────────────────────────────────────────
def test_gate_failure_leaves_main_unchanged(env, skip_sources, tmp_path) -> None:
    """compat 은 5% 허용으로 성공하지만(1/36 건너뜀) 제자리 게이트는 0건이라 막는다(G11)."""
    home, main = env
    marker = tmp_path / "post.txt"
    before = _sha(main)
    r = _run(tmp_path, skip_sources, *_base(main))
    assert r.rc == 2, r.out
    assert "+1 skipped" in r.out                       # compat 자체는 통과했다
    assert "필수 열" in r.out
    assert _sha(main) == before
    assert not marker.exists()
    crit = _levels(r, "crit")
    assert len(crit) == 1 and "③④ 게이트·반영" in crit[0]
    assert "v3 본 파일 무변경(COMMIT 전 실패)" in crit[0]
    assert (home / "data/_v3_post/staging_morning.db").exists()            # 실패하면 남긴다


def test_in_place_full_before_v3_history_is_rc2_then_window_days_fits(env, sources,
                                                                       tmp_path) -> None:
    """T-46 — 제자리 `--full` 창(730일, 2024-09-23~)이 v3 investor_detail_flows 이력 시작(2025-01-23)보다 앞이면
    compat 이 쓰기 전에 멈춘다(rc 2 crit, 본 파일 무변경). `--window-days` 로 창을 이력 안에 맞추면 반영된다."""
    _, main = env
    con = sqlite3.connect(str(main))
    con.execute("DELETE FROM investor_detail_flows")
    con.execute("INSERT INTO investor_detail_flows (stock_code, trade_date, individual) "
                "VALUES ('005930', '2025-01-23', -1)")
    con.commit()
    con.close()
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main))
    assert r.rc == 2, r.out
    assert "investor_detail_flows 이력 시작 2025-01-23" in r.out
    assert _sha(main) == before
    crit = _levels(r, "crit")
    assert len(crit) == 1 and "② compat export --in-place" in crit[0]
    days = (dt.date(2026, 9, 23) - dt.date(2025, 1, 23)).days
    r2 = _run(tmp_path, sources, *_base(main, "--window-days", str(days)))
    assert r2.rc == 0, r2.out
    window = json.loads(_q(main, 'SELECT "window" FROM _compat_meta')[0][0])
    assert (window["from_date"], window["full"]) == ("2025-01-23", True)


def test_compat_failure_leaves_main_unchanged(env, sources, tmp_path) -> None:
    """그날 모델 판이 없다 — compat 이 멈추고 반영·daily_post 는 돌지 않는다."""
    home, main = env
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main, "--model-root", str(tmp_path / "no-model")))
    assert r.rc == 2, r.out
    assert _sha(main) == before
    crit = _levels(r, "crit")
    assert len(crit) == 1 and "② compat export --in-place" in crit[0]
    assert "③④" not in r.out


# ── 복원 뒤 첫 반영(QL-I · T-42 · T-46) ─────────────────────────────────────────
def _restored(main: Path) -> None:
    """본 파일에 복원 기록 1행(`v3_restore.write_record` — 복원 트랜잭션이 남기는 그 행). 시각은 compat 기록보다
    늘 앞이다(2000-01)."""
    con = sqlite3.connect(str(main), isolation_level=None)
    try:
        con.execute("BEGIN")
        v3_restore.write_record(con, "2000-01-02T00:00:00.000000+00:00", "2026-09-23",
                                Path("bak/quant_x.db"), "0" * 64,
                                {t: {"n_before": 1, "n_rows": 1} for t in TABLES})
        con.execute("COMMIT")
    finally:
        con.close()


def test_first_reflect_after_restore_needs_the_flag(env, sources, tmp_path) -> None:
    """표식 없는 반영(체인 모양)은 복원 뒤에 계산됐어도 rc 2 crit·본 파일 무변경, 거부 사유에 사람이 하는 명령을
    적는다. `--first-after-restore` 면 rc 0. 그 뒤 다시 표식을 주면(이미 복원 뒤 반영이 있다) rc 2 — 오용 방지."""
    _, main = env
    _restored(main)
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main))
    assert r.rc == 2, r.out
    assert "--first-after-restore" in r.out and "CUTOVER_ROLLBACK §5" in r.out
    assert _sha(main) == before
    crit = _levels(r, "crit")
    assert len(crit) == 1 and "③④ 게이트·반영" in crit[0]
    r2 = _run(tmp_path, sources, *_base(main, "--first-after-restore"))
    assert r2.rc == 0, r2.out
    assert [b for (b,) in _q(main, "SELECT basis FROM _compat_meta ORDER BY exported_at")] == [
        "restore", "morning"]
    after = _sha(main)
    r3 = _run(tmp_path, sources, *_base(main, "--first-after-restore"))
    assert r3.rc == 2, r3.out
    assert "복원 뒤 첫 반영 전용" in r3.out
    assert _sha(main) == after


def test_flag_on_a_never_restored_main_is_refused(env, sources, tmp_path) -> None:
    """평상시(복원 기록 없음) 대상에 `--first-after-restore` 를 주면 rc 2·본 파일 무변경(P1)."""
    _, main = env
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main, "--first-after-restore"))
    assert r.rc == 2, r.out
    assert "복원 뒤 첫 반영 전용" in r.out
    assert _sha(main) == before


def test_chain_scripts_never_pass_first_after_restore() -> None:
    """복원 뒤 첫 반영은 사람만 한다(T-42 · T-46) — 장 마감 체인(close·refill·morning)·`compat_export.sh`·아침
    체인(`daily_build.sh`)과 그 설정은 표식을 넘기지 않는다. `v3_post.sh`(표식을 받는 쪽) 밖의 scripts·config
    어디에도 표식 문자열이 없어야 한다."""
    flag = "first-after-restore"
    named = [DB_ROOT / "scripts" / n for n in ("postclose_chain.sh", "compat_export.sh", "daily_build.sh")]
    assert all(p.is_file() for p in named)
    files = [p for root in (DB_ROOT / "scripts", DB_ROOT / "config") for p in root.rglob("*")
             if p.is_file() and p.name != "v3_post.sh"]
    assert set(named) <= set(files)
    hits = [str(p.relative_to(DB_ROOT)) for p in files
            if flag in p.read_bytes().decode("utf-8", errors="replace")]
    assert hits == []
    assert flag in (DB_ROOT / "scripts" / "v3_post.sh").read_text(encoding="utf-8")


# ── 그림자 ───────────────────────────────────────────────────────────────────
def test_shadow_stages_and_gates_without_writing_main_or_lock(env, evening_sources,
                                                              tmp_path) -> None:
    home, main = env
    marker = tmp_path / "post.txt"
    before = _sha(main)
    r = _run(tmp_path, evening_sources,
             *_evening(main, "--shadow", "--v3-post-cmd", f"echo x >> {marker}"))
    assert r.rc == 0, r.out
    assert _sha(main) == before
    assert r.flock == []                                # v3 daily_all 의 flock -n 을 막지 않는다
    assert not marker.exists()
    stg = home / "data/_v3_post/staging_evening_shadow.db"      # 제자리 사본과 섞이지 않게(NIT 3)
    assert not (home / "data/_v3_post/staging_evening.db").exists()
    assert _q(stg, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [(3,)]
    assert "그림자" in _levels(r, "info")[0]


def test_shadow_gate_failure_is_crit_and_main_unchanged(env, skip_sources, tmp_path) -> None:
    home, main = env
    before = _sha(main)
    r = _run(tmp_path, skip_sources, *_base(main, "--shadow"))
    assert r.rc == 2, r.out
    assert _sha(main) == before
    assert len(_levels(r, "crit")) == 1


def test_custom_staging_path(env, sources, tmp_path) -> None:
    home, main = env
    stg = tmp_path / "cmp" / "staging_1008.db"
    r = _run(tmp_path, sources, *_base(main, "--shadow", "--staging", str(stg)))
    assert r.rc == 0, r.out
    assert _q(stg, "SELECT status FROM _compat_meta") == [("ok",)]


# ── 락 경합 ──────────────────────────────────────────────────────────────────
def test_lock_contention_waits_then_runs(env, sources, tmp_path) -> None:
    home, main = env
    r = _run(tmp_path, sources, *_base(main), FLOCK_N_RC="1", FLOCK_WAIT_RC="0")
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9", "9"]                     # 비대기 실패 → 대기형으로 기다림
    assert "v3 락 대기" in _levels(r, "info")[0]
    assert _q(main, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [(3,)]


def test_lock_failure_is_warn_rc3_and_touches_nothing(env, sources, tmp_path) -> None:
    home, main = env
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main), FLOCK_N_RC="1", FLOCK_WAIT_RC="1")
    assert r.rc == 3, r.out
    assert _sha(main) == before
    assert not (home / "data/_v3_post").exists()        # 스테이징도 뜨지 않았다
    assert len(_levels(r, "warn")) == 1 and _levels(r, "crit") == []


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock 없음(맥) — 서버·CI 우분투에서만")
def test_real_lock_held_by_v3_chain_is_waited_for(env, sources, tmp_path) -> None:
    """v3 체인(`flock -n <락> …`)이 락을 쥐고 있으면 풀릴 때까지 기다렸다가 반영한다."""
    home, main = env
    lock = tmp_path / "v3.lock"
    holder = subprocess.Popen(["flock", str(lock), "sleep", "3"])
    try:
        time.sleep(0.5)
        r = _run(tmp_path, sources, *_base(main), stub_flock=False)
    finally:
        holder.wait(timeout=10)
    assert r.rc == 0, r.out
    assert r.elapsed >= 2.0
    assert "v3 락 대기 시작" in r.out


@pytest.mark.parametrize("how", ["same", "symlink", "wal"])
def test_staging_on_main_path_is_rc5_and_main_survives(env, sources, tmp_path, how) -> None:
    """MAJOR-1 — `--staging` 이 본 파일(링크·-wal 포함)이면 ① 이 본 파일을 지운다. 셸이 먼저 rc 5 로 막는다."""
    _, main = env
    if how == "same":
        stg = main
    elif how == "symlink":
        stg = tmp_path / "link.db"
        stg.symlink_to(main)
    else:
        stg = Path(f"{main}-wal")
        stg.write_bytes(b"")                          # 본 파일 -wal 이 있는 순간
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main, "--staging", str(stg)))
    assert r.rc == 5, r.out
    assert main.exists() and _sha(main) == before
    assert r.flock == []                                # 락도 잡기 전에 멈춘다


def test_morning_with_post_cmd_is_rc5(env, sources, tmp_path) -> None:
    """T-31 — 아침 재반영은 compat 만. post 명령이 오면 날짜 가드에 기대지 않고 인자 오류로 멈춘다."""
    _, main = env
    marker = tmp_path / "post.txt"
    r = _run(tmp_path, sources, *_base(main, "--v3-post-cmd", f"echo x >> {marker}"))
    assert r.rc == 5, r.out
    r2 = _run(tmp_path, sources, *_base(main), QL_V3_POST_CMD=f"echo x >> {marker}")
    assert r2.rc == 5, r2.out
    assert not marker.exists()
    assert not (tmp_path / "ql" / "data" / "_v3_post").exists()


# ── 인자 ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("args", [
    ["--basis", "morning", "--v3-db", "MAIN"],                       # --date 없음(기본값 없다)
    ["--date", D, "--basis", "noon", "--v3-db", "MAIN"],
    ["--date", D, "--basis", "morning"],                             # v3 경로 없음
    ["--date", D, "--basis", "morning", "--v3-db", "NOPE"],
    ["--date", D, "--basis", "morning", "--v3-db", "MAIN", "--bogus"],
    ["--date", D, "--basis", "morning", "--v3-db", "MAIN", "--window-days", "600"],  # --full 없음
])
def test_argument_errors_are_rc5_warn(env, sources, tmp_path, args) -> None:
    home, main = env
    args = [str(main) if a == "MAIN" else str(tmp_path / "nope.db") if a == "NOPE" else a
            for a in args]
    before = _sha(main)
    r = _run(tmp_path, sources, *args)
    assert r.rc == 5, r.out
    assert _sha(main) == before
    assert len(_levels(r, "warn")) == 1
    assert r.flock == []
