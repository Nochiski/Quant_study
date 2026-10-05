"""model 판 · factor_inputs 판 읽기 (고정 계약, M2 W2 T2.5).

  data/model/latest_<basis>.json                       마지막 성공 판
  data/model/_runs/<YYYYMMDD>_<basis>.json             날짜별 판(같은 JSON)
  data/model/<spec_id>/v=<build_id>/scores.parquet     점수 표(contracts.score_columns)
  data/model/<spec_id>/v=<build_id>/indicators.parquet 지표 긴 표(contracts.INDICATOR_COLUMNS)
  data/factor_inputs/<fi_표>/v=<fi_build_id>/*.parquet 입력 8표(hive_partitioning=false)
  data/factor_inputs/_runs/<YYYYMMDD>_<basis>.json     fi 판 manifest(equity 판 id 등)

`src/model/build.py` 를 import 하지 않는다 — 파일 규약만 공유한다.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import duckdb


class DeliverError(RuntimeError):
    """산출물을 만들 수 없는 입력 상태(판 없음 · 실패 판 · 파일 없음 · 잘못된 인자)."""


def iso(v: object) -> str | None:
    """DATE · datetime · 'YYYY-MM-DD…' · 'YYYYMMDD' → 'YYYY-MM-DD'."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def ymd(d: str | date) -> str:
    """'YYYY-MM-DD' 또는 date → 'YYYYMMDD'."""
    return (iso(d) or "").replace("-", "")


@dataclass(frozen=True)
class ModelRun:
    """model 판 하나(하루 · basis 하나)."""

    date: str                 # YYYY-MM-DD
    basis: str
    build_id: str
    fi_build_id: str
    primary_spec: str
    specs: dict[str, dict[str, object]]
    generated_at: str
    path: Path
    meta: dict[str, object] = field(repr=False)


def _run_from_json(path: Path) -> ModelRun:
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DeliverError(f"model 판 manifest 를 읽지 못했다: {path} — {e}") from e
    try:
        return ModelRun(
            date=iso(meta["date"]) or "", basis=str(meta["basis"]),
            build_id=str(meta["build_id"]), fi_build_id=str(meta["fi_build_id"]),
            primary_spec=str(meta["primary_spec"]), specs=dict(meta["specs"]),
            generated_at=str(meta.get("generated_at") or ""), path=path, meta=meta)
    except KeyError as e:
        raise DeliverError(f"model 판 manifest 에 {e} 가 없다: {path}") from e


def find_run(model_root: Path, d: str | date, basis: str) -> ModelRun | None:
    """그날·그 basis 의 성공 판. `_runs/<YYYYMMDD>_<basis>.json` 이 정본, 없으면 날짜가 같은
    `latest_<basis>.json`. 없거나 status ≠ ok 면 None."""
    target = iso(d)
    path = Path(model_root) / "_runs" / f"{ymd(d)}_{basis}.json"
    if not path.exists():
        latest = Path(model_root) / f"latest_{basis}.json"
        if not latest.exists():
            return None
        path = latest
    run = _run_from_json(path)
    if run.date != target or run.basis != basis or run.meta.get("status") != "ok":
        return None
    return run


def load_run(model_root: Path, d: str | date, basis: str) -> ModelRun:
    """find_run 과 같되 없으면 사유를 담아 실패한다."""
    run = find_run(model_root, d, basis)
    if run is not None:
        return run
    path = Path(model_root) / "_runs" / f"{ymd(d)}_{basis}.json"
    if path.exists():
        status = json.loads(path.read_text(encoding="utf-8")).get("status")
        raise DeliverError(
            f"{iso(d)} {basis} model 판이 성공 판이 아니다(status={status!r}): {path}")
    raise DeliverError(f"{iso(d)} {basis} model 판이 없다: {path}")


def previous_run(model_root: Path, d: str | date, basis: str) -> ModelRun | None:
    """d 보다 앞선 가장 최근 성공 판(같은 basis). 전일 순위 비교용."""
    runs_dir = Path(model_root) / "_runs"
    if not runs_dir.is_dir():
        return None
    cut = ymd(d)
    days = sorted((p.name[:8] for p in runs_dir.glob(f"*_{basis}.json")
                   if p.name[:8].isdigit() and p.name[:8] < cut), reverse=True)
    for day in days:
        run = find_run(model_root, day, basis)
        if run is not None:
            return run
    return None


