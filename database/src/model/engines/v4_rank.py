"""v4 백분위 순위 엔진 (플랜 `docs/plans/2026-09-24-v3-merge.md` §1-2 D-13' · §1-7 R-1·R-2·R-3).

가중·역할·게이트·유니버스는 레지스트리(`config/models/v4_rank_*.toml`)가, **지표 계산식은 여기**가
정본이다. 계산식은 IC 근거를 잰 연구 패널과 같게 맞췄다 — 창·최소 개수·부호의 출처를 상수마다
적는다(연구 SQL = 세션 스크래치 `factor_test/build_panel.py`·`momentum_test/build_mom.py`, 정의표 =
`docs/research/2026-09-26-v4-subfactor-ic.md` §2 · `2026-09-26-momentum-pullback-ic.md`).

흐름:
  모집단(fi_universe.eligible ∧ spec.universe 재판정 ∧ D 가격 행 — 밖이면 출력하지 않는다)
  → D-13 적격성(관리·정지·비적정 감사·지연 공시 표식 · 20세션 평균 거래대금 ≥ min_adv20).
    탈락 종목은 점수 표에 excluded·exclude_reason 으로만 남고 백분위 단면에 들어가지 않는다.
    표식이 NULL 이면 적격으로 두고 그 종목 지표 flag 에 `적격미확인(<표식>)` 을 남긴다.
  → 지표 원값(fi_* 만 읽는다; 결측이면 사유를 flag 에)
  → 점수 지표마다 백분위 0~100(동률 평균순위, 방향 적용; sector_neutral_buckets 는 대분류 안,
    값 있는 종목이 min_sector_size 미만인 대분류는 유니버스로 되돌리고 flag)
  → 버킷: 결측 비중(지표 weight 기준) ≥ bucket_missing_share 이면 버킷 결측(있던 값을 버렸으면
    그 버킷 점수 지표 flag 에 `버킷결측(NN%)`), 아니면 있는 지표끼리 가중평균
  → 종합 = 있는 버킷의 가중평균
  → 제외: 버킷 부족(insufficient_data) · 게이트(`<bucket>_gate`) — 점수는 남기고 rank 만 NULL
  → rank = 제외 아닌 종목의 (−종합, 종목코드) 순.

가격 지표는 `fi_adj_prices.adj_close`(없으면 `fi_prices.close`)의 **종목 자기 세션 행** 위에서 센다
(연구 SQL 의 `ROWS BETWEEN n PRECEDING`). `adj_ok` 가 창 안에서 바뀌면(= 미해결 기업행위 사건을
넘는 창, DQ-1) 그 지표는 결측이다. 창 전체가 사건 뒤(전부 False)면 척도가 이어지므로 계산한다.

표준 라이브러리만 쓰고, 모든 순회가 정렬된 순서라 입력 행 순서와 무관하게 같은 결과를 낸다.
"""
from __future__ import annotations

import calendar
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from model.contracts import (
    ELIGIBILITY_FLAGS,
    INDICATOR_COLUMNS,
    EngineResult,
    FactorInputs,
    Gate,
    Indicator,
    ModelSpec,
    UniverseRule,
    score_columns,
)
from model.engines._common import weighted_available

Row = Mapping[str, object]   # fi_* 행 — 값 타입은 계약(contracts.FI_TABLES)이 정한다

# ── 창·최소 개수(연구 SQL 과 같다) ─────────────────────────────────────────────
VOL_WINDOW, VOL_MIN = 60, 45        # R_VOL60: build_panel.py:122 W(59) · :255 n_vol60 ≥ 45
HIGH_WINDOW, HIGH_MIN = 252, 200    # M_52WH: build_panel.py:125 W(251) · :260 n_252 ≥ 200
LAG_1M, LAG_3M, LAG_6M, LAG_12M = 21, 63, 126, 252   # build_panel.py:126-129 ac_l21…ac_l252
FLOW_WINDOW, FLOW_MIN = 60, 45      # F_FRGN60: build_panel.py:132 W(59) · :269 n_frgn60 ≥ 45
CREDIT_LAG = 20                     # F_CRDT_CHG: build_panel.py:139 credit_l20 · :276-277
ANNUAL_MAX_AGE_DAYS = 730           # 연간 재무·배당: build_panel.py:174 · :228(D − 730일)
TTM_MAX_AGE_DAYS = 550              # TTM 최신 보고서: build_panel.py:199(D − 550일)
TTM_QUARTERS = 4
MN_PER_EOK = 100                    # 1억원 = 100백만원 — 수급(백만원) ÷ 시총(억원) 무차원화

