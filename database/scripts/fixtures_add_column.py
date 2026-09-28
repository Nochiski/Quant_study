#!/usr/bin/env python3
"""stage 절단본 픽스처(`database/tests/fixtures/stage_slice/<table>/v=*/**.parquet`)에
파생 컬럼 하나를 제자리에서 더한다.

절단본은 서버 current_build 에서 잘라낸 실물이고 생성 스크립트가 커밋돼 있지 않다
(`fixtures/stage_slice/README.md`). stage 규칙에 파생 컬럼을 더하면 서버 재빌드 전까지
절단본에는 그 열이 없어 소비층(equity) 테스트가 열지 못한다 — 서버 전량을 다시 내려받는 대신
**같은 식**으로 열만 채워 넣는다. 행 수·행 순서·기존 값은 그대로다.

사용:
  python database/scripts/fixtures_add_column.py stg_fin account_nm_norm \\
      --expr "regexp_replace(account_nm, '\\s+', '', 'g')" --after is_krw

파티션 파일마다 `read_parquet` → 열 삽입 → 같은 경로에 다시 쓴다. `--after` 는 컬럼 순서를
서버 빌드(`stage/build.py _output_columns` — extras 는 선언 순서대로 컬럼 뒤에 붙는다)와
맞추기 위한 것이다. 이미 그 열이 있는 파일은 건너뛴다(멱등).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

SLICE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "stage_slice"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def add_column(table_root: Path, column: str, expr: str, after: str) -> int:
    files = sorted(p for p in table_root.rglob("*.parquet"))
    if not files:
        raise SystemExit(f"파티션 parquet 이 없다: {table_root}")
    con = duckdb.connect()
    n_done = 0
    for f in files:
        src = str(f).replace("'", "''")
        cols = [str(r[0]) for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{src}')").fetchall()]
        if column in cols:
            continue
        if after not in cols:
            raise SystemExit(f"--after 컬럼이 없다: {after} ({f})")
        sel = [_q(c) for c in cols]
        sel.insert(cols.index(after) + 1, f"{expr} AS {_q(column)}")
        tmp = f.with_suffix(".parquet.tmp")
        dst = str(tmp).replace("'", "''")
        # preserve_insertion_order(기본 true) 가 행 순서를 지킨다 — 절단본 비교의 전제다.
        con.execute(f"COPY (SELECT {', '.join(sel)} FROM read_parquet('{src}')) "
                    f"TO '{dst}' (FORMAT PARQUET)")
        tmp.replace(f)
        n_done += 1
    con.close()
    return n_done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("table", help="stage 테이블명 (절단본 디렉터리 이름)")
    ap.add_argument("column", help="더할 컬럼명")
    ap.add_argument("--expr", required=True, help="같은 행에서 계산하는 SQL 식")
    ap.add_argument("--after", required=True, help="이 컬럼 바로 뒤에 넣는다")
    ap.add_argument("--root", type=Path, default=SLICE_ROOT, help="절단본 루트")
    a = ap.parse_args()
    root = a.root / a.table
    if not root.is_dir():
        print(f"없는 테이블: {root}", file=sys.stderr)
        return 2
    n = add_column(root, a.column, a.expr, a.after)
    print(f"{a.table}.{a.column} — 파티션 {n}개 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
