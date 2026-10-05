"""매일 엑셀 `data/deliver/daily/model_scores_<YYYYMMDD>_<basis>.xlsx` (MODEL_EXCEL_SPEC A).

시트: 점수 → 점수 원자료 → 지표(표시용) → 실적 → 업종 → 모델 비교 → 메타. 값만(수식 없음).
행 = 주 모델(run.primary_spec) 모집단 — 순위 종목(순위순) 뒤에 제외 종목(코드순).
지표 열은 레지스트리 `role` 이 가른다: score → 점수 원자료, display → 지표(표시용).
"""
from __future__ import annotations

import os
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from .common import (
    PREV_NEGATIVE_FLAGS,
    REVISION_KEYS,
    analyst_note,
    bucket_label,
    change_pct,
    coverage_label,
    exclude_label,
    ind_meta,
    market_label,
    missing,
    split_flags,
    winsorize,
    yoy,
)
from .qpack import Col, Group, Title, write_meta, write_table
from .reader import (
    DeliverError,
    fi_run_meta,
    iso,
    load_run,
    previous_run,
    read_fi,
    read_scores,
    ymd,
)
from .view import DayView, composite_of, load_day, rank_of, ticker_of

Row = dict[str, object]
Dictionary = list[tuple[str, str, str, str]]

MODEL_LABELS = {"scope@1.0": "scope_v1.0", "v4_rank@0.1": "v4 기본", "v4_rank@0.2": "v4 동일가중",
                "v3_zscore": "v3 원본", "v2_percentrank": "v2 원본"}
FOOTNOTES = (
    "① 유니버스 컷: 주 모델 모집단 = 추정치 보유 보통주(신선·유예) — D-13 적격성(관리·정지·"
    "비적정 감사·지연 공시·20세션 거래대금) 탈락은 점수 없이 제외 사유만.",
    "② 창: 가격 지표는 수정종가 세션 수, 재무는 D 이전 공시(PIT), 추정치는 당해 결산기 컨센서스.",
    "③ 색: 3색 백분위 10/50/90 — 초록(낮음)·노랑·빨강(높음). 순위 열은 반전(1위 = 빨강). "
    "레벨 값은 무색. 형광 노랑 = 신규 진입.",
    "④ 정렬 키: 점수·원자료·지표·실적·모델 비교 = 주 모델 순위 오름차순, 제외 종목은 뒤에 코드순.",
)


def model_label(spec_id: str) -> str:
    if spec_id in MODEL_LABELS:
        return MODEL_LABELS[spec_id]
    return MODEL_LABELS.get(spec_id.split("@")[0], spec_id)


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

    def cons_at(self, t: str, period: str, horizon: str = "cur") -> Row | None:
        return next((r for r in self.cons.get(t, ()) if str(r["target_period"]) == period
                     and r.get("horizon") == horizon), None)

    def fy0(self, t: str, d: str) -> str | None:
        """당해 결산기 = D 의 'YYYY/MM' 이상 중 최소(엔진 `_current_period` 와 같은 규칙)."""
        asof = f"{d[:4]}/{d[5:7]}"
        periods = [str(r["target_period"]) for r in self.cons.get(t, ())
                   if r.get("target_period") is not None and str(r["target_period"]) >= asof]
        return min(periods, default=None)


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
    return (*base,
            Col("market", "시장", "id", None, 5.0, definition="KS = KOSPI · KQ = KOSDAQ"),
            l1,
            Col("l2", "중분류", "id", None, 14.0, definition="WICS 중분류"),
            Col("mcap", "시총(억)", "num", "#,##0", 9.0,
                definition="D 시가총액, 억원(fi_universe)"),
            Col("adv20", "거래대금\n20일(억)", "num", "#,##0", 9.0,
                definition="최근 20세션 평균 거래대금, 억원(D-13 적격성 재료)"),
            Col("price", "주가", "num", "#,##0", 9.0, definition="D 종가, 원(fi_prices)"))


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
    others = [s for s in sorted(view.run.specs) if s != view.spec_id]
    model_line = f"주 모델 {view.spec_id}({model_label(view.spec_id)})"
    if others:
        model_line += " · 비교 " + ", ".join(f"{s}({model_label(s)})" for s in others)
    source = (f"{view.date} 기준({view.run.basis}) · Source: quant-ledger model·factor_inputs "
              f"· 판 {view.run.build_id} · fi {view.run.fi_build_id}")
    return Title(sheet, model_line, source, note)


