from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ._nodes import (
    BinaryNode,
    BinaryOperator,
    CrossSectionalNode,
    CrossSectionalOperator,
    FactorGraph,
    FieldNode,
    MissingPolicy,
    TimeSeriesNode,
    TimeSeriesOperator,
)


class FactorCategory(StrEnum):
    PRICE = "price"
    FINANCIAL = "financial"
    CONSENSUS = "consensus"
    FLOW = "flow"
    SHORT = "short"
    CREDIT = "credit"
    EVENT = "event"


class FactorPreference(StrEnum):
    HIGH = "high"
    LOW = "low"


class FactorAvailability(StrEnum):
    IMPLEMENTED = "implemented"
    CATALOG_ONLY = "catalog_only"


@dataclass(frozen=True)
class FactorDefinition:
    factor_id: str
    label: str
    description: str
    category: FactorCategory
    preference: FactorPreference
    output_unit: str
    required_field_ids: tuple[str, ...]
    minimum_history_sessions: int
    missing_policy: MissingPolicy
    availability: FactorAvailability
    default_graph: FactorGraph | None
    tags: tuple[str, ...] = ()


class FactorRegistry:
    def __init__(self, *, version: str, definitions: tuple[FactorDefinition, ...]) -> None:
        by_id = {definition.factor_id: definition for definition in definitions}
        if len(by_id) != len(definitions):
            raise ValueError(
                "factor registry contains duplicate identifiers — "
                f"version={version!r} definitions={len(definitions)} unique={len(by_id)}"
            )
        self._version = version
        self._definitions = definitions
        self._by_id = by_id

    @property
    def version(self) -> str:
        return self._version

    def all(self) -> tuple[FactorDefinition, ...]:
        return self._definitions

    def get(self, factor_id: str) -> FactorDefinition:
        try:
            return self._by_id[factor_id]
        except KeyError as error:
            raise KeyError(
                f"unknown factor definition — factor_id={factor_id!r} version={self._version!r}"
            ) from error


@dataclass(frozen=True)
class _CatalogSeed:
    factor_id: str
    label: str
    category: FactorCategory
    preference: FactorPreference
    unit: str
    fields: tuple[str, ...]
    history: int


def _field_graph(field_id: str) -> FactorGraph:
    return FactorGraph(
        nodes=(FieldNode(node_id="source", field_id=field_id, kind="field"),),
        output_node_id="source",
    )


def _implemented_graphs() -> dict[str, FactorGraph]:
    return {
        "price.momentum_12_1": FactorGraph(
            nodes=(
                FieldNode("close", "price.close", "field"),
                TimeSeriesNode(
                    "momentum", TimeSeriesOperator.MOMENTUM, "close", 252, "time_series", 21
                ),
                CrossSectionalNode(
                    "rank",
                    CrossSectionalOperator.RANK,
                    "momentum",
                    "cross_sectional",
                ),
            ),
            output_node_id="rank",
        ),
        "financial.book_to_market": FactorGraph(
            nodes=(
                FieldNode("book", "financial.book_equity", "field"),
                FieldNode("cap", "price.market_cap", "field"),
                BinaryNode("ratio", BinaryOperator.DIVIDE, "book", "cap", "binary"),
                CrossSectionalNode("rank", CrossSectionalOperator.RANK, "ratio", "cross_sectional"),
            ),
            output_node_id="rank",
        ),
        "consensus.forward_eps_growth": FactorGraph(
            nodes=(
                FieldNode("eps", "consensus.forward_eps", "field"),
                TimeSeriesNode("growth", TimeSeriesOperator.MOMENTUM, "eps", 20, "time_series"),
            ),
            output_node_id="growth",
        ),
        "flow.foreign_net_buy_20d": FactorGraph(
            nodes=(
                FieldNode("flow", "flow.foreign_net_buy", "field"),
                TimeSeriesNode("mean", TimeSeriesOperator.MEAN, "flow", 20, "time_series"),
            ),
            output_node_id="mean",
        ),
        "short.short_balance_ratio": _field_graph("short.short_balance_ratio"),
        "credit.margin_balance_change_20d": FactorGraph(
            nodes=(
                FieldNode("balance", "credit.margin_balance", "field"),
                TimeSeriesNode("change", TimeSeriesOperator.DELTA, "balance", 20, "time_series"),
            ),
            output_node_id="change",
        ),
        "event.earnings_surprise": _field_graph("event.earnings_surprise"),
    }


