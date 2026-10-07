"""equity 층 부분 커밋 되돌리기 (DEFECT-C03, 2026-09-19 감사).

`equity_rebuild_all.sh` 는 표를 하나씩 짓고 성공할 때마다 `manifest.commit()` 으로 그 표의
`current_build` 를 **즉시** 바꾼다. 층 전체 트랜잭션이 없으므로 중간에 실패하면 앞선 표는 새 판,
뒤의 표는 어제 판으로 남는다. `inputs.py` 와 `build_chain.sh` 는 "MANIFEST 포인터가 정본" 을
계약으로 못박았으니 그 상태는 곧 **표마다 다른 날의 판**을 소비자에게 내보내는 것이다. 실측:
09-11 확정 빌드가 14표 커밋 뒤 `credit_daily` 에서 멈췄고 혼합 판본이 09-13 03:35 ~ 09-16 15:14
약 3.5일 유지됐다(그 사이 catalog 도 안 돌아 `equity.duckdb` 매크로는 옛 `v=` 를 가리켰다).

되돌리기의 범위는 **포인터뿐**이다.
  · `MANIFEST.current_build` 를 패스 시작 때 고른 판(`before.json`, 없으면 직전 판)으로 옮긴다 —
    `builds[]` 목록과 `v=` 디렉터리는 그대로 둔다. keep=10(≈5거래일) 이라 이전 판은 반드시 살아
    있고, 지우면 그 판으로 다시 못 돌아간다.
  · 저녁·수동 패스: 이번 판을 커밋하지 못한 표(= rc≠0·미도달)는 **건드리지 않는다**. 그 표의
    current 는 이미 시작 판이라 한 칸 더 되돌리면 멀쩡한 판을 잃는다.
  · 아침 패스(C-01 · N-25 Q4): 목표는 마지막으로 **완료된** 아침 확정판(`latest_morning.json`)이고,
    before 의 **모든 표**(커밋 못 한 표·미도달 표 포함)를 그 판으로 옮긴다 — rc 0 표만 옮기면
    앞 표는 확정판, 뒤 표는 시작 때의 저녁 잠정판으로 섞인다(DEFECT-C03 재발). 포인터만 옮기므로
    판을 잃지 않는다.

`stage.manifest` 는 갈래 2 소유라 여기서는 읽기·원자쓰기 유틸(`load`·`_write_atomic`)만 쓴다.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path

from stage import manifest
from stage import model as stage_model

MANIFEST_NAME = "MANIFEST.json"
BASIS_MORNING = "morning"                 # 아침 확정판 — stage.model.BASIS_PREFIX 의 키(`m_`)
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
                  log_root: Path | None = None, before: Path | None = None,
                  basis: str = stage_model.BASIS_MANUAL) -> dict[str, str]:
    """실패한 패스의 포인터를 되돌린다. 반환값은 `{표: 되돌아간 build_id}`.

    `before`(패스 시작 시점에 `pass_start_targets` 가 고른 `{표: build_id}` JSON)가 있으면
    **그 판**으로, 없으면 직전 판으로.
      · 저녁·수동 패스: `logs/equity/rebuild_<PASS>/summary.tsv` 의 **rc 0 표만**.
      · 아침 패스(`basis='morning'`, C-01): rc 0 표 + before 의 **모든 표** — 목표는 마지막으로
        완료된 아침 확정판이라 커밋 못 한 표·미도달 표도 그 판으로 옮겨야 전 표가 한 판이 된다.
        아침 확정 빌드가 중간에 실패하면 "직전 판" 은 대개 전날 저녁 잠정판(`e_`)이라 MANIFEST 를
        직접 읽는 공유 소비자가 확정 자리에서 잠정판을 보게 된다(리뷰 REC-13).
    목표 판이 그새 GC 됐으면 이번 패스가 커밋한 표는 직전 판으로 폴백하고, 커밋 안 한 표는
    그대로 둔다.
    """
    targets: dict[str, str] = {}
    if before is not None:
        raw = json.loads(Path(before).read_text(encoding="utf-8"))
        targets = {str(k): str(v) for k, v in raw.items() if v}
    root = log_root if log_root is not None else LOG_ROOT_DEFAULT
    summary = root / f"rebuild_{pass_name}" / SUMMARY_NAME
    if not summary.exists():
        raise FileNotFoundError(f"rebuild summary not found: {summary} "
                                f"(pass={pass_name} — 되돌릴 표 목록을 알 수 없다)")
    committed = [t for t, rc in _rc_by_table(summary) if rc == 0]
    # 저녁·수동은 커밋한 표만(커밋 안 한 표를 되돌리면 시작 판을 잃는다), 아침은 before 의 모든 표도
    tables = (list(dict.fromkeys([*committed, *targets])) if basis == BASIS_MORNING
              else committed)
    out: dict[str, str] = {}
    for table in tables:
        target = targets.get(table)
        try:
            prev = rollback_table(equity_root / table, to_build_id=target)
        except ValueError:                    # 목표 판이 GC 됨
            prev = rollback_table(equity_root / table) if table in committed else None
        if prev is not None:
            out[table] = prev
    return out


def pass_start_targets(equity_root: Path, tables: Iterable[str], basis: str,
                       latest_morning: Path | None = None) -> dict[str, str]:
    """패스 시작 시점에 표마다 실패하면 돌아갈 판 `{표: build_id}` — `equity_rebuild_all.sh` 가
    `before.json` 으로 남기고 `rollback_pass(before=…)` 가 읽는다. MANIFEST 가 없거나 판이 없는 표는
    싣지 않는다(→ `rollback_pass` 의 직전 판 폴백).

    `basis` 는 이번 패스의 빌드 판(`--basis` 값, 생략이면 manual).
      · 저녁(evening)·수동(manual): 시작 시점의 `current_build`.
      · 아침(morning, C-01 · N-25 Q4 '직전 확정판' = 마지막으로 **완료된** 아침 판, 전 표 일관):
        `latest_morning`(build_chain deliver_step 이 stage·equity 가 다 ok 인 아침 판에서만 쓰는
        인계 포인터, **필수** — 경로는 `equity_rebuild_all.sh` 한 곳에 둔다)의 `equity_builds[표]`.
        실패한 아침 패스가 남긴 `m_` 는 이 파일에 안 오르므로 다음 날에도 고르지 않는다.
        **전부 아니면 전무**: MANIFEST 가 있는 표 중 하나라도 그 판을 못 얻으면(파일 없음·
        못 읽음·표 없음·그 판이 builds[] 에 없음 = GC) 전 표를 시작 `current_build`(옛 동작)로
        두고 stderr 에 이유 한 줄 — 표별로 폴백하면 확정판과 잠정판이 다시 섞인다.

    Raises:
        ValueError: 아침 패스인데 `latest_morning` 을 주지 않았다.
    """
    if basis == BASIS_MORNING and latest_morning is None:
        raise ValueError(f"pass_start_targets: basis=morning needs latest_morning path "
                         f"(equity_root={equity_root})")
    current: dict[str, str] = {}
    ids: dict[str, set[str]] = {}
    for table in tables:
        path = equity_root / table / MANIFEST_NAME
        if not path.exists():
            continue
        m = manifest.load(path)
        if not m.current_build:
            continue
        current[table] = m.current_build
        ids[table] = {b.build_id for b in m.builds}
    if basis != BASIS_MORNING or latest_morning is None:
        return current
    try:
        raw = json.loads(latest_morning.read_text(encoding="utf-8")).get("equity_builds")
    except (OSError, ValueError, AttributeError) as e:
        raw, why = None, f"{type(e).__name__}: {e}"
    else:
        why = "equity_builds 가 객체가 아니다"
    if not isinstance(raw, dict):
        print(f"!!! 아침 롤백 목표: {latest_morning} 를 못 읽어 전 표 시작 current 로 ({why})",
              file=sys.stderr)
        return current
    latest = {str(k): str(v) for k, v in raw.items() if v}
    missing = sorted(t for t in current if t not in latest)
    gone = sorted(t for t in current if t in latest and latest[t] not in ids[t])
    if missing or gone:
        print(f"!!! 아침 롤백 목표: {latest_morning} 판을 못 얻는 표가 있어 "
              f"전 표 시작 current 로 — "
              f"latest 에 없음 {missing} · GC(builds 에 없음) {gone}", file=sys.stderr)
        return current
    return {t: latest[t] for t in current}


__all__ = ["pass_start_targets", "rollback_pass", "rollback_table"]
