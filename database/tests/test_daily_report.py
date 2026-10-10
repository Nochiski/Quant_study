"""scripts/daily_report.py — 통합 일일 리포트 조립·등급 판정. 플랜 v1 §9 Task 6.1 / v2 §2-1 알림 정책.

발송은 하지 않는다(`_send` 는 호출 여부만 본다). 입력은 전부 tmp 로 조립한다.
"""
import json
import sys
from pathlib import Path

import pytest

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


def _ledger_evening(home: Path, *, kiwoom_rc: int = 0, dart_rc: int = 0, wise_rc: int = 0,
                    d: str = D, extra: dict[str, object] | None = None) -> None:
    """`extra` = 옛 픽스처에 없던 키(예: WISE 부분 실패 `wise_n_bad`·`wise_bad_summary`)."""
    payload = {"date": d, "finished_at": "2026-09-10T09:39:11Z", "kiwoom_rc": kiwoom_rc,
               "dart_rc": dart_rc, "wise_rc": wise_rc,
               "kiwoom_done_at": "2026-09-10T18:12:03+09:00",
               "wise_done_at": "2026-09-10T18:09:41+09:00", **(extra or {})}
    (home / "data" / "deliver" / "ledger_evening.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _runs(home: Path, rows: tuple[tuple[str, ...], ...] = (("ledger_chain", "ok"), ("evening_chain", "ok"))) -> None:
    """런 기록. 행은 (source, status[, detail])."""
    db = str(home / "data" / "raw" / "daily_run.db")
    for source, status, *detail in rows:
        rid = runlog.start(db, date=D, source=source)
        if status != "running":
            runlog.finish(db, rid, status=status, detail=detail[0] if detail else None)


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


# `daily_evening.sh` 가 실패한 저녁 체인 런에 남기는 detail 모양(:178-184) — 갈래별 rc 와 실패 갈래
_EVENING_DART_ONLY = ("kiwoom_rc=0 dart_rc=2 wise_rc=0 kiwoom_done_at=2026-09-10T21:12:03+09:00 "
                      "wise_done_at=2026-09-10T18:09:41+09:00 failed= DART(rc=2)")
_EVENING_KIWOOM_TOO = ("kiwoom_rc=1 dart_rc=2 wise_rc=0 kiwoom_done_at=2026-09-10T21:12:03+09:00 "
                       "wise_done_at=2026-09-10T18:09:41+09:00 failed= 키움(rc=1) DART(rc=2)")
_EVENING_WISE_TOO = ("kiwoom_rc=0 dart_rc=2 wise_rc=1 kiwoom_done_at=2026-09-10T21:12:03+09:00 "
                     "wise_done_at=2026-09-10T18:09:41+09:00 failed= DART(rc=2) WISE(rc=1)")
_EVENING_KIWOOM_ONLY = ("kiwoom_rc=1 dart_rc=0 wise_rc=0 kiwoom_done_at=2026-09-10T21:12:03+09:00 "
                        "wise_done_at=2026-09-10T18:09:41+09:00 failed= 키움(rc=1)")


def _recovery_day(tmp_path: Path, *, dart: tuple[str, ...], dart_rc: int = 2,
                  kiwoom_rc: int = 0, wise_rc: int = 0, evening: tuple[str, ...] = ()) -> Path:
    """저녁 dart 런(18:05) 뒤 아침 dart 런(06:00)이 같은 D 를 다시 판정한 날.
    `dart` = 런 순서대로의 상태. `evening` = 실패한 저녁 체인 런들의 detail(순서대로)."""
    home = _home(tmp_path)
    _ledger(home)
    for basis in ("evening", "morning"):
        _stage(home, basis)
        _latest(home, basis)
    _ledger_evening(home, dart_rc=dart_rc, kiwoom_rc=kiwoom_rc, wise_rc=wise_rc)
    chain = tuple(("evening_chain", "failed", e) for e in evening)
    _runs(home, rows=(("dart", dart[0]), *chain, ("ledger_chain", "ok"),
                      *(("dart", s) for s in dart[1:])))
    return home


def test_저녁_dart_실패를_아침_dart_런이_회복하면_crit_이_아니다(tmp_path: Path) -> None:
    """M-3 — 저녁 dart 가 유닛 하나를 못 받아 gate_failed, 06:00 런이 그 유닛을 받아 ok
    (A-05 의 설계된 재시도 경로). 옛 판정은 실패 런이 하나라도 있으면 crit, 저녁 dart_rc≠0 도
    crit 이었다."""
    r = _build(_recovery_day(tmp_path, dart=("gate_failed", "ok")))
    assert r.status is not dr.ReportStatus.CRIT, r.text
    assert "회복 dart" in r.text
    assert "DART rc=2(뒤 런에서 회복)" in r.text


def test_저녁_체인이_DART_하나로만_실패했고_dart_가_회복되면_crit_이_아니다(tmp_path: Path) -> None:
    """M-3 운영 모양 — 저녁 DART 갈래가 실패하면 `daily_evening.sh` 는 evening_chain 런도 failed 로
    남긴다. 원인이 DART 하나뿐이고 dart 가 아침에 회복됐으면 저녁 체인 실패도 회복이다."""
    r = _build(_recovery_day(tmp_path, dart=("gate_failed", "ok"), evening=(_EVENING_DART_ONLY,)))
    assert r.status is not dr.ReportStatus.CRIT, r.text
    assert "회복 dart(" in r.text and "회복 evening_chain(DART 뒤 런 회복)" in r.text


@pytest.mark.parametrize(("detail", "kiwoom_rc", "wise_rc"), [
    (_EVENING_KIWOOM_TOO, 1, 0),
    (_EVENING_WISE_TOO, 0, 1),
    ("dart_rc=2", 0, 0),                          # rc 일부만 — 키움·WISE 를 확인할 수 없다
    # 모르는 갈래(krx_rc)가 실패 — 갈래가 늘어도 거짓 회복이 열리지 않는다(P1)
    ("kiwoom_rc=0 dart_rc=2 wise_rc=0 krx_rc=1 failed= DART(rc=2) KRX(rc=1)", 0, 0),
    ("kiwoom_rc=0 dart_rc=0 wise_rc=0", 0, 0),   # 세 rc 가 다 0 인데 failed — 원인 미상은 회복 아님
    ("", 0, 0),
    ("형식 미상", 0, 0),
], ids=["kiwoom", "wise", "dart_rc_only", "unknown_branch", "all_zero", "empty", "unknown"])
def test_저녁_체인_실패_원인이_DART_만이_아니거나_모르면_dart_가_회복돼도_crit(
        tmp_path: Path, detail: str, kiwoom_rc: int, wise_rc: int) -> None:
    """키움·WISE 갈래도 실패했거나, detail 로 원인을 가릴 수 없으면 지금처럼 crit(P1)."""
    home = _recovery_day(tmp_path, dart=("gate_failed", "ok"), evening=(detail,),
                         kiwoom_rc=kiwoom_rc, wise_rc=wise_rc)
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "수집 실패 런 evening_chain" in r.text
    assert "회복 evening_chain" not in r.text


def test_저녁_체인_실패_런이_둘이면_전부_DART_만이어야_회복이다(tmp_path: Path) -> None:
    """키움만 실패한 저녁 체인 → 재실행에서 DART 만 실패 — 앞 런의 키움 실패가 남아 crit."""
    r = _build(_recovery_day(tmp_path, dart=("gate_failed", "ok"), kiwoom_rc=0,
                             evening=(_EVENING_KIWOOM_ONLY, _EVENING_DART_ONLY)))
    assert r.status is dr.ReportStatus.CRIT
    assert "수집 실패 런 evening_chain" in r.text
    assert "회복 evening_chain" not in r.text


def test_dart_마지막_런도_실패면_crit(tmp_path: Path) -> None:
    """저녁 dart_rc≠0 + 아침 dart 도 실패 — dart·저녁 체인·저녁 원장 모두 crit 그대로."""
    r = _build(_recovery_day(tmp_path, dart=("gate_failed", "gate_failed"),
                             evening=(_EVENING_DART_ONLY,)))
    assert r.status is dr.ReportStatus.CRIT
    assert "수집 실패 런 dart, evening_chain" in r.text
    assert "저녁 원장 수집 실패 dart_rc=2" in r.text
    assert "회복" not in r.text


def test_dart_ok_뒤_런이_실패하면_마지막_런_기준으로_crit(tmp_path: Path) -> None:
    """마지막 런 = run_id 가 가장 큰 런 — 앞 런 ok 가 뒤 런 실패를 덮지 않는다."""
    r = _build(_recovery_day(tmp_path, dart=("ok", "gate_failed"), dart_rc=0))
    assert r.status is dr.ReportStatus.CRIT
    assert "수집 실패 런 dart" in r.text
    assert "회복" not in r.text


def test_dart_kael_키_런은_뒤_런이_ok_여도_회복으로_덮지_않는다(tmp_path: Path) -> None:
    """v3 프로덕션 키 사용은 수집 실패가 아니라 사고다 — 사이에 다른 실패 런이 끼고 마지막 런이
    ok 여도 지우지 않는다(P1). 저녁 절도 같은 판정이라 '아침에 회복' 으로 적지 않는다."""
    r = _build(_recovery_day(tmp_path, dart=("kael_key_used", "gate_failed", "ok"),
                             evening=(_EVENING_DART_ONLY,)))
    assert r.status is dr.ReportStatus.CRIT
    assert "수집 실패 런 dart, evening_chain" in r.text
    assert "저녁 원장 수집 실패 dart_rc=2" in r.text
    assert "회복" not in r.text


def test_키움_fetch_는_뒤_런이_ok_여도_앞_런_실패가_crit(tmp_path: Path) -> None:
    """키움 fetch 는 런마다 TR 이 다르다(저녁 ka10060·ka10014 · 08:10 ka10008) — 뒤 런 ok 가 앞 런의
    결손을 메웠다는 뜻이 아니라 마지막 런 규칙을 걸지 않는다."""
    home = _full(tmp_path)
    _runs(home, rows=(("kiwoom_fetch", "coverage_failed"), ("kiwoom_fetch", "ok")))
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "수집 실패 런 kiwoom_fetch" in r.text


@pytest.mark.parametrize("status", ["cutoff", "late", "session_exception"])
def test_장_마감_수집의_정상_종료_상태는_crit_이_아니라_warn(tmp_path: Path, status: str) -> None:
    """수집기가 정상 종료로 정한 상태(`runlog.WARN_STATUSES`) — 16:00 컷오프·늦은 시작·세션 예외일.
    남은 종목은 QL-D 가 21:05 저녁 값으로 메운다. 실패 상태(error)는 그대로 crit."""
    home = _full(tmp_path)
    _runs(home, rows=(("ledger_chain", "ok"), ("evening_chain", "ok"),
                      ("kiwoom_postclose", status)))
    r = _build(home)
    assert r.status is dr.ReportStatus.WARN
    assert f"kiwoom_postclose {status}" in r.text and "수집 실패 런" not in r.text


def test_장_마감_수집은_같은_날_재실행이_ok_면_회복(tmp_path: Path) -> None:
    """kiwoom_postclose 는 런마다 그날 대상 전체를 다시 본다(이미 받은 종목만 빼고 남은 종목을 받는다)
    — 첫 런 error 뒤 같은 날 재실행이 ok 면 마지막 런 기준으로 회복이다(LAST_RUN_SOURCES)."""
    home = _full(tmp_path)
    _runs(home, rows=(("ledger_chain", "ok"), ("evening_chain", "ok"),
                      ("kiwoom_postclose", "error"), ("kiwoom_postclose", "ok")))
    r = _build(home)
    assert r.status is not dr.ReportStatus.CRIT
    assert "수집 실패 런" not in r.text and "회복 kiwoom_postclose" in r.text


def test_장_마감_수집_error_는_crit(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _runs(home, rows=(("ledger_chain", "ok"), ("evening_chain", "ok"),
                      ("kiwoom_postclose", "error")))
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "수집 실패 런 kiwoom_postclose" in r.text


@pytest.mark.parametrize("source", [*runlog.POSTCLOSE_STEPS, *runlog.POSTCLOSE_FOLLOWUPS])
def test_장_마감_체인_단계는_같은_날_재실행이_ok_면_회복(tmp_path: Path, source: str) -> None:
    """장 마감 체인(PR-8)의 단계 source 는 다시 돌면 그 T 의 그 단계를 통째로 다시 한다(stage 단독 빌드·fi·모델·엑셀·
    v3 반영·재반영·아침 재반영·대조) — 앞 런 failed 뒤 같은 날 손 재실행이 ok 면 마지막 런 기준 회복이다."""
    home = _full(tmp_path)
    _runs(home, rows=(("ledger_chain", "ok"), ("evening_chain", "ok"),
                      (source, "failed"), (source, "ok")))
    r = _build(home)
    assert r.status is dr.ReportStatus.INFO, r.text
    assert f"회복 {source}" in r.text and "수집 실패 런" not in r.text


def test_장_마감_체인_단계_실패는_crit(tmp_path: Path) -> None:
    home = _full(tmp_path)
    _runs(home, rows=(("ledger_chain", "ok"), ("evening_chain", "ok"), ("postclose_fi", "failed")))
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "수집 실패 런 postclose_fi" in r.text


def test_두_판_대조_불일치는_crit_이_아니라_warn(tmp_path: Path) -> None:
    """대조 rc 1(mismatch)은 기록이다 — 판정은 연속 창 집계 몫이라 즉시 등급으로 올리지 않는다."""
    home = _full(tmp_path)
    _runs(home, rows=(("ledger_chain", "ok"), ("evening_chain", "ok"),
                      ("postclose_compare", "mismatch")))
    r = _build(home)
    assert r.status is dr.ReportStatus.WARN, r.text
    assert "postclose_compare mismatch" in r.text and "수집 실패 런" not in r.text


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


def test_저녁_WISE_부분_실패는_warn_줄로_싣고_crit_은_아니다(tmp_path: Path) -> None:
    """N-27 ③ · N-30 ③ — 수집기 rc 0 인 채 일부 콜이 실패한 저녁. 실패 콜 수·종류를 경고에 싣되
    이 줄만으로 crit 로 올리지 않는다. 이 픽스처의 원장 건전성엔 `wise.run` 이 없다 — 회복 여부를
    모르면 경고로 남긴다(P1)."""
    home = _full(tmp_path)
    kinds = {"invalid_stock": 1, "전송 실패(http·exc·notjson)": 1}
    _ledger_evening(home, extra={"wise_n_bad": 2, "wise_bad_summary": kinds})
    r = _build(home)
    assert r.status is dr.ReportStatus.WARN, r.text
    assert "WISE 저녁 실패 2콜(invalid_stock 1, 전송 실패(http·exc·notjson) 1)" in r.text
    assert "■ 경고: " in r.text


@pytest.mark.parametrize("extra", [None, {"wise_n_bad": 0, "wise_bad_summary": {}}],
                         ids=["old_json_no_keys", "zero"])
def test_저녁_WISE_실패가_없으면_아무것도_안_붙는다(
        tmp_path: Path, extra: dict[str, object] | None) -> None:
    """회귀 가드 — 키가 없는 옛 인계 파일·실패 0 은 종전 리포트 그대로(info)."""
    home = _full(tmp_path)
    _ledger_evening(home, extra=extra)
    r = _build(home)
    assert r.status is dr.ReportStatus.INFO, r.text
    assert "WISE 저녁 실패" not in r.text


def test_저녁_WISE_실패_수를_모르면_warn(tmp_path: Path) -> None:
    """I-1 — 키가 있는데 null 이고 wise_rc 0 = 수집기는 끝났는데 런 로그를 못 읽었다. 실패 수를
    모르는 것도 경고다(P1)."""
    home = _full(tmp_path)
    _ledger_evening(home, extra={"wise_n_bad": None, "wise_bad_summary": None})
    r = _build(home)
    assert r.status is dr.ReportStatus.WARN, r.text
    assert "WISE 저녁 실패 수 확인 불가" in r.text


def test_저녁_WISE_rc_실패면_확인_불가_줄은_없다(tmp_path: Path) -> None:
    """wise_rc≠0 이면 이미 crit(저녁 원장 수집 실패) — 실패 수 모름은 따로 싣지 않는다."""
    home = _full(tmp_path)
    _ledger_evening(home, wise_rc=2, extra={"wise_n_bad": None, "wise_bad_summary": None})
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "wise_rc=2" in r.text
    assert "확인 불가" not in r.text


def test_저녁_WISE_실패를_같은_날_재실행이_회복했으면_등급을_안_올린다(tmp_path: Path) -> None:
    """같은 리포트의 원장 `wise.run` 이 PASS = 같은 날 재실행이 실패 콜을 다시 받았다(DART
    `_recovery` 와 같은 방식). 저녁 절에 '회복'으로만 적고 경고로 올리지 않는다."""
    home = _full(tmp_path)
    _ledger(home, checks=[_check("krx.rows", "required", "pass"),
                          _check("wise.run", "required", "pass")])
    _ledger_evening(home, extra={"wise_n_bad": 2, "wise_bad_summary": {"invalid_stock": 2}})
    r = _build(home)
    assert r.status is dr.ReportStatus.INFO, r.text
    assert "WISE 저녁 실패 2콜(invalid_stock 2) — 같은 날 재실행으로 회복(wise.run pass)" in r.text
    assert "■ 경고" not in r.text


def test_저녁_WISE_실패가_회복되지_않았으면_경고로_남긴다(tmp_path: Path) -> None:
    """`wise.run` FAIL — 원장 쪽에서 이미 crit 이고, 저녁 실패 줄은 지금처럼 경고에 남는다."""
    home = _full(tmp_path)
    _ledger(home, checks=[_check("krx.rows", "required", "pass"),
                          _check("wise.run", "required", "fail")])
    _ledger_evening(home, extra={"wise_n_bad": 2, "wise_bad_summary": {"invalid_stock": 2}})
    r = _build(home)
    assert r.status is dr.ReportStatus.CRIT
    assert "■ 경고: WISE 저녁 실패 2콜(invalid_stock 2)" in r.text
    assert "회복(wise.run" not in r.text


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
