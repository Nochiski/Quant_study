"""daily.cutover_watch — 컷오버 감시(컷오버 트랙 QL-L).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 QL-L · T-16 · T-27 · T-31, DECISIONS N-42 Q2,
`docs/COMPAT_LAYER.md` §8-1(V3-A·B·D). 입력은 전부 tmp 에 v3 로컬 사본(25dd56b)의 실제 모양으로 조립한다.
  · v3 quant.db — compat 이 쓰는 `v3_schema.sql`(v3 실제 DDL 합본) + v3 `pipeline_runs`(`backend/db/schema.py:189-197`
    그대로). `pipeline_runs` 행은 v3 쓰는 곳 셋의 모양 그대로다 — job_runner(`job_runner.py:86·150` UTC ISO `T`),
    DailyPipeline 하위 단계(`daily_pipeline.py:282` sqlite `datetime('now')`), 증권사 리포트(`research/brokers/_store.py:31`).
    `_compat_meta` 는 진짜 `compat.quant_db._write_meta` 로 쓴다.
  · pipeline.log — job_runner 로그 형식(`job_runner.py:15-19` `%Y-%m-%dT%H:%M:%S LEVEL message`)과 시각 없는 자식 경고
    줄(v3 `docs/system-guide/survey/01-collection.md` §7.2 서버 로그 실측 인용).
  · crontab — v3 `scripts/cron_schedule.sh:3` 줄 모양(경로는 `~` 로 적는다).
  · 알림 — 진짜 `scripts/notify.sh`(기록만)가 남긴 줄을 X-2 `window_judge` 가 읽어 분류한다.

D = 2026-10-19(월). KST 하루 = UTC 10-18 15:00 ~ 10-19 15:00.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
from pathlib import Path

import pytest
from compat.mappings import BY_TABLE
from compat.quant_db import SCHEMA_SQL_PATH, ExportResult, TableResult, _write_meta
from compat.v3_post import SCORE_TABLES
from daily import cutover_watch as cw
from daily import window_judge as wj

NOTIFY_SH = Path(__file__).resolve().parents[1] / "scripts" / "notify.sh"

D = "20261019"
D_ISO = "2026-10-19"

# v3 `backend/db/schema.py:189-197` 그대로
PIPELINE_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT CHECK(status IN ('running', 'success', 'failed')),
    error_message TEXT,
    rows_affected INTEGER DEFAULT 0
);
"""

# 컷오버 뒤 정상인 하루의 pipeline_runs (job, started_at UTC, status)
NORMAL_RUNS: tuple[tuple[str, str, str], ...] = (
    # 전날(10-18 KST) 20:05 — 컷오버 전 daily_all. 창 밖이라 보지 않는다
    ("chain:daily_all", "2026-10-18T11:05:01", "success"),
    ("daily_pipeline", "2026-10-18T11:05:02", "success"),
    ("stock_master", "2026-10-18 11:05:22", "success"),
    ("scoring", "2026-10-18T13:14:05", "success"),
    # 07:00 KST 브리핑 = 전날 22:00 UTC — D 에 든다
    ("chain:briefing_morning", "2026-10-18T22:00:01", "success"),
    ("holiday_gate", "2026-10-18T22:00:01", "success"),
    ("briefing_morning", "2026-10-18T22:00:02", "success"),
    ("chain:briefing_midday", "2026-10-19T03:15:01", "success"),
    ("holiday_gate", "2026-10-19T03:15:01", "success"),
    ("briefing_midday", "2026-10-19T03:15:02", "success"),
    # 장 마감 체인 ⑥ 뒤 v3_post.sh 가 부른 daily_post(T-31)
    ("chain:daily_post", "2026-10-19T07:20:01", "success"),
    ("holiday_gate", "2026-10-19T07:20:01", "success"),
    ("export_scores", "2026-10-19T07:20:02", "success"),
    # 20:05 KST daily_insight(V3-A)
    ("chain:daily_insight", "2026-10-19T11:05:01", "success"),
    ("holiday_gate", "2026-10-19T11:05:01", "success"),
    ("insight_pipeline", "2026-10-19T11:05:01", "success"),
    ("wiki_ingest", "2026-10-19T11:06:01", "success"),
    ("wiki_lint", "2026-10-19T11:28:01", "success"),
    # 21:00 KST 증권사 리포트(research_broker_<소스>, sqlite datetime('now'))
    ("research_broker_nh", "2026-10-19 12:00:27", "success"),
    ("research_broker_samsung", "2026-10-19 12:01:23", "success"),
    # 다음 날(10-20 KST) 00:00 — 창 밖
    ("chain:daily_all", "2026-10-19T15:00:00", "running"),
)

