from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path

from pydantic import TypeAdapter

from strategy_workbench.application.signal_research.facade.research import (
    SignalResearchRequest,
    SignalResearchService,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.canonical.facade.encoding import canonical_payload_json
from strategy_workbench.domain.signal_evaluation.facade.evaluation import PriceLabelSpec
from strategy_workbench.domain.strategy.facade.document import hydrate_strategy_document
from strategy_workbench.domain.strategy.facade.specification import StrategyIdentity

ServiceFactory = Callable[[Path, Path], SignalResearchService]
_ENVIRONMENT = TypeAdapter(RunEnvironment)


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 키가 중복되었습니다 — key={key!r}")
        result[key] = value
    return result


def _read_object(path: Path) -> dict[str, object]:
    raw: object = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object_pairs)
    if not isinstance(raw, dict):
        raise ValueError("입력 JSON의 맨 위는 객체여야 합니다.")
    return raw


def _write_json(path: Path, content: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 완성된 증거를 기존 파일에 덮어쓰지 않는다. 임시 파일은 같은 파일시스템에서만 공개한다.
    if path.exists():
        raise ValueError("결과 파일이 이미 있습니다. 새 output-json 경로를 지정해 주세요.")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_cli(factory: ServiceFactory, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "보존한 원장으로 날짜별 예측 순위·미래 가격·비교 가능 비율을 평가합니다. "
            "실제 계좌 수익률은 계산하지 않습니다."
        )
    )
    parser.add_argument(
        "--preserved-equity-root",
        type=Path,
        required=True,
        help="S01 create로 완성한 보존 데이터 위치",
    )
    parser.add_argument(
        "--repo-root", type=Path, required=True, help="S01 보존 당시 코드·환경과 대조할 저장소 위치"
    )
    parser.add_argument(
        "--strategy-json", type=Path, required=True, help="기존 StrategySpec의 작성용 JSON 문서"
    )
    parser.add_argument(
        "--environment-json",
        type=Path,
        required=True,
        help="신호 기간·유니버스를 명시한 기존 RunEnvironment JSON",
    )
    parser.add_argument(
        "--factor-id",
        action="append",
        required=True,
        help="전략 안의 평가할 팩터 ID. 여러 번 지정 가능",
    )
    parser.add_argument(
        "--horizon-sessions",
        type=int,
        nargs="+",
        required=True,
        help="공통 거래 달력의 종가 사이 간격 수",
    )
    parser.add_argument(
        "--entry-delay-sessions",
        type=int,
        default=0,
        help="신호 종가에서 진입 종가까지 늦추는 거래 세션 수",
    )
    parser.add_argument("--minimum-pairs", type=int, default=3)
    parser.add_argument(
        "--evaluation-as-of",
        type=date.fromisoformat,
        required=True,
        help="이 날짜까지 공개·만기된 가격 결과만 비교",
    )
    parser.add_argument(
        "--label-calendar-through",
        type=date.fromisoformat,
        required=True,
        help="라벨 만기를 찾을 공통 달력의 조회 끝. 평가일 이후 달력도 가능",
    )
    parser.add_argument("--output-json", required=True, help="새 증거 JSON 경로. '-'는 표준 출력")
    args = parser.parse_args(argv)
    try:
        hydrated = hydrate_strategy_document(
            _read_object(args.strategy_json),
            identity=StrategyIdentity(strategy_id="signal-research", revision=0),
        )
        if not hydrated.ok or hydrated.spec is None:
            raise ValueError(f"전략 문서를 읽을 수 없습니다: {hydrated.issues}")
        environment_raw = _read_object(args.environment_json)
        if set(environment_raw) - {field.name for field in fields(RunEnvironment)}:
            raise ValueError("실행 설정에 RunEnvironment가 소유하지 않는 항목이 있습니다.")
        environment = _ENVIRONMENT.validate_python(environment_raw)
        request = SignalResearchRequest(
            hydrated.spec,
            environment,
            tuple(args.factor_id),
            PriceLabelSpec(
                tuple(args.horizon_sessions), args.entry_delay_sessions, args.minimum_pairs
            ),
            args.evaluation_as_of,
            args.label_calendar_through,
        )
        service = factory(args.preserved_equity_root, args.repo_root)
        outcome = service.run(request)
        if outcome.report is None:
            print(
                f"연구 평가를 완료하지 못했습니다 — status={outcome.status.value}; "
                f"{outcome.detail}",
                file=sys.stderr,
            )
            return 2
        content = canonical_payload_json(asdict(outcome.report), indent=2)
        if args.output_json == "-":
            print(content)
        else:
            _write_json(Path(args.output_json), content)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(
            f"연구 실행을 중단했습니다. 보존 입력·명세·원천을 확인해 주세요 — {error}",
            file=sys.stderr,
        )
        return 2
