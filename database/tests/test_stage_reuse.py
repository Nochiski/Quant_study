"""묶음 7-3 — 아침 확정판 재사용(stg_fin_wise·stg_fin_wise_q, 플랜 2026-10-09-batch7-wise-dedup.md).

원장 내용 지문(키·sha256·fetched_at)·규칙 판본·코드 rev·baseline·픽스처·연도가 저녁 판과 같으면
아침에 다시 짓지 않고 저녁 판 파일을 하드링크한 새 `m_` 판으로 커밋한다. 하나라도 다르거나
실패하면 사유 한 줄을 남기고 일반 빌드로 간다(P1).
"""
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
from stage import __main__ as stage_main
from stage import build, fold, health, manifest, rules, snapshot
from test_stage_fold import _body, _r, _sha, snap_of

REV = "abc1234"
TABLE = "stg_fin_wise"
ROWS = [_r("005930", "cF3002", "Y", _body(3), "2026-10-01"),
        _r("005930", "cF4002", "Y", _body(2, "R"), "2026-10-01"),
        _r("000020", "cF3002", "Y", _body(2, "Z"), "2026-10-01"),
        _r("000020", "cF3002", "Q:IS", _body(2, "Q"), "2026-10-01")]


def _deployed(home: Path, rev: str | None) -> None:
    p = home / "DEPLOYED.json"
    if rev is None:
        p.unlink(missing_ok=True)
        return
    p.write_text(json.dumps({"rev": rev, "branch": "feat/v3-merge"}), encoding="utf-8")


def _run(home: Path, sid: str, basis: str, table: str = TABLE, *extra: str) -> int:
    return stage_main.main(["--table", table, "--snapshot-root", str(home / "snapshots"),
                            "--snapshot-id", sid, "--stage-root", str(home / "stage"),
                            "--basis", basis, *extra])


def _current(home: Path, table: str = TABLE) -> manifest.BuildRecord:
    m = manifest.load(home / "stage" / table / "MANIFEST.json")
    return next(b for b in m.builds if b.build_id == m.current_build)


def _edit_current(home: Path, **fields: object) -> None:
    """MANIFEST 현재 판 레코드의 필드를 바꾼다(옛 판·다른 판을 흉내)."""
    path = home / "stage" / TABLE / "MANIFEST.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for b in raw["builds"]:
        if b["build_id"] == raw["current_build"]:
            b.update(fields)
    path.write_text(json.dumps(raw), encoding="utf-8")


def _evening_dir(home: Path) -> Path:
    return home / "stage" / TABLE / f"v={_current(home).build_id}"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """QL_HOME — 저녁 스냅샷 s_e, DEPLOYED rev. 아침 스냅샷은 테스트가 뜬다(`_morning_snap`)."""
    monkeypatch.setenv("QL_HOME", str(tmp_path))
    snap_of(tmp_path, ROWS, sid="s_e")
    _deployed(tmp_path, REV)
    return tmp_path


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """build_table 호출 기록(표 이름) — 호출은 그대로 통과시킨다."""
    calls: list[str] = []
    real = build.build_table

    def wrapped(rule, *a, **kw):  # type: ignore[no-untyped-def]
        calls.append(rule.name)
        return real(rule, *a, **kw)

    monkeypatch.setattr(build, "build_table", wrapped)
    return calls


def _morning_snap(home: Path, rows: list[tuple] = ROWS) -> None:
    snap_of(home, rows, sid="s_m")


# ── G1 (옛 코드 FAIL) ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("table", ["stg_fin_wise", "stg_fin_wise_q"])
def test_g1_같은_원장이면_아침은_다시_짓지_않고_m_판으로_커밋한다(home: Path, spy: list[str],
                                                              table: str) -> None:
    _morning_snap(home)
    assert _run(home, "s_e", "evening", table) == 0
    evening = _current(home, table)
    spy.clear()
    assert _run(home, "s_m", "morning", table) == 0
    assert spy == []                                     # 옛 코드는 여기서 다시 짓는다
    morning = _current(home, table)
    assert morning.build_id.startswith("m_") and morning.snapshot_id == "s_m"
    assert morning.reused_from == evening.build_id and morning.code_rev == REV
    assert morning.content_hash == evening.content_hash
    assert morning.input_fingerprint == evening.input_fingerprint is not None
    # 재사용 판 content_hash = 같은 스냅샷 일반 빌드 판
    snap = snapshot.load_snapshot(home / "snapshots" / "s_m")
    r = build.build_table(rules.RULES[table], snap, home / "plain")
    assert r.ok and r.content_hash == morning.content_hash


