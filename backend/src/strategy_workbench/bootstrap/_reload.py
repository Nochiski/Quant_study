"""DB 새로고침의 앱 반영: 컨테이너를 통째로 새로 세우고 HTTP 앱을 바꿔 끼운다.

한 컨테이너는 부팅 때 원장 판본을 고정한다(`EquityDuckdbAdapter`). 새 판본을 읽으려면 그 판본으로 새
컨테이너를 세우는 수밖에 없다 — 같은 컨테이너 안에서 판본을 바꾸면 run 하나가 두 판을 섞는다.
`ReloadableApp` 은 uvicorn 이 띄우는 ASGI 앱이고, 요청을 지금 앱에 넘긴다. 반영은
1) 새 컨테이너·앱을 이벤트 루프 밖에서 세우고 2) 루프에서 새 앱의 lifespan 을 연 뒤 바꿔 끼우고
3) 옛 앱의 lifespan 을 닫는다(run worker 정리 등). 바꾸는 동안 도는 요청은 옛 앱에서 끝난다.
새로고침은 진행 중 계산이 없을 때만 시작하고 받는 동안 새 계산을 막으므로 옛 앱이 쥔 계산이 없다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import AsyncExitStack
from typing import Any

from fastapi import FastAPI

from strategy_workbench.application.ledger_refresh.facade.refresh import ActiveWork

from ._container import BackendContainer

logger = logging.getLogger(__name__)

# reason: ASGI 3 메시지는 공개 계약이 dict 다(키가 메시지 종류마다 다르다).
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class ContainerActiveWork:
    """`ActiveWorkPort` — 지금 컨테이너가 쥔 계산을 센다."""

    def __init__(self, container: Callable[[], BackendContainer]) -> None:
        self._container = container

    def active(self) -> ActiveWork:
        container = self._container()
        return ActiveWork(
            backtests=container.backtest_runs.active_run_count(),
            experiments=container.experiments.unfinished_count(),
            evaluations=container.continuous_accounts.running_count(),
        )


class ReloadableApp:
    """요청을 지금 앱으로 넘기는 ASGI 앱. `load` 로 첫 앱을 세우고 `apply` 로 바꿔 끼운다."""

    def __init__(self) -> None:
        self._build: Callable[[], FastAPI] | None = None
        self._app: FastAPI | None = None
        self._stack: AsyncExitStack | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._on_shutdown: Callable[[], None] | None = None

    def load(
        self, build: Callable[[], FastAPI], *, on_shutdown: Callable[[], None] | None = None
    ) -> ReloadableApp:
        """`build` 는 컨테이너를 `app.state.container` 에 실은 앱을 세운다(`build_http_app`).

        `on_shutdown` 은 서버가 끝날 때 한 번 부른다 — 반영을 넘어 사는 것(받는 중인 도구 끊기)의
        정리다.
        """
        self._build = build
        self._on_shutdown = on_shutdown
        self._app = build()
        return self

    @property
    def app(self) -> FastAPI:
        if self._app is None:
            raise RuntimeError("reloadable app is not loaded — call load(build) first")
        return self._app

    @property
    def container(self) -> BackendContainer:
        return self.app.state.container

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        await self.app(scope, receive, send)

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        await receive()  # lifespan.startup
        self._loop = asyncio.get_running_loop()
        stack = AsyncExitStack()
        try:
            await stack.enter_async_context(self.app.router.lifespan_context(self.app))
        except Exception as error:
            logger.exception("app startup failed")
            await send({"type": "lifespan.startup.failed", "message": repr(error)})
            return
        self._stack = stack
        await send({"type": "lifespan.startup.complete"})
        await receive()  # lifespan.shutdown
        self._closed = True
        if self._on_shutdown is not None:
            try:
                await asyncio.to_thread(self._on_shutdown)
            except Exception:
                logger.exception("reloadable app shutdown hook failed")
        try:
            # 반영으로 바꿔 끼웠으면 첫 앱의 스택은 이미 닫혔다 — 지금 앱의 스택을 닫는다.
            await self._stack.aclose()
        except Exception as error:
            logger.exception("app shutdown failed")
            await send({"type": "lifespan.shutdown.failed", "message": repr(error)})
            return
        await send({"type": "lifespan.shutdown.complete"})

    def apply(self) -> None:
        """`ApplyDataPort` — 새 원장으로 컨테이너를 다시 세워 바꿔 끼운다. 실패하면 옛 앱이 남는다.

        이벤트 루프 밖(새로고침 스레드)에서 부른다. lifespan 이 없는 앱(테스트 클라이언트)은 바꿔
        끼우기만 한다.
        """
        if self._build is None:
            raise RuntimeError("reloadable app is not loaded — call load(build) first")
        if self._closed:  # 서버가 끝나는 중이면 새 컨테이너를 세우지 않는다
            raise RuntimeError("server is shutting down — not reloading")
        app = self._build()
        if self._loop is None or self._stack is None:
            self._app = app
            return
        asyncio.run_coroutine_threadsafe(self._swap(app), self._loop).result()

    async def _swap(self, app: FastAPI) -> None:
        stack = AsyncExitStack()
        await stack.enter_async_context(app.router.lifespan_context(app))
        if self._closed:  # 세우는 동안 서버가 끝나기 시작했다 — 새 앱을 바로 닫는다
            await stack.aclose()
            raise RuntimeError("server is shutting down — the reloaded app was closed")
        old = self._stack
        self._app, self._stack = app, stack
        logger.info("app reloaded with new ledger data")
        if old is not None:
            try:
                await old.aclose()
            except Exception:
                logger.exception("old app shutdown failed after reload")
