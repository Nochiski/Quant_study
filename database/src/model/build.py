"""모델 판 빌드 — factor_inputs 판 → 레지스트리 spec → 게이트 → 판·MANIFEST 교체 (T2.5).

흐름 (factor_inputs `build.py` 와 같은 모양):
  factor_inputs 판 해석(`latest_<basis>.json` 또는 `--fi-build <id>`, 8표 `_meta.json` 의
  date·basis 가 요청과 같아야 한다) → FactorInputs 한 벌(아래) → spec 마다 엔진 2회(MG2)
  → 게이트 MG0~MG5
  → 통과: spec 마다 `_tmp/<build_id>/<spec_id>/{scores,indicators}.parquet` 를
             `<spec_id>/v=<build_id>/` 로 옮기고 `stage.manifest.commit`(keep=3)
             + 판 manifest `_runs/<D>_<basis>.json` + `latest_<basis>.json`
  → 실패: 아무 것도 쓰지 않고 `_failed/<build_id>.json` + `_runs/<D>_<basis>.json`
            (status gate_failed). MANIFEST·latest 는 건드리지 않는다(마지막 성공 판 유지).
            한 spec 이라도 FAIL 이면 전부 안 올린다.

판 id 하나(`m_<UTC>`)를 선택한 spec 이 공유한다. spec 마다 포인터를 따로 바꾸므로 전환 순간에는
spec 끼리 판이 섞여 보일 수 있다 — 소비자(deliver)는 `latest_<basis>.json` 의 `build_id` 로 읽는다.

FactorInputs 는 `fi_universe.eligible` 종목만 싣는다(나머지 7표도 그 종목으로 자른다). 세 엔진 모두
eligible 밖 종목을 읽지 않으므로 결과는 같고, 서버 09-28 판에서 spec 당 1초 미만이다. MG4 는 자르기
전 전체 `fi_prices` 로 센다.

parquet 은 duckdb 만으로 쓴다(서버 venv 에 pyarrow·pandas 가 없다): 행을 JSON Lines 로 내려 열마다
dtype 을 지정해 읽고 COPY. 부동소수는 repr 왕복이라 비트 단위로 같다.
"""
from __future__ import annotations

import json
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
from factor_inputs.build import _content_hash, _write_json
from stage import manifest
from stage.gates import GateResult
from stage.model import basis_of_build_id, make_build_id

from model import gates, registry
from model.contracts import FI_TABLES, FactorInputs, ModelSpec
from model.engines import ENGINES

RULES_VERSION = "mb1.1.0"   # 1.1.0(2026-10-05): v3_zscore 유니버스·MG1 에 min_analysts
LAYER = "model"
BASES = ("evening", "morning")
PRIMARY_DEFAULT = "scope@1.0"        # 레지스트리와 무관한 설정 — 인계(deliver)의 대표 모델
                                     # (2026-10-05 v4_rank@0.1 → scope@1.0, 사용자 결정 10-01)
SCORES_FILE = "scores.parquet"
INDICATORS_FILE = "indicators.parquet"


class ModelBuildError(Exception):
    """입력 판·인자 오류 — 판을 만들지 않고 멈춘다(CLI rc 2)."""


@dataclass(frozen=True)
class BuildResult:
    status: str                                  # 'ok' | 'gate_failed'
    build_id: str
    date: str
    basis: str
    fi_build_id: str
    primary_spec: str
    specs: dict[str, dict[str, Any]]             # spec_id → n_scores·n_ranked·n_excluded·gates
    gates: dict[str, list[GateResult]]
    run_manifest: Path
    failed_report: Path | None
    elapsed_s: float

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def summary(self) -> str:
        parts = [f"{sid} n={s['n_scores']} ranked={s['n_ranked']}" for sid, s in self.specs.items()]
        warns = [f"{sid}:{g.name}" for sid, rs in self.gates.items() for g in rs
                 if gates.status_of(g) == gates.WARN]
        fails = [f"{sid}:{g.name}" for sid, rs in self.gates.items() for g in gates.failed(rs)]
        return (f"model {self.status} date={self.date} basis={self.basis} build={self.build_id} "
                f"fi={self.fi_build_id} primary={self.primary_spec} | " + " | ".join(parts)
                + (f" | WARN={','.join(warns)}" if warns else "")
                + (f" | FAIL={','.join(fails)}" if fails else ""))


