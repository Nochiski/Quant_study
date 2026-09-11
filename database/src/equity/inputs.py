"""stage 입력 해석·고정 (EQUITY_WORKFLOW §1 · DESIGN §2 [결정 2]).

읽기 계약: `<stage_root>/<table>/MANIFEST.json` 의 `current_build` → 그 BuildRecord 의
`partitions[].path` 만 쓴다. 구버전 `v=…` 디렉토리가 keep=3 으로 공존하므로 디렉토리 glob 은
금지다(STAGE_HANDOFF §1).

고정: 고른 build 의 파티션 파일을 `<equity_root>/_pinned/<table>/v=<build>/` 로 하드링크하고
BuildRecord 1건만 담은 MANIFEST 를 그 옆에 원자 기록한다 — stage 의 keep=3 GC
(`manifest.commit()` 의 rmtree)가 입력을 지워도 빌드가 재현된다.
`_pinned/` 에는 `manifest.commit()` 을 부르지 않는다(그 함수가 keep 밖 `v=` 를 rmtree 한다).

equity 내부 입력: 이름이 `stg_` 로 시작하지 않는 입력은 앞서 커밋된 equity 테이블이다 —
`<equity_root>/<table>/MANIFEST.json` 의 current_build 를 같은 규약으로 `_pinned/` 에 고정한다
(S03 `universe_daily` 가 `security_span`·`trading_calendar` 를 읽는 식).
자기 참조는 model 이 거부한다.
"""
from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb
from stage import manifest


@dataclass(frozen=True)
class PinnedBuild:
    """고정한 stage 빌드 1개. 경로는 전부 MANIFEST 의 partitions[].path 에서 나온다."""

    table: str
    build_id: str
    root: Path                          # v=<build_id> 디렉토리
    partition_paths: tuple[Path, ...]   # partitions[].path 를 절대경로로 푼 것
    meta: dict[str, object]             # 첫 파티션 _meta.json (gates·lag_known·coverage_from)

    @property
    def globs(self) -> tuple[str, ...]:
        return tuple(str(p / "*.parquet") for p in self.partition_paths)

    @property
    def has_year_axis(self) -> bool:
        return any(p.name.startswith("year=") for p in self.partition_paths)


# `_pinned/<table>/v=<build_id>/` 경로에서 hive_partitioning 이 뽑아내는 축. stage 컬럼이 아니므로
# 뷰에 노출하지 않는다 — 노출하면 같은 이름의 stage 컬럼을 build_id 문자열로 덮어쓴다.
_PIN_AXIS_COLUMN = "v"

STAGE_PREFIX = "stg_"                # stage 테이블 실명은 전부 이 접두를 갖는다 (STAGE_HANDOFF §1)
PINNED_DIR = "_pinned"
# `<equity_root>/` 아래에서 테이블이 **아닌** 디렉토리 — `_pinned`·`_tmp`·`_failed`·`_asof`.
_NON_TABLE_PREFIXES: tuple[str, ...] = ("_", ".")


def source_root(stage_root: Path, equity_root: Path, table: str) -> Path:
    """입력 이름으로 출처 루트를 고른다 — `stg_*` 는 stage, 그 외는 앞서 커밋된 equity 테이블."""
    return stage_root if table.startswith(STAGE_PREFIX) else equity_root


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _record(m: manifest.Manifest, table: str, path: Path) -> manifest.BuildRecord:
    if m.current_build is None:
        raise FileNotFoundError(
            f"input table has no committed build — build it first: table={table} manifest={path}")
    for b in m.builds:
        if b.build_id == m.current_build:
            return b
    raise FileNotFoundError(
        f"MANIFEST.current_build is not in builds[]: table={table} manifest={path} "
        f"current_build={m.current_build} builds={[b.build_id for b in m.builds]}")


def _load_meta(partition_paths: tuple[Path, ...]) -> dict[str, object]:
    """첫 파티션의 `_meta.json`. build.py 가 전 파티션에 같은 gates 를 쓰므로 하나면 충분하다."""
    for p in partition_paths:
        f = p / "_meta.json"
        if f.exists():
            raw = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
    return {}


def _build(table_root: Path, table: str, rec: manifest.BuildRecord) -> PinnedBuild:
    paths = tuple(table_root / str(p["path"]) for p in rec.partitions)
    return PinnedBuild(table, rec.build_id, table_root / f"v={rec.build_id}", paths,
                       _load_meta(paths))


