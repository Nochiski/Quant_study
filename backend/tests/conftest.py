from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot
from strategy_workbench.adapters.outbound.secrets_local.facade.store import default_secrets_path
from strategy_workbench.bootstrap import _assistant, _container, _http

from .runtime_path_guard import RuntimePathGuard

# 테스트가 열면 안 되는 개발자 로컬 런타임 상태의 위치(#211). 아래 격리 fixture가 기본값을
# tmp로 바꾸기 전에, 실제 기본값에서 계산한다.
RUNTIME_PATH_GUARD = RuntimePathGuard(
    (
        _http.DEFAULT_STRATEGY_REPOSITORY_PATH.parent,
        _http.DEFAULT_ASSISTANT_DB_PATH.parent,
        _http.DEFAULT_RESEARCH_DB_PATH.parent,
        _container.DEFAULT_RUN_ARTIFACT_ROOT,
        default_secrets_path().parent,
    )
)
sys.addaudithook(RUNTIME_PATH_GUARD)

# composition root(`_http.py`)가 읽는 런타임 설정 환경 변수 전부. 개발자 셸에 남은 dev 서버
# 값(실제 DB·비밀 경로, `EQUITY_ADAPTER=duckdb`와 원장 root, 가짜 공급자, CORS origin)이 테스트를
# 실제 파일로 보내거나 결과를 개발자마다 다르게 만들지 않게 매 테스트에서 지운다. 값이 필요한
# 테스트는 `monkeypatch.setenv`로 직접 준다. `*_ENV` 상수에서 유도하므로 새 변수도 따라온다
# (#215 리뷰 P3-2).
# 실키 스모크 opt-in(`STRATEGY_WORKBENCH_LIVE_SMOKE`)과 e2e 시드 CLI 변수는 런타임 설정이 아니라서
# 대상이 아니다.
RUNTIME_SETTING_ENVS = tuple(
    sorted(
        value
        for name, value in vars(_http).items()
        if name.endswith("_ENV")
        and isinstance(value, str)
        and value.startswith("STRATEGY_WORKBENCH_")
    )
)


def _redirect_runtime_defaults(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    for name in RUNTIME_SETTING_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        _http, "DEFAULT_STRATEGY_REPOSITORY_PATH", root / "strategy-revisions.sqlite3"
    )
    monkeypatch.setattr(_http, "DEFAULT_ASSISTANT_DB_PATH", root / "assistant.sqlite3")
    monkeypatch.setattr(_http, "DEFAULT_RESEARCH_DB_PATH", root / "research.sqlite3")
    monkeypatch.setattr(_container, "DEFAULT_RUN_ARTIFACT_ROOT", root / "backtest-runs")
    monkeypatch.setattr(_assistant, "default_secrets_path", lambda: root / "secrets.json")


def _fail_if_touched(when: str) -> None:
    touched = RUNTIME_PATH_GUARD.drain()
    if touched:
        pytest.fail(
            f"{when} touched the developer's runtime state paths; isolate them under tmp "
            "(see isolate_runtime_state_paths) -- "
            f"watched={list(RUNTIME_PATH_GUARD.roots)} touched={sorted(set(touched))}",
            pytrace=False,
        )


@pytest.fixture(scope="session", autouse=True)
def _isolate_runtime_state_paths_for_the_session(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    """module·session 범위 fixture가 만드는 앱도 tmp를 쓰게 한다(#211).

    function 범위 격리는 그보다 먼저 세워지는 상위 범위 fixture(예: 시나리오 프레임을 한 번 만드는
    module fixture)를 덮지 못한다. 세션 끝에 남은 이벤트는 상위 범위 fixture의 teardown 몫이다.
    """
    with pytest.MonkeyPatch.context() as monkeypatch:
        root = tmp_path_factory.mktemp("runtime-state-session")
        _redirect_runtime_defaults(monkeypatch, root)
        yield root
    _fail_if_touched("a session or module fixture teardown")


@pytest.fixture(autouse=True)
def isolate_runtime_state_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """런타임 앱이 기본값으로 여는 경로를 테스트마다 새 tmp 디렉터리로 옮긴다(#211).

    `build_runtime_http_app`은 환경 변수가 없으면 `backend/.local/`의 strategy·assistant·research
    DB를 연다. 기본 `build_container`는 `.local/backtest-runs`를 만든다. 비밀 경로가 없는 어시스턴트
    설정은 OS 사용자 설정 디렉터리의 비밀 파일을 연다. 테스트가 이것을 열면 개발자의 실제 대화 이력
    DB가 업그레이드되어, 브랜치를 바꾼 뒤 옛 코드의 테스트가 깨진다. 기본값 자체를 tmp로 바꾸므로
    기본값을 단언하는 테스트는 그대로 통과하고, 값을 명시한 테스트는 영향을 받지 않는다. 경로가
    필요한 테스트는 이 fixture 값을 받아 쓴다. 런타임 설정 환경 변수(`RUNTIME_SETTING_ENVS`)도
    모두 지운다.
    """
    root = tmp_path_factory.mktemp("runtime-state")
    _redirect_runtime_defaults(monkeypatch, root)
    return root


@pytest.fixture(autouse=True)
def _forbid_runtime_state_paths() -> Iterator[None]:
    """런타임 상태 경로를 열거나 만든 쪽을 그 테스트의 오류로 드러낸다(#211).

    setup 시점에 이미 쌓인 이벤트는 이 테스트보다 먼저 세워진 상위 범위 fixture나 앞 테스트 뒤의
    정리가 낸 것이다. 버리지 않고 이 테스트의 setup 오류로 올린다.
    """
    _fail_if_touched("a fixture set up before this test")
    yield
    _fail_if_touched("this test")


def make_instrument(symbol: str = "005930") -> InstrumentId:
    return InstrumentId(venue="XKRX", symbol=symbol, asset_class=AssetClass.EQUITY, currency="KRW")


def make_bar(
    ts: datetime,
    instrument: InstrumentId,
    open_price: float,
    close_price: float,
    volume: int = 1_000,
) -> Bar:
    return Bar(
        ts=ts,
        instrument=instrument,
        open=open_price,
        high=max(open_price, close_price),
        low=min(open_price, close_price),
        close=close_price,
        volume=volume,
    )


def make_ohlc(
    ts: datetime,
    instrument: InstrumentId,
    open_price: float,
    high: float,
    low: float,
    close_price: float,
    volume: int = 1_000,
) -> Bar:
    return Bar(
        ts=ts,
        instrument=instrument,
        open=open_price,
        high=high,
        low=low,
        close=close_price,
        volume=volume,
    )


def make_snapshot(ts: datetime, *bars: Bar) -> MarketSnapshot:
    return MarketSnapshot(ts=ts, bars=bars)


def day(day_of_month: int) -> datetime:
    return datetime(2026, 8, day_of_month)