_SEEDS = (
    _CatalogSeed(
        "price.momentum_12_1",
        "12-1개월 모멘텀",
        FactorCategory.PRICE,
        FactorPreference.HIGH,
        "ratio",
        ("price.close",),
        252,
    ),
    _CatalogSeed(
        "price.momentum_6_1",
        "6-1개월 모멘텀",
        FactorCategory.PRICE,
        FactorPreference.HIGH,
        "ratio",
        ("price.close",),
        126,
    ),
    _CatalogSeed(
        "price.reversal_1m",
        "1개월 반전",
        FactorCategory.PRICE,
        FactorPreference.LOW,
        "ratio",
        ("price.close",),
        21,
    ),
    _CatalogSeed(
        "price.volatility_60d",
        "60일 변동성",
        FactorCategory.PRICE,
        FactorPreference.LOW,
        "ratio",
        ("price.close",),
        60,
    ),
    _CatalogSeed(
        "price.beta_252d",
        "시장 베타",
        FactorCategory.PRICE,
        FactorPreference.LOW,
        "ratio",
        ("price.close", "benchmark.close"),
        252,
    ),
    _CatalogSeed(
        "price.max_drawdown_252d",
        "52주 최대 낙폭",
        FactorCategory.PRICE,
        FactorPreference.LOW,
        "ratio",
        ("price.close",),
        252,
    ),
    _CatalogSeed(
        "price.distance_52w_high",
        "52주 고점 거리",
        FactorCategory.PRICE,
        FactorPreference.HIGH,
        "ratio",
        ("price.close",),
        252,
    ),
    _CatalogSeed(
        "price.overnight_return_20d",
        "야간 수익률",
        FactorCategory.PRICE,
        FactorPreference.HIGH,
        "ratio",
        ("price.open", "price.close"),
        21,
    ),
    _CatalogSeed(
        "price.intraday_return_20d",
        "장중 수익률",
        FactorCategory.PRICE,
        FactorPreference.HIGH,
        "ratio",
        ("price.open", "price.close"),
        21,
    ),
    _CatalogSeed(
        "price.liquidity_amihud_20d",
        "Amihud 비유동성",
        FactorCategory.PRICE,
        FactorPreference.LOW,
        "ratio",
        ("price.close", "price.volume"),
        21,
    ),
    _CatalogSeed(
        "financial.book_to_market",
        "장부가치 대비 시가총액",
        FactorCategory.FINANCIAL,
        FactorPreference.HIGH,
        "ratio",
        ("financial.book_equity", "price.market_cap"),
        1,
    ),
    _CatalogSeed(
        "financial.earnings_yield",
        "이익수익률",
        FactorCategory.FINANCIAL,
        FactorPreference.HIGH,
        "ratio",
        ("financial.net_income", "price.market_cap"),
        1,
    ),
    _CatalogSeed(
        "financial.sales_to_price",
        "매출액 대비 시가총액",
        FactorCategory.FINANCIAL,
        FactorPreference.HIGH,
        "ratio",
        ("financial.revenue", "price.market_cap"),
        1,
    ),
    _CatalogSeed(
        "financial.roe",
        "자기자본이익률",
        FactorCategory.FINANCIAL,
        FactorPreference.HIGH,
        "ratio",
        ("financial.net_income", "financial.book_equity"),
        5,
    ),
    _CatalogSeed(
        "financial.roa",
        "총자산이익률",
        FactorCategory.FINANCIAL,
        FactorPreference.HIGH,
        "ratio",
        ("financial.net_income", "financial.total_assets"),
        5,
    ),
    _CatalogSeed(
        "financial.gross_profitability",
        "매출총이익성",
        FactorCategory.FINANCIAL,
        FactorPreference.HIGH,
        "ratio",
        ("financial.gross_profit", "financial.total_assets"),
        5,
    ),
    _CatalogSeed(
        "financial.operating_margin",
        "영업이익률",
        FactorCategory.FINANCIAL,
        FactorPreference.HIGH,
        "ratio",
        ("financial.operating_income", "financial.revenue"),
        5,
    ),
    _CatalogSeed(
        "financial.asset_growth",
        "자산 성장률",
        FactorCategory.FINANCIAL,
        FactorPreference.LOW,
        "ratio",
        ("financial.total_assets",),
        5,
    ),
    _CatalogSeed(
        "financial.accruals",
        "발생액",
        FactorCategory.FINANCIAL,
        FactorPreference.LOW,
        "ratio",
        ("financial.operating_cash_flow", "financial.net_income", "financial.total_assets"),
        5,
    ),
    _CatalogSeed(
        "financial.leverage",
        "재무 레버리지",
        FactorCategory.FINANCIAL,
        FactorPreference.LOW,
        "ratio",
        ("financial.total_liabilities", "financial.total_assets"),
        1,
    ),
    _CatalogSeed(
        "consensus.forward_eps_growth",
        "선행 EPS 성장",
        FactorCategory.CONSENSUS,
        FactorPreference.HIGH,
        "ratio",
        ("consensus.forward_eps",),
        20,
    ),
    _CatalogSeed(
        "consensus.earnings_revision_1m",
        "1개월 이익 추정치 변경",
        FactorCategory.CONSENSUS,
        FactorPreference.HIGH,
        "ratio",
        ("consensus.forward_eps",),
        21,
    ),
    _CatalogSeed(
        "consensus.target_price_upside",
        "목표주가 상승여력",
        FactorCategory.CONSENSUS,
        FactorPreference.HIGH,
        "ratio",
        ("consensus.target_price", "price.close"),
        1,
    ),
    _CatalogSeed(
        "consensus.recommendation_change",
        "투자의견 변화",
        FactorCategory.CONSENSUS,
        FactorPreference.HIGH,
        "score",
        ("consensus.recommendation",),
        21,
    ),
    _CatalogSeed(
        "consensus.sales_revision_1m",
        "1개월 매출 추정치 변경",
        FactorCategory.CONSENSUS,
        FactorPreference.HIGH,
        "ratio",
        ("consensus.forward_sales",),
        21,
    ),
    _CatalogSeed(
        "consensus.dispersion",
        "이익 추정 분산",
        FactorCategory.CONSENSUS,
        FactorPreference.LOW,
        "ratio",
        ("consensus.eps_dispersion",),
        1,
    ),
    _CatalogSeed(
        "consensus.coverage_change",
        "애널리스트 커버리지 변화",
        FactorCategory.CONSENSUS,
        FactorPreference.HIGH,
        "count",
        ("consensus.analyst_count",),
        21,
    ),
    _CatalogSeed(
        "flow.foreign_net_buy_20d",
        "외국인 20일 순매수",
        FactorCategory.FLOW,
        FactorPreference.HIGH,
        "KRW",
        ("flow.foreign_net_buy",),
        20,
    ),
    _CatalogSeed(
        "flow.institution_net_buy_20d",
        "기관 20일 순매수",
        FactorCategory.FLOW,
        FactorPreference.HIGH,
        "KRW",
        ("flow.institution_net_buy",),
        20,
    ),
    _CatalogSeed(
        "flow.retail_net_buy_20d",
        "개인 20일 순매수",
        FactorCategory.FLOW,
        FactorPreference.LOW,
        "KRW",
        ("flow.retail_net_buy",),
        20,
    ),
    _CatalogSeed(
        "flow.foreign_ownership_change",
        "외국인 지분율 변화",
        FactorCategory.FLOW,
        FactorPreference.HIGH,
        "ratio",
        ("flow.foreign_ownership",),
        20,
    ),
    _CatalogSeed(
        "flow.turnover_20d",
        "20일 회전율",
        FactorCategory.FLOW,
        FactorPreference.HIGH,
        "ratio",
        ("price.volume", "price.shares_outstanding"),
        20,
    ),
    _CatalogSeed(
        "flow.volume_surge",
        "거래량 급증",
        FactorCategory.FLOW,
        FactorPreference.HIGH,
        "ratio",
        ("price.volume",),
        60,
    ),
    _CatalogSeed(
        "flow.block_trade_imbalance",
        "대량매매 불균형",
        FactorCategory.FLOW,
        FactorPreference.HIGH,
        "KRW",
        ("flow.block_buy", "flow.block_sell"),
        20,
    ),
    _CatalogSeed(
        "short.short_balance_ratio",
        "공매도 잔고 비율",
        FactorCategory.SHORT,
        FactorPreference.LOW,
        "ratio",
        ("short.short_balance_ratio",),
        1,
    ),
    _CatalogSeed(
        "short.short_sale_ratio_20d",
        "공매도 거래 비중",
        FactorCategory.SHORT,
        FactorPreference.LOW,
        "ratio",
        ("short.short_sale_value", "price.trading_value"),
        20,
    ),
    _CatalogSeed(
        "short.short_balance_change_20d",
        "공매도 잔고 변화",
        FactorCategory.SHORT,
        FactorPreference.LOW,
        "ratio",
        ("short.short_balance_ratio",),
        20,
    ),
    _CatalogSeed(
        "short.borrow_utilization",
        "대차 활용률",
        FactorCategory.SHORT,
        FactorPreference.LOW,
        "ratio",
        ("short.borrowed_quantity", "price.shares_outstanding"),
        1,
    ),
    _CatalogSeed(
        "short.short_covering",
        "숏커버 강도",
        FactorCategory.SHORT,
        FactorPreference.HIGH,
        "ratio",
        ("short.short_balance_ratio", "price.close"),
        20,
    ),
    _CatalogSeed(
        "credit.margin_balance_change_20d",
        "신용잔고 변화",
        FactorCategory.CREDIT,
        FactorPreference.LOW,
        "ratio",
        ("credit.margin_balance",),
        20,
    ),
    _CatalogSeed(
        "credit.margin_balance_ratio",
        "신용잔고 비율",
        FactorCategory.CREDIT,
        FactorPreference.LOW,
        "ratio",
        ("credit.margin_balance", "price.market_cap"),
        1,
    ),
    _CatalogSeed(
        "credit.credit_net_buy_20d",
        "신용 순매수",
        FactorCategory.CREDIT,
        FactorPreference.LOW,
        "KRW",
        ("credit.net_buy",),
        20,
    ),
    _CatalogSeed(
        "credit.collateral_ratio",
        "담보 비율",
        FactorCategory.CREDIT,
        FactorPreference.HIGH,
        "ratio",
        ("credit.collateral_value", "credit.loan_value"),
        1,
    ),
    _CatalogSeed(
        "credit.forced_liquidation_pressure",
        "반대매매 압력",
        FactorCategory.CREDIT,
        FactorPreference.LOW,
        "ratio",
        ("credit.forced_liquidation", "price.trading_value"),
        20,
    ),
    _CatalogSeed(
        "event.earnings_surprise",
        "실적 서프라이즈",
        FactorCategory.EVENT,
        FactorPreference.HIGH,
        "ratio",
        ("event.earnings_surprise",),
        1,
    ),
    _CatalogSeed(
        "event.dividend_yield_event",
        "배당 공시 수익률",
        FactorCategory.EVENT,
        FactorPreference.HIGH,
        "ratio",
        ("event.dividend_per_share", "price.close"),
        1,
    ),
    _CatalogSeed(
        "event.buyback_announcement",
        "자사주 매입 공시",
        FactorCategory.EVENT,
        FactorPreference.HIGH,
        "score",
        ("event.buyback_amount", "price.market_cap"),
        1,
    ),
    _CatalogSeed(
        "event.insider_trade",
        "임원·주요주주 거래",
        FactorCategory.EVENT,
        FactorPreference.HIGH,
        "ratio",
        ("event.insider_net_buy", "price.market_cap"),
        1,
    ),
    _CatalogSeed(
        "event.index_rebalance",
        "지수 편출입",
        FactorCategory.EVENT,
        FactorPreference.HIGH,
        "score",
        ("event.index_membership_change",),
        1,
    ),
    _CatalogSeed(
        "event.disclosure_sentiment",
        "공시 텍스트 심리",
        FactorCategory.EVENT,
        FactorPreference.HIGH,
        "score",
        ("event.disclosure_sentiment",),
        1,
    ),
)


def build_default_factor_registry() -> FactorRegistry:
    implemented = _implemented_graphs()
    definitions = tuple(
        FactorDefinition(
            factor_id=seed.factor_id,
            label=seed.label,
            description=f"{seed.label} 팩터의 PIT 안전 기본 정의",
            category=seed.category,
            preference=seed.preference,
            output_unit=seed.unit,
            required_field_ids=seed.fields,
            minimum_history_sessions=seed.history,
            missing_policy=MissingPolicy.DROP,
            availability=(
                FactorAvailability.IMPLEMENTED
                if seed.factor_id in implemented
                else FactorAvailability.CATALOG_ONLY
            ),
            default_graph=implemented.get(seed.factor_id),
            tags=(seed.category.value, seed.preference.value),
        )
        for seed in _SEEDS
    )
    return FactorRegistry(version="factor-registry-v1", definitions=definitions)
