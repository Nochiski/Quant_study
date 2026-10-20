"""백테스트 run 하나의 계산(run-worker spec D10).

벤치마크·데이터 준비 확인부터 산출물 커밋과 시도 요약 값까지, 그리고 실패 분류를 이 모듈이
소유한다. 스레드 전송과 worker 프로세스가 같은 `RunComputation.compute` 를 부른다. 상태 전이·
잠금·저장은 서비스(`_service.py`)가 소유하고, 여기서는 진행을 보고하고 종결을 값으로 돌려준다.
"""

from __future__ import annotations

import re
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace

from strategy_workbench.application.portfolio_design.facade.design import (
    EngineCompatibility,
    IncompatiblePortfolioRequestError,
    InvalidPortfolioRequestError,
    PortfolioDesignService,
    PortfolioPipelineCancelledError,
    PortfolioPipelineOptions,
    PortfolioPreviewRequest,
    RawObservationContractError,
    RawObservationUnavailableError,
)
from strategy_workbench.domain.analytics.facade.metrics import MetricScope
from strategy_workbench.domain.backtest.facade.environment import cost_history_sessions
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    DataWarning,
    RunFailureCode,
    RunStage,
    WarningSeverity,
)
from strategy_workbench.domain.backtest.facade.trials import representative_sharpe

from .ports.outgoing.artifact_store import BacktestArtifactStorePort
from .ports.outgoing.backtest_data import (
    BacktestDataNotReadyError,
    BacktestDataPort,
    BacktestDataQuery,
    BacktestDataUnavailableError,
)
from .ports.outgoing.backtest_executor import (
    BacktestExecutionRequest,
    BacktestExecutorPort,
    EquityWipedOutError,
    RunCancelledError,
)
from .ports.outgoing.run_execution import (
    RunCancelled,
    RunCommitted,
    RunFailed,
    RunFootprint,
    RunJob,
    RunOutcome,
)
from .ports.outgoing.trial_ledger import RunHeadline

# 실행 진행 막대에서 단계가 차지하는 구간. 실데이터 실측에서 tape 단계가 실행 시간의 96~97% 를
# 쓰므로(이슈 #162, 6개월·9.5년 구간) 막대 대부분을 tape 에 주고 나머지 단계를 그 뒤에 둔다.
_TAPE_PROGRESS_START = 0.02
_TAPE_PROGRESS_END = 0.8
_DATA_PROGRESS = 0.82
_ENGINE_PROGRESS_START = 0.84
_ENGINE_PROGRESS_END = 0.92
_ARTIFACT_PROGRESS = 0.93

