"""두 tape 엔진을 L3(파이프라인)로 대조하는 validator(columnar-tape spec 5절 Tier 2).

테스트가 아니다. `--equity-root` 를 주면 실원장(duckdb 어댑터), 없으면 mock 어댑터로 돈다. 사례는
그 어댑터의 필드 계약으로 도는 fixture 전략 문서와 레지스트리 `implemented` 기본 graph 다
(`tests/parity/_corpus.py` 의 `adapter_cases`). 지금 엔진은 객체 경로(`object`) 하나라
`--engine-a`·`--engine-b` 가 같다. 열 경로가 들어오면 `tests/parity/_engines.py` 의 `ENGINES` 에
더한다.

홀드아웃(`.claude/rules/holdout-window.md`):

- 측정 시작일이 연구 하한(`RESEARCH_START`)보다 이르면 아무것도 읽지 않고 거절한다.
- 출력은 사례별 불일치 개수와 차이의 (층, 사례, 경로)뿐이다. 값·수익률·IC 는 내지 않는다.

라이브 서버 상태(전략·연구 DB, 실행 산출물)는 열지 않는다. 원장 카탈로그는 read_only 로 열리고,
실행 동안 작업 디렉터리를 임시 폴더로 옮겨 duckdb 임시 파일도 거기 둔다.

사용법:
    uv run python scripts/parity_tape.py --start 2023-07-03 --end 2024-01-12
    uv run python scripts/parity_tape.py --equity-root ~/quant-ledger/data/equity \\
        --start 2025-01-02 --end 2025-12-30
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="두 tape 엔진을 L3 로 대조한다. 불일치 개수와 키만 낸다."
    )
    parser.add_argument("--equity-root", type=Path, help="원장 equity 루트. 없으면 mock")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--universe", default="krx.common-stock")
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
    with tempfile.TemporaryDirectory(prefix="parity-tape-") as scratch:
        os.chdir(scratch)
        try:
            adapter = _adapter(equity_root)
            cases = [
                case
                for case in adapter_cases(adapter, environment, source="adapter")
                if args.case in case.key
            ]
            for case in cases:
                started = time.perf_counter()
                expected = engine_a.pipeline(case, adapter, adapter)
                actual = engine_b.pipeline(case, adapter, adapter)
                report = compare("L3", [(case.key, expected, actual)], limit=args.limit)
                mismatches += report.total
                outcome = expected.error_type if isinstance(expected, Failure) else "ok"
                print(
                    f"{case.key} result={outcome} mismatches={report.total} "
                    f"seconds={time.perf_counter() - started:.1f}",
                    flush=True,
                )
                if not report.ok:
                    print(format_report(report, values=False), flush=True)
        finally:
            os.chdir(working_directory)
    print(
        f"engines={args.engine_a}/{args.engine_b} source={'ledger' if equity_root else 'mock'} "
        f"window={args.start}..{args.end} cases={len(cases)} mismatches={mismatches}"
    )
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