# ── 회귀 가드: 조건을 하나씩 깨면 일반 빌드 ─────────────────────────────────────────────────────
def _new_blob(home: Path) -> None:
    _morning_snap(home, ROWS + [_r("005930", "cF3002", "Y", _body(4, "N"), "2026-10-02")])


def _same_key_other_body(home: Path) -> None:
    """같은 날 같은 키를 다른 원문으로 덮음 — fetched_at 은 그대로(지문의 sha256 축만 움직인다)."""
    first = ROWS[0]
    body = _body(5, "X")
    _morning_snap(home, [(*first[:4], body, _sha(body), len(body), first[7]), *ROWS[1:]])


def _fetched_at_only(home: Path) -> None:
    _morning_snap(home, [(*ROWS[0][:7], "2026-10-01T09:00:00"), *ROWS[1:]])


def _rules_version(home: Path) -> None:
    _morning_snap(home)
    _edit_current(home, rules_version="2.6.0")


def _code_rev_changed(home: Path) -> None:
    _morning_snap(home)
    _deployed(home, "zzz9999")


def _code_rev_missing_now(home: Path) -> None:
    _morning_snap(home)
    _deployed(home, None)


def _code_rev_missing_evening(home: Path) -> None:
    _morning_snap(home)
    _edit_current(home, code_rev=None)


def _current_is_morning(home: Path) -> None:
    _morning_snap(home)
    assert _run(home, "s_m", "morning") == 0              # 첫 아침(재사용) → 현재 판 m_
    assert _current(home).build_id.startswith("m_")


def _current_is_manual(home: Path) -> None:
    _morning_snap(home)
    assert _run(home, "s_e", "manual") == 0


def _current_is_reused(home: Path) -> None:
    _morning_snap(home)
    _edit_current(home, reused_from="e_20261001T120000_000000Z")


def _baseline_entry(home: Path) -> None:
    _morning_snap(home)
    (home / "stage" / "baseline.json").write_text(
        json.dumps({TABLE: {"thresholds": {"G7": 0.01}}}), encoding="utf-8")


def _year(home: Path) -> None:
    _morning_snap(home)
    for p in _evening_dir(home).glob("**/_meta.json"):
        meta = json.loads(p.read_text(encoding="utf-8"))
        meta["build_inputs"]["year_utc"] -= 1
        p.write_text(json.dumps(meta), encoding="utf-8")


def _off_file(home: Path) -> None:
    _morning_snap(home)
    (home / "stage" / "REUSE_OFF").touch()


def _parquet_damaged(home: Path) -> None:
    """저녁 판 parquet 1개를 한 행 모자란 정상 parquet 로 바꾼다 — 하드링크 뒤 해시 재계산이 잡는다."""
    _morning_snap(home)
    f = next(_evening_dir(home).glob("year=*/*.parquet"))
    tmp = f.with_suffix(".tmp")
    con = duckdb.connect()
    n = con.execute(f"SELECT count(*) FROM read_parquet('{f}')").fetchone()[0]  # type: ignore[index]
    con.execute(f"COPY (SELECT * FROM read_parquet('{f}') LIMIT {n - 1}) TO '{tmp}' (FORMAT PARQUET)")
    con.close()
    os.replace(tmp, f)


GUARDS: dict[str, tuple[Callable[[Path], None], str]] = {
    "새 blob": (_new_blob, "input_fingerprint"),
    "같은 키 다른 원문": (_same_key_other_body, "input_fingerprint"),
    "원문 같고 fetched_at 만 다름": (_fetched_at_only, "input_fingerprint"),
    "rules_version 다름": (_rules_version, "rules_version"),
    "code_rev 다름": (_code_rev_changed, "code_rev"),
    "code_rev 지금 없음": (_code_rev_missing_now, "code_rev"),
    "code_rev 저녁 판 없음": (_code_rev_missing_evening, "code_rev"),
    "현재 판 m_": (_current_is_morning, "current_not_evening"),
    "현재 판 b_": (_current_is_manual, "current_not_evening"),
    "현재 판 재사용 판": (_current_is_reused, "current_not_evening"),
    "baseline 항목 다름": (_baseline_entry, "build_inputs"),
    "연도 다름": (_year, "build_inputs"),
    "끄기 파일": (_off_file, "off_file"),
    "저녁 판 parquet 손상": (_parquet_damaged, "content_hash"),
}