# 컷오버 전(지금 v3) 하루 — daily_all 10단계 + DailyPipeline 하위 단계
PRECUTOVER_RUNS: tuple[tuple[str, str, str], ...] = (
    ("chain:daily_all", "2026-10-19T11:05:01", "success"),
    ("calendar_refresh", "2026-10-19T11:05:01", "success"),
    ("holiday_gate", "2026-10-19T11:05:01", "success"),
    ("daily_pipeline", "2026-10-19T11:05:01", "success"),
    ("holiday_check", "2026-10-19 11:05:01", "success"),
    ("stock_master", "2026-10-19 11:05:22", "success"),
    ("daily_prices", "2026-10-19 11:05:23", "success"),
    ("investor_flows", "2026-10-19 11:22:23", "success"),
    ("consensus", "2026-10-19 11:47:42", "success"),
    ("adj_prices", "2026-10-19T12:50:37", "success"),
    ("scoring", "2026-10-19T13:14:05", "success"),
    ("scoring_v2", "2026-10-19T13:14:10", "success"),
    ("export_scores", "2026-10-19T13:14:12", "success"),
    ("insight_pipeline", "2026-10-19T13:14:26", "success"),
    ("wiki_ingest", "2026-10-19T13:15:23", "success"),
    ("wiki_lint", "2026-10-19T13:37:25", "success"),
)

NORMAL_LOG = """\
2026-10-18T11:05:01 INFO job=daily_pipeline attempt=1/3
ka10081 ohlcv 005930 page 0: ReadTimeout
wisereport failed for 005930 after 3 attempts: TimeoutError
2026-10-18T12:50:37 INFO job=daily_pipeline status=success attempt=1
2026-10-19T11:05:01 INFO job=holiday_gate attempt=1/1
[holiday_gate] 20261019 영업일 → 진행
2026-10-19T11:05:01 INFO job=holiday_gate status=success attempt=1
2026-10-19T11:05:01 INFO job=insight_pipeline attempt=1/1
2026-10-19 11:05:30,123 WARNING ka20006 업종일봉 001 응답 지연 — 재시도
2026-10-19 11:05:31,456 WARNING ka10051 업종별 투자자 순매수 0 행
2026-10-19T11:06:01 INFO job=insight_pipeline status=success attempt=1
2026-10-19T11:06:01 INFO job=wiki_ingest attempt=1/1
[ingest] 전체 완료: 2026-10-19
2026-10-19T11:28:01 INFO job=wiki_ingest status=success attempt=1
2026-10-19T11:28:01 INFO job=wiki_lint attempt=1/1
[lint] 이번 주 마지막 영업일 아님, 스킵
2026-10-19T11:28:01 INFO job=wiki_lint status=success attempt=1
2026-10-19T15:00:00 INFO job=daily_pipeline attempt=1/3
ka10059 failed for 005930 page 0: 429
"""

# 그날 daily_all 이 그대로 돈 로그(①의 금지 표지 셋 — 키움 종목별 TR·KIS·WiseReport)
PRECUTOVER_LOG = """\
2026-10-19T11:05:01 INFO job=calendar_refresh attempt=1/1
[calendar_refresh] 20261019 cache refreshed (is_holiday=False)
2026-10-19T11:05:01 INFO job=calendar_refresh status=success attempt=1
2026-10-19T11:05:01 INFO job=daily_pipeline attempt=1/3
holiday_check attempt 1 failed: Server error '500 Internal Server Error' for url 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/chk-holiday?BASS_DT=20261019'
ka10081 ohlcv 005930 page 0: ReadTimeout
wisereport failed for 005930 after 3 attempts: TimeoutError
2026-10-19T12:50:37 INFO job=daily_pipeline status=success attempt=1
2026-10-19T12:50:37 INFO job=adj_prices attempt=1/1
2026-10-19T13:14:05 INFO job=adj_prices status=success attempt=1
2026-10-19T13:14:05 INFO job=scoring attempt=1/2
2026-10-19T13:14:10 INFO job=scoring status=success attempt=1
"""

