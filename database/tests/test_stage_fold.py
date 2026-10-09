"""묶음 7-1 — WISE 재무 연속 판 접기(stg_fin_wise·stg_fin_wise_q, 플랜 2026-10-09-batch7-wise-dedup.md).

원장 `ws_raw` 는 수집일마다 같은 원문을 새 판으로 저장한다(PK 에 fetched_date). 두 표는 같은
(종목, ep, pkey) 의 **바로 앞** 원장 blob 과 sha256 이 같은 blob 을 파싱 전에 버린다 — 첫 판과
A→B→A 로 되돌아온 판은 남는다. 원장 sha256 은 압축 전 원문 해시다(`backfill_wise.py:412`).
"""
import hashlib
import random
import sqlite3
import zlib
from pathlib import Path

import duckdb
import pytest
from stage import build, fold, gates, parsers, rules, snapshot
from test_stage_consensus import SAMSUNG_5001, SAMSUNG_5002
from test_stage_wise import (SAMSUNG_BROKERS, SAMSUNG_CELLS, WS_COLS, WS_RAW_PK_DDL, _fin_blob,
                             _fin_row, _html, _html_broker, _matrix_body, _t2, _z)


def _sha(body: bytes) -> str:
    """원장 규약 — 압축 전 원문의 sha256 hex."""
    return hashlib.sha256(zlib.decompress(body)).hexdigest()


def _r(cmp: str, ep: str, pkey: str, body: bytes, fd: str, sha: str | None = None,
       at: str | None = None) -> tuple:
    """ws_raw 한 행. fetched_at 은 그날 KST 09:05(UTC 00:05)."""
    return (cmp, ep, pkey, fd, body, sha if sha is not None else _sha(body), len(body),
            at or f"{fd}T00:05:00")


def _body(n: int, tag: str = "A") -> bytes:
    """DATA n 행짜리 재무 원문 — tag 가 다르면 원문(=sha256)이 다르다."""
    return _fin_blob([_fin_row(f"2{i:05d}", f"계정{tag}{i}") for i in range(n)])