def runs_between(model_root: Path, start: str | date, end: str | date,
                 basis: str) -> list[ModelRun]:
    """start ≤ 날짜 ≤ end 의 성공 판(같은 basis), 날짜 오름차순. 순위 흐름용."""
    runs_dir = Path(model_root) / "_runs"
    if not runs_dir.is_dir():
        return []
    lo, hi = ymd(start), ymd(end)
    days = sorted(p.name[:8] for p in runs_dir.glob(f"*_{basis}.json")
                  if p.name[:8].isdigit() and lo <= p.name[:8] <= hi)
    return [run for run in (find_run(model_root, day, basis) for day in days)
            if run is not None]


def _query(sql: str) -> list[dict[str, object]]:
    con = duckdb.connect()
    try:
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()


def _lit(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _spec_dir(model_root: Path, run: ModelRun, spec_id: str) -> Path:
    return Path(model_root) / spec_id / f"v={run.build_id}"


def read_scores(model_root: Path, run: ModelRun, spec_id: str) -> list[dict[str, object]]:
    """점수 표 전부(행 순서 그대로)."""
    path = _spec_dir(model_root, run, spec_id) / "scores.parquet"
    if not path.exists():
        raise DeliverError(f"{spec_id} 점수 표가 없다: {path}")
    return _query(f"SELECT * FROM read_parquet({_lit(path)}, hive_partitioning=false)")


def read_indicators(model_root: Path, run: ModelRun, spec_id: str) -> list[dict[str, object]]:
    """지표 긴 표. v3·v2 이식판처럼 없으면 빈 목록."""
    path = _spec_dir(model_root, run, spec_id) / "indicators.parquet"
    if not path.exists():
        return []
    return _query(f"SELECT * FROM read_parquet({_lit(path)}, hive_partitioning=false)")


def read_fi(fi_root: Path, table: str, build_id: str, *, columns: Sequence[str] = (),
            select: str = "", where: str = "", tail: str = "") -> list[dict[str, object]]:
    """factor_inputs 표 하나(판 id 고정). `select`·`where`·`tail` 은 이 패키지 호출부가 만든
    고정 SQL 조각만 받는다(사용자 입력을 넣지 않는다)."""
    part = Path(fi_root) / table / f"v={build_id}"
    if not part.is_dir():
        raise DeliverError(f"factor_inputs {table} 판이 없다: {part}")
    cols = select or (", ".join(f'"{c}"' for c in columns) if columns else "*")
    src = f"read_parquet({_lit(part / '*.parquet')}, hive_partitioning=false)"
    sql = f"SELECT {cols} FROM {src}" + (f" WHERE {where}" if where else "")
    rows = _query(sql + (f" {tail}" if tail else ""))
    return display_names(rows) if table == "fi_universe" else rows


def display_names(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """엑셀·캡션 표시용 이름 정리 — 원장 이름은 그대로 두고 여기서만 다듬는다.

    KRX 종목명은 종류를 꼬리에 붙이고("오리온홀딩스보통주"), WICS 라벨은 머리에 "WICS " 를 붙인다
    (09-29 첫 실판 확인). 보통주 꼬리만 뗀다 — 우선주 등 다른 종류 이름은 구분이 필요해 그대로 둔다.
    """
    for r in rows:
        name = r.get("name")
        if isinstance(name, str) and name.endswith("보통주") and len(name) > len("보통주"):
            r["name"] = name[: -len("보통주")]
        for k in ("sector_l1_name", "sector_l2_name"):
            v = r.get(k)
            if isinstance(v, str) and v.startswith("WICS "):
                r[k] = v[len("WICS "):]
    return rows


def fi_run_meta(fi_root: Path, d: str | date, basis: str, build_id: str) -> dict[str, object]:
    """fi 판 manifest(`_runs/<D>_<basis>.json`). 판 id 가 다르거나 없으면 빈 dict."""
    path = Path(fi_root) / "_runs" / f"{ymd(d)}_{basis}.json"
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if meta.get("build_id") == build_id else {}
