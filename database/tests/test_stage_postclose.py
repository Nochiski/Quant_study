"""stage `stg_flow_postclose_kiwoom` — 장 마감 직후 원장(`postclose.db`) ka10060 (컷오버 PR-2 · T-4).

원장은 수집기 실물 함수(`daily.postclose.connect`·`insert_first`)로, 대조용 키움 원장은 운영 수집기의
`kw_daily.ensure_table`·`insert_rows` 로 만든다 — 스키마를 손으로 베끼지 않는다. 응답 행 값은
`test_stage_kiwoom` 의 ka10060 손계산 값과 같은 모양이다(투자자 13열 = 백만원, 개인만 순매도).

T = 20261013(화). 수집 시각은 UTC 원문 — 06:41Z = 15:41 KST, 07:00Z = 16:00 KST, 12:05Z = 21:05 KST.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import duckdb
import pytest
from daily import kw_daily, postclose
from stage import __main__ as stage_cli
from stage import build, freshness, gates, health, model, rules, rules_kiwoom, snapshot

NAME = "stg_flow_postclose_kiwoom"
FLOW = "stg_flow_daily_kiwoom"
T = "20261013"
KST = dt.timezone(dt.timedelta(hours=9))
RUN_STAGE_ALL = Path(__file__).resolve().parents[1] / "scripts" / "run_stage_all.sh"

_INV = ("-292", "12", "5", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10")


def _resp(cur: str, pred: str, vol: str, flows: Sequence[str]) -> dict[str, object]:
    """키움 ka10060 응답 한 행(`postclose.COLS` 열)."""
    return dict(zip(postclose.COLS, (T, cur, pred, vol, *flows), strict=True))


# (종목, 응답 행, price_valid, 받은 시각 UTC) — 000020 은 16:00 뒤에 받아 가격이 애프터마켓 값이다
ROWS: tuple[tuple[str, dict[str, object], bool, str], ...] = (
    ("005930", _resp("-262500", "-19000", "1234567", _INV), True, "2026-10-13T06:41:05"),
    ("0001A0", _resp("+51900", "+100", "0", ("0",) * 13), True, "2026-10-13T06:41:06"),
    ("000020", _resp("+1010", "+10", "3000", ("-5",) + ("5",) * 12), False, "2026-10-13T07:00:03"),
)


def _postclose_db(path: Path) -> Path:
    """수집기 실물 함수로 장 마감 원장을 만든다(스키마 = `kw_daily.ensure_table` + fetched_at·price_valid)."""
    con = postclose.connect(path)
    try:
        for ticker, row, valid, received in ROWS:
            postclose.insert_first(con, postclose.COLS, ticker, [row], collected_at=received,
                                   fetched_at="2026-10-13T06:41:00", price_valid=valid)
    finally:
        con.close()
    return path


def _kiwoom_db(path: Path, stamp: str) -> Path:
    """운영 수집기와 같은 함수로 저녁 키움 원장 표에 같은 응답 행을 넣는다(collected_at = 실행 스탬프)."""
    con = sqlite3.connect(path)
    try:
        kw_daily.ensure_table(con, postclose.TABLE, postclose.COLS)
        for ticker, row, _valid, _received in ROWS:
            kw_daily.insert_rows(con, postclose.TABLE, postclose.COLS, ticker, [row], "ka10060", stamp)
        con.commit()
    finally:
        con.close()
    return path


def _snap(tmp_path: Path, db: str, src: Path, sid: str) -> snapshot.Snapshot:
    return snapshot.make_snapshot({db: src}, tmp_path / "snapshots", snapshot_id=sid)


def _flow_fixtures(tmp_path: Path) -> Path:
    """`stg_flow_daily_kiwoom` 의 unit_scale 13열 임시 골든 — 없으면 그 표의 G4 가 실패한다(§9)."""
    want = dict(zip((f"{k}_krw" for k in kw_daily.FLOW_KEYS),
                    (str(int(v) * 1_000_000) for v in _INV), strict=True))
    fx = [{"key": {"ticker": "005930", "date": "2026-10-13"}, "column": c, "expect": v,
           "measured_sql": "", "measured_at": "2026-10-10"} for c, v in want.items()]
    path = tmp_path / "fx_flow.json"
    path.write_text(json.dumps(fx), encoding="utf-8")
    return path


def _read(r: build.BuildResult) -> duckdb.DuckDBPyConnection:
    if r.out_dir is None:
        raise AssertionError(f"build not committed: {r.status} {r.table}")
    con = duckdb.connect()
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{r.out_dir}/year=*/*.parquet', "
                f"hive_partitioning=true)")
    return con


def _failed(r: build.BuildResult) -> list[str]:
    return [f"{g.name}:{g.detail}" for g in r.gates if g.status is gates.GateStatus.FAIL]


def _gate(r: build.BuildResult, name: str) -> gates.GateResult:
    return next(g for g in r.gates if g.name == name)


# ── 규칙 ──────────────────────────────────────────────────────────────────────
def test_postclose_rule_shares_the_ka10060_rules_and_adds_two_columns() -> None:
    """파싱은 저녁 표와 **같은 규칙 객체**다(P4) — 복사본이면 한쪽만 고쳐질 수 있다."""
    flow, post = rules_kiwoom.STG_FLOW_DAILY_KIWOOM, rules_kiwoom.STG_FLOW_POSTCLOSE_KIWOOM
    n = len(flow.columns)
    assert all(a is b for a, b in zip(post.columns[:n], flow.columns, strict=True))
    assert [(c.name, c.kind) for c in post.columns[n:]] == [
        ("price_valid", model.KIND_BOOL), ("collected_at", model.KIND_TEXT)]
    assert post.extras is flow.extras and post.invariants is flow.invariants
    assert post.golden_from is flow
    assert (post.sources[0].db, post.sources[0].table) == ("postclose", postclose.TABLE)
    assert post.write_mode == "first_write_wins"            # 원장 INSERT OR IGNORE(첫 관측 유지)
    assert rules.RULES[NAME] is post
    assert rules.SOLO_LEDGER_FILES == {"postclose": "postclose.db"}
    assert "postclose" not in rules.LEDGER_FILES            # 연구 체인 스냅샷 세트 밖


def test_only_the_postclose_table_inherits_a_golden() -> None:
    """골든 물려받기는 장 마감 표 하나만 — 다른 표가 조용히 G4 를 건너뛰지 못하게 레지스트리로 고정한다."""
    assert {n for n, r in rules.RULES.items() if r.golden_from is not None} == {NAME}


# ── 빌드 ──────────────────────────────────────────────────────────────────────
def test_postclose_build_parses_like_ka10060_and_keeps_price_valid_and_receipt_time(
    tmp_path: Path
) -> None:
    snap = _snap(tmp_path, "postclose", _postclose_db(tmp_path / "postclose.db"), "snap_pc")
    r = build.build_table(rules.RULES[NAME], snap, tmp_path / "stage")   # 픽스처 파일 없음
    assert r.ok, _failed(r)
    assert (r.n_src, r.n_rows, r.n_reject) == (3, 3, 0)
    con = _read(r)
    got = con.execute(
        "SELECT ticker, date, close_krw, close_krw_dir, pred_pre_krw, volume_shr, ind_invsr_krw, "
        "frgnr_invsr_krw, natfor_krw, src_api, price_valid, collected_at, observed_date, "
        "available_date, available_basis, _src FROM t ORDER BY ticker").fetchall()
    d = date(2026, 10, 13)
    assert got == [
        ("000020", d, 1010, 1, 10, 3000, -5_000_000, 5_000_000, 5_000_000, "ka10060", False,
         "2026-10-13T07:00:03", d, d, "default", "ka10060"),
        ("0001A0", d, 51900, 1, 100, 0, 0, 0, 0, "ka10060", True,
         "2026-10-13T06:41:06", d, d, "default", "ka10060"),
        ("005930", d, 262500, -1, -19000, 1234567, -292_000_000, 12_000_000, 10_000_000,
         "ka10060", True, "2026-10-13T06:41:05", d, d, "default", "ka10060"),
    ]
    assert con.execute("SELECT miss_kind.price_valid FROM t WHERE ticker = '005930'"
                       ).fetchone() == (None,)
    # unit_scale 13열은 저녁 표의 골든이 지킨다 — 이 표에 고정 행이 없어 골든 행을 둘 수 없다
    g4 = _gate(r, "G4")
    assert g4.status is gates.GateStatus.SKIP
    assert g4.detail == "golden_inherited"       # 일반 no_fixtures 와 갈라 허용표(K1-7a)가 표 한정으로 허용한다
    assert g4.metrics["golden_from"] == FLOW
    inherited = g4.metrics["unit_scale_inherited"]
    assert isinstance(inherited, list) and sorted(inherited) == sorted(
        c.name for c in rules.RULES[FLOW].columns if c.unit_scale is not None)


def test_g4_inherits_only_an_identical_rule(tmp_path: Path) -> None:
    """스케일이 다른 열은 이름이 같아도 물려받지 않는다 — 골든 없는 unit_scale 열 = G4 실패 그대로."""
    post = rules.RULES[NAME]
    snap = _snap(tmp_path, "postclose", _postclose_db(tmp_path / "postclose.db"), "snap_g4")
    bad = tuple(dataclasses.replace(c, unit_scale=1_000) if c.name == "orgn_krw" else c
                for c in post.columns)
    r = build.build_table(dataclasses.replace(post, columns=bad), snap, tmp_path / "s1")
    assert r.status is build.BuildStatus.GATE_FAILED
    assert _gate(r, "G4").metrics["unit_scale_uncovered"] == ["orgn_krw"]
    r2 = build.build_table(dataclasses.replace(post, golden_from=None), snap, tmp_path / "s2")
    assert r2.status is build.BuildStatus.GATE_FAILED
    uncovered = _gate(r2, "G4").metrics["unit_scale_uncovered"]
    assert isinstance(uncovered, list) and len(uncovered) == 13


def test_g4_does_not_inherit_across_source_trs(tmp_path: Path) -> None:
    """열 규칙이 같아도 원천 TR 이 다르면 물려받지 않는다 — 단위가 다른 TR 이 같은 헬퍼 ColumnRule 을 다시 쓰면
    골든이 그 TR 의 단위를 지키지 않는다(1000배 오차가 SKIP 으로 통과하던 구멍)."""
    post = rules.RULES[NAME]
    snap = _snap(tmp_path, "postclose", _postclose_db(tmp_path / "postclose.db"), "snap_tr")
    other_tr = (dataclasses.replace(post.sources[0], src_tag="ka10014"),)
    r = build.build_table(dataclasses.replace(post, sources=other_tr), snap, tmp_path / "stage")
    assert r.status is build.BuildStatus.GATE_FAILED
    g4 = _gate(r, "G4")
    uncovered = g4.metrics["unit_scale_uncovered"]
    assert isinstance(uncovered, list) and len(uncovered) == 13
    assert "unit_scale_inherited" not in g4.metrics
    assert "원천 TR" in str(g4.metrics["golden_from_rejected"])


def test_same_raw_rows_give_the_same_shared_columns_as_the_evening_table(tmp_path: Path) -> None:
    """재생 대조 — 같은 응답 행을 저녁 키움 원장(21:05 실행 스탬프)과 장 마감 원장(행별 수신 시각)에
    넣고 두 표를 지으면, 저녁 표가 내는 열은 전부 같다. 장 마감 표의 덧붙인 두 열만 다르다."""
    kw = _snap(tmp_path, "kiwoom", _kiwoom_db(tmp_path / "kiwoom.db", "2026-10-13T12:05:00"),
               "snap_kw")
    pc = _snap(tmp_path, "postclose", _postclose_db(tmp_path / "postclose.db"), "snap_pc")
    r_kw = build.build_table(rules.RULES[FLOW], kw, tmp_path / "stage",
                             fixtures_path=_flow_fixtures(tmp_path))
    r_pc = build.build_table(rules.RULES[NAME], pc, tmp_path / "stage")
    assert r_kw.ok and r_pc.ok, _failed(r_kw) + _failed(r_pc)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW kw AS SELECT * FROM read_parquet('{r_kw.out_dir}/year=*/*.parquet', "
                f"hive_partitioning=true)")
    con.execute(f"CREATE VIEW pc AS SELECT * FROM read_parquet('{r_pc.out_dir}/year=*/*.parquet', "
                f"hive_partitioning=true)")
    kw_cols = [str(r[0]) for r in con.execute("DESCRIBE kw").fetchall()]
    pc_cols = [str(r[0]) for r in con.execute("DESCRIBE pc").fetchall()]
    assert set(pc_cols) - set(kw_cols) == {"price_valid", "collected_at"}
    castable = [c.name for c in rules.RULES[FLOW].castable_columns]
    # `v` 는 경로의 빌드 id(하이브 열)라 내용이 아니다. miss_kind 는 장 마감 표에 price_valid 필드가 더 있어 열별로 본다
    shared = [f'"{c}"' for c in kw_cols if c not in ("miss_kind", "v")] + [
        f'miss_kind."{c}" AS "mk_{c}"' for c in castable]
    sel = ", ".join(shared)
    a = con.execute(f"SELECT {sel} FROM kw ORDER BY ticker").fetchall()
    b = con.execute(f"SELECT {sel} FROM pc ORDER BY ticker").fetchall()
    assert len(a) == 3 and a == b


# ── CLI 단독 빌드 ─────────────────────────────────────────────────────────────
def test_cli_builds_the_table_alone_from_a_postclose_only_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """원장 폴더에 postclose.db 하나만 있어도 짓는다 — 스냅샷도 그 원장 하나만 뜬다."""
    raw = tmp_path / "raw"
    raw.mkdir()
    _postclose_db(raw / "postclose.db")
    rc = stage_cli.main(["--table", NAME, "--raw-dir", str(raw), "--stage-root",
                         str(tmp_path / "stage"), "--snapshot-root", str(tmp_path / "snapshots"),
                         "--basis", "evening"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"ok table={NAME} build=e_" in out and "basis=evening rows=3" in out
    (snap_dir,) = (tmp_path / "snapshots").iterdir()
    assert sorted(p.name for p in snap_dir.glob("*.db")) == ["postclose.db"]


# ── 건전성 C1~C6 ──────────────────────────────────────────────────────────────
def test_health_c1_to_c6_judge_the_postclose_table_alone(tmp_path: Path) -> None:
    """장 마감 체인이 이 표 하나만 판정할 때(`tables=`) C1~C6 이 전부 돈다 — C6 는 허용 지연 10일."""
    assert freshness.judgement(NAME) == (10, "")
    rule = rules.RULES[NAME]
    snap = _snap(tmp_path, "postclose", _postclose_db(tmp_path / "postclose.db"), "snap_h")
    root = tmp_path / "stage"
    for _ in range(2):                                     # 두 번째 판 = 같은 입력 → C3·C4 대조 대상
        r = build.build_table(rule, snap, root, build_id=model.make_build_id("evening"))
        assert r.ok, _failed(r)
    today = dt.datetime.now(KST).strftime("%Y%m%d")
    h = health.check_stage(root, "evening", T, built_on=today, tables={NAME: rule.write_mode})
    by = {c.name: c for c in h.checks}
    assert h.ok, h.summary()
    assert by["C1"].metrics["n_ok"] == 1
    assert by["C3"].metrics["n_partition_tables"] == 1     # first_write_wins — 전 파티션 비감소
    assert by["C4"].metrics["n_frozen"] == 1               # 같은 입력 → 같은 해시
    assert by["C6"].status is health.Status.PASS and by["C6"].metrics["n_checked"] == 1


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 필요")
def test_run_stage_all_skips_the_postclose_table_and_records_it(tmp_path: Path) -> None:
    """연구 체인 전량 빌드는 이 표를 짓지 않고 skipped.txt 에 남긴다 — stage.health 의 --skip 입력이라
    연구 판 C1 이 '판 없음'으로 깨지지 않고, 뺀 사실은 리포트에 남는다."""
    root = tmp_path / "ql"
    (root / "scripts").mkdir(parents=True)
    (root / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(RUN_STAGE_ALL, root / "scripts" / "run_stage_all.sh")
    py = root / ".venv" / "bin" / "python"
    py.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    py.chmod(0o755)
    env = dict(os.environ, QL_BUILD_LOCK_HELD="1")
    p = subprocess.run(["bash", str(root / "scripts" / "run_stage_all.sh"), "snap_x"], env=env,
                       capture_output=True, text=True, timeout=120, check=False)
    assert "ALL_DONE" in p.stdout, p.stdout + p.stderr
    skipped = (root / "logs" / "stage_all" / "skipped.txt").read_text(encoding="utf-8").split()
    assert NAME in skipped
    assert not (root / "logs" / "stage_all" / f"{NAME}.log").exists()
    assert (root / "logs" / "stage_all" / f"{FLOW}.log").exists()
