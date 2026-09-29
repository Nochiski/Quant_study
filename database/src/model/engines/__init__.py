"""모델 엔진 — `contracts.ALL_ENGINES` 이름 → `contracts.Engine` 구현.

v3·v2 는 v3 원본 이식(골든 일치), v4_rank 는 새 모델(D-13', 실험판 @0.1·@0.2).
공용 순수 함수는 `_common`.
"""
from __future__ import annotations

from model.contracts import Engine
from model.engines import v2_percentrank, v3_zscore, v4_rank

ENGINES: dict[str, Engine] = {
    v3_zscore.ENGINE.name: v3_zscore.ENGINE,
    v2_percentrank.ENGINE.name: v2_percentrank.ENGINE,
    v4_rank.ENGINE.name: v4_rank.ENGINE,
}