def resolve(root: Path, table: str) -> PinnedBuild:
    """`<root>/<table>/MANIFEST.json` 의 current_build 를 푼다(root = stage 또는 equity)."""
    table_root = root / table
    path = table_root / "MANIFEST.json"
    return _build(table_root, table, _record(manifest.load(path), table, path))


def load_pinned(equity_root: Path, table: str, build_id: str) -> PinnedBuild:
    """이미 `_pinned/` 에 고정된 빌드를 다시 연다 (재판정·재현 빌드용)."""
    table_root = equity_root / "_pinned" / table
    path = table_root / "MANIFEST.json"
    rec = next((b for b in manifest.load(path).builds if b.build_id == build_id), None)
    if rec is None:
        raise FileNotFoundError(f"pinned build not found — table={table} build_id={build_id} "
                                f"manifest={path}")
    return _build(table_root, table, rec)


def pin(stage_root: Path, equity_root: Path, table: str) -> PinnedBuild:
    """current_build 를 `_pinned/` 에 하드링크로 고정한다. 같은 build 재고정은 no-op.

    다른 파일시스템이면 `os.link` 의 OSError(EXDEV)를 그대로 올린다 — 조용한 복사로 대체하지
    않는다(DESIGN §10 P9 가 stage 와 equity 가 같은 디바이스임을 실측했다).
    """
    from_root = source_root(stage_root, equity_root, table)
    src = resolve(from_root, table)
    dst_table_root = equity_root / "_pinned" / table
    dst_root = dst_table_root / f"v={src.build_id}"
    src_root = from_root / table / f"v={src.build_id}"
    dst_paths: list[Path] = []
    for src_dir in src.partition_paths:
        out = dst_root / src_dir.relative_to(src_root)
        dst_paths.append(out)
        if out.exists():
            continue                     # 이미 고정됨 — 재링크하지 않는다
        out.mkdir(parents=True)
        for f in sorted(src_dir.iterdir()):
            if f.is_file():              # `_reject/` 는 입력이 아니다 → 디렉토리는 건너뛴다
                os.link(f, out / f.name)
    _write_build_record(dst_table_root, table, manifest.load(
        from_root / table / "MANIFEST.json"), src.build_id)
    parts = tuple(dst_paths)
    return PinnedBuild(table, src.build_id, dst_root, parts, _load_meta(parts))


