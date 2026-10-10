"""replay.sh 보조 — 격리 재생의 stage 입력 고정 · 표별 해시 · 두 루트 대조(컷오버 트랙 X-1).

표준 라이브러리만 쓴다. 검증 대상 코드(`replay.sh --code`)를 import 하지 않아야 옛 배포본·새 브랜치
어느 쪽을 재생해도 이 도구가 같은 규약으로 읽는다. 읽는 규약은 `src/stage/manifest.py`(MANIFEST
포인터가 정본 — 디렉터리 glob 금지)와 `src/equity/inputs.py`(`_pinned/<표>/MANIFEST.json` 에 고정한
BuildRecord 가 쌓이고 판 디렉터리는 stage 와 같은 상대 경로)를 따른다.

  stage-at --ops-data DIR --date D --basis B --dest DIR
      인계 이력 `<ops-data>/deliver/history/<D>_<B>.json` 의 `stage_builds` 를 가리키는 임시 stage
      루트를 DEST 에 세운다. 표마다 그 판을 운영 stage(keep 3판)에서, 없으면 equity
      `_pinned/`(인계 이력 30일 보호 — `build_chain.sh` gc_step)에서 찾아 파티션 파일을 하드링크로
      옮기고(`equity.inputs.pin` 과 같은 방식 — 디렉터리 링크를 두지 않아 `rm -rf` 가 운영에 닿지
      않는다), 그 판 BuildRecord 1개만 담은 MANIFEST 를 쓴다. 운영 파일은 읽기만 한다.
      어디에도 없는 판은 MANIFEST 만 쓰고 판 디렉터리는 만들지 않는다 — 그 표를 읽는 단계가 '파일
      없음'으로 실패한다. 표를 아예 빼면 fi 의 선택 원천(`stg_fin_wise_q`)이 조용히 빈 표로 대체돼
      해시만 달라지고 원인이 가려진다(P1).
      stdout: `표 build_id 출처(stage|pinned|missing) 실제 경로` TSV · stderr: 사람이 읽는 한 줄.
  rows --data DIR --layer equity|fi|model [--table T] [--date D] [--basis B]
      표별 `표 build_id content_hash n_rows` TSV. equity 는 current_build, fi·model 은 --date 가
      있으면 `_runs/<D>_<B>.json` 의 판, 없으면 current_build. model 은 spec 마다 scores 행과
      `<spec>:indicators` 행(파티션의 indicators_content_hash·n_indicators) 두 줄이다.
      `_runs` 가 없거나 ok 가 아니면 `_runs/<파일>` 이름의 빈 행 하나를 낸다(대조에서 '없음').
  compare A B [--date D] [--basis B]
      두 루트(`<root>/data/…`)의 표별 content_hash 대조(읽기 전용). `data/deliver/` 가 있는 루트
      (운영)의 equity 는 인계 이력 `history/<D>_<B>.json` 이 가리키는 판(current 는 이미 다음 판일
      수 있다) — 그 이력이 없으면 rc 2. `data/deliver/` 가 없는 루트(재생)는 current_build.
      운영 equity keep(10판) 밖으로 밀린 판은 '없음'으로 나온다. fi·model 은 rows 와 같다.


장 마감 판 재생(`replay.sh --basis evening`, 컷오버 PR-8b)이 쓰는 것:
  stage-pin --ops-data DIR --tables A,B[,…] [--optional C,…] --dest DIR
      운영 stage 현판(표마다 current_build)을 DEST 에 stage-at 과 같은 방식(파일 하드링크 + 1판
      MANIFEST)으로 고정한다 — fi 가 stage 에서 직접 읽는 표를 패스 동안 붙잡아 운영 GC·새 판과
      무관하게 한다. 선택 표(--optional)는 운영에 현판이 없으면 건너뛴다(fi 가 'absent' 로 읽는다).
      stdout 은 stage-at 과 같은 TSV.
  handoff --data DIR --stage-root DIR --date D [--note TEXT]
      재생 합성 인계 이력 `<data>/deliver/history/<D>_morning.json` — 장 마감 판 fi `--builds-from`
      과 수집기 대상(`daily.postclose.resolve_targets`)이 읽는 자리. equity = `<data>/equity` 현판,
      stage = --stage-root(stage-pin)의 판, health = ok(부르는 쪽이 equity-pass 로 먼저 본다),
      재생 표시 `replay`.
  equity-pass --logs DIR
      출력 루트 equity 판이 온전한가 — equity 를 지은 마지막 패스(summary.tsv 에 equity 행이 있는
      가장 큰 passN)의 표가 전부 rc 0 이면 0, 아니면 1(사유 stderr). 그런 패스가 없어도 1.
  board-summary --compare-dir DIR --dates T1,T2,… [--since EPOCH] [--summary FILE]
                [--postclose-dir DIR] [--d-prime D']
      날짜별 두 판 대조 결과(`daily.board_compare` 의 `<T>.json`) 집계 TSV — verdict · rc ·
      미설명 수 · spec 별 Spearman · 그날 실패 단계 · 재생 원장 결손. --since 보다 오래된 파일은 이번
      패스 것이 아니라 '없음'. 실패 단계 = 패스 summary.tsv(--summary)에서 `<단계>@T` 의 rc≠0
      (skip 은 실패가 아니다)과 그 T 의 연구 판 D'(`fi_r@D'` — 첫 T 의 D' 는 --d-prime, 다음 T 는 앞
      T)가 실패했으면 그것. 결과가 '없음'인 날의 사유는 실패 단계가 있으면 '실패 단계 X', 없으면
      '미실행'(앞 단계 건너뜀·준비 실패). 재생 원장 결손 = `<postclose-dir>/<T>/postclose.db` 의
      `replay_source`(scripts/replay_evening.py) n_missing · n_candidates_missing(파일·행이 없으면 '-').
      끝 줄은 합계 한 줄. rc 0 = 모든 날 pass, 아니면 1.

rc: 0 정상(compare 는 전부 같음) · 1 compare 다름·한쪽 없음(equity-pass 온전하지 않음 ·
board-summary pass 아닌 날 있음) · 2 입력 오류.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
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


def _model_rows(spec: str, build_id: str | None, rec: dict | None) -> dict[str, Row]:
    """spec 의 scores 행 + indicators 행. 지표 해시는 첫 파티션에 있다(model/build.py commit)."""
    part = ((rec or {}).get("partitions") or [{}])[0]
    ind: Row = (build_id or NONE, str(part.get("indicators_content_hash", NONE)),
                str(part.get("n_indicators", NONE)))
    return {spec: _row(build_id, rec), f"{spec}:indicators": ind if rec else _row(build_id, None)}


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


def _link_files(src_root: Path, rec: dict, dst_root: Path) -> None:
    """파티션 파일을 하드링크한다. 하위 디렉터리(`_reject/`)는 입력이 아니라 건너뛴다
    (inputs.pin 과 같다)."""
    for p in rec.get("partitions") or []:
        rel = str(p.get("path", ""))
        out = dst_root / rel
        out.mkdir(parents=True, exist_ok=True)
        for f in sorted((src_root / rel).iterdir()):
            if f.is_file():
                try:
                    os.link(f, out / f.name)
                except OSError as e:
                    raise ToolError(f"하드링크 실패: {f} → {out / f.name} ({e}) — 출력 루트는 운영 "
                                    "data 와 같은 파일시스템이어야 한다") from e


def _write_one_build(tdir: Path, table: str, build_id: str, rec: dict) -> None:
    """그 판 BuildRecord 1개만 담은 MANIFEST — 읽는 쪽(`stage.manifest`·`equity.inputs`)이
    current 로 푼다."""
    (tdir / "MANIFEST.json").write_text(
        json.dumps({"table": table, "current_build": build_id, "keep": 1, "builds": [rec]},
                   ensure_ascii=False, indent=1), encoding="utf-8")


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
            _link_files(vdir.parent, rec, tdir)
        _write_one_build(tdir, table, build_id, rec)
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
        if not (use_history and date and (data / "deliver").is_dir()):
            return "current_build", _current_rows(root)
        if not hist.exists():
            raise ToolError(f"인계 이력이 없다: {hist} — 이 루트의 D 판을 정할 수 없다")
        builds = _load(hist).get("equity_builds") or {}
        return (f"인계 이력 {hist.name}",
                {t: _row(str(b), _find(_builds(root / t / "MANIFEST.json")[1], str(b)))
                 for t, b in sorted(builds.items())})
    if not date:
        if layer == "fi":
            return "current_build", _current_rows(root)
        out: dict[str, Row] = {}
        for spec in _tables(root):
            cur, builds = _builds(root / spec / "MANIFEST.json")
            out.update(_model_rows(spec, cur, _find(builds, cur)))
        return "current_build", out
    runs = root / "_runs" / f"{date}_{basis}.json"
    absent = {f"_runs/{runs.name}": (NONE, NONE, NONE)}     # 대조에서 '없음' 1 로 센다
    if not runs.exists():
        return f"_runs/{runs.name} 없음", absent
    p = _load(runs)
    if p.get("status") != "ok":
        return f"_runs/{runs.name} status={p.get('status')}", absent
    bid = str(p.get("build_id"))
    if layer == "fi":
        return (f"_runs/{runs.name}",
                {t: (bid, str(i.get("content_hash", NONE)), str(i.get("n_rows", NONE)))
                 for t, i in sorted((p.get("tables") or {}).items())})
    rows_: dict[str, Row] = {}
    for spec in sorted(p.get("specs") or {}):
        rows_.update(_model_rows(spec, bid, _find(_builds(root / spec / "MANIFEST.json")[1], bid)))
    return f"_runs/{runs.name}", rows_


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


# ── stage-pin · handoff · equity-pass · board-summary (장 마감 판 재생, PR-8b) ─────────────
def stage_pin(ops_data: Path, tables: list[str], optional: list[str], dest: Path) -> int:
    if dest.exists():
        raise ToolError(f"고정 stage 루트가 이미 있다: {dest} — 패스마다 새 경로를 쓴다")
    plan: list[tuple[str, str | None, tuple[str, Path, dict] | None]] = []
    for table in [*tables, *optional]:
        cur, _ = _builds(ops_data / "stage" / table / "MANIFEST.json")
        if cur is None:
            if table in optional:
                plan.append((table, None, None))
                continue
            raise ToolError(f"운영 stage 에 이 표의 현판이 없다: {ops_data / 'stage' / table}")
        found = _locate(ops_data, table, cur)
        if found is None:
            raise ToolError(f"운영 stage 현판의 판 디렉터리·파티션이 없다: table={table} "
                            f"build_id={cur}")
        plan.append((table, cur, found))
    dest.mkdir(parents=True)
    absent: list[str] = []
    for table, cur, found in plan:
        if cur is None or found is None:
            absent.append(table)
            print(f"{table}\t{NONE}\tabsent\t{NONE}")
            continue
        source, vdir, rec = found
        tdir = dest / table
        tdir.mkdir()
        _link_files(vdir.parent, rec, tdir)
        _write_one_build(tdir, table, cur, rec)
        print(f"{table}\t{cur}\t{source}\t{vdir}")
    print(f"stage-pin: 운영 stage 현판 {len(plan) - len(absent)}표 고정 → {dest}"
          + (f" · 선택 표 현판 없음 {', '.join(absent)}(fi 가 absent 로 읽는다)" if absent else ""),
          file=sys.stderr)
    return 0


def handoff(data: Path, stage_root: Path, date: str, note: str) -> int:
    if not (len(date) == 8 and date.isdigit()):
        raise ToolError(f"--date 는 YYYYMMDD: {date!r}")
    eq_root = data / "equity"
    eq = {t: cur for t in _tables(eq_root)
          if (cur := _builds(eq_root / t / "MANIFEST.json")[0]) is not None}
    st = {t: cur for t in _tables(stage_root)
          if (cur := _builds(stage_root / t / "MANIFEST.json")[0]) is not None}
    if not eq:
        raise ToolError(f"equity 현판이 없다: {eq_root}")
    if not st:
        raise ToolError(f"고정 stage 판이 없다: {stage_root}")
    path = data / "deliver" / "history" / f"{date}_morning.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {"date": date, "basis": "morning", "stage_builds": st, "equity_builds": eq,
           "health": {"stage": "ok", "equity": "ok"},
           "replay": {"note": note or "재생 합성 인계 이력(PR-8b)", "equity_root": str(eq_root),
                      "stage_root": str(stage_root),
                      "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    print(f"handoff {path.name}: equity {len(eq)}표 · stage {len(st)}표 → {path}")
    return 0


def equity_pass(logs: Path) -> int:
    passes = sorted((p for p in logs.glob("pass*") if p.name[4:].isdigit()),
                    key=lambda p: int(p.name[4:]), reverse=True)
    for p in passes:
        tsv = p / "summary.tsv"
        if not tsv.exists():
            continue
        rows = [ln.split("\t") for ln in tsv.read_text(encoding="utf-8").splitlines()[1:]]
        eq = [r for r in rows if r and r[0] == "equity"]
        if not eq:
            continue
        bad = [r[1] for r in eq if len(r) < 3 or r[2] != "0"]
        if bad:
            print(f"{p.name} equity 가 온전하지 않다 — 실패·건너뜀 {len(bad)}/{len(eq)}표: "
                  f"{', '.join(bad[:5])}", file=sys.stderr)
            return 1
        print(f"{p.name} equity {len(eq)}/{len(eq)}")
        return 0
    print(f"equity 를 지은 패스가 없다: {logs}", file=sys.stderr)
    return 1


# 재생 원장의 재생 표시 표 — scripts/replay_evening.py REPLAY_TABLE 과 같다(이 도구는 그 모듈을 읽지 않는다)
REPLAY_SOURCE_TABLE = "replay_source"


def _failed_steps(summary: Path) -> dict[str, list[str]]:
    """패스 summary.tsv 의 날짜 단계(`<단계>@<날짜>`) 중 rc≠0 — {날짜: [단계…]}(실행 순서).
    skip 은 돌지 않은 것이라 실패가 아니다."""
    out: dict[str, list[str]] = {}
    for ln in summary.read_text(encoding="utf-8").splitlines()[1:]:
        r = ln.split("\t")
        if len(r) < 3 or r[1] != NONE or "@" not in r[0] or r[2] in ("0", "skip"):
            continue
        out.setdefault(r[0].rsplit("@", 1)[1], []).append(r[0])
    return out


def _replay_missing(postclose_dir: Path | None, d: str) -> tuple[str, str]:
    """(n_missing, n_candidates_missing) — 재생 원장이 없거나 그날 행이 없으면 NONE."""
    path = None if postclose_dir is None else postclose_dir / d / "postclose.db"
    if path is None or not path.is_file():
        return NONE, NONE
    try:
        con = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)
        try:
            row = con.execute(f'SELECT n_missing, n_candidates_missing FROM "{REPLAY_SOURCE_TABLE}" '
                              "WHERE dt = ?", (d,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return NONE, NONE
    return (NONE, NONE) if row is None else (str(row[0]), str(row[1]))


def board_summary(compare_dir: Path, dates: list[str], since: float,
                  summary: Path | None = None, postclose_dir: Path | None = None,
                  d_prime: str | None = None) -> int:
    print("date\tverdict\trc\tn_unexplained\tn_unexplained_tickers\tspearman_min\tspearman\t"
          "failed_steps\tn_missing\tn_candidates_missing\treasons")
    if summary is not None and not summary.is_file():
        raise ToolError(f"패스 summary.tsv 가 없다: {summary}")
    failed_by_day = {} if summary is None else _failed_steps(summary)
    count: Counter[str] = Counter()
    n_unexp = n_failed_days = 0
    worst: tuple[float, str, str] | None = None
    for i, d in enumerate(dates):
        # 그날 실패 단계 — 그 T 의 단계 + 그 T 의 연구 판 D'(fi_r@D') 가 실패했으면 그것(앞에 둔다)
        dp = dates[i - 1] if i else d_prime
        failed = [f"fi_r@{dp}"] if dp and f"fi_r@{dp}" in failed_by_day.get(dp, []) else []
        failed += failed_by_day.get(d, [])
        shown_failed = ",".join(failed) or NONE
        missing = "\t".join(_replay_missing(postclose_dir, d))
        path = compare_dir / f"{d}.json"
        if not path.exists() or path.stat().st_mtime < since:
            count["없음"] += 1
            if summary is None:
                why = "이번 패스의 대조 결과가 없다"
            elif failed:
                n_failed_days += 1
                why = f"실패 단계 {', '.join(failed)} — 이번 패스의 대조 결과가 없다"
            else:
                why = "미실행 — 이번 패스의 대조 결과가 없고 그날 실패 단계도 없다(앞 단계 건너뜀·준비 실패)"
            print(f"{d}\t없음" + f"\t{NONE}" * 5 + f"\t{shown_failed}\t{missing}\t{why}")
            continue
        rep = _load(path)
        verdict = str(rep.get("verdict"))
        raw = rep.get("model")
        model = raw if isinstance(raw, dict) else {}
        sp = {sid: m.get("spearman") for sid, m in sorted(model.items()) if isinstance(m, dict)}
        vals = sorted((float(v), sid) for sid, v in sp.items() if isinstance(v, int | float))
        if vals and (worst is None or vals[0][0] < worst[0]):
            worst = (vals[0][0], vals[0][1], d)
        n = rep.get("n_unexplained")
        n_unexp += n if isinstance(n, int) else 0
        reasons = rep.get("reasons") or ([str(rep["error"])] if rep.get("error") else [])
        count[verdict] += 1
        shown = ",".join(f"{sid}={NONE if v is None else f'{float(v):.4f}'}"
                         for sid, v in sp.items())
        print(f"{d}\t{verdict}\t{rep.get('rc')}\t{NONE if n is None else n}\t"
              f"{rep.get('n_unexplained_tickers', NONE)}\t"
              f"{NONE if not vals else f'{vals[0][0]:.4f}'}\t{shown or NONE}\t"
              f"{shown_failed}\t{missing}\t"
              f"{'; '.join(str(r) for r in reasons) or NONE}")
    low = NONE if worst is None else f"{worst[0]:.4f}({worst[1]} {worst[2]})"
    none_split = ("" if summary is None or not count["없음"] else
                  f"(실패 단계 {n_failed_days} · 미실행 {count['없음'] - n_failed_days})")
    print(f"대조 {len(dates)}일 — pass {count['pass']} · fail {count['fail']} · "
          f"error {count['error']} · 없음 {count['없음']}{none_split} · 미설명 합 {n_unexp:,} · "
          f"Spearman 최저 {low}")
    return 0 if count["pass"] == len(dates) else 1


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
    sp = sub.add_parser("stage-pin")
    sp.add_argument("--ops-data", type=Path, required=True)
    sp.add_argument("--tables", required=True, help="쉼표 목록 — 현판이 없으면 rc 2")
    sp.add_argument("--optional", default="", help="쉼표 목록 — 현판이 없으면 건너뛴다")
    sp.add_argument("--dest", type=Path, required=True)
    h = sub.add_parser("handoff")
    h.add_argument("--data", type=Path, required=True)
    h.add_argument("--stage-root", type=Path, required=True)
    h.add_argument("--date", required=True)
    h.add_argument("--note", default="")
    e = sub.add_parser("equity-pass")
    e.add_argument("--logs", type=Path, required=True)
    b = sub.add_parser("board-summary")
    b.add_argument("--compare-dir", type=Path, required=True)
    b.add_argument("--dates", required=True, help="쉼표 목록 YYYYMMDD")
    b.add_argument("--since", type=float, default=0.0,
                   help="이보다 오래된 결과 파일은 '없음'(epoch 초)")
    b.add_argument("--summary", type=Path, help="그 패스 summary.tsv — 날짜별 실패 단계(rc≠0)")
    b.add_argument("--postclose-dir", type=Path,
                   help="재생 원장 폴더(<T>/postclose.db) — replay_source 의 결손 수")
    b.add_argument("--d-prime", help="첫 T 의 연구 판 D' — 그 fi_r 실패를 첫 T 에 싣는다")
    a = ap.parse_args(argv)

    def split(v: str) -> list[str]:
        return [x for x in v.split(",") if x]
    try:
        if a.cmd == "stage-at":
            return stage_at(a.ops_data, a.date, a.basis, a.dest)
        if a.cmd == "rows":
            return rows(a.data, a.layer, a.table, a.date, a.basis)
        if a.cmd == "stage-pin":
            return stage_pin(a.ops_data, split(a.tables), split(a.optional), a.dest)
        if a.cmd == "handoff":
            return handoff(a.data, a.stage_root, a.date, a.note)
        if a.cmd == "equity-pass":
            return equity_pass(a.logs)
        if a.cmd == "board-summary":
            return board_summary(a.compare_dir, split(a.dates), a.since, a.summary,
                                 a.postclose_dir, a.d_prime)
        return compare(a.a, a.b, a.date, a.basis)
    except ToolError as e:
        print(f"replay_tool {a.cmd}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
