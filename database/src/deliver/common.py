"""전달층 공통 — 라벨 · 표시 규칙(MODEL_EXCEL_SPEC D·E) · 후보 선정.

점수 계산은 하지 않는다. 백분위 재순위는 엔진과 같은 공식(`pct_rank_avg`)을 import 해 쓴다 —
정의가 두 곳으로 갈리지 않게.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from model.engines.v4_rank import pct_rank_avg

# ── 라벨 ─────────────────────────────────────────────────────────────────────
BUCKET_LABELS: dict[str, str] = {
    "low_risk": "저위험", "value": "밸류", "quality": "퀄리티", "pull": "고점근접+반전",
    "revision": "리비전", "aux": "보조", "momentum_display": "추세 모멘텀",
    "momentum": "모멘텀", "flow": "수급", "valuation": "밸류", "growth": "성장"}


def bucket_label(b: str) -> str:
    return BUCKET_LABELS.get(b, b)


@dataclass(frozen=True)
class IndMeta:
    """지표 표시 규칙 — 라벨 · 배율(비율 → %) · 숫자 서식 · 변화 열 여부(색 스케일 대상)."""

    label: str
    scale: float = 1.0
    fmt: str = "#,##0.00"
    change: bool = False


IND_META: dict[str, IndMeta] = {
    "VOL60": IndMeta("60일 변동성(%)", 100, "#,##0.00"),
    "EP": IndMeta("후행 E/P(%)", 100, "#,##0.00"),
    "DY0": IndMeta("배당수익률(%)", 100, "#,##0.00"),
    "OPM_TTM": IndMeta("영업이익률 TTM(%)", 100, "#,##0.0"),
    "FCF_A": IndMeta("FCF/자산(%)", 100, "#,##0.0"),
    "M_PULL_C": IndMeta("고점근접+반전(0~100)", 100, "#,##0.0"),
    "REV_OP_1M": IndMeta("영업이익 추정 1M(%)", 100, "#,##0.0", True),
    "REV_OP_3M": IndMeta("영업이익 추정 3M(%)", 100, "#,##0.0", True),
    "REV_NI_1M": IndMeta("순이익 추정 1M(%)", 100, "#,##0.0", True),
    "REV_NI_3M": IndMeta("순이익 추정 3M(%)", 100, "#,##0.0", True),
    "CRDT_CHG": IndMeta("신용잔고 20일 변화(%)", 100, "#,##0.0", True),
    "FRGN60": IndMeta("외국인 60일 순매수/시총(%)", 100, "#,##0.00"),
    "R1M": IndMeta("1M 수익률(%)", 100, "#,##0.0", True),
    "M_52WH": IndMeta("52주 고점 근접(%)", 100, "#,##0.0"),
    "R3M": IndMeta("3M 수익률(%)", 100, "#,##0.0", True),
    "R6M": IndMeta("6M 수익률(%)", 100, "#,##0.0", True),
    "R12_1": IndMeta("12-1 수익률(%)", 100, "#,##0.0", True),
    "EP_FWD": IndMeta("선행 E/P(%)", 100, "#,##0.00"),
}
REVISION_KEYS = frozenset({"REV_OP_1M", "REV_OP_3M", "REV_NI_1M", "REV_NI_3M"})


def ind_meta(key: str) -> IndMeta:
    """레지스트리에 새 지표가 생기면 키 그대로 라벨 · 배율 1 로 나온다(열은 자동으로 는다)."""
    return IND_META.get(key, IndMeta(key))


EXCLUDE_LABELS: dict[str, str] = {
    "admin": "관리종목", "halted": "매매정지", "audit_adverse": "감사의견 비적정",
    "filing_late": "정기보고서 지연", "adv20": "거래대금 미달", "adv20_unknown": "거래대금 미상",
    "insufficient_data": "데이터 부족"}


def exclude_label(reason: object) -> str:
    """엔진 exclude_reason → '라벨(코드)'. `<bucket>_gate` 는 '<버킷> 게이트'."""
    if reason is None or reason == "":
        return ""
    code = str(reason)
    if code in EXCLUDE_LABELS:
        return f"{EXCLUDE_LABELS[code]}({code})"
    if code.endswith("_gate"):
        return f"{bucket_label(code[:-5])} 게이트({code})"
    return code


def coverage_label(state: object, age: object) -> str:
    """fresh → 신선 · grace → 유예 D+n(마지막 신선일부터 거래일 n) · lapsed → 소멸 ·
    none → 없음."""
    if state == "fresh":
        return "신선"
    if state == "grace":
        return f"유예 D+{age}" if isinstance(age, int) else "유예"
    return {"lapsed": "소멸", "none": "없음"}.get(str(state), "" if state is None else str(state))


def market_label(m: object) -> str:
    return {"KOSPI": "KS", "KOSDAQ": "KQ"}.get(str(m), "" if m is None else str(m))


def missing(reason: object) -> str:
    """결측 표기(D: 결측 = '결측(사유)' 문자열)."""
    return f"결측({reason or '원천없음'})"


# ── 표식(E) ─────────────────────────────────────────────────────────────────
SIGN_FLAGS = ("흑전", "적전", "적확", "적축")
PREV_NEGATIVE_FLAGS = frozenset({"흑전", "적확", "적축"})   # 이전값 < 0 → 변화율 대신 표식만


def split_flags(flag: object) -> list[str]:
    return [f for f in str(flag or "").split(";") if f]


def yoy(cur: float | None, prev: float | None) -> tuple[float | None, str | None]:
    """실적 y-y(%) — 흑전(−→+) · 적전(+→−) · 적지(−→−)는 증가율 대신 표식만(E). 흑지는 빈칸.

    이전값 0 은 부호 전환으로 본다(0→+ 흑전 · 0→− 적전). 값이 없으면 (None, None).
    """
    if cur is None or prev is None:
        return None, None
    if prev > 0:
        if cur < 0:
            return None, "적전"
        return (cur / prev - 1.0) * 100.0, None
    if prev < 0:
        if cur > 0:
            return None, "흑전"
        if cur < 0:
            return None, "적지"
        return None, None
    if cur > 0:
        return None, "흑전"
    if cur < 0:
        return None, "적전"
    return None, None


def change_pct(cur: float | None, prev: float | None) -> tuple[float | None, str | None]:
    """추정 변화율(%) — v3 규약(리비전과 같다): 흑전 · 적전 · 적확 · 적축.
    이전값 ≤ 0 이면 변화율 대신 표식(E), 적전은 변화율과 표식을 함께 둔다."""
    if cur is None or prev is None or prev == 0:
        return None, None
    flag = None
    if prev < 0 < cur:
        flag = "흑전"
    elif cur < 0 < prev:
        flag = "적전"
    elif prev < 0 and cur < 0:
        flag = "적확" if abs(cur) > abs(prev) else "적축"
    if flag in PREV_NEGATIVE_FLAGS:
        return None, flag
    return (cur - prev) / abs(prev) * 100.0, flag


# ── 통계 ─────────────────────────────────────────────────────────────────────
def quantile(sorted_vals: Sequence[float], q: float) -> float:
    """선형 보간 분위수(numpy 기본과 같다). 빈 목록은 호출부가 거른다."""
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def winsorize(values: Sequence[object], lo: float = 0.01, hi: float = 0.99) -> list[object]:
    """숫자만 [1%, 99%] 분위수로 자른다(표시 전용 — 순위·점수에는 쓰지 않는다).
    문자·None 은 그대로."""
    nums = sorted(float(v) for v in values
                  if isinstance(v, int | float) and not isinstance(v, bool))
    if len(nums) < 3:
        return list(values)
    a, b = quantile(nums, lo), quantile(nums, hi)
    return [min(max(float(v), a), b) if isinstance(v, int | float) and not isinstance(v, bool)
            else v for v in values]


def group_pct(values: Mapping[str, float], group_of: Mapping[str, str | None],
              min_size: int) -> dict[str, float]:
    """그룹(업종) 안 백분위 0~100(엔진 공식). 표본 < min_size 이거나 그룹이 없으면
    유니버스 백분위."""
    wide = pct_rank_avg(values)
    groups: dict[str, dict[str, float]] = {}
    for t, v in values.items():
        g = group_of.get(t)
        if g is not None:
            groups.setdefault(g, {})[t] = v
    out = dict(wide)
    for members in groups.values():
        if len(members) >= min_size:
            out.update(pct_rank_avg(members))
    return out


def cap_candidates(ranked: Sequence[Mapping[str, object]], top_n: int, max_per: int,
                   sector_of: Callable[[Mapping[str, object]], object],
                   ) -> list[Mapping[str, object]]:
    """순위순 행에서 업종당 max_per 까지만 담아 top_n 개(OutputRule).
    업종이 없으면 '(없음)' 한 묶음."""
    out: list[Mapping[str, object]] = []
    count: dict[str, int] = {}
    for r in ranked:
        key = str(sector_of(r) or "(없음)")
        if count.get(key, 0) >= max_per:
            continue
        count[key] = count.get(key, 0) + 1
        out.append(r)
        if len(out) >= top_n:
            break
    return out


__all__ = [
    "BUCKET_LABELS", "EXCLUDE_LABELS", "IND_META", "PREV_NEGATIVE_FLAGS", "REVISION_KEYS",
    "SIGN_FLAGS", "IndMeta", "bucket_label", "cap_candidates", "change_pct", "coverage_label",
    "exclude_label", "group_pct", "ind_meta", "market_label", "missing", "pct_rank_avg",
    "quantile", "split_flags", "winsorize", "yoy",
]