_V3 = "cd ~/kael-system-v3 && /usr/bin/flock -n /tmp/{lock}.lock bash -c 'source ~/.local/bin/env && " \
      "export $(grep -v \"^#\" .env | xargs) && PYTHONPATH=. .venv/bin/python {cmd}' >> ~/logs/kael-v3/{log}.log 2>&1"
CRON_DAILY_ALL = "5 11 * * 1-5 " + _V3.format(lock="kael_v3_daily_all",
                                               cmd="scripts/job_runner.py --chain daily_all", log="pipeline")
CRON_INSIGHT = "5 11 * * 1-5 " + _V3.format(lock="kael_v3_daily_insight",
                                             cmd="scripts/job_runner.py --chain daily_insight", log="pipeline")
CRON_YEAR = "0 20 29 12 * " + _V3.format(lock="kael_v3_year_refresh", cmd="-m scripts.refresh_year_holidays",
                                          log="year_refresh")
CRON_MONTHLY = "0 0 1 * * " + _V3.format(lock="kael_v3_monthly_review", cmd="-m scripts.monthly_holiday_review",
                                          log="monthly_review")
CRON_OTHERS = (
    "CRON_TZ=Asia/Seoul",
    "0 7 * * 1-5 " + _V3.format(lock="kael_briefing_morning", cmd="scripts/job_runner.py --chain briefing_morning",
                                log="briefing"),
    "30 11 * * 1-5 " + _V3.format(lock="kael_v3_research", cmd="-m backend.research.pipeline", log="research"),
    "*/30 * * * * " + _V3.format(lock="kael_news_ingest", cmd="-m backend.news.ingest", log="news"),
    "0 21 * * * /bin/bash ~/quant-ledger/scripts/daily_ledger.sh >> ~/quant-ledger/logs/cron_daily_ledger.log 2>&1",
)
# 컷오버 뒤 — daily_all·휴장 쓰기 2줄은 주석으로 남기고(되돌리기 = 주석 풀기) daily_insight 를 더했다
NORMAL_CRON = "\n".join(("# 평일 20:05 KST — insight·위키(V3-A). daily_all 은 V3-B 로 껐다",
                         CRON_INSIGHT, "# " + CRON_DAILY_ALL, "#" + CRON_YEAR, "#" + CRON_MONTHLY,
                         *CRON_OTHERS)) + "\n"
# 컷오버 전 — 지금 v3 crontab
PRECUTOVER_CRON = "\n".join((CRON_DAILY_ALL, CRON_YEAR, CRON_MONTHLY, *CRON_OTHERS)) + "\n"

MODEL_BUILDS = {"scope@1.0": "m_20261019_0741_ab12", "v2_percentrank@1.0": "m_20261019_0741_cd34"}


