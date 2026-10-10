"""daily.silent_loss — 조용한 손실 검사(컷오버 트랙 K1-4a · N-42 Q4 · 로드맵 K1-4).

매일 아침 확정판 뒤 모델 폐포(stage·equity) 표를 직전 거래일 아침 확정판과 대조해 '설명 안 되는 값→NULL·
행 삭제·available_date 변경'을 센다. 비교 엔진은 `scripts/equity_diff.py`(diff_core) 하나다.

픽스처는 `test_equity_diff` 와 같은 방식 — `tmp_path` 에 `<루트>/<표>/v=<build>/year=YYYY/part0.parquet` +
`MANIFEST.json` 을 손으로 세우고, 인계 이력 `data/deliver/history/<D>_morning.json` 두 개(D·직전 거래일)를 둔다.
표 이름·grain·날짜 열은 실제 규칙 선언(equity `RULES` · stage `RULES`)을 쓴다 — 선언이 바뀌면 여기서 깨진다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import duckdb
import pytest

from daily import runlog
from daily import silent_loss as sl
from daily import window_judge as wj

D, DP = "20261008", "20261007"          # 목요일 · 직전 거래일(수)
HOLIDAYS = ["20261009"]                  # 한글날 — 창 계산에는 안 걸린다
WIN_IN = "2026-09-30"                    # 재수집 창(D 와 그 앞 10세션 = 09-24 ~ 10-08) 안
WIN_OUT = "2025-01-02"                   # 창 밖

EQ_SCHEMA = {"ticker": "VARCHAR", "date": "DATE", "close_krw": "DOUBLE", "available_date": "DATE"}
ST_SCHEMA = {"ticker": "VARCHAR", "date": "DATE", "close_krw": "DOUBLE", "available_date": "DATE",
             "observed_date": "DATE"}
ROOTS = {"equity": Path("data/equity"), "stage": Path("data/stage")}
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


# ── 픽스처 ──────────────────────────────────────────────────────────────────
class _Raw(str):
    """SQL 리터럴을 그대로 쓰는 값 — STRUCT 같은 중첩 값 픽스처용."""


def _lit(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, _Raw):
        return str(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return repr(v)


def _write_parquet(path: Path, rows: list[dict], schema: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sel = [" SELECT " + ", ".join(f"CAST({_lit(r.get(c))} AS {t}) AS {c}" for c, t in schema.items())
           for r in rows]
    con = duckdb.connect()
    try:
        con.execute(f"COPY ({' UNION ALL '.join(sel)}) TO '{path}' (FORMAT PARQUET)")
    finally:
        con.close()


def _hash(rows: list[dict]) -> str:
    """내용이 같으면 같은 값 — 빌더의 content_hash 와 같은 성질만 흉내 낸다."""
    body = json.dumps(sorted(rows, key=lambda r: json.dumps(r, sort_keys=True)), sort_keys=True)
    return f"{len(rows)}:{hashlib.sha1(body.encode()).hexdigest()[:12]}"


def _build(home: Path, layer: str, table: str, bid: str, rows: list[dict], *,
           schema: dict[str, str], rules: str = "r1") -> None:
    """`<루트>/<표>/v=<bid>/year=YYYY/part0.parquet` 를 쓰고 MANIFEST 에 판 기록 1건을 덧붙인다(current = 이것).
    equity 판 기록에는 파티션 content_hash 가 있고 stage 판 기록에는 없다(실제 두 빌더와 같다)."""
    troot = home / ROOTS[layer] / table
    by_year: dict[str, list[dict]] = {}
    for r in rows:                          # date 열이 없는 표는 whole(파티션 없음)
        by_year.setdefault(str(r["date"])[:4] if "date" in r else "", []).append(r)
    parts = []
    for y, rs in sorted(by_year.items()):
        rel = f"v={bid}/year={y}" if y else f"v={bid}"
        _write_parquet(troot / rel / "part0.parquet", rs, schema)
        p: dict[str, object] = {"path": rel, "n_rows": len(rs)}
        if layer == "equity":
            p["content_hash"] = _hash(rs)
        parts.append(p)
    mpath = troot / "MANIFEST.json"
    mf = (json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists()
          else {"table": table, "current_build": None, "keep": 3, "builds": []})
    mf["builds"].append({"build_id": bid, "snapshot_id": "", "rules_version": rules,
                         "built_at_utc": "2026-10-08T00:00:00+00:00", "n_rows": len(rows),
                         "content_hash": _hash(rows), "partitions": parts, "gates": [],
                         "inputs": {}, "basis": "morning"})
    mf["current_build"] = bid
    mpath.write_text(json.dumps(mf), encoding="utf-8")


def _handoff(home: Path, d: str, *, stage: dict[str, str], equity: dict[str, str],
             health: str = "ok") -> None:
    p = home / "data/deliver/history" / f"{d}_morning.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"date": d, "basis": "morning", "stage_builds": stage or {"stg_x": "m_0"},
                             "equity_builds": equity or {"x": "m_0"},
                             "health": {"stage": health, "equity": health}}), encoding="utf-8")


def _home(tmp_path: Path, *, block: bool | None = None) -> Path:
    home = tmp_path / "quant-ledger"
    (home / "data/calendar").mkdir(parents=True)
    (home / "data/calendar/kis_holidays_2026.json").write_text(
        json.dumps({"year": "2026", "holidays": HOLIDAYS}), encoding="utf-8")
    notify = home / "scripts/notify.sh"
    notify.parent.mkdir(parents=True)
    notify.write_text('#!/usr/bin/env bash\necho "$1|$2|$3" >> "$(dirname "$0")/../notify.txt"\n',
                      encoding="utf-8")
    notify.chmod(0o755)
    if block is not None:
        (home / "config").mkdir()
        (home / "config/silent_loss.env").write_text(
            f"# 테스트\nSILENT_LOSS_BLOCK={1 if block else 0}\n", encoding="utf-8")
    return home


def _px(ticker: str, date: str, close: float | None, avail: str | None = None) -> dict:
    return {"ticker": ticker, "date": date, "close_krw": close, "available_date": avail or date}


BASE = [_px("A", WIN_OUT, 100.0), _px("A", WIN_IN, 101.0), _px("A", "2026-10-07", 102.0),
        _px("B", "2025-01-03", 50.0)]


def _equity_pair(home: Path, after: list[dict], *, rules_before: str = "e1.28.0",
                 rules_after: str = "e1.28.0", table: str = "price_daily") -> None:
    _build(home, "equity", table, "m_b", BASE, schema=EQ_SCHEMA, rules=rules_before)
    _build(home, "equity", table, "m_a", after, schema=EQ_SCHEMA, rules=rules_after)
    _handoff(home, DP, stage={}, equity={table: "m_b"})
    _handoff(home, D, stage={}, equity={table: "m_a"})


def _notes(home: Path) -> list[str]:
    p = home / "notify.txt"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _tbl(res: sl.Result, name: str) -> dict:
    hit = [t for t in res.tables if t["table"] == name]
    assert len(hit) == 1, [t["table"] for t in res.tables]
    return hit[0]


EQ_PD = [("equity", "price_daily")]


# ── 갈래 ────────────────────────────────────────────────────────────────────
def test_새_날짜_행만_늘면_미설명_0(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _equity_pair(home, [*BASE, _px("A", "2026-10-08", 103.0)])
    res = sl.check(home, D, tables=EQ_PD)
    c = _tbl(res, "price_daily")["counts"]
    assert (c["rows_added"], c["rows_removed"], c["unexplained"], c["window"]) == (1, 0, 0, 0)
    assert res.verdict == "ok" and res.rc == 0
    assert res.prev == DP
    assert res.window == (dt.date(2026, 9, 24), dt.date(2026, 10, 8))


def test_재수집_창_안_값_NULL_과_값_변경은_창_갈래(tmp_path: Path) -> None:
    home = _home(tmp_path)
    after = [_px("A", WIN_OUT, 100.0), _px("A", WIN_IN, None), _px("A", "2026-10-07", 999.0),
             _px("B", "2025-01-03", 50.0)]
    _equity_pair(home, after)
    res = sl.check(home, D, tables=EQ_PD)
    t = _tbl(res, "price_daily")
    assert t["window_column"] == "date"
    assert t["counts"]["window"] == 2                 # 값→NULL 1 + 값 변경 1
    assert t["counts"]["unexplained"] == 0
    assert res.verdict == "ok" and res.rc == 0


def test_창_밖_값_NULL_행_삭제_공개일_변경은_미설명(tmp_path: Path) -> None:
    """창 밖 값→NULL 1 · 행 삭제 1 · available_date 변경 1(창 안이어도 — 공개시점 축은 창으로 설명하지 않는다)."""
    home = _home(tmp_path)
    after = [_px("A", WIN_OUT, None), _px("A", WIN_IN, 101.0),
             _px("A", "2026-10-07", 102.0, avail="2026-10-08")]
    _equity_pair(home, after)
    res = sl.check(home, D, tables=EQ_PD)
    t = _tbl(res, "price_daily")
    assert t["counts"]["unexplained"] == 3
    assert t["unexplained_by_kind"] == {"value_to_null": 1, "rows_removed": 1, "available_date": 1}
    keys = [s["key"] for s in t["samples"]]
    assert {"ticker": "A", "date": WIN_OUT} in keys and {"ticker": "B", "date": "2025-01-03"} in keys
    assert res.verdict == "unexplained" and res.rc == 1


def test_규칙_판본이_바뀐_표는_규칙_갈래(tmp_path: Path) -> None:
    home = _home(tmp_path)
    after = [_px("A", WIN_OUT, None), _px("A", WIN_IN, 101.0), _px("A", "2026-10-07", 102.0)]
    _equity_pair(home, after, rules_after="e1.29.0")
    res = sl.check(home, D, tables=EQ_PD)
    t = _tbl(res, "price_daily")
    assert t["rules_changed"] and (t["rules_before"], t["rules_after"]) == ("e1.28.0", "e1.29.0")
    assert t["counts"]["rules_change"] == 2 and t["counts"]["unexplained"] == 0
    assert res.verdict == "ok"


@pytest.mark.parametrize("gone", ["disk", "manifest"])
def test_판이_GC_되면_판정_불가(tmp_path: Path, gone: str) -> None:
    home = _home(tmp_path)
    _equity_pair(home, BASE)
    troot = home / "data/equity/price_daily"
    if gone == "disk":
        shutil.rmtree(troot / "v=m_b")
    else:
        mf = json.loads((troot / "MANIFEST.json").read_text(encoding="utf-8"))
        mf["builds"] = [b for b in mf["builds"] if b["build_id"] != "m_b"]
        (troot / "MANIFEST.json").write_text(json.dumps(mf), encoding="utf-8")
    res = sl.check(home, D, tables=EQ_PD)
    t = _tbl(res, "price_daily")
    assert t["status"] == "undecidable" and "m_b" in t["reason"]
    assert res.totals["undecidable"] == 1
    assert res.verdict == "undecidable" and res.rc == 2


def test_직전_거래일_인계_이력이_없으면_판정_불가(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _equity_pair(home, BASE)
    (home / f"data/deliver/history/{DP}_morning.json").unlink()
    res = sl.check(home, D, tables=EQ_PD)
    assert res.verdict == "undecidable" and res.rc == 2
    assert f"{DP}_morning.json" in (res.error or "")


def test_같은_판이나_같은_내용이면_읽지_않는다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _build(home, "equity", "price_daily", "m_b", BASE, schema=EQ_SCHEMA)
    _build(home, "equity", "trading_calendar", "m_c", [{"date": "2026-10-07"}],
           schema={"date": "DATE"})
    _build(home, "equity", "trading_calendar", "m_d", [{"date": "2026-10-07"}],
           schema={"date": "DATE"})
    _handoff(home, DP, stage={}, equity={"price_daily": "m_b", "trading_calendar": "m_c"})
    _handoff(home, D, stage={}, equity={"price_daily": "m_b", "trading_calendar": "m_d"})
    res = sl.check(home, D, tables=[("equity", "price_daily"), ("equity", "trading_calendar")])
    assert _tbl(res, "price_daily")["status"] == "same_build"
    assert _tbl(res, "trading_calendar")["status"] == "same_content"
    assert res.verdict == "ok"


def test_equity_는_해시가_같은_파티션을_건너뛰고_바뀐_파티션만_비교한다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _equity_pair(home, [*BASE[:3], _px("B", "2025-01-03", 50.0), _px("A", "2026-10-08", 1.0)])
    res = sl.check(home, D, tables=EQ_PD)
    t = _tbl(res, "price_daily")
    assert t["partitions"]["skipped"] == ["year=2025"]
    assert t["partitions"]["compared"] == ["year=2026"]
    assert t["counts"]["rows_added"] == 1 and t["counts"]["unexplained"] == 0


def test_격자의_NULL_키는_막지_않고_중복_키만_판정_불가다(tmp_path: Path) -> None:
    """flow_daily grain (date, ticker, src) — not_collected 셀은 src NULL 이 설계다(조인은 NULL 안전). 같은 키가
    두 번이면 조인이 팬아웃하므로 그때만 판정 불가."""
    home = _home(tmp_path)
    schema = {"date": "DATE", "ticker": "VARCHAR", "src": "VARCHAR", "net_krw": "DOUBLE"}
    before = [{"date": WIN_OUT, "ticker": "A", "src": None, "net_krw": None},
              {"date": WIN_OUT, "ticker": "B", "src": "kiwoom", "net_krw": 5.0}]
    after = [*before, {"date": "2026-10-08", "ticker": "A", "src": None, "net_krw": None}]
    _build(home, "equity", "flow_daily", "m_b", before, schema=schema)
    _build(home, "equity", "flow_daily", "m_a", after, schema=schema)
    _build(home, "equity", "flow_daily", "m_dup", [*after, after[0]], schema=schema)
    _handoff(home, DP, stage={}, equity={"flow_daily": "m_b"})
    _handoff(home, D, stage={}, equity={"flow_daily": "m_a"})
    res = sl.check(home, D, tables=[("equity", "flow_daily")])
    t = _tbl(res, "flow_daily")
    assert t["status"] == "compared" and t["counts"]["rows_added"] == 1 and t["counts"]["unexplained"] == 0
    _handoff(home, D, stage={}, equity={"flow_daily": "m_dup"})
    t = _tbl(sl.check(home, D, tables=[("equity", "flow_daily")]), "flow_daily")
    assert t["status"] == "undecidable" and "중복" in t["reason"]


# ── stage 표 ────────────────────────────────────────────────────────────────
def _st(ticker: str, date: str, close: float | None, obs: str) -> dict:
    return {"ticker": ticker, "date": date, "close_krw": close, "available_date": date,
            "observed_date": obs}


def test_stage_키_유일_표는_자연키로_판본_표는_관측일까지로_비교한다(tmp_path: Path) -> None:
    """stg_price_daily(key_unique) 는 (ticker, date), stg_credit_daily(판본 append_only) 는 (ticker, date,
    observed_date) — 새 판본 추가는 정상, 옛 판본이 사라지면 행 삭제(미설명)."""
    home = _home(tmp_path)
    px_b = [_st("A", WIN_OUT, 10.0, WIN_OUT), _st("A", WIN_IN, 11.0, WIN_IN)]
    px_a = [_st("A", WIN_OUT, None, WIN_OUT), _st("A", WIN_IN, 11.0, WIN_IN)]
    cr_b = [_st("A", WIN_OUT, 5.0, WIN_OUT), _st("A", WIN_OUT, 6.0, "2025-01-05")]
    cr_a = [_st("A", WIN_OUT, 6.0, "2025-01-05"), _st("A", WIN_OUT, 7.0, "2025-01-09")]
    for table, b, a in (("stg_price_daily", px_b, px_a), ("stg_credit_daily", cr_b, cr_a)):
        _build(home, "stage", table, "m_b", b, schema=ST_SCHEMA, rules="2.8.0")
        _build(home, "stage", table, "m_a", a, schema=ST_SCHEMA, rules="2.8.0")
    _handoff(home, DP, stage={"stg_price_daily": "m_b", "stg_credit_daily": "m_b"}, equity={})
    _handoff(home, D, stage={"stg_price_daily": "m_a", "stg_credit_daily": "m_a"}, equity={})
    res = sl.check(home, D, tables=[("stage", "stg_price_daily"), ("stage", "stg_credit_daily")])
    px, cr = _tbl(res, "stg_price_daily"), _tbl(res, "stg_credit_daily")
    assert px["key"] == ["ticker", "date"] and px["window_column"] == "date"
    assert px["unexplained_by_kind"]["value_to_null"] == 1
    assert cr["key"] == ["ticker", "date", "observed_date"]
    assert (cr["counts"]["rows_added"], cr["counts"]["rows_removed"]) == (1, 1)
    assert cr["unexplained_by_kind"]["rows_removed"] == 1
    assert res.totals["unexplained"] == 2


def test_stage_목록_구조체_열은_동등_비교로_센다(tmp_path: Path) -> None:
    """서버 10-08 결함 — stg_wise_coverage 의 `_cast_fail_cols`(INTEGER[])를 수치로 보고 DOUBLE 캐스트해 표 전체가
    판정 불가였다. 수치 밖 열은 IS DISTINCT FROM 동등 비교: 같은 값 → 차이 0 · 값→NULL → 미설명 · 내용 변경 → 값 변경."""
    home = _home(tmp_path)
    schema = {"ticker": "VARCHAR", "status_current": "VARCHAR", "_cast_fail_cols": "INTEGER[]",
              "miss_kind": "STRUCT(status_current VARCHAR, n VARCHAR)", "observed_date": "DATE"}

    def row(t: str, fails: object, mk: str) -> dict:
        return {"ticker": t, "status_current": "ok", "_cast_fail_cols": fails, "miss_kind": _Raw(mk),
                "observed_date": "2026-10-01"}

    same_mk = "{'status_current': NULL, 'n': 'ledger_zero'}"
    before = [row("A", [1], same_mk), row("B", [], "{'status_current': NULL, 'n': NULL}"),
              row("C", [2], same_mk), row("D", [1], same_mk)]
    after = [row("A", [1], same_mk), row("B", [], "{'status_current': 'blank', 'n': NULL}"),
             row("C", None, same_mk), row("D", [1, 2], same_mk)]
    _build(home, "stage", "stg_wise_coverage", "m_b", before, schema=schema, rules="2.8.0")
    _build(home, "stage", "stg_wise_coverage", "m_a", after, schema=schema, rules="2.8.0")
    _handoff(home, DP, stage={"stg_wise_coverage": "m_b"}, equity={})
    _handoff(home, D, stage={"stg_wise_coverage": "m_a"}, equity={})
    res = sl.check(home, D, tables=[("stage", "stg_wise_coverage")])
    t = _tbl(res, "stg_wise_coverage")
    assert t["status"] == "compared", t["reason"]
    assert t["window_column"] is None                         # whole 표 — 재수집 창 없음
    c = t["counts"]
    assert (c["value_to_null"], c["value_changed"], c["null_to_value"]) == (1, 2, 0)
    assert c["unexplained"] == 1 and t["unexplained_by_kind"]["value_to_null"] == 1
    assert t["samples"][0]["key"] == {"ticker": "C"} and t["samples"][0]["before"] == "[2]"


# ── 폐포 ────────────────────────────────────────────────────────────────────
def test_폐포는_fi_원천에서_equity_입력을_따라_닫힌다(monkeypatch: pytest.MonkeyPatch) -> None:
    from factor_inputs import queries

    base = sl.closure_tables()
    names = {t for _, t in base}
    direct = {s for v in queries.TABLE_SOURCES.values() for s in v}
    assert direct <= names                                  # 직접 원천은 다 든다
    assert "corp_event" in names and "stg_fin" in names     # 전이(adj_factor ← corp_event, fin_std ← stg_fin)
    assert "stg_flow_postclose_kiwoom" not in names         # 장 마감 판 원천은 연구 루트 밖(T-29)
    for layer, t in base:
        assert (layer == "stage") == t.startswith("stg_")
    assert "short_daily" not in names
    monkeypatch.setattr(queries, "TABLE_SOURCES", {**queries.TABLE_SOURCES, "fi_x": ("short_daily",)})
    grown = {t for _, t in sl.closure_tables()}
    assert "short_daily" in grown and "stg_short_daily_kiwoom" in grown
    assert len(grown) > len(names)


# ── 실행(main) · 기록 ───────────────────────────────────────────────────────
def _main(home: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sl, "closure_tables", lambda: list(EQ_PD))
    return sl.main(["check", "--date", D, "--home", str(home), *args])


def test_기록형은_결과_파일_런_로그_warn_한_줄(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path)
    _equity_pair(home, [_px("A", WIN_OUT, None), *BASE[1:]])
    assert _main(home, monkeypatch) == 1
    rep = json.loads((home / f"logs/silent_loss/{D}.json").read_text(encoding="utf-8"))
    assert rep["verdict"] == "unexplained" and rep["mode"] == "record" and rep["date"] == D
    assert rep["totals"]["unexplained"] == 1
    notes = _notes(home)
    assert len(notes) == 1 and notes[0].startswith(f"warn|{sl.TITLE_RECORD} D={D}")
    runs = runlog.recent(home / "data/raw/daily_run.db", source=runlog.SILENT_LOSS)
    assert [(r.date, r.status) for r in runs] == [(D, "unexplained")]
    assert "unexplained" in runlog.WARN_STATUSES[runlog.SILENT_LOSS]


def test_미설명_0_이면_알림_없음(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path)
    _equity_pair(home, [*BASE, _px("A", "2026-10-08", 103.0)])
    assert _main(home, monkeypatch) == 0
    assert _notes(home) == []
    runs = runlog.recent(home / "data/raw/daily_run.db", source=runlog.SILENT_LOSS)
    assert [r.status for r in runs] == ["ok"]


# ── 차단 스위치(지금은 꺼짐 — 켜는 쪽 동작만 고정) ────────────────────────────
def test_저장소_설정은_꺼짐이다() -> None:
    repo = Path(__file__).resolve().parents[1]
    on, _ = sl.block_enabled(repo)
    assert on is False
    assert (repo / sl.CONF).exists()


def test_차단_스위치를_켜면_미설명이_crit_과_차단이다(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path, block=True)
    _equity_pair(home, [_px("A", WIN_OUT, None), *BASE[1:]])
    assert _main(home, monkeypatch) == 3
    rep = json.loads((home / f"logs/silent_loss/{D}.json").read_text(encoding="utf-8"))
    assert rep["verdict"] == "blocked" and rep["mode"] == "block"
    notes = _notes(home)
    assert len(notes) == 1 and notes[0].startswith(f"crit|{sl.TITLE_BLOCK} D={D}")
    assert wj.classify_crit(notes[0].split("|")[1]) == "counted"
    rc, why = sl.gate(home, D)
    assert rc == 1 and "미설명 1" in why


def test_차단_스위치를_켜면_판정_불가도_차단이다(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """N-42 Q4 '필수 검사 SKIP = 실패' — 차단형이 되면 판정 불가는 통과가 아니다."""
    home = _home(tmp_path, block=True)
    _equity_pair(home, BASE)
    shutil.rmtree(home / "data/equity/price_daily/v=m_b")
    assert _main(home, monkeypatch) == 3
    assert sl.gate(home, D)[0] == 1


def test_차단_관문(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """관문(`gate`) — 스위치가 꺼져 있으면 결과와 무관하게 통과, 켜져 있으면 결과 파일이 그 D 의 미설명 0·판정 불가
    0 일 때만 통과. 결과 파일이 없거나 못 읽으면 막는다(P1)."""
    home = _home(tmp_path, block=False)
    _equity_pair(home, [_px("A", WIN_OUT, None), *BASE[1:]])
    assert _main(home, monkeypatch) == 1                     # 기록형
    assert sl.gate(home, D)[0] == 0                          # 꺼짐 → 통과
    (home / "config/silent_loss.env").write_text("SILENT_LOSS_BLOCK=1\n", encoding="utf-8")
    assert sl.gate(home, D)[0] == 1                          # 켜짐 + 미설명 → 막음
    assert sl.gate(home, "20261007")[0] == 1                 # 결과 파일 없음 → 막음
    (home / f"logs/silent_loss/{D}.json").write_text("{", encoding="utf-8")
    assert sl.gate(home, D)[0] == 1                          # 못 읽음 → 막음
    home2 = _home(tmp_path / "ok", block=True)
    _equity_pair(home2, [*BASE, _px("A", "2026-10-08", 103.0)])
    assert _main(home2, monkeypatch) == 0
    assert sl.gate(home2, D)[0] == 0


@pytest.mark.parametrize(("text", "on"), [
    ("SILENT_LOSS_BLOCK=1\n", True), ("SILENT_LOSS_BLOCK='1'\n", True),
    ("SILENT_LOSS_BLOCK=0\n", False), ("SILENT_LOSS_BLOCK=yes\n", False), ("", False),
    ("SILENT_LOSS_BLOCK=1\nSILENT_LOSS_BLOCK=0\n", False), ("# SILENT_LOSS_BLOCK=1\n", False)])
def test_스위치는_켜는_쪽만_정확한_값(tmp_path: Path, text: str, on: bool) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/silent_loss.env").write_text(text, encoding="utf-8")
    assert sl.block_enabled(tmp_path)[0] is on


@pytest.mark.parametrize("text", [
    None, "", "SILENT_LOSS_BLOCK=1\n", "SILENT_LOSS_BLOCK='1'\n", 'SILENT_LOSS_BLOCK="1"\n',
    "SILENT_LOSS_BLOCK=0\n", "SILENT_LOSS_BLOCK=yes\n", "SILENT_LOSS_BLOCK=\n", "SILENT_LOSS_BLOCK=' 1'\n",
    "  SILENT_LOSS_BLOCK = 1  \n", "SILENT_LOSS_BLOCK=1\r\n", "SILENT_LOSS_BLOCK=1\nSILENT_LOSS_BLOCK=0\n",
    "SILENT_LOSS_BLOCK=0\nSILENT_LOSS_BLOCK=1\n", "# SILENT_LOSS_BLOCK=1\n", "export SILENT_LOSS_BLOCK=1\n",
    "SILENT_LOSS_BLOCK=1 # 켬\n", "SILENT_LOSS_BLOCKX=1\n", "SILENT_LOSS_BLOCK\n"])
def test_셸_스위치_판정은_파이썬과_같다(tmp_path: Path, text: str | None) -> None:
    """15:41 장 마감 체인은 스위치를 파이썬 없이 셸에서 읽는다(scripts/postclose_conf.sh silent_loss_block_on) — 같은 파일을
    읽는 두 곳이 다르게 판정하면 안 된다(P4)."""
    if text is not None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config/silent_loss.env").write_bytes(text.encode("utf-8"))
    p = subprocess.run(["bash", "-c", 'cd "$1" && . "$2" && silent_loss_block_on', "_", str(tmp_path),
                        str(SCRIPTS / "postclose_conf.sh")], capture_output=True, text=True, check=False)
    assert p.returncode in (0, 1), p.stderr
    assert (p.returncode == 0) is sl.block_enabled(tmp_path)[0]


# ── daily_build.sh — 체인 맨 끝, 실패해도 체인 rc 불변 ─────────────────────────
_BUILD_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *prev_trading_day*) echo 2026-09-23 ;;
  esac
  exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = daily.silent_loss ]; then
  echo "python $*" >> "$HOME/quant-ledger/hook.txt"
  exit "${SL_RC:-0}"
fi
exit 0
"""


