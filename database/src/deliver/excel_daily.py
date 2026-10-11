"""매일 엑셀 `data/deliver/daily/model_scores_<YYYYMMDD>_<basis>.xlsx` (MODEL_EXCEL_SPEC A).

확정 양식(샘플 22 — DECISIONS §7 #6·#7)을 직접 만든다. 시트: Score → Sector → Earnings →
Analyst · Events · Market · ETF · Flow · Short(새 시트 — 샘플 28 · 30, `sheets_extra`) → Compare → Raw →
Meta → trend(숨김, 1M 흐름 원자료). 값만(수식 없음).
행 = 주 모델(run.primary_spec) 모집단 — 순위 종목(순위순) 뒤에 제외 종목(코드순).
Raw 지표 열은 레지스트리 `role` 이 가른다(score). 표시용(display) 지표 시트는 10-11 사용자
결정으로 뺐다.
"""
from __future__ import annotations

import logging
import os
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
from model.contracts import FactorInputs
from model.engines._common import crosses_step
from model.engines.v3_zscore import (
    ADJ_JUMP_PARAM,
    HISTORY_DAYS,
    _period_return,
    _price_histories,
)
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule, Rule
from openpyxl.styles import Alignment, Border, Font, PatternFill
from openpyxl.styles.differential import DifferentialStyle

from . import sheets_extra
from .common import (
    BASIS_NOTES,
    PREV_NEGATIVE_FLAGS,
    REVISION_KEYS,
    analyst_note,
    bucket_label,
    cap_candidates,
    coverage_label,
    exclude_label,
    ind_meta,
    market_label,
    missing,
    split_flags,
    yoy,
)
from .extra_common import Stock
from .qpack import (
    C_HDR,
    SIDE_HDR,
    SIDE_PAIR,
    SIGN_COLORS,
    Band,
    Col,
    Group,
    Page,
    Title,
    XRefs,
    add_column_sparklines,
    add_sparklines,
    finish_charts,
    font,
    new_workbook,
    note,
    put,
    scale_fixed_zero,
    scale_high_good,
    scale_zero_anchor,
    text_width,
    write_confirmed,
    write_meta_confirmed,
)
from .reader import (
    DeliverError,
    deployed_rev,
    fi_run_meta,
    history_spec,
    iso,
    load_run,
    previous_run,
    read_fi,
    read_scores,
    ymd,
)
from .sources import open_sources
from .trend import (
    FALLBACK_NOTE,
    WINDOWS,
    Trend,
    history_basis,
    load_trend,
    spark_groups,
)
from .trend import write_sheet as write_trend_sheet
from .view import DayView, composite_of, load_day, rank_of, ticker_of

Row = dict[str, object]
Dictionary = list[tuple[str, str, str, str]]
log = logging.getLogger(__name__)

MODEL_LABELS = {"scope@2.0": "scope_v2.0", "scope@1.0": "scope_v1.0", "v4_rank@0.1": "v4 기본",
                "v4_rank@0.2": "v4 동일가중",
                "v3_zscore": "v3 원본", "v2_percentrank": "v2 원본"}
FOOTNOTES = (
    "① 유니버스 컷: 주 모델 모집단은 Meta 시트 '유니버스 규칙' 줄을 따른다. 탈락 종목은 점수 없이 "
    "제외 사유만 적는다.",
    "② 창: 가격 지표는 수정종가 세션 수, 재무는 D 이전 공시(PIT), 추정치는 당해 결산기 컨센서스.",
    "③ 색: 3색 백분위 10/50/90, 빨강(낮음)·노랑(가운데)·초록(높음), 초록 = 좋음. "
    "순위 열은 반전(1위 = 초록). Δ순위 1M 은 가운데 = 0(하락 빨강 · 상승 초록). "
    "z 엔진(scope·v3) 주 모델의 Score 축 z 칸은 고정 3색 −3·0·+3, Sector 축별 평균(Z)은 "
    "대분류 ±0.5 · 업종 ±1(5종목 미만 업종은 회색). "
    "레벨 값은 무색. 형광 노랑 = 오늘 후보 진입(Score 전일 순위 칸).",
    "④ 정렬 키: Score·Raw·Earnings·Compare = 주 모델 순위 오름차순, 제외 종목은 뒤에 코드순.",
    "⑤ 흐름: 셀 안 꺾은선(엑셀 스파크라인) = 1M 동안 날짜별 순위(원순위), "
    "위로 갈수록 순위 상승. 선 색 = 그 기간 Δ순위(초록 상승 · 빨강 하락 · 회색 같음·모름) — "
    "선의 처음 → 끝 방향과 같다. 1일·1W Δ순위는 싣지 않는다.",
)
# 4행 기준일 뒤에 붙는 말 — 읽는 사람이 '판'을 모른다(10-11 사용자 '전일 데이터 이런 식으로').
# 주말 보강판은 확정 샘플(10-11 보강판 발송본 = 샘플 22 양식) 그대로 '주말 보강 데이터'(N-45)
BASIS_WORDS = {"morning": "아침 확정 데이터", "evening": "장 마감 데이터",
               "supplement": "주말 보강 데이터"}
SOURCE_TEXT = "출처: quant-ledger"


Z_SPAN = 3.0       # z 칸 색 끝점 ±3 — 엔진 z 상한(z_score_winsorized ±3σ)과 같다
Z_WIDTH_U, Z_WIDTH_S = 6.9, 5.35   # z 칸 폭 상한(유니버스 · 업종) — 결측 글이 길어도 넓히지 않는다
SMALL_SECTOR = 5   # Sector 축 평균을 회색으로 두는 종목 수 미만(평균이 종목 몇 개의 값일 뿐)

# 비교 열에서 빼는 모델(model_id) — v4 는 결함 수정 전까지 뺀다(N-27 §8-17). 판 계산·저장은 그대로
HIDDEN_COMPARE_MODELS = frozenset({"v4_rank"})


def hidden_compare_reason() -> str:
    """비교 열에서 뺀 이유 한 줄(메타·모델 비교 시트) — `HIDDEN_COMPARE_MODELS` 에서 만든다."""
    return f"{'·'.join(sorted(HIDDEN_COMPARE_MODELS))} 비교 열은 결함 수정 전까지 뺌(N-27)"


def model_label(spec_id: str) -> str:
    if spec_id in MODEL_LABELS:
        return MODEL_LABELS[spec_id]
    return MODEL_LABELS.get(spec_id.split("@")[0], spec_id)


def compared_specs(view: DayView) -> list[str]:
    """엑셀에 싣는 비교 모델 — 주 모델 밖 spec 중 `HIDDEN_COMPARE_MODELS` 를 뺀 것(제목·점수 시트
    '다른 모델 순위'·모델 비교 시트·메타가 이 한 곳을 따른다)."""
    return [s for s in sorted(view.run.specs)
            if s != view.spec_id and s.split("@")[0] not in HIDDEN_COMPARE_MODELS]


def left_out_specs(view: DayView) -> list[str]:
    """판에는 있지만 비교 열에서 뺀 모델(N-27)."""
    shown = set(compared_specs(view))
    return [s for s in sorted(view.run.specs) if s != view.spec_id and s not in shown]


def _num(v: object) -> float | None:
    if isinstance(v, int | float) and not isinstance(v, bool) and v == v:
        return float(v)
    return None


def period_add(period: str, months: int) -> str:
    """'YYYY/MM' + 개월."""
    y, m = int(period[:4]), int(period[5:7])
    k = y * 12 + (m - 1) + months
    return f"{k // 12:04d}/{k % 12 + 1:02d}"


# ── fi 원자료 ────────────────────────────────────────────────────────────────
@dataclass
class FiData:
    """엑셀이 읽는 factor_inputs 원자료(판 id 고정)."""

    close: dict[str, float | None] = field(default_factory=dict)
    fins: dict[str, list[Row]] = field(default_factory=dict)
    cons: dict[str, list[Row]] = field(default_factory=dict)
    flow_last: dict[str, str | None] = field(default_factory=dict)

    def annual(self, t: str, need: Sequence[str] = ("ni",)) -> Row | None:
        """need 열 중 하나라도 값이 있는 가장 최근 연간 확정 행."""
        rows = [r for r in self.fins.get(t, ()) if r.get("period_type") == "annual"
                and any(r.get(c) is not None for c in need)]
        return max(rows, key=lambda r: str(r["period"]), default=None)

    def annual_at(self, t: str, period: str) -> Row | None:
        return next((r for r in self.fins.get(t, ()) if r.get("period_type") == "annual"
                     and str(r["period"]) == period), None)

    def quarters(self, t: str) -> dict[str, Row]:
        return {str(r["period"]): r for r in self.fins.get(t, ())
                if r.get("period_type") == "quarter"}

    def revenue_net(self, t: str, period: str) -> bool:
        """그 결산기 확정 매출이 순액(순영업이익 — 은행·증권·카드, fi1.2.0 연간 행 `revenue_basis`
        'net')인가. 열이 없는 옛 fi 판은 늘 False."""
        row = self.annual_at(t, period)
        return (row is not None and row.get("revenue") is not None
                and row.get("revenue_basis") == "net")

    def cons_at(self, t: str, period: str, horizon: str = "cur") -> Row | None:
        return next((r for r in self.cons.get(t, ()) if str(r["target_period"]) == period
                     and r.get("horizon") == horizon), None)

    def fy0(self, t: str, d: str) -> str | None:
        """당해 결산기 = D 의 'YYYY/MM' 이상 중 최소(엔진 `_current_period` 와 같은 규칙)."""
        asof = f"{d[:4]}/{d[5:7]}"
        periods = [str(r["target_period"]) for r in self.cons.get(t, ())
                   if r.get("target_period") is not None and str(r["target_period"]) >= asof]
        return min(periods, default=None)

    def fy_month(self, t: str) -> str:
        """결산월 'MM' — 컨센서스 결산기(가장 늦은 것)의 월, 컨센서스가 없으면 확정 연간 행의 월,
        둘 다 없으면 '12'. 실적 시트 연간 칸 = 결산기가 끝나는 연도(사용자 결정 10-06).
        U22 전까지 fi 연간 행은 12월 결산만이라 두 번째 대안은 늘 '12' — 컨센서스 없는
        비12월 결산사는 12월로 떨어진다(물결 4)."""
        p = max((str(r["target_period"]) for r in self.cons.get(t, ())
                 if r.get("target_period") is not None), default=None)
        if p is None:
            a = self.annual(t, ("revenue", "op", "ni"))
            p = None if a is None else str(a["period"])
        return "12" if p is None else p[5:7]