def bucket_weight_label(view: DayView, b: str) -> str:
    if view.spec is None:
        return bucket_label(b)
    return f"{bucket_label(b)} {view.spec.buckets[b] * 100:.0f}%"


def score_indicators(view: DayView) -> list[tuple[str, str, str]]:
    """(key, bucket, definition) — role=score, 레지스트리 순서. spec 이 없으면 데이터에서."""
    return _indicators(view, "score")


def display_indicators(view: DayView) -> list[tuple[str, str, str]]:
    return _indicators(view, "display")


def _indicators(view: DayView, role: str) -> list[tuple[str, str, str]]:
    if view.spec is not None:
        return [(i.key, i.bucket, i.definition) for i in view.spec.indicators if i.role == role]
    seen: dict[str, tuple[str, str, str]] = {}
    for per in view.ind.values():
        for k, r in per.items():
            if r.get("role") == role and k not in seen:
                seen[k] = (k, str(r.get("bucket")), "")
    return list(seen.values())


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


# ── 시트: 점수 ───────────────────────────────────────────────────────────────
def sheet_scores(wb: Workbook, view: DayView, fi: FiData,
                 prev_rank: Mapping[str, int | None]) -> Dictionary:
    others = sorted(view.other_ranks)
    groups = [
        Group("종목", id_cols(full=True)),
        Group("종합", (
            Col("rank", "순위", "rank", "#,##0", 7.0, key_col=True, color=False,
                definition="주 모델 순위(1 = 최고). 제외 종목은 빈칸"),
            Col("composite", "종합\n점수", "num", "#,##0.00", definition=(
                "있는 버킷 점수의 가중평균(0~100, 엔진 값). 제외 종목도 점수는 남는다")),
            Col("prev_rank", "전일\n순위", "num", "#,##0", color=False,
                definition="직전 성공 판(같은 basis)의 주 모델 순위"),
            Col("d_rank", "Δ순위", "chg", "#,##0", definition=(
                "전일 순위 − 오늘 순위(양수 = 상승). 색 = 높음 빨강")),
            Col("excl", "제외 사유", "txt", None, 16.0, definition=(
                "엔진 exclude_reason — D-13 적격성(관리·정지·감사·지연·거래대금) · 데이터 부족 · "
                "버킷 게이트(고점근접+반전 하위 30%)")),
            Col("cov", "커버리지", "txt", None, 8.0, definition=(
                "추정치 신선도(T2.11): 신선 · 유예 D+n(마지막 신선일부터 거래일 n) · 소멸")),
            Col("note", "비고", "txt", None, 16.0, definition=(
                "추정기관수(WISE 최근 3개월 투자의견을 낸 증권사 수): 0 = 최근 3개월 의견 없음 · "
                "1~3 = 애널리스트 3명 이하 · 모름 = 애널리스트 수 미상 · 4 이상은 빈칸")),
        ), core=True),
    ]
    for b in view.buckets:
        name = bucket_label(b)
        groups.append(Group(bucket_weight_label(view, b), (
            Col(f"{b}_u", f"{name}\n유니버스", "pct", "#,##0.0", definition=(
                f"{name} 버킷 점수(엔진)의 유니버스 백분위 0~100 — 높을수록 좋다")),
            Col(f"{b}_s", f"{name}\n업종", "pct", "#,##0.0", definition=(
                f"{name} 버킷 점수의 WICS 대분류 안 백분위(표본 < 5 면 유니버스)")),
        )))
    groups.append(Group("결측", (Col("miss", "결측 축", "txt", None, 14.0, definition=(
        "버킷 점수가 빈 축(점수 대상 종목만). 사유는 축 칸의 '결측(사유)'")),)))
    if others:
        groups.append(Group("다른 모델 순위", (
            *(Col(f"r:{s}", model_label(s), "num", "#,##0", color=False,
                  definition=f"{s} 순위(같은 판)") for s in others),
            Col("max_diff", "최대 차이", "num", "#,##0",
                definition="주 모델 포함 모델 순위의 최댓값 − 최솟값(순위 둘 이상일 때)"),
        )))

    rows: list[Row] = []
    for row in view.rows:
        t = ticker_of(row)
        u = view.uni.get(t, {})
        r = rank_of(row)
        pr = prev_rank.get(t)
        out: Row = {**id_values(view, t, fi), "rank": r, "composite": composite_of(row),
                    "prev_rank": pr, "d_rank": None if r is None or pr is None else pr - r,
                    "excl": exclude_label(row.get("exclude_reason")),
                    # v3·v2 점수 행엔 신선도 열이 없다 → 그 판 fi_universe 값(v4 는 같은 값을 행에 싣는다)
                    "cov": coverage_label(row.get("coverage_state", u.get("coverage_state")),
                                          u.get("coverage_age_days")),
                    "note": analyst_note(u.get("n_analysts"))}
        scored = t in view.ind
        miss = []
        for b in view.buckets:
            if view.bucket_score(t, b) is not None:
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
    note = ("축 = 버킷 점수의 백분위(높을수록 좋다, 빨강 = 상위). 제외 종목은 점수만 남고 순위가 "
            "없다. 결측은 '결측(사유)'.")
    return write_table(wb, title_for(view, "점수", note), groups, rows, sort_key="rank")


