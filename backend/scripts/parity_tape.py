"""두 tape 엔진을 대조하는 validator(columnar-tape spec 5절 Tier 2). 층은 L3(기본)와 L0 이다.

테스트가 아니다. `--equity-root` 를 주면 실원장(duckdb 어댑터), 없으면 mock 어댑터로 돈다. 사례는
그 어댑터의 필드 계약으로 도는 fixture 전략 문서와 레지스트리 `implemented` 기본 graph 다
(`tests/parity/_corpus.py` 의 `adapter_cases`).

- `--layer L3`: 사례마다 두 엔진의 파이프라인 결과를 대조한다. 지금 엔진은 객체 경로(`object`)
  하나라 `--engine-a`·`--engine-b` 가 같다. 열 경로가 들어오면 `tests/parity/_engines.py` 의
  `ENGINES` 에 더한다.
- `--layer L0`: 사례들이 원천에 보내는 원시 질의(파이프라인이 실제로 만드는 질의)를 모아 필드를
  합친 질의 하나로, `load_raw_panel(q)` 와 `RawPanel.from_observation_set(load_raw_observations(q),
  field_ids=q.field_ids)` 를 field_id 기준·비트 단위로 대조한다(spec 5절 L0). 어댑터가
  `ColumnarRawObservationPort` 능력을 선언해야 한다(duckdb). 두 경로의 로딩 시간도 낸다.

홀드아웃(`.claude/rules/holdout-window.md`):

- 측정 시작일이 연구 하한(`RESEARCH_START`)보다 이르면 아무것도 읽지 않고 거절한다.
- 출력은 불일치 개수와 차이의 (층, 사례, 경로)뿐이다. 값·수익률·IC 는 내지 않는다. L0 은 경로마다
  다른 칸 수만 낸다(워밍업 칸 값도 내지 않는다).
- 대조기가 잡지 않은 예외도 실원장에서는 예외 종류만 남긴다(메시지에 값이 실릴 수 있다).

사례가 하나도 없거나, 불일치·예외가 있으면 exit 1 이다. 마지막 줄은 결과 종류별 개수다.

라이브 서버 상태(전략·연구 DB, 실행 산출물)는 열지 않는다. 원장 카탈로그는 read_only 로 열리고,
실행 동안 작업 디렉터리를 임시 폴더로 옮겨 duckdb 임시 파일도 거기 둔다.

사용법:
    uv run python scripts/parity_tape.py --start 2023-07-03 --end 2024-01-12
    uv run python scripts/parity_tape.py --equity-root ~/quant-ledger/data/equity \\
        --start 2025-01-02 --end 2025-12-30
    uv run python scripts/parity_tape.py --layer L0 --equity-root ~/quant-ledger/data/equity \\
        --start 2025-01-02 --end 2025-12-30
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import numpy as np

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.portfolio_design.facade.ports import (
    ColumnarRawObservationPort,
    RawObservationQuery,
    RawObservationSet,
    RawPanel,
)
from strategy_workbench.domain.backtest.facade.environment import (
    ResearchWindowViolationError,
    RunEnvironment,
    require_research_window,
)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
# 대조기·말뭉치는 `tests/parity` 에 있다. 스크립트로 실행하면 sys.path[0] 이 `scripts/` 라
# `backend/` 를 먼저 세운다(`tools/export_assistant_prompts.py` 와 같은 관례).
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from tests.parity._compare import (  # noqa: E402  # reason: sys.path를 먼저 세워야 import된다
    compare,
    format_report,
)
from tests.parity._corpus import (  # noqa: E402  # reason: sys.path를 먼저 세워야 import된다
    EquityAdapter,
    PipelineCase,
    adapter_cases,
)
from tests.parity._engines import (  # noqa: E402  # reason: sys.path를 먼저 세워야 import된다
    ENGINES,
    Failure,
)


def _adapter(equity_root: Path | None) -> EquityAdapter:
    if equity_root is None:
        return MockEquityDataAdapter.demo()
    # duckdb 는 optional extra `equity` 라 실원장을 고를 때만 읽는다.
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )

    return EquityDuckdbAdapter(equity_root)


class _Recorded(Exception):
    """원시 질의를 받아 적은 뒤 파이프라인을 멈추는 신호."""

    def __init__(self, query: RawObservationQuery) -> None:
        super().__init__(query.field_ids)
        self.query = query


class _QueryRecorder:
    """원시 관측 포트 자리에 서서 파이프라인이 만든 질의만 받아 적는다(원천은 읽지 않는다).

    취소·진행 능력을 선언하지 않아 서비스가 `load_raw_observations` 를 부른다.
    """

    def load_raw_observations(self, query: RawObservationQuery) -> RawObservationSet:
        raise _Recorded(query)


def _raw_queries(
    cases: Sequence[PipelineCase], adapter: EquityAdapter, outcomes: collections.Counter[str]
) -> list[RawObservationQuery]:
    """사례마다 파이프라인이 원천에 보낼 질의. 원시 로딩 전에 끝나는 사례는 질의가 없다."""
    queries: list[RawObservationQuery] = []
    for case in cases:
        try:
            result = ENGINES["object"].pipeline(case, _QueryRecorder(), adapter)
        except _Recorded as recorded:
            queries.append(recorded.query)
            continue
        except Exception as error:
            # 실원장 예외 메시지는 셀 값을 실을 수 있어 종류만 남긴다(holdout-window.md).
            outcomes[f"query_error:{type(error).__name__}"] += 1
            print(f"{case.key} query_error={type(error).__name__}", flush=True)
            continue
        kind = result.error_type if isinstance(result, Failure) else "ok"
        outcomes[f"no_query:{kind}"] += 1
        print(f"{case.key} no_query={kind}", flush=True)
    return queries


def _union_query(queries: Sequence[RawObservationQuery]) -> RawObservationQuery:
    """사례 질의를 합친 질의 하나 — 필드는 합집합(정렬), 워밍업은 가장 긴 것이다.

    셀은 (종목, 세션, 필드)의 함수라 필드를 합쳐도 사례 질의의 칸은 그대로 들어 있다.
    """
    first = queries[0]
    return RawObservationQuery(
        market=first.market,
        universe_id=first.universe_id,
        start=first.start,
        end=first.end,
        field_ids=tuple(sorted({field_id for query in queries for field_id in query.field_ids})),
        history_sessions_before_start=max(query.history_sessions_before_start for query in queries),
    )


def _count(differs: np.ndarray) -> int:
    return int(np.count_nonzero(differs))


def _bits(values: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.float64).view(np.int64)


def panel_differences(expected: RawPanel, actual: RawPanel) -> list[tuple[str, int]]:
    """L0 차이를 (경로, 다른 칸 수)로 낸다. 값은 싣지 않는다(holdout-window.md).

    field_id 로 열을 짝짓고, 칸마다 present·has_value·kind·available 이 같고 값이 IEEE 비트로
    같은지 본다(허용오차 0, spec D1). 행 키가 다르면 칸 비교는 하지 않는다.
    """
    differences: list[tuple[str, int]] = []
    meta = ("status", "data_snapshot_id", "sessions", "history_sessions", "detail", "warnings")
    for name in (*meta, "securities", "sectors"):
        if getattr(expected, name) != getattr(actual, name):
            differences.append((f"meta.{name}", 1))
    if expected.row_date.shape != actual.row_date.shape:
        differences.append(("rows.len", abs(expected.row_date.size - actual.row_date.size)))
        return differences
    keys = (expected.row_date != actual.row_date) | (expected.row_security != actual.row_security)
    if keys.any() or expected.securities != actual.securities:
        differences.append(("rows.key", max(_count(keys), 1)))
        return differences
    for name in ("universe_member", "sector"):
        if count := _count(getattr(expected, name) != getattr(actual, name)):
            differences.append((f"rows.{name}", count))
    if count := _count(_bits(expected.previous_weight) != _bits(actual.previous_weight)):
        differences.append(("rows.previous_weight", count))
    expected_ids = [column.field_id for column in expected.fields]
    if expected_ids != [column.field_id for column in actual.fields]:
        differences.append(("fields.order", 1))
    columns = {column.field_id: column for column in actual.fields}
    for wanted in expected.fields:
        got = columns.get(wanted.field_id)
        if got is None:
            differences.append((f"fields[{wanted.field_id!r}].missing", 1))
            continue
        both = wanted.present & got.present
        valued = both & wanted.has_value & got.has_value
        texts = valued & ((wanted.text >= 0) | (got.text >= 0))
        numbers = valued & ~texts
        checks = {
            "present": wanted.present != got.present,
            "has_value": both & (wanted.has_value != got.has_value),
            "kind": both & (wanted.kind != got.kind),
            "available": both & (wanted.available != got.available),
            "value": numbers & (_bits(wanted.value) != _bits(got.value)),
        }
        if texts.any():
            wanted_text = [wanted.categories[code] if code >= 0 else None for code in wanted.text]
            got_text = [got.categories[code] if code >= 0 else None for code in got.text]
            checks["text"] = texts & np.array(
                [left != right for left, right in zip(wanted_text, got_text, strict=True)]
            )
        for name, differs in checks.items():
            if count := _count(differs):
                differences.append((f"fields[{wanted.field_id!r}].{name}", count))
    return differences


def _noop_checkpoint() -> None:
    pass


def _noop_progress(fraction: float) -> None:
    pass


def _run_l0(
    adapter: EquityAdapter,
    cases: Sequence[PipelineCase],
    *,
    limit: int,
    outcomes: collections.Counter[str],
) -> int:
    """L0 대조 하나를 돌려 불일치 칸 수를 낸다."""
    if not isinstance(adapter, ColumnarRawObservationPort):
        raise SystemExit(
            f"L0 needs an adapter with the columnar capability — got {type(adapter).__name__}"
        )
    queries = _raw_queries(cases, adapter, outcomes)
    if not queries:
        return 0
    query = _union_query(queries)
    started = time.perf_counter()
    raw = adapter.load_raw_observations(query)
    row_seconds = time.perf_counter() - started
    started = time.perf_counter()
    reference = RawPanel.from_observation_set(raw, field_ids=query.field_ids)
    convert_seconds = time.perf_counter() - started
    del raw
    started = time.perf_counter()
    columnar = adapter.load_raw_panel(query, checkpoint=_noop_checkpoint, progress=_noop_progress)
    columnar_seconds = time.perf_counter() - started
    differences = panel_differences(reference, columnar)
    mismatches = sum(count for _, count in differences)
    outcomes["l0_ok" if not differences else "l0_mismatch"] += 1
    print(
        f"L0 queries={len(queries)} fields={len(query.field_ids)} "
        f"history={query.history_sessions_before_start} rows={reference.row_date.size} "
        f"status={reference.status.value} mismatches={mismatches} "
        f"row_seconds={row_seconds:.1f} convert_seconds={convert_seconds:.1f} "
        f"columnar_seconds={columnar_seconds:.1f}",
        flush=True,
    )
    for path, count in differences[:limit]:
        print(f"  L0 path={path} cells={count}", flush=True)
    if len(differences) > limit:
        print(f"  ... {len(differences) - limit} more paths", flush=True)
    # 필드 id 는 키라 낸다(값이 아니다).
    print(f"L0 field_ids={list(query.field_ids)}", flush=True)
    return mismatches


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="두 tape 엔진을 대조한다. 불일치 개수와 키만 낸다."
    )
    parser.add_argument("--equity-root", type=Path, help="원장 equity 루트. 없으면 mock")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--universe", default="krx.common-stock")
    parser.add_argument("--layer", choices=("L0", "L3"), default="L3")
    parser.add_argument("--engine-a", choices=sorted(ENGINES), default="object")
    parser.add_argument("--engine-b", choices=sorted(ENGINES), default="object")
    parser.add_argument("--case", default="", help="사례 키에 이 문자열이 든 것만 돈다")
    parser.add_argument("--limit", type=int, default=20, help="사례마다 보일 차이 경로 수")
    args = parser.parse_args(argv)
    try:
        require_research_window(args.start, requested_by="scripts/parity_tape.py")
    except ResearchWindowViolationError as error:
        print(error, file=sys.stderr)
        return 2
    equity_root = None if args.equity_root is None else args.equity_root.expanduser().resolve()
    environment = RunEnvironment(start=args.start, end=args.end, universe_id=args.universe)
    engine_a, engine_b = ENGINES[args.engine_a], ENGINES[args.engine_b]
    working_directory = Path.cwd()
    mismatches = 0
    outcomes: collections.Counter[str] = collections.Counter()
    with tempfile.TemporaryDirectory(prefix="parity-tape-") as scratch:
        os.chdir(scratch)
        try:
            adapter = _adapter(equity_root)
            cases = [
                case
                for case in adapter_cases(adapter, environment, source="adapter")
                if args.case in case.key
            ]
            if args.layer == "L0":
                try:
                    mismatches = _run_l0(adapter, cases, limit=args.limit, outcomes=outcomes)
                except Exception as error:
                    if equity_root is None:
                        raise
                    outcomes[f"error:{type(error).__name__}"] += 1
                    print(f"L0 result=error:{type(error).__name__}", flush=True)
            for case in cases if args.layer == "L3" else ():
                started = time.perf_counter()
                try:
                    expected = engine_a.pipeline(case, adapter, adapter)
                    actual = engine_b.pipeline(case, adapter, adapter)
                except Exception as error:
                    if equity_root is None:
                        raise
                    # 실원장 예외 메시지는 셀 값을 실을 수 있어 종류만 남긴다(holdout-window.md).
                    outcome = f"error:{type(error).__name__}"
                    outcomes[outcome] += 1
                    print(f"{case.key} result={outcome}", flush=True)
                    continue
                report = compare("L3", [(case.key, expected, actual)], limit=args.limit)
                mismatches += report.total
                outcome = expected.error_type if isinstance(expected, Failure) else "ok"
                outcomes[outcome] += 1
                print(
                    f"{case.key} result={outcome} mismatches={report.total} "
                    f"seconds={time.perf_counter() - started:.1f}",
                    flush=True,
                )
                if not report.ok:
                    print(format_report(report, values=False), flush=True)
        finally:
            os.chdir(working_directory)
    engines = "columnar/object" if args.layer == "L0" else f"{args.engine_a}/{args.engine_b}"
    print(
        f"layer={args.layer} engines={engines} source={'ledger' if equity_root else 'mock'} "
        f"window={args.start}..{args.end} cases={len(cases)} mismatches={mismatches}"
    )
    errors = sum(count for name, count in outcomes.items() if "error:" in name)
    counts = " ".join(f"{name}={count}" for name, count in sorted(outcomes.items()))
    print(f"outcomes {counts or 'none'}")
    checked = outcomes["l0_ok"] + outcomes["l0_mismatch"] if args.layer == "L0" else len(cases)
    return 0 if cases and checked and mismatches == 0 and errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
