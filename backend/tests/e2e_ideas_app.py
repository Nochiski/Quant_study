"""그래프 예제용 100종목 앱을 기존 3종목 E2E 앱과 격리해 조립한다.

운영 팩토리·원문·계산 규칙은 바꾸지 않는다. 예제는 2021 합성 구간을 사용한다.
기존 mock의 월초 구성원 탈락은 이 fixture의 전 기간 membership으로 대체한다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI

from strategy_workbench.adapters.outbound.equity_mock._fixture import (
    Membership,
    build_demo_fixture,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.bootstrap import _container
from strategy_workbench.bootstrap.facade.http import (
    build_http_app,
    build_runtime_http_app,
    runtime_allowed_origins,
    runtime_assistant_settings,
)
from strategy_workbench.domain.equity.facade.research_data import SecurityRef, canonical_revision


class IdeasEquityDataAdapter(MockEquityDataAdapter):
    """100종목 구성원과 기존 합성 원시값·bar 생성기를 사용하는 테스트 데이터."""

    def _member(self, membership: Membership, security_index: int, session: date) -> bool:
        return membership.first_session <= session <= membership.last_session


def ideas_adapter() -> IdeasEquityDataAdapter:
    fixture = build_demo_fixture()
    securities = tuple(item.security for item in fixture.memberships) + tuple(
        SecurityRef(f"sec-{900000 + index}-1", str(900000 + index), f"예제 종목 {index}", "XKRX")
        for index in range(4, 101)
    )
    memberships = tuple(
        Membership(security, date(2000, 1, 3), date(2030, 12, 31)) for security in securities
    )
    # fixture.sessions는 유지한다. 이를 합성 구간으로 늘리면 존재하지 않는 관측을 읽게 된다.
    snapshot = replace(
        fixture.snapshot,
        snapshot_id="mock-ideas-"
        + canonical_revision((fixture.snapshot, memberships, "membership-v1")),
        source="graph-ideas-synthetic-fixture",
    )
    return IdeasEquityDataAdapter(replace(fixture, snapshot=snapshot, memberships=memberships))


def build_e2e_http_app() -> FastAPI:
    """run-playwright의 소유 런타임에서만 사용하는 서버 팩토리."""
    runtime = Path(os.environ["STRATEGY_WORKBENCH_E2E_RUNTIME_DIR"])
    prefix = os.environ["STRATEGY_WORKBENCH_E2E_IDEAS_PREFIX"]
    if not runtime.is_absolute() or not prefix.startswith("/") or prefix == "/":
        raise ValueError("격리 E2E 런타임과 전용 예제 경로가 필요합니다")
    root = build_runtime_http_app()
    own = runtime / "ideas"
    own.mkdir(parents=True, exist_ok=True)
    assistant = replace(
        runtime_assistant_settings(),
        db_path=own / "assistant.sqlite3",
        secrets_path=own / "assistant-secrets.json",
    )
    # 조립 중에만 주입한다. 요청 처리 중 전역 patch나 adapter 교환은 없다.
    with (
        patch.object(MockEquityDataAdapter, "demo", return_value=ideas_adapter()),
        patch.object(_container, "DEFAULT_RUN_ARTIFACT_ROOT", own / "backtest-runs"),
    ):
        ideas = build_http_app(
            strategy_repository_path=own / "strategy.sqlite3",
            research_db_path=own / "research.sqlite3",
            assistant=assistant,
            allowed_origins=runtime_allowed_origins(),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(root.router.lifespan_context(root))
            await stack.enter_async_context(ideas.router.lifespan_context(ideas))
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount(prefix, ideas)
    app.mount("/", root)
    return app
