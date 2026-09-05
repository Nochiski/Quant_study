"""equity.duckdb — 데이터 없이 테이블 매크로만 (DESIGN §2 [결정 1] · §10 P1a~d).

P1c 실측: 매크로 본문의 경로는 **절대경로**여야 한다(상대경로는 `No files found`).
P1b: 파일 DB 를 `read_only=True` 로 재오픈해 매크로를 호출할 수 있다.
쓰기는 임시 파일에 만든 뒤 `os.replace` — 이미 파일을 연 read_only 리더는 옛 inode 를 계속 본다.
빌드·GC 뒤에는 반드시 다시 만든다(굽힌 `v=` 가 keep=3 GC 로 rmtree 되면 매크로가 깨진다).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from stage import manifest

CATALOG_NAME = "equity.duckdb"
META_NAME = "_catalog_meta.json"
# 매크로 이름 = 식별자 + 선택적 인자 목록. `v_universe(d, policy := 'all')` 같은 형태를 받는다.
_MACRO_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*(\([^()]*\))?$")

# 뷰 매크로 등록부 — T9(`v_universe` 등)가 채운다. 비어 있으면 빈 카탈로그를 만든다.
MACROS: dict[str, str] = {}


def table_builds(equity_root: Path) -> dict[str, str]:
    """커밋된 equity 테이블 → current_build. `_pinned`·`_tmp`·`_failed` 는 테이블이 아니다."""
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


def snapshot_id(builds: dict[str, str]) -> str:
    """전 테이블 current_build 를 정렬해 뜬 해시 — 카탈로그가 어느 판을 가리키는지의 지문."""
    payload = "\n".join(f"{t}={b}" for t, b in sorted(builds.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def write_catalog(equity_root: Path, macros: dict[str, str]) -> Path:
    """매크로만 담은 `equity.duckdb` 를 임시 파일에 만들고 `os.replace` 로 교체한다."""
    bad = sorted(n for n in macros if not _MACRO_NAME_RE.match(n))
    if bad:
        raise ValueError(f"invalid macro name(s): got={bad} "
                         f"expected=<identifier>[(<args>)] equity_root={equity_root}")
    equity_root.mkdir(parents=True, exist_ok=True)
    tmp = equity_root / f".{CATALOG_NAME}.{os.getpid()}.tmp"
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    try:
        for name, sql in sorted(macros.items()):
            con.execute(f"CREATE OR REPLACE MACRO {name} AS TABLE {sql}")
    finally:
        con.close()
    dst = equity_root / CATALOG_NAME
    os.replace(tmp, dst)        # 파일 원자 교체 — 유일한 전환 지점(MANIFEST 규약과 같다)
    builds = table_builds(equity_root)
    meta = {"snapshot_id": snapshot_id(builds), "builds": builds,
            "macros": sorted(macros), "written_at_utc":
                datetime.now(UTC).isoformat(timespec="seconds")}
    meta_path = equity_root / META_NAME
    meta_tmp = meta_path.with_suffix(".json.tmp")
    meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(meta_tmp, meta_path)
    return dst
