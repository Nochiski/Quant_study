"""equity 층 부분 커밋 되돌리기 (DEFECT-C03, 2026-09-19 감사).

`equity_rebuild_all.sh` 는 표를 하나씩 짓고 성공할 때마다 `manifest.commit()` 으로 그 표의
`current_build` 를 **즉시** 바꾼다. 층 전체 트랜잭션이 없으므로 중간에 실패하면 앞선 표는 새 판,
뒤의 표는 어제 판으로 남는다. `inputs.py` 와 `build_chain.sh` 는 "MANIFEST 포인터가 정본" 을
계약으로 못박았으니 그 상태는 곧 **표마다 다른 날의 판**을 소비자에게 내보내는 것이다. 실측:
09-11 확정 빌드가 14표 커밋 뒤 `credit_daily` 에서 멈췄고 혼합 판본이 09-13 03:35 ~ 09-16 15:14
약 3.5일 유지됐다(그 사이 catalog 도 안 돌아 `equity.duckdb` 매크로는 옛 `v=` 를 가리켰다).

되돌리기의 범위는 **포인터뿐**이다.
  · `MANIFEST.current_build` 를 직전 판으로 옮긴다 — `builds[]` 목록과 `v=` 디렉터리는 그대로
    둔다. keep=10(≈5거래일) 이라 이전 판은 반드시 살아 있고, 지우면 그 판으로 다시 못 돌아간다.
  · 이번 판을 커밋하지 못한 표(= rc≠0)는 **건드리지 않는다**. 그 표의 current 는 이미 어제 판이라
    한 칸 더 되돌리면 멀쩡한 판을 잃는다.

`stage.manifest` 는 갈래 2 소유라 여기서는 읽기·원자쓰기 유틸(`load`·`_write_atomic`)만 쓴다.
"""
from __future__ import annotations

from pathlib import Path

from stage import manifest

MANIFEST_NAME = "MANIFEST.json"
LOG_ROOT_DEFAULT = Path("logs/equity")
SUMMARY_NAME = "summary.tsv"


def rollback_table(table_root: Path, to_build_id: str | None = None) -> str | None:
    """한 표의 `current_build` 를 되돌린다. 반환값은 되돌아간 판(안 되돌렸으면 None).

    `to_build_id` 가 없으면 `builds[]` 에서 현재 판 **바로 앞** 판으로 간다.
    """
    path = table_root / MANIFEST_NAME
    if not path.exists():
        return None
    m = manifest.load(path)
    ids = [b.build_id for b in m.builds]
    if to_build_id is not None:
        if to_build_id not in ids:
            raise ValueError(f"rollback target not in MANIFEST.builds: table={m.table} "
                             f"target={to_build_id} builds={ids}")
        target = to_build_id
    else:
        if m.current_build is None or m.current_build not in ids:
            return None
        i = ids.index(m.current_build)
        if i == 0:
            return None                      # 되돌아갈 이전 판이 없다
        target = ids[i - 1]
    if target == m.current_build:
        return None
    m.current_build = target
    manifest._write_atomic(path, m)          # noqa: SLF001  # reason: 원자 교체 규약 재사용
    return target


def _rc_by_table(summary: Path) -> list[tuple[str, int]]:
    """`summary.tsv`(표\trc\t초\t해시) 를 순서대로 읽는다."""
    out: list[tuple[str, int]] = []
    for line in summary.read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) >= 2 and cols[0]:
            out.append((cols[0], int(cols[1])))
    return out


def rollback_pass(equity_root: Path, pass_name: str, *,
                  log_root: Path | None = None) -> dict[str, str]:
    """`logs/equity/rebuild_<PASS>/summary.tsv` 의 **rc 0 표만** 직전 판으로 되돌린다.

    반환값은 `{표: 되돌아간 build_id}` — 되돌릴 것이 없던 표는 빠진다.
    """
    root = log_root if log_root is not None else LOG_ROOT_DEFAULT
    summary = root / f"rebuild_{pass_name}" / SUMMARY_NAME
    if not summary.exists():
        raise FileNotFoundError(f"rebuild summary not found: {summary} "
                                f"(pass={pass_name} — 되돌릴 표 목록을 알 수 없다)")
    out: dict[str, str] = {}
    for table, rc in _rc_by_table(summary):
        if rc != 0:
            continue                          # 커밋 자체가 없다 — 되돌리면 어제 판을 잃는다
        prev = rollback_table(equity_root / table)
        if prev is not None:
            out[table] = prev
    return out


__all__ = ["rollback_pass", "rollback_table"]