def write_ws(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(WS_RAW_PK_DDL)
    con.executemany(f"INSERT INTO ws_raw VALUES ({','.join('?' * len(WS_COLS))})", rows)
    con.commit()
    con.close()


def snap_of(tmp_path: Path, rows: list[tuple], sid: str = "s") -> snapshot.Snapshot:
    p = tmp_path / f"raw_{sid}" / "wisereport.db"
    write_ws(p, rows)
    return snapshot.make_snapshot({"wise": p}, tmp_path / "snapshots", snapshot_id=sid)


def build_of(tmp_path: Path, name: str, snap: snapshot.Snapshot, root: str = "stage",
             **kw: object) -> build.BuildResult:
    return build.build_table(rules.RULES[name], snap, tmp_path / root, **kw)


def rows_by_day(r: build.BuildResult, where: str = "TRUE") -> dict[str, int]:
    assert r.out_dir is not None
    con = duckdb.connect()
    got = con.execute(f"SELECT CAST(fetched_date AS VARCHAR), count(*) FROM read_parquet("
                      f"'{r.out_dir}/year=*/*.parquet', hive_partitioning=true) WHERE {where} "
                      "GROUP BY 1 ORDER BY 1").fetchall()
    con.close()
    return {str(d): int(n) for d, n in got}


def g8(r: build.BuildResult) -> gates.GateResult:
    return next(g for g in r.gates if g.name == "G8")


# ── G1 (옛 코드 FAIL) ─────────────────────────────────────────────────────────────────────────
def test_g1_같은_원문_이틀이면_둘째_날_행은_0이고_접은_수는_1(tmp_path: Path) -> None:
    """옛 코드는 같은 원문을 날짜마다 새 판으로 풀어 행이 2배가 된다."""
    body = _body(2)
    snap = snap_of(tmp_path, [_r("005930", "cF3002", "Y", body, "2026-10-01"),
                              _r("005930", "cF3002", "Y", body, "2026-10-02")])
    r = build_of(tmp_path, "stg_fin_wise", snap)
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    assert rows_by_day(r) == {"2026-10-01": 2}
    assert g8(r).metrics["n_folded"] == {"cF3002:Y": 1}


ABAB = (("stg_fin_wise", "cF4002", "Y"), ("stg_fin_wise_q", "cF3002", "Q:IS"))
ABAB_WANT = {"2026-10-01": 3, "2026-10-06": 2, "2026-10-07": 3}


def _abab_days(tmp_path: Path, name: str, ep: str, pkey: str) -> dict[str, int]:
    a, b = _body(3, "A"), _body(2, "B")
    days = ("2026-10-01", "2026-10-02", "2026-10-06", "2026-10-07")
    snap = snap_of(tmp_path, [_r("005930", ep, pkey, x, d) for x, d in zip((a, a, b, a), days, strict=True)],
                   sid=f"s_{name}")
    r = build_of(tmp_path, name, snap)
    assert r.ok, (name, [g for g in r.gates if g.status is gates.GateStatus.FAIL])
    return rows_by_day(r)


@pytest.mark.parametrize("name, ep, pkey", ABAB)
def test_g1_A_A_B_A_는_첫_판_B_되돌아온_A_세_판(tmp_path: Path, name: str, ep: str, pkey: str
                                             ) -> None:
    """A(d1)·A(d2)·B(d3)·A(d4) → d1·d3·d4. 각 판 행 수 = 원문 DATA 수. 두 표 모두."""
    assert _abab_days(tmp_path, name, ep, pkey) == ABAB_WANT


# ── 접기 함수 회귀 가드 ───────────────────────────────────────────────────────────────────────
def _b(cmp: str, ep: str, pkey: str, fd: str, tag: str) -> parsers.RawBlob:
    body = _body(1, tag)
    return parsers.RawBlob(cmp, ep, pkey, fd, body, f"{fd}T00:05:00", _sha(body))


def _kept(fn: object, blobs: list[parsers.RawBlob]) -> list[tuple[str, str, str, str]]:
    f = fn(blobs)  # type: ignore[operator]
    return [(b.cmp_cd, b.ep, b.pkey, b.fetched_date) for b in f.kept]


INDEPENDENT = [_b("005930", "cF3002", "Y:BS", "2026-10-01", "A"),   # 같은 원문 · pkey·ep 만 다름
               _b("005930", "cF3002", "Y:CF", "2026-10-01", "A"),
               _b("005930", "cF4002", "Y", "2026-10-01", "A"),
               _b("005930", "cF3002", "Y:CF", "2026-10-02", "A")]   # ← 이것만 접힌다


def test_첫_판과_중간에_들어온_종목은_남는다() -> None:
    blobs = [_b("005930", "cF3002", "Y", "2026-10-01", "A"), _b("005930", "cF3002", "Y", "2026-10-02", "A"),
             _b("000020", "cF3002", "Y", "2026-10-02", "A")]     # 다른 종목의 같은 원문 · d2 첫 판
    assert _kept(fold.fold_consecutive, blobs) == [("000020", "cF3002", "Y", "2026-10-02"),
                                                   ("005930", "cF3002", "Y", "2026-10-01")]


def test_pkey_와_ep_는_따로_접는다() -> None:
    f = fold.fold_consecutive(INDEPENDENT)
    assert [(b.ep, b.pkey, b.fetched_date) for b in f.kept] == [
        ("cF3002", "Y:BS", "2026-10-01"), ("cF3002", "Y:CF", "2026-10-01"),
        ("cF4002", "Y", "2026-10-01")]
    assert f.n_folded == {"cF3002:Y:BS": 0, "cF3002:Y:CF": 1, "cF4002:Y": 0}


def test_수집_공백을_넘어_바로_앞_blob_과_비교한다() -> None:
    """A(d1)·없음(d2)·A(d3) → d1 만(D7-1). d2 에 다른 종목이 수집됐어도 단위의 바로 앞은 d1 이다."""
    blobs = [_b("005930", "cF3002", "Y", "2026-10-01", "A"), _b("000020", "cF3002", "Y", "2026-10-02", "B"),
             _b("005930", "cF3002", "Y", "2026-10-06", "A")]
    assert _kept(fold.fold_consecutive, blobs) == [("000020", "cF3002", "Y", "2026-10-02"),
                                                   ("005930", "cF3002", "Y", "2026-10-01")]


def test_입력_순서를_섞어도_같은_결과() -> None:
    days = ("2026-10-01", "2026-10-02", "2026-10-06", "2026-10-07", "2026-10-08")
    blobs = [_b(c, ep, pk, d, t) for c in ("005930", "000020") for ep, pk in
             (("cF3002", "Y"), ("cF3002", "Q:IS"), ("cF4002", "Y")) for d, t in zip(days, "AABAC", strict=True)]
    want = fold.fold_consecutive(blobs)
    for seed in range(5):
        shuffled = blobs[:]
        random.Random(seed).shuffle(shuffled)
        assert fold.fold_consecutive(shuffled) == want


def test_keys_stale_는_ep_pkey_최신_수집일보다_먼저_끊긴_단위를_센다() -> None:
    blobs = [_b("005930", "cF3002", "Y", "2026-10-01", "A"), _b("005930", "cF3002", "Y", "2026-10-02", "A"),
             _b("000020", "cF3002", "Y", "2026-10-01", "A"),           # 10-02 에 빠짐 → stale
             _b("000020", "cF4002", "Y", "2026-10-01", "A")]           # cF4002 최신이 10-01 → 아님
    assert fold.keys_stale(fold.fold_consecutive(blobs).last_date) == {"cF3002:Y": 1, "cF4002:Y": 0}


# ── 빌드 회귀 가드 ───────────────────────────────────────────────────────────────────────────
def test_같은_스냅샷_두_번_빌드는_content_hash_가_같다(tmp_path: Path) -> None:
    a, b = _body(3, "A"), _body(2, "B")
    days = ("2026-10-01", "2026-10-02", "2026-10-06", "2026-10-07")
    snap = snap_of(tmp_path, [_r("005930", "cF3002", "Y", x, d)
                              for x, d in zip((a, a, b, a), days, strict=True)])
    one, two = (build_of(tmp_path, "stg_fin_wise", snap, root=root) for root in ("s1", "s2"))
    assert one.ok and two.ok and one.content_hash == two.content_hash
    assert g8(one).metrics == g8(two).metrics


def test_G1_G5_등식은_그대로_성립하고_n_dedup_은_0(tmp_path: Path) -> None:
    a, b = _body(3, "A"), _body(2, "B")
    rows = [_r("005930", "cF3002", "Y", a, "2026-10-01"), _r("005930", "cF3002", "Y", a, "2026-10-02")]
    first = build_of(tmp_path, "stg_fin_wise", snap_of(tmp_path, rows, sid="s1"))
    second = build_of(tmp_path, "stg_fin_wise", snap_of(
        tmp_path, rows + [_r("005930", "cF3002", "Y", a, "2026-10-06"),
                          _r("005930", "cF3002", "Y", b, "2026-10-07")], sid="s2"))
    for r in (first, second):
        status = {g.name: g.status for g in r.gates}
        assert r.ok and r.n_dedup == 0 and status["G1"] is gates.GateStatus.PASS, status
    g5 = next(g for g in second.gates if g.name == "G5")
    assert g5.status is gates.GateStatus.PASS and g5.metrics["d_src"] == 2      # B 판만 늘었다
    assert g8(second).metrics["n_folded"] == {"cF3002:Y": 2}


# 접지 않는 WISE blob 6표 — 같은 원문을 두 날짜로 넣어도 둘 다 남고, 접기 함수·원장 지표를 타지 않는다.
UNFOLDED = {
    "stg_consensus_monthly": [("005930", "cF5001", "202612", SAMSUNG_5001),
                              ("005930", "cF5002", "202612", SAMSUNG_5002)],
    "stg_consensus_annual": [("005930", "c1050001_data", "T2Y", _z({"JsonData": [_t2("2026.12(E)")]}))],
    "stg_consensus_quarterly": [("005930", "c1050001_data", "T2Q",
                                 _z({"JsonData": [_t2("2025.09(A)")]}))],
    "stg_consensus_matrix": [("005930", "c1050001_data", "T4:202612", _matrix_body())],
    "stg_analyst_summary": [("005930", "c1010001", "", _html(SAMSUNG_CELLS))],
    "stg_analyst_broker": [("000250", "c1010001", "", _html_broker(SAMSUNG_BROKERS))],
}


@pytest.mark.parametrize("name", sorted(UNFOLDED))
def test_접지_않는_WISE_6표는_새_코드에서도_같은_원문_두_날짜를_둘_다_싣는다(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    rule = rules.RULES[name]
    assert rule.blob_source is not None and not rule.blob_source.fold_consecutive
    assert not rule.morning_reuse

    def boom(blobs: object) -> fold.Folded:
        raise AssertionError(f"fold called for {name}")

    monkeypatch.setattr(fold, "fold_consecutive", boom)
    rows = [_r(c, ep, pk, body, d, sha=hashlib.sha256(body).hexdigest(), at=f"{d}T00:05:00")
            for c, ep, pk, body in UNFOLDED[name] for d in ("2026-10-01", "2026-10-02")]
    r = build_of(tmp_path, name, snap_of(tmp_path, rows))
    assert r.ok, [g for g in r.gates if g.status is gates.GateStatus.FAIL]
    days = rows_by_day(r)
    assert set(days) == {"2026-10-01", "2026-10-02"} and len(set(days.values())) == 1, days
    assert not {"n_ledger_blobs", "n_folded", "n_sha_mismatch", "n_keys_stale",
                "input_fingerprint"} & set(g8(r).metrics)


def test_두_표만_접기와_아침_재사용을_선언한다() -> None:
    folding = {n for n, r in rules.RULES.items()
               if r.blob_source is not None and r.blob_source.fold_consecutive}
    reusing = {n for n, r in rules.RULES.items() if r.morning_reuse}
    assert folding == reusing == {"stg_fin_wise", "stg_fin_wise_q"}
    for n in folding:
        bs = rules.RULES[n].blob_source
        assert bs is not None and "sha256" in bs.required_columns      # G0 이 원장 열 실재를 본다


# ── 부정 테스트 (이 변형이면 위 가드가 FAIL 해야 한다) ─────────────────────────────────────────
def _fold_first_seen(blobs: object) -> fold.Folded:
    """변형 — 비교 상대를 '단위에서 이미 본 판 전부'로(DART 선례). A→B→A 의 셋째 A 를 버린다."""
    kept, folded, last, seen = [], {}, {}, set()
    for b in sorted(blobs, key=lambda x: (x.cmp_cd, x.ep, x.pkey, x.fetched_date)):  # type: ignore[attr-defined]
        unit, label = (b.cmp_cd, b.ep, b.pkey), f"{b.ep}:{b.pkey}"
        folded.setdefault(label, 0)
        if (unit, b.sha256) in seen:
            folded[label] += 1
        else:
            kept.append(b)
        seen.add((unit, b.sha256))
        last[unit] = b.fetched_date
    return fold.Folded(kept, folded, last)


def _fold_without_pkey(blobs: object) -> fold.Folded:
    """변형 — 단위에서 pkey 를 뺀다((cmp_cd, ep)). 다른 pkey 의 같은 원문 첫 판을 버린다."""
    kept, folded, last = [], {}, {}
    prev: tuple[object, str] | None = None
    for b in sorted(blobs, key=lambda x: (x.cmp_cd, x.ep, x.pkey, x.fetched_date)):  # type: ignore[attr-defined]
        label = f"{b.ep}:{b.pkey}"
        folded.setdefault(label, 0)
        if prev == ((b.cmp_cd, b.ep), b.sha256):
            folded[label] += 1
        else:
            kept.append(b)
        prev = ((b.cmp_cd, b.ep), b.sha256)
        last[(b.cmp_cd, b.ep, b.pkey)] = b.fetched_date
    return fold.Folded(kept, folded, last)


@pytest.mark.parametrize("name, ep, pkey", ABAB)
def test_부정_처음_본_판과_비교하면_A_B_A_셋째_판이_사라진다(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, ep: str, pkey: str) -> None:
    monkeypatch.setattr(fold, "fold_consecutive", _fold_first_seen)
    got = _abab_days(tmp_path, name, ep, pkey)
    assert got != ABAB_WANT and "2026-10-07" not in got


def test_부정_단위에서_pkey_를_빼면_다른_pkey_첫_판이_사라진다() -> None:
    assert _kept(fold.fold_consecutive, INDEPENDENT) != _kept(_fold_without_pkey, INDEPENDENT)
    assert ("005930", "cF3002", "Y:CF", "2026-10-01") not in _kept(_fold_without_pkey, INDEPENDENT)


def _ctx(pm: dict[str, object], n_src: int) -> gates.GateContext:
    return gates.GateContext(con=None, rule=rules.RULES["stg_fin_wise"], src_view="", stage_view="",  # type: ignore[arg-type]
                             reject_view="", n_src=n_src, n_dedup=0, n_reject=0, n_stage=n_src,
                             thresholds={}, fixtures=None, baseline=None, previous_g1=None,
                             cross_alias=None, current_year=2026, parse_metrics=pm)


def test_부정_n_ledger_blobs_가_1_줄면_G8_FAIL(tmp_path: Path) -> None:
    a = _body(2)
    r = build_of(tmp_path, "stg_fin_wise", snap_of(tmp_path, [
        _r("005930", "cF3002", "Y", a, "2026-10-01"), _r("005930", "cF3002", "Y", a, "2026-10-02"),
        _r("005930", "cF3002", "Q:IS", a, "2026-10-01")]))
    pm = dict(g8(r).metrics)
    assert r.ok and pm["n_ledger_blobs"] == 3
    assert gates.g8_parse_equation(_ctx(pm, r.n_src)).status is gates.GateStatus.PASS
    pm["n_ledger_blobs"] = 2
    bad = gates.g8_parse_equation(_ctx(pm, r.n_src))
    assert bad.status is gates.GateStatus.FAIL and "n_ledger_blobs 2" in bad.detail


def test_부정_원장_sha256_이_압축본_해시면_n_sha_mismatch_로_G8_FAIL(tmp_path: Path) -> None:
    a, b = _body(2, "A"), _body(2, "B")
    rows = [_r("005930", "cF3002", "Y", x, d, sha=hashlib.sha256(x).hexdigest())
            for x, d in ((a, "2026-10-01"), (a, "2026-10-02"), (b, "2026-10-06"))]
    r = build_of(tmp_path, "stg_fin_wise", snap_of(tmp_path, rows))
    m = g8(r).metrics
    assert not r.ok and g8(r).status is gates.GateStatus.FAIL
    assert m["n_sha_mismatch"] == 2 and "sha_mismatch=2" in g8(r).detail     # 남긴 2판 모두
    assert m["n_folded"] == {"cF3002:Y": 1}         # 접기 자체는 열 값이 일관되면 돈다


def test_부정_깨진_본문은_parse_failed_로_FAIL_하고_sha_불일치로_세지_않는다(tmp_path: Path) -> None:
    broken = b"\x78\x9cbroken"
    rows = [_r("005930", "cF3002", "Y", _body(2), "2026-10-01"),
            _r("005930", "cF3002", "Y", broken, "2026-10-02", sha=hashlib.sha256(broken).hexdigest())]
    r = build_of(tmp_path, "stg_fin_wise", snap_of(tmp_path, rows))
    m = g8(r).metrics
    assert not r.ok and m["n_parse_failed"] == 1 and m["n_sha_mismatch"] == 0
    assert "parse_failed=1" in g8(r).detail and "sha_mismatch" not in g8(r).detail