# run `error` 문자열에서 서버 절대 경로를 가린다. 서버 경로를 담을 수 있는 서드파티 원문(파일·DB
# 입출력 예외)이 응답으로 나가는 곳은 run `error` 하나다 — 우리가 쓰는 문장(어댑터 detail 등)은
# 처음부터 경로 없이 쓰지만(#163), duckdb·`OSError` 원문은 `C:\...`·`/home/...`·`\\server\share` 를
# 담아 화면(role=alert)에 서버 레이아웃·계정명·호스트명을 흘린다. 원문은 서버 로그에. 규칙(오탐·
# 미탐을 모두 줄이는 쪽으로):
#   - `root=`·`path=`·`file=`·`dir=`·`manifest=`(접미형 `equity_root=` 포함) 값은 모양과 무관하게
#     전부 가린다(컨테이너 `/app`, MSYS `/c/Users`, 임의 루트 포함).
#   - 드라이브(`C:\`)·UNC(`\\host\share`)·`file://` 은 어디 있든 가린다(모양만으로 경로). 따옴표로
#     감싼 드라이브·UNC 경로는 여는 따옴표와 같은 종류의 닫는 따옴표까지 가린다 — 공백이 든 경로
#     (`C:\Users\John Smith\…`)나 다른 따옴표가 든 계정명(`"C:\Users\O'Brien\…"`)이 중간에 끊겨
#     계정명이 새지 않게 한다(#318).
#   - 키 없는 POSIX 문자열은 (a) 확장자 있는 파일(`/x/y/z.parquet`)과 (b) 계정명·서버 레이아웃을
#     담는 루트(`/home`·`/Users`·`/tmp` 등) 아래 디렉터리만 가린다 — 서드파티 예외(`OSError`,
#     duckdb)가 싣는 `'/home/<user>/...'` 를 잡되, `/data/universe_id` 같은 JSON Pointer 진단
#     경로와 `/s` 단위 표기는 파일 경로가 아니다.
#   - `https://` 등 다른 URL 은 그대로 둔다. 값 끝의 문장부호(`,`·`.`)는 경로에 넣지 않는다.
_PATH_CHARS = r"[^\s'\"`()<>]"
_POSIX_ROOTS = r"(?:home|Users|root|tmp|var|srv|opt|mnt|app|workspace|Library|private|Volumes)"
_ABSOLUTE_PATH = re.compile(
    r"(?P<url>\b(?!file://)[A-Za-z][A-Za-z0-9+.\-]*://" + _PATH_CHARS + r"*)"
    r"|(?P<key>\b\w*(?:root|path|file|dir|directory|manifest)=)(?P<value>" + _PATH_CHARS + r"+)"
    r"|(?P<path>"
    r"(?<=\")(?:[A-Za-z]:[\\/]|\\\\)[^\"\r\n]*(?=\")"
    r"|(?<=')(?:[A-Za-z]:[\\/]|\\\\)[^'\r\n]*(?=')"
    r"|\bfile://" + _PATH_CHARS + r"*"
    r"|\\\\" + _PATH_CHARS + r"+"
    r"|(?<!\w)[A-Za-z]:[\\/]" + _PATH_CHARS + r"*"
    r"|(?<![\w.:])/(?:[\w.\-~%+]+/)+[\w\-~%+]+\.\w+"
    r"|(?<![\w.:])/" + _POSIX_ROOTS + r"(?:/[\w.\-~%+]*)+"
    r")",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = ",.;:"


def _mask_paths(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        whole = match.group(0)
        if match.group("url") is not None:
            return whole
        kept = whole.rstrip(_TRAILING_PUNCTUATION)
        trailing = whole[len(kept) :]
        if match.group("key") is not None:
            return match.group("key") + "<path>" + trailing
        return "<path>" + trailing

    return _ABSOLUTE_PATH.sub(replace, text)


class InvalidBacktestRunError(ValueError):
    pass


class NoPositionsError(RuntimeError):
    """tape 가 한 번도 종목을 고르지 않았다 — 기간 안에 리밸런싱일이 없거나 선정이 비었다(#360).

    요청이 틀린 것도 서버 오류도 아니라 이 설정의 결과라 전용 실패 코드로 끝난다.
    """


RunProgress = Callable[[RunStage, float, str], None]
"""(단계, run 막대 위치 0~1, 설명). 취소가 이겼으면 `RunCancelledError` 를 낸다."""


@dataclass(frozen=True)
class RunComputation:
    """run 계산이 읽는 포트 묶음. 서비스가 자기 의존으로 만들어 전송에 넘긴다."""

    portfolio_design: PortfolioDesignService
    data_source: BacktestDataPort
    executor: BacktestExecutorPort
    artifact_store: BacktestArtifactStorePort

    def compute(
        self, job: RunJob, *, progress: RunProgress, cancelled: Callable[[], bool]
    ) -> RunOutcome:
        """계산하고 결과를 커밋한다. 종결은 값으로 돌려주고 예외를 올리지 않는다.

        `progress` 나 `cancelled` 가 취소를 알리면 그 자리에서 멈춘다. 돌려준 커밋은 아직 보이지
        않는다 — 완료와 취소의 선형화는 서비스가 한다.
        """
        try:
            return self._commit(job, progress, cancelled)
        except RunCancelledError:
            return RunCancelled()
        except BaseException as error:
            # 프레임을 먼저 비운다. 실패 값을 지역 변수에 두면 이 프레임(아직 도는 중이라
            # 비울 수 없다)과 순환을 이뤄 job·listener 가 GC 를 기다린다. 실패 분류는 예외
            # 타입·메시지만 읽는다.
            _release_frames(error)
            return RunFailed(_failure_code(error), _describe_failure(error), exception=error)

    def _commit(
        self, job: RunJob, progress: RunProgress, cancelled: Callable[[], bool]
    ) -> RunCommitted:
        spec = job.spec
        strategy = spec.strategy
        environment = spec.environment
        # tape 단계: 원시 관측 로딩 + 팩터 평가 + TargetTape 컴파일. 실데이터에서 실행 시간의
        # 대부분을 차지하므로 취소 콜백을 파이프라인 checkpoint 에 그대로 건다.
        progress("tape", _TAPE_PROGRESS_START, "Compiling target tape")
        # 데이터 포트의 준비(원장 카탈로그 등, #369)와 벤치마크 id(#361)는 tape 앞에서 한 번의
        # 조회로 확인한다 — tape 계산을 다 쓴 뒤 알리지 않는다. 벤치마크가 없으면 종목 없는
        # 질의다. 접수는 여전히 데이터를 읽지 않는다(#158).
        self.data_source.load_backtest_dataset(
            BacktestDataQuery(environment.start, environment.end, (), spec.benchmark_security_id)
        )
        try:
            # require_engine_compatible 은 start() 의 preflight 판정을 되풀이하는 심층 방어다 —
            # 같은 순수 판정이라 정상 경로에서는 발동하지 않는다.
            pipeline = self.portfolio_design.run_pipeline(
                PortfolioPreviewRequest(strategy, environment=environment),
                options=PortfolioPipelineOptions(
                    require_engine_compatible=True, parameter_values=spec.parameter_values
                ),
                cancelled=cancelled,
                # 파이프라인 진행(0~1)을 run 막대의 tape 구간으로 옮긴다.
                progress=lambda fraction, message: progress(
                    "tape",
                    _TAPE_PROGRESS_START + fraction * (_TAPE_PROGRESS_END - _TAPE_PROGRESS_START),
                    message,
                ),
            )
        except PortfolioPipelineCancelledError as error:
            raise RunCancelledError("run cancelled while compiling target tape") from error
        preview = pipeline.preview
        footprint = RunFootprint(
            sessions=pipeline.raw_sessions,
            fields=pipeline.raw_fields,
            factors=len(strategy.factors),
            observation_rows=len(pipeline.raw_observations),
        )
        # 원시 관측·팩터 평가는 tape 뒤에 쓰지 않는다. 쥐고 있으면 data·engine 단계 내내 수백만
        # 객체가 살아 최대 RSS 가 커진다.
        del pipeline
        tape = preview.tape
        if tape.strategy_hash != job.provenance.spec_hash:
            raise RuntimeError(
                "compiled TargetTape hash differs from the accepted strategy provenance — "
                f"run_id={job.run_id} provenance={job.provenance.spec_hash!r} "
                f"tape={tape.strategy_hash!r}"
            )
        _raise_if_cancelled(cancelled)
        progress("data", _DATA_PROGRESS, "Loading market data")
        security_ids = tuple(
            sorted({target.security_id for frame in tape.frames for target in frame.targets})
        )
        if not security_ids:
            raise NoPositionsError(
                "target tape selected no position in any rebalance frame — "
                f"frames={len(tape.frames)} start={environment.start} "
                f"end={environment.end} rebalance={strategy.portfolio.rebalance.value}"
            )
        dataset = self.data_source.load_backtest_dataset(
            BacktestDataQuery(
                start=environment.start,
                end=environment.end,
                security_ids=security_ids,
                benchmark_security_id=spec.benchmark_security_id,
                history_sessions_before_start=cost_history_sessions(environment),
            )
        )
        # The preview's caveats travel with the data they describe, so the manifest records
        # every warning the run was built on, not just the market-data ones.
        # tape 컴파일 경고는 컴파일러 코드를 그대로 쓴다(이슈 #203). 원시 관측 코드로 묶으면
        # 결과 화면에서 출처가 틀린다.
        dataset = replace(
            dataset,
            warnings=(
                *_as_data_warnings(preview.warnings),
                *(
                    DataWarning(
                        code=item.code.value,
                        message=item.message,
                        severity=WarningSeverity.WARNING,
                    )
                    for item in preview.tape.warnings
                ),
                *dataset.warnings,
            ),
        )
        _raise_if_cancelled(cancelled)
        progress("engine", _ENGINE_PROGRESS_START, "Running backtest engine")
        result = self.executor.execute(
            BacktestExecutionRequest(
                job.run_id,
                spec,
                tape,
                dataset,
                job.provenance,
                job.strategy_source,
                job.lineage_strategy_id,
            ),
            # 실행기는 자기 작업 안의 비율(0~1)과 설명만 보고한다. run 막대의 engine 구간으로
            # 옮기고 단계는 `engine` 하나로 둔다 — 단계 어휘는 `RunStage` 가 소유한다(DR-B-03).
            progress=lambda value, message: progress(
                "engine",
                _ENGINE_PROGRESS_START
                + min(max(value, 0.0), 1.0) * (_ENGINE_PROGRESS_END - _ENGINE_PROGRESS_START),
                message,
            ),
            cancelled=cancelled,
        )
        _raise_if_cancelled(cancelled)
        progress("artifact", _ARTIFACT_PROGRESS, "Committing artifacts")
        # 시도 요약은 커밋 앞에서 구한다 — 구하다 실패하면 보이지 않을 결과 파일을 남기지 않는다.
        session_sharpe = representative_sharpe(result)
        headline = run_headline(result)
        commit = self.artifact_store.commit(result)
        return RunCommitted(
            sha256=commit.sha256,
            session_sharpe=session_sharpe,
            metric_registry_version=result.manifest.metric_registry_version,
            headline=headline,
            footprint=footprint,
        )


def _raise_if_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise RunCancelledError("run cancelled")


def _failure_code(error: BaseException) -> RunFailureCode:
    """run `error_code`. 어휘 SoT 는 `RunFailureCode`(프론트 번역 키 `backtest.run.error.*`)."""

    if isinstance(error, InvalidPortfolioRequestError):
        return "portfolio.strategy.invalid"
    if isinstance(error, RawObservationUnavailableError):
        return "portfolio.data.unavailable"
    if isinstance(error, RawObservationContractError):
        return "portfolio.raw_observation.invalid"
    if isinstance(error, (InvalidBacktestRunError, IncompatiblePortfolioRequestError)):
        return "backtest.run.invalid"
    if isinstance(error, EquityWipedOutError):
        return "backtest.run.equity_wiped_out"
    if isinstance(error, BacktestDataNotReadyError):
        return "backtest.run.data_not_ready"
    if isinstance(error, NoPositionsError):
        return "backtest.run.no_positions"
    if isinstance(error, BacktestDataUnavailableError):
        return "backtest.run.benchmark_unknown"
    return "backtest.run.internal"


def _describe_failure(error: BaseException) -> str:
    """run 상태의 `error` 문자열(화면 노출용).

    검증 실패는 issue 코드·경로를, 엔진 비호환은 부족한 능력을 실어야 화면에서 원인을 알 수 있다.
    서버 절대 경로는 가린다 — 원문은 서비스가 로그로 남긴다.
    """

    text = f"{type(error).__name__}: {error}"
    if isinstance(error, InvalidPortfolioRequestError):
        issues = "; ".join(
            f"{issue.code}@{issue.path}: {issue.message}" for issue in error.validation.issues
        )
        text = f"{text} — {issues}" if issues else text
    elif isinstance(error, IncompatiblePortfolioRequestError):
        text = f"{text} — {_describe_engine_issues(error.compatibility)}"
    return _mask_paths(text)


def _describe_engine_issues(engine: EngineCompatibility) -> str:
    return (
        "; ".join(
            f"{issue.category}.{issue.name}={issue.support}"
            + (f" ({issue.reason})" if issue.reason else "")
            for issue in engine.issues
        )
        or "no issues reported"
    )


def _as_data_warnings(messages: tuple[str, ...]) -> tuple[DataWarning, ...]:
    """Raw observation warnings as manifest rows, under one code so their origin stays readable."""
    return tuple(
        DataWarning(
            code="portfolio.raw_observation",
            message=message,
            severity=WarningSeverity.WARNING,
        )
        for message in messages
    )


# 목록 머리 지표의 registry id. 값은 결과의 FULL 범위 지표 그대로 옮긴다(재설계 결정 1).
_HEADLINE_METRICS = ("total_return", "sharpe", "max_drawdown")


def run_headline(result: BacktestRunResult) -> RunHeadline:
    """완료 결과의 FULL 범위 머리 지표. 지표가 없거나 사용 불가면 그 칸은 None 이다."""
    values = {
        metric.metric_id: metric.value
        for metric in result.metrics
        if metric.scope is MetricScope.FULL and metric.metric_id in _HEADLINE_METRICS
    }
    return RunHeadline(
        total_return=values.get("total_return"),
        sharpe=values.get("sharpe"),
        max_drawdown=values.get("max_drawdown"),
    )


def _release_frames(error: BaseException) -> None:
    """실패 예외 체인의 traceback 프레임 지역 변수를 비운다(#572 리뷰 P2).

    traceback 이 계산 프레임을 쥐면 그 지역 변수(tape·데이터셋·결과)가 GC 미루기 구간 밖까지 살아,
    구간이 끝난 뒤의 전체 수집이 그것을 훑어 같은 프로세스의 HTTP 요청을 세운다(#196). 파일·줄·함수
    정보는 남아 로그 stack trace 는 그대로다. 아직 도는 프레임(이 함수를 부른 `compute`)은
    `traceback.clear_frames` 가 건너뛴다.
    """
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        pending.extend(
            item for item in (current.__cause__, current.__context__) if item is not None
        )