def _daily_build(home: Path, *, sl_rc: int, brc: int = 0) -> tuple[int, list[str], list[str], str]:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "data/raw"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    for name in ("daily_build.sh", "raw_lock.sh"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    order = 'echo "$(basename "$0") $*" >> "$HOME/quant-ledger/hook.txt"\n'
    stubs = {".venv/bin/python": _BUILD_PY,
             "scripts/postclose_chain.sh": "#!/usr/bin/env bash\n" + order + "exit 0\n",
             "scripts/build_morning.sh": "#!/usr/bin/env bash\n" + order + f"exit {brc}\n",
             "scripts/model_daily.sh": "#!/usr/bin/env bash\n" + order + "exit 0\n",
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n'}
    for rel, body in stubs.items():
        (root / rel).write_text(body, encoding="utf-8")
        (root / rel).chmod(0o755)
    con = sqlite3.connect(root / "data/raw/krx.db")
    con.execute("CREATE TABLE ingest_log (bas_dd TEXT, status TEXT)")
    con.execute("INSERT INTO ingest_log VALUES (?, 'ok')", (D,))
    con.commit()
    con.close()
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"), QL_RAW_LOCK_HELD="1",
               SL_RC=str(sl_rc))
    for k in ("QL_HOME", "PYTHONPATH", "QL_SKIP_KW", "QL_FORCE"):
        env.pop(k, None)
    p = subprocess.run(["bash", str(root / "scripts/daily_build.sh"), "--date", D],
                       env=env, capture_output=True, text=True, timeout=120, check=False)
    log = "".join(f.read_text(encoding="utf-8") for f in sorted((root / "logs").glob("daily_build_*.log")))

    def lines(p: Path) -> list[str]:
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    return p.returncode, lines(root / "hook.txt"), lines(root / "notify.txt"), log


NO_SQLITE3 = pytest.mark.skipif(shutil.which("sqlite3") is None,
                                reason="sqlite3 CLI 없음 — daily_build 의 KRX 단계가 쓴다")


@NO_SQLITE3
@pytest.mark.parametrize("sl_rc", [0, 1, 2, 3, 9])
def test_daily_build_조용한_손실_검사는_체인_끝이고_rc_를_바꾸지_않는다(tmp_path: Path, sl_rc: int) -> None:
    rc, hook, notes, log = _daily_build(tmp_path, sl_rc=sl_rc)
    assert rc == 0, log
    assert hook == [f"build_morning.sh --date {D}", f"model_daily.sh --date {D}",
                    f"postclose_chain.sh morning --date {D}",
                    f"python -m daily.silent_loss check --date {D}"]
    assert f"조용한 손실 검사 종료 rc={sl_rc}" in log
    final = [n for n in notes if n.startswith("info|daily_build 완료|")]
    assert len(final) == 1 and f"조용한 손실 검사 종료 rc={sl_rc}" in final[0]
    warns = [n.split("|")[1] for n in notes if n.startswith("warn|")]
    # 0~3 은 모듈이 스스로 기록·알림한다. 그 밖(모듈이 죽음)만 체인이 warn 1건으로 남긴다(조용한 실패 금지)
    assert warns == ([] if sl_rc in (0, 1, 2, 3) else [f"daily_build 조용한 손실 검사 rc={sl_rc}"])


@NO_SQLITE3
def test_daily_build_확정판이_없으면_조용한_손실_검사를_돌리지_않는다(tmp_path: Path) -> None:
    rc, hook, _, _ = _daily_build(tmp_path, sl_rc=0, brc=2)
    assert rc == 2
    assert hook == [f"build_morning.sh --date {D}"]


def test_차단_crit_제목은_세는_목록이다() -> None:
    assert wj.classify_crit(f"{sl.TITLE_BLOCK} D={D}: 미설명 3") == "counted"
