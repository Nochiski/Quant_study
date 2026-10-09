"""접수한 실행 요청(`BacktestRunSpec`) ↔ 저장 JSON.

전략 spec 은 두 번째 DTO 를 만들지 않고 domain 의 canonical JSON 과 hydrate 로 옮긴다
(`strategy_sqlite/_record_codec.py` 선례). canonical 표기에는 저장 정체성(`strategy_id`·
`revision`)이 빠지므로 옆에 따로 적는다. 나머지 칸(실행 설정·지표 창·저장 리비전 참조)은
HTTP 가 요청 본문을 읽을 때와 같은 dataclass 검증(pydantic `TypeAdapter`)으로 되읽어
`__post_init__` 불변식을 다시 탄다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from pydantic import TypeAdapter

from strategy_workbench.domain.backtest.facade.runs import BacktestRunSpec, InlineDraft
from strategy_workbench.domain.strategy.facade.document import hydrate_strategy_document
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
    canonical_strategy_json,
)

from ._errors import ResearchStorageError

_RUN_SPEC = TypeAdapter(BacktestRunSpec)


def encode_request(request: BacktestRunSpec) -> str:
    source = request.strategy_source
    draft = source if isinstance(source, InlineDraft) else None
    stripped = replace(request, strategy=None, strategy_source=None if draft else source)
    payload = {
        "run_spec": _RUN_SPEC.dump_python(stripped, mode="json"),
        "strategy": _encode_spec(request.strategy),
        "inline_draft": (
            None
            if draft is None
            else {"spec": _encode_spec(draft.spec), "source_hash": draft.source_hash}
        ),
    }
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)


def decode_request(text: str, *, run_id: str) -> BacktestRunSpec:
    try:
        payload = json.loads(text)
        request = _RUN_SPEC.validate_python(payload["run_spec"])
        draft = payload["inline_draft"]
        if draft is not None:
            request = replace(
                request,
                strategy_source=InlineDraft(
                    spec=_decode_spec(draft["spec"]),
                    kind="inline_draft",
                    source_hash=draft["source_hash"],
                ),
            )
        strategy = payload["strategy"]
        return replace(request, strategy=None if strategy is None else _decode_spec(strategy))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        # pydantic `ValidationError` 도 `ValueError` 다.
        raise ResearchStorageError(
            f"stored backtest request failed integrity validation — run_id={run_id}: {error}"
        ) from error


def _encode_spec(spec: StrategySpec | None) -> dict[str, Any] | None:
    if spec is None:
        return None
    return {
        "strategy_id": spec.identity.strategy_id,
        "revision": spec.identity.revision,
        "canonical": canonical_strategy_json(spec),
    }


def _decode_spec(item: dict[str, Any]) -> StrategySpec:
    canonical = item["canonical"]
    hydration = hydrate_strategy_document(
        json.loads(canonical),
        identity=StrategyIdentity(item["strategy_id"], item["revision"]),
    )
    if not hydration.ok or hydration.spec is None:
        detail = ", ".join(f"{issue.code}@{issue.pointer}" for issue in hydration.issues[:5])
        raise ValueError(f"canonical strategy payload cannot hydrate — {detail}")
    if canonical_strategy_json(hydration.spec) != canonical:
        raise ValueError("stored strategy JSON is not canonical for its hydrated StrategySpec")
    return hydration.spec