# ── flag 어휘 — raw 가 없으면 결측 사유(반드시 하나), 있으면 표식 ─────────────────
NO_SOURCE = "원천없음"              # 원천 행·값이 없다(기한 밖 재무 포함)
SHORT = "이력부족"                  # 세션 수가 창의 최소 개수 미만
ADJ_UNRESOLVED = "수정주가미해결"    # 창이 미해결 기업행위 사건을 넘는다(adj_ok 전환)
NON_POSITIVE = "분모≤0"             # 분모(시총·매출·자산·과거 잔고·과거 추정)가 0 이하
NO_DIVIDEND = "무배당"              # DY0 = 0 으로 둔 것(연구 V_DY0: 무배당·미공시 = 0)
SECTOR_SMALL = "업종소수→전체"       # 대분류 표본 < min_sector_size → 유니버스 백분위
SECTOR_NONE = "업종없음→전체"        # 대분류 코드가 없다 → 유니버스 백분위
ELIG_UNKNOWN = "적격미확인"          # D-13 적격성 표식이 NULL — 적격으로 두되 표시(뒤에 (표식))
BUCKET_MISSING = "버킷결측"          # 결측 비중 ≥ bucket_missing_share 로 버킷을 비웠다(뒤에 (NN%))
BASIS_MIX = "기준혼합"               # TTM 4분기에 연결·별도가 섞였다 — 값은 쓰고 표시(10-01 결정 D-Q1)
HOLDING_EXCLUDED = "지주사_별도제외"  # 지주사의 연결 아닌 분기를 비워 연속 4분기가 없다(10-01)

REASON_INSUFFICIENT = "insufficient_data"
REASON_ADV20 = "adv20"                      # 20세션 평균 거래대금 < min_adv20
REASON_ADV20_UNKNOWN = "adv20_unknown"      # 거래대금을 모른다 → 적격으로 보지 않는다
# D-13 적격성 표식(contracts.ELIGIBILITY_FLAGS) → fi_universe BOOLEAN 열. 탈락 사유 = 표식 이름
FLAG_COLUMNS = {"admin": "is_admin", "halted": "is_halted", "audit_adverse": "audit_adverse",
                "filing_late": "filing_late"}
LIVE_COVERAGE = ("fresh", "grace")
REVISIONS = {"REV_OP_1M": ("op", "1m"), "REV_OP_3M": ("op", "3m"),
             "REV_NI_1M": ("ni", "1m"), "REV_NI_3M": ("ni", "3m")}
PRICE_LAGS = {"R1M": LAG_1M, "R3M": LAG_3M, "R6M": LAG_6M}
KNOWN_KEYS = frozenset({"VOL60", "EP", "DY0", "OPM_TTM", "FCF_A", "M_PULL_C", "CRDT_CHG",
                        "FRGN60", "R12_1", "M_52WH", "EP_FWD", *REVISIONS, *PRICE_LAGS})


@dataclass(frozen=True)
class Val:
    """지표 원값 하나. `raw is None` 이면 `flag` = 결측 사유, 아니면 부호 전환 등 표식(없으면
    None)."""

    raw: float | None
    flag: str | None = None


def _miss(reason: str) -> Val:
    return Val(None, reason)


# ── 백분위 ───────────────────────────────────────────────────────────────────
def _tie_groups(values: Mapping[str, float]) -> list[tuple[int, int, list[str]]]:
    """값 오름차순(동률은 종목코드순)으로 정렬해 동률 묶음마다 (첫 위치, 끝 위치, 종목들)."""
    items = sorted(values.items(), key=lambda kv: (kv[1], kv[0]))
    out: list[tuple[int, int, list[str]]] = []
    i = 0
    while i < len(items):
        j = i
        while j + 1 < len(items) and items[j + 1][1] == items[i][1]:
            j += 1
        out.append((i, j, [k for k, _ in items[i:j + 1]]))
        i = j + 1
    return out


def pct_rank_avg(values: Mapping[str, float]) -> dict[str, float]:
    """0~100 백분위 = 100 × (평균순위 − 1) / (n − 1). 동률은 평균순위, n = 1 이면 50(정보 없음)."""
    n = len(values)
    out: dict[str, float] = {}
    for i, j, keys in _tie_groups(values):
        p = 50.0 if n == 1 else 100.0 * (i + j) / (2 * (n - 1))
        out.update(dict.fromkeys(keys, p))
    return out


def percent_rank_min(values: Mapping[str, float]) -> dict[str, float]:
    """SQL `percent_rank()` 그대로 — (최소순위 − 1) / (n − 1) ∈ [0, 1], n = 1 이면 0.

    M_PULL_C 의 두 재료 순위에만 쓴다(연구 `build_mom.py:206-213` 이 이 함수로 IC 를 쟀다).
    """
    n = len(values)
    out: dict[str, float] = {}
    for i, _, keys in _tie_groups(values):
        out.update(dict.fromkeys(keys, 0.0 if n == 1 else i / (n - 1)))
    return out


# ── 입력 정리 ────────────────────────────────────────────────────────────────
def _iso(v: object) -> str:
    """DATE(date·datetime·'YYYY-MM-DD…') → 'YYYY-MM-DD'."""
    return str(v)[:10]


