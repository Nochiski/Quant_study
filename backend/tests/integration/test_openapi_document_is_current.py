"""추적되는 `backend/openapi.json`이 live 앱의 OpenAPI와 같아야 한다.

CI는 `npm run api:generate` 뒤 `git diff --exit-code`로 같은 사실을 검사하지만, backend gate만
돌리는 PR에서는 그 단계가 없어 계약 변경 뒤 재생성을 빠뜨리기 쉽다(P1-04 리뷰 P1-001).
재생성 명령은 `uv run python scripts/export_openapi.py openapi.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

from strategy_workbench.bootstrap.facade.http import build_http_app

TRACKED = Path(__file__).resolve().parents[2] / "openapi.json"


def test_tracked_openapi_matches_the_live_application() -> None:
    live = json.dumps(build_http_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True)

    assert TRACKED.read_text(encoding="utf-8") == live + "\n", (
        "backend/openapi.json is stale; regenerate with: "
        "uv run python scripts/export_openapi.py openapi.json"
    )
