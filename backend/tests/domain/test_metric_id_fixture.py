"""지표 id 골든이 Metric Registry와 같은지 본다 (결과 설명 spec R4).

지표의 쉬운 한글 이름·뜻은 frontend i18n이 `metric.<metric_id>`를 키로 소유한다. frontend 테스트는
backend를 부르지 못하므로 이 골든(`tests/fixtures/analytics/metric_ids.json`)을 읽어 id마다 ko·en
문구가 있는지 본다. 이 테스트는 골든이 registry와 같은지를 지킨다. 지표를 더하면 여기서 먼저 깨지고,
골든을 고치면 frontend 테스트가 문구를 쓰라고 깨진다 — 두 단계가 이어져야 새 지표가 뜻 없이 화면에
나가지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "analytics" / "metric_ids.json"


def test_the_metric_id_golden_lists_every_registry_metric_in_order() -> None:
    registry = build_default_metric_registry()
    expected = [definition.metric_id for definition in registry.definitions()]

    stored = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    assert stored == expected, (
        f"{_FIXTURE.name} is stale; write the registry ids in registry order: {expected}"
    )