def _num(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise TypeError(f"숫자 열에 {type(v).__name__} 값 {v!r}")
    return float(v)


def _month_end(period: str) -> date:
    """'YYYY/MM' → 그 달 말일(결산기·분기 기말)."""
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _month_index(period: str) -> int:
    return int(period[:4]) * 12 + int(period[5:7])


def _group(rows: Sequence[Row], want: set[str]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for r in rows:
        t = str(r["ticker"])
        if t in want:
            out.setdefault(t, []).append(r)
    return out


@dataclass(frozen=True)
class _Series:
    """종목 하나의 가격 세션 행(D 이하, 날짜 오름차순). px = 수정종가(없으면 종가), ok = adj_ok."""

    dates: tuple[str, ...]
    px: tuple[float, ...]
    ok: tuple[bool, ...]

    def crosses_event(self, lo: int, hi: int) -> bool:
        """[lo, hi] 창 안에서 adj_ok 가 바뀌나 — 바뀌면 미해결 사건을 넘는 창이다."""
        seg = self.ok[lo:hi + 1]
        return any(seg) and not all(seg)


def _series(inputs: FactorInputs, codes: set[str], d: str) -> dict[str, _Series]:
    """fi_prices 행 집합 위에 fi_adj_prices 값을 얹는다. 가격이 없거나 0 이하인 행은 뺀다
    (연구 격자 `build_panel.py:103` `adj_close > 0`). adj 행이 없으면 adj_ok = True 로 둔다."""
    adj = {(str(r["ticker"]), _iso(r["date"])): r for r in inputs.rows("fi_adj_prices")
           if str(r["ticker"]) in codes}
    rows: dict[str, list[tuple[str, float, bool]]] = {c: [] for c in codes}
    for r in inputs.rows("fi_prices"):
        t, day = str(r["ticker"]), _iso(r["date"])
        if t not in codes or day > d:
            continue
        a = adj.get((t, day))
        a_px = None if a is None else _num(a["adj_close"])
        px = a_px if a_px is not None else _num(r["close"])
        if px is None or px <= 0:
            continue
        ok = True if a is None or a["adj_ok"] is None else bool(a["adj_ok"])
        rows[t].append((day, px, ok))
    out = {}
    for t, v in rows.items():
        v.sort(key=lambda x: x[0])
        out[t] = _Series(tuple(x[0] for x in v), tuple(x[1] for x in v), tuple(x[2] for x in v))
    return out


# ── 가격 지표 ────────────────────────────────────────────────────────────────
def _vol60(s: _Series) -> Val:
    """표본표준편차(최근 60세션 일간 수익률), 수익률 ≥ 45개. 연구 R_VOL60 은 이 값의 음수다."""
    i = len(s.px) - 1
    lo = max(1, i - VOL_WINDOW + 1)
    rets = [s.px[j] / s.px[j - 1] - 1.0 for j in range(lo, i + 1)]
    if len(rets) < VOL_MIN:
        return _miss(SHORT)
    if s.crosses_event(lo - 1, i):
        return _miss(ADJ_UNRESOLVED)
    return Val(statistics.stdev(rets))


def _ret(s: _Series, lag: int) -> Val:
    """px[t] / px[t − lag] − 1 (lag 세션)."""
    i = len(s.px) - 1
    if i < lag:
        return _miss(SHORT)
    if s.crosses_event(i - lag, i):
        return _miss(ADJ_UNRESOLVED)
    return Val(s.px[i] / s.px[i - lag] - 1.0)


def _r12_1(s: _Series) -> Val:
    """px[t − 21] / px[t − 252] − 1 (`build_panel.py:264`)."""
    i = len(s.px) - 1
    if i < LAG_12M:
        return _miss(SHORT)
    if s.crosses_event(i - LAG_12M, i - LAG_1M):
        return _miss(ADJ_UNRESOLVED)
    return Val(s.px[i - LAG_1M] / s.px[i - LAG_12M] - 1.0)


def _w52h(s: _Series) -> Val:
    """px[t] / max(최근 252세션), 세션 ≥ 200 (`build_panel.py:260`)."""
    i = len(s.px) - 1
    lo = max(0, i - HIGH_WINDOW + 1)
    if i + 1 - lo < HIGH_MIN:
        return _miss(SHORT)
    if s.crosses_event(lo, i):
        return _miss(ADJ_UNRESOLVED)
    return Val(s.px[i] / max(s.px[lo:i + 1]))


def _pull_c(w52: Mapping[str, Val], r1m: Mapping[str, Val]) -> dict[str, Val]:
    """M_PULL_C = (percent_rank(52WH) + percent_rank(−1개월 수익률)) / 2 — 두 재료가 다 있는 종목
    단면(`build_mom.py:206-213`). 재료가 없으면 그 재료의 결측 사유를 잇는다."""
    both = sorted(t for t in w52 if w52[t].raw is not None and r1m[t].raw is not None)
    p52 = percent_rank_min({t: r for t in both if (r := w52[t].raw) is not None})
    prev = percent_rank_min({t: -r for t in both if (r := r1m[t].raw) is not None})
    out: dict[str, Val] = {}
    for t in w52:
        if t in p52:
            out[t] = Val((p52[t] + prev[t]) / 2)
        else:
            out[t] = _miss(w52[t].flag or r1m[t].flag or NO_SOURCE)
    return out


# ── 재무 지표 ────────────────────────────────────────────────────────────────
def _latest_annual(rows: Sequence[Row], d: date, need: tuple[str, ...]) -> Row | None:
    """연간 행 중 `need` 열이 모두 있고 기말이 D − 730일 이후인 가장 최근 기.

    연구(`build_panel.py:154-175`)는 DART 최신 연간 1행을 보지만, fi_fin_summary 연간 행은 WISE·DART
    두 원천의 기 합집합이라 최신 기가 한쪽 원천 열만 가질 수 있다 — 값이 있는 최신 기를 쓴다.
    """
    floor = d - timedelta(days=ANNUAL_MAX_AGE_DAYS)
    for r in sorted(rows, key=lambda r: str(r["period"]), reverse=True):
        if r["period_type"] != "annual" or _month_end(str(r["period"])) < floor:
            continue
        if all(r[c] is not None for c in need):
            return r
    return None


def _ratio(num: float | None, den: float | None) -> Val:
    if num is None or den is None:
        return _miss(NO_SOURCE)
    if den <= 0:
        return _miss(NON_POSITIVE)
    return Val(num / den)


def _ep(fins: Sequence[Row], d: date, cap: float | None) -> Val:
    """V_EP = 연간 순이익 ÷ D 시가총액(억원 ÷ 억원)."""
    r = _latest_annual(fins, d, ("ni",))
    return _ratio(None if r is None else _num(r["ni"]), cap)


def _dy0(fins: Sequence[Row], d: date, close_d: float | None) -> Val:
    """V_DY0 = 보통주 DPS ÷ D 종가(원 ÷ 원), 무배당·미공시 = 0 (`build_panel.py:286`)."""
    if close_d is None:
        return _miss(NO_SOURCE)
    if close_d <= 0:
        return _miss(NON_POSITIVE)
    r = _latest_annual(fins, d, ("dps",))
    if r is None:
        return Val(0.0, NO_DIVIDEND)
    dps = _num(r["dps"])
    return Val((dps or 0.0) / close_d)


def _fcf_a(fins: Sequence[Row], d: date) -> Val:
    """Q_FCFA = (영업현금흐름 − |capex|) ÷ 자산총계 — fi `fcf` 가 이미 그 차다(억원 ÷ 억원)."""
    r = _latest_annual(fins, d, ("fcf", "total_assets"))
    if r is None:
        return _miss(NO_SOURCE)
    return _ratio(_num(r["fcf"]), _num(r["total_assets"]))


def _opm_ttm(fins: Sequence[Row], d: date) -> Val:
    """Q_OPM_TTM = 연속 4분기 영업이익 합 ÷ 매출 합(매출 합 > 0).

    fi 분기 행은 3개월 값이다(4Q = 사업보고서 − 1~3Q). 최신 분기부터 내려가며 op·revenue 가 다 있는
    연속 4분기를 찾는다. 그 최신 분기 기말이 D − 550일 이전이면 결측(`build_panel.py:199`).
    """
    qs = sorted((r for r in fins if r["period_type"] == "quarter"),
                key=lambda r: str(r["period"]), reverse=True)
    floor = d - timedelta(days=TTM_MAX_AGE_DAYS)
    held = False
    for k in range(len(qs) - TTM_QUARTERS + 1):
        run = qs[k:k + TTM_QUARTERS]
        idx = [_month_index(str(r["period"])) for r in run]
        if any(a - b != 3 for a, b in zip(idx, idx[1:], strict=False)):
            continue
        ops = [_num(r["op"]) for r in run]
        revs = [_num(r["revenue"]) for r in run]
        if any(v is None for v in ops) or any(v is None for v in revs):
            held = held or any(_HOLDING_MARK in str(r["fs_basis"] or "") for r in run)
            continue
        if _month_end(str(run[0]["period"])) < floor:
            return _miss(NO_SOURCE)
        v = _ratio(sum(v for v in ops if v is not None),
                   sum(v for v in revs if v is not None))
        kinds = {k for k in (_basis_kind(r) for r in run) if k is not None}
        return Val(v.raw, BASIS_MIX) if v.raw is not None and len(kinds) > 1 else v
    return _miss(HOLDING_EXCLUDED if held else NO_SOURCE)


_HOLDING_MARK = "지주사제외"         # factor_inputs 가 지주사의 연결 아닌 분기 fs_basis 에 붙이는 표식


def _basis_kind(r: Row) -> str | None:
    """분기 행의 재무제표 기준 → 연결 | 별도. 'WISE:IFRS연결' · 'DART:CFS' · 'WISE:IFRS별도|지주사제외'."""
    b = str(r["fs_basis"] or "")
    if not b:
        return None
    return "연결" if ("연결" in b or b.endswith("CFS")) else "별도"


def _opm_peers(inputs: FactorInputs, codes: set[str]) -> dict[str, str]:
    """영업이익률 비교 그룹(10-01 결정): 최근 분기 매출이 순액(`revenue_basis` 'net' — 은행·증권·금융지주
    순영업이익)인 종목. 총액(보험 영업수익·일반 매출)과 같은 대분류에서 한 줄로 세우면 총액 쪽이 구조적으로
    꼴찌라, 영업이익률 백분위만 이 종목들을 대분류 안 별도 그룹으로 매긴다."""
    latest: dict[str, tuple[str, str]] = {}
    for r in inputs.rows("fi_fin_summary"):
        t = str(r["ticker"])
        rb = r.get("revenue_basis")
        if t in codes and r["period_type"] == "quarter" and rb:
            if t not in latest or str(r["period"]) > latest[t][0]:
                latest[t] = (str(r["period"]), str(rb))
    return {t: b for t, (_, b) in latest.items() if b == "net"}


# ── 수급 · 신용 ──────────────────────────────────────────────────────────────
def _frgn60(s: _Series, flows: Sequence[Row], cap: float | None) -> Val:
    """F_FRGN60 = 최근 60세션(종목 가격 세션) 외국인 순매수 합 ÷ 시총, 값 있는 세션 ≥ 45."""
    by_day = {_iso(r["date"]): _num(r["foreign_investor"]) for r in flows}
    vals = [v for day in s.dates[-FLOW_WINDOW:] if (v := by_day.get(day)) is not None]
    if len(vals) < FLOW_MIN:
        return _miss(SHORT)
    if cap is None:
        return _miss(NO_SOURCE)
    if cap <= 0:
        return _miss(NON_POSITIVE)
    return Val(sum(vals) / (cap * MN_PER_EOK))


def _crdt_chg(s: _Series, credit: Sequence[Row], d: str) -> Val:
    """F_CRDT_CHG 원값 = 잔고주수[t] / 잔고주수[t − 20세션] − 1 (방향 −1 은 레지스트리).

    t = D 까지 **입수된**(available_date ≤ D) 최신 잔고일 — 연구는 date 당일 값을 썼지만(look-ahead,
    감사 09-19 E01) 여기는 입수일을 지킨다. t − 20 은 종목 가격 세션으로 센다(`build_panel.py:139`).
    """
    bal = {_iso(r["date"]): _num(r["credit_balance"]) for r in credit
           if r["available_date"] is not None and _iso(r["available_date"]) <= d}
    pos = {day: i for i, day in enumerate(s.dates)}
    seen = sorted(day for day, v in bal.items() if v is not None and day in pos)
    if not seen:
        return _miss(NO_SOURCE)
    i = pos[seen[-1]]
    if i < CREDIT_LAG:
        return _miss(SHORT)
    r = _ratio(bal[seen[-1]], bal.get(s.dates[i - CREDIT_LAG]))
    return r if r.raw is None else Val(r.raw - 1.0)


# ── 컨센서스 ────────────────────────────────────────────────────────────────
def _current_period(rows: Sequence[Row], asof_ym: str) -> dict[str, Row]:
    """결산기 = D 의 'YYYY/MM' 이상 중 최소(v3 `_consensus_pair` 와 같은 규칙) → {horizon: 행}."""
    periods = [str(r["target_period"]) for r in rows
               if r["target_period"] is not None and str(r["target_period"]) >= asof_ym]
    if not periods:
        return {}
    tp = min(periods)
    return {str(r["horizon"]): r for r in rows if str(r["target_period"]) == tp}


def _sign_flag(cur: float, prev: float) -> str | None:
    """v3 규약(`engines/v3_zscore._change` = v3 `revision.py:75-87`) — 흑전·적전·적확·적축."""
    if prev < 0 < cur:
        return "흑전"
    if cur < 0 < prev:
        return "적전"
    if prev < 0 and cur < 0:
        return "적확" if abs(cur) > abs(prev) else "적축"
    return None


def _revision(cur: float | None, prev: float | None) -> Val:
    """(현재 − 과거) / |과거|. v3 는 과거가 없거나 0 이면 0.0 으로 두지만(v3@1.0 재현) v4 는
    결측이다."""
    if cur is None or prev is None:
        return _miss(NO_SOURCE)
    if prev == 0:
        return _miss(NON_POSITIVE)
    return Val((cur - prev) / abs(prev), _sign_flag(cur, prev))


# ── 규칙 · 검사 ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Rules:
    """spec.params 중 이 엔진이 읽는 것(v4_rank_*.toml [params])."""

    neutral_buckets: frozenset[str]
    min_sector_size: int
    min_buckets: int
    required_buckets: tuple[str, ...]
    revision_min_analysts: int
    bucket_missing_share: float


def _int_param(p: Mapping[str, object], key: str, errs: list[str]) -> int:
    v = p.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        errs.append(f"params.{key} = {v!r} — 1 이상 정수여야 한다")
        return 1
    return v


def _share_param(p: Mapping[str, object], key: str, errs: list[str]) -> float:
    v = p.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float) or not 0 < v <= 1:
        errs.append(f"params.{key} = {v!r} — (0, 1] 비율이어야 한다")
        return 1.0
    return float(v)


def _str_list_param(p: Mapping[str, object], key: str, buckets: Sequence[str],
                    errs: list[str]) -> tuple[str, ...]:
    v = p.get(key)
    if not isinstance(v, list | tuple) or not all(isinstance(x, str) for x in v):
        errs.append(f"params.{key} = {v!r} — 버킷 이름 목록이어야 한다")
        return ()
    bad = [x for x in v if x not in buckets]
    if bad:
        errs.append(f"params.{key} 의 {bad} 가 buckets {list(buckets)} 에 없다")
    return tuple(str(x) for x in v)


def _check(spec: ModelSpec, inputs: FactorInputs) -> _Rules:
    """이 엔진이 지킬 수 없는 spec·입력은 조용히 넘기지 않고 거절한다(오류 전부를 한 번에)."""
    if spec.engine != V4RankEngine.name:
        raise ValueError(f"{spec.spec_id}: engine {spec.engine!r} ≠ v4_rank")
    errs = list(spec.validate())
    unknown = sorted({i.key for i in spec.indicators} - KNOWN_KEYS)
    if unknown:
        errs.append(f"모르는 지표 {unknown} — 계산식이 있는 것: {sorted(KNOWN_KEYS)}")
    score_keys = {i.key for i in spec.indicators if i.role == "score"}
    for g in spec.gates:
        if g.rule != "require_value" and g.key not in score_keys:
            errs.append(f"gate {g.key}: 백분위 게이트는 role=score 지표에만")
    # fi_universe.eligible(기본 규칙 판정)에 규칙을 더하는 방식이라 기본보다 넓힐 수 없다
    base = UniverseRule()
    u = spec.universe
    if not set(u.sec_types) <= set(base.sec_types):
        errs.append(f"universe.sec_types {u.sec_types} ⊄ 기본 {base.sec_types}")
    if not set(u.markets) <= set(base.markets):
        errs.append(f"universe.markets {u.markets} ⊄ 기본 {base.markets}")
    if not u.require_estimates:
        errs.append("universe.require_estimates = false 는 eligible(추정치 보유)을 넓힌다")
    unmapped = sorted(set(ELIGIBILITY_FLAGS) - set(FLAG_COLUMNS))
    if unmapped:
        errs.append(f"적격성 표식 {unmapped} 의 fi_universe 열 대응이 엔진에 없다")
    p = spec.params
    buckets = tuple(spec.buckets)
    rules = _Rules(
        neutral_buckets=frozenset(_str_list_param(p, "sector_neutral_buckets", buckets, errs)),
        min_sector_size=_int_param(p, "min_sector_size", errs),
        min_buckets=_int_param(p, "min_buckets", errs),
        required_buckets=_str_list_param(p, "required_buckets", buckets, errs),
        revision_min_analysts=_int_param(p, "revision_min_analysts", errs),
        bucket_missing_share=_share_param(p, "bucket_missing_share", errs))
    if rules.neutral_buckets and spec.sector_neutral is None:
        errs.append("params.sector_neutral_buckets 가 있는데 sector_neutral 이 없다")
    errs += inputs.check(V4RankEngine.name)
    if errs:
        raise ValueError(
            f"{spec.spec_id} × factor_inputs {inputs.build_id}: {len(errs)}건 — {errs}")
    return rules


@dataclass(frozen=True)
class _Member:
    """모집단 종목 하나. `reason` 이 있으면 D-13 적격성 탈락(점수 표 exclude_reason),
    `notes` = 적격으로 둔 채 남길 표시(NULL 표식)."""

    row: Row
    reason: str | None
    notes: tuple[str, ...] = ()


def _member(rule: UniverseRule, r: Row) -> _Member:
    """D-13 적격성 — 표식은 ELIGIBILITY_FLAGS 순서로 먼저 걸린 것 하나, 그다음 거래대금.
    적자는 거르지 않는다(E/P 음수는 값이다)."""
    notes: list[str] = []
    for flag in ELIGIBILITY_FLAGS:
        if flag not in rule.exclude:
            continue
        v = r[FLAG_COLUMNS[flag]]
        if v is None:
            notes.append(f"{ELIG_UNKNOWN}({flag})")
        elif v:
            return _Member(r, flag)
    if rule.min_adv20 is not None:
        adv = _num(r["adv20"])
        if adv is None:
            return _Member(r, REASON_ADV20_UNKNOWN)
        if adv < rule.min_adv20:
            return _Member(r, REASON_ADV20)
    return _Member(r, None, tuple(notes))


def _universe(spec: ModelSpec, inputs: FactorInputs, d: str) -> dict[str, _Member]:
    """모집단 = fi_universe.eligible(기본 규칙) ∧ spec.universe 재판정 ∧ D 가격 행 → {종목: 판정}.
    모집단 밖 종목은 모델 대상이 아니라 출력하지 않는다(추정치 보유 보통주가 아니다)."""
    on_d = {str(r["ticker"]) for r in inputs.rows("fi_prices")
            if _iso(r["date"]) == d and r["close"] is not None}
    u = spec.universe
    out: dict[str, _Member] = {}
    for r in inputs.rows("fi_universe"):
        t = str(r["ticker"])
        if not r["eligible"] or t not in on_d:
            continue
        if r["sec_type"] not in u.sec_types or r["market"] not in u.markets:
            continue
        cap = _num(r["market_cap"])
        if u.min_market_cap is not None and (cap is None or cap < u.min_market_cap):
            continue
        state, age = r["coverage_state"], r["coverage_age_days"]
        if u.require_estimates and not (
                state == "fresh"
                or (state in LIVE_COVERAGE and isinstance(age, int)
                    and age <= u.coverage_grace_days)):
            continue
        out[t] = _member(u, r)
    return out


# ── 지표 원값 ────────────────────────────────────────────────────────────────
def _raw_values(inputs: FactorInputs, uni: Mapping[str, Row], d: str,
                min_analysts: int) -> dict[str, dict[str, Val]]:
    """지표 키 → {종목: Val}. 계산식이 있는 지표 전부를 낸다(spec 이 고른 것만 출력된다)."""
    codes = set(uni)
    dd = date.fromisoformat(d)
    series = _series(inputs, codes, d)
    fins = _group(inputs.rows("fi_fin_summary"), codes)
    flows = _group(inputs.rows("fi_flows"), codes)
    credit = _group(inputs.rows("fi_credit"), codes)
    cons = _group(inputs.rows("fi_consensus"), codes)
    close_d = {str(r["ticker"]): _num(r["close"]) for r in inputs.rows("fi_prices")
               if _iso(r["date"]) == d and str(r["ticker"]) in codes}
    asof_ym = f"{d[:4]}/{d[5:7]}"

    out: dict[str, dict[str, Val]] = {k: {} for k in sorted(KNOWN_KEYS)}
    for t in sorted(codes):
        s, f, cap = series[t], fins.get(t, []), _num(uni[t]["market_cap"])
        out["VOL60"][t] = _vol60(s)
        out["M_52WH"][t] = _w52h(s)
        out["R12_1"][t] = _r12_1(s)
        for key, lag in PRICE_LAGS.items():
            out[key][t] = _ret(s, lag)
        out["EP"][t] = _ep(f, dd, cap)
        out["DY0"][t] = _dy0(f, dd, close_d.get(t))
        out["OPM_TTM"][t] = _opm_ttm(f, dd)
        out["FCF_A"][t] = _fcf_a(f, dd)
        out["FRGN60"][t] = _frgn60(s, flows.get(t, []), cap)
        out["CRDT_CHG"][t] = _crdt_chg(s, credit.get(t, []), d)

        by_h = _current_period(cons.get(t, []), asof_ym)
        cur = by_h.get("cur")
        out["EP_FWD"][t] = _ratio(None if cur is None else _num(cur["ni"]), cap)
        n = None if cur is None else cur["n_analysts"]
        for key, (metric, horizon) in REVISIONS.items():
            if cur is None:
                out[key][t] = _miss(NO_SOURCE)
            elif not isinstance(n, int) or n < min_analysts:
                out[key][t] = _miss(f"커버리지<{min_analysts}")
            else:
                prev = by_h.get(horizon)
                out[key][t] = _revision(_num(cur[metric]),
                                        None if prev is None else _num(prev[metric]))
    out["M_PULL_C"] = _pull_c(out["M_52WH"], out["R1M"])
    return out


# ── 백분위 · 버킷 · 게이트 ────────────────────────────────────────────────────
def _percentiles(ind: Indicator, vals: Mapping[str, Val], sector_of: Mapping[str, str | None],
                 neutral: bool, min_size: int) -> tuple[dict[str, float], dict[str, str]]:
    """점수 지표 하나 → ({종목: 0~100}, {종목: 되돌림 flag}). 방향을 곱한 뒤 오름차순 백분위."""
    signed = {t: ind.direction * v.raw for t, v in vals.items() if v.raw is not None}
    wide = pct_rank_avg(signed)
    if not neutral:
        return wide, {}
    groups: dict[str, dict[str, float]] = {}
    for t, x in signed.items():
        sec = sector_of.get(t)
        if sec is not None:
            groups.setdefault(sec, {})[t] = x
    pct: dict[str, float] = {}
    note: dict[str, str] = {}
    for sec in sorted(groups):
        if len(groups[sec]) >= min_size:
            pct.update(pct_rank_avg(groups[sec]))
    for t in signed:
        if t not in pct:
            pct[t] = wide[t]
            note[t] = SECTOR_NONE if sector_of.get(t) is None else SECTOR_SMALL
    return pct, note


def _gate_hit(g: Gate, raw: Val, pct: float | None) -> bool:
    """게이트 규칙(contracts.GATE_RULES). 백분위 게이트는 값이 없으면 판정하지 않는다(제외 안
    함)."""
    if g.rule == "require_value":
        return raw.raw is None
    if pct is None or g.value is None:
        return False
    cut = round(g.value * 100, 9)         # 비율 → 백분위 문턱(부동소수 끝자리 제거)
    if g.rule == "exclude_bottom_pct":
        return pct < cut
    if g.rule == "exclude_top_pct":
        return pct > 100 - cut
    raise ValueError(f"gate {g.key}: 지원하지 않는 rule {g.rule!r}")


@dataclass(frozen=True)
class _Bucket:
    """버킷 하나의 판정. `score is None` 이면 결측, `note` = 있는 값을 결측 규칙으로 버렸다는
    표식."""

    score: float | None
    note: str | None = None


def _bucket(inds: Sequence[Indicator], pcts: Mapping[str, Mapping[str, float]], t: str,
            missing_share: float) -> _Bucket:
    """결측 비중(지표 weight 기준) ≥ missing_share 이면 결측(D-13 "절반 이상 결측이면 버킷 결측"),
    아니면 있는 지표 백분위끼리 비례 재정규화한 가중평균. 표식은 있는 값을 버렸을 때만 단다 —
    전부 결측이면 지표마다 이미 사유가 있다."""
    total = sum(i.weight for i in inds)
    if total <= 0:
        return _Bucket(None)
    subs = {i.key: pcts[i.key][t] for i in inds if t in pcts[i.key]}
    share = sum(i.weight for i in inds if i.key not in subs) / total
    if round(share, 12) >= missing_share:          # 가중 합의 부동소수 끝자리를 문턱에서 뗀다
        return _Bucket(None, f"{BUCKET_MISSING}({share:.0%})" if subs else None)
    return _Bucket(weighted_available(subs, {i.key: i.weight for i in inds}))


def _score_row(spec: ModelSpec, d: str, u: Row, bscores: Mapping[str, float],
               reason: str | None) -> dict[str, object]:
    """점수 표 한 행(`score_columns(spec)` 순서). rank 는 정렬 뒤에 채운다."""
    row: dict[str, object] = {c: None for c in score_columns(spec)}
    row.update(ticker=str(u["ticker"]), score_date=d, spec_id=spec.spec_id,
               composite=weighted_available(bscores, spec.buckets),
               excluded=reason is not None, exclude_reason=reason,
               sector_l1=u["sector_l1"], sector_l2=u["sector_l2"],
               coverage_state=u["coverage_state"], n_buckets_used=len(bscores),
               **{f"{b}_score": bscores.get(b) for b in spec.buckets})
    return row


def _join(*flags: str | None) -> str | None:
    parts = [f for f in flags if f]
    return ";".join(parts) if parts else None


# ── 엔진 ─────────────────────────────────────────────────────────────────────
class V4RankEngine:
    """`contracts.Engine` 구현 — 점수 표(`score_columns(spec)`) + 지표 긴 표(INDICATOR_COLUMNS)."""

    name = "v4_rank"

    def output_columns(self, spec: ModelSpec) -> tuple[str, ...]:
        return score_columns(spec)

    def run(self, spec: ModelSpec, inputs: FactorInputs) -> EngineResult:
        rules = _check(spec, inputs)
        d = inputs.date
        members = _universe(spec, inputs, d)
        uni = {t: m.row for t, m in members.items() if m.reason is None}
        codes = sorted(uni)
        raws = _raw_values(inputs, uni, d, rules.revision_min_analysts)

        level = "sector_l2" if spec.sector_neutral == "L2" else "sector_l1"
        sector_of = {t: (None if uni[t][level] is None else str(uni[t][level])) for t in codes}
        scored = [i for i in spec.indicators if i.role == "score"]
        pcts: dict[str, dict[str, float]] = {}
        notes: dict[str, dict[str, str]] = {}
        peers = _opm_peers(inputs, set(codes))
        for ind in scored:
            sec = sector_of
            if ind.key == "OPM_TTM" and peers:
                sec = {t: (s if s is None or t not in peers else f"{s}|{peers[t]}")
                       for t, s in sector_of.items()}
            pcts[ind.key], notes[ind.key] = _percentiles(
                ind, raws[ind.key], sec,
                spec.sector_neutral is not None and ind.bucket in rules.neutral_buckets,
                rules.min_sector_size)

        by_bucket: dict[str, list[Indicator]] = {b: [] for b in spec.buckets}
        for ind in scored:
            by_bucket[ind.bucket].append(ind)
        bucket_of = {i.key: i.bucket for i in spec.indicators}
        bucket_notes: dict[tuple[str, str], str] = {}
        rows: list[dict[str, object]] = []
        for t in codes:
            bscores: dict[str, float] = {}
            for b, inds in by_bucket.items():
                res = _bucket(inds, pcts, t, rules.bucket_missing_share)
                if res.score is not None:
                    bscores[b] = res.score
                if res.note is not None:
                    bucket_notes[t, b] = res.note
            reason: str | None = None
            if (len(bscores) < rules.min_buckets
                    or any(b not in bscores for b in rules.required_buckets)):
                reason = REASON_INSUFFICIENT
            else:
                for g in spec.gates:
                    if _gate_hit(g, raws[g.key][t], pcts.get(g.key, {}).get(t)):
                        reason = f"{bucket_of[g.key]}_gate"
                        break
            rows.append(_score_row(spec, d, uni[t], bscores, reason))
        # D-13 적격성 탈락 — 점수 없이 사유만(백분위 단면에 넣지 않았다)
        rows += [_score_row(spec, d, m.row, {}, m.reason)
                 for t, m in sorted(members.items()) if m.reason is not None]

        ranked = sorted((r for r in rows if not r["excluded"]),
                        key=lambda r: (-_rank_key(r["composite"]), str(r["ticker"])))
        for k, r in enumerate(ranked, start=1):
            r["rank"] = k
        rest = sorted((r for r in rows if r["excluded"]), key=lambda r: str(r["ticker"]))

        indicators: list[dict[str, object]] = []
        for t in codes:
            for ind in spec.indicators:
                v = raws[ind.key][t]
                score = ind.role == "score"
                flag = _join(v.flag,
                             notes[ind.key].get(t) if score else None,
                             bucket_notes.get((t, ind.bucket)) if score else None,
                             *members[t].notes)
                vals: tuple[object, ...] = (
                    t, d, spec.spec_id, ind.key, ind.bucket, ind.role, v.raw,
                    pcts[ind.key].get(t) if score else None, flag)
                indicators.append(dict(zip(INDICATOR_COLUMNS, vals, strict=True)))
        return EngineResult(scores=[*ranked, *rest], indicators=indicators)


def _rank_key(composite: object) -> float:
    """정렬용 종합점수 — 제외가 아닌 종목은 버킷 ≥ min_buckets 라 종합이 반드시 있다."""
    if not isinstance(composite, float):
        raise TypeError(f"순위 대상 종목의 composite 가 float 가 아니다: {composite!r}")
    return composite


ENGINE = V4RankEngine()

__all__ = ["ENGINE", "V4RankEngine", "Val", "pct_rank_avg", "percent_rank_min"]