def _write_build_record(dst_table_root: Path, table: str, m: manifest.Manifest,
                        build_id: str) -> None:
    """`_pinned/<table>/MANIFEST.json` 에 BuildRecord 를 누적 기록한다(current_build = 이번 것).

    고정한 판본은 전부 살아 있어야 한다 — 상위 테이블이 재빌드돼 새 판본을 고정해도 `load_pinned` 로
    옛 판본을 다시 열어 재판정·재현 빌드를 할 수 있어야 하므로 덮어쓰지 않고 합친다.

    `manifest.commit()` 을 쓰면 안 된다 — keep 밖 `v=` 를 rmtree 해서 고정한 하드링크를 지운다.
    `manifest._write_atomic` 은 private 이라 같은 규약(임시 파일 → os.replace)을 여기서 쓴다.
    """
    rec = next((b for b in m.builds if b.build_id == build_id), None)
    if rec is None:
        raise FileNotFoundError(f"build record disappeared while pinning: table={table} "
                                f"build_id={build_id} builds={[b.build_id for b in m.builds]}")
    dst_table_root.mkdir(parents=True, exist_ok=True)
    path = dst_table_root / "MANIFEST.json"
    kept = [b for b in manifest.load(path).builds if b.build_id != build_id] + [rec]
    pinned = manifest.Manifest(table=table, current_build=build_id, keep=len(kept), builds=kept)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(pinned), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def create_views(con: duckdb.DuckDBPyConnection, pinned: dict[str, PinnedBuild],
                 declared: dict[str, list[str]]) -> None:
    """stage 테이블마다 선언 컬럼만 투영한 TEMP VIEW 를 stage 실명으로 만든다.

    선언에 없는 컬럼은 투영하지 않는다. 선언했는데 parquet 에 없는 컬럼도 투영하지 않고
    빠뜨린다 — 뷰 생성이 Binder 오류로 죽는 대신 EG0 가 `declared_columns_missing` 으로 잡게
    하기 위해서다. `hive_partitioning=true` 는 경로의 `year=` 를 컬럼으로 붙이므로(HANDOFF §1)
    그 축은 선언과 무관하게 노출해 `.sql` 이 연도 프루닝 술어로 쓴다. 같은 이유로 붙는
    `v=<build_id>` 축(`v` 컬럼)은 stage 컬럼이 아니므로 뷰에서 뺀다.
    """
    for table, pb in sorted(pinned.items()):
        globs = ", ".join(f"'{g}'" for g in pb.globs)
        src = (f"read_parquet([{globs}], hive_partitioning=true, union_by_name=true)")
        described = [str(r[0]) for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
        actual = [c for c in described if c != _PIN_AXIS_COLUMN]
        want = declared.get(table) or actual
        sel = [c for c in want if c in actual]
        if "year" in actual and "year" not in sel:
            sel.append("year")
        cols = ", ".join(_q(c) for c in sel) if sel else "NULL AS _no_declared_column"
        con.execute(f"CREATE OR REPLACE TEMP VIEW {_q(table)} AS SELECT {cols} FROM {src}")


def declared_columns_missing(con: duckdb.DuckDBPyConnection, table: str,
                             cols: list[str]) -> list[str]:
    """EG0-P02 — 선언 컬럼 중 그 뷰에 없는 것. stage 컬럼명 기준이다."""
    actual = {str(r[0]) for r in con.execute(f"DESCRIBE {_q(table)}").fetchall()}
    actual.discard(_PIN_AXIS_COLUMN)
    return [c for c in cols if c not in actual]


# ── `_pinned/` GC (플랜 v1 §8 Task 5.2 · v2 §4 B.3) ─────────────────────────
# 하루 2판(저녁 e_ · 아침 m_)을 짓기 시작하면 `_pinned/` 는 매일 두 판씩 늘어난다. 하드링크라
# stage 원본이 살아 있는 동안은 추가 용량이 0 이지만, stage 가 keep=3 으로 옛 판을 지운 뒤에는
# `_pinned/` 가 그 데이터의 **유일한 사본**이 되어 실체 용량을 문다(판당 ≈1.5 GB).


@dataclass(frozen=True)
class PinnedRef:
    """고정된 입력 판 하나의 주소 — `_pinned/<table>/v=<build_id>/`."""

    table: str
    build_id: str


@dataclass(frozen=True)
class GcResult:
    """`gc_pinned` 판정. 지운 것과 **남긴 이유**를 따로 싣는다 — 남은 용량을 보고 어느 축이
    잡고 있는지(참조·보호·최신 keep) 바로 가려야 하기 때문이다."""

    removed: tuple[PinnedRef, ...] = ()
    kept_referenced: tuple[PinnedRef, ...] = ()     # 현행 equity BuildRecord.inputs 가 가리킨다
    kept_protected: tuple[PinnedRef, ...] = ()      # 호출자가 보호 목록으로 넘겼다
    kept_recent: tuple[PinnedRef, ...] = ()         # 표별 최신 keep 판
    scanned_tables: tuple[str, ...] = ()
    freed_bytes: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_removed(self) -> int:
        return len(self.removed)


def equity_tables(equity_root: Path) -> list[str]:
    """`<equity_root>/` 아래의 equity 테이블 디렉토리 이름. `_`·`.` 로 시작하면 테이블이 아니다."""
    if not equity_root.exists():
        return []
    return sorted(d.name for d in equity_root.iterdir()
                  if d.is_dir() and not d.name.startswith(_NON_TABLE_PREFIXES))


def referenced_pins(equity_root: Path) -> set[PinnedRef]:
    """살아 있는 equity 빌드가 가리키는 입력 판 전부.

    `current_build` 만 보지 않는다 — MANIFEST 는 keep 개 빌드를 남기고 `equity gate --build <옛
    판>` 이 그 어느 것이든 재판정할 수 있다. 재판정은 `load_pinned` 로 `_pinned/` 를 다시 여므로
    현행 기록이 가리키는 판을 지우면 재판정 경로가 죽는다.
    """
    out: set[PinnedRef] = set()
    for table in equity_tables(equity_root):
        m = manifest.load(equity_root / table / "MANIFEST.json")
        for rec in m.builds:
            for src_table, build_id in rec.inputs.items():
                out.add(PinnedRef(str(src_table), str(build_id)))
    return out


def _pin_sort_key(build_id: str) -> tuple[str, str]:
    """판 정렬 키 — 접두어(`b_`/`e_`/`m_`) 뒤 UTC 시각 문자열. 접두어를 빼야 저녁·아침 판이
    시간순으로 섞인다. 형식이 다른 id(테스트 픽스처)는 그 문자열 자체로 정렬된다."""
    return (build_id[2:] if build_id[1:2] == "_" else build_id, build_id)


def _dir_bytes(path: Path) -> int:
    """지우면 실제로 돌아오는 바이트 — `_pinned/` 는 stage 원본에 건 하드링크라 다른 링크가 살아 있는
    파일(`st_nlink > 1`)은 지워도 디스크가 비지 않는다(검수 R2 이의 7)."""
    return sum(st.st_size for f in path.rglob("*") if f.is_file()
               for st in (f.stat(),) if st.st_nlink <= 1)


def gc_pinned(equity_root: Path, *, keep: int = manifest.KEEP_DEFAULT,
              protect: Iterable[str] = ()) -> GcResult:
    """`_pinned/` 에서 **아무도 안 가리키고 최신 keep 밖인** 판을 지운다.

    남기는 축 셋(합집합):
      ① 현행 equity BuildRecord.inputs 가 가리키는 판 (`referenced_pins`)
      ② `protect` 로 받은 build_id — 전달 규약이 가리키는 판(`data/deliver/history/*.json` 30일
         보존분 + 월말 영구분, 플랜 v2 §4 B.3 ③). 파일 형식은 파이프라인(B.1) 소유라 이 함수는
         **build_id 문자열만** 받는다
      ③ 표별 최신 `keep` 판 — 다음 빌드가 곧 다시 고정할 판을 지웠다 되고정하는 낭비 방지

    `manifest.commit()` 을 부르지 않는다 — 그 함수는 keep 밖 `v=` 를 자기 규칙으로 rmtree 해서
    보호 축 ①②를 무시한다. MANIFEST 재기록은 `_write_build_record` 와 같은 규약(임시 파일 →
    os.replace)으로 여기서 한다.
    """
    if keep < 0:
        raise ValueError(f"keep must be >= 0: got={keep}")
    root = equity_root / PINNED_DIR
    if not root.exists():
        return GcResult()
    referenced = referenced_pins(equity_root)
    protected_ids = {str(b) for b in protect}
    removed: list[PinnedRef] = []
    kept_ref: list[PinnedRef] = []
    kept_prot: list[PinnedRef] = []
    kept_recent: list[PinnedRef] = []
    tables: list[str] = []
    freed = 0
    errors: list[str] = []
    for table_dir in sorted(d for d in root.iterdir() if d.is_dir()):
        table = table_dir.name
        tables.append(table)
        versions = sorted((d for d in table_dir.iterdir()
                           if d.is_dir() and d.name.startswith("v=")),
                          key=lambda d: _pin_sort_key(d.name[2:]))
        recent = {d.name[2:] for d in versions[-keep:]} if keep else set()
        survivors: list[str] = []
        for vdir in versions:
            build_id = vdir.name[2:]
            ref = PinnedRef(table, build_id)
            if ref in referenced:
                kept_ref.append(ref)
            elif build_id in protected_ids:
                kept_prot.append(ref)
            elif build_id in recent:
                kept_recent.append(ref)
            else:
                size = _dir_bytes(vdir)
                try:
                    shutil.rmtree(vdir)
                except OSError as e:            # 권한·경합은 GC 를 멈출 사유가 아니다
                    errors.append(f"{table}/v={build_id}: {e}")
                    survivors.append(build_id)
                    continue
                removed.append(ref)
                freed += size
                continue
            survivors.append(build_id)
        _rewrite_pinned_manifest(table_dir, table, survivors)
    return GcResult(tuple(removed), tuple(kept_ref), tuple(kept_prot), tuple(kept_recent),
                    tuple(tables), freed, tuple(errors))


def _rewrite_pinned_manifest(table_dir: Path, table: str, survivors: list[str]) -> None:
    """지운 판의 BuildRecord 를 `_pinned/<table>/MANIFEST.json` 에서 뺀다.

    `current_build` 가 지워졌으면(= 아무도 안 가리키는 옛 current) 살아남은 최신 판으로 옮긴다.
    남은 판이 없으면 MANIFEST 자체를 지운다 — `current_build=None` 인 빈 기록을 남기면
    `load_pinned` 가 "판이 없다" 가 아니라 "기록이 깨졌다" 로 읽힌다.
    """
    path = table_dir / "MANIFEST.json"
    if not path.exists():
        return
    m = manifest.load(path)
    kept = [b for b in m.builds if b.build_id in set(survivors)]
    if not kept:
        path.unlink()
        return
    if len(kept) == len(m.builds) and m.current_build in {b.build_id for b in kept}:
        return                              # 바뀐 것이 없다 — 파일을 건드리지 않는다
    current = m.current_build if m.current_build in {b.build_id for b in kept} else \
        max((b.build_id for b in kept), key=_pin_sort_key)
    out = manifest.Manifest(table=table, current_build=current, keep=len(kept), builds=kept)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(out), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
