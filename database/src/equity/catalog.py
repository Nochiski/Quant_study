"""equity.duckdb — 데이터 없이 테이블 매크로만 (DESIGN §2 [결정 1] · §10 P1a~d).

P1c 실측: 매크로 본문의 경로는 **절대경로**여야 한다(상대경로는 `No files found`).
P1b: 파일 DB 를 `read_only=True` 로 재오픈해 매크로를 호출할 수 있다.
쓰기는 임시 파일에 만든 뒤 `os.replace` — 이미 파일을 연 read_only 리더는 옛 inode 를 계속 본다.
빌드·GC 뒤에는 반드시 다시 만든다(굽힌 `v=` 가 keep=3 GC 로 rmtree 되면 매크로가 깨진다).

`publish()` 가 카탈로그 단계의 전역 게이트(GATES §7-1 마지막 줄 · §9 §8-6)를 돈다 —
  EG11  뷰 결과 결정성: 같은 임시 카탈로그를 별도 read_only 연결 두 개로 열어 고정 표본의 해시가
        같다
  EG5c  as-of 불변: 직전 `_asof/<view>/<snapshot_id>/` 표본과 이번 표본의 행 차이 0 (첫 실행 skip)
  EG3-P05 `v_firm_mktcap` 을 isin8 축으로 독립 재계산해 대조 (EG3_firm_mktcap)
고정 표본 = baseline `trading_calendar.asof_sample_dates × asof_sample_tickers`(S02 등재).
게이트가 하나라도 FAIL 이면 `equity.duckdb` 를 **교체하지 않고** 테이블은 그대로 둔다
(`_failed/catalog_<snapshot>.json`). 통과하면 교체 → `_asof/` 표본 기록(keep=ASOF_KEEP) →
`_catalog_meta.json`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from stage import manifest
from stage.gates import GateResult, GateStatus

from . import views
from .baseline import Baseline
from .model import record_basis

CATALOG_NAME = "equity.duckdb"
META_NAME = "_catalog_meta.json"
ASOF_DIR = "_asof"
ASOF_KEEP = 3
ASOF_VIEWS: tuple[str, ...] = (                                 # 표본을 남기는 뷰
    "v_cum_adj", "v_adj_price",                                  # S06 (base = as_of)
    "v_adj_price_fwd", "v_adj_volume_fwd")                       # S21 후속 (전방 조정)
ASOF_PART = "part0.parquet"
ASOF_META = "_meta.json"
SAMPLE_DATES = ("trading_calendar", "asof_sample_dates")
SAMPLE_TICKERS = ("trading_calendar", "asof_sample_tickers")
DIFF_SAMPLE_ROWS = 10
# 매크로 이름 = 식별자 + 선택적 인자 목록. `v_universe(d, policy := 'all')` 같은 형태를 받는다.
_MACRO_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*(\([^()]*\))?$")


def table_builds(equity_root: Path) -> dict[str, str]:
    """커밋된 equity 테이블 → current_build. `_pinned`·`_tmp`·`_failed`·`_asof` 는 테이블이
    아니다."""
    out: dict[str, str] = {}
    if not equity_root.exists():
        return out
    for d in sorted(equity_root.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
            continue
        m = manifest.load(d / "MANIFEST.json")
        if m.current_build is not None:
            out[d.name] = m.current_build
    return out


def table_bases(equity_root: Path) -> dict[str, str]:
    """커밋된 equity 테이블 → 그 판(`model.BUILD_BASES`). 저녁 잠정판이 섞인 카탈로그를
    소비자가 알아볼 수 있어야 한다(플랜 v2 §4 B.2 `--basis`)."""
    out: dict[str, str] = {}
    if not equity_root.exists():
        return out
    for d in sorted(equity_root.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
            continue
        m = manifest.load(d / "MANIFEST.json")
        rec = next((b for b in m.builds if b.build_id == m.current_build), None)
        if rec is not None:
            out[d.name] = record_basis(rec)
    return out


def snapshot_id(builds: dict[str, str]) -> str:
    """전 테이블 current_build 를 정렬해 뜬 해시 — 카탈로그가 어느 판을 가리키는지의 지문."""
    payload = "\n".join(f"{t}={b}" for t, b in sorted(builds.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _creation_order(macros: dict[str, str]) -> list[str]:
    """의존 매크로(`views.MACRO_DEPENDS`)를 먼저 — duckdb 는 매크로 본문을 생성 시점에
    바인딩한다."""
    base = {sig: sig.split("(", 1)[0] for sig in macros}
    done: list[str] = []
    pending = sorted(macros)
    while pending:
        ready = [s for s in pending
                 if all(d in {base[x] for x in done} or d not in base.values()
                        for d in views.MACRO_DEPENDS.get(base[s], ()))]
        if not ready:
            raise ValueError(f"macro dependency cycle or unresolved: pending={pending}")
        done += ready
        pending = [s for s in pending if s not in ready]
    return done


def build_catalog_file(path: Path, macros: dict[str, str]) -> None:
    """매크로만 담은 duckdb 파일을 `path` 에 만든다(있으면 지우고)."""
    bad = sorted(n for n in macros if not _MACRO_NAME_RE.match(n))
    if bad:
        raise ValueError(f"invalid macro name(s): got={bad} "
                         f"expected=<identifier>[(<args>)] path={path}")
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    try:
        for name in _creation_order(macros):
            con.execute(f"CREATE OR REPLACE MACRO {name} AS TABLE {macros[name]}")
    finally:
        con.close()


def _now() -> str:
    """마이크로초까지 — `_asof/` 스냅샷 정렬 키라 같은 초 안의 두 publish 도 순서가 보존돼야
    한다."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _commit(equity_root: Path, tmp: Path, macros: dict[str, str], gates: list[GateResult],
            asof: dict[str, dict[str, object]], skipped: dict[str, str]) -> Path:
    """임시 파일을 `equity.duckdb` 로 원자 교체하고 `_catalog_meta.json` 을 쓴다."""
    dst = equity_root / CATALOG_NAME
    os.replace(tmp, dst)        # 파일 원자 교체 — 유일한 전환 지점(MANIFEST 규약과 같다)
    builds = table_builds(equity_root)
    bases = table_bases(equity_root)
    distinct = sorted(set(bases.values()))
    _write_json(equity_root / META_NAME, {
        "snapshot_id": snapshot_id(builds), "builds": builds, "macros": sorted(macros),
        "macros_skipped": dict(skipped), "gates": [g.as_dict() for g in gates],
        # 판이 표마다 갈릴 수 있다(저녁에 가격 계열만 다시 짓는 증분 경로) — 전량 목록과
        # 「한 판인가」 요약을 함께 싣는다.
        "table_basis": bases, "basis": distinct[0] if len(distinct) == 1 else "mixed",
        "asof": asof, "written_at_utc": _now()})
    return dst


