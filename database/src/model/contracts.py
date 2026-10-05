"""모델 층 계약 (플랜 `docs/plans/2026-09-24-v3-merge.md` M2 W0 · T2.0).

W1 갈래 넷(equity 분리 · factor_inputs 층 · v3 이식 · v2 이식)과 v4 엔진이 서로 엇나가지 않게
**모양만 먼저 고정**한다. 여기에는 계산이 없다.

  ① 입력 — `factor_inputs` 층이 판마다 굽는 표 8개(`FI_TABLES`). 엔진은 이것만 읽는다.
     단위는 compat(v3 미러)와 같다: 가격 원 · 재무·시총 억원 · 수급 백만원 · 주식수 주.
     그래야 v3 이식 엔진이 v3 원본과 |Δ| ≤ 1e-9 로 맞는다(G-M3 ①).
  ② 모델 설정 — `ModelSpec`(레지스트리 YAML 한 개 = 하나). 가중치·유니버스·제외 게이트·
     지표 역할(score|display, 사용자 09-26)을 담는다. 버전은 `spec_id = model_id@version`.
  ③ 엔진 — `Engine.run(spec, inputs) -> EngineResult`. 플랜 원안의 `list[dict]` 에 지표 원값
     긴 표를 더했다: 엑셀 `점수 원자료`·`지표(표시용)` 시트가 엔진이 쓴 값을 그대로 옮겨야 해서다
     (다시 계산하면 두 곳의 정의가 갈린다). v3·v2 이식 엔진은 원값이 48·21열 안에 있으므로
     `indicators` 를 비워도 된다.
  ④ 출력 — v3 48열·v2 21열은 v3 `score_history`·`score_history_v2` 그대로(compat 로 되쓴다).
     v4 계열은 기본 열 + 버킷마다 `<bucket>_score` 열(`score_columns(spec)`).

입력 표 8개 중 6개는 플랜 원안이고 `fi_credit` 은 v4 설계(D-13': 보조 버킷의 신용잔고 변화)가
플랜 뒤에 정해져 더한 것이다. `fi_consensus_annual` 은 v2 원천(c1050001)이 v3 원천과 값이 달라
(W1-d 실측) 두 원본을 동시에 맞추려고 나눈 v2 전용 표다.
`fi_fin_summary` 에 분기 행(`period_type='quarter'`)을 둔 것도 v4
영업이익률 TTM 때문이다 — v3·v2 는 연간 행만 읽는다.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

DTYPES = ("VARCHAR", "DATE", "BIGINT", "INTEGER", "DOUBLE", "BOOLEAN")
UNITS = ("", "원", "억원", "백만원", "주", "%", "배", "일")


# ── ① 입력 표 계약 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Col:
    name: str
    dtype: str
    unit: str = ""
    note: str = ""


@dataclass(frozen=True)
class TableContract:
    name: str
    grain: tuple[str, ...]
    columns: tuple[Col, ...]
    window: str          # 판 기준일 D 에서 어디까지 싣나
    source: str          # 어느 층·표에서 굽나
    readers: tuple[str, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


def _c(name: str, dtype: str, unit: str = "", note: str = "") -> Col:
    return Col(name, dtype, unit, note)


ALL_ENGINES = ("v3_zscore", "v2_percentrank", "v4_rank")

# 수급 주체 — compat `investor_detail_flows` 와 같은 이름·순서(백만원, 순매수)
FLOW_SUBJECTS = ("individual", "foreign_investor", "institution_total", "financial_investment",
                 "insurance", "investment_trust", "etc_financial", "bank", "pension_fund",
                 "private_equity", "nation", "etc_corporation")

FI_PRICES = TableContract(
    "fi_prices", ("ticker", "date"),
    (_c("ticker", "VARCHAR"), _c("date", "DATE"),
     _c("open", "BIGINT", "원"), _c("high", "BIGINT", "원"), _c("low", "BIGINT", "원"),
     _c("close", "BIGINT", "원"), _c("volume", "BIGINT", "주"), _c("amount", "BIGINT", "원"),
     _c("price_source", "VARCHAR", note="krx | evening_snapshot — 저녁 판 T 행만 snapshot(FG2)")),
    window="D 까지 550 달력일(v3 가격 이력 창과 같다)",
    source="equity price_daily(KRX) + 저녁 판은 evening_snapshot 의 T 행 오버레이",
    readers=ALL_ENGINES)

FI_ADJ_PRICES = TableContract(
    "fi_adj_prices", ("ticker", "date"),
    (_c("ticker", "VARCHAR"), _c("date", "DATE"),
     _c("adj_close", "DOUBLE", "원", "수정종가 — 모멘텀·변동성·52주 고점은 이것만"),
     _c("adj_factor", "DOUBLE", note="누적 수정계수(원가 × 계수 = 수정가)"),
     _c("adj_ok", "BOOLEAN",
        note="False = 미해결 기업행위 사건 구간(DQ-1). v3@1.0 은 무시(원본 동등성), "
             "v4 계열은 해당 창을 결측 처리")),
    window="fi_prices 와 같다",
    source="equity price_adj_daily · adj_factor",
    readers=ALL_ENGINES)

FI_FLOWS = TableContract(
    "fi_flows", ("ticker", "date"),
    (_c("ticker", "VARCHAR"), _c("date", "DATE"),
     *(_c(s, "DOUBLE", "백만원", "순매수") for s in FLOW_SUBJECTS)),
    window="D 까지 60 세션(v3 20 · v2 40 달력일 · v4 외국인 60 세션)",
    source="equity flow_daily(키움 ka10060, 저녁 판 T 행은 evening_snapshot)",
    readers=ALL_ENGINES)

FI_UNIVERSE = TableContract(
    "fi_universe", ("ticker",),
    (_c("ticker", "VARCHAR"), _c("date", "DATE", note="판 기준일 D"),
     _c("name", "VARCHAR"), _c("market", "VARCHAR", note="KOSPI | KOSDAQ"),
     _c("sec_type", "VARCHAR", note="common | spac | preferred …(D-11 어휘)"),
     _c("listed_date", "DATE"),
     _c("shares", "BIGINT", "주"), _c("market_cap", "DOUBLE", "억원"),
     _c("mktcap_basis", "VARCHAR", note="krx | t1_shares_x_t_close(저녁 T, B-24)"),
     _c("sector_l1", "VARCHAR", note="WICS 대분류 코드(G10…)"), _c("sector_l1_name", "VARCHAR"),
     _c("sector_l2", "VARCHAR", note="WICS 중분류 코드(G1010…)"), _c("sector_l2_name", "VARCHAR"),
     _c("has_estimates", "BOOLEAN", note="당해 12월기 op·ni 추정치가 신선 또는 유예 상태"),
     _c("coverage_state", "VARCHAR", note="fresh | grace | lapsed | none (T2.11)"),
     _c("coverage_age_days", "INTEGER", "일", note="마지막 신선 수집일부터 거래일 수"),
     _c("n_analysts", "INTEGER", note="추정기관 수 = WISE 최근 3개월 투자의견을 낸 증권사 수"
        "(0 = 의견 없음, NULL = 모름). v4 리비전 ≥3 판정·scope 유니버스(min_analysts)"),
     _c("adv20", "DOUBLE", "억원", "최근 20세션 평균 거래대금(D-13 적격성 ≥ 10억)"),
     _c("is_admin", "BOOLEAN", note="관리종목 지정 중(D 기준)"),
     _c("is_halted", "BOOLEAN", note="매매정지 중(D 기준)"),
     _c("audit_adverse", "BOOLEAN", note="최근 연간 감사의견 한정·부적정·의견거절"),
     _c("filing_late", "BOOLEAN", note="정기보고서 법정기한 지연 제출(최근 1건)"),
     _c("eligible", "BOOLEAN",
        note="기본 UniverseRule()(v3 미러) 통과 여부. 규칙이 다른 spec 은 엔진이 속성 열로 재판정"),
     _c("exclude_reason", "VARCHAR", note="eligible=False 사유(표시용)")),
    window="D 한 날",
    source="equity universe_daily · security · sector_snapshot(WICS) · consensus 신선도(T2.11)",
    readers=ALL_ENGINES)

FI_CONSENSUS = TableContract(
    "fi_consensus", ("ticker", "target_period", "horizon"),
    (_c("ticker", "VARCHAR"), _c("target_period", "VARCHAR", note="YYYY/MM(연간 결산기)"),
     _c("horizon", "VARCHAR", note="cur | 1w | 1m | 3m — 그 시점에 관측된 컨센서스"),
     _c("revenue", "DOUBLE", "억원"), _c("op", "DOUBLE", "억원"), _c("ni", "DOUBLE", "억원"),
     _c("eps", "DOUBLE", "원"), _c("bps", "DOUBLE", "원"), _c("per", "DOUBLE", "배"),
     _c("pbr", "DOUBLE", "배"), _c("roe", "DOUBLE", "%"),
     _c("n_analysts", "INTEGER"),
     _c("obs_date", "DATE", note="그 horizon 에 쓴 관측일"),
     _c("fetched_date", "DATE", note="수집일 — 신선도 판정(T2.11)")),
    window="당해·차기·차차기 결산기 × {cur, 1w, 1m, 3m}",
    source=("equity consensus_daily·consensus_revision(S17b) — "
            "v3 revision_daily(cur)/compare(1w…3m) 대응"),
    readers=ALL_ENGINES)

FI_CONSENSUS_ANNUAL = TableContract(
    "fi_consensus_annual", ("ticker", "period", "data_type"),
    (_c("ticker", "VARCHAR"), _c("period", "VARCHAR", note="YYYY/MM"),
     _c("data_type", "VARCHAR",
        note="E(추정) | A(확정) — 같은 기에 둘 다 올 수 있다(v2 는 E 우선)"),
     _c("revenue", "DOUBLE", "억원"), _c("op", "DOUBLE", "억원"), _c("ni", "DOUBLE", "억원"),
     _c("eps", "DOUBLE", "원"), _c("per", "DOUBLE", "배"),
     _c("fetched_date", "DATE", note="수집일(최신 ≤ D 한 판)")),
    window="판 기준일 연도 Y 의 Y−1/12 · Y/12 · Y+1/12 (v2 결산기 고정)",
    source=("stg_consensus_annual(WISE c1050001 T2Y) — v2 원천. v3 가 읽는 fi_consensus(매트릭스)·"
            "fi_fin_summary(cF3002)와 같은 기·같은 항목이어도 값이 다르다"
            "(09-29 실측: 당해 op 318종목) "
            "— 두 원본을 동시에 맞추려고 표를 나눴다"),
    readers=("v2_percentrank",))

FI_FIN_SUMMARY = TableContract(
    "fi_fin_summary", ("ticker", "period", "period_type"),
    (_c("ticker", "VARCHAR"), _c("period", "VARCHAR", note="YYYY/MM"),
     _c("period_type", "VARCHAR", note="annual | quarter(분기는 v4 TTM 용)"),
     _c("revenue", "DOUBLE", "억원"), _c("op", "DOUBLE", "억원"), _c("ni", "DOUBLE", "억원"),
     _c("eps", "DOUBLE", "원"), _c("bps", "DOUBLE", "원"), _c("per", "DOUBLE", "배"),
     _c("pbr", "DOUBLE", "배"), _c("roe", "DOUBLE", "%"), _c("roa", "DOUBLE", "%"),
     _c("debt_ratio", "DOUBLE", "%"), _c("fcf", "DOUBLE", "억원"), _c("capex", "DOUBLE", "억원"),
     _c("op_margin", "DOUBLE", "%"), _c("ni_margin", "DOUBLE", "%"),
     _c("dividend_yield", "DOUBLE", "%", "WISE 투자지표(v3 밸류가 읽는 값)"),
     _c("dps", "DOUBLE", "원",
        "보통주 주당배당금(DART, 연간) — v4 DY0 = dps ÷ 현재 종가, 무배당 0(연구 R-2 정의)"),
     _c("shares", "BIGINT", "주"),
     _c("ev_ebitda", "DOUBLE", "배"), _c("yoy", "DOUBLE", "%"),
     _c("gross_profit", "DOUBLE", "억원"), _c("total_assets", "DOUBLE", "억원"),
     _c("fs_basis", "VARCHAR", note="연결 | 별도 | GAAP개별(DQ-5·DQ-10)"),
     _c("capex_basis", "VARCHAR", note="fin_std capex_basis 그대로(DQ-8)"),
     _c("revenue_basis", "VARCHAR",
        note="분기 행 매출 계정 종류 gross | net(은행·증권·금융지주 순영업이익) — v4 영업이익률 비교 그룹(10-01)"),
     _c("available_date", "DATE",
        note="공시·수집으로 알 수 있게 된 날. PIT(≤ D)는 굽는 단계가 적용하고 엔진은 읽지 않는다")),
    window="확정치만(추정치는 fi_consensus). 연간 2기(v3 LIMIT 2) + 분기 5기(v4 TTM)",
    source=("stg_fin_wise(연간 손익·지표) + stg_fin_wise_q(분기 손익, 10-01 T-Q4 — WISE 가 없는 종목만 "
            "equity fin_std 분기) + equity fin_std(연간 자산·현금흐름) — compat T1.5 SQL 을 이 층으로 옮긴다"),
    readers=ALL_ENGINES)

FI_CREDIT = TableContract(
    "fi_credit", ("ticker", "date"),
    (_c("ticker", "VARCHAR"), _c("date", "DATE"),
     _c("credit_balance", "BIGINT", "주", "신용융자 잔고 주식수"),
     _c("credit_ratio", "DOUBLE", "%", "상장주식 대비 잔고율"),
     _c("available_date", "DATE", note="KIS 신용은 T+1 이후 확정 — look-ahead 차단(감사 09-19)")),
    window="D 까지 60 세션",
    source="equity credit_daily(KIS)",
    readers=("v4_rank",))

FI_TABLES: dict[str, TableContract] = {t.name: t for t in (
    FI_PRICES, FI_ADJ_PRICES, FI_FLOWS, FI_UNIVERSE, FI_CONSENSUS, FI_CONSENSUS_ANNUAL,
    FI_FIN_SUMMARY, FI_CREDIT)}


# ── ② 모델 설정 ───────────────────────────────────────────────────────────────
ROLES = ("score", "display")
# D-13 적격성 제외 표식 — UniverseRule.exclude 의 어휘. fi_universe BOOLEAN 열과 짝:
#   admin → is_admin · halted → is_halted ·
#   audit_adverse → audit_adverse · filing_late → filing_late
ELIGIBILITY_FLAGS = ("admin", "halted", "audit_adverse", "filing_late")
GATE_RULES = ("exclude_bottom_pct", "exclude_top_pct", "require_value")
SECTOR_LEVELS = ("L1", "L2")


@dataclass(frozen=True)
class Indicator:
    """하위 지표 하나. `role=display` 는 계산·표시만 하고 점수에 넣지 않는다(사용자 09-26)."""
    key: str
    bucket: str
    role: str = "score"
    direction: int = 1           # +1 높을수록 좋다 · −1 낮을수록 좋다
    weight: float = 1.0          # 버킷 안 가중(비례 재정규화)
    definition: str = ""         # 엑셀에 병기할 정의 문자열


@dataclass(frozen=True)
class Gate:
    """제외 게이트 — 예: 고점근접+반전(M_PULL_C) 하위 30% 제외(D-13')."""
    key: str
    rule: str
    value: float | None = None


@dataclass(frozen=True)
class UniverseRule:
    require_estimates: bool = True                  # D-10
    sec_types: tuple[str, ...] = ("common", "spac")  # D-11(v3 미러). v4 는 ("common",)
    markets: tuple[str, ...] = ("KOSPI", "KOSDAQ")
    min_market_cap: float | None = None              # 억원
    coverage_grace_days: int = 5                     # D-14 후보(유예, 거래일)
    min_adv20: float | None = None                   # 억원. D-13 적격성(v4 = 10)
    # 추정기관수(fi_universe.n_analysts — 최근 3개월 투자의견을 낸 증권사 수) 하한. 0 은 원천이
    # 밝힌 0 이라 제외하고, NULL(모름)은 제외하지 않는다 — 수집 실패로 종목이 조용히 빠지지 않게.
    # scope = 1(2026-10-05 사용자 결정 '3개월 의견 없으면 유니버스 제외').
    min_analysts: int | None = None
    exclude: tuple[str, ...] = ()                    # ELIGIBILITY_FLAGS 부분집합(v3 미러 = 없음)


@dataclass(frozen=True)
class OutputRule:
    top_n: int = 30                   # 주간 후보 수(권장안)
    sector_level: str = "L1"
    max_per_sector: int = 9           # 대분류당 상한(30%)


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    version: str
    engine: str
    buckets: Mapping[str, float]
    indicators: tuple[Indicator, ...] = ()
    universe: UniverseRule = field(default_factory=UniverseRule)
    output: OutputRule = field(default_factory=OutputRule)
    sector_neutral: str | None = None     # "L1" 이면 버킷 안 백분위를 대분류 안에서
    gates: tuple[Gate, ...] = ()
    params: Mapping[str, object] = field(default_factory=dict)   # 엔진 고유(v3 하위가중 등)

    @property
    def spec_id(self) -> str:
        return f"{self.model_id}@{self.version}"

    def validate(self) -> list[str]:
        """설정 오류 목록(빈 목록 = 통과). 레지스트리가 적재 때 부른다."""
        errs: list[str] = []
        if self.engine not in ALL_ENGINES:
            errs.append(f"engine {self.engine!r} 는 {ALL_ENGINES} 밖")
        if not self.buckets:
            errs.append("buckets 가 비었다")
        elif abs(sum(self.buckets.values()) - 1.0) > 1e-9:
            errs.append(f"buckets 가중 합 {sum(self.buckets.values())!r} ≠ 1")
        if any(w < 0 for w in self.buckets.values()):
            errs.append("음수 버킷 가중")
        keys = [i.key for i in self.indicators]
        if len(keys) != len(set(keys)):
            errs.append("indicator key 중복")
        for i in self.indicators:
            if i.role not in ROLES:
                errs.append(f"{i.key}: role {i.role!r} ∉ {ROLES}")
            if i.direction not in (1, -1):
                errs.append(f"{i.key}: direction 은 ±1")
            if i.role == "score" and i.bucket not in self.buckets:
                errs.append(f"{i.key}: bucket {i.bucket!r} 이 buckets 에 없다")
        if self.indicators:
            scored = {i.bucket for i in self.indicators if i.role == "score"}
            for b in self.buckets:
                if b not in scored:
                    errs.append(f"bucket {b!r} 에 score 지표가 없다")
        for g in self.gates:
            if g.rule not in GATE_RULES:
                errs.append(f"gate {g.key}: rule {g.rule!r} ∉ {GATE_RULES}")
            if g.key not in keys:
                errs.append(f"gate {g.key}: 선언된 indicator 가 아니다")
            if g.rule != "require_value" and not (g.value is not None and 0 < g.value < 1):
                errs.append(f"gate {g.key}: 비율 value 는 (0, 1)")
        if self.sector_neutral is not None and self.sector_neutral not in SECTOR_LEVELS:
            errs.append(f"sector_neutral {self.sector_neutral!r} ∉ {SECTOR_LEVELS}")
        if self.output.sector_level not in SECTOR_LEVELS:
            errs.append(f"output.sector_level ∉ {SECTOR_LEVELS}")
        if self.output.top_n <= 0 or self.output.max_per_sector <= 0:
            errs.append("output.top_n·max_per_sector 는 양수")
        if self.universe.coverage_grace_days < 0:
            errs.append("universe.coverage_grace_days 는 0 이상")
        bad_flags = set(self.universe.exclude) - set(ELIGIBILITY_FLAGS)
        if bad_flags:
            errs.append(f"universe.exclude {sorted(bad_flags)} ∉ {ELIGIBILITY_FLAGS}")
        if self.universe.min_adv20 is not None and self.universe.min_adv20 < 0:
            errs.append("universe.min_adv20 는 0 이상")
        return errs

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> ModelSpec:
        """레지스트리 YAML 을 읽은 dict → ModelSpec.

        모르는 키는 거절한다(오타가 조용히 무시되지 않게).
        """
        known = {"model_id", "version", "engine", "buckets", "indicators", "universe", "output",
                 "sector_neutral", "gates", "params"}
        extra = set(d) - known
        if extra:
            raise ValueError(f"ModelSpec 모르는 키 {sorted(extra)}")
        uni = d.get("universe") or {}
        out = d.get("output") or {}
        assert isinstance(uni, Mapping) and isinstance(out, Mapping)
        return cls(
            model_id=str(d["model_id"]), version=str(d["version"]), engine=str(d["engine"]),
            buckets={str(k): float(v) for k, v in dict(d["buckets"]).items()},  # type: ignore[arg-type]
            indicators=tuple(Indicator(**i) for i in d.get("indicators") or ()),  # type: ignore[arg-type]
            universe=UniverseRule(**{k: tuple(v) if isinstance(v, list) else v
                                     for k, v in uni.items()}),
            output=OutputRule(**out),
            sector_neutral=d.get("sector_neutral"),  # type: ignore[arg-type]
            gates=tuple(Gate(**g) for g in d.get("gates") or ()),  # type: ignore[arg-type]
            params=dict(d.get("params") or {}),  # type: ignore[arg-type]
        )


# ── ③ 엔진 ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FactorInputs:
    """판 하나의 입력. `tables` = 표 이름 → 행(dict) 목록. 엔진은 이 밖을 읽지 않는다."""
    date: str             # 판 기준일 D (YYYY-MM-DD)
    basis: str            # evening | morning
    build_id: str         # factor_inputs 판 id
    tables: Mapping[str, Sequence[Mapping[str, object]]]

    def rows(self, name: str) -> Sequence[Mapping[str, object]]:
        if name not in FI_TABLES:
            raise KeyError(f"계약에 없는 입력 표 {name!r}")
        return self.tables.get(name, ())

    def check(self, readers_of: str | None = None) -> list[str]:
        """계약 대조 — 표가 있나, 첫 행의 컬럼 집합이 계약과 같나. `readers_of` 를 주면 그 엔진이
        읽는 표만 요구한다."""
        errs: list[str] = []
        for name, t in FI_TABLES.items():
            if readers_of is not None and readers_of not in t.readers:
                continue
            if name not in self.tables:
                errs.append(f"{name} 없음")
                continue
            rows = self.tables[name]
            if rows and set(rows[0]) != set(t.column_names):
                miss = sorted(set(t.column_names) - set(rows[0]))
                more = sorted(set(rows[0]) - set(t.column_names))
                errs.append(f"{name} 컬럼 불일치 — 없음 {miss} · 계약 밖 {more}")
        return errs


@dataclass(frozen=True)
class EngineResult:
    scores: list[dict[str, object]]
    indicators: list[dict[str, object]] = field(default_factory=list)   # INDICATOR_COLUMNS


class Engine(Protocol):
    name: str

    def output_columns(self, spec: ModelSpec) -> tuple[str, ...]: ...

    def run(self, spec: ModelSpec, inputs: FactorInputs) -> EngineResult: ...


# ── ④ 출력 ───────────────────────────────────────────────────────────────────
# v3 `score_history` 48열 — compat `v3_schema.sql` 과 같은 순서(테스트가 대조한다)
V3_SCORE_COLUMNS: tuple[str, ...] = (
    "stock_code", "score_date", "momentum_score", "revision_score", "flow_score",
    "valuation_score", "composite_score", "rank", "quality_score", "growth_score",
    "sentiment_score", "volatility_score", "size_score", "foreign_score", "shareholder_score",
    "r1m", "r3m", "r6m", "r9m", "r12m",
    "op_change_1w", "ni_change_1w", "op_change_1m", "ni_change_1m", "op_change_3m", "ni_change_3m",
    "flow_inst_5d", "flow_inst_20d", "flow_for_5d", "flow_for_20d", "flow_pe_5d", "flow_pe_20d",
    "qual_gpa", "qual_roa", "qual_fcf_assets", "qual_debt_ratio", "qual_gpa_change", "qual_std_20d",
    "val_per", "val_pbr", "val_ev_ebitda", "val_dividend_yield",
    "op_1w_flag", "ni_1w_flag", "op_1m_flag", "ni_1m_flag", "op_3m_flag", "ni_3m_flag")

# v2 `score_history_v2` 21열
V2_SCORE_COLUMNS: tuple[str, ...] = (
    "stock_code", "score_date", "momentum_score", "growth_score", "flow_score", "value_score",
    "total_score", "rank", "r1m", "r3m", "r6m", "op_yoy_cur", "op_yoy_next", "ni_yoy_cur",
    "ni_yoy_next", "inst_5d", "inst_20d", "frgn_5d", "frgn_20d", "per_cur", "per_next")

# v4 계열 점수 표 — 기본 열 + 버킷마다 `<bucket>_score`(백분위 0~100)
SCORE_BASE_COLUMNS: tuple[str, ...] = (
    "ticker", "score_date", "spec_id", "rank", "composite", "excluded", "exclude_reason",
    "sector_l1", "sector_l2", "coverage_state", "n_buckets_used")

# 지표 원값 긴 표 — 엑셀 `점수 원자료`(role=score) · `지표(표시용)`(role=display) 의 원천
INDICATOR_COLUMNS: tuple[str, ...] = (
    "ticker", "score_date", "spec_id", "key", "bucket", "role", "raw", "pct", "flag")


def score_columns(spec: ModelSpec) -> tuple[str, ...]:
    """spec 에 맞는 점수 표 컬럼. v3·v2 는 고정 스키마, 그 밖은 기본 열 + 버킷 열."""
    if spec.engine == "v3_zscore":
        return V3_SCORE_COLUMNS
    if spec.engine == "v2_percentrank":
        return V2_SCORE_COLUMNS
    return SCORE_BASE_COLUMNS + tuple(f"{b}_score" for b in spec.buckets)


__all__ = [
    "ALL_ENGINES", "DTYPES", "ELIGIBILITY_FLAGS", "FI_ADJ_PRICES",
    "FI_CONSENSUS", "FI_CONSENSUS_ANNUAL",
    "FI_CREDIT", "FI_FIN_SUMMARY",
    "FI_FLOWS", "FI_PRICES", "FI_TABLES", "FI_UNIVERSE", "FLOW_SUBJECTS", "GATE_RULES",
    "INDICATOR_COLUMNS", "ROLES", "SCORE_BASE_COLUMNS", "SECTOR_LEVELS", "UNITS",
    "V2_SCORE_COLUMNS", "V3_SCORE_COLUMNS", "Col", "Engine", "EngineResult", "FactorInputs",
    "Gate", "Indicator", "ModelSpec", "OutputRule", "TableContract", "UniverseRule",
    "score_columns",
]