def _group(rows: Sequence[Row]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for r in rows:
        out.setdefault(str(r["ticker"]), []).append(r)
    return out


def load_fi(fi_root: Path, view: DayView) -> FiData:
    d = date.fromisoformat(view.date).isoformat()          # SQL 에 넣기 전 형식 검증
    bid = view.run.fi_build_id
    close = {str(r["ticker"]): _num(r["close"]) for r in read_fi(
        fi_root, "fi_prices", bid, columns=("ticker", "close"), where=f"date = DATE '{d}'")}
    flow_last = {str(r["ticker"]): iso(r["last_date"]) for r in read_fi(
        fi_root, "fi_flows", bid, select="ticker, max(date) AS last_date", tail="GROUP BY ticker")}
    return FiData(close=close, fins=_group(read_fi(fi_root, "fi_fin_summary", bid)),
                  cons=_group(read_fi(fi_root, "fi_consensus", bid)), flow_last=flow_last)


# ── 공통 열 ──────────────────────────────────────────────────────────────────
def id_cols(full: bool = False) -> tuple[Col, ...]:
    base = (Col("code", "코드", "id", None, 7.0, definition="종목코드 6자리(문자)"),
            Col("name", "이름", "id", None, 14.0, definition="종목명(fi_universe)"))
    l1 = Col("l1", "대분류", "id", None, 10.0, definition="WICS 대분류(fi_universe 기준 스냅샷)")
    if not full:
        return (*base, l1)
    # 시총 · 거래대금 · 주가는 매일 엑셀 Score 만 쓴다(주간 엑셀은 앞 다섯 열만). 폭은 옛 머리 글
    # 기준(확정 샘플 그대로), 거래대금은 10억 미만만 소수 1자리
    return (*base,
            Col("market", "시장", "id", None, 5.0, definition="KS = KOSPI · KQ = KOSDAQ"),
            l1,
            Col("l2", "중분류", "id", None, 14.0, definition="WICS 중분류"),
            Col("mcap", "시총\n(억원)", "num", "#,##0", 9.0, fit_label="시총(억)",
                definition="D 시가총액, 억원(fi_universe)"),
            Col("adv20", "거래대금\n20일(억원)", "num", "#,##0", 9.0,
                fit_label="거래대금\n20일(억)", fmt_all="[<10]#,##0.0;#,##0",
                definition="최근 20세션 평균 거래대금, 억원(D-13 적격성 재료)"),
            Col("price", "주가\n(원)", "num", "#,##0", 9.0, fit_label="주가",
                definition="D 종가, 원(fi_prices)"))


def id_values(view: DayView, t: str, fi: FiData | None = None) -> Row:
    u = view.uni.get(t, {})
    row = view.by_ticker.get(t, {})
    return {"code": t, "name": u.get("name") or "",
            "market": market_label(u.get("market")),
            "l1": u.get("sector_l1_name") or row.get("sector_l1") or "",
            "l2": u.get("sector_l2_name") or row.get("sector_l2") or "",
            "mcap": _num(u.get("market_cap")), "adv20": _num(u.get("adv20")),
            "price": None if fi is None else fi.close.get(t)}


def title_for(view: DayView, sheet: str, note: str = "") -> Title:
    others = compared_specs(view)
    model_line = f"주 모델 {view.spec_id}({model_label(view.spec_id)})"
    if others:
        model_line += " · 비교 " + ", ".join(f"{s}({model_label(s)})" for s in others)
    source = (f"{view.date} 기준({view.run.basis}) · Source: quant-ledger model·factor_inputs "
              f"· 판 {view.run.build_id} · fi {view.run.fi_build_id}")
    return Title(sheet, model_line, source, note)


def model_id_of(spec_id: str) -> str:
    """'scope@2.0' → 'scope'."""
    return spec_id.split("@")[0]


def band(view: DayView, title: str, sort_label: str | None,
         notes: Sequence[tuple[str | None, str]] = (), *, meta: bool = False) -> Band:
    """확정 양식 1~5행 — 표지와 값은 ':' 로, 사람이 읽는 이름만(모델 id · 데이터 id 는 Meta)."""
    others = compared_specs(view)
    when = f"{view.date} {BASIS_WORDS.get(view.run.basis, view.run.basis)}"
    where = "아래 표" if meta else "Meta 시트"
    return Band(sort_label, title,
                (f"주 모델: {model_label(view.spec_id)}",
                 "비교: " + (", ".join(model_label(s) for s in others) or "없음")),
                (f"기준일: {when}", f"{SOURCE_TEXT} · 데이터 id: {where}"), tuple(notes))


def bucket_weight_label(view: DayView, b: str) -> str:
    if view.spec is None:
        return bucket_label(b)
    return f"{bucket_label(b)} {view.spec.buckets[b] * 100:.0f}%"


def score_indicators(view: DayView) -> list[tuple[str, str, str]]:
    """(key, bucket, definition) — role=score, 레지스트리 순서. spec 이 없으면 데이터에서."""
    return _indicators(view, "score")


def _indicators(view: DayView, role: str) -> list[tuple[str, str, str]]:
    if view.spec is not None:
        return [(i.key, i.bucket, i.definition) for i in view.spec.indicators if i.role == role]
    seen: dict[str, tuple[str, str, str]] = {}
    for per in view.ind.values():
        for k, r in per.items():
            if r.get("role") == role and k not in seen:
                seen[k] = (k, str(r.get("bucket")), "")
    return list(seen.values())


# ── scope·v3 점수 표 원값(E-10, N-25 Q7) ─────────────────────────────────────
RATIO, PCT, TIMES, FLOW = "비율", "%", "배", "순매수/시총"
UNIT_FMT = {RATIO: "0.0000", PCT: "#,##0.00", TIMES: "#,##0.00", FLOW: "0.000"}
UNIT_TEXT = {RATIO: "비율(0.05 = 5%)", PCT: "%", TIMES: "배",
             FLOW: "순매수/시총(순매수 백만원 ÷ 시총 억원, 1 = 시총의 1%)"}


@dataclass(frozen=True)
class RawCol:
    """점수 표의 원값·표식 열 하나. unit 이 빈 문자열이면 표식(글자) 열.

    label = 단위를 뺀 옛 머리 글(열 폭 기준) · head = 7행 머리 글 · group = 6행 그룹 이름 —
    그룹에 있는 말(수익률 · 영업이익 추정 · 단위)은 머리에서 되풀이하지 않는다(확정 샘플)."""

    key: str
    bucket: str
    label: str
    unit: str
    definition: str
    head: str = ""
    group: str = ""

    @property
    def fit_label(self) -> str:
        return f"{self.label}\n({self.unit})" if self.unit else self.label

    @property
    def shown(self) -> str:
        return self.head or self.fit_label


RAW_GROUPS = {"momentum": "모멘텀 · 수익률(비율)", "flow": "수급 · 순매수/시총(%)",
              "quality": "퀄리티", "valuation": "밸류"}


def _rev_cols(metric: str, name: str) -> tuple[RawCol, ...]:
    group = f"리비전 · {name} 추정 변화(비율)"
    out: list[RawCol] = []
    for p, ago in (("1w", "1주 전"), ("1m", "1개월 전"), ("3m", "3개월 전")):
        out += [RawCol(f"{metric}_change_{p}", "revision", f"{name} 추정 {p.upper()}", RATIO,
                       f"당해 결산기 {name} 컨센서스 (cur − {ago}) ÷ |{ago}|. 현재값·비교값이 "
                       "없거나 비교값이 0이면 0(v3 규약), 컨센서스 쌍이 없으면 빈칸",
                       p.upper(), group),
                RawCol(f"{metric}_{p}_flag", "revision", f"{name} {p.upper()}\n표식", "",
                       f"{name} {p.upper()} 부호 전환 — 흑전 · 적전 · 적확 · 적축(v3 규약, 변화율 "
                       "칸은 그대로)", f"{p.upper()}\n표식", group)]
    return tuple(out)


def _flow_cols(prefix: str, name: str, subject: str) -> tuple[RawCol, ...]:
    return tuple(RawCol(f"flow_{prefix}_{n}d", "flow", f"{name} {n}일", FLOW,
                        f"{subject} 순매수 — D 이하 최근 수급 행 {n}개 합(백만원) ÷ 시총(억원). "
                        "1 = 시총의 1%", f"{name}\n{n}일", RAW_GROUPS["flow"])
                 for n in (5, 20))


def _plain_cols(bucket: str, *cols: tuple[str, str, str, str]) -> tuple[RawCol, ...]:
    return tuple(RawCol(k, bucket, label, unit, d, "", RAW_GROUPS[bucket])
                 for k, label, unit, d in cols)


# scope·v3_zscore 점수 표(contracts.V3_SCORE_COLUMNS) 원값 27열 + 표식 6열 — 엔진 값을 배율 없이
# 그대로 싣는다. 순서 = 버킷 순서(momentum·revision·flow·quality·valuation) · 버킷 안 엔진 순서
V3_RAW: tuple[RawCol, ...] = (
    *(RawCol(f"r{m}", "momentum", f"{m.upper()} 수익률", RATIO,
             f"{n}세션 전 대비 수익률(수정종가, 없으면 종가). 가격 행이 {n}개 이하면 빈칸",
             m.upper(), RAW_GROUPS["momentum"])
      for m, n in (("1m", 20), ("3m", 60), ("6m", 120), ("9m", 180), ("12m", 240))),
    *_rev_cols("op", "영업이익"), *_rev_cols("ni", "순이익"),
    *_flow_cols("inst", "기관", "기관 합계"), *_flow_cols("for", "외국인", "외국인"),
    *_flow_cols("pe", "사모", "사모펀드"),
    *_plain_cols(
        "quality",
        ("qual_gpa", "GP/A", RATIO, "매출총이익 ÷ 자산총계(최근 연간 확정)"),
        ("qual_roa", "ROA", PCT, "최근 연간 확정 ROA(DART 순이익 ÷ 자산총계 × 100)"),
        ("qual_fcf_assets", "FCF/자산", RATIO,
         "FCF(영업현금흐름 − |capex|) ÷ 자산총계(최근 연간 확정)"),
        ("qual_debt_ratio", "부채비율", PCT,
         "부채총계 ÷ 자본총계 × 100(최근 연간 확정, 낮을수록 좋다)"),
        ("qual_gpa_change", "GP/A 변화", RATIO, "(GP/A − 전기 GP/A) ÷ |전기 GP/A|(연간 2기)"),
        ("qual_std_20d", "20일 변동성", RATIO,
         "최근 21행 일간 수익률 표본 표준편차(낮을수록 좋다)")),
    *_plain_cols(
        "valuation",
        ("val_per", "PER", TIMES, "최근 연간 확정 PER(WISE, 양수만 — 적자는 빈칸)"),
        ("val_pbr", "PBR", TIMES, "최근 연간 확정 PBR(WISE, 양수만)"),
        ("val_ev_ebitda", "EV/EBITDA", TIMES,
         "최근 연간 EV/EBITDA(WISE, 양수만). scope 는 가중에서 뺐다(점수에 안 씀, 10-02 결정)"),
        ("val_dividend_yield", "배당수익률", PCT, "최근 연간 배당수익률(WISE 투자지표)")),
)


def score_raw_columns(view: DayView) -> tuple[RawCol, ...]:
    """주 모델 엔진이 v3_zscore(scope·v3 원본)면 점수 표 원값 열(V3_RAW), 아니면 빈 튜플.
    점수 표에 없는 열은 그 칸만 빈다(`row.get`) — 열 하나가 빠졌다고 33열·업종 3열·단위 줄을 통째로
    잃지 않는다(검토 사소 1). 빠진 열은 sheet_raw 가 로그 한 줄로 남긴다."""
    return V3_RAW if view.z_engine else ()


# 퀄리티 손익 지표(엔진 원값 열) — 다 비고 변동성만 남으면 비고에 적는다
# (G-27 표시, 원인은 적지 않는다)
QUALITY_PL_KEYS = tuple(c.key for c in V3_RAW if c.bucket == "quality" and c.key != "qual_std_20d")
# Score 비고 표지 — 짧게(확정 샘플 · 뜻은 Q7 머리 메모 범례)
QUALITY_VOL_ONLY_NOTE = "퀄리티 변동성만"
NET_REVENUE_NOTE = "매출 순액"


def quality_vol_only(view: DayView, t: str) -> bool:
    """scope·v3 퀄리티가 변동성(qual_std_20d) 한 지표로만 계산됐는가 — 손익 지표가 다 비었다.
    외화 재무 격리(241560)·법인 매핑·신규 상장(477850·0011T0) 등 이유가 여럿이라 원인은
    적지 않는다."""
    if not score_raw_columns(view):
        return False
    row = view.by_ticker.get(t, {})
    return (all(_num(row.get(k)) is None for k in QUALITY_PL_KEYS)
            and _num(row.get("qual_std_20d")) is not None)


def raw_units_text(cols: Sequence[RawCol]) -> str:
    """단위표 한 줄 — 단위마다 열 이름."""
    by_unit: dict[str, list[str]] = {}
    for c in cols:
        if c.unit:
            by_unit.setdefault(c.unit, []).append(c.key)
    return " / ".join(f"{UNIT_TEXT[u]}: {' · '.join(keys)}" for u, keys in by_unit.items())


def bucket_missing_reason(view: DayView, t: str, b: str) -> str:
    """버킷이 빈 이유 — 버킷결측(NN%) 표식이 있으면 그것, 아니면 첫 지표의 결측 사유."""
    per = view.ind.get(t, {})
    keys = [k for k, bk, _ in score_indicators(view) if bk == b]
    flags = [f for k in keys for f in split_flags(per.get(k, {}).get("flag"))]
    for f in flags:
        if f.startswith("버킷결측"):
            return f
    for k in keys:
        r = per.get(k, {})
        if r.get("raw") is None:
            fl = split_flags(r.get("flag"))
            if fl:
                return fl[0]
    return "원천없음"


# ── 추정치 유예 · 앞 주 모델 이력(N-47 · Q-E1) ───────────────────────────────────
@dataclass(frozen=True)
class Grace:
    """주 모델 추정치 유예 표시(N-47) — spec 유예 거래일(0 이면 표시 없음)과 종목별 1M 흐름 날짜
    중 유예였던 날 수."""

    days: int = 0
    month: Mapping[str, int] = field(default_factory=dict)


def grace_of(view: DayView, fi_root: Path, trend: Trend) -> Grace:
    """주 모델 유예가 있으면 1M 흐름 날짜(`trend.dates` — 1M 흐름과 같은 창)마다 그날 fi 판
    fi_universe 에서 coverage_state 가 grace(나이 ≥ 1)인 종목을 센다. 그날 판을 못 읽으면(지운 fi
    판 · 다른 루트의 T-50 아침 판 · 열이 없는 옛 판) 그날은 세지 않는다."""
    days = 0 if view.spec is None else view.spec.universe.coverage_grace_days
    if not days:
        return Grace()
    month: dict[str, int] = {}
    for day in trend.dates:
        r = trend.runs.get(day)
        if r is None:
            continue
        try:
            rows = read_fi(fi_root, "fi_universe", r.fi_build_id,
                           columns=("ticker", "coverage_age_days"),
                           where="coverage_state = 'grace'")
        except (DeliverError, duckdb.Error) as e:
            log.info("1M 유예 일수 — %s fi 판 %s 를 읽지 못해 그날은 세지 않는다: %s",
                     day, r.fi_build_id, e)
            continue
        for x in rows:
            age = x.get("coverage_age_days")
            if isinstance(age, int) and age >= 1:
                t = str(x["ticker"])
                month[t] = month.get(t, 0) + 1
    return Grace(days, month)


def grace_notes(grace: Grace, t: str, state: object, age: object) -> tuple[str | None, ...]:
    """Score 비고 유예 표지 — 지금 유예 중이면 '유예 n일째'(n = 마지막 신선일부터 거래일),
    1M 흐름 날짜 중 유예였던 날이 있으면 '1M 유예 n일'."""
    if not grace.days:
        return ()
    now = f"유예 {age}일째" if state == "grace" and isinstance(age, int) and age >= 1 else None
    n = grace.month.get(t)
    return now, (f"1M 유예 {n}일" if n else None)


def older_mark(trend: Trend) -> tuple[str, str] | None:
    """(앞 주 모델 라벨, 마지막으로 앞 주 모델 순위를 쓴 날 MM-DD) — 1M 흐름 · 비교 데이터를 앞 주
    모델로 이은 날이 없으면 None."""
    if not trend.older:
        return None
    last = max(trend.older)
    return model_label(trend.older[last]), last[5:]


def switch_day(trend: Trend) -> str | None:
    """1M 흐름에서 주 모델 순위를 처음 쓴 날 MM-DD(그 앞은 앞 주 모델) — 앞 주 모델을 안 썼으면
    None."""
    if not trend.older:
        return None
    return next((d[5:] for d in trend.dates if d not in trend.older), None)


# ── 후보 30 과 변화(확정 샘플 Score AM · AN) ─────────────────────────────────
ENTRY, EXIT = "진입", "이탈"
NO_PREV = "전일 데이터 없음"


@dataclass(frozen=True)
class Candidates:
    """오늘 후보(순위순, 대분류마다 최대 k 까지 top_n 종목)와 전일 후보 대비 변화.

    전일 후보 = 전일 순위에 같은 규칙(전일 대분류 = 오늘 값 — 전일 업종 스냅샷은 읽지 않는다).
    변화: 진입(오늘 후보인데 전일 후보 아님) · 이탈(전일 후보인데 오늘 아님 — 오늘 순위가
    top_n 안이면 'k종목 초과' 로 밀린 이탈) · k종목 초과(순위 top_n 안인데 대분류가 이미
    k종목, 전일도 후보 아님). 전일 순위가 하나도 없으면(첫 날 · 같은 basis 직전 성공 데이터 없음 ·
    읽기 실패) 변화는 모두 비운다 — 30종목 전부 '진입'으로 보이면 틀린 말이다(코드 리뷰 I-2)."""

    picked: frozenset[str]
    change: Mapping[str, str]
    cut: int | None                 # 오늘 후보 중 가장 낮은 순위(커트라인)
    full: tuple[str, ...]           # k종목이 찬 대분류 이름(Sector 코드순)
    top_n: int
    k: int
    no_prev: bool = False           # 전일 순위가 없어 변화를 비웠다

    @property
    def full_text(self) -> str:
        return "·".join(self.full) or "없음"


def candidates(view: DayView, prev_rank: Mapping[str, int | None]) -> Candidates:
    out = view.output
    top_n, k = out.top_n, out.max_per_sector
    level = "sector_l2" if out.sector_level == "L2" else "sector_l1"

    def sector_of(t: str) -> object:
        return view.by_ticker.get(t, {}).get(level)

    picked = list(view.candidates)
    no_prev = not any(r is not None for r in prev_rank.values())
    if no_prev:
        log.warning("전일 데이터 없음(주 모델 %s · %s %s) — Score 변화 열을 비운다",
                    view.spec_id, view.date, view.run.basis)
    order = sorted((t for t in (ticker_of(r) for r in view.rows) if prev_rank.get(t) is not None),
                   key=lambda t: prev_rank[t] or 0)
    before = {str(r["t"]) for r in cap_candidates([{"t": t} for t in order], top_n, k,
                                                  lambda r: sector_of(str(r["t"])))}
    now = set(picked)
    change: dict[str, str] = {}
    for r in () if no_prev else view.rows:          # 전일 순위가 없으면 변화를 매기지 않는다
        t, rk = ticker_of(r), rank_of(r)
        inside = rk is not None and rk <= top_n
        if t in now and t not in before:
            change[t] = ENTRY
        elif t in before and t not in now:
            change[t] = f"{EXIT}({k}종목 초과)" if inside else EXIT
        elif inside and t not in now:
            change[t] = f"{k}종목 초과"
    counts: dict[object, int] = {}
    for t in picked:
        counts[sector_of(t)] = counts.get(sector_of(t), 0) + 1
    name_key = "sector_l2_name" if level == "sector_l2" else "sector_l1_name"
    names: dict[object, str] = {}
    for t in picked:
        names.setdefault(sector_of(t), str(view.uni.get(t, {}).get(name_key) or sector_of(t)))
    full = tuple(names[s] for s in sorted(counts, key=str) if counts[s] >= k)
    cut = max((view.rank(t) or 0 for t in picked), default=None)
    return Candidates(frozenset(picked), change, cut, full, top_n, k, no_prev)


def candidate_rule_text(c: Candidates) -> str:
    """Meta '후보 규칙' 값."""
    return (f"상위 {c.top_n} · 대분류(L1)당 최대 {c.k}: 순위순으로 내려가며 {c.k} 가 찬 대분류는 "
            f"건너뛰고 아래 순위로 채움 · 오늘 커트라인: 순위 {c.cut if c.cut is not None else '-'}"
            f" · {c.k}종목이 찬 대분류: {c.full_text}")


def composite_definition(view: DayView) -> str:
    """Score 점수 열 정의 — z 엔진은 축 가중합 식과 그날 범위를 적는다."""
    if not view.z_engine or view.spec is None:
        return "있는 버킷 점수의 가중평균(0~100, 엔진 값). 제외 종목도 점수는 남는다"
    # 가중치 글은 Meta '가중치' 줄(소수 1자리 %)을 다시 읽은 값과 같게 — 30.0% → 30
    weights = " · ".join(f"{bucket_label(b)} {float(f'{w * 100:.1f}'):g}"
                         for b, w in view.spec.buckets.items()) + "%"
    comps = [c for r in view.rows if (c := composite_of(r)) is not None]
    span = ""
    if comps:
        lo = f"{min(comps):.2f}"
        span = f"({view.date[5:10]} 데이터 {lo.replace('-', '−', 1)}~{max(comps):+.2f})"
    return (f"{len(view.buckets)}축 유니버스(Z)의 가중합({weights}). 결측 축은 남은 가중치로 다시 "
            f"나눔. 0 = 유니버스 평균 근처, 높을수록 좋음{span}. 제외 종목도 점수는 남는다")


NOTE_LEGEND = (
    ("애널 ≤3", "의견 낸 애널리스트가 3명 이하 (추정치가 흔들리기 쉬움)"),
    ("의견 0(3M)", "최근 3개월 동안 의견 낸 애널리스트 없음"),
    ("애널 미상", "애널리스트 수를 모름"),
    ("N월 결산", "12월 결산이 아닌 회사"),
    (NET_REVENUE_NOTE, "매출을 순액으로 적는 회사(증권·카드 등), 매출 YoY 비움"),
    (QUALITY_VOL_ONLY_NOTE, "퀄리티를 주가 변동성 하나로만 계산"),
)


# ── 시트: Score ──────────────────────────────────────────────────────────────
def sheet_scores(wb: Workbook, view: DayView, fi: FiData, prev_rank: Mapping[str, int | None],
                 trend: Trend, cands: Candidates,
                 grace: Grace | None = None) -> tuple[Dictionary, Dictionary]:
    """반환 = (열 사전, 사전 끝에 붙일 후보 · 변화 두 줄). `grace` = 추정치 유예 표시(N-47)."""
    grace = grace or Grace()
    others = compared_specs(view)
    year = int(view.date[:4])
    k, top_n = cands.k, cands.top_n
    groups = [
        Group("종목", id_cols(full=True)),
        Group("종합", (
            Col("rank", "순위", "rank", "#,##0", 7.0, key_col=True, color=False, core=True,
                definition="주 모델 순위(1 = 최고). 제외 종목은 빈칸"),
            Col("composite", "점수", "num", "#,##0.00", fit_label="종합\n점수", core=True,
                definition=composite_definition(view)),
            Col("prev_rank", "전일\n순위", "num", "#,##0", color=False, core=True,
                definition=("직전 날짜 데이터(아침은 아침끼리, 장 마감은 장 마감끼리)의 "
                            "주 모델 순위")),
            Col("d1m", "Δ순위\n1M", "chg", "#,##0", zero_mid=True, core=True, definition=(
                "1M 비교 데이터(D−1개월 이하 마지막 날짜) 순위 − 오늘 순위(양수 = 상승). "
                "색 가운데 = 0 — 상승 초록 · 하락 빨강")),
            Col("t1m", "1M\n흐름", "spark", None, 11.0, core=True, definition=(
                "1M 동안 날짜별 순위 꺾은선(원순위, 위 = 상승). 선 색 = Δ순위 1M 부호 = 선의 "
                "처음 → 끝 방향")),
        )),
        Group("상태", (
            # 너비 상한 26 — 가장 긴 v4 라벨 '고점근접+반전 게이트(pull_gate)'가 qpack fit 25.05
            # (N-26 4.5 — 상한 16 이면 v4 라벨 8개 중 6개가 잘렸다)
            Col("excl", "제외 사유", "txt", None, 26.0, definition=(
                "엔진 exclude_reason — D-13 적격성(관리·정지·감사·지연·거래대금) · 데이터 부족 · "
                "버킷 게이트(고점근접+반전 하위 30%)")),
            Col("cov", "커버리지", "txt", None, 8.0, definition=(
                "추정치 신선도(T2.11): 신선 · 유예 D+n(마지막 신선일부터 거래일 n) · 소멸")),
            # 표지는 짧게(애널 ≤3 등), 폭 17 고정 — 뜻은 머리 칸 메모 범례
            Col("note", "비고", "txt", None, fixed=17.0, definition=(
                "추정기관수(WISE 최근 3개월 투자의견을 낸 증권사 수): 0 = 최근 3개월 의견 없음 · "
                "1~3 = 애널리스트 3명 이하 · 모름 = 애널리스트 수 미상 · 4 이상은 빈칸. "
                "결산월이 12월이 아니면 'N월 결산'(Earnings 시트 연간 칸 = 결산기가 끝나는 연도) · "
                "scope 퀄리티 손익 지표가 다 비면 '퀄리티 = 변동성만(손익 지표 없음)' · "
                "확정 매출이 순액이라 매출 y-y 를 비우면 '매출 = 순영업이익(순액) — "
                "총액 추정치와 y-y 비교 안 함' 을 ' · ' 로 덧붙인다")),
        ), gap=False),
    ]
    for b in view.buckets:
        name = bucket_label(b)
        if view.z_engine:         # z 엔진 — 버킷 점수가 z 라 z 그대로(10-08 '둘 다 z')
            cols = (
                Col(f"{b}_u", "유니버스\n(Z)", "num", "#,##0.00", Z_WIDTH_U, color=True,
                    span=Z_SPAN, fit_label=f"{name}\n유니버스 z", fmt_all="0.0",
                    dict_label="유니버스(Z)",
                    definition=(f"{name} 버킷 점수(엔진 z — 종합 점수에 들어간 값 그대로, "
                                "±3 상한). 0 = 유니버스 평균, 높을수록 좋다")),
                Col(f"{b}_s", "업종\n(Z)", "num", "#,##0.00", Z_WIDTH_S, color=True, span=Z_SPAN,
                    fit_label=f"{name}\n업종 z", fmt_all="0.0", dict_label="업종(Z)",
                    definition=(f"{name} 버킷 z 를 WICS 대분류 안에서 다시 z(대분류 평균·표본 "
                                "표준편차, ±3σ 로 자른 뒤 표준화 — 엔진 z 와 같은 식, ±3 안). "
                                "값 있는 종목 < 5 이거나 표준편차 0 이면 유니버스 z. 순위·점수에 "
                                "쓰지 않는 참고값")))
        else:
            cols = (
                Col(f"{b}_u", "유니버스", "pct", "#,##0.0", fit_label=f"{name}\n유니버스",
                    definition=f"{name} 버킷 점수(엔진)의 유니버스 백분위 0~100 — 높을수록 좋다"),
                Col(f"{b}_s", "업종", "pct", "#,##0.0", fit_label=f"{name}\n업종",
                    definition=f"{name} 버킷 점수의 WICS 대분류 안 백분위(표본 < 5 면 유니버스)"))
        groups.append(Group(bucket_weight_label(view, b), cols))
    groups.append(Group("결측", (Col("miss", "축", "txt", None, 14.0, fit_label="결측 축",
                                   definition=("버킷 점수가 빈 축(점수 대상 종목만). 사유는 축 "
                                               "칸의 '결측(사유)'")),)))
    if others:
        groups.append(Group("비교 순위", (
            *(Col(f"r:{s}", model_label(s), "num", "#,##0", color=False,
                  definition=f"{s} 순위(같은 날짜)") for s in others),
            Col("max_diff", "최대 차이", "num", "#,##0",
                definition="주 모델 포함 모델 순위의 최댓값 − 최솟값(순위 둘 이상일 때)"),
        )))

    rows: list[Row] = []
    for row in view.rows:
        t = ticker_of(row)
        u = view.uni.get(t, {})
        r = rank_of(row)
        pr = prev_rank.get(t)
        mm = fi.fy_month(t)
        state = row.get("coverage_state", u.get("coverage_state"))
        notes = (analyst_note(u.get("n_analysts")),
                 *grace_notes(grace, t, state, u.get("coverage_age_days")),
                 None if mm == "12" else f"{int(mm)}월 결산",
                 QUALITY_VOL_ONLY_NOTE if quality_vol_only(view, t) else None,
                 NET_REVENUE_NOTE if revenue_basis_mixed(fi, t, year) else None)
        out: Row = {**id_values(view, t, fi), "rank": r, "composite": composite_of(row),
                    "prev_rank": pr, "d1m": trend.delta("1M", t, r),
                    "excl": exclude_label(row.get("exclude_reason")),
                    # v3·v2 점수 행엔 신선도 열이 없다 → 그 판 fi_universe 값(v4 는 같은 값을 행에 싣는다)
                    "cov": coverage_label(state, u.get("coverage_age_days")),
                    "note": " · ".join(n for n in notes if n) or None,
                    "cand": "후보" if t in cands.picked else None,
                    "chg": cands.change.get(t)}
        scored = view.scored(t)
        miss = []
        for b in view.buckets:
            if view.bucket_score(t, b) is not None:
                if view.z_engine:
                    out[f"{b}_u"] = view.bucket_score(t, b)
                    out[f"{b}_s"] = view.zsec[b].get(t)
                else:
                    out[f"{b}_u"] = view.upct[b].get(t)
                    out[f"{b}_s"] = view.spct[b].get(t)
            elif scored:
                out[f"{b}_u"] = out[f"{b}_s"] = missing(bucket_missing_reason(view, t, b))
                miss.append(bucket_label(b))
        out["miss"] = "·".join(miss)
        ranks = [x for x in (r, *(view.other_ranks[s].get(t) for s in others)) if x is not None]
        for s in others:
            out[f"r:{s}"] = view.other_ranks[s].get(t)
        out["max_diff"] = max(ranks) - min(ranks) if len(ranks) >= 2 else None
        rows.append(out)
    # 후보 · 변화 — 변화 열 폭은 가장 긴 표지에 맞춘다
    chg_width = max(4.4, max((text_width(str(r["chg"])) + 1 for r in rows if r.get("chg")),
                             default=0.0))
    groups.append(Group(f"후보(대분류당 최대 {k})", (
        Col("cand", "후보", "txt", None, fixed=6.9, definition=(
            f"상위 {top_n} · 대분류(L1)당 최대 {k} 규칙으로 뽑힌 {top_n}종목(Meta 후보 규칙). "
            f"순위순으로 내려가며 {k} 가 찬 대분류는 건너뜀. 빈칸: 후보 아님")),
        Col("chg", "변화", "txt", None, fixed=chg_width, definition=(
            f"진입: 전일 후보 {top_n} 에 없던 오늘 후보 · 이탈: 전일 후보였는데 오늘 후보 아님 · "
            f"이탈({k}종목 초과): 순위는 {top_n} 안인데 대분류가 이미 {k}종목이라 밀려 이탈 · "
            f"{k}종목 초과: 순위 ≤ {top_n} 인데 대분류 {k} 초과. 전일 후보 = 전일 순위에 같은 규칙 "
            "적용(전일 대분류 = 오늘 값 가정). 노랑(전일 순위 칸) = 진입")),
    ), gap=False))

    first_axis = f"{view.buckets[0]}_u" if view.buckets else None
    axis_note = ("유니버스(Z): 종합 점수에 들어간 값 · 업종(Z): 같은 대분류 안에서 다시 잰 "
                 "참고값 · 값 없음: 결측(사유)" if view.z_engine else
                 "유니버스: 버킷 점수의 유니버스 백분위 · 업종: 같은 대분류 안 백분위 · "
                 "값 없음: 결측(사유)")
    notes_5: list[tuple[str | None, str]] = [
        (None, "순위: 1 = 가장 좋음 · 색: 초록 좋음, 빨강 나쁨 · 노랑: 오늘 후보 진입")]
    if first_axis:
        notes_5.append((first_axis, axis_note))
    notes_5.append(("cand", (f"후보: 순위순, 대분류당 최대 {k}종목 · {k}종목 초과: 순위 {top_n} "
                             f"안인데 그 대분류가 이미 {k}종목 · 진입·이탈: 전일 후보 대비")))
    sh = write_confirmed(wb, "Score", band(view, "Score: 종목별 종합 점수와 순위",
                                           "정렬: 순위 오름차순 ▲", notes_5),
                         groups, [rows], sort_key="rank")
    ws = sh.ws
    _h6, r0, r1 = sh.blocks[0]
    if rows:
        # 상태 '신선' 은 옅은 회색 글자(이상 값만 눈에 띄게) · 전일 순위 칸 노랑 = 오늘 후보 진입
        cov, prev, chg = sh.letter("cov"), sh.letter("prev_rank"), sh.letter("chg")
        ws.conditional_formatting.add(f"{cov}{r0}:{cov}{r1}", FormulaRule(
            formula=[f'${cov}{r0}="신선"'], font=Font(color="FFA6A6A6")))
        ws.conditional_formatting.add(f"{prev}{r0}:{prev}{r1}", FormulaRule(
            formula=[f'${chg}{r0}="{ENTRY}"'],
            fill=PatternFill("solid", fgColor="FFFFFF00", bgColor="FFFFFF00")))
    # 후보 블록 머리 — 앞 블록과 사이 선은 머리 행만 가는 회색(확정 샘플)
    c_cand, c_chg = sh.cols["cand"], sh.cols["chg"]
    for r in (6, 7):
        left = ws.cell(r, c_cand - 1)
        b = left.border
        left.border = Border(left=b.left, right=SIDE_HDR, top=b.top, bottom=b.bottom)
        cell = ws.cell(r, c_chg)
        b = cell.border
        cell.border = Border(left=SIDE_HDR, right=b.right, top=b.top, bottom=b.bottom)
    # 머리 칸 메모 — 비고 표지 범례 · 후보 규칙 · 변화 뜻
    legend_items = list(NOTE_LEGEND)
    if grace.days:                      # 유예 표지는 유예가 있는 주 모델만(N-47)
        legend_items[3:3] = [
            ("유예 n일째", f"추정치가 사라진 지 n거래일(최대 {grace.days}일 남김)"),
            ("1M 유예 n일", "1M 흐름 기간 중 추정치 유예였던 날 수")]
    legend = ["비고 표지 뜻", "", *(f"■ {a}: {b}" for a, b in legend_items), "",
              "표지가 없으면 빈칸, 여러 개면 ' · ' 로 이어 붙임."]
    ws.cell(7, sh.cols["note"]).comment = note("\n".join(legend), "비고", wrap=False)
    # 1M 흐름 머리 메모 — 유예 중 순위의 뜻 · 앞 주 모델로 이은 날(있을 때만)
    flow = []
    if grace.days:
        flow.append(f"유예: 추정치가 사라진 종목도 {grace.days}거래일 동안 유니버스에 남는다. 그동안 "
                    "순위는 마지막 신선일 추정치로 매긴 값(비고 '유예 n일째' · '1M 유예 n일')")
    if (sw := switch_day(trend)) is not None and (mark := older_mark(trend)) is not None:
        flow.append(f"{sw} 이전은 {mark[0]} 순위(주 모델이 {model_label(view.spec_id)} 로 "
                    "바뀌기 전 — N-47)")
    if flow:
        ws.cell(7, sh.cols["t1m"]).comment = note(
            "\n".join(["1M 흐름: 1M 동안 날짜별 순위 꺾은선(위 = 상승, 선 색 = Δ순위 1M 부호)",
                       *flow]))
    ws.cell(7, c_cand).comment = note(
        f"후보: 상위 {top_n} · 대분류(L1)당 최대 {k}(Meta 후보 규칙). 순위순으로 내려가며 "
        f"{k} 가 찬 대분류는 건너뜀. 오늘 커트라인: 순위 "
        f"{cands.cut if cands.cut is not None else '-'} · "
        f"{k}종목이 찬 대분류: {cands.full_text}.")
    ws.cell(7, c_chg).comment = note(
        f"변화\n■ 진입: 전일 후보 {top_n} 에 없던 오늘 후보(노랑 = 전일 순위 칸)\n"
        "■ 이탈: 전일 후보였는데 오늘 후보 아님\n"
        f"■ 이탈({k}종목 초과): 순위는 {top_n} 안인데 대분류가 이미 {k}종목이라 밀려 이탈\n"
        f"■ {k}종목 초과: 순위 ≤ {top_n} 인데 대분류 {k} 초과(전일도 후보 아님)\n"
        "전일 후보 = 전일 순위에 같은 규칙 적용(전일 대분류 = 오늘 값 가정)"
        + (f"\n{NO_PREV}: 오늘은 변화 열을 비움" if cands.no_prev else ""))
    # 후보 · 변화 두 줄은 Meta 열 사전 끝에(그룹 이름 '후보')
    tail = [("Score", "후보", label, d) for _s, g, label, d in sh.dictionary
            if g.startswith("후보(")]
    main = [e for e in sh.dictionary if not e[1].startswith("후보(")]
    return main, tail


# ── 시트: Raw ────────────────────────────────────────────────────────────
def ind_cells(view: DayView, t: str, key: str) -> tuple[object, object, str]:
    """(표시값, 엔진 백분위, 표식) — 결측은 '결측(사유)', 리비전 이전값<0 은 표식만(E)."""
    r = view.ind.get(t, {}).get(key)
    if r is None:
        return None, None, ""
    flags = split_flags(r.get("flag"))
    raw = _num(r.get("raw"))
    pct = _num(r.get("pct"))
    if raw is None:
        return missing(flags[0] if flags else None), pct, ";".join(flags[1:])
    if key in REVISION_KEYS and PREV_NEGATIVE_FLAGS.intersection(flags):
        return None, pct, ";".join(flags)
    if key in ("EP", "EP_FWD") and raw < 0:
        flags = ["적자", *flags]
    return raw * ind_meta(key).scale, pct, ";".join(flags)


def sheet_raw(wb: Workbook, view: DayView, fi: FiData) -> Dictionary:
    inds = score_indicators(view)
    groups = [Group("종목", id_cols())]
    for b in dict.fromkeys(bk for _, bk, _ in inds):
        cols: list[Col] = []
        for key, bk, definition in inds:
            if bk != b:
                continue
            m = ind_meta(key)
            extra = " · 표시 = 원값 × 100(%)" if m.scale == 100 else ""
            if key in REVISION_KEYS:
                extra += " · 이전값 < 0(흑전·적확·적축)이면 변화율 대신 표식만(E)"
            cols += [
                Col(f"v:{key}", m.label, "num", m.fmt, color=False,
                    definition=f"{key}: {definition}{extra}"),
                Col(f"p:{key}", f"{key}\n백분위", "num", "#,##0.0", color=False, definition=(
                    f"{key} 엔진 백분위 0~100(방향 적용, 동률 평균순위; 밸류·퀄리티는 대분류 안)")),
                Col(f"f:{key}", f"{key}\n표식", "txt", None, 9.0, definition=(
                    "엔진 flag — 부호 전환(흑전·적전·적확·적축) · 적자 · 무배당 · 업종 되돌림 · "
                    "버킷결측(NN%) · 적격미확인(표식)")),
            ]
        groups.append(Group(bucket_label(b), tuple(cols)))
    # scope·v3 는 지표 긴 표가 0행이고 원값이 점수 표 열에 있다 — 그 열을 그대로 싣는다(E-10).
    # 그룹 = 버킷(리비전은 영업이익 · 순이익 두 블록을 구분 열 없이 붙인다)
    raw_cols = score_raw_columns(view)
    gone = [c.key for c in raw_cols if view.rows and c.key not in view.rows[0]]
    if gone:
        log.warning("Raw — 주 모델 %s 판 %s 점수 표에 원값 열 %d개가 없어 그 열만 빈칸: %s",
                    view.spec_id, view.run.build_id, len(gone), ", ".join(gone))
    prev_bucket = None
    for name in dict.fromkeys(c.group for c in raw_cols):
        members = [c for c in raw_cols if c.group == name]
        groups.append(Group(name, tuple(
            Col(f"s:{c.key}", c.shown, "num", UNIT_FMT[c.unit], color=False, fit_label=c.fit_label,
                fmt_all=UNIT_FMT[c.unit] if c.unit == RATIO else None,
                definition=f"{c.key}: {c.definition}. 단위 {UNIT_TEXT[c.unit]}") if c.unit
            else Col(f"s:{c.key}", c.shown, "txt", None, 9.0, fit_label=c.fit_label,
                     definition=f"{c.key}: {c.definition}")
            for c in members), gap=members[0].bucket != prev_bucket))
        prev_bucket = members[0].bucket
    groups.append(Group("데이터 기준", (
        Col("fy", "연간\n기준기", "txt", None, 8.0,
            definition="순이익이 있는 최근 연간 확정 결산기(E/P·배당의 후보 기, 730일 창은 엔진)"),
        Col("fy_avail", "재무\n접수일", "txt", None, 10.0,
            definition="그 연간 행의 available_date(공시·수집으로 알게 된 날)"),
        Col("fq", "분기\n기준", "txt", None, 8.0,
            definition="영업이익이 있는 최근 분기(TTM 끝 분기)"),
        Col("obs", "추정\n관측일", "txt", None, 10.0,
            definition="당해 결산기 컨센서스(cur) 관측일 = 추정 base_date"),
        Col("n_an", "애널리스트\n수", "num", "#,##0", color=False,
            definition=("추정기관 수(fi_universe) = WISE 최근 3개월 투자의견을 낸 증권사 수. "
                        "0 = 의견 없음, 빈칸 = 모름")),
        Col("flow_d", "수급\n최신일", "txt", None, 10.0, definition="fi_flows 에 있는 최신 수급일"),
    )))
    rows: list[Row] = []
    for row in view.rows:
        t = ticker_of(row)
        out: Row = id_values(view, t)
        for key, _, _ in inds:
            out[f"v:{key}"], out[f"p:{key}"], out[f"f:{key}"] = ind_cells(view, t, key)
        for c in raw_cols:
            out[f"s:{c.key}"] = row.get(c.key)
        a = fi.annual(t, ("ni",))
        qs = [p for p, q in fi.quarters(t).items() if q.get("op") is not None]
        fy0 = fi.fy0(t, view.date)
        cur = None if fy0 is None else fi.cons_at(t, fy0)
        out.update(fy=None if a is None else a["period"],
                   fy_avail=None if a is None else iso(a.get("available_date")),
                   fq=max(qs, default=None), obs=None if cur is None else iso(cur.get("obs_date")),
                   n_an=view.uni.get(t, {}).get("n_analysts"), flow_d=fi.flow_last.get(t))
        rows.append(out)
    # 5행 — A5 + 둘째 · 셋째 블록 첫 열 위
    firsts = [g.cols[0].key for g in groups[1:3]]
    texts = (["단위: 머리글 괄호(비율 0.05 = 5%)", "열 뜻: 머리 칸 메모(빨간 삼각형) · 색 없음"]
             if raw_cols else ["열 뜻: 머리 칸 메모(빨간 삼각형) · 색 없음"])
    notes_5 = [(None, "빈칸: 엔진 값 없음" if raw_cols else "결측: 결측(사유)"),
               *zip(firsts, texts, strict=False)]
    sh = write_confirmed(wb, "Raw", band(view, "Raw: 점수 계산에 쓴 원값", "정렬: Score 순위 순",
                                         notes_5),
                         groups, [rows], defs_notes=True, sign_colors=True,
                         page=Page(fit_width=2, over_then_down=True, title_cols="A:C"))
    return sh.dictionary



# ── 시트: Earnings ────────────────────────────────────────────────────────────
METRICS = (("revenue", "매출"), ("op", "영업이익"), ("ni", "순이익"))
N_QUARTERS = 5          # fi_fin_summary 는 분기 5기만 싣는다(계약 창)


def _actual(fi: FiData, t: str, period: str, key: str) -> float | None:
    row = fi.annual_at(t, period)
    return None if row is None else _num(row.get(key))


def _estimate(fi: FiData, t: str, period: str, key: str) -> float | None:
    row = fi.cons_at(t, period)
    return None if row is None else _num(row.get(key))


def _either(fi: FiData, t: str, period: str, key: str) -> float | None:
    """y-y 비교 기준 — 확정 우선, 없으면 추정."""
    a = _actual(fi, t, period, key)
    return a if a is not None else _estimate(fi, t, period, key)


def annual_years(year: int) -> tuple[tuple[str, int, str], ...]:
    """실적 시트 연간 칸 (slot, 결산 연도, 머리글) — 판 날짜의 연도 Y 로 Y−1 · YE · (Y+1)E.
    해가 바뀌면 저절로 넘어간다(N-19). 칸 = 결산기가 끝나는 연도(9월 결산이면 2026E = 2026/09
    결산기)."""
    return (("a", year - 1, f"{year - 1}"), ("e0", year, f"{year}E"),
            ("e1", year + 1, f"{year + 1}E"))


def revenue_basis_mixed(fi: FiData, t: str, year: int) -> list[str]:
    """매출 y-y 를 비운 연간 칸(slot) — 견줄 두 값이 다 있는데 한쪽만 확정 순액
    (`FiData.revenue_net`)이면 총액(추정치는 늘 총액)과 견주게 되어 y-y 가 가짜로 부푼다
    (4-2b 명세 검토 #1: 증권·카드 6종목 2025 순액 대 2026E 총액 +180~+1,100%).
    비교값은 확정 우선이라 확정 행의 기준으로 가른다. 한쪽 값이 없어 y-y 가 원래 비는 칸은 넣지
    않는다 — 점수 시트 비고는 실제로 비운 칸이 있을 때만 붙는다."""
    mm = fi.fy_month(t)
    out: list[str] = []
    for slot, yr, _ in annual_years(year):
        p, p0 = f"{yr}/{mm}", f"{yr - 1}/{mm}"
        cur = (_either if slot == "a" else _estimate)(fi, t, p, "revenue")
        if cur is None or _either(fi, t, p0, "revenue") is None:
            continue
        if (slot == "a" and fi.revenue_net(t, p)) != fi.revenue_net(t, p0):
            out.append(slot)
    return out


def yoy_label(prev_head: str, head: str) -> str:
    """YoY 머리 — 비교하는 두 해가 보이게(10-11 사용자: 2025 옆 YoY 를 2026 대비로 읽음)."""
    return f"YoY\n({prev_head}-{head})"


def sheet_earnings(wb: Workbook, view: DayView, fi: FiData) -> Dictionary:
    year = int(view.date[:4])
    years = annual_years(year)
    groups = [Group("종목", id_cols())]
    # 블록 안 세로선 — 연도 묶음(값 + 그 YoY) 사이 · 분기 YoY 앞은 A6A6A6, 묶음 안은 D9D9D9
    sides = {}
    for key, label in METRICS:
        cols: list[Col] = []
        prev_head = f"{year - 2}"
        for slot, yr, head in years:
            what = (f"{yr}년에 끝나는 결산기(12월 결산 = {yr}/12, 9월 결산이면 {yr}/09) "
                    "확정치(사업보고서). 확정치가 없으면 같은 결산기 추정치(컨센서스 cur)를 회색 'E' "
                    "서식으로, 둘 다 없으면 빈칸" if slot == "a"
                    else f"{yr}년에 끝나는 결산기 추정치(컨센서스 cur)")
            cols += [
                Col(f"{key}_{slot}", head, "num", "#,##0", definition=f"{label}(억원) {what}"),
                Col(f"{key}_{slot}_y", yoy_label(prev_head, head), "chg", "#,##0.0", color=False,
                    fit_lines=True, definition=(
                        f"{label} 전년 대비(%) — 한 해 앞 같은 결산월 결산기(확정 우선, 없으면 "
                        "추정)와 비교. 부호가 바뀌면 숫자 대신 흑자전환(−→+) · 적자전환(+→−) · "
                        "적자지속(−→−)"
                        + (". 옆 칸이 추정치(회색 'E')면 이 칸도 회색" if slot == "a" else ""))),
            ]
            if slot != "a":
                sides[f"{key}_{slot}"] = SIDE_PAIR
            prev_head = head
        groups.append(Group(f"{label}(억원 · YoY %)", tuple(cols)))
    # 분기 창 = 판 전 종목 중 영업이익이 있는 가장 최근 달력 분기를 끝으로 5칸(오래된 → 최근).
    # 머리글 'YY.nQ' — 'YYYY/MM' 의 03→1Q · 06→2Q · 09→3Q · 12→4Q. 기말이 분기말이 아닌 결산월
    # (예 11월 결산의 2026/08)은 창 끝을 정하지 않고 칸에도 들어가지 않는다(최근 분기 y-y 는 그대로)
    q_last = max((p for row in view.rows for p, q in fi.quarters(ticker_of(row)).items()
                  if q.get("op") is not None and p[5:7] in ("03", "06", "09", "12")),
                 default=None)
    q_periods = ([] if q_last is None
                 else [period_add(q_last, -3 * k) for k in range(N_QUARTERS - 1, -1, -1)])
    qcols = [Col(f"q:{p}", f"{p[2:4]}.{int(p[5:7]) // 3}Q", "num", "#,##0",
                 definition=f"{p} 분기 영업이익(3개월 값), 억원 — 그 분기 행이 없으면 빈칸")
             for p in q_periods]
    qcols.append(Col("q_y", "YoY\n(최근 분기)", "chg", "#,##0.0",
                     color=False, fit_lines=True, definition=(
                         "종목마다 영업이익이 있는 최근 분기의 전년 동기 대비(%). 부호가 바뀌면 "
                         "숫자 대신 흑자전환·적자전환·적자지속")))
    sides["q_y"] = SIDE_PAIR
    groups.append(Group("분기 영업이익(억원 · YoY %)", tuple(qcols)))

    rows: list[Row] = []
    est: list[tuple[int, str]] = []         # Y−1 칸에 추정치를 넣은 (행 순번, 지표)
    for i, row in enumerate(view.rows):
        t = ticker_of(row)
        out: Row = id_values(view, t)
        mm = fi.fy_month(t)                 # 결산월 — 비12월이면 Score 비고에 'N월 결산'
        mixed = revenue_basis_mixed(fi, t, year)     # 순액 대 총액 매출 y-y — 비우고 비고
        for key, _ in METRICS:
            for slot, yr, _head in years:
                p = f"{yr}/{mm}"
                v = _either(fi, t, p, key) if slot == "a" else _estimate(fi, t, p, key)
                if slot == "a" and v is not None and _actual(fi, t, p, key) is None:
                    est.append((i, key))
                y, f = yoy(v, _either(fi, t, f"{yr - 1}/{mm}", key))
                if key == "revenue" and slot in mixed:
                    y, f = None, None
                out[f"{key}_{slot}"], out[f"{key}_{slot}_y"] = v, f or y   # 부호 전환이면 글자
        qs = fi.quarters(t)
        for p in q_periods:
            q = qs.get(p)
            out[f"q:{p}"] = None if q is None else _num(q.get("op"))
        with_op = [p for p, q in qs.items() if q.get("op") is not None]
        if with_op:
            q0 = max(with_op)
            prev = qs.get(period_add(q0, -12))
            y, f = yoy(_num(qs[q0].get("op")), None if prev is None else _num(prev.get("op")))
            out["q_y"] = f or y
        rows.append(out)
    starts = [g.cols[0].key for g in groups[1:]]
    notes_5 = [(None, "YoY 글자: 흑자 파랑 · 적자 빨강"),
               *zip(starts, (
                   f"{year - 1}: 확정치(없으면 추정치를 회색 E 로) · {year}E·{year + 1}E: 컨센서스",
                   f"연도: 결산기가 끝나는 해(9월 결산 {year}E = {year}/09)",
                   "매출 순액 회사: 매출 YoY 비움 · 어닝시즌: 원천 없음",
                   f"분기: 최근 {N_QUARTERS}분기(분기말이 다른 결산월은 칸이 비고 YoY 만)"),
                   strict=True)]
    sh = write_confirmed(wb, "Earnings",
                         band(view, "Earnings: 연간·분기 실적과 YoY", "정렬: Score 순위 순",
                              notes_5),
                         groups, [rows], inner_sides=sides, sign_colors=True)
    ws = sh.ws
    _h6, r0, r1 = sh.blocks[0]
    # YoY 색 — 가운데 0, 양 끝 = 음수 · 양수 |값| 90% 분위(날마다 다시 잰다)
    if rows:
        for key in [f"{k}_{slot}_y" for k, _ in METRICS for slot, _yr, _h in years] + ["q_y"]:
            letter = sh.letter(key)
            ws.conditional_formatting.add(f"{letter}{r0}:{letter}{r1}",
                                          scale_zero_anchor([r.get(key) for r in rows]))
    # Y−1 칸 중 추정치를 넣은 칸만 'E' 서식·회색 글자로 고친다. 바로 옆 YoY 칸도 추정치 기준
    # 증가율이라 회색 글자(N-25 Q10) — 흑자 · 적자 글자는 그 색 그대로
    for i, key in est:
        cell = ws.cell(r0 + i, sh.cols[f"{key}_a"])
        cell.number_format, cell.font = '#,##0"E"', font(color=C_HDR)
        yy = ws.cell(r0 + i, sh.cols[f"{key}_a_y"])
        if yy.value not in SIGN_COLORS:
            yy.font = font(color=C_HDR)
    return sh.dictionary



# ── 시트: Sector ─────────────────────────────────────────────────────────────
WEEK_SESSIONS = 5       # Sector 1W 수익률 = 5세션 전 대비


def _median(xs: Sequence[float]) -> float | None:
    return statistics.median(xs) if xs else None


def jump_rule(view: DayView) -> bool:
    """주 모델이 미해결 수정주가 점프 결측 규칙(scope `adj_jump_missing`, T-9)을 켰나."""
    return view.spec is not None and view.spec.params.get(ADJ_JUMP_PARAM) is True


def week_returns(fi_root: Path, view: DayView) -> dict[str, float]:
    """종목별 1W(5세션 전 대비) 수익률 — 엔진(v3_zscore) 가격 이력 규칙을 그대로 부른다:
    fi_prices [D−550일, D] 행, 값 = fi_adj_prices.adj_close(없으면 close), 날짜 오름차순.
    주 모델이 점프 결측 규칙(`jump_rule`)을 켰으면 엔진 r1m 처럼 창(최근 6행) 안에서
    `adj_jump_ok` 계단이 바뀌는 종목은 뺀다 — 미해결 분할 · 병합이 낀 수익률이 업종 1W 를 끌지
    않게(코드 리뷰 I-1)."""
    d = date.fromisoformat(view.date).isoformat()          # SQL 에 넣기 전 형식 검증
    start = (date.fromisoformat(d) - timedelta(days=HISTORY_DAYS)).isoformat()
    codes = [ticker_of(r) for r in view.rows]
    safe = [t for t in codes if t.isalnum()]               # 코드는 영숫자만 SQL 에 넣는다
    if not safe:
        return {}
    where = (f"date BETWEEN DATE '{start}' AND DATE '{d}' AND ticker IN ("
             + ", ".join(f"'{t}'" for t in safe) + ")")
    bid = view.run.fi_build_id
    prices = read_fi(fi_root, "fi_prices", bid, columns=("ticker", "date", "close"), where=where)
    try:
        adj = read_fi(fi_root, "fi_adj_prices", bid, where=where)
    except DeliverError as e:
        # 수정종가 표가 없으면 종가만 — 분할 · 병합이 낀 종목 수익률이 틀릴 수 있어 남긴다
        log.warning("Sector 1W — fi_adj_prices 를 읽지 못해 종가로 계산한다(데이터 %s): %s", bid, e)
        adj = []
    adj = [{"ticker": r["ticker"], "date": r["date"], "adj_close": r.get("adj_close"),
            "adj_jump_ok": r.get("adj_jump_ok")} for r in adj]
    inputs = FactorInputs(d, view.run.basis, bid, {"fi_prices": prices, "fi_adj_prices": adj})
    histories, steps = _price_histories(inputs, safe, d)
    masked = sorted(t for t in histories
                    if jump_rule(view) and crosses_step(steps[t][-(WEEK_SESSIONS + 1):]))
    if masked:
        log.info("Sector 1W — 창 안 미해결 수정주가 점프로 뺀 종목 %d: %s", len(masked),
                 ", ".join(masked))
    return {t: r for t, h in histories.items()
            if t not in masked and (r := _period_return(h, WEEK_SESSIONS)) is not None}


def sheet_sectors(wb: Workbook, view: DayView, week: Mapping[str, float]) -> Dictionary:
    """위 표 = 대분류, 빈 줄, 아래 표 = 업종(중분류). 축 평균 색은 대분류 ±0.5 · 업종 ±1, 5종목 미만
    업종은 회색, 수익률은 표마다 백분위. `week` = 종목별 1W 수익률(`week_returns`)."""
    n_ranked = len(view.ranked)
    top_n, k = view.output.top_n, view.output.max_per_sector
    cands = set(view.candidates)
    # 업종 지표 재료 — v4 는 지표 긴 표(EP·REV_OP_1M·R1M·R3M), scope·v3 는 점수 표 원값
    # (val_per·op_change_1m·r1m·r3m — 지표 긴 표가 0행이라 옛 코드는 세 열이 빈칸, E-10)
    v3 = bool(score_raw_columns(view))
    # 1W 정의 — '엔진 r1m 과 같은 규칙'은 v3 계열만, 점프 결측 규칙을 켠 주 모델(scope)은 그 말까지
    week_rule = "fi_prices 행, 수정종가 우선"
    if v3:
        week_rule = "엔진 r1m 과 같은 규칙: " + week_rule + (
            ", 창 안에 미해결 수정주가 점프가 낀 종목은 뺀다" if jump_rule(view) else "")
    week_col = Col("r1w", "1W 수익률\n(%)", "num", "#,##0.0",
                   definition=f"1W 수익률(5세션 전 대비 — {week_rule}) 시총가중 평균")
    if v3:
        has = set(view.rows[0]) if view.rows else set()
        lvl_key, rev_key, ret_key, r3_key = "val_per", "op_change_1m", "r1m", "r3m"
        metric_cols = (
            Col("per_med", "PER 중앙값\n(배)", "num", "#,##0.00",
                definition="최근 연간 확정 PER(엔진 val_per, 양수만) 중앙값"),
            Col("rev_up", "리비전 상향\n비율(%)", "num", "#,##0.0", definition=(
                "영업이익 추정 1M 변화(엔진 op_change_1m) > 0 종목 비율(값 있는 종목 중 — 현재값·"
                "비교값이 없거나 비교값이 0이면 엔진 값이 0 이라 상향이 아니다, v3 규약)")),
            week_col,
            Col("r1m", "1M 수익률\n(%)", "num", "#,##0.0",
                definition="1M 수익률(엔진 r1m × 100) 시총가중 평균"),
            Col("r3m", "3M 수익률\n(%)", "num", "#,##0.0",
                definition=("3M 수익률(엔진 r3m — 60세션 전 대비, 수정종가 우선 × 100) "
                            "시총가중 평균")))
    else:
        has = {k for per in view.ind.values() for k in per}
        lvl_key, rev_key, ret_key, r3_key = "EP", "REV_OP_1M", "R1M", "R3M"
        metric_cols = (
            Col("ep_med", "E/P 중앙값\n(%)", "num", "#,##0.00", definition="후행 E/P(EP) 중앙값"),
            Col("rev_up", "리비전 상향\n비율(%)", "num", "#,##0.0",
                definition="영업이익 추정 1M 변화(REV_OP_1M) > 0 종목 비율(값 있는 종목 중)"),
            week_col,
            Col("r1m", "1M 수익률\n(%)", "num", "#,##0.0",
                definition="1M 수익률(R1M) 시총가중 평균"),
            Col("r3m", "3M 수익률\n(%)", "num", "#,##0.0",
                definition="3M 수익률(R3M) 시총가중 평균"))
    axis_cols = tuple(
        Col(f"{b}_u", bucket_label(b), "num", "#,##0.00", color=False,
            definition=f"{bucket_label(b)} 유니버스 z(엔진 버킷 점수)의 업종 평균")
        if view.z_engine else
        Col(f"{b}_u", bucket_label(b), "num", "#,##0.0", color=False,
            definition=f"{bucket_label(b)} 유니버스 백분위의 업종 평균")
        for b in view.buckets)
    groups = [
        Group("업종", (
            Col("level", "구분", "txt", None, 6.0, definition="대분류 · 중분류(WICS)"),
            Col("code", "코드", "id", None, fixed=6.2, definition="WICS 코드"),
            Col("sname", "업종명", "id", None, 16.0, item=True, definition="WICS 업종명"),
            Col("parent", "대분류", "id", None, 12.0, definition="중분류의 상위 대분류"))),
        Group("규모", (
            Col("n", "종목 수", "num", "#,##0", definition="주 모델 모집단 종목 수(제외 포함)"),
            Col("n_ranked", "순위\n종목 수", "num", "#,##0", definition="순위가 있는 종목 수"),
            Col("mcap", "시총 합\n(억원)", "num", "#,##0", fit_label="시총 합(억)",
                definition="모집단 시가총액 합, 억원"),
            Col("w", "시총 비중\n(%)", "num", "#,##0.0", fit_label="시총 비중(%)",
                definition="모집단 시총 합 대비 비중"))),
        Group("쏠림", (
            Col("t30", f"상위 {top_n}\n비중(%)", "num", "#,##0.0", fixed=6.2,
                definition=f"순위 ≤ {top_n} 종목 중 이 업종 비중(대분류당 {k}종목 규칙 적용 전)"),
            Col("t100", "상위 100\n비중(%)", "num", "#,##0.0", fixed=6.2,
                definition="순위 ≤ 100 종목 중 비중"),
            # 머리 글 = 아래(업종) 표 · Meta 사전. 위(대분류) 표 머리는 '(최대 k)' 를 붙이고 n/k 로
            Col("n_cand", "후보 수", "num", "#,##0", fixed=6.2, definition=(
                f"대분류당 최대 {k}종목 규칙을 적용한 후보 {top_n} 중 수 · 대분류 표는 n/{k} 로 "
                f"표시({k}/{k} = {k}종목이 찬 대분류)")))),
        Group("종합", (Col("comp_med", "점수\n중앙값", "num", "#,##0.00",
                           fit_label="종합 점수\n중앙값", core=True,
                           definition="순위 종목 종합 점수 중앙값"),)),
        Group("축별 평균(Z)" if view.z_engine else "축별 평균 백분위", axis_cols),
        Group("업종 지표", metric_cols),
        Group("상위 종목", (Col("top", "상위 3(순위)", "txt", None, fixed=58.0,
                              definition="업종 안 순위 상위 3 종목 '이름(순위)'"),)),
    ]
    total_cap = sum(_num(view.uni.get(ticker_of(r), {}).get("market_cap")) or 0.0
                    for r in view.rows)

    def aggregate(level: str, code: str, members: list[str]) -> Row:
        first = view.uni.get(members[0], {})
        name_key = "sector_l1_name" if level == "대분류" else "sector_l2_name"
        ranked = sorted((t for t in members if view.rank(t) is not None),
                        key=lambda t: view.rank(t) or 0)
        caps = {t: _num(view.uni.get(t, {}).get("market_cap")) for t in members}
        cap_sum = sum(v for v in caps.values() if v is not None)
        out: Row = {"level": level, "code": code, "sname": first.get(name_key) or code,
                    "parent": first.get("sector_l1_name") if level == "중분류" else "",
                    "n": len(members), "n_ranked": len(ranked), "mcap": cap_sum,
                    "w": cap_sum / total_cap * 100 if total_cap else None,
                    "t30": (sum(1 for t in ranked if (view.rank(t) or 0) <= top_n)
                            / min(top_n, n_ranked) * 100 if n_ranked else None),
                    "t100": (sum(1 for t in ranked if (view.rank(t) or 0) <= 100)
                             / min(100, n_ranked) * 100 if n_ranked else None),
                    "n_cand": sum(1 for t in members if t in cands),
                    "comp_med": _median([c for t in ranked
                                         if (c := composite_of(view.by_ticker[t]))
                                         is not None]),
                    "top": ", ".join(f"{view.name(t) or t}({view.rank(t)})" for t in ranked[:3])}
        for b in view.buckets:
            if view.z_engine:
                vals = [z for t in members if (z := view.bucket_score(t, b)) is not None]
            else:
                vals = [view.upct[b][t] for t in members if t in view.upct[b]]
            out[f"{b}_u"] = statistics.fmean(vals) if vals else None

        def raws(key: str) -> dict[str, float]:
            if v3:
                return {t: v for t in members
                        if (v := _num(view.by_ticker[t].get(key))) is not None}
            return {t: v for t in members
                    if (v := _num(view.ind.get(t, {}).get(key, {}).get("raw"))) is not None}

        def cap_weighted(vals: Mapping[str, float]) -> float | None:
            """시총가중 평균 × 100(%) — 시총이 없거나 0 인 종목은 뺀다."""
            got = {t: v for t, v in vals.items() if caps.get(t)}
            w = sum(caps[t] or 0.0 for t in got)
            return sum(v * (caps[t] or 0.0) for t, v in got.items()) / w * 100 if w else None
        if lvl_key in has:
            lv = raws(lvl_key)
            med = statistics.median(lv.values()) if lv else None
            if v3:
                out["per_med"] = med                                  # 배 그대로
            else:
                out["ep_med"] = None if med is None else med * 100    # 비율 → %
        if rev_key in has:
            rv = raws(rev_key)
            out["rev_up"] = sum(1 for v in rv.values() if v > 0) / len(rv) * 100 if rv else None
        out["r1w"] = cap_weighted({t: week[t] for t in members if t in week})
        if ret_key in has:
            out["r1m"] = cap_weighted(raws(ret_key))
        if r3_key in has:
            out["r3m"] = cap_weighted(raws(r3_key))
        return out

    blocks: list[list[Row]] = []
    for level, col in (("대분류", "sector_l1"), ("중분류", "sector_l2")):
        by: dict[str, list[str]] = {}
        for r in view.rows:
            code = r.get(col)
            if code is not None:
                by.setdefault(str(code), []).append(ticker_of(r))
        blocks.append([aggregate(level, code, by[code]) for code in sorted(by)])
    first_axis = axis_cols[0].key if axis_cols else None
    notes_5: list[tuple[str | None, str]] = [(None, "위: 대분류 표 · 아래: 업종(중분류) 표"),
                                             ("n", f"회색: {SMALL_SECTOR}종목 미만 업종")]
    if first_axis:
        notes_5.append((first_axis, "축 평균: 유니버스(Z) 평균" if view.z_engine
                        else "축 평균: 유니버스 백분위 평균"))
    notes_5.append(("per_med" if v3 else "ep_med",
                    f"수익률: 시총가중, {WEEK_SESSIONS}·20·60거래일 전 대비"))
    sh = write_confirmed(wb, "Sector", band(view, "Sector: 업종별 점수·수익률 집계",
                                            "정렬: 업종 코드 순", notes_5),
                         groups, blocks, style="sector", page=Page(fit_height=1))
    ws = sh.ws
    (h6a, r0a, r1a), (h6b, r0b, r1b) = sh.blocks
    for r, text in ((h6a, "대분류(WICS 10)"), (h6b, "업종(WICS 중분류)")):
        cell = ws.cell(r, 1, text)
        cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=False)
    m = sh.cols["n_cand"]
    put(ws, h6a + 1, m, f"후보 수\n(최대 {k})")
    for r in range(r0a, r1a + 1):
        ws.cell(r, m).number_format = f'0"/{k}"'
    # 색 — 아래 표 5종목 미만 업종 축 평균은 회색(먼저 · 멈춤), 축 평균 대분류 ±0.5 · 업종 ±1
    # (평균은 종목 z 보다 폭이 좁다), 수익률은 표마다 백분위
    cf = ws.conditional_formatting
    a0 = sh.letter(axis_cols[0].key) if axis_cols else None
    a1 = sh.letter(axis_cols[-1].key) if axis_cols else None
    n_col = sh.letter("n")
    if a0 and r1b >= r0b:
        cf.add(f"{a0}{r0b}:{a1}{r1b}", Rule(
            type="expression", stopIfTrue=True,
            formula=[f"AND(ISNUMBER(${n_col}{r0b}),${n_col}{r0b}<{SMALL_SECTOR})"],
            dxf=DifferentialStyle(font=Font(color="FF8C8C8C"),
                                  fill=PatternFill(bgColor="FFF2F2F2", fill_type="solid"))))
    for (r0, r1), lim in (((r0a, r1a), 0.5), ((r0b, r1b), 1.0)):
        if r1 < r0:
            continue
        for c in axis_cols:
            letter = sh.letter(c.key)
            cf.add(f"{letter}{r0}:{letter}{r1}",
                   scale_fixed_zero(lim) if view.z_engine else scale_high_good())
        for key in ("r1w", "r1m", "r3m"):
            letter = sh.letter(key)
            cf.add(f"{letter}{r0}:{letter}{r1}", scale_high_good())
    return sh.dictionary