# ── 픽스처 조립 ────────────────────────────────────────────────────────────
def _write_v3_db(path: Path, *, runs: tuple[tuple[str, str, str], ...] = NORMAL_RUNS,
                 scores: tuple[int, int] = (5, 6), record: tuple[int, int] | None = (5, 6),
                 model_builds: dict[str, str] | None = None, score_date: str = D_ISO) -> Path:
    """v3 quant.db — `scores` = (score_history, score_history_v2) 그날 행 수, `record` = compat 기록이 넣은 행 수
    (None 이면 기록 없음). 기록은 장 마감 판 ⑥ 의 9표 반영(점수 포함)이다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8") + PIPELINE_RUNS_DDL)
        con.executemany("INSERT INTO pipeline_runs (job_name, started_at, status) VALUES (?, ?, ?)", runs)
        for table, n in zip(SCORE_TABLES, scores, strict=True):
            ncols = len(con.execute(f"PRAGMA table_info({table})").fetchall())
            rows = []
            for i in range(n):
                row: list[object] = [None] * ncols
                row[0], row[1], row[6] = f"{i:06d}", score_date, float(i)
                rows.append(tuple(row))
            # 다른 날 행 — 그날 행 수에 섞이지 않는다
            other: list[object] = [None] * ncols
            other[0], other[1], other[6] = "999999", "2026-10-16", 1.0
            rows.append(tuple(other))
            con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * ncols)})", rows)
        if record is not None:
            tables = {t: TableResult(n_rows=2, n_skipped=0, sources={}) for t in BY_TABLE}
            for t, n in zip(SCORE_TABLES, record, strict=True):
                tables[t] = TableResult(n_rows=n, n_skipped=0, sources={})
            _write_meta(con, ExportResult(
                date=D_ISO, basis="evening", target=str(path), exported_at="2026-10-19T07:19:30.000000+00:00",
                window={"days": 14, "full": False, "from_date": "2026-10-05", "to_date": D_ISO},
                consensus_asof="2026-10-16", tables=tables,
                model_builds=MODEL_BUILDS if model_builds is None else model_builds))
    finally:
        con.close()
    return path


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """quant-ledger 루트 — 진짜 notify.sh(기록만)를 둔다."""
    home = tmp_path / "ql"
    (home / "scripts").mkdir(parents=True)
    (home / "logs").mkdir()
    os.symlink(NOTIFY_SH, home / "scripts" / "notify.sh")
    monkeypatch.setenv("QL_HOME", str(home))
    monkeypatch.delenv("QL_NOTIFY_TELEGRAM", raising=False)
    return home


def _inputs(tmp_path: Path, *, log: str = NORMAL_LOG, cron: str = NORMAL_CRON, **db) -> dict[str, Path]:
    v3 = tmp_path / "v3"
    v3.mkdir(exist_ok=True)
    (v3 / "pipeline.log").write_text(log, encoding="utf-8")
    (v3 / "crontab.txt").write_text(cron, encoding="utf-8")
    return {"db": _write_v3_db(v3 / "quant.db", **db), "log": v3 / "pipeline.log", "cron": v3 / "crontab.txt"}


def _run(home: Path, inp: dict[str, Path], *extra: str) -> int:
    return cw.main(["--date", D, "--home", str(home), "--v3-db", str(inp["db"]), "--v3-log", str(inp["log"]),
                    "--crontab", str(inp["cron"]), *extra])


def _out(home: Path) -> dict:
    return json.loads((home / "data" / "cutover" / f"watch_{D}.json").read_text(encoding="utf-8"))


def _crits(home: Path) -> list[wj.Note]:
    path = home / "logs" / "notify.log"
    if not path.exists():
        return []
    log = wj.read_notify(path)
    assert log.unparsed == []
    return [n for ns in log.by_date.values() for n in ns if n.level == "crit"]


def _jobs(out: dict) -> dict[str, str]:
    return {j["job"]: j["class"] for j in out["collect"]["jobs"]}


# ── 정상 ───────────────────────────────────────────────────────────────────
def test_정상_허용_job_만이면_rc0_이고_알림이_없다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    home = _home(tmp_path, monkeypatch)
    assert _run(home, _inputs(tmp_path)) == 0
    out = _out(home)
    assert (out["tool"], out["schema"], out["date"], out["mode"]) == (cw.TOOL, cw.SCHEMA, D, "watch")
    assert (out["verdict"], out["rc"], out["violations"]) == ("ok", 0, [])
    assert out["window_utc"] == ["2026-10-18T15:00:00Z", "2026-10-19T15:00:00Z"]
    jobs = _jobs(out)
    # 창 밖(전날 20:05 daily_all · 다음 날 00:00) 은 보지 않는다. 07:00 KST 브리핑(전날 22:00 UTC)은 D 다
    assert "chain:daily_all" not in jobs and "daily_pipeline" not in jobs and "scoring" not in jobs
    assert jobs["briefing_morning"] == "allowed" and jobs["chain:daily_insight"] == "allowed"
    assert jobs["research_broker_nh"] == "allowed"
    assert set(jobs.values()) == {"allowed"}
    # 허용 TR(T-27) 표지는 보이기만 한다 · 시각 없는 줄은 앞 시각 줄 날짜(전날 ka10081·wisereport 는 창 밖)
    marks = {(m["token"], m["allowed"]) for m in out["collect"]["markers"]}
    assert marks == {("ka20006", True), ("ka10051", True)}
    sh = out["scores"]["tables"]["score_history"]
    assert (sh["rows"], sh["record"]["n_rows"], sh["record"]["build_id"]) == (5, 5, MODEL_BUILDS["scope@1.0"])
    assert out["cron"]["daily_post_runs"] == 1
    assert _crits(home) == []
    screen = capsys.readouterr().out
    assert "컷오버 감시 2026-10-19(월)" in screen and "정상(rc 0)" in screen


# ── ① 무거운 수집 ──────────────────────────────────────────────────────────
def test_금지_수집_job_이_돌면_위반이고_세는_crit_한_줄(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    runs = NORMAL_RUNS + (("daily_pipeline", "2026-10-19T11:30:00", "success"),
                          ("stock_master", "2026-10-19 11:30:20", "success"),
                          ("consensus", "2026-10-19 12:00:00", "running"))
    assert _run(home, _inputs(tmp_path, runs=runs, log=NORMAL_LOG + PRECUTOVER_LOG)) == 1
    out = _out(home)
    assert (out["verdict"], out["rc"]) == ("violation", 1)
    jobs = _jobs(out)
    for job in ("daily_pipeline", "stock_master", "consensus", "calendar_refresh", "adj_prices"):
        assert jobs[job] == "collect", job
    assert jobs["scoring"] == "scoring"                     # 로그에서 본 스코어링은 ② 위반
    v1 = " ".join(out["collect"]["violations"])
    assert "daily_pipeline" in v1 and "consensus" in v1 and "calendar_refresh" in v1
    bad = {m["token"] for m in out["collect"]["markers"] if not m["allowed"]}
    assert bad == {"ka10081", "kis", "wisereport"}
    assert any("scoring" in v for v in out["scores"]["violations"])
    crits = _crits(home)
    assert len(crits) == 1
    assert crits[0].title.startswith(f"{cw.TITLE_VIOLATION} D={D}")
    assert wj.classify_crit(crits[0].title) == "counted"   # X-2 세는 목록(v3 반영 경로, T-39)
    assert out["notified"] is True


def test_분류표에_없는_job_은_허용_목록_밖이라_위반(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    """허용 목록에 없는 새 job 은 위반이다(fail-closed) — 허용 목록을 고쳐야 통과한다."""
    home = _home(tmp_path, monkeypatch)
    runs = NORMAL_RUNS + (("chain:daily_data", "2026-10-19T09:30:01", "success"),
                          ("new_collector", "2026-10-19T09:30:02", "failed"))
    assert _run(home, _inputs(tmp_path, runs=runs)) == 1
    out = _out(home)
    assert _jobs(out)["new_collector"] == "unknown" and _jobs(out)["chain:daily_data"] == "unknown"
    assert out["collect"]["jobs"][0]["class"] == "unknown"          # 분류 안 된 job 이 맨 앞
    assert any("new_collector" in v and "허용 목록 밖" in v for v in out["collect"]["violations"])
    assert "분류 안 된 job: chain:daily_data · new_collector" in capsys.readouterr().out


def test_허용_job_안이라도_허용_밖_TR_표지는_위반(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    log = NORMAL_LOG.replace("ka10051 업종별 투자자 순매수 0 행", "ka10059 failed for 005930 page 0: 429")
    assert _run(home, _inputs(tmp_path, log=log)) == 1
    out = _out(home)
    m = [x for x in out["collect"]["markers"] if x["token"] == "ka10059"]
    assert len(m) == 1 and m[0]["allowed"] is False and m[0]["job"] == "insight_pipeline"
    assert out["scores"]["violations"] == [] and out["cron"]["violations"] == []


# ── ② 점수 쓰기 한 곳 ───────────────────────────────────────────────────────
def test_v3_스코어링이_돌면_위반(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """v3 scoring 이 compat 반영 뒤 INSERT OR REPLACE — 행 수가 compat 기록과 갈린다(1,329 vs 593 축소판)."""
    home = _home(tmp_path, monkeypatch)
    runs = NORMAL_RUNS + (("scoring", "2026-10-19T13:14:05", "success"),
                          ("scoring_v2", "2026-10-19T13:14:10", "success"))
    assert _run(home, _inputs(tmp_path, runs=runs, scores=(13, 25))) == 1
    out = _out(home)
    v2 = out["scores"]["violations"]
    assert any("job scoring 실행" in v for v in v2) and any("job scoring_v2 실행" in v for v in v2)
    assert any("score_history 2026-10-19 행 13" in v and "5" in v for v in v2)
    assert any("score_history_v2 2026-10-19 행 25" in v for v in v2)
    assert out["collect"]["violations"] == []
    assert _jobs(out)["scoring"] == "scoring"
    assert len(_crits(home)) == 1


def test_compat_기록_없이_점수_행이_있으면_위반(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    assert _run(home, _inputs(tmp_path, record=None)) == 1
    out = _out(home)
    assert out["scores"]["tables"]["score_history"]["record"] is None
    assert any("compat 반영 기록" in v for v in out["scores"]["violations"])


def test_compat_기록에_판_id_가_없으면_위반(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    assert _run(home, _inputs(tmp_path, model_builds={"scope@1.0": "m_x"})) == 1
    v2 = _out(home)["scores"]["violations"]
    assert len(v2) == 1 and "v2_percentrank@1.0" in v2[0]


def test_점수_행도_기록도_없으면_위반이_아니다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """장 마감 판 실패일·휴장일 — 점수는 다음 날 아침 재반영이 채운다(T-38). 감시의 일이 아니다."""
    home = _home(tmp_path, monkeypatch)
    assert _run(home, _inputs(tmp_path, scores=(0, 0), record=None)) == 0


# ── ③ v3 크론 ──────────────────────────────────────────────────────────────
def test_daily_all_크론이_남으면_위반(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    cron = NORMAL_CRON + CRON_DAILY_ALL + "\n"
    assert _run(home, _inputs(tmp_path, cron=cron)) == 1
    out = _out(home)
    assert len(out["cron"]["lines"]["daily_all"]) == 1
    assert len(out["cron"]["violations"]) == 1 and "daily_all" in out["cron"]["violations"][0]
    assert len(_crits(home)) == 1


@pytest.mark.parametrize("line", [CRON_YEAR, CRON_MONTHLY])
def test_휴장_쓰기_크론이_남으면_위반(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str) -> None:
    home = _home(tmp_path, monkeypatch)
    assert _run(home, _inputs(tmp_path, cron=NORMAL_CRON + line + "\n")) == 1
    v3 = _out(home)["cron"]["violations"]
    assert len(v3) == 1 and "V3-D" in v3[0]


def test_daily_insight_크론이_없으면_위반(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    cron = NORMAL_CRON.replace(CRON_INSIGHT + "\n", "")
    assert _run(home, _inputs(tmp_path, cron=cron)) == 1
    v3 = _out(home)["cron"]["violations"]
    assert len(v3) == 1 and "daily_insight" in v3[0]


def test_crontab_은_표준입력으로도_받는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`crontab -l | python -m daily.cutover_watch --crontab -` — 임시 파일 없이."""
    home = _home(tmp_path, monkeypatch)
    inp = _inputs(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(PRECUTOVER_CRON))
    assert cw.main(["--date", D, "--home", str(home), "--v3-db", str(inp["db"]), "--v3-log", str(inp["log"]),
                    "--crontab", "-"]) == 1
    out = _out(home)
    assert out["inputs"]["crontab"] == "-"
    assert len(out["cron"]["violations"]) == 4       # daily_all · daily_insight 없음 · 휴장 쓰기 2줄


