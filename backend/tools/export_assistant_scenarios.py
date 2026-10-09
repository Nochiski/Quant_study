"""가짜 공급자 시나리오 SSE 프레임 골든을 다시 뽑는다 (WORKFLOW A-07).

`backend/`에서:

    uv run python tools/export_assistant_scenarios.py

대본과 프레임 변환은 `tests/assistant_scenarios.py`가 소유한다. 여기가 얇은 진입점인 이유는
시나리오를 돌리려면 가짜 공급자(`tests/application/_assistant_fakes.py`의 `ScriptedProvider`)가
필요하고, 그 가짜의 owner가 테스트 트리이기 때문이다. 같은 가짜를 `tools/` 아래에 한 벌 더 두면
대본이 두 곳에서 갈라진다.

프롬프트 골든(`export_assistant_prompts.py`)과 나누어 둔 이유도 같다. 그쪽은 테스트 트리를
전혀 import하지 않고 도는데, 한 스크립트로 합치면 프롬프트 골든 테스트가 그 스크립트를 import할
때 테스트 헬퍼까지 딸려 온다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
# `tests`는 `backend/` 아래의 패키지다. 스크립트를 직접 실행하면 sys.path[0]이 `tools/`라
# 그 패키지가 보이지 않는다.
sys.path.insert(0, str(_BACKEND_ROOT))

from tests.assistant_scenarios import (  # noqa: E402  # reason: sys.path를 먼저 세워야 import된다
    SCENARIO_DIR,
    build_scenario_frames,
)

__all__ = ["main"]


def main() -> None:
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in build_scenario_frames().items():
        (SCENARIO_DIR / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {(SCENARIO_DIR / name).relative_to(_BACKEND_ROOT)}")


if __name__ == "__main__":
    main()