# ── 시트: Compare ────────────────────────────────────────────────────────────
def sheet_models(wb: Workbook, view: DayView) -> Dictionary:
    specs = [view.spec_id, *compared_specs(view)]
    ranks = {view.spec_id: {ticker_of(r): rank_of(r) for r in view.rows}, **view.other_ranks}
    top_n = view.output.top_n
    groups = [
        Group("종목", id_cols()),
        Group("순위", tuple(Col(f"r:{s}", model_label(s), "rank", "#,##0",
                              key_col=s == view.spec_id, core=s == view.spec_id,
                              definition=f"{s} 순위(1 = 최고)")
                          for s in specs)),
        Group(f"상위 {top_n} 포함(●)", tuple(Col(f"t:{s}", model_label(s), "txt", None, 8.0,
                                                definition=f"{s} 순위 ≤ {top_n} 이면 ●")
                                            for s in specs)),
        Group("차이", (Col("max_diff", "최대 차이", "num", "#,##0",
                         definition="모델 순위 최댓값 − 최솟값(순위 둘 이상)"),)),
    ]
    rows: list[Row] = []
    for row in view.rows:
        t = ticker_of(row)
        out: Row = id_values(view, t)
        got = []
        for s in specs:
            r = ranks[s].get(t)
            out[f"r:{s}"] = r
            out[f"t:{s}"] = "●" if r is not None and r <= top_n else ""
            if r is not None:
                got.append(r)
        out["max_diff"] = max(got) - min(got) if len(got) >= 2 else None
        rows.append(out)
    notes_5: list[tuple[str | None, str]] = [
        (None, "빈칸: 그 모델 유니버스 밖"), (f"r:{view.spec_id}", "같은 날 데이터 순위"),
        (f"t:{view.spec_id}", f"●: 그 모델 상위 {top_n}")]
    left_out = sorted({model_id_of(s).split("_")[0] for s in left_out_specs(view)})
    if left_out:
        notes_5.append(("max_diff", (f"{'·'.join(left_out)}: 결함 수정 전까지 뺌"
                                     "(Meta '비교에서 뺀 모델')")))
    sh = write_confirmed(wb, "Compare", band(view, "Compare: 모델별 순위 비교",
                                             f"정렬: {model_id_of(view.spec_id)} 순위 오름차순 ▲",
                                             notes_5),
                         groups, [rows], sort_key=f"r:{view.spec_id}", page=Page(portrait=True))
    return sh.dictionary