# ── 기준선(컷오버 전 그림자 기간) ─────────────────────────────────────────────
def test_baseline_은_지금_v3_를_기록만_하고_위반으로_보지_않는다(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    home = _home(tmp_path, monkeypatch)
    inp = _inputs(tmp_path, runs=PRECUTOVER_RUNS, log=PRECUTOVER_LOG, cron=PRECUTOVER_CRON,
                  scores=(1329, 2526), record=None)
    assert _run(home, inp, "--baseline") == 0
    out = _out(home)
    assert (out["mode"], out["verdict"], out["rc"]) == ("baseline", "baseline", 0)
    jobs = _jobs(out)
    assert jobs == {"chain:daily_all": "collect", "calendar_refresh": "collect", "holiday_gate": "allowed",
                    "daily_pipeline": "collect", "holiday_check": "collect", "stock_master": "collect",
                    "daily_prices": "collect", "investor_flows": "collect", "consensus": "collect",
                    "adj_prices": "collect", "scoring": "scoring", "scoring_v2": "scoring",
                    "export_scores": "allowed", "insight_pipeline": "allowed", "wiki_ingest": "allowed",
                    "wiki_lint": "allowed"}
    # 감시 모드였다면 걸렸을 것 — 기록으로 남긴다
    assert out["violations"] and out["cron"]["violations"] and out["scores"]["violations"]
    assert out["scores"]["tables"]["score_history_v2"]["rows"] == 2526
    assert out["notified"] is None
    assert _crits(home) == []
    assert "기준선" in capsys.readouterr().out


# ── 입력 오류 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("missing", ["db", "log", "cron"])
def test_입력이_없으면_rc2_이고_판정_불가_crit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      missing: str) -> None:
    """못 쟀으면 통과가 아니다(공통 3) — 감시 모드는 판정 불가 crit, 결과 파일은 verdict error."""
    home = _home(tmp_path, monkeypatch)
    inp = _inputs(tmp_path)
    inp[missing] = tmp_path / "없는" / inp[missing].name
    assert _run(home, inp) == 2
    out = _out(home)
    assert (out["verdict"], out["rc"]) == ("error", 2)
    assert str(inp[missing]) in out["error"]
    crits = _crits(home)
    assert len(crits) == 1 and crits[0].title.startswith(f"{cw.TITLE_ERROR} D={D}")
    assert wj.classify_crit(crits[0].title) == "counted"


