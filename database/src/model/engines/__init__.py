"""모델 엔진 — `contracts.ALL_ENGINES` 이름 → `contracts.Engine` 구현.

이식이 끝난 엔진만 싣는다(v2_percentrank·v4_rank 는 W1-d·v4 갈래가 더한다).
공용 순수 함수는 `_common`.
"""
from __future__ import annotations

from model.contracts import Engine
from model.engines import v3_zscore

ENGINES: dict[str, Engine] = {
    v3_zscore.ENGINE.name: v3_zscore.ENGINE,
}