# ── parquet 쓰기 ─────────────────────────────────────────────────────────────
def _json_default(v: object) -> str:
    if isinstance(v, date):
        return v.isoformat()
    raise TypeError(f"parquet 로 쓸 수 없는 값 {type(v).__name__}: {v!r}")


def write_parquet(rows: Sequence[Mapping[str, object]], dtypes: Mapping[str, str],
                  path: Path) -> None:
    """행 목록 → `dtypes` 열 순서·타입의 parquet 한 파일. 행이 없어도 스키마는 싣는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ", ".join(f'"{c}"' for c in dtypes)
    src = path.with_name(path.name + ".jsonl")
    con = duckdb.connect()
    try:
        if rows:
            with src.open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps({c: r[c] for c in dtypes}, ensure_ascii=False,
                                        allow_nan=False, default=_json_default) + "\n")
            spec = "{" + ", ".join(f"'{c}': '{t}'" for c, t in dtypes.items()) + "}"
            sel = (f"SELECT {cols} FROM read_json('{src.as_posix()}', "
                   f"format='newline_delimited', columns={spec})")
        else:
            sel = ("SELECT " + ", ".join(f'CAST(NULL AS {t}) AS "{c}"' for c, t in dtypes.items())
                   + " WHERE false")
        con.execute(f"COPY ({sel}) TO '{path.as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
        src.unlink(missing_ok=True)


# ── 입력 ─────────────────────────────────────────────────────────────────────
def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as e:
        raise ModelBuildError(f"--date 는 YYYYMMDD 여야 한다: {value!r}") from e


def _select_specs(specs: str | Sequence[str]) -> tuple[ModelSpec, ...]:
    """'all' = 레지스트리 전부. 아니면 spec_id 목록(중복 제거, spec_id 순)."""
    if specs == "all":
        return registry.all_specs()
    ids = sorted(set([specs] if isinstance(specs, str) else specs))
    if not ids:
        raise ModelBuildError("--specs 가 비었다")
    out = []
    for sid in ids:
        try:
            out.append(registry.get(sid))
        except KeyError as e:
            raise ModelBuildError(f"--specs {sid}: {e}") from e
    return tuple(out)


def _resolve_fi(fi_root: Path, fi_build: str, d_iso: str, basis: str) -> str:
    """factor_inputs 판 id. 8표의 `_meta.json` date·basis 가 요청과 다르면 거절."""
    if fi_build == "latest":
        pointer = fi_root / f"latest_{basis}.json"
        if not pointer.exists():
            raise ModelBuildError(f"factor_inputs 포인터가 없다: {pointer}")
        latest = json.loads(pointer.read_text(encoding="utf-8"))
        bid = str(latest["build_id"])
        if latest.get("date") != d_iso or latest.get("basis") != basis:
            raise ModelBuildError(
                f"factor_inputs {pointer.name} 의 판이 요청과 다르다: build_id={bid} "
                f"date={latest.get('date')} basis={latest.get('basis')} "
                f"— 요청 date={d_iso} basis={basis}")
    else:
        bid = fi_build
    for name in FI_TABLES:
        vdir = fi_root / name / f"v={bid}"
        meta_path = vdir / "_meta.json"
        if not meta_path.exists() or not any(vdir.glob("*.parquet")):
            raise ModelBuildError(f"factor_inputs 판이 없다: {vdir}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for key, want in (("date", d_iso), ("basis", basis)):
            if meta.get(key) != want:
                raise ModelBuildError(f"factor_inputs {name}/v={bid} 의 {key}={meta.get(key)} "
                                      f"— 요청 {key}={want}")
    return bid


def load_inputs(fi_root: Path, fi_build_id: str, d_iso: str,
                basis: str) -> tuple[FactorInputs, int]:
    """(eligible 종목만 실은 FactorInputs, 전체 fi_prices 에서 D 종가가 있는 종목 수)."""
    con = duckdb.connect()
    try:
        for name in FI_TABLES:
            glob = (fi_root / name / f"v={fi_build_id}" / "*.parquet").resolve().as_posix()
            con.execute(f'CREATE TEMP VIEW "{name}" AS SELECT * FROM '
                        f"read_parquet('{glob}', hive_partitioning=false)")
        con.execute("CREATE TEMP TABLE _elig AS SELECT DISTINCT ticker FROM fi_universe "
                    "WHERE eligible")
        tables: dict[str, list[dict[str, object]]] = {}
        for name in FI_TABLES:
            where = ("WHERE eligible" if name == "fi_universe"
                     else "WHERE ticker IN (SELECT ticker FROM _elig)")
            rel = con.execute(f'SELECT * FROM "{name}" {where}')
            cols = [d[0] for d in rel.description]
            tables[name] = [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
        row = con.execute(f"SELECT count(DISTINCT ticker) FROM fi_prices WHERE date = "
                          f"DATE '{d_iso}' AND close IS NOT NULL").fetchone()
    finally:
        con.close()
    n_on_d = 0 if row is None else int(row[0])
    return FactorInputs(d_iso, basis, fi_build_id, tables), n_on_d


def _previous(root: Path, spec: ModelSpec) -> gates.Previous | None:
    """같은 spec 의 직전 성공 판(MANIFEST current_build). 파일이 지워졌으면 None."""
    m = manifest.load(root / spec.spec_id / "MANIFEST.json")
    if m.current_build is None:
        return None
    path = root / spec.spec_id / f"v={m.current_build}" / SCORES_FILE
    if not path.exists():
        return None
    tcol, comp = gates.ticker_col(spec), gates.composite_col(spec)
    con = duckdb.connect()
    try:
        got = con.execute(f'SELECT "{tcol}", "{comp}", "rank", score_date FROM '
                          f"read_parquet('{path.as_posix()}', hive_partitioning=false)").fetchall()
    finally:
        con.close()
    return gates.Previous(
        build_id=m.current_build,
        score_date=None if not got else str(got[0][3]),
        composite={str(t): float(c) for t, c, _, _ in got if c is not None},
        rank={str(t): int(k) for t, _, k, _ in got if k is not None})


# ── 빌드 ─────────────────────────────────────────────────────────────────────
def build(date_s: str, basis: str, root: Path, fi_root: Path, *, fi_build: str = "latest",
          specs: str | Sequence[str] = "all", primary: str = PRIMARY_DEFAULT,
          min_prices_on_d: int = gates.MIN_PRICES_ON_D, min_ranked: int = gates.MIN_RANKED,
          keep: int = manifest.KEEP_DEFAULT, build_id: str | None = None) -> BuildResult:
    """판 기준일 D(YYYYMMDD)의 모델 판. 게이트 FAIL 은 결과 status 로, 입력·인자 오류는
    `ModelBuildError` 로 낸다."""
    t0 = time.time()
    d = _parse_date(date_s)
    d_iso = d.isoformat()
    if basis not in BASES:
        raise ModelBuildError(f"--basis 는 {BASES} 중 하나: {basis!r}")
    selected = _select_specs(specs)
    ids = [s.spec_id for s in selected]
    if primary not in ids:
        raise ModelBuildError(f"--primary {primary} 가 선택한 spec {ids} 에 없다")
    bid = build_id or make_build_id(basis)
    if basis_of_build_id(bid) not in ("manual", basis):
        raise ModelBuildError(f"build_id 접두어가 --basis 와 다르다: {bid} vs {basis}")
    root, fi_root = Path(root), Path(fi_root)
    fi_bid = _resolve_fi(fi_root, fi_build, d_iso, basis)
    inputs, n_on_d = load_inputs(fi_root, fi_bid, d_iso, basis)

    results = {}
    gate_results: dict[str, list[GateResult]] = {}
    for spec in selected:
        engine = ENGINES[spec.engine]
        first = engine.run(spec, inputs)
        ctx = gates.GateContext(
            spec=spec, date=d_iso, inputs=inputs, result=first, rerun=engine.run(spec, inputs),
            n_prices_on_d=n_on_d, min_prices_on_d=min_prices_on_d, min_ranked=min_ranked,
            previous=_previous(root, spec))
        results[spec.spec_id] = first
        gate_results[spec.spec_id] = gates.run_all(ctx)
    summary: dict[str, dict[str, Any]] = {
        s.spec_id: {**gates.counts(s, results[s.spec_id]),
                    "gates": gates.as_dicts(gate_results[s.spec_id])} for s in selected}
    failed = any(gates.failed(rs) for rs in gate_results.values())
    status = "gate_failed" if failed else "ok"
    elapsed = round(time.time() - t0, 1)
    payload: dict[str, object] = {
        "layer": LAYER, "status": status, "build_id": bid, "date": d_iso, "basis": basis,
        "fi_build_id": fi_bid,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "specs": summary, "primary_spec": primary, "elapsed_s": elapsed}
    run_manifest = root / "_runs" / f"{d.strftime('%Y%m%d')}_{basis}.json"

    def result(report: Path | None) -> BuildResult:
        return BuildResult(status, bid, d_iso, basis, fi_bid, primary, summary, gate_results,
                           run_manifest, report, elapsed)

    if failed:
        report = root / "_failed" / f"{bid}.json"
        _write_json(report, payload)
        _write_json(run_manifest, payload)
        return result(report)

    tmp_root = root / "_tmp" / bid
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    hashes: dict[str, tuple[str, str]] = {}
    try:
        con = duckdb.connect()
        try:
            for spec in selected:
                out = tmp_root / spec.spec_id
                res = results[spec.spec_id]
                write_parquet(res.scores, gates.score_dtypes(spec), out / SCORES_FILE)
                write_parquet(res.indicators, gates.INDICATOR_DTYPES, out / INDICATORS_FILE)
                hashes[spec.spec_id] = (_content_hash(con, out / SCORES_FILE),
                                        _content_hash(con, out / INDICATORS_FILE))
        finally:
            con.close()
    except BaseException:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise

    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    for spec in selected:
        sid = spec.spec_id
        spec_root = root / sid
        spec_root.mkdir(parents=True, exist_ok=True)
        final_dir = spec_root / f"v={bid}"
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.move(str(tmp_root / sid), str(final_dir))
        n = summary[sid]
        h_scores, h_ind = hashes[sid]
        manifest.commit(spec_root, manifest.BuildRecord(
            build_id=bid, snapshot_id="", rules_version=RULES_VERSION, basis=basis,
            built_at_utc=built_at, n_rows=int(n["n_scores"]), content_hash=h_scores,
            partitions=[{"path": f"v={bid}", "n_rows": n["n_scores"], "content_hash": h_scores,
                         "n_indicators": len(results[sid].indicators),
                         "indicators_content_hash": h_ind}],
            gates=[{"name": k, **v} for k, v in n["gates"].items()],
            inputs={"factor_inputs": fi_bid}), keep=keep)
    shutil.rmtree(tmp_root, ignore_errors=True)
    _write_json(run_manifest, payload)
    _write_json(root / f"latest_{basis}.json", payload)
    return result(None)