def test_기준선_모드의_입력_오류는_알리지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    inp = _inputs(tmp_path)
    inp["log"] = tmp_path / "없는.log"
    assert _run(home, inp, "--baseline") == 2
    assert _out(home)["verdict"] == "error"
    assert _crits(home) == []


def test_빈_crontab_은_입력_오류(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`crontab -l` 실패(사용자 crontab 없음 포함)는 빈 출력이다 — '크론 위반 0' 과 다르다."""
    home = _home(tmp_path, monkeypatch)
    assert _run(home, _inputs(tmp_path, cron="# 주석뿐\nCRON_TZ=Asia/Seoul\n\n")) == 2
    assert "crontab" in _out(home)["error"]


def test_pipeline_runs_표가_없으면_입력_오류(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    inp = _inputs(tmp_path)
    con = sqlite3.connect(str(inp["db"]))
    con.execute("DROP TABLE pipeline_runs")
    con.commit()
    con.close()
    assert _run(home, inp) == 2
    assert "pipeline_runs" in _out(home)["error"]


def test_날짜_형식이_틀리면_rc2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    inp = _inputs(tmp_path)
    assert cw.main(["--date", "2026-10-19", "--home", str(home), "--v3-db", str(inp["db"]),
                    "--v3-log", str(inp["log"]), "--crontab", str(inp["cron"])]) == 2


def test_dry_run_은_결과_파일도_알림도_없다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    assert _run(home, _inputs(tmp_path, cron=PRECUTOVER_CRON), "--dry-run") == 1
    assert not (home / "data" / "cutover").exists()
    assert _crits(home) == []


def test_v3_파일은_바꾸지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, monkeypatch)
    inp = _inputs(tmp_path)
    before = {p.name: p.read_bytes() for p in (inp["db"], inp["log"], inp["cron"])}
    _run(home, inp)
    assert {p.name: p.read_bytes() for p in (inp["db"], inp["log"], inp["cron"])} == before
