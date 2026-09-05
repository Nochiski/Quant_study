"""손 픽스처 equity_root 생성기 — `MANIFEST.json` + `v=<build>/[year=YYYY/]part0.parquet`.

equity 층 DESIGN §2 의 판본 골격(현재 빌드 포인터 `current_build` → `builds[].partitions[].path`)을
테스트 안에서 pyarrow 로 만든다. 산출물 파일에 의존하지 않는다(`.claude/rules/testing.md`).
컬럼 타입은 실물(`rules_s0*.py` 선언)과 같게 둔다 — 가격은 DECIMAL, 계수는 DOUBLE.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

PriceRow = tuple[str, date, float | None, float | None, float | None, float | None, int | None]
"""ticker, date, open, high, low, close, volume_shr — None 은 NULL."""
SpanRow = tuple[str, int, date, date, str]
"""ticker, span_seq, first_date, last_date, end_reason."""
FactorRow = tuple[str, date, str, str, float, bool]
"""ticker, effective_date, event_id, event_type, share_factor, factor_ok."""


def write_equity_table(
    root: Path,
    table: str,
    data: pa.Table,
    *,
    build_id: str = "b_test",
    year_column: str | None = None,
) -> Path:
    """`<root>/<table>/v=<build_id>/…` 에 parquet 를 쓰고 MANIFEST 의 current_build 를 가리킨다.

    year_column 을 주면 `year=YYYY/part0.parquet` 하이브 디렉토리로 나눈다(date_axis 테이블).
    """
    table_root = root / table
    vdir = table_root / f"v={build_id}"
    partitions: list[dict[str, object]] = []
    if year_column is None:
        vdir.mkdir(parents=True, exist_ok=True)
        pq.write_table(data, vdir / "part0.parquet")
        partitions.append({"path": f"v={build_id}", "n_rows": data.num_rows})
    else:
        years = [session.year for session in data[year_column].to_pylist()]
        for year in sorted(set(years)):
            part = data.filter(pa.array([y == year for y in years], type=pa.bool_()))
            pdir = vdir / f"year={year}"
            pdir.mkdir(parents=True, exist_ok=True)
            pq.write_table(part, pdir / "part0.parquet")
            partitions.append({"path": f"v={build_id}/year={year}", "n_rows": part.num_rows})
    write_manifest(table_root, build_id, partitions, n_rows=data.num_rows)
    return table_root


def write_manifest(
    table_root: Path,
    current_build: str | None,
    partitions: list[dict[str, object]],
    *,
    n_rows: int = 0,
    builds: list[dict[str, object]] | None = None,
) -> Path:
    """stage `manifest.py` 와 같은 모양의 MANIFEST.json. `builds` 를 주면 그대로 싣는다."""
    table_root.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "build_id": current_build,
        "snapshot_id": "",
        "rules_version": "e_test",
        "built_at_utc": "2026-09-05T00:00:00+00:00",
        "n_rows": n_rows,
        "content_hash": "",
        "partitions": partitions,
        "gates": [],
        "inputs": {},
    }
    payload = {
        "table": table_root.name,
        "current_build": current_build,
        "keep": 3,
        "builds": [record] if builds is None and current_build is not None else (builds or []),
    }
    path = table_root / "MANIFEST.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _decimal(values: Sequence[float | int | None], scale_type: pa.DataType) -> pa.Array:
    return pa.array(
        [None if v is None else Decimal(str(int(v))) for v in values], type=scale_type
    )


def price_table(rows: list[PriceRow]) -> pa.Table:
    """`price_daily` 관심 컬럼. `price_kind` 는 S04 규칙대로 volume>0 → trade, =0 → reference."""
    volumes = [r[6] for r in rows]
    return pa.table(
        {
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "date": pa.array([r[1] for r in rows], type=pa.date32()),
            "open": _decimal([r[2] for r in rows], pa.decimal128(9, 0)),
            "high": _decimal([r[3] for r in rows], pa.decimal128(9, 0)),
            "low": _decimal([r[4] for r in rows], pa.decimal128(9, 0)),
            "close": _decimal([r[5] for r in rows], pa.decimal128(9, 0)),
            "volume_shr": _decimal(volumes, pa.decimal128(13, 0)),
            "price_kind": pa.array(
                [None if v is None else ("trade" if v > 0 else "reference") for v in volumes],
                type=pa.string(),
            ),
        }
    )


def span_table(rows: list[SpanRow]) -> pa.Table:
    return pa.table(
        {
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "span_seq": pa.array([r[1] for r in rows], type=pa.int64()),
            "first_date": pa.array([r[2] for r in rows], type=pa.date32()),
            "last_date": pa.array([r[3] for r in rows], type=pa.date32()),
            "n_days": pa.array([(r[3] - r[2]).days + 1 for r in rows], type=pa.int64()),
            "end_reason": pa.array([r[4] for r in rows], type=pa.string()),
        }
    )


def calendar_table(sessions: list[date]) -> pa.Table:
    ordered = sorted(sessions)
    return pa.table(
        {
            "date": pa.array(ordered, type=pa.date32()),
            "prev_td": pa.array([None, *ordered[:-1]], type=pa.date32()),
            "next_td": pa.array([*ordered[1:], None], type=pa.date32()),
        }
    )


def security_table(rows: list[tuple[str, str]]) -> pa.Table:
    return pa.table(
        {
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "sec_type": pa.array([r[1] for r in rows], type=pa.string()),
        }
    )


def factor_table(rows: list[FactorRow], apply_dates: list[date | None] | None = None) -> pa.Table:
    """`adj_factor` 관심 컬럼. `apply_dates` 를 주면 `apply_date` 컬럼을 붙인다(S06 후속)."""
    columns: dict[str, pa.Array] = {
        "ticker": pa.array([r[0] for r in rows], type=pa.string()),
        "effective_date": pa.array([r[1] for r in rows], type=pa.date32()),
        "event_id": pa.array([r[2] for r in rows], type=pa.string()),
        "event_type": pa.array([r[3] for r in rows], type=pa.string()),
        "price_factor": pa.array([1.0 / r[4] for r in rows], type=pa.float64()),
        "share_factor": pa.array([r[4] for r in rows], type=pa.float64()),
        "factor_ok": pa.array([r[5] for r in rows], type=pa.bool_()),
    }
    if apply_dates is not None:
        columns["apply_date"] = pa.array(apply_dates, type=pa.date32())
    return pa.table(columns)