def _declined_morning(home: Path, spy: list[str], capsys: pytest.CaptureFixture[str],
                      setup: Callable[[Path], None]) -> str:
    """저녁 빌드 → setup → 아침. 아침 출력을 돌려준다."""
    assert _run(home, "s_e", "evening") == 0
    setup(home)
    spy.clear()
    capsys.readouterr()
    assert _run(home, "s_m", "morning") == 0
    return capsys.readouterr().out


@pytest.mark.parametrize("case", list(GUARDS))
def test_조건이_하나라도_깨지면_사유_한_줄_뒤_일반_빌드(home: Path, spy: list[str],
                                                     capsys: pytest.CaptureFixture[str], case: str
                                                     ) -> None:
    setup, reason = GUARDS[case]
    out = _declined_morning(home, spy, capsys, setup)
    assert spy == [TABLE], out                                    # 일반 build_table 로 갔다
    assert f"reuse_declined reason={reason}" in out, out
    cur = _current(home)
    assert cur.build_id.startswith("m_") and cur.reused_from is None and cur.snapshot_id == "s_m"
    assert not (home / "stage" / "_tmp" / cur.build_id).exists()   # 반쯤 만든 판이 남지 않는다


def test_재사용_중_예외면_사유를_남기고_일반_빌드(home: Path, spy: list[str],
                                              capsys: pytest.CaptureFixture[str],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    def no_link(src: object, dst: object) -> None:
        raise OSError("cross-device link")

    _morning_snap(home)
    assert _run(home, "s_e", "evening") == 0
    monkeypatch.setattr(os, "link", no_link)
    spy.clear()
    capsys.readouterr()
    assert _run(home, "s_m", "morning") == 0
    out = capsys.readouterr().out
    assert spy == [TABLE] and "reuse_declined reason=error OSError: cross-device link" in out
    cur = _current(home)
    assert cur.reused_from is None and not (home / "stage" / "_tmp" / cur.build_id).exists()


def test_CLI_게이트_임계_override_가_있으면_재사용하지_않는다(home: Path, spy: list[str],
                                                           capsys: pytest.CaptureFixture[str]
                                                           ) -> None:
    _morning_snap(home)
    assert _run(home, "s_e", "evening") == 0
    spy.clear()
    capsys.readouterr()
    assert _run(home, "s_m", "morning", TABLE, "--g7", "0.5") == 0
    assert spy == [TABLE] and "reuse_declined reason=gate_threshold_override" in capsys.readouterr().out


def _hash_of(d: Path, work: Path) -> str:
    """빌드와 같은 content_hash. 빌드는 `v=` 없는 임시 경로에서 잰다 — hive 분할이 `v=<id>` 도 열로
    읽기 때문에 `v=` 밖으로 복사해서 잰다."""
    copy = work / "hash_copy"
    shutil.copytree(d, copy)
    con = duckdb.connect()
    try:
        return build._content_hash(con, str(copy / "year=*" / "*.parquet"))
    finally:
        con.close()


def test_저녁_판_meta_는_바이트_그대로이고_저녁_판이_지워져도_m_판은_읽힌다(home: Path,
                                                                     tmp_path: Path) -> None:
    _morning_snap(home)
    assert _run(home, "s_e", "evening") == 0
    e_dir = _evening_dir(home)
    before = {p: p.read_bytes() for p in e_dir.glob("**/_meta.json")}
    assert _run(home, "s_m", "morning") == 0
    cur = _current(home)
    assert cur.reused_from == e_dir.name[2:]
    assert {p: p.read_bytes() for p in e_dir.glob("**/_meta.json")} == before
    m_dir = home / "stage" / TABLE / f"v={cur.build_id}"
    meta = json.loads(next(m_dir.glob("**/_meta.json")).read_text(encoding="utf-8"))
    assert meta["build_id"] == cur.build_id and meta["snapshot_id"] == "s_m"
    assert meta["reused_from"] == e_dir.name[2:]
    assert [p["path"] for p in cur.partitions] == [f"v={cur.build_id}/year=2026"]
    shutil.rmtree(e_dir)                              # keep 3 GC 가 저녁 판을 지운 뒤
    assert _hash_of(m_dir, tmp_path / "work") == cur.content_hash


def test_재사용_판은_건전성_C1_C3_C4_C6_을_통과한다(home: Path) -> None:
    _morning_snap(home)
    assert _run(home, "s_e", "evening") == 0
    started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    assert _run(home, "s_m", "morning") == 0
    built_on = dt.datetime.now(health.KST).strftime("%Y%m%d")
    r = health.check_stage(home / "stage", "morning", "20261002", built_on=built_on,
                           tables={TABLE: "append_only"}, started_at=started)
    status = {c.name: c for c in r.checks}
    for name in ("C1", "C2", "C3", "C4", "C6"):
        assert status[name].status is health.Status.PASS, (name, status[name].detail)
    assert status["C4"].metrics["n_frozen"] == 1          # 계수·해시 동결 대조를 실제로 했다


def _stage_all_parse(log: Path) -> dict[str, str]:
    """`scripts/run_stage_all.sh:61-65` 의 해석 그대로."""
    script = (f"LOG={log}\n"
              "LINE=$(grep -E '^(ok|gate_failed|error)' \"$LOG\" | tail -1)\n"
              "STATUS=$(echo \"$LINE\" | awk '{print $1}')\n"
              "ROWS=$(echo \"$LINE\" | sed -n 's/.* rows=\\([0-9,]*\\).*/\\1/p'); "
              "SRC=$(echo \"$LINE\" | sed -n 's/.* src=\\([0-9,]*\\).*/\\1/p')\n"
              "EL=$(echo \"$LINE\" | sed -n 's/.* \\([0-9.]*\\)s$/\\1/p')\n"
              "printf '%s\\t%s\\t%s\\t%s' \"$STATUS\" \"$ROWS\" \"$SRC\" \"$EL\"\n")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    return dict(zip(("status", "rows", "src", "elapsed"), out.split("\t"), strict=True))


def test_출력_줄은_run_stage_all_해석이_그대로다(home: Path, tmp_path: Path,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    _morning_snap(home)
    assert _run(home, "s_e", "evening") == 0
    capsys.readouterr()
    assert _run(home, "s_m", "morning") == 0
    out = capsys.readouterr().out
    log = tmp_path / "stage_all.log"
    log.write_text(out, encoding="utf-8")
    got = _stage_all_parse(log)
    cur = _current(home)
    assert f"reused_from={cur.reused_from} " in out
    assert got["status"] == "ok" and got["rows"] == f"{cur.n_rows:,}" and got["src"]
    assert float(got["elapsed"]) >= 0
    # 거절 줄은 결과 줄로 잡히지 않는다
    _deployed(home, "zzz9999")
    capsys.readouterr()
    assert _run(home, "s_m", "morning") == 0
    out = capsys.readouterr().out
    log.write_text(out, encoding="utf-8")
    assert "reuse_declined" in out and _stage_all_parse(log)["status"] == "ok"


# ── 부정 테스트 (이 변형이면 위 가드가 FAIL 해야 한다) ─────────────────────────────────────────
def test_부정_지문에서_sha256_을_빼면_같은_키_다른_원문을_재사용해_버린다(
        home: Path, spy: list[str], capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    def without_sha(lite: object, bs: object) -> str:
        rows = sorted(lite.execute(  # type: ignore[attr-defined]
            "SELECT cmp_cd, ep, pkey, fetched_date, fetched_at FROM ws_raw").fetchall())
        return hashlib.sha256(repr(rows).encode()).hexdigest()

    monkeypatch.setattr(fold, "input_fingerprint", without_sha)
    _declined_morning(home, spy, capsys, _same_key_other_body)
    assert spy == [] and _current(home).reused_from is not None     # 가드 '같은 키 다른 원문' 이 FAIL
