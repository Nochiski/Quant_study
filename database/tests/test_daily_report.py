"""scripts/daily_report.py — 통합 일일 리포트 조립·등급 판정. 플랜 v1 §9 Task 6.1 / v2 §2-1 알림 정책.

발송은 하지 않는다(`_send` 는 호출 여부만 본다). 입력은 전부 tmp 로 조립한다.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# scripts/ 는 패키지가 아니라 위 sys.path 주입 뒤에야 import 된다.
import daily_report as dr

# daily 패키지는 conftest 가 src/ 를 sys.path 에 올려 준다.
from daily import runlog

D = "20260910"


# ── 픽스처 조립 ────────────────────────────────────────────────────────────
def _home(tmp_path: Path) -> Path:
    home = tmp_path / "ql"
    (home / "logs" / "health").mkdir(parents=True)
    (home / "data" / "deliver").mkdir(parents=True)
    (home / "data" / "raw").mkdir(parents=True)
    (home / "locks").mkdir(parents=True)
    return home


def _lock_glob(home: Path) -> str:
    for name in ("quant_ledger_raw.lock", "quant_ledger_build.lock"):
        (home / "locks" / name).write_text("", encoding="utf-8")
    return str(home / "locks" / "quant_ledger_*.lock")


def _check(name: str, level: str, status: str) -> dict[str, object]:
    return {"name": name, "level": level, "status": status, "value": 0, "expected": "기대", "detail": ""}


def _ledger(home: Path, *, checks: list[dict[str, object]] | None = None, d: str = D) -> None:
    checks = checks if checks is not None else [_check("krx.rows", "required", "pass")]
    n_pass = sum(1 for c in checks if c["status"] == "pass")
    ok = not any(c["status"] == "fail" and c["level"] in ("required", "halt") for c in checks)
    payload = {"date": d, "ok": ok, "universe_size": 2563, "checks": checks,
               "summary": f"원장 건전성 {d}: {'OK' if ok else 'FAIL'} pass {n_pass}/{len(checks)}"}
    (home / "logs" / "health" / f"{d}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _stage(home: Path, basis: str, *, ok: bool = True, d: str = D) -> None:
    payload = {"date": d, "basis": basis, "ok": ok, "summary": f"stage {basis} {'OK' if ok else 'FAIL'} 66/66"}
    (home / "logs" / "health" / f"stage_{d}_{basis}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _latest(home: Path, basis: str, *, health: object = True, d: str = D,
            elapsed_s: object = 1342) -> None:
    payload = {"date": d, "basis": basis, "generated_at": "2026-09-10T18:38:20+09:00",
               "health": health, "elapsed_s": elapsed_s}
    (home / "data" / "deliver" / f"latest_{basis}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _ledger_evening(home: Path, *, kiwoom_rc: int = 0, dart_rc: int = 0, wise_rc: int = 0, d: str = D) -> None:
    payload = {"date": d, "finished_at": "2026-09-10T09:39:11Z", "kiwoom_rc": kiwoom_rc,
               "dart_rc": dart_rc, "wise_rc": wise_rc,
               "kiwoom_done_at": "2026-09-10T18:12:03+09:00", "wise_done_at": "2026-09-10T18:09:41+09:00"}
    (home / "data" / "deliver" / "ledger_evening.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _runs(home: Path, rows: tuple[tuple[str, str], ...] = (("ledger_chain", "ok"), ("evening_chain", "ok"))) -> None:
    db = str(home / "data" / "raw" / "daily_run.db")
    for source, status in rows:
        rid = runlog.start(db, date=D, source=source)
        if status != "running":
            runlog.finish(db, rid, status=status, detail=None)


def _full(tmp_path: Path) -> Path:
    """정상 하루 — 전 입력이 다 있고 전부 통과."""
    home = _home(tmp_path)
    _ledger(home)
    for basis in ("evening", "morning"):
        _stage(home, basis)
        _latest(home, basis)
    _ledger_evening(home)
    _runs(home)
    return home


def _build(home: Path, *, disk_free_gb: float = 283.4) -> dr.ReportResult:
    return dr.build_report(str(home), D, lock_glob=_lock_glob(home), disk_free_gb=disk_free_gb)


# ── 등급 판정 ──────────────────────────────────────────────────────────────
def test_전부_정상이면_info_이고_각_절이_메시지에_들어간다(tmp_path: Path) -> None:
    r = _build(_full(tmp_path))
    assert r.status is dr.ReportStatus.INFO
    assert r.missing == ()
    for token in ("원장 건전성", "stage", "빌드", "저녁 원장", "런", "디스크", "락"):
        assert token in r.text, f"{token} 절이 빠졌다: {r.text}"


def test_원장_required_실패는_crit(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _ledger(home, checks=[_check("krx.rows", "required", "fail")])
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "krx.rows" in r.text


def test_kael_키_사용은_crit_이고_검사명이_이유에_찍힌다(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _ledger(home, checks=[_check("krx.rows", "required", "pass"), _check("dart.key.kael", "halt", "fail")])
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "dart.key.kael" in r.text


def test_디스크_50GB_미만은_crit(tmp_path: Path) -> None:
    r = _build(_full(tmp_path), disk_free_gb=48.2)
    assert r.status is dr.ReportStatus.CRIT
    assert "48.2" in r.text


def test_런_failed_는_수집_실패로_crit(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _ledger(home)
    _runs(home, rows=(("ledger_chain", "failed"),))
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "ledger_chain" in r.text


def test_런이_아직_running_이면_warn(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _ledger(home)
    _runs(home, rows=(("evening_chain", "running"),))
    r = _build(home)
    assert r.status is dr.ReportStatus.WARN


def test_저녁_원장_rc_0이_아니면_crit(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _ledger_evening(home, wise_rc=2)
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "wise_rc=2" in r.text


def test_빌드_health_실패는_게이트_폐기로_crit(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _latest(home, "evening", health=False)
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "evening" in r.text


def test_stage_게이트_실패도_crit(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _stage(home, "morning", ok=False)
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT


def test_건전성_warn_만_실패하면_warn(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _ledger(home, checks=[_check("krx.rows", "required", "pass"), _check("wise.cov_rate", "warn", "fail")])
    r = _build(home)
    assert r.status is dr.ReportStatus.WARN
    assert "wise.cov_rate" in r.text


# ── 결측 처리 ──────────────────────────────────────────────────────────────
def test_입력_결측은_없음으로만_적고_등급을_안_올린다(tmp_path: Path) -> None:
    """파일 없음은 워치독 몫이다 — 리포트는 목록만 남기고 info 를 유지한다."""
    home = _home(tmp_path)
    _ledger(home)
    _runs(home)
    r = _build(home)
    assert r.status is dr.ReportStatus.INFO
    assert "logs/health/stage_20260910_evening.json" in r.missing
    assert "data/deliver/latest_morning.json" in r.missing
    assert "data/deliver/ledger_evening.json" in r.missing
    assert "없음" in r.text


def test_날짜가_다른_인계_파일은_결측으로_본다(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _latest(home, "evening", d="20260909")
    r = _build(home)
    assert r.status is dr.ReportStatus.INFO
    assert any("latest_evening.json" in m for m in r.missing), r.missing


def test_원장_리포트가_없어도_등급은_안_올라간다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _runs(home)
    r = _build(home)
    assert r.status is dr.ReportStatus.INFO
    assert "logs/health/20260910.json" in r.missing


def test_daily_run_db_가_없으면_결측으로만_적는다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _ledger(home)
    r = _build(home)
    assert r.status is dr.ReportStatus.INFO
    assert "data/raw/daily_run.db" in r.missing


# ── 메시지 ────────────────────────────────────────────────────────────────
def test_메시지는_3900자를_넘지_않는다(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _ledger(home, checks=[_check(f"src.검사항목이름이제법긴경우{i:03d}", "warn", "fail") for i in range(400)])
    r = _build(home)
    assert len(r.text) <= dr.MAX_TEXT


def test_락_점유_여부가_표시된다(tmp_path: Path) -> None:
    home = _full(tmp_path)
    r = _build(home)
    assert "raw=" in r.text and "build=" in r.text


def test_dry_run_은_발송하지_않고_출력만_한다(tmp_path: Path, capsys, monkeypatch) -> None:
    home = _full(tmp_path)
    sent: list[object] = []
    monkeypatch.setattr(dr, "_send", lambda *a, **k: sent.append(a) or True)
    rc = dr.main(["--date", D, "--home", str(home), "--dry-run", "--lock-glob", _lock_glob(home)])
    assert rc == 0
    assert sent == []
    assert "원장 건전성" in capsys.readouterr().out


def test_build_chain_이_쓰는_dict_모양의_health_와_elapsed_를_판정한다(tmp_path: Path) -> None:
    """생산자(`build_chain.sh` deliver_step)는 `health: {"stage","equity"}`·`elapsed_s: {"stage","equity"}`
    dict 를 쓴다. bool·int 픽스처만 통과하던 판정이 실물에서는 보류(info)로 떨어졌다(검수 R4-02)."""
    home = _full(tmp_path)
    _latest(home, "evening", health={"stage": "ok", "equity": "fail"},
            elapsed_s={"stage": 1350, "equity": 480})
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "빌드 evening 게이트 폐기" in r.text
    assert "1830초" in r.text            # 1350 + 480
    _latest(home, "evening", health={"stage": "ok", "equity": "ok"},
            elapsed_s={"stage": 1350, "equity": 480})
    assert _build(home).status is not dr.ReportStatus.CRIT
