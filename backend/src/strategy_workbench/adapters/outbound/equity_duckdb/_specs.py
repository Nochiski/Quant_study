"""`EquityDuckdbAdapter` 가 답하는 field_id 선언표 — 원천(`SOURCE_SPECS`)과 필드(`FIELD_SPECS`).

정본은 `database/docs/EQUITY_FIELD_MAP.md` §2 의 42 field_id 판정과 `EQUITY_DESIGN.md` §4-2~§4-6 의
테이블 grain·컬럼이다. 어댑터 본체(`_adapter.py`)는 **이 표를 해석만** 하고 field_id 별 분기를 두지
않는다 — 새 필드는 여기 한 행을 더하는 것으로 끝난다.

읽는 방식은 둘뿐이다(`SourceMode`).

`GRID`  (ticker, session) 격자 위의 일별 행 — `price_daily`·`price_adj_daily`(조정 공백 적용일을
        가린 카탈로그 뷰 `v_adj_close` 로 읽는다, #220) · 격자 3테이블
        `flow_daily`·`short_daily`·`credit_daily`(무상증자 척도 창을 가린 카탈로그 뷰
        `v_credit_balance` 로 읽는다, #249). 랙 n 은 **정확히 n 세션 전 행**이고 그 세션에
        행이 없으면 셀을 내지 않는다(합성 금지 — 재상장 구간 첫날이 직전 구간 값을 물지 않는다).
        격자 3테이블은 `fill_kind`(STRUCT(kind, evidence)) 로 결측 사유를 함께 주고
        (`SourceSpec.kind_expr`), 어댑터는 그것을 `CellKind` 로 옮긴다.
`LATEST` `available_date` 축의 관측 — 재무·컨센서스·의견·배당·자사주·임원지분. 셀 값은 **컷오프
        (as_of 에서 랙 n 세션 전 거래일) 이하의 마지막 관측**이고, `available_date` 는 그 관측의
        공개일이다(세션과 다를 수 있다 — 계약은 `available_date ≤ as_of` 만 요구한다). 관측이
        하나도 없으면 셀을 내지 않는다. 관측 하나를 고르는 규칙(`reduce`)이 grain 을 (축,
        available_date) 로 줄인다 — `pick`(정렬 1행) 또는 `sum`(같은 공개일의 행 합).

축(`SourceAxis`)은 `TICKER` 아니면 `CORP` 다. corp 축 테이블은 티커 컬럼이 없어(법인→티커 1:N,
DESIGN §4-4·§4-5) 어댑터가 `corp_ticker` 로 전개하며, **한 법인의 종류주 티커 전부가 같은 값을
받는다**(재무·지분은 법인 사실이라 옳고, `dividend_event` 는 종류 축을 접으므로 우선주 티커도
보통주 DPS 를 받는다 — FIELD_MAP §2 `event.dividend_per_share` 부분 판정의 근거 ②). `corp_ticker`
는 시점축 없는 현재 스냅샷이라 폐지·티커 재사용 구간에서 대응이 어긋날 수 있다(DESIGN §4-5 6).

랙의 정본은 **`dataset_profile`(S19)의 `recommended_lag_sessions`** 이고, 어댑터가 부팅할 때
읽어 field_id 마다 적용한다. 아래 `SourceSpec.lag_sessions`·`lag_basis` 는 그 표가 없는 루트
(구판·손 픽스처)를 위한 **폴백**일 뿐이며, 폴백이 쓰이면 `list_fields()` 의 `available_date_basis`
에 그 사실이 드러난다. 두 값이 갈리면 대장이 이긴다 — 어댑터가 자기 상수로 PIT 를 우기면
공개 전 값을 내주게 된다(TECH_DEBT §4). 소비자는 질의의 `lag_overrides` 로 필드마다 늘릴 수 있다.

여기 없는 field_id 는 `list_fields()` 밖이고 질의하면 `INVALID_QUERY` 다(mock 폴백 없음). 사용자
사유는 `FIELD_NOT_IN_LEDGER`·`FIELD_NOT_PROVIDED` 한 문장이고, `UNSUPPORTED_FIELDS` 는 field_id
마다 원장 작업 메모를 둔다. 원장이 field_map 으로 선언한 필드는 이 표나 미지원표 중 정확히 한쪽에
있다(`tests/contract/test_equity_field_contract_parity.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from strategy_workbench.domain.equity.facade.research_data import (
    FieldFrequency,
    FieldValueType,
)

PRICE_TABLE = "price_daily"
CALENDAR_TABLE = "trading_calendar"
SPAN_TABLE = "security_span"
UNIVERSE_TABLE = "universe_daily"
PROFILE_TABLE = "dataset_profile"
POLICY_TABLE = "universe_policy"
SECURITY_TABLE = "security"
FACTOR_TABLE = "adj_factor"
CORP_TICKER_TABLE = "corp_ticker"
FIN_TABLE = "fin_std"
DISCLOSURE_TABLE = "disclosure_version"
CONSENSUS_TABLE = "consensus_daily"
OPINION_TABLE = "opinion_daily"
DIVIDEND_TABLE = "dividend_event"
EVENT_TABLE = "corp_event"
HOLDER_TABLE = "holder_daily"
FLOW_TABLE = "flow_daily"
SHORT_TABLE = "short_daily"
CREDIT_TABLE = "credit_daily"

ADJ_TABLE = "price_adj_daily"
CONSENSUS_MACRO = "v_consensus"
FIN_MACRO = "v_fin_latest"
CREDIT_MACRO = "v_credit_balance"
ADJ_MACRO = "v_adj_close"
# 원장이 접지 못한 사건(#369) — 필드 원천이 아니라 백테스트 사건 피드가 읽는다
UNFOLDED_MACRO = "v_unfolded_event"

REQUIRED_TABLES = (CALENDAR_TABLE, SPAN_TABLE, UNIVERSE_TABLE, POLICY_TABLE, PRICE_TABLE)


class SourceMode(Enum):
    GRID = "grid"
    LATEST = "latest"


class SourceAxis(Enum):
    TICKER = "ticker"
    CORP = "corp_code"


class Reduce(Enum):
    NONE = "none"  # GRID — 격자 행이 이미 (축, 세션) 유일
    PICK = "pick"  # (축, available_date) 당 정렬 첫 행
    SUM = "sum"  # (축, available_date) 당 값 합 — 전부 NULL 이면 NULL


@dataclass(frozen=True)
class SourceSpec:
    """필드들이 공유하는 읽는 자리 하나. `relation` 은 테이블 이름 또는 카탈로그 매크로 이름.

    `kind_expr` 은 격자 테이블(S08~S10)의 `fill_kind` STRUCT 에서 결측 사유를 꺼내는 식이다
    (`fill_kind['kind']`). `short_daily` 처럼 한 테이블이 원천마다 `fill_kind` 를 따로 두면
    **읽는 자리도 원천마다 하나**다 — 같은 relation 위의 SourceSpec 두 개가 서로 다른
    `kind_expr` 을 갖는다. None 이면 그 원천에는 결측 사유 축이 없어 셀 종류는 값 유무로만
    갈린다(값 있으면 OBSERVED, 없으면 MISSING).

    `pick_order` 는 두 모드에 다 쓴다 — `LATEST` 는 (축 키, available_date) 당 1행,
    `GRID` 는 (축 키, date) 당 1행을 고른다. GRID 에서 필요한 것은 `flow_daily` 뿐이다
    (grain 에 `src` 가 들어 한 격자 셀에 원천 수만큼 행이 올 수 있다).

    `required_columns` 는 매크로 원천이 선언 밖에서(`row_filter` 등) 읽는 열이다. 매크로는 게시돼
    있어도 옛 카탈로그면 그 열이 없을 수 있어, 어댑터가 부팅 때 확인하고 없으면 이 원천만 뺀다.

    `masked_expr` 은 원장 뷰가 값이 틀려 일부러 가린 행의 표시 식이다(참이면 셀 종류 MASKED, 값은
    NULL). 무엇을 가릴지는 뷰가 정하고 어댑터는 표시만 읽는다 — MASKED 셀은 실행 결측 정책이
    채우지 않는다(#298). 뷰의 가림 표시 열 선언은 원장 `views.MASK_COLUMNS` 이고, 이 배선이 그
    선언과 같은지는 `tests/contract/test_equity_field_contract_parity.py` 가 본다. 부팅 검사가
    카탈로그 열과 이름으로 대조하므로 식이 아니라 열 이름을 쓴다(없으면 `catalog_columns_missing`).
    가림 표시를 둔 원천은 필드를 하나만 낸다 — 팩터 평가기(`_value_spans`)가 가린 칸 경계를 필드마다
    따로 이어, 한 원천의 두 필드를 시점을 달리해 섞는 식은 같은 행의 층 이동을 건너도 결측이 되지
    않는다(필드를 더하려면 평가기를 먼저 원천 단위로 바꾼다, #349 리뷰 P3-3).

    `omitted_is_zero` 는 그 원천의 `src_omitted`(원천이 행을 뺀 칸)가 원장 규약상 "그날 0" 이라는
    선언이다. 참이면 어댑터가 그 칸을 값 0 의 SOURCE_OMITTED_ZERO 로 내고, 거짓이면 MISSING 으로
    접는다(#371). 어느 원천이 참인가의 정본은 FIELD_MAP §1 「결측 어휘」다.
    """

    name: str
    dataset_id: str
    relation: str
    is_macro: bool
    mode: SourceMode
    axis: SourceAxis
    key_column: str
    available_expr: str
    content_expr: str
    reduce: Reduce
    row_filter: str | None
    pick_order: str | None
    lag_sessions: int
    lag_basis: str
    requires: tuple[str, ...]
    frequency: FieldFrequency
    kind_expr: str | None = None
    required_columns: tuple[str, ...] = ()
    masked_expr: str | None = None
    omitted_is_zero: bool = False


@dataclass(frozen=True)
class FieldSpec:
    """어댑터가 답하는 field_id 하나. `expr` 은 `source` 관계 위의 SELECT 식이다."""

    field_id: str
    source: str
    expr: str
    label: str
    unit: str
    value_type: FieldValueType
    description: str
    disclosure_basis: str
    evidence: str
    # 분할·증자 조정 없는 원주가 시계열이면 시점 간 변화를 잴 때 쓸 조정 필드 id(BACKLOG-018).
    # compile 이 필드 계약(`FieldMetadata.adjusted_field_id`)으로 읽어 warning 을 낸다.
    adjusted_field_id: str | None = None
    # 원천 폴백 랙(`SourceSpec.lag_sessions`)과 다른 필드만 적는다. 한 원천 안에서 원장
    # dataset_profile 의 랙이 갈리는 경우다(price 원천의 market_cap·shares_outstanding, 이슈 #246).
    lag_sessions: int | None = None
    lag_basis: str | None = None
    # 값이 행보다 늦게 공개되는 필드만 적는다 — 원천 relation 의 공개일 열 이름(LATEST PICK 원천).
    # 부팅 검사가 카탈로그 열과 이름으로 대조하므로 식은 쓰지 않는다.
    # 행은 원천의 `available_expr` 로 고르고, 이 날이 컷오프보다 늦으면 그 셀은 그때까지 결측이다.
    # 재무 TTM 은 창 안 분기가 정정 재제출로 행보다 늦게 접수되면 그날 완성된다(#238).
    available_expr: str | None = None


# 재무 TTM 의 공개일 열(v_fin_latest, #238) — 창 안 네 분기값 공개일의 max. 흐름 필드가 행 대신
# 이 날부터 보인다. 옛 카탈로그에 이 열이 있는지는 어댑터가 부팅 때 이 선언에서 끌어와 확인한다.
_TTM_INCOME_AVAILABLE = "ttm_income_available_date"
_TTM_CF_AVAILABLE = "ttm_cf_available_date"

# ── 원천 (읽는 자리) ──────────────────────────────────────────────────────────
# 폴백 랙은 원장 dataset_profile(S19) 선언과 같다. 표가 없는 루트(옛 루트·부분 동기화 루트)에서도
# 원장보다 짧게 읽으면 공개 전 값을 조용히 쓰게 된다(silent look-ahead, 이슈 #246). 같은지는
# `tests/contract/test_equity_fallback_lag.py` 가 원장 선언과 대조한다.

_PRICE_LAG_BASIS = (
    "price_daily.available_date = date (S04 available_rule — 가격류 stage lag_known=true, 공표 "
    "시각 미제공이라 세션 종가 확정 시점) → 0 세션"
)
_ADJ_LAG_BASIS = (
    "price_adj_daily.available_date = date (S23 available_rule — fold_date = greatest(apply_date, "
    "available_date) 규약상 접힌 계수는 전부 그날 이전에 공개됐다) → 0 세션"
)
_SHARES_LAG_BASIS = (
    "price_daily.shares_out ← stg_listing_daily(stage lag_known=false) — KRX 일별 마스터 게시 "
    "시각을 모른다 → dataset_profile(S19) 선언과 같은 1 세션"
)
_DART_LAG_BASIS = (
    "available_date = rcept_dt (DART 접수일, basis derived) — 접수 시각 미제공이라 그날 장중에 쓸 "
    "수 있었는지 모른다 → dataset_profile(S19) 선언과 같은 1 세션"
)
_CONSENSUS_LAG_BASIS = (
    "wise available_date = fetched_date(measured), v3 = collected_date(measured) — 관측 시각은 "
    "미측정이라 dataset_profile(S19) 선언과 같은 1 세션(v3 의 +1영업일은 dataset_profile 이 "
    "적용한다)"
)
_OPINION_LAG_BASIS = (
    "opinion_daily.available_date = obs_date — wise 는 measured(fetched_date), v3 는 default + "
    "coverage_degraded(수집 시각 컬럼 없음, DESIGN §4-6) → dataset_profile(S19) 선언과 같은 1 "
    "세션이되 v3 구간은 잰 값이 아니다"
)
_GRID_LAG_BASIS = (
    "available_date = date (basis default) — S08~S10 stage 원천이 전부 lag_known=false 라 공표 "
    "시각을 모른다 → 익일 지식으로 쓴다. dataset_profile(S19) 선언과 같은 1 세션"
)
_CREDIT_LAG_BASIS = (
    "available_date = 잔고 기준일 (basis default) — 공표는 T+2 이나 우리 체인은 T+3 아침에 받는다"
    "(stage lag_known=false, 실입수 observed − date = +3일) → dataset_profile(S19) 선언과 같은 "
    "3 세션(EQUITY_FIELD_MAP DEFECT-E01 정정)"
)

SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        name="price",
        dataset_id=PRICE_TABLE,
        relation=PRICE_TABLE,
        is_macro=False,
        mode=SourceMode.GRID,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="date",
        content_expr="date",
        reduce=Reduce.NONE,
        row_filter=None,
        pick_order=None,
        lag_sessions=0,
        lag_basis=_PRICE_LAG_BASIS,
        requires=(PRICE_TABLE,),
        frequency=FieldFrequency.DAILY,
    ),
    SourceSpec(
        name="adj",
        dataset_id=ADJ_TABLE,
        # 조정 공백 적용일을 가린 수정주가를 원장 뷰에서 읽는다(#220). 가림 판정은 뷰 몫이라 여기
        # 다시 적지 않고, 가린 행(`adj_gap`)은 MASKED 로 내 결측 정책이 채우지 않는다(#298).
        # 카탈로그가 없거나 낡으면 이 원천도 빠진다 — 표로 돌아가 읽으면 가린 공백이 조용히 다시
        # 열린다.
        relation=ADJ_MACRO,
        is_macro=True,
        mode=SourceMode.GRID,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="date",
        reduce=Reduce.NONE,
        row_filter=None,
        pick_order=None,
        lag_sessions=0,
        lag_basis=_ADJ_LAG_BASIS,
        requires=(ADJ_TABLE, FACTOR_TABLE, ADJ_MACRO),
        frequency=FieldFrequency.DAILY,
        masked_expr="adj_gap",
    ),
    SourceSpec(
        name="fin",
        dataset_id=FIN_TABLE,
        relation=FIN_MACRO,
        is_macro=True,
        mode=SourceMode.LATEST,
        axis=SourceAxis.CORP,
        key_column="corp_code",
        available_expr="available_date",
        content_expr="period_end",
        reduce=Reduce.PICK,
        # 옛 기간 정정본이 더 늦은 기간보다 늦게 접수되면 그 행은 고르지 않는다 — 컷오프에서 고를
        # 행은 "공개된 가장 최근 기간" 이고 그 판정은 뷰가 한다(v_fin_latest.period_frontier, #225).
        row_filter="period_frontier",
        # 같은 접수일에 여러 기간이 실리면(정정 일괄 재제출) 최신 기간·최신 보고서 종류를 고른다.
        pick_order="period_end DESC, report_code DESC",
        lag_sessions=1,
        lag_basis=_DART_LAG_BASIS,
        requires=(FIN_TABLE, DISCLOSURE_TABLE, CORP_TICKER_TABLE, FIN_MACRO),
        frequency=FieldFrequency.QUARTERLY,
        # #225 전에 만든 카탈로그의 v_fin_latest 에는 이 열이 없다 — 재생성 전까지 재무만 뺀다.
        required_columns=("period_frontier",),
    ),
    SourceSpec(
        name="consensus_eps",
        dataset_id=CONSENSUS_TABLE,
        relation=CONSENSUS_MACRO,
        is_macro=True,
        mode=SourceMode.LATEST,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="obs_date",
        reduce=Reduce.PICK,
        # 관측 달 이후로 끝나는 회계기간(= FY1 이상)만 남기고 그 중 가장 가까운 기간을 고른다.
        row_filter="metric = 'eps' AND target_period >= strftime(obs_month, '%Y%m')",
        pick_order="target_period ASC, obs_month DESC",
        lag_sessions=1,
        lag_basis=_CONSENSUS_LAG_BASIS,
        requires=(CONSENSUS_TABLE, CONSENSUS_MACRO),
        frequency=FieldFrequency.MONTHLY,
    ),
    SourceSpec(
        name="consensus_revenue",
        dataset_id=CONSENSUS_TABLE,
        relation=CONSENSUS_MACRO,
        is_macro=True,
        mode=SourceMode.LATEST,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="obs_date",
        reduce=Reduce.PICK,
        row_filter="metric = 'revenue' AND target_period >= strftime(obs_month, '%Y%m')",
        pick_order="target_period ASC, obs_month DESC",
        lag_sessions=1,
        lag_basis=_CONSENSUS_LAG_BASIS,
        requires=(CONSENSUS_TABLE, CONSENSUS_MACRO),
        frequency=FieldFrequency.MONTHLY,
    ),
    SourceSpec(
        name="opinion",
        dataset_id=OPINION_TABLE,
        relation=OPINION_TABLE,
        is_macro=False,
        mode=SourceMode.LATEST,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="obs_date",
        reduce=Reduce.PICK,
        row_filter=None,
        # 같은 (ticker, obs_date) 에 wise·v3 가 공존한다(DESIGN §4-6) — 잰 판본(coverage_degraded
        # = FALSE = wise)을 먼저 고르고 동률은 src 사전순. 겹친 구간의 값은 5축 전부 일치했다(P37).
        pick_order="coverage_degraded NULLS LAST, src",
        lag_sessions=1,
        lag_basis=_OPINION_LAG_BASIS,
        requires=(OPINION_TABLE,),
        frequency=FieldFrequency.DAILY,
    ),
    SourceSpec(
        name="dividend",
        dataset_id=DIVIDEND_TABLE,
        relation=DIVIDEND_TABLE,
        is_macro=False,
        mode=SourceMode.LATEST,
        axis=SourceAxis.CORP,
        key_column="corp_code",
        available_expr="available_date",
        content_expr="coalesce(stlm_dt, available_date)",
        reduce=Reduce.PICK,
        row_filter=None,
        # 종류(stock_knd) 축을 접는다 — 값이 있는 행 우선, 최신 사업연도, 동률은 stock_knd 사전순
        # (한글 정렬상 '보통주' 가 '우선주' 앞). 고른 한 값이 그 법인의 전 종류주 티커로 나간다.
        pick_order="(dps_krw IS NULL), bsns_year DESC, reprt_code DESC, stock_knd",
        lag_sessions=1,
        lag_basis=_DART_LAG_BASIS,
        requires=(DIVIDEND_TABLE, CORP_TICKER_TABLE),
        frequency=FieldFrequency.ANNUAL,
    ),
    SourceSpec(
        name="buyback",
        dataset_id=EVENT_TABLE,
        relation=EVENT_TABLE,
        is_macro=False,
        mode=SourceMode.LATEST,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="announce_date",
        reduce=Reduce.SUM,
        row_filter="event_type = 'tsstk_aq'",
        pick_order=None,
        lag_sessions=1,
        lag_basis=_DART_LAG_BASIS,
        requires=(EVENT_TABLE,),
        frequency=FieldFrequency.EVENT,
    ),
    # ── 격자 3테이블 (S08~S10) — `fill_kind` 축을 갖는 GRID 원천 ──────────────
    SourceSpec(
        name="flow",
        dataset_id=FLOW_TABLE,
        relation=FLOW_TABLE,
        is_macro=False,
        mode=SourceMode.GRID,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="date",
        reduce=Reduce.NONE,
        row_filter=None,
        # grain 이 (date, ticker, **src**) 라 한 격자 셀에 원장 행이 둘일 수 있다(상보 결합,
        # DESIGN §4-3). 포트 grain 은 (security, session, field) 하나뿐이므로 **결정적으로
        # 한 행을 고른다** — 키움(ka10060) 우선, 그다음 src 사전순. 키움을 앞세운 근거는
        # ① 13주체 전부를 주는 유일한 원천이고(KIS 는 etc_fnnc·natn·natfor 가 무대응 NULL)
        # ② 커버가 넓다(절단본 kiwoom 22,531행 vs kis 3,925행). 값을 섞지는 않는다 —
        # 고른 한 행의 값이 그대로 나가고, 두 원천이 겹치는 셀은 절단본 0 이며 서버에서도
        # `EG3_flow_daily.n_src_overlap` 이 매 빌드 센다. `src` 자체는 필드로 내지 않는다.
        pick_order="(src IS DISTINCT FROM 'kiwoom'), src",
        lag_sessions=1,
        lag_basis=_GRID_LAG_BASIS,
        requires=(FLOW_TABLE,),
        frequency=FieldFrequency.DAILY,
        kind_expr="fill_kind['kind']",
    ),
    SourceSpec(
        name="short_kiwoom",
        dataset_id=SHORT_TABLE,
        relation=SHORT_TABLE,
        is_macro=False,
        mode=SourceMode.GRID,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="date",
        reduce=Reduce.NONE,
        row_filter=None,
        pick_order=None,  # grain (date, ticker) — 격자 셀당 1행
        lag_sessions=1,
        lag_basis=_GRID_LAG_BASIS,
        requires=(SHORT_TABLE,),
        frequency=FieldFrequency.DAILY,
        kind_expr="fill_kind_short_kiwoom['kind']",
        # 키움 공매도 샤드가 그 종목·그날을 처리하고 행을 뺐으면 그날 공매도가 없었다(FX-3-001)
        omitted_is_zero=True,
    ),
    SourceSpec(
        name="lending_kiwoom",
        dataset_id=SHORT_TABLE,
        relation=SHORT_TABLE,
        is_macro=False,
        mode=SourceMode.GRID,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="date",
        reduce=Reduce.NONE,
        row_filter=None,
        pick_order=None,
        lag_sessions=1,
        lag_basis=_GRID_LAG_BASIS,
        requires=(SHORT_TABLE,),
        frequency=FieldFrequency.DAILY,
        kind_expr="fill_kind_lending_kiwoom['kind']",
    ),
    SourceSpec(
        name="credit",
        dataset_id=CREDIT_TABLE,
        # 무상증자 척도 창을 가린 잔고를 원장 뷰에서 읽는다(#249). 창 판정은 뷰 몫이라 여기
        # 다시 적지 않는다. 카탈로그가 없거나 낡으면 재무·컨센서스처럼 이 원천도 빠진다 — 표로
        # 돌아가 읽으면 가린 창이 조용히 다시 열린다.
        relation=CREDIT_MACRO,
        is_macro=True,
        mode=SourceMode.GRID,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="date",
        reduce=Reduce.NONE,
        row_filter=None,
        pick_order=None,
        lag_sessions=3,
        lag_basis=_CREDIT_LAG_BASIS,
        requires=(CREDIT_TABLE, EVENT_TABLE, CREDIT_MACRO),
        frequency=FieldFrequency.DAILY,
        kind_expr="fill_kind['kind']",
        masked_expr="bonus_window",
    ),
    SourceSpec(
        name="insider",
        dataset_id=HOLDER_TABLE,
        relation=HOLDER_TABLE,
        is_macro=False,
        mode=SourceMode.LATEST,
        axis=SourceAxis.CORP,
        key_column="corp_code",
        available_expr="available_date",
        content_expr="rcept_dt",
        reduce=Reduce.SUM,
        row_filter="src = 'elestock'",
        pick_order=None,
        lag_sessions=1,
        lag_basis=_DART_LAG_BASIS,
        requires=(HOLDER_TABLE, CORP_TICKER_TABLE),
        frequency=FieldFrequency.EVENT,
    ),
)

# ── 필드 ──────────────────────────────────────────────────────────────────────

# 흐름 계정(손익·현금흐름)은 v_fin_latest 의 ttm_* 를 낸다(#212). 원 계정은 보고서 종류가 기간을
# 정해 as-of 최신 관측을 그대로 내면 종목마다 기간이 섞였다. TTM 규칙(연속 4분기가 다 공개된 날
# 부터, 하나라도 비거나 연결·별도·매출 기준이 섞이면 결측, 3개월·연간 값으로 대신하지 않는다)의
# 정본은 원장 뷰 v_fin_latest 이고 어댑터는 컬럼만 고른다.
_FIN_TTM_DESCRIPTION = (
    "최근 4분기 합(TTM)입니다. 네 분기 값이 모두 공개된 날부터 보이고, 한 분기라도 비었거나 "
    "연결·별도 기준이 섞이면 빈 값입니다."
)
_FIN_EVIDENCE = (
    "equity.duckdb v_fin_latest(as_of) ← fin_std(vintage_kind='api_restated', CFS 우선 "
    "fs_div_used) × disclosure_version, 법인→티커는 corp_ticker"
)
_FIN_DISCLOSURE = "DART 정기보고서 접수일(rcept_no 의 rcept_dt) — 정정본 접수번호를 API 가 돌려준다"

# 격자 3테이블(S08~S10) 필드의 빈 셀 문장. 결측 어휘가 셀 종류로 옮겨지는 규칙은
# `SourceSpec.omitted_is_zero` 와 FIELD_MAP §1 「결측 어휘」가 정본이다.
_BLANK_NOT_ZERO = "값이 없는 날은 0 이 아니라 빈 값입니다."

FIELD_SPECS: tuple[FieldSpec, ...] = (
    # ── price_daily (FIELD_MAP §2 price.*) ──────────────────────────────────
    FieldSpec(
        field_id="price.close",
        source="price",
        expr="close",
        label="종가(원주가)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        description=(
            "KRX 종가(원주가)입니다. 분할·증자·병합을 반영하지 않아 사건일에 값이 끊기므로, "
            "수익률·모멘텀·이평·변동성은 수정주가(price.adj_close)로 잽니다. 가격 필터처럼 그날의 "
            "절대 가격이 필요할 때 씁니다."
        ),
        disclosure_basis="정규장 종가 확정 시점",
        evidence="price_daily.close ← stg_price_daily ∪ stg_etf_price_daily (EG20 원주가 불변)",
        adjusted_field_id="price.adj_close",
    ),
    # stage 가 '0' 을 NULL 로 둔 행은 그대로 NULL 이다(원칙 ④, GAP-14).
    FieldSpec(
        field_id="price.open",
        source="price",
        expr="open",
        label="시가(원주가)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        description="KRX 시가(원주가)입니다. 원천이 시가를 주지 않은 날은 0 이 아니라 빈 값입니다.",
        disclosure_basis="정규장 종가 확정 시점",
        evidence="price_daily.open ← stg_price_daily ∪ stg_etf_price_daily (원주가 무수정)",
    ),
    # 분할 구간을 잇는 거래량 시계열은 원장 매크로 v_adj_volume_fwd 축이다(내부 스코프).
    FieldSpec(
        field_id="price.volume",
        source="price",
        expr="volume_shr",
        label="거래량(원거래량)",
        unit="shares",
        value_type=FieldValueType.COUNT,
        description="KRX 거래량(주)입니다. 분할·병합을 반영하지 않은 원거래량입니다.",
        disclosure_basis="정규장 종가 확정 시점",
        evidence="price_daily.volume_shr ← stage 값 무수정 (price_kind='reference' 행은 0)",
    ),
    # 우선주를 합친 법인 시총은 원장 v_firm_mktcap 이 따로 낸다.
    FieldSpec(
        field_id="price.market_cap",
        source="price",
        expr="mktcap_krw",
        label="시가총액",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=(
            "종가 × 그날 KRX 상장주식수입니다. 종목별 값이라 같은 회사 우선주의 시가총액은 더하지 "
            "않습니다."
        ),
        disclosure_basis="정규장 종가 확정 시점 · KRX 상장주식수",
        evidence="price_daily.mktcap_krw = close × shares_out (stage MKTCAP 대조 불일치 0)",
        lag_sessions=1,
        lag_basis=_SHARES_LAG_BASIS,
    ),
    # 정본은 KRX 상장주식수(stg_listing_daily.list_shrs)다. DART 발행주식총수
    # (shares_outstanding.issued_shr)는 검산용이라 이 필드로 내지 않는다(DESIGN §4-5 6).
    FieldSpec(
        field_id="price.shares_outstanding",
        source="price",
        expr="shares_out",
        label="상장주식수",
        unit="shares",
        value_type=FieldValueType.COUNT,
        description=(
            "KRX 상장주식수(ETF 는 상장좌수)입니다. 비상장 종류주나 상장 전 신주가 빠져 DART "
            "발행주식총수와 다를 수 있습니다."
        ),
        disclosure_basis="KRX 일별 상장주식수(그날 원장)",
        evidence="price_daily.shares_out ← stg_listing_daily.list_shrs (listing 행 없으면 NULL)",
        lag_sessions=1,
        lag_basis=_SHARES_LAG_BASIS,
    ),
    FieldSpec(
        field_id="price.trading_value",
        source="price",
        expr="value_krw",
        label="거래대금",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description="KRX 거래대금(원)입니다.",
        disclosure_basis="정규장 종가 확정 시점",
        evidence="price_daily.value_krw ← stg_price_daily ∪ stg_etf_price_daily",
    ),
    # ── 카탈로그 매크로 (e1.19.0 부터 FIELD_MAP §2 지원 — 원장 field_scope=field_map) ──
    # 가림 규칙(원장이 접지 못한 적용일 = MASKED)의 정본은 원장 뷰 v_adj_close 다(#220·#298).
    # 가린 칸을 건너는 창·시점 비교가 결측이 되는 규칙은 팩터 평가기가 소유한다(#315·#337).
    FieldSpec(
        field_id="price.adj_close",
        source="adj",
        expr="adj_close",
        label="조정 종가(전방 조정)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        description=(
            "분할·무상증자·병합을 반영한 수정 종가입니다. 수익률·모멘텀·이평·변동성처럼 가격 "
            "변화를 잴 때 씁니다. 원장이 그날 사건을 반영하지 못한 날은 빈 값이고 결측 처리로도 "
            "채우지 않으며, 그날을 품는 집계와 그날을 사이에 둔 두 시점 비교도 빕니다. 유상증자 "
            "권리락처럼 조정하지 않는 사건은 가격 변화가 그대로 남습니다. 수준은 첫 관측 기준이라 "
            "종목 간 가격 비교에는 쓰지 않습니다."
        ),
        disclosure_basis=(
            "원주가 세션 확정 + 계수 available_date(min(공시 접수일, apply_date 다음 세션))"
        ),
        evidence=(
            "equity.duckdb v_adj_close(as_of) ← price_adj_daily.adj_close(S23 표 = price_daily × "
            "adj_factor × security_span) — 조정 공백 적용일 행만 adj_factor 로 가린다. "
            "카탈로그 매크로 v_adj_price_fwd 는 표와 같은 값을 내는 읽기 경로다"
        ),
    ),
    # ── fin_std (FIELD_MAP §2 financial.*) ───────────────────────────────────
    # 금융업은 표준계정 매출이 없어 원장이 대체 축(revenue_basis)을 쓴다(GAP-01). 이 필드는
    # 값만 내고 기준은 내지 않는다.
    FieldSpec(
        field_id="financial.revenue",
        source="fin",
        expr="ttm_revenue",
        label="매출액(TTM)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=(
            _FIN_TTM_DESCRIPTION
            + " 금융업은 표준 매출 계정이 없어 영업수익 같은 다른 기준의 값이 들어오고, 어느 "
            "기준인지는 싣지 않습니다."
        ),
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
        available_expr=_TTM_INCOME_AVAILABLE,
    ),
    FieldSpec(
        field_id="financial.gross_profit",
        source="fin",
        expr="ttm_gross_profit",
        label="매출총이익(TTM)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=_FIN_TTM_DESCRIPTION,
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
        available_expr=_TTM_INCOME_AVAILABLE,
    ),
    FieldSpec(
        field_id="financial.operating_income",
        source="fin",
        expr="ttm_op_profit",
        label="영업이익(TTM)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=_FIN_TTM_DESCRIPTION,
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
        available_expr=_TTM_INCOME_AVAILABLE,
    ),
    FieldSpec(
        field_id="financial.net_income",
        source="fin",
        expr="ttm_net_income",
        label="당기순이익(TTM)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=_FIN_TTM_DESCRIPTION,
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
        available_expr=_TTM_INCOME_AVAILABLE,
    ),
    # 원장 현금흐름은 보고서 종류와 무관하게 연초누계라(DEFECT-C02) 뷰가 누계 차로 분기값을
    # 만든다. 연초누계 원값은 이 필드로 내지 않는다.
    FieldSpec(
        field_id="financial.operating_cash_flow",
        source="fin",
        expr="ttm_cf_operating",
        label="영업활동현금흐름(TTM)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=(
            _FIN_TTM_DESCRIPTION
            + " 분기값을 누계 차로 만들어 직전 분기 보고서가 없으면 비므로, 손익 항목보다 빈 값이 "
            "많을 수 있습니다."
        ),
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
        available_expr=_TTM_CF_AVAILABLE,
    ),
    FieldSpec(
        field_id="financial.total_assets",
        source="fin",
        expr="total_asset",
        label="자산총계",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description="재무상태표의 자산총계(보고 기간 말 잔액)입니다.",
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    FieldSpec(
        field_id="financial.total_liabilities",
        source="fin",
        expr="total_liab",
        label="부채총계",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description="재무상태표의 부채총계(보고 기간 말 잔액)입니다.",
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    # 지배주주분(equity_owners)은 내부 스코프다.
    FieldSpec(
        field_id="financial.book_equity",
        source="fin",
        expr="total_equity",
        label="자본총계",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description="재무상태표의 자본총계(보고 기간 말 잔액, 지배·비지배 합계)입니다.",
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    # ── consensus_daily · opinion_daily (FIELD_MAP §2 consensus.*) ───────────
    # 12M 선행 합성은 팩터층 몫이다(FIELD_MAP §2). v_consensus 가 관측 달 이후로 끝나는
    # 회계기간 중 가장 가까운 것(FY1)을 고르고, 단위의 정본은 행의 unit 열이다.
    FieldSpec(
        field_id="consensus.forward_eps",
        source="consensus_eps",
        expr="est_mean",
        label="선행 EPS(FY1 컨센서스 평균)",
        unit="KRW",
        # 주당 금액이다. 원장 dataset_profile 의 value_type='amount' 와 같다(#230).
        value_type=FieldValueType.AMOUNT,
        description=(
            "가장 가까운 다음 회계연도(FY1)의 EPS 컨센서스 평균(원)입니다. 12개월 선행 값이 "
            "아닙니다."
        ),
        disclosure_basis="WISE fetched_date(measured) / v3 collected_date(measured)",
        evidence=(
            "equity.duckdb v_consensus(as_of) ← consensus_daily(metric='eps'), 겹치는 달은 먼저 "
            "알 수 있던 한 행으로 접힌다"
        ),
    ),
    # 스케일 변환은 소비자 몫이다(FIELD_MAP §2 S17 표). FY1 선택은 forward_eps 와 같다.
    FieldSpec(
        field_id="consensus.forward_sales",
        source="consensus_revenue",
        expr="est_mean",
        label="선행 매출액(FY1 컨센서스 평균)",
        unit="억원",
        value_type=FieldValueType.AMOUNT,
        description=(
            "가장 가까운 다음 회계연도(FY1)의 매출액 컨센서스 평균입니다. 단위가 원이 아니라 "
            "억원입니다."
        ),
        disclosure_basis="WISE fetched_date(measured) / v3 collected_date(measured)",
        evidence="equity.duckdb v_consensus(as_of) ← consensus_daily(metric='revenue')",
    ),
    # 원장이 min·max 만 준다. wise 구간에만 있고 겹치는 달에 v_consensus 가 v3 를 고르면
    # 결측이다.
    FieldSpec(
        field_id="consensus.eps_dispersion",
        source="consensus_eps",
        expr="est_max - est_min",
        label="EPS 추정치 범위(max − min)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=(
            "FY1 EPS 추정치의 범위(최댓값 − 최솟값, 원)입니다. 표준편차가 아니고, 한 원천 "
            "구간에는 값이 없어 빌 수 있습니다."
        ),
        disclosure_basis="WISE fetched_date(measured)",
        evidence="equity.duckdb v_consensus(as_of).est_max − .est_min (metric='eps')",
    ),
    # 같은 (ticker, obs_date) 의 두 원천 중 잰 판본(wise)을 먼저 고르고, 없는 날은 v3
    # (coverage_degraded — 수집 시각을 원장이 주지 않는다)를 쓴다.
    FieldSpec(
        field_id="consensus.target_price",
        source="opinion",
        expr="target_price_krw",
        label="목표주가(컨센서스)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        description=(
            "애널리스트 목표주가 컨센서스(원)입니다. 보통주에만 있고 우선주·ETF 에는 없습니다."
        ),
        disclosure_basis="WISE 화면 수집일(measured) / v3 관측일(default, degraded)",
        evidence="opinion_daily.target_price_krw ← stg_analyst_summary ∪ stg_v3_analyst_opinions",
    ),
    # WISE 가 이미 접은 점수다 — 원장은 임계로 등급을 굽지 않는다(원칙 ①).
    FieldSpec(
        field_id="consensus.recommendation",
        source="opinion",
        expr="opinion_score",
        label="투자의견 점수(컨센서스)",
        unit="score",
        value_type=FieldValueType.RATIO,
        description=(
            "애널리스트 투자의견 컨센서스 점수입니다. 원천이 매긴 점수 그대로이고 보통주에만 "
            "있습니다."
        ),
        disclosure_basis="WISE 화면 수집일(measured) / v3 관측일(default, degraded)",
        evidence="opinion_daily.opinion_score ← stg_analyst_summary ∪ stg_v3_analyst_opinions",
    ),
    # consensus_daily 에는 n_analyst 가 없어 애널리스트 수 축은 opinion_daily 뿐이다(DESIGN §4-6).
    FieldSpec(
        field_id="consensus.analyst_count",
        source="opinion",
        expr="analyst_count",
        label="추정기관 수",
        unit="count",
        value_type=FieldValueType.COUNT,
        description="컨센서스에 참여한 추정기관 수입니다. 보통주에만 있습니다.",
        disclosure_basis="WISE 화면 수집일(measured) / v3 관측일(default, degraded)",
        evidence="opinion_daily.analyst_count ← stg_analyst_summary ∪ stg_v3_analyst_opinions",
    ),
    # ── flow_daily (FIELD_MAP §2 flow.*) ─────────────────────────────────────
    FieldSpec(
        field_id="flow.foreign_net_buy",
        source="flow",
        expr="frgnr_invsr_krw",
        label="외국인 순매수(대금)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=("외국인 순매수 대금(원)입니다. " + _BLANK_NOT_ZERO),
        disclosure_basis="원장 날짜(공표 시각 미제공, basis default)",
        evidence=(
            "flow_daily.frgnr_invsr_krw ← stg_flow_daily_kiwoom(ka10060) ∪ "
            "stg_flow_split_daily(KIS frgn_ntby_tr_pbmn_krw), 같은 셀에 둘 다 있으면 키움"
        ),
    ),
    # GAP-03 — `orgn` 은 원장의 합계 컬럼이라 기관 7주체 합과 다르고 12주체 항등식
    # (EG3-P06)에서도 빠진다.
    FieldSpec(
        field_id="flow.institution_net_buy",
        source="flow",
        expr="orgn_krw",
        label="기관 순매수(대금)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=(
            "기관 순매수 대금(원)입니다. 원장의 기관 합계 값이라 기관 세부 주체를 더한 값과 다를 "
            "수 있습니다. " + _BLANK_NOT_ZERO
        ),
        disclosure_basis="원장 날짜(공표 시각 미제공, basis default)",
        evidence="flow_daily.orgn_krw ← 키움 orgn_krw ∪ KIS orgn_ntby_tr_pbmn_krw",
    ),
    FieldSpec(
        field_id="flow.retail_net_buy",
        source="flow",
        expr="ind_invsr_krw",
        label="개인 순매수(대금)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=("개인 순매수 대금(원)입니다. " + _BLANK_NOT_ZERO),
        disclosure_basis="원장 날짜(공표 시각 미제공, basis default)",
        evidence="flow_daily.ind_invsr_krw ← 키움 ind_invsr_krw ∪ KIS prsn_ntby_tr_pbmn_krw",
    ),
    # ── short_daily (FIELD_MAP §2 short.*) ───────────────────────────────────
    # 원천은 키움(ka10014) 하나로 고정한다 — 표는 두 원천을 나란히 두고, 키움이 커버가
    # 넓다(절단본 18,265 대 KIS 3,891). 폴백 병합은 원천을 섞으므로 하지 않는다(DESIGN §4-3).
    FieldSpec(
        field_id="short.short_sale_value",
        source="short_kiwoom",
        expr="short_value_kiwoom_krw",
        label="공매도 거래대금(키움)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=(
            "공매도 거래대금(원)입니다. 한 원천(키움)만 써서 시계열에 원천이 섞이지 않습니다. "
            "원천이 뺀 날은 공매도가 없던 날이라 0 이고, 수집하지 못한 날은 빈 값입니다."
        ),
        disclosure_basis="원장 날짜(공표 시각 미제공, basis default)",
        evidence=(
            "short_daily.short_value_kiwoom_krw ← stg_short_daily_kiwoom.shrts_trde_prica_krw "
            "(unit_scale=1e3), 결측 사유는 fill_kind_short_kiwoom"
        ),
    ),
    # GAP-04 종결(S09-2)로 원천이 키움(ka20068)이다. 단위는 원장 항등식으로 주 단위를
    # 확정했고(금액축 ÷ 잔고 = 종가), 폴백 병합은 하지 않는다. KIS 축은 별도 열이다.
    FieldSpec(
        field_id="short.borrowed_quantity",
        source="lending_kiwoom",
        expr="lending_balance_kiwoom_shr",
        label="대차잔고(주식수, 키움)",
        unit="shares",
        value_type=FieldValueType.COUNT,
        description=(
            "대차잔고(주)입니다. 한 원천(키움)만 쓰고, 원천이 준 음수 잔고도 그대로 둡니다. "
            + _BLANK_NOT_ZERO
        ),
        disclosure_basis="원장 날짜(공표 시각 미제공, basis default)",
        evidence=(
            "short_daily.lending_balance_kiwoom_shr ← stg_lending_daily.rmnd, 결측 "
            "사유는 fill_kind_lending_kiwoom(금액축 lending_balance_kiwoom_krw 는 내부 스코프)"
        ),
    ),
    # ── credit_daily (FIELD_MAP §2 credit.*) ─────────────────────────────────
    # 주식수 축만 낸다 — 금액축은 단위 미상이다(amt_basis='unknown'). 척도 창 가림은 원장 뷰
    # v_credit_balance(#249), 상장주식수를 넘는 행은 S10 이 격리해 MISSING 이다(DESIGN §9 결정 9).
    FieldSpec(
        field_id="credit.margin_balance",
        source="credit",
        expr="whol_loan_rmnd_stcn_shr",
        label="신용융자 잔고(주식수)",
        unit="shares",
        value_type=FieldValueType.COUNT,
        description=(
            "신용융자 잔고(주)입니다. 무상증자 권리락 뒤 한동안은 잔고 단위가 섞여 빈 값이고 결측 "
            "처리로도 채우지 않습니다(공시가 늦은 일부 사건은 가리지 못합니다). 잔고가 "
            "상장주식수를 넘는 이상 행도 빈 값입니다. " + _BLANK_NOT_ZERO
        ),
        disclosure_basis=(
            "원장 날짜(basis default) — 신용잔고는 T+2 공표이고 우리 체인은 T+3 아침에 받는다. "
            "그래서 dataset_profile(S19)이 3 세션 뒤부터 쓰게 정한다"
            "(EQUITY_FIELD_MAP DEFECT-E01 정정)"
        ),
        evidence=(
            "equity.duckdb v_credit_balance(as_of) ← credit_daily.whol_loan_rmnd_stcn_shr ← "
            "stg_credit_daily(KIS 신용잔고) 무수정, 무상증자 척도 창만 corp_event(bonus)로 가린다"
        ),
    ),
    # ── 사건 (FIELD_MAP §2 event.*) ──────────────────────────────────────────
    # 연 1회(reprt_code='11011') 값이고 종류(stock_knd) 축을 접는다. 락일이 없어 TR·배당
    # 재투자 팩터의 재료가 아니다(DESIGN §4-5 5·§11).
    FieldSpec(
        field_id="event.dividend_per_share",
        source="dividend",
        expr="dps_krw",
        label="주당 현금배당금(최근 사업보고서)",
        unit="KRW",
        # 주당 금액이다. 원장 dataset_profile 의 value_type='amount' 와 같다(#230).
        value_type=FieldValueType.AMOUNT,
        description=(
            "최근 사업보고서의 주당 현금배당금(원)입니다. 배당락일·기준일이 없어 보고서 "
            "접수일부터 보이므로 배당 재투자 계산에는 쓸 수 없습니다. 우선주도 보통주 배당금을 "
            "받습니다."
        ),
        disclosure_basis="DART 사업보고서 접수일(결산일 + 3~8개월)",
        evidence="dividend_event.dps_krw ← stg_dividend(se wide 전개), 법인→티커는 corp_ticker",
    ),
    # 사업보고서 확정치(treasury_stock)와는 축도 시점도 다르다. 창·감쇠는 팩터층 몫이다.
    FieldSpec(
        field_id="event.buyback_amount",
        source="buyback",
        expr="sum(amount_krw)",
        label="자기주식 취득 결정 금액(최근 공시)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        description=(
            "최근 자기주식 취득 결정 공시 금액(원)이고 같은 날 공시는 합합니다. 다음 공시 전까지 "
            "같은 값이 이어집니다."
        ),
        disclosure_basis="DART 주요사항보고 접수일(announce_date)",
        evidence="corp_event.amount_krw (event_type='tsstk_aq', 서버 1,951건)",
    ),
    # DART 소유보고 API 가 롤링 2년 창만 주고 재수집이 불가능하다(DART_DESIGN P3e).
    FieldSpec(
        field_id="event.insider_net_buy",
        source="insider",
        expr="sum(qty_change_shr)",
        label="임원·주요주주 지분 증감(최근 보고, 주식수)",
        unit="shares",
        value_type=FieldValueType.COUNT,
        description=(
            "임원·주요주주 지분 증감(주, 최근 보고)입니다. 원천이 최근 2년치만 줘서 그 전 기간은 "
            "비어 있습니다. 같은 날 여러 보고는 합하고 다음 보고 전까지 값이 이어집니다."
        ),
        disclosure_basis="DART 소유보고 접수일(rcept_dt)",
        evidence="holder_daily.qty_change_shr (src='elestock'), 법인→티커는 corp_ticker",
    ),
)

# 아래 필드의 사용자 대면 사유 문장 — compile 진단과 질의 거절이 싣는다(#316). 원장
# `dataset_profile` 에 그 field_id 가 있는지로 둘을 가른다(#373): 원장에 있는데 이 어댑터가 내주지
# 않는 필드도 있다.
FIELD_NOT_IN_LEDGER = "원장이 이 필드를 싣지 않는다"
FIELD_NOT_PROVIDED = "원장에는 있지만 이 연결은 이 필드를 내주지 않는다"

# 어댑터가 내지 않는 FIELD_MAP §2 필드 — field_id → 사유 메모. `list_fields()` 밖이고(mock 폴백
# 금지, DESIGN §7) 메모는 원장 작업 기록이라 사용자에게 싣지 않는다. "미배선"은 원장에는 있는데
# 선언표에 아직 없는 필드다(#421).
UNSUPPORTED_FIELDS: dict[str, str] = {
    "benchmark.close": (
        "미지원(현 설계) — index_daily 는 security 축이 아니다. 벤치마크는 예약 접두 `idx:` 로 "
        "받되 S21 은 내지 않는다(GAP-09, FIELD_MAP §2)"
    ),
    "flow.foreign_ownership": (
        "미배선 — 원장 flow_daily.foreign_wght_pct(퍼센트, 키움 ka10008)로 있다(#421)"
    ),
    "flow.foreign_limit_exhaustion": (
        "미배선 — 원장 flow_daily.foreign_limit_exh_pct(퍼센트, 키움 ka10008)로 있다(#421)"
    ),
    "flow.pension_net_buy": (
        "미배선 — 원장 flow_daily.pension_net_buy_kiwoom_krw(원, 키움만)로 있다(#421)"
    ),
    "flow.block_buy": "미지원 — 대량매매 미수집(FACTORS.md §9)",
    "flow.block_sell": "미지원 — 대량매매 미수집(FACTORS.md §9)",
    "short.short_sale_volume": (
        "미배선 — 원장 short_daily.short_volume_kiwoom_shr(주, 키움)로 있다(#421)"
    ),
    "short.short_balance_ratio": (
        "미지원 — 분모가 다른 테이블(price_daily.shares_out)이라 셀 하나로 굽지 않는다. "
        "게다가 short_daily 가 주는 것은 잔고가 아니라 거래량이다(F45 취득 불가, 라벨 정정 필요)"
    ),
    "credit.net_buy": (
        "미지원(S10 판정 09-06) — stg_credit_daily 39컬럼에 순매수 축이 없다. 유일한 후보 "
        "`신규 − 상환` 은 순매수가 아니라 잔고 증감의 구성요소인데 실제 증감과도 맞지 않는다"
        "(절단본 융자 17,364/24,711 · 대주 24,672/24,711, 신규·상환 음수 6행)"
    ),
    "credit.collateral_value": "미지원 — 원천 없음",
    "credit.loan_value": "미지원 — 원천 없음",
    "credit.forced_liquidation": "미지원 — 원천 없음",
    "event.earnings_surprise": "미지원 — 잠정실적 공시일 원천이 없다(FACTORS.md §9)",
    "event.index_membership_change": "미지원 — 지수 구성종목 PIT 없음(WORKFLOW §6)",
    "event.disclosure_sentiment": "미지원 — 텍스트층(문서층 P4)",
    "classification.sector": (
        "미지원 — corp.induty_code 는 시점축 없는 **현재값 라벨**이라 과거 세션에 붙이면 "
        "look-ahead 다. DESIGN §7 이 sector_id=None 을 규약으로 못박았다(GAP-07)"
    ),
}

SOURCE_BY_NAME: dict[str, SourceSpec] = {spec.name: spec for spec in SOURCE_SPECS}
FIELD_BY_ID: dict[str, FieldSpec] = {spec.field_id: spec for spec in FIELD_SPECS}


def _reject_unread_declarations(
    source_specs: tuple[SourceSpec, ...], field_specs: tuple[FieldSpec, ...]
) -> None:
    """선언한 식을 그 원천의 질의가 읽지 않으면 모듈을 올릴 때 막는다.

    선언의 모양 규칙은 여기 한 곳에 둔다. 읽히지 않는 선언은 조용히 무시되고, 계약 테스트는
    선언만 본다.
    - 필드 공개일 열(`FieldSpec.available_expr`)은 LATEST 원천의 PICK 질의(`_latest`)만 읽는다.
      GRID 는 조용히 행 공개일로 보이고(look-ahead) SUM 은 집계 질의가 깨진다(#300 리뷰 P3-3·r2
      P3-1).
    - 가림 표시(`SourceSpec.masked_expr`)는 격자 질의(`_grid`)만 읽는다. LATEST 원천에 두면 셀이
      MASKED 가 되지 않아 결측 정책이 가린 셀을 다시 채운다(#311 리뷰 P3-3).
    - 원천 생략 0(`SourceSpec.omitted_is_zero`)은 결측 사유 축(`kind_expr`)이 있어야 읽힌다. 없으면
      계약 테스트는 mock 과 선언이 같다고 보는데 어댑터는 그 칸을 MISSING 으로 낸다(#371).
    """
    by_name = {spec.name: spec for spec in source_specs}
    misplaced = [
        spec.field_id
        for spec in field_specs
        if spec.available_expr is not None
        and (by_name[spec.source].mode, by_name[spec.source].reduce)
        != (SourceMode.LATEST, Reduce.PICK)
    ]
    if misplaced:
        raise ValueError(f"available_expr needs a LATEST PICK source — fields={misplaced}")
    unread = [
        spec.name
        for spec in source_specs
        if spec.masked_expr is not None and spec.mode is not SourceMode.GRID
    ]
    if unread:
        raise ValueError(f"masked_expr needs a GRID source — sources={unread}")
    unread = [spec.name for spec in source_specs if spec.omitted_is_zero and spec.kind_expr is None]
    if unread:
        raise ValueError(f"omitted_is_zero needs a kind_expr — sources={unread}")


_reject_unread_declarations(SOURCE_SPECS, FIELD_SPECS)
