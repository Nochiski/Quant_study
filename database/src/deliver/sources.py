"""새 시트(ETF · Analyst · Events · Market · Flow · Short)의 원천 읽기 — equity · stage 판 고정.

equity 표는 fi 판 JSON(`factor_inputs/_runs/<D>_<basis>.json`)의 `equity_builds` 에 있으면 그 판,
없으면 그날 `MANIFEST.json` 의 current_build 를 읽는다(`equity.inputs.resolve` — 디렉토리 glob 은
keep=3 으로 남은 옛 판을 함께 문다). stage 표도 같은 규칙으로 `stage_builds` 에 있으면 그 판(fi 가
읽는 컨센서스 · WISE 재무 표뿐 — 새 시트가 읽는 stage 표는 없어 늘 current_build). 읽은 판 id 는
`used` 에 모아 Meta 에 적는다.

루트는 fi 판 JSON 의 `equity_root` · `stage_root` 가 기본이고, deliver CLI `--equity-root` ·
`--stage-root` 로 덮는다(로컬 사본 대조 · 리허설).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
from equity import inputs

log = logging.getLogger(__name__)

# 새 시트 계산의 자원 상한 — 여섯 시트가 연결 하나를 같이 쓴다(메모리 상한도 하나). 서버 예산(새 시트
# 전체 60초 · 1.5GB) 안에서 엑셀 쪽 파이썬 메모리 몫을 남긴다
DUCKDB_THREADS = 2
DUCKDB_MEMORY_LIMIT = "1200MB"


class SourceMissing(Exception):
    """원천이 없거나 비어서 시트를 만들 수 없다 — 시트만 '데이터 없음: 사유'로 둔다."""


@dataclass
class Sources:
    """판 고정 원천 읽기. `equity(표)` · `stage(표)` 는 DuckDB 관계식 문자열을 돌려준다."""

    equity_root: Path | None
    stage_root: Path | None
    equity_pins: Mapping[str, str] = field(default_factory=dict)
    stage_pins: Mapping[str, str] = field(default_factory=dict)
    used: dict[str, str] = field(default_factory=dict)      # '층/표' → 판 id
    label: str = "새 시트"                                   # 로그에 적는 읽는 쪽 이름

    def _read(self, layer: str, root: Path | None, table: str, build_id: str | None) -> str:
        # 사유 글은 엑셀 시트에 그대로 실린다 — 서버 경로는 로그에만 남기고, 읽는 사람이 모르는
        # '판' 대신 '데이터'(10-11 사용자)
        if root is None:
            raise SourceMissing(f"{layer} 위치를 모른다(factor_inputs 기록에 {layer}_root 없음)")
        try:
            pb = inputs.resolve(Path(root), table, build_id)
        except (FileNotFoundError, KeyError, ValueError) as e:
            log.warning("%s 원천 %s %s 를 못 읽었다 — %s", self.label, layer, table, e)
            raise SourceMissing(f"{layer} {table} 데이터를 못 찾았다") from e
        files = [str(p) for p in pb.globs]
        if not files:
            raise SourceMissing(f"{layer} {table} 데이터 {pb.build_id} 에 파일이 없다")
        self.used[f"{layer}/{table}"] = pb.build_id
        lst = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
        return f"read_parquet([{lst}], hive_partitioning=false, union_by_name=true)"

    def equity(self, table: str) -> str:
        return self._read("equity", self.equity_root, table, self.equity_pins.get(table))

    def stage(self, table: str) -> str:
        return self._read("stage", self.stage_root, table, self.stage_pins.get(table))

    def used_text(self) -> str:
        """Meta 한 줄 — 층마다 '표=판 id'."""
        out = []
        for layer in ("equity", "stage"):
            items = [f"{k.split('/', 1)[1]}={v}" for k, v in sorted(self.used.items())
                     if k.startswith(layer + "/")]
            if items:
                out.append(f"{layer} " + ", ".join(items))
        return " · ".join(out) or "없음"


def open_sources(fi_meta: Mapping[str, object], equity_root: Path | None = None,
                 stage_root: Path | None = None) -> Sources:
    """fi 판 JSON 에서 루트 · equity · stage 판 고정표를 읽는다. 인자로 준 루트가 이긴다."""
    def root(v: object) -> Path | None:
        return Path(v) if isinstance(v, str) and v else None

    def pins(key: str) -> dict[str, str]:
        got = fi_meta.get(key)
        return {str(k): str(v) for k, v in got.items()} if isinstance(got, Mapping) else {}
    return Sources(equity_root or root(fi_meta.get("equity_root")),
                   stage_root or root(fi_meta.get("stage_root")),
                   pins("equity_builds"), pins("stage_builds"))


def connect(temp_dir: Path | None = None) -> duckdb.DuckDBPyConnection:
    """자원을 묶은 DuckDB 연결. temp_dir = 메모리를 넘길 때 쓸 임시 폴더(호출부가 만들고 지운다)."""
    con = duckdb.connect()
    con.execute(f"SET threads = {DUCKDB_THREADS}")
    con.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
    if temp_dir is not None:
        con.execute(f"SET temp_directory = '{str(temp_dir).replace(chr(39), chr(39) * 2)}'")
    return con


def rows(con: duckdb.DuckDBPyConnection, sql: str,
         params: list[object] | None = None) -> list[dict[str, object]]:
    cur = con.execute(sql, params or [])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def num(x: object) -> float | None:
    """DECIMAL · 정수 · 실수 → float(None 그대로)."""
    if x is None:
        return None
    if isinstance(x, int | float | Decimal) and not isinstance(x, bool):
        return float(x)
    raise TypeError(f"숫자 칸이 아니다: {type(x).__name__} {x!r}")


def num0(x: object) -> float:
    """빈칸 = 0(합계 · 샘플의 `float(x or 0)`)."""
    v = num(x)
    return 0.0 if v is None else v


def day_of(x: object) -> date:
    if isinstance(x, date):
        return x
    raise TypeError(f"날짜 칸이 아니다: {type(x).__name__} {x!r}")


def text(x: object) -> str:
    """글자 칸(빈칸 = '')."""
    return "" if x is None else str(x)


def lit(d: date) -> str:
    """SQL 날짜 상수 — date 형만 받는다."""
    return f"DATE '{date.fromisoformat(d.isoformat()).isoformat()}'"


__all__ = ["SourceMissing", "Sources", "connect", "day_of", "lit", "num", "num0", "open_sources",
           "rows", "text"]
