"""개별종목차트 유스케이스의 요청·응답 값 타입. 가격은 모두 화면 가격(저장 좌표 × k)이다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from strategy_workbench.application.stock_chart._errors import LineIssue
from strategy_workbench.application.stock_chart._lines import ChartPoint, LevelPath
from strategy_workbench.application.stock_chart.ports.outgoing.chart_drawings import (
    AutoRecord,
    ChartLine,
)
from strategy_workbench.application.stock_chart.ports.outgoing.chart_prices import (
    ChartInstrument,
    InstrumentRef,
)
from strategy_workbench.domain.chart_analysis.facade.bars import Candle, Timeframe
from strategy_workbench.domain.chart_analysis.facade.levels import (
    AnchorIssue,
    ChannelCandidate,
    HorizontalZone,
    SwingChoice,
    TrendDirection,
)
from strategy_workbench.domain.chart_analysis.facade.line_record import (
    LineKind,
    LineSource,
    WindowSource,
)
from strategy_workbench.domain.chart_analysis.facade.regime import RegimeJudgement
from strategy_workbench.domain.chart_analysis.facade.series import PivotKind


@dataclass(frozen=True)
class ChartContext:
    """응답 공통 머리. `as_of`는 원장 최신 확정 거래일, `notice`는 연습용 데이터 안내다."""

    instrument: ChartInstrument
    as_of: date
    practice_data: bool
    notice: str | None


@dataclass(frozen=True)
class InstrumentSearch:
    practice_data: bool
    notice: str | None
    instruments: tuple[ChartInstrument, ...]


@dataclass(frozen=True)
class CandleView:
    """화면 가격 캔들. 마지막 봉이 진행 중일 수 있다(`Candle.in_progress`, 표시만).

    `change_pct`는 `candles`와 같은 순서로 직전 봉 종가 대비 등락률(%)이다. 직전 봉은 표시
    구간 밖이어도 같은 계열의 바로 앞 봉이고, 계열의 첫 봉은 None이다. 화면은 그대로 보인다.
    """

    context: ChartContext
    timeframe: Timeframe
    candles: tuple[Candle, ...]
    last_completed: date | None
    change_pct: tuple[float | None, ...] = ()


@dataclass(frozen=True)
class SwingSettings:
    """피벗 민감도(후보용·구조 판정용 k). None이면 domain 기본값이고 범위는 domain이 검사한다."""

    candidate_sensitivity: float | None = None
    structure_sensitivity: float | None = None


@dataclass(frozen=True)
class WindowRequest:
    """판정·분석 구간의 시작·끝 봉 날짜. 둘 다 None이면 주기별 기본 구간(가운데 제안 칩)이다."""

    start: date | None = None
    end: date | None = None


DEFAULT_WINDOW = WindowRequest()
DEFAULT_SWING = SwingSettings()


@dataclass(frozen=True)
class WindowView:
    start: date
    end: date
    bars: int
    source: WindowSource


@dataclass(frozen=True)
class WindowSuggestion:
    bars: int
    start: date
    end: date
    default: bool


@dataclass(frozen=True)
class PivotPoint:
    kind: PivotKind
    bar_date: date
    price: float
    confirmed_date: date


@dataclass(frozen=True)
class RegimeView:
    """판정 값은 domain `judgement` 그대로이고, 피벗만 화면 좌표(`structure_highs`·`_lows`)로
    옮긴다. `rules`는 판정 규칙 설명 문장(domain `REGIME_RULES`), `pivot_rule`은 피벗 확정 규칙
    문장이다."""

    context: ChartContext
    timeframe: Timeframe
    window: WindowView
    suggestions: tuple[WindowSuggestion, ...]
    judgement: RegimeJudgement
    structure_highs: tuple[PivotPoint, ...]
    structure_lows: tuple[PivotPoint, ...]
    sentence: str
    warnings: tuple[str, ...]
    rules: tuple[str, ...]
    pivot_rule: str


@dataclass(frozen=True)
class LineDrawing:
    """선 한 줄의 화면 좌표: 저장에 그대로 쓸 기준점과 레벨 선을 그릴 점."""

    anchors: tuple[ChartPoint, ...]
    paths: tuple[LevelPath, ...]


@dataclass(frozen=True)
class ChannelCandidateView:
    drawing: LineDrawing
    candidate: ChannelCandidate
    created: date


@dataclass(frozen=True)
class ChannelView:
    context: ChartContext
    timeframe: Timeframe
    window: WindowView
    direction: TrendDirection
    recommended_direction: TrendDirection | None
    candidates: tuple[ChannelCandidateView, ...]
    total_candidates: int
    anchor_issue: AnchorIssue | None
    anchor_issue_message: str | None
    record: AutoRecord
    # 첫 점으로 고를 수 있는 점(그 방향의 확정 피벗, domain `first_anchor_choices`)
    anchor_choices: tuple[PivotPoint, ...]


@dataclass(frozen=True)
class RetracementView:
    context: ChartContext
    timeframe: Timeframe
    window: WindowView
    choice: SwingChoice
    drawing: LineDrawing | None
    created: date | None
    message: str | None
    record: AutoRecord


@dataclass(frozen=True)
class ZoneView:
    drawing: LineDrawing
    zone: HorizontalZone
    low: float
    high: float
    formed: date
    members: tuple[PivotPoint, ...]


@dataclass(frozen=True)
class ZonesView:
    context: ChartContext
    timeframe: Timeframe
    window: WindowView
    zones: tuple[ZoneView, ...]
    basis: str
    record: AutoRecord
    rules: tuple[str, ...]


@dataclass(frozen=True)
class LineEdit:
    """선의 고칠 수 있는 칸. 기준점은 화면 좌표(봉 날짜, 화면 가격)다 — 서비스가 ÷k 해서
    저장한다."""

    anchors: tuple[ChartPoint, ...]
    levels: tuple[float, ...] | None = None
    extend_right: bool = True
    memo: str = ""
    locked: bool = False
    visible: bool = True


@dataclass(frozen=True)
class LineShape:
    """저장 전 미리보기의 입력 — 저장 요청과 같은 초안의 기하 칸."""

    instrument: InstrumentRef
    timeframe: Timeframe
    kind: LineKind
    anchors: tuple[ChartPoint, ...]
    levels: tuple[float, ...] | None = None


@dataclass(frozen=True)
class ChartLineDraft:
    instrument: InstrumentRef
    timeframe: Timeframe
    kind: LineKind
    source: LineSource
    edit: LineEdit
    auto: AutoRecord | None = None


@dataclass(frozen=True)
class ChartLineView:
    """저장된 선과 화면 좌표. 그릴 수 없으면 `issue`가 있고 `paths`가 빈다. `created`는 자동 선을
    저장한 입력으로 다시 계산한 생긴 날이다(저장하지 않는다)."""

    line: ChartLine
    anchors: tuple[ChartPoint, ...]
    paths: tuple[LevelPath, ...]
    issue: LineIssue | None
    created: date | None


@dataclass(frozen=True)
class ChartLines:
    context: ChartContext
    lines: tuple[ChartLineView, ...]


@dataclass(frozen=True)
class ConfluenceLine:
    """저장하지 않은 보이는 선(자동 후보 등). `ref`는 응답에서 이 선을 가리키는 이름이다."""

    ref: str
    timeframe: Timeframe
    kind: LineKind
    anchors: tuple[ChartPoint, ...]
    levels: tuple[float, ...] | None = None


@dataclass(frozen=True)
class ConfluenceMember:
    ref: str
    kind: LineKind
    level: float | None
    low: float
    high: float


@dataclass(frozen=True)
class ConfluenceGroup:
    low: float
    high: float
    kinds: tuple[LineKind, ...]
    members: tuple[ConfluenceMember, ...]


@dataclass(frozen=True)
class ConfluenceView:
    context: ChartContext
    tolerance: float
    groups: tuple[ConfluenceGroup, ...]
    caution: str
    rules: tuple[str, ...]
    # 기준점이 지금 계열 밖이라 넣지 못한 저장 선
    skipped_line_ids: tuple[str, ...]
