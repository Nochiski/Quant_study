"""schema 1.1 문서에서 실행 설정을 만드는 브리지(spec D6 전환 규칙, P2-01).

브리지는 `RunEnvironment` 의 owner 인 `domain/backtest` 가 소유한다. `application/backtest_run`
에 두면 `portfolio_design → backtest_run` 화살표가 생겨 기존 `backtest_run → portfolio_design`
과 순환이 되고, 경계 규칙은 application → application 을 다른 유스케이스의 outgoing port 소비
에만 허용한다(브리지는 port 가 아니라 로직이다).
"""

from __future__ import annotations

from strategy_workbench.domain.factor.facade.expression import MissingPolicy
from strategy_workbench.domain.strategy.facade.specification import StrategySpec

from ._models import RunEnvironment


def environment_from_legacy_spec(spec: StrategySpec) -> RunEnvironment:
    """1.1 문서의 `data`·`execution`·첫 팩터 `graph.missing_policy` 로 실행 설정을 만든다.

    결측 정책만 1:1 이 아니다 — 1.1 은 팩터 그래프마다 따로 갖고 1.2 는 실행 설정에 하나만
    둔다(spec D3 S3). 팩터별로 값이 다른 문서는 첫 팩터의 값을 대표로 쓴다. P2-01 시점에는
    평가·플랜이 여전히 `graph.missing_policy` 를 팩터별로 읽으므로 이 대표값이 실행을 바꾸지
    않는다. 값을 실제로 소비하는 것은 `graph.missing_policy` 를 지우는 P2-02 이고, 그때
    브리지는 업그레이드 입력 전용으로 좁아진다.

    팩터가 없는 문서(1.2 의 빈 `factors`)는 모델 기본값 `MissingPolicy.DROP` 을 쓴다.
    """
    factors = spec.factors
    missing = factors[0].graph.missing_policy if factors else MissingPolicy.DROP
    return RunEnvironment(
        market=spec.data.market,
        frequency=spec.data.frequency,
        start=spec.data.start,
        end=spec.data.end,
        universe_id=spec.data.universe_id,
        timing=spec.execution.timing,
        participation_rate=spec.execution.participation_rate,
        fee_bps=spec.execution.fee_bps,
        slippage_bps=spec.execution.slippage_bps,
        missing=missing,
    )


def resolve_environment(spec: StrategySpec, environment: RunEnvironment | None) -> RunEnvironment:
    """실행 설정 우선순위의 단일 정본: 명시한 값이 있으면 그것, 없으면 브리지.

    preview·trace·run 세 요청이 같은 규칙을 쓰도록 한 곳에 둔다. P2-03 에서 `environment` 가
    필수가 되면 `None` 분기가 사라진다.
    """
    return environment if environment is not None else environment_from_legacy_spec(spec)