# ── 시트: 점수 원자료 ─────────────────────────────────────────────────────────
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
    groups.append(Group("기준", (
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
        a = fi.annual(t, ("ni",))
        qs = [p for p, q in fi.quarters(t).items() if q.get("op") is not None]
        fy0 = fi.fy0(t, view.date)
        cur = None if fy0 is None else fi.cons_at(t, fy0)
        out.update(fy=None if a is None else a["period"],
                   fy_avail=None if a is None else iso(a.get("available_date")),
                   fq=max(qs, default=None), obs=None if cur is None else iso(cur.get("obs_date")),
                   n_an=view.uni.get(t, {}).get("n_analysts"), flow_d=fi.flow_last.get(t))
        rows.append(out)
    note = "점수에 들어간 하위 지표 원값 · 엔진 백분위 · 표식. 5행 = 정의(전문은 메타). 색 없음."
    return write_table(wb, title_for(view, "점수 원자료", note), groups, rows, defs_row=True)


# ── 시트: 지표(표시용) ────────────────────────────────────────────────────────
def sheet_display(wb: Workbook, view: DayView, fi: FiData) -> Dictionary:
    inds = display_indicators(view)
    groups = [Group("종목", id_cols())]
    for b in dict.fromkeys(bk for _, bk, _ in inds):
        cols: list[Col] = []
        for key, bk, definition in inds:
            if bk != b:
                continue
            m = ind_meta(key)
            cols += [Col(f"v:{key}", m.label, "chg" if m.change else "num", m.fmt,
                         definition=f"{key}: {definition} · 표시 = 1/99 윈저라이즈"),
                     Col(f"f:{key}", f"{key}\n표식", "txt", None, 9.0,
                         definition="엔진 flag(결측 사유는 값 칸의 '결측(사유)')")]
        groups.append(Group(f"{bucket_label(b)}(표시)", tuple(cols)))
    groups.append(Group("재무(최근 연간)", (
        Col("roe", "ROE(%)", definition="최근 연간 확정 ROE(WISE 투자지표) · 1/99 윈저라이즈"),
        Col("roa", "ROA(%)", definition="최근 연간 확정 ROA(WISE) · 1/99 윈저라이즈"),
        Col("debt", "부채비율(%)", definition="최근 연간 확정 부채비율(WISE) · 1/99 윈저라이즈"),
    )))
    groups.append(Group("컨센서스(당해 결산기)", (
        Col("per", "선행 PER(배)", definition=(
            "당해 결산기 컨센서스 PER(cur). 컨센서스 순이익 < 0 이면 '적자' · 1/99 윈저라이즈")),
        Col("pbr", "선행 PBR(배)", fmt="#,##0.00",
            definition="당해 결산기 컨센서스 PBR(cur) · 1/99 윈저라이즈"),
        Col("eps1m", "선행 EPS\n1M 변화(%)", "chg", definition=(
            "(EPS cur − EPS 1개월 전) ÷ |1개월 전| × 100. 이전값 < 0 은 표식만(E) · 윈저라이즈")),
        Col("eps1m_f", "EPS 1M\n표식", "txt", None, 8.0, definition="흑전·적전·적확·적축(v3 규약)"),
    )))
    rows: list[Row] = []
    for row in view.rows:
        t = ticker_of(row)
        out: Row = id_values(view, t)
        for key, _, _ in inds:
            v, _p, f = ind_cells(view, t, key)
            out[f"v:{key}"], out[f"f:{key}"] = v, f
        a = fi.annual(t, ("roe", "roa", "debt_ratio"))
        if a is not None:
            out.update(roe=_num(a.get("roe")), roa=_num(a.get("roa")),
                       debt=_num(a.get("debt_ratio")))
        fy0 = fi.fy0(t, view.date)
        cur = None if fy0 is None else fi.cons_at(t, fy0)
        prev = None if fy0 is None else fi.cons_at(t, fy0, "1m")
        if cur is not None:
            ni = _num(cur.get("ni"))
            out["per"] = "적자" if ni is not None and ni < 0 else _num(cur.get("per"))
            out["pbr"] = _num(cur.get("pbr"))
            out["eps1m"], out["eps1m_f"] = change_pct(
                _num(cur.get("eps")), None if prev is None else _num(prev.get("eps")))
        rows.append(out)
    numeric = [f"v:{k}" for k, _, _ in inds] + ["roe", "roa", "debt", "per", "pbr", "eps1m"]
    for key in numeric:
        vals = winsorize([r.get(key) for r in rows])
        for r, v in zip(rows, vals, strict=True):
            r[key] = v
    note = ("점수에 넣지 않는 참고 지표(레지스트리 role=display) + 원자료. "
            "숫자는 1/99 윈저라이즈한 표시값. 색은 변화 열만.")
    return write_table(wb, title_for(view, "지표(표시용)", note), groups, rows, defs_row=True)


# ── 시트: 실적 ───────────────────────────────────────────────────────────────
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


def sheet_earnings(wb: Workbook, view: DayView, fi: FiData) -> Dictionary:
    groups = [Group("종목", id_cols()),
              Group("결산기", (
                  Col("fy_1", "FY-1\n(확정)", "txt", None, 8.0,
                      definition="최근 연간 확정 결산기(매출·영업이익·순이익 중 하나라도 값)"),
                  Col("fy0", "FY0 · FY1\n(추정)", "txt", None, 14.0,
                      definition="FY-1 + 12개월 · + 24개월 결산기(컨센서스 cur)")))]
    for key, label in METRICS:
        cols: list[Col] = []
        for slot, what in (("a", "FY-1"), ("e0", "FY0 E"), ("e1", "FY1 E")):
            base = {"a": "확정", "e0": "당해 추정(컨센서스 cur)", "e1": "차기 추정(컨센서스 cur)"}
            cols += [
                Col(f"{key}_{slot}", f"{what}", "num", "#,##0",
                    definition=f"{label} {base[slot]}, 억원"),
                Col(f"{key}_{slot}_y", f"{what}\ny-y(%)", "chg", "#,##0.0", definition=(
                    f"{label} 전년 대비(%) — 직전 결산기(확정 우선, 없으면 추정)와 비교")),
                Col(f"{key}_{slot}_f", f"{what}\n표식", "txt", None, 6.0, definition=(
                    "흑전(−→+) · 적전(+→−) · 적지(−→−): 증가율 대신 표식만(E)")),
            ]
        groups.append(Group(f"{label}(억원)", tuple(cols)))
    qcols = [Col("q0p", "최근\n분기", "txt", None, 8.0, definition="영업이익이 있는 최근 분기")]
    for k in range(N_QUARTERS - 1, -1, -1):
        qcols.append(Col(f"q{k}", "Q0(최근)" if k == 0 else f"Q-{k}", "num", "#,##0",
                         definition=f"분기 영업이익(3개월 값), 억원 — 최근 분기 − {3 * k}개월"))
    qcols += [Col("q_y", "Q0\ny-y(%)", "chg", "#,##0.0",
                  definition="최근 분기 영업이익 전년 동기 대비"),
              Col("q_f", "Q0\n표식", "txt", None, 6.0, definition="흑전·적전·적지(E)"),
              Col("q_avail", "Q0\n공시일", "txt", None, 10.0,
                  definition="최근 분기 행의 available_date(공시로 알게 된 날)")]
    groups.append(Group("분기 영업이익(억원)", tuple(qcols)))

    rows: list[Row] = []
    for row in view.rows:
        t = ticker_of(row)
        out: Row = id_values(view, t)
        a = fi.annual(t, ("revenue", "op", "ni"))
        if a is not None:
            fy_1 = str(a["period"])
        else:
            fy0p = fi.fy0(t, view.date)
            fy_1 = None if fy0p is None else period_add(fy0p, -12)
        if fy_1 is not None:
            p0, p1 = period_add(fy_1, 12), period_add(fy_1, 24)
            out.update(fy_1=fy_1, fy0=f"{p0} · {p1}")
            for key, _ in METRICS:
                slots = (("a", fy_1, _actual(fi, t, fy_1, key)),
                         ("e0", p0, _estimate(fi, t, p0, key)),
                         ("e1", p1, _estimate(fi, t, p1, key)))
                for slot, p, v in slots:
                    y, f = yoy(v, _either(fi, t, period_add(p, -12), key))
                    out[f"{key}_{slot}"], out[f"{key}_{slot}_y"], out[f"{key}_{slot}_f"] = v, y, f
        qs = fi.quarters(t)
        with_op = [p for p, q in qs.items() if q.get("op") is not None]
        if with_op:
            q0 = max(with_op)
            out["q0p"] = q0
            for k in range(N_QUARTERS):
                q = qs.get(period_add(q0, -3 * k))
                out[f"q{k}"] = None if q is None else _num(q.get("op"))
            out["q_y"], out["q_f"] = yoy(_num(out.get("q0")),
                                         _num(out.get(f"q{N_QUARTERS - 1}")))
            out["q_avail"] = iso(qs[q0].get("available_date"))
        rows.append(out)
    note = ("연간 FY-1 확정 · FY0·FY1 추정(컨센서스) · 분기 영업이익(최근 5기). 부호 전환은 증가율 "
            "대신 표식. 어닝시즌(예정일·잠정치·서프라이즈)은 원천이 없어 비운다.")
    return write_table(wb, title_for(view, "실적", note), groups, rows)


# ── 시트: 업종 ───────────────────────────────────────────────────────────────
def _median(xs: Sequence[float]) -> float | None:
    return statistics.median(xs) if xs else None


def sheet_sectors(wb: Workbook, view: DayView) -> Dictionary:
    n_ranked = len(view.ranked)
    top_n = view.output.top_n
    cands = set(view.candidates)
    has = {k for per in view.ind.values() for k in per}
    groups = [
        Group("업종", (
            Col("level", "구분", "txt", None, 6.0, definition="대분류 · 중분류(WICS)"),
            Col("code", "코드", "id", None, 7.0, definition="WICS 코드"),
            Col("sname", "업종명", "id", None, 16.0, item=True, definition="WICS 업종명"),
            Col("parent", "대분류", "id", None, 12.0, definition="중분류의 상위 대분류"))),
        Group("규모", (
            Col("n", "종목 수", "num", "#,##0", definition="주 모델 모집단 종목 수(제외 포함)"),
            Col("n_ranked", "순위\n종목 수", "num", "#,##0", definition="순위가 있는 종목 수"),
            Col("mcap", "시총 합(억)", "num", "#,##0", definition="모집단 시가총액 합, 억원"),
            Col("w", "시총 비중(%)", "num", "#,##0.0", definition="모집단 시총 합 대비 비중"))),
        Group("쏠림(%)", (
            Col("t30", f"상위 {top_n}\n비중", "num", "#,##0.0",
                definition=f"순위 ≤ {top_n} 종목 중 이 업종 비중(업종 상한 적용 전)"),
            Col("t100", "상위 100\n비중", "num", "#,##0.0", definition="순위 ≤ 100 종목 중 비중"),
            Col("n_cand", "후보 수", "num", "#,##0",
                definition=f"업종 상한({view.output.max_per_sector}) 적용 후보 {top_n} 중 수"))),
        Group("종합", (Col("comp_med", "종합 점수\n중앙값", "num", "#,##0.00",
                           definition="순위 종목 종합 점수 중앙값"),), core=True),
        Group("축별 평균 백분위", tuple(
            Col(f"{b}_u", bucket_label(b), "pct", "#,##0.0",
                definition=f"{bucket_label(b)} 유니버스 백분위의 업종 평균")
            for b in view.buckets)),
        Group("업종 지표", (
            Col("ep_med", "E/P 중앙값\n(%)", "num", "#,##0.00", definition="후행 E/P(EP) 중앙값"),
            Col("rev_up", "리비전 상향\n비율(%)", "num", "#,##0.0",
                definition="영업이익 추정 1M 변화(REV_OP_1M) > 0 종목 비율(값 있는 종목 중)"),
            Col("r1m", "1M 수익률\n(%)", "chg", "#,##0.0",
                definition="1M 수익률(R1M) 시총가중 평균"))),
        Group("상위 종목", (Col("top", "상위 3(순위)", "txt", None, 34.0,
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
            vals = [view.upct[b][t] for t in members if t in view.upct[b]]
            out[f"{b}_u"] = statistics.fmean(vals) if vals else None

        def raws(key: str) -> dict[str, float]:
            return {t: v for t in members
                    if (v := _num(view.ind.get(t, {}).get(key, {}).get("raw"))) is not None}
        if "EP" in has:
            ep = raws("EP")
            out["ep_med"] = None if not ep else statistics.median(ep.values()) * 100
        if "REV_OP_1M" in has:
            rv = raws("REV_OP_1M")
            out["rev_up"] = sum(1 for v in rv.values() if v > 0) / len(rv) * 100 if rv else None
        if "R1M" in has:
            r1 = {t: v for t, v in raws("R1M").items() if caps.get(t)}
            w = sum(caps[t] or 0.0 for t in r1)
            out["r1m"] = sum(v * (caps[t] or 0.0) for t, v in r1.items()) / w * 100 if w else None
        return out

    rows: list[Row] = []
    for level, col in (("대분류", "sector_l1"), ("중분류", "sector_l2")):
        by: dict[str, list[str]] = {}
        for r in view.rows:
            code = r.get(col)
            if code is not None:
                by.setdefault(str(code), []).append(ticker_of(r))
        rows += [aggregate(level, code, by[code]) for code in sorted(by)]
    note = ("주 모델 모집단의 WICS 대분류·중분류 집계. 축 평균은 유니버스 백분위 평균, 1M 수익률은 "
            "시총가중.")
    return write_table(wb, title_for(view, "업종", note), groups, rows, style="sector")


# ── 시트: 모델 비교 ──────────────────────────────────────────────────────────
def sheet_models(wb: Workbook, view: DayView) -> Dictionary:
    specs = [view.spec_id, *sorted(view.other_ranks)]
    ranks = {view.spec_id: {ticker_of(r): rank_of(r) for r in view.rows}, **view.other_ranks}
    top_n = view.output.top_n
    groups = [
        Group("종목", id_cols()),
        Group("순위", tuple(Col(f"r:{s}", model_label(s), "rank", "#,##0",
                              key_col=s == view.spec_id, definition=f"{s} 순위(1 = 최고)")
                          for s in specs), core=True),
        Group(f"상위 {top_n}", tuple(Col(f"t:{s}", model_label(s), "txt", None, 8.0,
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
    note = "주 모델 모집단 종목의 모델별 순위(같은 판). 모델마다 유니버스가 달라 빈칸이 있다."
    return write_table(wb, title_for(view, "모델 비교", note), groups, rows,
                       sort_key=f"r:{view.spec_id}")


# ── 시트: 메타 ───────────────────────────────────────────────────────────────
def gates_text(g: object) -> str:
    """판 manifest 의 게이트 요약(dict · list 모두)."""
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
    parts = ["추정치 보유" if u.require_estimates else "추정치 무관",
             "/".join(u.sec_types), "/".join(u.markets),
             f"추정치 유예 {u.coverage_grace_days} 거래일"]
    if u.min_market_cap is not None:
        parts.append(f"시총 ≥ {u.min_market_cap:,.0f}억")
    if u.min_adv20 is not None:
        parts.append(f"20세션 거래대금 ≥ {u.min_adv20:,.0f}억")
    if u.exclude:
        parts.append("제외 " + "·".join(u.exclude))
    return " · ".join(parts)


def meta_pairs(view: DayView, fi_meta: Mapping[str, object], fi: FiData | None) -> list[
        tuple[str, object]]:
    run = view.run
    counts: dict[str, int] = {}
    for r in view.rows:
        if r.get("exclude_reason"):
            k = exclude_label(r["exclude_reason"])
            counts[k] = counts.get(k, 0) + 1
    scored = [t for t in view.by_ticker if t in view.ind]
    miss = {b: sum(1 for t in scored if view.bucket_score(t, b) is None) for b in view.buckets}
    pairs: list[tuple[str, object]] = [
        ("기준일", view.date), ("basis", run.basis),
        ("주 모델", f"{view.spec_id}({model_label(view.spec_id)})"),
        ("비교 모델", ", ".join(f"{s}({model_label(s)})" for s in sorted(view.other_ranks))),
        ("model 판 id", run.build_id), ("model 생성 시각", run.generated_at),
        ("factor_inputs 판 id", run.fi_build_id),
        ("equity 판 id", _builds_text(fi_meta.get("equity_builds")) or "(fi manifest 없음)"),
        ("유니버스 규칙", universe_text(view)),
        ("종목 수", f"모집단 {len(view.rows)} · 순위 {len(view.ranked)} · "
                   f"제외 {len(view.rows) - len(view.ranked)}"),
        ("제외 사유별", " · ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "없음"),
        ("축별 결측 수", " · ".join(f"{bucket_label(b)} {n}" for b, n in miss.items())),
    ]
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
            ("후보 규칙", f"상위 {view.output.top_n} · {view.output.sector_level} 업종당 최대 "
                      f"{view.output.max_per_sector}"),
        ]
    for sid in sorted(run.specs):
        s = run.specs[sid]
        pairs.append((f"판 게이트 {sid}",
                      f"점수 {s.get('n_scores')} · 순위 {s.get('n_ranked')} · "
                      f"제외 {s.get('n_excluded')} · {gates_text(s.get('gates'))}"))
    pairs += [(f"각주 {i}", text) for i, text in enumerate(FOOTNOTES, start=1)]
    return pairs


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


def daily_path(out_root: Path, d: str, basis: str) -> Path:
    return Path(out_root) / "daily" / f"model_scores_{ymd(d)}_{basis}.xlsx"


def save_atomic(wb: Workbook, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    wb.save(tmp)
    os.replace(tmp, path)


def build_daily(d: str | date, basis: str, *, model_root: Path, fi_root: Path, out_root: Path,
                config_dir: Path | None = None) -> DailyResult:
    run = load_run(model_root, d, basis)
    view = load_day(model_root, fi_root, run, config_dir=config_dir)
    fi = load_fi(fi_root, view)
    prev = previous_run(model_root, run.date, basis)
    prev_rank: dict[str, int | None] = {}
    if prev is not None:
        sid = view.spec_id if view.spec_id in prev.specs else prev.primary_spec
        try:
            prev_rank = {ticker_of(r): rank_of(r) for r in read_scores(model_root, prev, sid)}
        except DeliverError:
            prev_rank = {}

    wb = Workbook()
    wb.remove(wb.worksheets[0])
    dictionary: Dictionary = []
    dictionary += sheet_scores(wb, view, fi, prev_rank)
    dictionary += sheet_raw(wb, view, fi)
    dictionary += sheet_display(wb, view, fi)
    dictionary += sheet_earnings(wb, view, fi)
    dictionary += sheet_sectors(wb, view)
    dictionary += sheet_models(wb, view)
    pairs = meta_pairs(view, fi_run_meta(fi_root, run.date, basis, run.fi_build_id), fi)
    pairs.insert(6, ("전일 비교 판", "없음" if prev is None else f"{prev.date} {prev.build_id}"))
    write_meta(wb, title_for(view, "메타"), pairs, dictionary)
    path = daily_path(out_root, run.date, basis)
    save_atomic(wb, path)
    top = [(rank_of(r) or 0, ticker_of(r), view.name(ticker_of(r))) for r in view.ranked[:5]]
    return DailyResult(path, run.date, basis, view.spec_id, top, len(view.rows),
                       len(view.ranked), wb.sheetnames)


__all__ = ["DailyResult", "FiData", "build_daily", "daily_path", "id_cols", "id_values",
           "ind_cells", "load_fi", "model_label", "period_add", "save_atomic", "title_for"]