# ── 시트: Meta ───────────────────────────────────────────────────────────────
def gates_text(g: object) -> str:
    """model 데이터 manifest 의 게이트 요약(dict · list 모두)."""
    if isinstance(g, Mapping):
        items = [(str(k), v) for k, v in g.items()]
    elif isinstance(g, list):
        items = [(str(x.get("name", "?")), x) for x in g if isinstance(x, Mapping)]
    else:
        return "" if g is None else str(g)
    return " · ".join(f"{k} {v.get('status', '?') if isinstance(v, Mapping) else v}"
                      for k, v in items)


def _builds_text(v: object) -> str:
    if isinstance(v, Mapping):
        ids = sorted({str(x.get("build_id", x)) if isinstance(x, Mapping) else str(x)
                      for x in v.values()})
        return ", ".join(ids)
    return "" if v is None else str(v)


def universe_text(view: DayView) -> str:
    if view.spec is None:
        return "레지스트리에 없는 spec — 규칙 미상"
    u = view.spec.universe
    g = u.coverage_grace_days
    parts = ["추정치 보유" if u.require_estimates else "추정치 무관",
             "/".join(u.sec_types), "/".join(u.markets),
             f"추정치 유예 {g}거래일" if g else "추정치 유예 없음"]
    if u.min_analysts is not None:
        parts.append(f"추정기관수 ≥ {u.min_analysts}")
    if u.min_market_cap is not None:
        parts.append(f"시총 ≥ {u.min_market_cap:,.0f}억")
    if u.min_adv20 is not None:
        parts.append(f"20세션 거래대금 ≥ {u.min_adv20:,.0f}억")
    if u.exclude:
        parts.append("제외 " + "·".join(u.exclude))
    return " · ".join(parts)


