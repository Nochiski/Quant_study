"""인계 이력(`data/deliver/history/<D>_<basis>.json`) 읽기 — 그날 체인이 쓴 판 id 로 입력을
고정한다.

파일은 `scripts/build_chain.sh` deliver_step 이 쓴다:
  {"date", "basis", "stage_builds": {표: build_id}, "equity_builds": {표: build_id},
   "health": {"stage": "ok"|"fail", "equity": "ok"|"fail"}, …}
stage·equity 가 실패한 날에도 이 파일은 남는다 — 그때 판 목록은 그 시점 current 라 직전 판이
섞여 있다. 그래서 판 목록을 믿어도 되는지는 `health_ok` 로 본다(읽는 쪽이 정한다).

쓰는 곳: compat `--builds-from`(과거 날짜 비교) · factor_inputs `--builds-from`(장 마감 판이
직전 거래일 확정판을 날짜로 고정, 컷오버 T-2). 판 id → 파일 해석은 `equity.inputs.resolve`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_OK = "ok"


class HandoffError(ValueError):
    """인계 이력을 읽을 수 없거나 판 목록이 없다."""


@dataclass(frozen=True)
class Handoff:
    path: Path
    equity_builds: dict[str, str]
    stage_builds: dict[str, str]
    health: dict[str, str]          # 키가 없거나 객체가 아니면 빈 dict(= ok 아님)
    date: str | None = None         # 대상 거래일 D(YYYYMMDD) — 없으면 None, 검사는 읽는 쪽이 한다
    basis: str | None = None        # 'morning'(아침 확정판) · 'evening' — 없으면 None

    @property
    def health_ok(self) -> bool:
        """그날 stage·equity 가 둘 다 ok — `daily_build.sh` 확정판 완료 판정과 같은 기준."""
        return self.health.get("stage") == _OK and self.health.get("equity") == _OK


def load(path: Path) -> Handoff:
    """인계 이력 JSON 을 읽는다. 파일이 없거나 깨졌거나 판 목록이 비면 `HandoffError`."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise HandoffError(f"인계 이력(--builds-from)이 없다: {path} — 그날 체인이 인계 파일을 "
                           "쓰지 않았다") from e
    except (OSError, ValueError) as e:
        raise HandoffError(f"인계 이력(--builds-from)을 읽지 못했다: {path} ({e})") from e
    if not isinstance(raw, dict):
        raise HandoffError(f"인계 이력(--builds-from)이 객체가 아니다: {path}")
    builds: dict[str, dict[str, str]] = {}
    for key in ("equity_builds", "stage_builds"):
        got = raw.get(key)
        if not isinstance(got, dict) or not got:
            raise HandoffError(f"인계 이력(--builds-from)에 {key} 가 없다: {path}")
        builds[key] = {str(k): str(v) for k, v in got.items()}
    health = raw.get("health")
    date, basis = raw.get("date"), raw.get("basis")
    return Handoff(path=Path(path), equity_builds=builds["equity_builds"],
                   stage_builds=builds["stage_builds"],
                   health={str(k): str(v) for k, v in health.items()}
                   if isinstance(health, dict) else {},
                   date=None if date is None else str(date),
                   basis=None if basis is None else str(basis))
