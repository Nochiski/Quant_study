"""replay.sh 보조 — 격리 재생의 stage 입력 고정 · 표별 해시 · 두 루트 대조(컷오버 트랙 X-1).

표준 라이브러리만 쓴다. 검증 대상 코드(`replay.sh --code`)를 import 하지 않아야 옛 배포본·새 브랜치
어느 쪽을 재생해도 이 도구가 같은 규약으로 읽는다. 읽는 규약은 `src/stage/manifest.py`(MANIFEST
포인터가 정본 — 디렉터리 glob 금지)와 `src/equity/inputs.py`(`_pinned/<표>/MANIFEST.json` 에 고정한
BuildRecord 가 쌓이고 판 디렉터리는 stage 와 같은 상대 경로)를 따른다.

  stage-at --ops-data DIR --date D --basis B --dest DIR
      인계 이력 `<ops-data>/deliver/history/<D>_<B>.json` 의 `stage_builds` 를 가리키는 임시 stage
      루트를 DEST 에 세운다. 표마다 그 판을 운영 stage(keep 3판)에서, 없으면 equity
      `_pinned/`(인계 이력 30일 보호 — `build_chain.sh` gc_step)에서 찾아 `v=<build>` 디렉터리
      심볼릭 링크로 걸고, 그 판 BuildRecord 1개만 담은 MANIFEST 를 쓴다. 운영 파일은 읽기만 한다.
      어디에도 없는 판은 MANIFEST 만 쓰고 판 디렉터리는 만들지 않는다 — 그 표를 읽는 단계가 '파일
      없음'으로 실패한다. 표를 아예 빼면 fi 의 선택 원천(`stg_fin_wise_q`)이 조용히 빈 표로 대체돼
      해시만 달라지고 원인이 가려진다(P1).
      stdout: `표 build_id 출처(stage|pinned|missing) 실제 경로` TSV · stderr: 사람이 읽는 한 줄.
  rows --data DIR --layer equity|fi|model [--table T] [--date D] [--basis B]
      표별 `표 build_id content_hash n_rows` TSV. equity 는 current_build, fi·model 은 --date 가
      있으면 `_runs/<D>_<B>.json` 의 판, 없으면 current_build.
  compare A B [--date D] [--basis B]
      두 루트(`<root>/data/…`)의 표별 content_hash 대조(읽기 전용). equity 는 그 루트에 인계
      이력 `data/deliver/history/<D>_<B>.json` 이 있으면 그 판(운영 루트 — current 는 이미 다음
      판일 수 있다), 없으면 current_build(재생 루트). fi·model 은 rows 와 같다.

rc: 0 정상(compare 는 전부 같음) · 1 compare 다름·한쪽 없음 · 2 입력 오류.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

NONE = "-"
LAYER_DIR = {"equity": "equity", "fi": "factor_inputs", "model": "model"}
Row = tuple[str, str, str]           # (build_id, content_hash, n_rows) — 못 찾으면 NONE


class ToolError(Exception):
    """입력 오류 — rc 2 로 끝낸다."""


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ToolError(f"JSON 을 못 읽었다: {path} ({type(e).__name__}: {e})") from e


def _builds(manifest_path: Path) -> tuple[str | None, list[dict]]:
    """(current_build, builds[]). MANIFEST 가 없으면 (None, [])."""
    if not manifest_path.exists():
        return None, []
    m = _load(manifest_path)
    return m.get("current_build"), list(m.get("builds") or [])


def _find(builds: list[dict], build_id: str | None) -> dict | None:
    return next((b for b in builds if b.get("build_id") == build_id), None)


def _row(build_id: str | None, rec: dict | None) -> Row:
    if rec is None:
        return (build_id or NONE, NONE, NONE)
    return (str(build_id), str(rec.get("content_hash", NONE)), str(rec.get("n_rows", NONE)))


def _tables(layer_root: Path) -> list[str]:
    """MANIFEST 가 있는 표 디렉터리. `_`·`.` 로 시작하면 표가 아니다(equity.inputs 와 같은 규약)."""
    if not layer_root.is_dir():
        return []
    return sorted(d.name for d in layer_root.iterdir()
                  if d.is_dir() and not d.name.startswith(("_", "."))
                  and (d / "MANIFEST.json").exists())


# ── stage-at ─────────────────────────────────────────────────────────────────
def _locate(ops_data: Path, table: str, build_id: str) -> tuple[str, Path, dict] | None:
    """(출처, 판 디렉터리, BuildRecord). 레코드와 모든 파티션 디렉터리가 있어야 그 출처를 쓴다."""
    for source, table_root in (("stage", ops_data / "stage" / table),
                               ("pinned", ops_data / "equity" / "_pinned" / table)):
        rec = _find(_builds(table_root / "MANIFEST.json")[1], build_id)
        vdir = table_root / f"v={build_id}"
        if rec is None or not vdir.is_dir():
            continue
        if all((table_root / str(p.get("path", ""))).is_dir() for p in rec.get("partitions") or []):
            return source, vdir, rec
    return None


def stage_at(ops_data: Path, date: str, basis: str, dest: Path) -> int:
    hist = ops_data / "deliver" / "history" / f"{date}_{basis}.json"
    rep = _load(hist)
    builds = rep.get("stage_builds")
    if not isinstance(builds, dict) or not builds:
        raise ToolError(f"인계 이력에 stage_builds 가 없다: {hist}")
    if dest.exists():
        raise ToolError(f"임시 stage 루트가 이미 있다: {dest} — 패스마다 새 경로를 쓴다")
    dest.mkdir(parents=True)
    count = {"stage": 0, "pinned": 0, "missing": 0}
    missing: list[str] = []
    for table, bid in sorted(builds.items()):
        build_id = str(bid)
        tdir = dest / table
        tdir.mkdir()
        found = _locate(ops_data, table, build_id)
        if found is None:
            source, path = "missing", NONE
            # 판 디렉터리가 없는 레코드 — 읽는 단계가 이 경로를 짚고 실패한다
            rec: dict = {"build_id": build_id, "snapshot_id": "", "rules_version": "",
                         "built_at_utc": "", "n_rows": 0, "content_hash": "missing",
                         "partitions": [{"path": f"v={build_id}"}]}
            missing.append(table)
        else:
            source, vdir, rec = found
            path = str(vdir)
            (tdir / f"v={build_id}").symlink_to(vdir, target_is_directory=True)
        (tdir / "MANIFEST.json").write_text(
            json.dumps({"table": table, "current_build": build_id, "keep": 1, "builds": [rec]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        count[source] += 1
        print(f"{table}\t{build_id}\t{source}\t{path}")
    print(f"stage-at {hist.name}: {len(builds)}표 — 운영 stage {count['stage']} · "
          f"_pinned {count['pinned']} · 없음 {count['missing']}"
          + (f" ({', '.join(missing)} — 이 표를 읽는 단계는 실패한다)" if missing else "")
          + f" · 그날 health {json.dumps(rep.get('health'), ensure_ascii=False)}", file=sys.stderr)
    return 0


# ── rows ─────────────────────────────────────────────────────────────────────
def _current_rows(layer_root: Path) -> dict[str, Row]:
    out: dict[str, Row] = {}
    for t in _tables(layer_root):
        cur, builds = _builds(layer_root / t / "MANIFEST.json")
        out[t] = _row(cur, _find(builds, cur))
    return out


def layer_rows(data: Path, layer: str, date: str | None, basis: str,
               use_history: bool = False) -> tuple[str, dict[str, Row]]:
    """(어느 판을 골랐는지, {표: Row})."""
    root = data / LAYER_DIR[layer]
    if layer == "equity":
        hist = data / "deliver" / "history" / f"{date}_{basis}.json"
        if not (use_history and date and hist.exists()):
            return "current_build", _current_rows(root)
        builds = _load(hist).get("equity_builds") or {}
        return (f"인계 이력 {hist.name}",
                {t: _row(str(b), _find(_builds(root / t / "MANIFEST.json")[1], str(b)))
                 for t, b in sorted(builds.items())})
    if not date:
        return "current_build", _current_rows(root)
    runs = root / "_runs" / f"{date}_{basis}.json"
    if not runs.exists():
        return f"_runs/{runs.name} 없음", {}
    p = _load(runs)
    if p.get("status") != "ok":
        return f"_runs/{runs.name} status={p.get('status')}", {}
    bid = str(p.get("build_id"))
    if layer == "fi":
        return (f"_runs/{runs.name}",
                {t: (bid, str(i.get("content_hash", NONE)), str(i.get("n_rows", NONE)))
                 for t, i in sorted((p.get("tables") or {}).items())})
    return (f"_runs/{runs.name}",
            {s: _row(bid, _find(_builds(root / s / "MANIFEST.json")[1], bid))
             for s in sorted(p.get("specs") or {})})


def rows(data: Path, layer: str, table: str | None, date: str | None, basis: str) -> int:
    _, got = layer_rows(data, layer, date, basis)
    if table is not None:
        got = {table: got.get(table, (NONE, NONE, NONE))}
    for t, (bid, h, n) in got.items():
        print(f"{t}\t{bid}\t{h}\t{n}")
    return 0


# ── compare ──────────────────────────────────────────────────────────────────
def compare(a: Path, b: Path, date: str | None, basis: str) -> int:
    print(f"== 대조 A={a} · B={b} · D={date or NONE} basis={basis}")
    print("layer\ttable\tA_build\tA_hash\tB_build\tB_hash\tverdict")
    totals: list[str] = []
    n_diff = n_none = 0
    for layer in LAYER_DIR:
        src_a, ra = layer_rows(a / "data", layer, date, basis, use_history=True)
        src_b, rb = layer_rows(b / "data", layer, date, basis, use_history=True)
        print(f"# {layer}: A {src_a} · B {src_b}")
        same = diff = none = 0
        for t in sorted(set(ra) | set(rb)):
            x, y = ra.get(t, (NONE,) * 3), rb.get(t, (NONE,) * 3)
            if NONE in (x[1], y[1]):
                verdict, none = "없음", none + 1
            elif x[1] == y[1]:
                verdict, same = "같음", same + 1
            else:
                verdict, diff = "다름", diff + 1
            print(f"{layer}\t{t}\t{x[0]}\t{x[1]}\t{y[0]}\t{y[1]}\t{verdict}")
        totals.append(f"{layer} {same}/{same + diff + none}")
        n_diff, n_none = n_diff + diff, n_none + none
    print(f"대조 같음 {' · '.join(totals)} — 다름 {n_diff} · 없음 {n_none}")
    return 0 if n_diff == 0 and n_none == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="replay_tool.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage-at")
    s.add_argument("--ops-data", type=Path, required=True)
    s.add_argument("--date", required=True)
    s.add_argument("--basis", required=True)
    s.add_argument("--dest", type=Path, required=True)
    r = sub.add_parser("rows")
    r.add_argument("--data", type=Path, required=True)
    r.add_argument("--layer", choices=sorted(LAYER_DIR), required=True)
    r.add_argument("--table")
    r.add_argument("--date")
    r.add_argument("--basis", default="morning")
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    c.add_argument("--date")
    c.add_argument("--basis", default="morning")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "stage-at":
            return stage_at(a.ops_data, a.date, a.basis, a.dest)
        if a.cmd == "rows":
            return rows(a.data, a.layer, a.table, a.date, a.basis)
        return compare(a.a, a.b, a.date, a.basis)
    except ToolError as e:
        print(f"replay_tool {a.cmd}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