def write_catalog(equity_root: Path, macros: dict[str, str]) -> Path:
    """매크로만 담은 `equity.duckdb` 를 임시 파일에 만들고 `os.replace` 로 교체한다(게이트 없음)."""
    equity_root.mkdir(parents=True, exist_ok=True)
    tmp = equity_root / f".{CATALOG_NAME}.{os.getpid()}.tmp"
    build_catalog_file(tmp, macros)
    return _commit(equity_root, tmp, macros, [], {}, {})


# ── 카탈로그 단계 게이트 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class CatalogResult:
    ok: bool
    path: Path                              # equity.duckdb (실패 시 기존 파일 경로)
    snapshot_id: str
    builds: dict[str, str]
    macros: dict[str, str]
    skipped: dict[str, str]
    gates: list[GateResult]
    asof: dict[str, dict[str, object]] = field(default_factory=dict)
    failed_report: Path | None = None


def _sample_lists(baseline: Baseline) -> tuple[list[str], list[str]] | None:
    dates = baseline.get(*SAMPLE_DATES)
    tickers = baseline.get(*SAMPLE_TICKERS)
    if not isinstance(dates, list) or not isinstance(tickers, list) or not dates or not tickers:
        return None
    return [str(d) for d in dates], [str(t) for t in tickers]


