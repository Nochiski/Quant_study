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


class LegacyMissingPolicyConflictError(ValueError):
    """1.1 문서의 팩터별 결측 정책이 서로 달라 실행 설정 하나로 옮길 수 없다(P2-02).

    1.1 은 팩터 그래프마다 `missing_policy` 를 갖고 1.2 는 실행 설정에 `missing` 하나만 둔다
    (spec D3 S3). 값이 갈리는 문서를 대표값 하나로 접으면 팩터 일부가 조용히 다른 결측 처리로
    계산된다 — 조용히 틀린 값을 내느니 거부하고, 요청에 `environment` 를 명시하게 한다.
    """

    code = "run_environment.missing_policy_conflict"

    def __init__(self, by_factor: tuple[tuple[str, MissingPolicy], ...]) -> None:
        observed = ", ".join(f"{factor_id}={policy.value}" for factor_id, policy in by_factor)
        super().__init__(
            "1.1 문서의 팩터별 결측 정책이 달라 실행 설정 하나로 옮길 수 없다 — "
            f"code={self.code} factors={len(by_factor)} "
            f"expected=모든 팩터가 같은 missing_policy got={{{observed}}} "
            "— 실행 요청에 environment 를 명시하면 문서의 이 값들은 읽지 않으므로 그대로 통과한다"
        )
        self.by_factor = by_factor


def _missing_from_legacy_factors(spec: StrategySpec) -> MissingPolicy:
    """팩터별 `graph.missing_policy` 를 실행 설정의 단일 값으로 접는다.

    전부 같으면 그 값, 팩터가 없으면 모델 기본값, 서로 다르면 거부한다.
    """
    by_factor = tuple((factor.factor_id, factor.graph.missing_policy) for factor in spec.factors)
    distinct = {policy for _factor_id, policy in by_factor}
    if len(distinct) > 1:
        raise LegacyMissingPolicyConflictError(by_factor)
    return next(iter(distinct), MissingPolicy.DROP)


def environment_from_legacy_spec(spec: StrategySpec) -> RunEnvironment:
    """1.1 문서의 `data`·`execution`·`graph.missing_policy` 로 실행 설정을 만든다.

    P2-02 이후 `graph.missing_policy` 를 읽는 곳은 여기 하나다 — 평가와 플랜은 실행 설정의
    `missing` 만 읽는다. 그래서 이 브리지가 돌려주는 값이 실제로 실행을 바꾼다. 팩터별 값이
    갈리는 문서는 `LegacyMissingPolicyConflictError` 로 거부한다.

    팩터가 없는 문서(1.2 의 빈 `factors`)는 모델 기본값 `MissingPolicy.DROP` 을 쓴다.

    **호출 전에 문서가 `validate_strategy` 를 통과해 있어야 한다.** 문서 값의 진단 owner 는
    validator 이고(`strategy.execution.cost`·`strategy.data.date_order` …), `RunEnvironment`
    생성자는 명시 실행 설정의 owner 다. 검증 전에 브리지를 부르면 코드화된 진단 대신 생성자의
    raw `ValueError` 가 먼저 터져 프론트가 매핑할 코드를 잃는다.

    Raises:
        LegacyMissingPolicyConflictError: 팩터마다 `graph.missing_policy` 가 다를 때.
    """
    missing = _missing_from_legacy_factors(spec)
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

    브리지 분기를 타므로 호출자는 `environment_from_legacy_spec` 과 같은 선행 조건을 진다 —
    문서 검증이 먼저다.

    Raises:
        LegacyMissingPolicyConflictError: `environment` 가 없고 문서의 팩터별
            `graph.missing_policy` 가 서로 다를 때.
    """
    return environment if environment is not None else environment_from_legacy_spec(spec)
