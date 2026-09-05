#!/usr/bin/env python3
"""MVP-B 백테스트 1회 — equity_root 위에서 워크벤치 파이프라인으로 `price.momentum_12_1` 월간
롱온리.

EQUITY_WORKFLOW §3-5(S21 축소): `build_container(equity_adapter="duckdb", equity_root=…)` 로 부팅한
워크벤치의 `BacktestRunService`(raw 관측 → 팩터 그래프 평가 → 월간 리밸런싱 TargetTape → 엔진
커널)를 실제로 한 번 돌리고 요약을 찍는다. 팩터 그래프는 레지스트리 `price.momentum_12_1`(252세션
모멘텀, 21세션 skip, 횡단면 rank)을 그대로 쓰되 가격 필드만 `--price-field` 로 바꾼다 — 기본
`price.adj_close`(결정 6: 레지스트리는 아직 `price.close` 를 요구한다, GitHub #64). 워밍업 세션이
캘린더 시작 전으로 넘치면 어댑터가 잘라내고 경고를 남긴다(초기 월은 스코어가 없어 포지션이 비는
것이 정상).

사용 (backend venv — ruamel.yaml·numpy·pyarrow·duckdb 가 필요하다):
  uv run --project backend python workspace/dongmin/scripts/run_mvp_backtest.py \\
      --root <equity_root> --start 2011-01-03 --end 2026-08-20 --universe krx.common-stock \\
      [--price-field price.adj_close] [--top 20] [--artifact-root <dir>]
      [--engine-src <repo>/backend/src]

종료 코드 0 = run COMPLETED, 1 = FAILED/CANCELLED(요약에 error), 2 = 인자·환경 오류.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from uuid import uuid4

ENGINE_SRC_ENV = "QL_ENGINE_SRC"
FACTOR_ID = "price.momentum_12_1"
PRICE_FIELDS = ("price.adj_close", "price.close")
POLL_SECONDS = 0.2


def default_engine_src() -> Path:
    """`$QL_ENGINE_SRC` 또는 `<repo>/backend/src`(scripts/ 에서 3단계 위) — equity.contract 규약."""
    env = os.environ.get(ENGINE_SRC_ENV)
    return Path(env) if env else Path(__file__).resolve().parents[3] / "backend" / "src"


def load_backend(engine_src: Path) -> None:
    marker = engine_src / "strategy_workbench" / "bootstrap" / "facade" / "container.py"
    if not marker.exists():
        raise FileNotFoundError(
            f"workbench not found — engine_src={engine_src} expected={marker} "
            f"(--engine-src 또는 ${ENGINE_SRC_ENV} 로 backend/src 를 지정)")
    root = str(engine_src)
    if root not in sys.path:
        sys.path.insert(0, root)


@dataclass(frozen=True)
class RunSummary:
    status: str
    run_id: str
    start: date
    end: date
    universe_id: str
    price_field: str
    data_snapshot_id: str
    n_sessions: int
    n_history_sessions: int
    n_securities: int
    n_rebalances: int
    n_rebalances_with_positions: int
    total_return: float | None
    cagr: float | None
    max_drawdown: float | None
    tape_hash: str
    run_fingerprint: str | None
    artifact_uri: str | None
    warnings: tuple[str, ...]
    error: str | None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


def momentum_spec(start: date, end: date, universe_id: str, price_field: str, top: int):
    """레지스트리 `price.momentum_12_1` 그래프의 FieldNode 만 `price_field` 로 바꾼 월간 롱온리."""
    from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
        InMemoryStrategyRepository,
    )
    from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
    from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry
    from strategy_workbench.domain.strategy.facade.specification import (
        DataStep,
        FactorDirection,
        FactorGraph,
        FactorSignal,
        FactorStep,
        FieldNode,
        Market,
        PortfolioSide,
        RebalanceFrequency,
        SelectionMethod,
    )

    definition = build_default_factor_registry().get(FACTOR_ID)
    if definition.default_graph is None:
        raise ValueError(f"registry factor has no default graph — factor_id={FACTOR_ID}")
    graph = FactorGraph(
        nodes=tuple(
            replace(node, field_id=price_field) if isinstance(node, FieldNode) else node
            for node in definition.default_graph.nodes),
        output_node_id=definition.default_graph.output_node_id,
        missing_policy=definition.default_graph.missing_policy,
    )
    template = StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "mvp",
                                     today=lambda: end).template()
    return replace(
        template,
        title=f"MVP-B {FACTOR_ID} on {price_field}",
        data=DataStep(market=Market.KRX, start=start, end=end, universe_id=universe_id),
        factors=FactorStep(factors=(FactorSignal(
            factor_id=FACTOR_ID, label=definition.label, direction=FactorDirection.HIGH,
            weight=1.0, graph=graph),)),
        portfolio=replace(template.portfolio, side=PortfolioSide.LONG_ONLY,
                          rebalance=RebalanceFrequency.MONTHLY,
                          selection_method=SelectionMethod.TOP_N, selection_count=top),
    )


def run(root: Path, start: date, end: date, universe_id: str, *, price_field: str = PRICE_FIELDS[0],
        top: int = 20, artifact_root: Path | None = None) -> RunSummary:
    """컨테이너 부팅 → run 시작 → 완료까지 폴링 → 요약. backend 는 미리 sys.path 에 있어야 한다."""
    from strategy_workbench.application.backtest_run.facade.runs import BacktestRunSpec, RunStatus
    from strategy_workbench.application.portfolio_design.facade.design import (
        PortfolioPreviewRequest,
    )
    from strategy_workbench.bootstrap.facade.container import build_container
    from strategy_workbench.domain.backtest.facade.runs import ExecutionCore

    if price_field not in PRICE_FIELDS:
        raise ValueError(f"price_field must be one of {PRICE_FIELDS} — got={price_field!r}")
    container = build_container(
        equity_adapter="duckdb", equity_root=root,
        artifact_root=artifact_root or root / "_runs" / f"mvp_{uuid4().hex[:8]}")
    spec = momentum_spec(start, end, universe_id, price_field, top)
    pipeline = container.portfolio_design.run_pipeline(PortfolioPreviewRequest(spec))
    tape = pipeline.preview.tape
    sessions = {o.as_of for o in pipeline.observations}
    response = container.backtest_runs.start(BacktestRunSpec(
        strategy=spec, core=ExecutionCore.PYTHON))
    run_id = response.run.run_id
    terminal = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    state = container.backtest_runs.state(run_id)
    while state.status not in terminal:
        time.sleep(POLL_SECONDS)
        state = container.backtest_runs.state(run_id)
    metrics: dict[str, float | None] = {}
    fingerprint: str | None = None
    if state.status is RunStatus.COMPLETED:
        result = container.backtest_runs.result(run_id)
        metrics = {m.metric_id: m.value for m in result.metrics if m.scope.value == "full"}
        fingerprint = result.manifest.run_fingerprint
    requested = {s for s in sessions if start <= s <= end}
    return RunSummary(
        status=state.status.value, run_id=run_id, start=start, end=end, universe_id=universe_id,
        price_field=price_field, data_snapshot_id=tape.data_snapshot_id,
        n_sessions=len(requested), n_history_sessions=len(sessions) - len(requested),
        n_securities=len({o.security_id for o in pipeline.observations}),
        n_rebalances=len(tape.frames),
        n_rebalances_with_positions=sum(1 for f in tape.frames if f.targets),
        total_return=metrics.get("total_return"), cagr=metrics.get("cagr"),
        max_drawdown=metrics.get("max_drawdown"), tape_hash=tape.tape_hash,
        run_fingerprint=fingerprint, artifact_uri=state.artifact_uri,
        warnings=pipeline.preview.warnings, error=state.error)


def print_summary(s: RunSummary) -> None:
    print(f"{s.status} run={s.run_id} {s.start}..{s.end} universe={s.universe_id} "
          f"factor={FACTOR_ID} field={s.price_field}")
    print(f"  snapshot={s.data_snapshot_id} sessions={s.n_sessions} securities={s.n_securities} "
          f"rebalances={s.n_rebalances} (with positions {s.n_rebalances_with_positions})")
    print(f"  total_return={s.total_return} cagr={s.cagr} max_drawdown={s.max_drawdown}")
    print(f"  tape_hash={s.tape_hash} run_fingerprint={s.run_fingerprint}")
    print(f"  artifact={s.artifact_uri}")
    for w in s.warnings:
        print(f"  warning: {w}")
    if s.error:
        print(f"  error: {s.error}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_mvp_backtest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True, help="equity_root (MANIFEST·equity.duckdb)")
    ap.add_argument("--start", type=date.fromisoformat, required=True)
    ap.add_argument("--end", type=date.fromisoformat, required=True)
    ap.add_argument("--universe", default="krx.common-stock")
    ap.add_argument("--price-field", choices=PRICE_FIELDS, default=PRICE_FIELDS[0])
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--artifact-root", type=Path)
    ap.add_argument("--engine-src", type=Path, default=default_engine_src())
    a = ap.parse_args(argv)
    try:
        load_backend(a.engine_src)
        summary = run(a.root, a.start, a.end, a.universe, price_field=a.price_field, top=a.top,
                      artifact_root=a.artifact_root)
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print_summary(summary)
    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())
