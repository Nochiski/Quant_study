"""data/equity/baseline.json — 게이트 상수·회귀 고정값 (DESIGN §2 · GATES §0-3).

스키마는 stage 와 같다: `{table: {metric: value, thresholds: {...}}}` + `_measured[]`
(항목마다 SQL 을 실어 재현 가능하게). 원자 교체는 `stage.baseline.write` 를 그대로 쓴다.
차이는 측정면뿐이다 — 원장 SQLite 가 아니라 `_pinned/` stage parquet + 커밋된 equity 산출.

게이트는 숫자를 코드에 쓰지 않는다. 없는 상수는 `None` 을 돌려주고 호출부가
`skip(no_baseline)` 으로 기록한다(GATES §0-3).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from stage.baseline import write  # noqa: F401  # reason: 원자 교체 규약 재사용 (SoT 중복 금지)

FILENAME = "baseline.json"


@dataclass(frozen=True)
class Baseline:
    """baseline.json 한 판. `data` 는 파일 전체(stage 스키마 그대로)."""

    data: dict[str, object] = field(default_factory=dict)
    path: Path | None = None

    def table(self, table: str) -> dict[str, object]:
        v = self.data.get(table)
        return v if isinstance(v, dict) else {}

    def get(self, table: str, metric: str) -> object | None:
        """`{table}.{metric}` 상수. 미등재는 None — 호출부가 skip(no_baseline) 로 처리한다.

        `threshold_<GATE>` 는 stage 와 같이 `{table: {thresholds: {GATE: v}}}` 에도 산다.
        """
        t = self.table(table)
        if metric in t:
            return t[metric]
        if metric.startswith("threshold_"):
            th = t.get("thresholds")
            if isinstance(th, dict):
                return th.get(metric[len("threshold_"):])
        return None

    def require(self, table: str, metric: str) -> object:
        v = self.get(table, metric)
        if v is None:
            raise KeyError(f"baseline constant not found — table={table} metric={metric} "
                           f"path={self.path} (등재는 사람 승인: GATES §7-3)")
        return v


def path_for(equity_root: Path) -> Path:
    return equity_root / FILENAME


def load(path: Path) -> Baseline:
    """없으면 빈 Baseline. 첫 빌드는 전 상수가 미등재이고 게이트가 skip(no_baseline) 를 낸다."""
    if not path.exists():
        return Baseline({}, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"baseline.json root must be an object: path={path} "
                         f"got={type(raw).__name__}")
    return Baseline(raw, path)
