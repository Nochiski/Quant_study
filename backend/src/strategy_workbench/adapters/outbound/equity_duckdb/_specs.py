"""`EquityDuckdbAdapter` 가 답하는 field_id 선언표 — 원천(`SOURCE_SPECS`)과 필드(`FIELD_SPECS`).

정본은 `database/docs/EQUITY_FIELD_MAP.md` §2 의 42 field_id 판정과 `EQUITY_DESIGN.md` §4-2~§4-6 의
테이블 grain·컬럼이다. 어댑터 본체(`_adapter.py`)는 **이 표를 해석만** 하고 field_id 별 분기를 두지
않는다 — 새 필드는 여기 한 행을 더하는 것으로 끝난다.

읽는 방식은 둘뿐이다(`SourceMode`).

`GRID`  (ticker, session) 격자 위의 일별 행 — `price_daily`·`price_adj_daily` · 격자 3테이블
        `flow_daily`·`short_daily`·`credit_daily`. 랙 n 은 **정확히 n 세션 전 행**이고 그 세션에
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

랙은 컬럼군별 상수(`lag_sessions`)다 — `dataset_profile`(S19)이 아직 없어서다. 값과 근거는
`views.py`(`PRICE_LAG_SESSIONS`·`FACTOR_LAG_SESSIONS`·`CONSENSUS_LAG_SESSIONS`·`FIN_LAG_SESSIONS`)
와 각 `rules_s*.py` 의 `available_rule` 을 옮긴 것이고 `lag_basis` 에 근거를 적었다. S19 가 오면
프로필 값으로 갈아 끼운다. 소비자는 질의의 `lag_overrides` 로 필드마다 늘릴 수 있다.

여기 없는 field_id(FIELD_MAP 42 중 13)는 `list_fields()` 밖이고 질의하면 `INVALID_QUERY` 다
(mock 폴백 없음) — 사유는 `UNSUPPORTED_FIELDS` 가 field_id 마다 적어 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from strategy_workbench.domain.equity.facade.research_data import FieldValueType

PRICE_TABLE = "price_daily"
CALENDAR_TABLE = "trading_calendar"
SPAN_TABLE = "security_span"
UNIVERSE_TABLE = "universe_daily"
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
    frequency: str
    kind_expr: str | None = None


@dataclass(frozen=True)
class FieldSpec:
    """어댑터가 답하는 field_id 하나. `expr` 은 `source` 관계 위의 SELECT 식이다."""

    field_id: str
    source: str
    expr: str
    label: str
    unit: str
    value_type: FieldValueType
    verdict: str  # FIELD_MAP §2 판정 — 지원 / 부분 / (equity 내부 스코프)
    description: str
    disclosure_basis: str
    evidence: str


# ── 원천 (읽는 자리) ──────────────────────────────────────────────────────────

_PRICE_LAG_BASIS = (
    "price_daily.available_date = date (S04 available_rule — 가격류 stage lag_known=true, 공표 "
    "시각 미제공이라 세션 종가 확정 시점) → 0 세션"
)
_ADJ_LAG_BASIS = (
    "price_adj_daily.available_date = date (S23 available_rule — fold_date = greatest(apply_date, "
    "available_date) 규약상 접힌 계수는 전부 그날 이전에 공개됐다) → 0 세션"
)
_DART_LAG_BASIS = (
    "available_date = rcept_dt (DART 접수일, basis derived) — 접수일 자체가 공개일이라 세션 랙을 "
    "더하면 이중 계산이다 → 0 세션"
)
_CONSENSUS_LAG_BASIS = (
    "views.CONSENSUS_LAG_SESSIONS=0 — wise available_date = fetched_date(measured), v3 = "
    "collected_date(measured)로 둘 다 우리가 실제로 관측한 날이다"
)
_OPINION_LAG_BASIS = (
    "opinion_daily.available_date = obs_date — wise 는 measured(fetched_date), v3 는 default + "
    "coverage_degraded(수집 시각 컬럼 없음, DESIGN §4-6) → 0 세션이되 v3 구간은 잰 값이 아니다"
)
_GRID_LAG_BASIS = (
    "available_date = date (basis default) — S08~S10 stage 원천이 전부 lag_known=false 라 공표 "
    "시각을 모른다. 랙 0 은 '원장 날짜가 곧 그날 알 수 있던 날' 이라는 **가정**이고 신용잔고는 "
    "실제로 T+1 공표다 → 세션 랙 확정은 dataset_profile(S19) 몫이니 그때까지 소비자가 "
    "lag_overrides 로 물려 써야 한다(FIELD_MAP §3 S09 주석)"
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
        frequency="daily",
    ),
    SourceSpec(
        name="adj",
        dataset_id=ADJ_TABLE,
        relation=ADJ_TABLE,
        is_macro=False,
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
        requires=(ADJ_TABLE,),
        frequency="daily",
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
        row_filter=None,
        # 같은 접수일에 여러 기간이 실리면(정정 일괄 재제출) 최신 기간·최신 보고서 종류를 고른다.
        pick_order="period_end DESC, report_code DESC",
        lag_sessions=0,
        lag_basis=_DART_LAG_BASIS,
        requires=(FIN_TABLE, DISCLOSURE_TABLE, CORP_TICKER_TABLE, FIN_MACRO),
        frequency="quarterly",
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
        lag_sessions=0,
        lag_basis=_CONSENSUS_LAG_BASIS,
        requires=(CONSENSUS_TABLE, CONSENSUS_MACRO),
        frequency="monthly",
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
        lag_sessions=0,
        lag_basis=_CONSENSUS_LAG_BASIS,
        requires=(CONSENSUS_TABLE, CONSENSUS_MACRO),
        frequency="monthly",
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
        lag_sessions=0,
        lag_basis=_OPINION_LAG_BASIS,
        requires=(OPINION_TABLE,),
        frequency="daily",
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
        lag_sessions=0,
        lag_basis=_DART_LAG_BASIS,
        requires=(DIVIDEND_TABLE, CORP_TICKER_TABLE),
        frequency="annual",
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
        lag_sessions=0,
        lag_basis=_DART_LAG_BASIS,
        requires=(EVENT_TABLE,),
        frequency="event",
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
        lag_sessions=0,
        lag_basis=_GRID_LAG_BASIS,
        requires=(FLOW_TABLE,),
        frequency="daily",
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
        lag_sessions=0,
        lag_basis=_GRID_LAG_BASIS,
        requires=(SHORT_TABLE,),
        frequency="daily",
        kind_expr="fill_kind_short_kiwoom['kind']",
    ),
    SourceSpec(
        name="lending_kis",
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
        lag_sessions=0,
        lag_basis=_GRID_LAG_BASIS,
        requires=(SHORT_TABLE,),
        frequency="daily",
        kind_expr="fill_kind_loan_kis['kind']",
    ),
    SourceSpec(
        name="credit",
        dataset_id=CREDIT_TABLE,
        relation=CREDIT_TABLE,
        is_macro=False,
        mode=SourceMode.GRID,
        axis=SourceAxis.TICKER,
        key_column="ticker",
        available_expr="available_date",
        content_expr="date",
        reduce=Reduce.NONE,
        row_filter=None,
        pick_order=None,
        lag_sessions=0,
        lag_basis=_GRID_LAG_BASIS,
        requires=(CREDIT_TABLE,),
        frequency="daily",
        kind_expr="fill_kind['kind']",
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
        lag_sessions=0,
        lag_basis=_DART_LAG_BASIS,
        requires=(HOLDER_TABLE, CORP_TICKER_TABLE),
        frequency="event",
    ),
)

# ── 필드 ──────────────────────────────────────────────────────────────────────

_FIN_PERIOD_NOTE = (
    "기간 어휘는 report_code 가 정한다 — 11011 은 12개월, 11012·11013·11014 는 3개월이다"
    "(DEFECT-C02). 즉 as-of 최신 관측이 분기면 3개월 값, 사업보고서면 12개월 값이라 시계열이 "
    "기간을 섞는다. TTM 합성은 팩터층 몫이고 v_fin_latest 가 ttm_* 를 따로 낸다"
)
_FIN_EVIDENCE = (
    "equity.duckdb v_fin_latest(as_of) ← fin_std(vintage_kind='api_restated', CFS 우선 "
    "fs_div_used) × disclosure_version, 법인→티커는 corp_ticker"
)
_FIN_DISCLOSURE = "DART 정기보고서 접수일(rcept_no 의 rcept_dt) — 정정본 접수번호를 API 가 돌려준다"

# 격자 3테이블(S08~S10)의 결측 어휘를 소비층으로 옮길 때 접히는 축 — 프로필 description 에 그대로
# 실어 소비자가 "왜 src_omitted 가 안 보이나" 를 코드가 아니라 카탈로그에서 읽게 한다.
_FILL_KIND_NOTE = (
    "값 없는 셀은 **0 이 아니라 NULL** 이고 이유는 `fill_kind` 가 나른다(DESIGN §9 결정 8). "
    "셀 종류 대응은 measured→OBSERVED · not_collected→NOT_COLLECTED · empty_response→MISSING "
    "이고, **`src_omitted` 는 SOURCE_OMITTED_ZERO 가 아니라 MISSING 으로 접힌다** — 워크벤치 "
    "도메인이 SOURCE_OMITTED_ZERO 셀에 값을 요구하는데(`RawFieldValue.__post_init__`) equity 는 "
    "그 자리를 NULL 로 두기로 했기 때문이다. 그래서 '0 으로 읽어도 되는 결측' 이라는 라벨은 "
    "S19 `dataset_profile` 이 별도 축으로 실을 때까지 소비층에 도달하지 않는다"
)

FIELD_SPECS: tuple[FieldSpec, ...] = (
    # ── price_daily (FIELD_MAP §2 price.*) ──────────────────────────────────
    FieldSpec(
        field_id="price.close",
        source="price",
        expr="close",
        label="종가(원주가)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        verdict="부분",
        description="KRX 원주가 — 분할·증자 조정 없음(원칙 ②). 조정가는 price.adj_close.",
        disclosure_basis="정규장 종가 확정 시점",
        evidence="price_daily.close ← stg_price_daily ∪ stg_etf_price_daily (EG20 원주가 불변)",
    ),
    FieldSpec(
        field_id="price.open",
        source="price",
        expr="open",
        label="시가(원주가)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        verdict="부분",
        description=(
            "KRX 원주가 시가. stage 가 '0' 을 NULL 로 둔 행은 그대로 NULL 이다(원칙 ④) — 값이 "
            "없는 것과 0 을 섞지 않는다(GAP-14)."
        ),
        disclosure_basis="정규장 종가 확정 시점",
        evidence="price_daily.open ← stg_price_daily ∪ stg_etf_price_daily (원주가 무수정)",
    ),
    FieldSpec(
        field_id="price.volume",
        source="price",
        expr="volume_shr",
        label="거래량(원거래량)",
        unit="shares",
        value_type=FieldValueType.COUNT,
        verdict="지원",
        description=(
            "KRX 원거래량 — **조정하지 않는다**. 분할 구간 시계열이 필요하면 팩터층이 "
            "v_adj_volume_fwd 축으로 바꾼다(equity 내부 스코프)."
        ),
        disclosure_basis="정규장 종가 확정 시점",
        evidence="price_daily.volume_shr ← stage 값 무수정 (price_kind='reference' 행은 0)",
    ),
    FieldSpec(
        field_id="price.market_cap",
        source="price",
        expr="mktcap_krw",
        label="시가총액",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="지원",
        description=(
            "원주가 × KRX 상장주식수(같은 날 listing). 우선주 합산 아님(v_firm_mktcap 별도)."
        ),
        disclosure_basis="정규장 종가 확정 시점 · KRX 상장주식수",
        evidence="price_daily.mktcap_krw = close × shares_out (stage MKTCAP 대조 불일치 0)",
    ),
    FieldSpec(
        field_id="price.shares_outstanding",
        source="price",
        expr="shares_out",
        label="상장주식수",
        unit="shares",
        value_type=FieldValueType.COUNT,
        verdict="지원",
        description=(
            "정본은 KRX 상장주식수다(stg_listing_daily.list_shrs · ETF 는 상장좌수). DART "
            "발행주식총수(shares_outstanding.issued_shr)는 검산·보조이고 이 필드로 나가지 "
            "않는다 — 비상장 종류주·신주 상장 전 구간에서 둘은 갈린다(DESIGN §4-5 6)."
        ),
        disclosure_basis="KRX 일별 상장주식수(그날 원장)",
        evidence="price_daily.shares_out ← stg_listing_daily.list_shrs (listing 행 없으면 NULL)",
    ),
    FieldSpec(
        field_id="price.trading_value",
        source="price",
        expr="value_krw",
        label="거래대금",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="지원",
        description="KRX 거래대금 — stage 값 무수정.",
        disclosure_basis="정규장 종가 확정 시점",
        evidence="price_daily.value_krw ← stg_price_daily ∪ stg_etf_price_daily",
    ),
    # ── 카탈로그 매크로 (equity 내부 스코프 — FIELD_MAP 42 밖) ────────────────
    FieldSpec(
        field_id="price.adj_close",
        source="adj",
        expr="adj_close",
        label="조정 종가(전방 조정)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        verdict="equity 내부 스코프",
        description=(
            "원주가 × 그날까지 공개·적용된 계수(adj_factor factor_ok 행, apply_date 축)의 누적 "
            "share_factor. 첫 관측 수준 고정, 사건 뒤 가격을 올린다 — (security, date) 의 순수 "
            "함수라 창·as_of 에 무관(완전 PIT)."
        ),
        disclosure_basis=(
            "원주가 세션 확정 + 계수 available_date(min(공시 접수일, apply_date 다음 세션))"
        ),
        evidence=(
            "price_adj_daily.adj_close ← price_daily × adj_factor × security_span (S23 표). "
            "카탈로그 매크로 v_adj_price_fwd 는 같은 값을 내는 읽기 경로일 뿐이고, 이 필드는 "
            "표를 직접 읽으므로 카탈로그가 낡거나 없어도 살아 있다"
        ),
    ),
    # ── fin_std (FIELD_MAP §2 financial.*) ───────────────────────────────────
    FieldSpec(
        field_id="financial.revenue",
        source="fin",
        expr="revenue",
        label="매출액",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="부분",
        description=(
            f"{_FIN_PERIOD_NOTE}. 금융업 470사는 표준계정 매출이 없어 대체 축을 쓴다"
            "(revenue_basis ∈ standard·banking_gross·insurance_gross·consensus·unavailable, "
            "GAP-01) — 이 필드는 값만 내고 basis 는 내지 않는다."
        ),
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    FieldSpec(
        field_id="financial.gross_profit",
        source="fin",
        expr="gross_profit",
        label="매출총이익",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="지원",
        description=(
            f"{_FIN_PERIOD_NOTE}. 절단본 커버율 0.945, 삼성전자 2018 111,377,004백만원 = "
            "매출 − 매출원가 원 단위 일치(DESIGN §10 P30)."
        ),
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    FieldSpec(
        field_id="financial.operating_income",
        source="fin",
        expr="op_profit",
        label="영업이익",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="지원",
        description=_FIN_PERIOD_NOTE,
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    FieldSpec(
        field_id="financial.net_income",
        source="fin",
        expr="net_income",
        label="당기순이익",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="지원",
        description=_FIN_PERIOD_NOTE,
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    FieldSpec(
        field_id="financial.operating_cash_flow",
        source="fin",
        expr="cf_operating_ytd",
        label="영업활동현금흐름(연초누계)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="부분",
        description=(
            "현금흐름은 보고서 종류와 무관하게 **연초누계**다(DEFECT-C02). 분기 축(cf_operating_q "
            "= 자기 누계 − 직전 보고서 누계)은 직전 판본이 없으면 NULL 이라 축을 고르는 것은 "
            "소비 측 몫이고, 이 필드는 누계 축을 낸다(FIELD_MAP §2 '두 축을 다 싣는다')."
        ),
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    FieldSpec(
        field_id="financial.total_assets",
        source="fin",
        expr="total_asset",
        label="자산총계",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="지원",
        description="재무상태표 시점 값 — 기간 어휘가 없다(period_end 시점의 잔액).",
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
        verdict="지원",
        description="재무상태표 시점 값 — 기간 어휘가 없다(period_end 시점의 잔액).",
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    FieldSpec(
        field_id="financial.book_equity",
        source="fin",
        expr="total_equity",
        label="자본총계",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="지원",
        description=(
            "재무상태표 시점 값(지배·비지배 합계 — 지배주주분 equity_owners 는 equity 내부 "
            "스코프다)."
        ),
        disclosure_basis=_FIN_DISCLOSURE,
        evidence=_FIN_EVIDENCE,
    ),
    # ── consensus_daily · opinion_daily (FIELD_MAP §2 consensus.*) ───────────
    FieldSpec(
        field_id="consensus.forward_eps",
        source="consensus_eps",
        expr="est_mean",
        label="선행 EPS(FY1 컨센서스 평균)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        verdict="부분",
        description=(
            "**12개월 선행이 아니다** — equity 는 target_period 별 값만 주고 12M 합성은 팩터층 "
            "몫이다(FIELD_MAP §2). 어댑터는 관측 달 이후로 끝나는 회계기간 중 가장 가까운 것"
            "(FY1)을 고른다. 단위는 행의 unit 컬럼이 정본이고 eps 는 원이다."
        ),
        disclosure_basis="WISE fetched_date(measured) / v3 collected_date(measured)",
        evidence=(
            "equity.duckdb v_consensus(as_of) ← consensus_daily(metric='eps'), 겹치는 달은 먼저 "
            "알 수 있던 한 행으로 접힌다"
        ),
    ),
    FieldSpec(
        field_id="consensus.forward_sales",
        source="consensus_revenue",
        expr="est_mean",
        label="선행 매출액(FY1 컨센서스 평균)",
        unit="억원",
        value_type=FieldValueType.AMOUNT,
        verdict="부분",
        description=(
            "**단위가 억원이다**(원 아님 — FIELD_MAP §2 S17 표). 스케일 변환은 소비자 몫이고 "
            "행의 unit 컬럼이 정본이다. target_period 선택 규칙은 consensus.forward_eps 와 같다."
        ),
        disclosure_basis="WISE fetched_date(measured) / v3 collected_date(measured)",
        evidence="equity.duckdb v_consensus(as_of) ← consensus_daily(metric='revenue')",
    ),
    FieldSpec(
        field_id="consensus.eps_dispersion",
        source="consensus_eps",
        expr="est_max - est_min",
        label="EPS 추정치 범위(max − min)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="부분",
        description=(
            "**표준편차가 아니라 범위**다 — 원장이 min·max 만 준다(FIELD_MAP §2). wise 구간에만 "
            "있고 v3 행은 min/max 가 NULL 이라, 겹치는 달에 v_consensus 가 v3 를 고르면 결측이다."
        ),
        disclosure_basis="WISE fetched_date(measured)",
        evidence="equity.duckdb v_consensus(as_of).est_max − .est_min (metric='eps')",
    ),
    FieldSpec(
        field_id="consensus.target_price",
        source="opinion",
        expr="target_price_krw",
        label="목표주가(컨센서스)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        verdict="부분",
        description=(
            "커버는 보통주에만 있다(WISE 804 / v3 810 — 우선주·ETF·외국주 0). 어댑터는 같은 "
            "(ticker, obs_date) 의 두 원천 중 잰 판본(wise)을 먼저 고르고, wise 가 없는 날은 "
            "v3(coverage_degraded=true — 수집 시각을 원장이 주지 않는다)를 쓴다."
        ),
        disclosure_basis="WISE 화면 수집일(measured) / v3 관측일(default, degraded)",
        evidence="opinion_daily.target_price_krw ← stg_analyst_summary ∪ stg_v3_analyst_opinions",
    ),
    FieldSpec(
        field_id="consensus.recommendation",
        source="opinion",
        expr="opinion_score",
        label="투자의견 점수(컨센서스)",
        unit="score",
        value_type=FieldValueType.RATIO,
        verdict="부분",
        description=(
            "WISE 가 이미 접은 컨센서스 의견 점수 그대로다 — equity 는 임계로 등급을 굽지 "
            "않는다(원칙 ①). 원천 선택 규칙은 consensus.target_price 와 같다."
        ),
        disclosure_basis="WISE 화면 수집일(measured) / v3 관측일(default, degraded)",
        evidence="opinion_daily.opinion_score ← stg_analyst_summary ∪ stg_v3_analyst_opinions",
    ),
    FieldSpec(
        field_id="consensus.analyst_count",
        source="opinion",
        expr="analyst_count",
        label="추정기관 수",
        unit="count",
        value_type=FieldValueType.COUNT,
        verdict="부분",
        description=(
            "consensus_daily 에는 n_analyst 가 없다 — 애널리스트 수 축은 이 테이블뿐이다"
            "(DESIGN §4-6). 원천 선택 규칙은 consensus.target_price 와 같다."
        ),
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
        verdict="지원",
        description=(
            "**원 단위**다 — stage 가 백만원 ×1e6 환산을 마친 값을 equity 도 어댑터도 다시 "
            f"곱하지 않는다(STAGE_HANDOFF §2 · EG3_flow_daily 단위 대조). {_FILL_KIND_NOTE}."
        ),
        disclosure_basis="원장 날짜(공표 시각 미제공, basis default)",
        evidence=(
            "flow_daily.frgnr_invsr_krw ← stg_flow_daily_kiwoom(ka10060) ∪ "
            "stg_flow_split_daily(KIS frgn_ntby_tr_pbmn_krw), 같은 셀에 둘 다 있으면 키움"
        ),
    ),
    FieldSpec(
        field_id="flow.institution_net_buy",
        source="flow",
        expr="orgn_krw",
        label="기관 순매수(대금)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="부분",
        description=(
            "GAP-03 — `orgn` 은 원장의 **합계 컬럼**이고 기관 7주체 합과 다르다(절단본 18,581행 "
            "중 10,783행 불일치, 편차 최대 2,834억원). 그래서 12주체 항등식(EG3-P06)에서도 "
            f"빠진다. {_FILL_KIND_NOTE}."
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
        verdict="지원",
        description=f"원 단위(stage ×1e6 완료 — 재환산 금지). {_FILL_KIND_NOTE}.",
        disclosure_basis="원장 날짜(공표 시각 미제공, basis default)",
        evidence="flow_daily.ind_invsr_krw ← 키움 ind_invsr_krw ∪ KIS prsn_ntby_tr_pbmn_krw",
    ),
    # ── short_daily (FIELD_MAP §2 short.*) ───────────────────────────────────
    FieldSpec(
        field_id="short.short_sale_value",
        source="short_kiwoom",
        expr="short_value_kiwoom_krw",
        label="공매도 거래대금(키움)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="지원",
        description=(
            "**원천은 키움(ka10014) 하나로 고정**한다 — `short_daily` 는 두 원천을 합치지 않고 "
            "접미사 컬럼으로 나란히 두므로(사용자 확정 09-06, DESIGN §4-3) 어댑터가 하나를 "
            "고른다. 키움인 근거: KRX 정본이 없는 축이고 커버가 훨씬 넓다(절단본 measured "
            "키움 18,265 vs KIS 3,891). **폴백 병합은 하지 않는다** — 키움이 없는 날 KIS 로 "
            "갈아타면 시계열이 원천을 섞고 단위·정의 차가 조용히 들어온다. KIS 축 "
            "(`short_value_kis_krw`)이 필요하면 S19 dataset_profile 이 별도 field_id 로 "
            f"낸다. 원 단위(stage 가 천원 ×1e3 환산 완료). {_FILL_KIND_NOTE}."
        ),
        disclosure_basis="원장 날짜(공표 시각 미제공, basis default)",
        evidence=(
            "short_daily.short_value_kiwoom_krw ← stg_short_daily_kiwoom.shrts_trde_prica_krw "
            "(unit_scale=1e3), 결측 사유는 fill_kind_short_kiwoom"
        ),
    ),
    FieldSpec(
        field_id="short.borrowed_quantity",
        source="lending_kis",
        expr="lending_balance_kis_shr",
        label="대차잔고(주식수, KIS)",
        unit="shares",
        value_type=FieldValueType.COUNT,
        verdict="부분",
        description=(
            "GAP-04. **KIS 축뿐이다** — 키움 대차 원장(`stg_lending_daily`)이 S09 입력에 없어 "
            "`lending_balance_kiwoom_raw` 는 후속 슬라이스 몫이고, 두 축의 단위 대조도 그때 "
            "선다(DESIGN §4-3 구현 결과 ①). 원장이 주는 **음수 잔고는 그대로 보존**한다 — "
            f"equity 도 어댑터도 자르지 않는다(원칙 ④). {_FILL_KIND_NOTE}."
        ),
        disclosure_basis="원장 날짜(공표 시각 미제공, basis default)",
        evidence=(
            "short_daily.lending_balance_kis_shr ← stg_loan_daily_kis.rmnd_stcn_shr, 결측 "
            "사유는 fill_kind_loan_kis(금액축 lending_balance_kis_krw 는 내부 스코프)"
        ),
    ),
    # ── credit_daily (FIELD_MAP §2 credit.*) ─────────────────────────────────
    FieldSpec(
        field_id="credit.margin_balance",
        source="credit",
        expr="whol_loan_rmnd_stcn_shr",
        label="신용융자 잔고(주식수)",
        unit="shares",
        value_type=FieldValueType.COUNT,
        verdict="부분",
        description=(
            "**주식수 축**이다 — 금액축 `*_amt` 6컬럼은 단위 미상이라(`credit_daily.amt_basis` "
            "= 'unknown', STAGE_HANDOFF §4) 이 필드로 나가지 않는다. 대주 잔고"
            "(`whol_stln_rmnd_stcn_shr`)는 별개 축이고 equity 내부 스코프다. 잔고가 상장주식수를 "
            "넘는 원장 행은 S10 이 `_reject/balance_over_shares/` 로 격리하고 그 셀은 "
            f"`empty_response`(→ MISSING)로 남는다(DESIGN §9 결정 9). {_FILL_KIND_NOTE}."
        ),
        disclosure_basis=(
            "원장 날짜(basis default) — KIS 신용잔고는 실제로 T+1 공표이나 랙 축은 "
            "dataset_profile(S19)이 확정한다"
        ),
        evidence="credit_daily.whol_loan_rmnd_stcn_shr ← stg_credit_daily(KIS 신용잔고) 무수정",
    ),
    # ── 사건 (FIELD_MAP §2 event.*) ──────────────────────────────────────────
    FieldSpec(
        field_id="event.dividend_per_share",
        source="dividend",
        expr="dps_krw",
        label="주당 현금배당금(최근 사업보고서)",
        unit="KRW",
        value_type=FieldValueType.PRICE,
        verdict="부분",
        description=(
            "**락일·기준일이 없다** — 값이 서는 시점은 사업보고서 접수일뿐이라 TR·배당 재투자 "
            "팩터는 이 필드로 만들 수 없다(DESIGN §4-5 5 · §11). 연 1회(reprt_code='11011') 값이고 "
            "종류(stock_knd) 축을 접으므로 우선주 티커도 보통주 DPS 를 받는다."
        ),
        disclosure_basis="DART 사업보고서 접수일(결산일 + 3~8개월)",
        evidence="dividend_event.dps_krw ← stg_dividend(se wide 전개), 법인→티커는 corp_ticker",
    ),
    FieldSpec(
        field_id="event.buyback_amount",
        source="buyback",
        expr="sum(amount_krw)",
        label="자기주식 취득 결정 금액(최근 공시)",
        unit="KRW",
        value_type=FieldValueType.AMOUNT,
        verdict="지원",
        description=(
            "자기주식 취득 결정공시(tsstk_aq) 금액이고 같은 공시일의 여러 건은 합한다. "
            "**최근 공시 값이 그대로 이어진다** — 창·감쇠는 팩터층 몫이고 available_date 가 언제 "
            "공시된 값인지 말한다. 사업보고서 확정치(treasury_stock)와는 축도 시점도 다르다."
        ),
        disclosure_basis="DART 주요사항보고 접수일(announce_date)",
        evidence="corp_event.amount_krw (event_type='tsstk_aq', 서버 1,951건)",
    ),
    FieldSpec(
        field_id="event.insider_net_buy",
        source="insider",
        expr="sum(qty_change_shr)",
        label="임원·주요주주 지분 증감(최근 보고, 주식수)",
        unit="shares",
        value_type=FieldValueType.COUNT,
        verdict="부분",
        description=(
            "**커버 구간이 롤링 2년이다** — DART 임원·주요주주 소유보고 API 가 그 창만 주고 "
            "재수집이 불가능하다(DART_DESIGN P3e). 절단본 실측 창 2024-08-26~2026-08-26. 같은 "
            "접수일의 보고자 여러 명은 합하고(값이 NULL 인 보고자는 빠진다), 최근 보고 값이 "
            "그대로 이어진다 — 창·감쇠는 팩터층 몫이다."
        ),
        disclosure_basis="DART 소유보고 접수일(rcept_dt)",
        evidence="holder_daily.qty_change_shr (src='elestock'), 법인→티커는 corp_ticker",
    ),
)

# FIELD_MAP §2 의 42 중 어댑터가 내지 않는 13 — field_id → 사유. `list_fields()` 밖이고 질의하면
# `INVALID_QUERY` 의 detail 에 이 문장이 붙는다(mock 폴백 금지, DESIGN §7).
UNSUPPORTED_FIELDS: dict[str, str] = {
    "benchmark.close": (
        "미지원(현 설계) — index_daily 는 security 축이 아니다. 벤치마크는 예약 접두 `idx:` 로 "
        "받되 S21 은 내지 않는다(GAP-09, FIELD_MAP §2)"
    ),
    "flow.foreign_ownership": (
        "미확인 — flow_daily 에 컬럼이 없다. 원천이 stg_flow_daily_kiwoom(ka10060)이 아니라 "
        "stg_foreign_daily(ka10008)인데 절단본에 없어 S08 이 컬럼을 만들지 않았다(만들고 NULL 로 "
        "두면 '있는데 비어 있는' 컬럼이 된다). 선행 조건은 절단본 절단 → S08-2"
    ),
    "flow.block_buy": "미지원 — 대량매매 미수집(FACTORS.md §9)",
    "flow.block_sell": "미지원 — 대량매매 미수집(FACTORS.md §9)",
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
