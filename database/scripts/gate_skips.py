"""최근 판 게이트 기록의 SKIP 을 허용표(`src/stage/skip_allow.py`)와 대조한다(K1-7a, 읽기 전용).

판 기록만 읽는다(MANIFEST.json · `_runs/*.json`). 원장·parquet 는 열지 않고 아무것도 쓰지 않는다.
  equity         `<home>/data/equity/<표>/MANIFEST.json` 의 current_build + builds[] 뒤에서 N 판
  stage          fi 가 직접 읽는 표(`factor_inputs.build.STAGE_SOURCES`)만 — 같은 방식
  factor_inputs  `<home>/data/factor_inputs/_runs/*.json` 이름순 뒤에서 N 개의 gates
  model          `<home>/data/model/_runs/*.json` 이름순 뒤에서 N 개의 specs·excluded_specs gates

출력(TSV): layer · table · gate · reason · n_builds · allowed(yes/NO) · last_build.
끝 줄에 허용표 밖 (층, 표, 게이트, 사유) 수. 하나라도 있으면 rc 1, 없으면 rc 0.

사용(서버, 배포 전 판에서):
  `.venv/bin/python scripts/gate_skips.py [--home ~/quant-ledger] [--last 10]`
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# 아래 둘은 위 sys.path 주입 뒤에야 import 된다(scripts/ 는 패키지가 아니다)
from factor_inputs.build import STAGE_SOURCES
from stage import skip_allow

Row = tuple[str, str, str, str]          # (layer, table, gate, reason)


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"# 읽기 실패 {path}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _manifest_builds(root: Path, tables: list[str],
                     last: int) -> Iterator[tuple[str, str, object]]:
    for t in tables:
        path = root / t / "MANIFEST.json"
        if not path.exists():
            print(f"# 판 없음 {path}", file=sys.stderr)
            continue
        m = _load(path)
        if not isinstance(m, dict):
            continue
        builds = [b for b in (m.get("builds") or []) if isinstance(b, dict)]
        picked = builds[-last:] if last > 1 else []
        # 롤백으로 포인터가 앞 판으로 돌아갔을 수 있다 — 현재 판은 늘 넣는다
        picked += [b for b in builds
                   if b.get("build_id") == m.get("current_build") and b not in picked]
        for b in picked:
            yield t, str(b.get("build_id")), b.get("gates")


def _equity_tables(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and not d.name.startswith(("_", ".")) and
                  (d / "MANIFEST.json").exists())


def _runs(root: Path, last: int) -> list[tuple[str, dict]]:
    d = root / "_runs"
    out = []
    for p in sorted(d.glob("*.json"))[-last:] if d.is_dir() else []:
        rec = _load(p)
        if isinstance(rec, dict):
            out.append((str(rec.get("build_id") or p.stem), rec))
    return out


def collect(home: Path, last: int) -> dict[Row, list[str]]:
    """(층, 표, 게이트, 사유) → 그 SKIP 이 나온 판 id 목록(오래된 순)."""
    data = home / "data"
    seen: dict[Row, list[str]] = {}

    def add(layer: str, table: str, bid: str, recorded: object) -> None:
        for gate, reason in skip_allow.recorded_skips(recorded):
            seen.setdefault((layer, table, gate, reason), []).append(bid)

    eq = data / "equity"
    for t, bid, g in _manifest_builds(eq, _equity_tables(eq), last):
        add("equity", t, bid, g)
    for t, bid, g in _manifest_builds(data / "stage", list(STAGE_SOURCES), last):
        add("stage", t, bid, g)
    for bid, rec in _runs(data / "factor_inputs", last):
        add("factor_inputs", "-", bid, rec.get("gates"))
    for bid, rec in _runs(data / "model", last):
        for key in ("specs", "excluded_specs"):
            specs = rec.get(key)
            for spec_id, s in (specs.items() if isinstance(specs, dict) else ()):
                if isinstance(s, dict):
                    add("model", str(spec_id), bid, s.get("gates"))
    return seen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="최근 판 게이트 SKIP 대 허용표(K1-7a, 읽기 전용)")
    ap.add_argument("--home", type=Path,
                    default=Path(os.environ.get("QL_HOME") or os.path.expanduser("~/quant-ledger")))
    ap.add_argument("--last", type=int, default=1,
                    help="표·층마다 뒤에서 몇 판을 볼지(기본 1 = 현재 판·최신 기록)")
    a = ap.parse_args(argv)
    if a.last < 1:
        ap.error("--last 는 1 이상")
    seen = collect(a.home, a.last)
    print("layer\ttable\tgate\treason\tn_builds\tallowed\tlast_build")
    n_out = 0
    for (layer, table, gate, reason), bids in sorted(seen.items()):
        ok = skip_allow.find(layer, gate, reason, None if table == "-" else table) is not None
        n_out += not ok
        print("\t".join((layer, table, gate, reason, str(len(bids)), "yes" if ok else "NO",
                         bids[-1])))
    print(f"# home={a.home} last={a.last} — SKIP 종류 {len(seen)} · 허용표 밖 {n_out}")
    return 1 if n_out else 0


if __name__ == "__main__":
    raise SystemExit(main())
