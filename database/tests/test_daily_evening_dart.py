"""저녁 DART 판(D13 · N-62, `daily.evening_dart`) — 단계 순서 · 멈춤 · 인계 · 정리.

표 하나하나의 빌드(stage·equity·문서 캐시)는 각 층의 테스트가 실물로 본다. 여기서는 단계를 부르는 runner 를 가짜로
바꿔(자식 프로세스 대신 MANIFEST 에 판을 커밋) 폐포 순서 · 스냅숏 하나 · 멈춘 단계 뒤 미실행 · 인계 health 를 본다.
스냅숏은 실물(`stage.snapshot.make_snapshot`)이다.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
from daily import evening_dart
from daily.evening_dart import EQUITY_TABLES, STAGE_TABLES, Roots
from equity import handoff
from stage import manifest

T = "20260929"


def _roots(base: Path) -> Roots:
    raw = base / "raw"
    raw.mkdir(parents=True)
    con = sqlite3.connect(raw / "dart.db")
    con.execute("CREATE TABLE dart_list (rcept_no TEXT)")
    con.execute("INSERT INTO dart_list VALUES ('20260929000001')")
    con.commit()
    con.close()
    (raw / "krx.db").write_bytes(b"")           # 다른 원장은 스냅숏에 들지 않는다
    return Roots(model_db=base / "model_db", raw_dir=raw, research_stage=base / "stage",
                 baseline=base / "equity" / "baseline.json")


def _doc_cache(stage_root: Path, sid: str, status: str, mtime: float) -> None:
    d = stage_root / "_tmp" / "doc" / sid
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    os.utime(d / "summary.json", (mtime, mtime))


def _name(cmd: Sequence[str]) -> str:
    module = cmd[2]
    if module == "stage.doc_prepass":
        return "doc_prepass"
    if module == "stage":
        return f"stage:{cmd[cmd.index('--table') + 1]}"
    return f"equity:{cmd[cmd.index('build') + 1]}"


class FakeRunner:
    """자식 프로세스 대신 — 성공하면 그 표에 저녁 판을 커밋한다(`fail_at` 단계는 rc 1, 판 없음)."""

    def __init__(self, roots: Roots, fail_at: str | None = None) -> None:
        self.roots, self.fail_at = roots, fail_at
        self.calls: list[list[str]] = []

    def __call__(self, cmd: Sequence[str]) -> int:
        self.calls.append(list(cmd))
        name = _name(cmd)
        if name == self.fail_at:
            return 1
        if name == "doc_prepass":
            sid = cmd[cmd.index("--snapshot-id") + 1]
            _doc_cache(self.roots.stage, sid, "ok", 0.0)
            return 0
        layer, table = name.split(":")
        root = self.roots.stage if layer == "stage" else self.roots.equity
        sid = cmd[cmd.index("--snapshot-id") + 1] if layer == "stage" else ""
        n = len(self.calls)
        (root / table).mkdir(parents=True, exist_ok=True)
        manifest.commit(root / table, manifest.BuildRecord(
            build_id=f"e_20260929T0930{n:02d}_000000Z", snapshot_id=sid, rules_version="x",
            built_at_utc="2026-09-29T09:30:00+00:00", n_rows=1, content_hash="1:0"))
        return 0

    def names(self) -> list[str]:
        return [_name(c) for c in self.calls]


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    r = _roots(tmp_path)
    _doc_cache(r.research_stage, "snap_20260927T232500Z", "ok", 1_000.0)
    _doc_cache(r.research_stage, "snap_20260928T232500Z", "ok", 2_000.0)       # 최신 ok — 기준
    _doc_cache(r.research_stage, "snap_20260929T000000Z", "gate_failed", 3_000.0)
    return r


def test_build_runs_the_closure_in_order_on_one_dart_snapshot(roots: Roots) -> None:
    """dart.db 하나만 뜬 스냅숏 한 판으로 문서 캐시(연구 최신 ok 기준) → stage 16표 → equity 6표(연구 baseline) 순서로
    짓고, 인계는 그날 저녁 판 · health ok · 표마다 지금 판 · 모델 DB 루트다(fi `--dart-builds-from` 이 읽는 모양)."""
    run = FakeRunner(roots)
    r = evening_dart.build(T, roots, run=run, python="py")
    assert r.ok, r.failed
    assert run.names() == ["doc_prepass", *(f"stage:{t}" for t in STAGE_TABLES),
                           *(f"equity:{t}" for t in EQUITY_TABLES)]
    snap = roots.snapshots / str(r.snapshot_id)
    assert sorted(p.name for p in snap.glob("*.db")) == ["dart.db"]
    doc = run.calls[0]
    assert (doc[doc.index("--base-snapshot") + 1], doc[doc.index("--base-stage-root") + 1]) == (
        "snap_20260928T232500Z", str(roots.research_stage))
    for cmd in run.calls[1:1 + len(STAGE_TABLES)]:
        assert cmd[cmd.index("--snapshot-id") + 1] == r.snapshot_id
        assert (cmd[cmd.index("--stage-root") + 1], cmd[cmd.index("--basis") + 1]) == (
            str(roots.stage), "evening")
        assert cmd[cmd.index("--baseline") + 1] == str(roots.research_stage / "baseline.json")
    for cmd in run.calls[1 + len(STAGE_TABLES):]:
        assert (cmd[cmd.index("--root") + 1], cmd[cmd.index("--baseline") + 1]) == (
            str(roots.equity), str(roots.baseline))
    h = handoff.load(r.handoff)
    assert r.handoff == evening_dart.handoff_path(roots.model_db, T)
    assert (h.date, h.basis, h.health_ok) == (T, "evening", True)
    assert list(h.stage_builds) == list(STAGE_TABLES)
    assert list(h.equity_builds) == list(EQUITY_TABLES)
    assert all(b.startswith("e_") for b in [*h.stage_builds.values(), *h.equity_builds.values()])
    assert (h.equity_root, h.stage_root) == (str(roots.equity.resolve()), str(roots.stage.resolve()))


@pytest.mark.parametrize(("fail_at", "ran_equity", "health"), [
    ("stage:stg_fin", False, {"stage": "fail", "equity": "fail"}),
    ("equity:fin_std", True, {"stage": "ok", "equity": "fail"}),
])
def test_a_failed_step_stops_the_rest_and_writes_a_failed_handoff(roots: Roots, fail_at: str,
                                                                  ran_equity: bool,
                                                                  health: dict[str, str]) -> None:
    """한 단계가 실패하면 그 뒤 단계는 돌지 않고 인계는 health fail 로 쓴다 — 같은 날 앞서 ok 인계가 있었어도 덮어서
    fi 가 낡은 판을 읽지 않는다(fi 가 health fail 인계를 rc 2 로 거절 → 체인이 전 거래일 공시 판으로 간다)."""
    assert evening_dart.build(T, roots, run=FakeRunner(roots), python="py",
                              snapshot_id="snap_20260929T093000Z").ok
    run = FakeRunner(roots, fail_at=fail_at)
    r = evening_dart.build(T, roots, run=run, python="py", snapshot_id="snap_20260929T094000Z")
    assert not r.ok and r.failed is not None and r.failed.startswith(fail_at)
    assert run.names()[-1] == fail_at
    assert any(n.startswith("equity:") for n in run.names()) is ran_equity
    h = handoff.load(r.handoff)
    assert h.health == health and not h.health_ok


def test_without_an_ok_research_doc_cache_nothing_is_built(tmp_path: Path) -> None:
    """연구 루트에 ok 문서 캐시가 없으면 문서 표를 못 지으므로 stage 부터 돌지 않고 멈춘다. 판 목록이 빈 인계는
    `equity.handoff` 가 읽지 않는다(fi 도 rc 2) — health 는 파일에서 본다."""
    roots = _roots(tmp_path)
    _doc_cache(roots.research_stage, "snap_20260928T232500Z", "gate_failed", 1_000.0)
    run = FakeRunner(roots)
    r = evening_dart.build(T, roots, run=run, python="py")
    assert not r.ok and r.failed is not None and r.failed.startswith("doc_prepass")
    assert run.calls == []
    raw = json.loads(r.handoff.read_text(encoding="utf-8"))
    assert (raw["health"], raw["stage_builds"], raw["equity_builds"]) == (
        {"stage": "fail", "equity": "fail"}, {}, {})
    with pytest.raises(handoff.HandoffError):
        handoff.load(r.handoff)


def test_ok_build_keeps_only_the_current_doc_cache(roots: Roots) -> None:
    """성공하면 모델 DB 문서 캐시는 지금 판이 선 스냅숏 것만 남긴다(다음 저녁은 연구 캐시를 기준으로 새로 세운다)."""
    _doc_cache(roots.stage, "snap_20260928T093000Z", "ok", 0.0)            # 어제 저녁 캐시
    r = evening_dart.build(T, roots, run=FakeRunner(roots), python="py")
    assert r.ok
    assert sorted(p.name for p in (roots.stage / "_tmp" / "doc").iterdir()) == [r.snapshot_id]


def test_cli_rejects_a_bad_date(capsys) -> None:
    assert evening_dart.main(["build", "--date", "2026-09-29"]) == 2
    assert "--date" in capsys.readouterr().err