def meta_pairs(view: DayView, fi_meta: Mapping[str, object], fi: FiData | None,
               cands: Candidates | None = None) -> list[tuple[str, object]]:
    """Meta 위 표 — 줄 이름 · 값의 '판' 은 '데이터' · '날짜' 로(10-11 사용자: 읽는 사람이
    판을 모름)."""
    run = view.run
    counts: dict[str, int] = {}
    for r in view.rows:
        if r.get("exclude_reason"):
            k = exclude_label(r["exclude_reason"])
            counts[k] = counts.get(k, 0) + 1
    scored = [t for t in view.by_ticker if view.scored(t)]
    miss = {b: sum(1 for t in scored if view.bucket_score(t, b) is None) for b in view.buckets}
    pairs: list[tuple[str, object]] = [("기준일", view.date), ("basis", run.basis)]
    if run.basis in BASIS_NOTES:     # 장 마감(evening) · 주말 보강(supplement)만 — 아침은 줄 없음(PR-6)
        pairs.append(("데이터 성격", BASIS_NOTES[run.basis]))
    pairs += [
        ("주 모델", f"{view.spec_id}({model_label(view.spec_id)})"),
        ("비교 모델", ", ".join(f"{s}({model_label(s)})" for s in compared_specs(view))),
    ]
    left_out = left_out_specs(view)
    if left_out:
        pairs.append(("비교에서 뺀 모델",
                      ", ".join(f"{s}({model_label(s)})" for s in left_out)
                      + f" — {hidden_compare_reason()}. 데이터 계산·저장은 그대로"
                      "(아래 데이터 검사 줄)"))
    pairs += [
        ("model 데이터 id", run.build_id), ("model 생성 시각", run.generated_at),
        ("factor_inputs 데이터 id", run.fi_build_id),
        ("equity 데이터 id", _builds_text(fi_meta.get("equity_builds")) or "(fi manifest 없음)"),
        ("유니버스 규칙", universe_text(view)),
        ("종목 수", f"모집단 {len(view.rows)} · 순위 {len(view.ranked)} · "
                   f"제외 {len(view.rows) - len(view.ranked)}"),
        ("제외 사유별", " · ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "없음"),
        ("축별 결측 수", " · ".join(f"{bucket_label(b)} {n}" for b, n in miss.items())),
    ]
    raw_cols = score_raw_columns(view)
    if raw_cols:
        pairs.append(("Raw 단위", raw_units_text(raw_cols)))
    if fi is not None:
        fin = [a for rows in fi.fins.values() for r in rows if (a := iso(r.get("available_date")))]
        obs = [o for rows in fi.cons.values() for r in rows
               if r.get("horizon") == "cur" and (o := iso(r.get("obs_date")))]
        flows = [x for x in fi.flow_last.values() if x]
        pairs.append(("데이터 기준일", f"가격 {view.date} · "
                                  f"재무 최신 접수 {max(fin, default='-')} · "
                                  f"추정 관측 최신 {max(obs, default='-')} · "
                                  f"수급 최신 {max(flows, default='-')}"))
    if view.spec is not None:
        pairs += [
            ("모델 버전", f"{view.spec.model_id} {view.spec.version} · 엔진 {view.spec.engine}"),
            ("가중치", " · ".join(f"{bucket_label(b)} {w * 100:.1f}%"
                               for b, w in view.spec.buckets.items())),
            ("제외 게이트", " · ".join(f"{g.key} {g.rule} {g.value}" for g in view.spec.gates)
             or "없음"),
            ("후보 규칙", candidate_rule_text(cands) if cands is not None else
             f"상위 {view.output.top_n} · {view.output.sector_level} 업종당 최대 "
             f"{view.output.max_per_sector}"),
        ]
    for sid in sorted(run.specs):
        s = run.specs[sid]
        pairs.append((f"데이터 검사 {sid}",
                      f"점수 {s.get('n_scores')} · 순위 {s.get('n_ranked')} · "
                      f"제외 {s.get('n_excluded')} · {gates_text(s.get('gates'))}"))
    excluded = run.meta.get("excluded_specs")
    if isinstance(excluded, Mapping):
        for sid, s in sorted(excluded.items()):
            if isinstance(s, Mapping) and "error" in s:      # 실행 예외로 뺀 비교 모델(D-01)
                pairs.append((f"제외된 비교 모델 {sid}",
                              f"실행 오류로 이번 데이터에서 뺐다 · {s['error']}"))
                continue
            gates_of = s.get("gates") if isinstance(s, Mapping) else None
            pairs.append((f"제외된 비교 모델 {sid}",
                          f"게이트 실패로 이번 데이터에서 뺐다 · {gates_text(gates_of)}"))
    pairs += [(f"각주 {i}", text) for i, text in enumerate(FOOTNOTES, start=1)]
    return pairs


def insert_after(pairs: list[tuple[str, object]], key: str,
                 new: Sequence[tuple[str, object]]) -> None:
    """메타 줄을 키 이름 뒤에 끼운다 — 고정 위치 숫자는 줄 수가 바뀌면 다른 줄 사이로 옮겨 갔다
    (E-09: 1W 를 뺀 뒤 '순위 흐름 판'이 factor_inputs·equity 판 id 사이에 끼었다). 그 키가 없으면
    끝에 덧붙이고 경고만 남긴다 — 메타 줄 순서 때문에 그날 엑셀·발송을 막지 않는다(검토 사소 6)."""
    keys = [k for k, _ in pairs]
    if key not in keys:
        log.warning("메타 줄 %r 가 없어 %s 를 끝에 덧붙인다 — 있는 줄: %s",
                    key, [k for k, _ in new], keys)
        pairs.extend(new)
        return
    at = keys.index(key) + 1
    pairs[at:at] = list(new)


# ── 조립 ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DailyResult:
    path: Path
    date: str
    basis: str
    spec_id: str
    top: list[tuple[int, str, str]]     # (순위, 코드, 이름) 상위 5
    n_rows: int
    n_ranked: int
    sheets: list[str]
    build_id: str                       # model 데이터 id — 발송 장부·정정 캡션용(N-25 Q9)
    generated_at: str                   # model 생성 시각(JSON generated_at)


def daily_path(out_root: Path, d: str, basis: str) -> Path:
    return Path(out_root) / "daily" / f"model_scores_{ymd(d)}_{basis}.xlsx"


def save_atomic(wb: Workbook, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    wb.save(tmp)
    os.replace(tmp, path)


def extra_universe(view: DayView) -> dict[str, Stock]:
    """새 시트의 종목 유니버스 = Score 시트 행(같은 순서) — 이름 · 대분류 · 종합 순위 · 시총 ·
    Raw 모멘텀 1M(%)."""
    out: dict[str, Stock] = {}
    for row in view.rows:
        t = ticker_of(row)
        v = id_values(view, t)
        r1m = _num(row.get("r1m"))
        out[t] = Stock(t, str(v["name"]), str(v["l1"]), rank_of(row), _num(v["mcap"]),
                       None if r1m is None else r1m * 100)
    return out


def finish_extra(path: Path, wb: Workbook, sparks: Mapping[str, Sequence[tuple[str, str]]]) -> None:
    """새 시트 저장 뒤 손질 — 그래프 값 사본 · 날짜 축 · 제목 끔(`finish_charts`)과 셀 안 막대.
    실패해도 엑셀은 낸다: 손질 전 파일도 그대로 열리는 엑셀이고(그래프는 엑셀이 셀에서 다시 그린다),
    손질 함수는 임시 파일을 스스로 지운다. 경고 로그만 남긴다."""
    try:
        finish_charts(path, wb)
        for sheet, cells in sparks.items():
            add_column_sparklines(path, sheet, cells)
    except Exception:  # noqa: BLE001  # reason: 저장 뒤 손질 실패가 그날 엑셀을 막지 않는다(I-3)
        log.warning("새 시트 저장 뒤 손질(그래프 값 사본 · 셀 안 막대)을 못 했다 — 손질 전 엑셀을 "
                    "그대로 낸다: %s", path.name, exc_info=True)


def build_daily(d: str | date, basis: str, *, model_root: Path, fi_root: Path, out_root: Path,
                config_dir: Path | None = None,
                trend_fallback_root: Path | None = None, equity_root: Path | None = None,
                stage_root: Path | None = None) -> DailyResult:
    """`trend_fallback_root` = 장 마감 판(evening) 1M 비교 판이 evening 이력에 없을 때 대신 읽을
    아침 확정판 model 루트(T-50, `load_trend`). None 이면 대체하지 않는다. 아침 판은 받아도 무시.
    `equity_root` · `stage_root` = 새 시트 원천 루트(없으면 fi 판 JSON 의 루트)."""
    run = load_run(model_root, d, basis)
    view = load_day(model_root, fi_root, run, config_dir=config_dir)
    fi = load_fi(fi_root, view)
    # 전일 비교 판 — 같은 basis 직전 판. 주말 보강판은 직전 거래일 아침 확정판(N-45, `HISTORY_BASIS`)
    prev = previous_run(model_root, run.date, history_basis(basis))
    prev_rank: dict[str, int | None] = {}
    # 전일 순위 spec — 직전 판에 주 모델이 없으면 앞 주 모델(N-47 · Q-E1), 둘 다 없으면
    # 전일 데이터 없음
    prev_spec = None if prev is None else history_spec(prev, view.spec_id)
    if prev is not None and prev_spec is None:
        log.warning("직전 데이터 %s %s 에 주 모델 %s 도 앞 주 모델도 없다", prev.date,
                    prev.build_id, view.spec_id)
    elif prev is not None and prev_spec is not None:
        try:
            prev_rank = {ticker_of(r): rank_of(r)
                         for r in read_scores(model_root, prev, prev_spec)}
        except DeliverError as e:
            log.warning("전일 순위를 읽지 못했다(%s %s): %s", prev.date, prev.build_id, e)
            prev_rank = {}

    trend = load_trend(model_root, run, view.spec_id, basis, trend_fallback_root)
    cands = candidates(view, prev_rank)

    # 시트 순서 = 확정 양식(Score · Sector · Earnings · 새 시트 여섯 · Compare · Raw · Meta · trend).
    # Meta 열 사전은 옛 순서(Score · Raw · Earnings · Sector · Compare) 뒤에 Score 후보 두 줄, 그 뒤
    # 새 시트(ETF · Analyst · Events · Market · Flow · Short)
    wb = new_workbook()
    wb.remove(wb.worksheets[0])
    score_dict, score_tail = sheet_scores(wb, view, fi, prev_rank, trend, cands,
                                          grace_of(view, fi_root, trend))
    sector_dict = sheet_sectors(wb, view, week_returns(fi_root, view))
    earnings_dict = sheet_earnings(wb, view, fi)
    fi_meta = fi_run_meta(fi_root, run.date, basis, run.fi_build_id)
    src = open_sources(fi_meta, equity_root, stage_root)
    extra = sheets_extra.build(wb, src, XRefs.of(wb["Sector"]), date.fromisoformat(run.date),
                               extra_universe(view), index=len(wb.worksheets),
                               tmp_root=Path(out_root) / "tmp")
    compare_dict = sheet_models(wb, view)
    raw_dict = sheet_raw(wb, view, fi)
    dictionary: Dictionary = [*score_dict, *raw_dict, *earnings_dict, *sector_dict, *compare_dict]
    pairs = meta_pairs(view, fi_meta, fi, cands)
    # 전일 비교 데이터 — 직전 데이터가 있어도 순위를 쓸 수 없으면 그 말을 붙인다(변화 열을 비운 날)
    prev_text = NO_PREV if prev is None else f"{prev.date} {prev.build_id}"
    if prev is not None and cands.no_prev:
        prev_text += f" — 순위를 쓸 수 없어 {NO_PREV}"
    elif prev_spec is not None and prev_spec != view.spec_id:
        prev_text += f" · {model_label(prev_spec)} 순위"          # 앞 주 모델로 이은 날(N-47)
    base_text: dict[str, str] = {}
    for w in WINDOWS:
        b = trend.base[w]
        text = (f"{b} {FALLBACK_NOTE}" if w in trend.fallback
                else b or "없음(그 전 데이터가 없다)")
        if b is not None and b in trend.older:
            text += f" · {model_label(trend.older[b])} 순위"
        base_text[w] = text
    flow_text = (f"{trend.dates[0]} ~ {trend.dates[-1]} · {len(trend.dates)}개"
                 if trend.dates else "없음")
    if (mark := older_mark(trend)) is not None:
        flow_text += f" · {mark[1]}까지 {mark[0]}"
    insert_after(pairs, "model 생성 시각", [
        ("전일 비교 데이터", prev_text),
        *((f"{w} 비교 데이터", base_text[w]) for w in WINDOWS),
        ("순위 흐름 날짜", flow_text)])
    # 같은 model 데이터로 코드만 바뀐 정정판을 파일로 구별한다(E-08)
    insert_after(pairs, "equity 데이터 id", [
        ("엑셀 생성 시각", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("코드 rev", deployed_rev())])
    insert_after(pairs, "코드 rev", [("새 시트 데이터 id", src.used_text())])
    write_meta_confirmed(wb, band(view, "Meta: 데이터 정보와 열 사전", None,
                                  [(None, "위: 데이터 정보 · 아래({hdr}행~): 시트별 열 사전")],
                                  meta=True),
                         pairs, dictionary, score_tail, extra.defs)
    tickers = [ticker_of(r) for r in view.rows]
    write_trend_sheet(wb, tickers, trend)
    path = daily_path(out_root, run.date, basis)
    save_atomic(wb, path)
    finish_extra(path, wb, extra.sparks)
    score_ws = wb["Score"]
    heads = {str(score_ws.cell(7, c).value): score_ws.cell(7, c).column_letter
             for c in range(1, score_ws.max_column + 1)}
    cols = {w: heads[f"{w}\n흐름"] for w in WINDOWS if f"{w}\n흐름" in heads}
    deltas = {w: {ticker_of(r): trend.delta(w, ticker_of(r), rank_of(r)) for r in view.rows}
              for w in WINDOWS}
    add_sparklines(path, "Score", spark_groups(tickers, trend, date.fromisoformat(run.date),
                                               cols, deltas))
    top = [(rank_of(r) or 0, ticker_of(r), view.name(ticker_of(r))) for r in view.ranked[:5]]
    return DailyResult(path, run.date, basis, view.spec_id, top, len(view.rows),
                       len(view.ranked),
                       [ws.title for ws in wb.worksheets if ws.sheet_state == "visible"],
                       build_id=run.build_id, generated_at=run.generated_at)


__all__ = ["DailyResult", "FiData", "build_daily", "daily_path", "id_cols", "id_values",
           "ind_cells", "load_fi", "model_label", "period_add", "save_atomic", "title_for"]