def _lit_list(values: list[str], kind: str) -> str:
    inner = ", ".join(f"{kind} '{v}'" if kind else "'" + v.replace("'", "''") + "'"
                      for v in values)
    return f"[{inner}]"


def sample_sql(view: str, dates: list[str], tickers: list[str]) -> str:
    """고정 표본: 각 as_of 에서 뷰를 호출해 표본 티커 행만 (as_of 컬럼을 앞에 붙인다)."""
    return (f"SELECT s.as_of, v.* FROM (SELECT unnest({_lit_list(dates, 'DATE')}) AS as_of) s, "
            f"LATERAL (SELECT * FROM {view}(s.as_of)) v "
            f"WHERE v.ticker IN (SELECT unnest({_lit_list(tickers, '')}))")


def _hash(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[int, str]:
    row = con.execute(f"SELECT count(*), bit_xor(hash(CAST(t AS VARCHAR))) FROM ({sql}) t"
                      ).fetchone()
    if row is None:
        raise RuntimeError(f"hash query returned no row: {sql[:200]}")
    n, h = row
    return int(str(n)), ("0" if h is None else format(int(str(h)), "x"))


def _read_only(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True)


def eg11_determinism(tmp_catalog: Path, macros: dict[str, str], sample: tuple[list[str], list[str]]
                     | None, work: Path) -> tuple[GateResult, dict[str, dict[str, object]]]:
    """EG11 — 같은 카탈로그를 별도 연결 두 개로 열어 고정 표본 해시가 같다. 표본 parquet 도
    여기서 쓴다."""
    present = [v for v in ASOF_VIEWS if any(k.startswith(v + "(") for k in macros)]
    if not present:
        return GateResult("EG11", GateStatus.SKIP, "not_built",
                          {"views": list(ASOF_VIEWS), "macros": sorted(macros)}), {}
    if sample is None:
        return GateResult("EG11", GateStatus.SKIP, "no_baseline",
                          {"missing_metric": [".".join(SAMPLE_DATES), ".".join(SAMPLE_TICKERS)],
                           "views": present}), {}
    dates, tickers = sample
    hashes: dict[str, dict[str, object]] = {}
    written: dict[str, dict[str, object]] = {}
    bad: list[str] = []
    for view in present:
        sql = sample_sql(view, dates, tickers)
        a = _read_only(tmp_catalog)
        try:
            n1, h1 = _hash(a, sql)
            out = work / f"{view}.parquet"
            work.mkdir(parents=True, exist_ok=True)
            a.execute(f"COPY ({sql} ORDER BY as_of, ticker, date) TO '{out}' (FORMAT PARQUET)")
        finally:
            a.close()
        b = _read_only(tmp_catalog)
        try:
            n2, h2 = _hash(b, sql)
        finally:
            b.close()
        hashes[view] = {"n_rows": n1, "hash_1": h1, "hash_2": h2, "n_rows_2": n2}
        written[view] = {"path": str(out), "n_rows": n1, "content_hash": f"{n1}:{h1}"}
        if (n1, h1) != (n2, h2):
            bad.append(view)
    metrics: dict[str, object] = {"views": present, "n_sample_dates": len(dates),
                                  "n_sample_tickers": len(tickers), "hashes": hashes}
    if bad:
        return GateResult("EG11", GateStatus.FAIL,
                          f"view result differs between two read_only connections: {bad}",
                          metrics), written
    return GateResult("EG11", GateStatus.PASS, "고정 표본 해시 동일(연결 2개)", metrics), written


def _asof_snapshots(equity_root: Path, view: str) -> list[tuple[str, Path, dict[str, object]]]:
    """`_asof/<view>/<snapshot_id>/` 목록을 written_at_utc 내림차순으로."""
    root = equity_root / ASOF_DIR / view
    if not root.exists():
        return []
    out: list[tuple[str, Path, dict[str, object]]] = []
    for d in root.iterdir():
        meta_path = d / ASOF_META
        if d.is_dir() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                out.append((str(meta.get("written_at_utc", "")), d, meta))
    return sorted(out, key=lambda t: (t[0], t[1].name), reverse=True)


def eg5c_asof_invariance(equity_root: Path, written: dict[str, dict[str, object]],
                         sample: tuple[list[str], list[str]] | None,
                         rebase: bool) -> GateResult:
    """EG5c — 직전 `_asof/` 표본과 이번 표본의 행 차이(키 (as_of, ticker, date) 위 행 해시) 0.

    첫 실행은 skip(no_previous_snapshot). 차이가 있으면 FAIL 이고 카탈로그는 교체되지 않는다 —
    사람이 `--rebase-asof` 로 승인하면 pass 로 두고 이번 표본이 새 기준이 된다(GATES §7-3).
    """
    if not written:
        return GateResult("EG5c", GateStatus.SKIP, "not_built" if sample else "no_baseline",
                          {"views": list(ASOF_VIEWS)})
    per_view: dict[str, dict[str, object]] = {}
    n_diff_total = 0
    for view, info in written.items():
        snaps = _asof_snapshots(equity_root, view)
        if not snaps:
            per_view[view] = {"previous_snapshot_id": None}
            continue
        _, prev_dir, prev_meta = snaps[0]
        con = duckdb.connect()
        try:
            con.execute(f"CREATE TEMP VIEW cur AS SELECT * FROM read_parquet('{info['path']}')")
            con.execute(f"CREATE TEMP VIEW prv AS SELECT * FROM "
                        f"read_parquet('{prev_dir / ASOF_PART}')")
            con.execute("""
                CREATE TEMP TABLE _d AS
                WITH c AS (SELECT as_of, ticker, date, hash(CAST(t AS VARCHAR)) AS h FROM cur t),
                     p AS (SELECT as_of, ticker, date, hash(CAST(t AS VARCHAR)) AS h FROM prv t)
                SELECT coalesce(c.as_of, p.as_of) AS as_of, coalesce(c.ticker, p.ticker) AS ticker,
                       coalesce(c.date, p.date) AS date,
                       CASE WHEN p.h IS NULL THEN 'only_current'
                            WHEN c.h IS NULL THEN 'only_previous'
                            WHEN c.h <> p.h THEN 'changed' END AS kind
                FROM c FULL OUTER JOIN p ON p.as_of = c.as_of AND p.ticker = c.ticker
                                        AND p.date = c.date""")
            kinds = {str(r[0]): int(str(r[1])) for r in con.execute(
                "SELECT kind, count(*) FROM _d WHERE kind IS NOT NULL GROUP BY 1 ORDER BY 1"
                ).fetchall()}
            keys = [f"{r[0]}|{r[1]}|{r[2]}|{r[3]}" for r in con.execute(
                "SELECT as_of, ticker, date, kind FROM _d WHERE kind IS NOT NULL "
                f"ORDER BY 1, 2, 3 LIMIT {DIFF_SAMPLE_ROWS}").fetchall()]
        finally:
            con.close()
        n_diff = sum(kinds.values())
        n_diff_total += n_diff
        per_view[view] = {"previous_snapshot_id": prev_dir.name,
                          "previous_builds": prev_meta.get("builds"),
                          "previous_written_at_utc": prev_meta.get("written_at_utc"),
                          "n_diff": n_diff, "diff_by_kind": kinds, "diff_keys": keys}
    metrics: dict[str, object] = {"views": per_view, "n_diff_total": n_diff_total,
                                  "rebase_asof": rebase}
    if all(v.get("previous_snapshot_id") is None for v in per_view.values()):
        return GateResult("EG5c", GateStatus.SKIP, "no_previous_snapshot", metrics)
    if n_diff_total == 0:
        return GateResult("EG5c", GateStatus.PASS, "직전 표본과 동일", metrics)
    if rebase:
        return GateResult("EG5c", GateStatus.PASS,
                          f"rebased(approved --rebase-asof): n_diff={n_diff_total}", metrics)
    return GateResult("EG5c", GateStatus.FAIL,
                      f"as-of sample differs from previous snapshot: n_diff={n_diff_total} "
                      f"(승인하려면 catalog --rebase-asof)", metrics)


def eg3_firm_mktcap(tmp_catalog: Path, equity_root: Path, macros: dict[str, str],
                    sample: tuple[list[str], list[str]] | None) -> GateResult:
    """EG3-P05 — `v_firm_mktcap(d)` 를 isin8 축(corp_ticker × price_daily)으로 독립 재계산해 대조.

    뷰는 `common_ticker` 로 묶고 여기서는 `isin8` 로 묶는다 — 같은 식을 재호출하는 항진명제(§5-C7)를
    피한다. 대조 날짜는 baseline `asof_sample_dates`(별도 `fixture_date` 상수를 두지 않는다).
    """
    if not any(k.startswith("v_firm_mktcap(") for k in macros):
        return GateResult("EG3_firm_mktcap", GateStatus.SKIP, "not_built", {})
    if sample is None:
        return GateResult("EG3_firm_mktcap", GateStatus.SKIP, "no_baseline",
                          {"missing_metric": ".".join(SAMPLE_DATES)})
    dates = sample[0]
    con = _read_only(tmp_catalog)
    try:
        con.execute(f"CREATE TEMP VIEW ct AS SELECT * FROM "
                    f"{views.parquet_source(equity_root, 'corp_ticker')}")
        con.execute(f"CREATE TEMP VIEW px AS SELECT ticker, date, mktcap_krw FROM "
                    f"{views.parquet_source(equity_root, 'price_daily')} "
                    f"WHERE date IN (SELECT unnest({_lit_list(dates, 'DATE')}))")
        row = con.execute(f"""
            WITH expect AS (
              SELECT ct.isin8, px.date, count(*) AS n_leg, sum(px.mktcap_krw) AS mktcap,
                     count(*) FILTER (WHERE NOT ct.is_common) AS n_noncommon
              FROM ct JOIN px USING (ticker)
              WHERE ct.isin8 LIKE 'KR7%' AND px.mktcap_krw IS NOT NULL
              GROUP BY 1, 2),
            ek AS (
              SELECT e.*, (SELECT c.ticker FROM ct c WHERE c.isin8 = e.isin8 AND c.is_common)
                            AS firm_ticker
              FROM expect e),
            got AS (
              SELECT g.* FROM (SELECT unnest({_lit_list(dates, 'DATE')}) AS dd) s,
                   LATERAL (SELECT * FROM v_firm_mktcap(s.dd)) g),
            j AS (
              SELECT ek.firm_ticker, ek.date, ek.n_leg, ek.mktcap, ek.n_noncommon,
                     g.n_leg AS g_leg, g.firm_mktcap_krw AS g_mktcap
              FROM ek LEFT JOIN got g ON g.firm_ticker = ek.firm_ticker AND g.date = ek.date)
            SELECT count(*),
                   count(*) FILTER (WHERE g_leg IS NULL),
                   count(*) FILTER (WHERE g_leg IS NOT NULL
                                      AND (g_leg <> n_leg OR g_mktcap IS DISTINCT FROM mktcap)),
                   count(*) FILTER (WHERE n_leg > 1),
                   coalesce(sum(n_noncommon), 0),
                   (SELECT count(*) FROM got)
            FROM j""").fetchone()
    finally:
        con.close()
    if row is None:
        raise RuntimeError("EG3_firm_mktcap query returned no row")
    n_groups, n_missing, n_mismatch, n_multi, n_noncommon, n_view = (int(str(x)) for x in row)
    metrics: dict[str, object] = {
        "dates": dates, "n_kr7_groups": n_groups, "n_missing_in_view": n_missing,
        "n_mismatch": n_mismatch, "n_multi_leg_groups": n_multi,
        "n_noncommon_legs": n_noncommon, "n_view_rows": n_view}
    ok = n_missing == 0 and n_mismatch == 0
    return GateResult("EG3_firm_mktcap", GateStatus.PASS if ok else GateStatus.FAIL,
                      "isin8 독립 재계산 일치" if ok else
                      f"firm mktcap mismatch: missing={n_missing} mismatch={n_mismatch} "
                      f"(groups={n_groups})", metrics)


def _record_asof(equity_root: Path, written: dict[str, dict[str, object]], sid: str,
                 builds: dict[str, str], sample: tuple[list[str], list[str]] | None,
                 keep: int) -> dict[str, dict[str, object]]:
    """임시 표본을 `_asof/<view>/<snapshot_id>/` 로 옮기고 keep 밖 스냅샷을 지운다."""
    out: dict[str, dict[str, object]] = {}
    for view, info in written.items():
        dest = equity_root / ASOF_DIR / view / sid
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        shutil.move(str(info["path"]), str(dest / ASOF_PART))
        meta = {"view": view, "snapshot_id": sid, "builds": builds,
                "asof_sample_dates": sample[0] if sample else [],
                "asof_sample_tickers": sample[1] if sample else [],
                "n_rows": info["n_rows"], "content_hash": info["content_hash"],
                "written_at_utc": _now()}
        _write_json(dest / ASOF_META, meta)
        for _, d, _ in _asof_snapshots(equity_root, view)[keep:]:
            shutil.rmtree(d)
        out[view] = {"snapshot_id": sid, "path": str(dest / ASOF_PART),
                     "n_rows": info["n_rows"], "content_hash": info["content_hash"]}
    return out


def publish(equity_root: Path, baseline: Baseline, *, keep: int = ASOF_KEEP,
            rebase_asof: bool = False) -> CatalogResult:
    """매크로 렌더 → 임시 카탈로그 → EG11·EG5c·EG3-P05 → 통과 시 교체 + `_asof/` 기록."""
    equity_root.mkdir(parents=True, exist_ok=True)
    macros, skipped = views.render_macros(equity_root)
    builds = table_builds(equity_root)
    sid = snapshot_id(builds)
    sample = _sample_lists(baseline)
    tmp = equity_root / f".{CATALOG_NAME}.{os.getpid()}.tmp"
    work = equity_root / "_tmp" / f"catalog_{sid}"
    if work.exists():
        shutil.rmtree(work)
    build_catalog_file(tmp, macros)
    try:
        eg11, written = eg11_determinism(tmp, macros, sample, work)
        results = [eg11, eg5c_asof_invariance(equity_root, written, sample, rebase_asof),
                   eg3_firm_mktcap(tmp, equity_root, macros, sample)]
        if any(g.status is GateStatus.FAIL for g in results):
            tmp.unlink()
            shutil.rmtree(work, ignore_errors=True)
            report = equity_root / "_failed" / f"catalog_{sid}.json"
            _write_json(report, {"catalog": CATALOG_NAME, "snapshot_id": sid, "builds": builds,
                                 "macros": sorted(macros), "macros_skipped": skipped,
                                 "first_failed_gate": next(
                                     g.name for g in results if g.status is GateStatus.FAIL),
                                 "gates": [g.as_dict() for g in results]})
            return CatalogResult(False, equity_root / CATALOG_NAME, sid, builds, macros, skipped,
                                 results, {}, report)
        asof = _record_asof(equity_root, written, sid, builds, sample, keep)
        shutil.rmtree(work, ignore_errors=True)
        path = _commit(equity_root, tmp, macros, results, asof, skipped)
    finally:
        if tmp.exists():
            tmp.unlink()
    return CatalogResult(True, path, sid, builds, macros, skipped, results, asof, None)


__all__ = ["ASOF_DIR", "ASOF_KEEP", "ASOF_VIEWS", "CATALOG_NAME", "META_NAME", "CatalogResult",
           "build_catalog_file", "publish", "sample_sql", "snapshot_id", "table_bases",
           "table_builds", "write_catalog"]
