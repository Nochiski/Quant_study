"""Events 시트 — 공시 · 지분 · 자사주 · 증자(DECISIONS §7 #11, 확정 샘플 30).

원천: stage `stg_disclosure`(DART 공시 목록) · `stg_corp_map`(고유번호 → 종목코드) · `stg_event_*`
(주요사항: 자사주 취득 · 처분 결정 · 유상증자 · 전환사채 등) · equity `holder_daily`(임원 · 주요주주 ·
5% 대량보유 보고) · `price_daily`(종가). 기준일 = 공시 최신 접수일(4행), 지분 보고 · 주요사항 결정은
그 원천의 최신 날짜로 따로 세고 다르면 표 제목에 적는다.
같은 날 같은 공시의 정정 · 첨부 판본은 한 건, 임원 보유 순변동은 임원(등기 · 비등기)만(임원이 아닌
주요주주 보고는 여러 달 변동을 한꺼번에 내거나 주식 병합 전 수량이 섞여 뺀다), 5% 대량보유는 지분 변화가
있는 보고만. 주요사항 결정은 뒤에 정정 공시가 나온 판본(DART 비고 '정')을 빼고 정정본에 '[정정]'.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import duckdb
from openpyxl import Workbook

from .extra_common import (
    F_CNT,
    F_FL,
    F_PCT,
    F_PP,
    Calendar,
    Universe,
    base_text,
    by_rank,
    close_day,
    closes,
    krx_calendar,
    latest,
    md,
    span,
    tail_cap,
    until,
)
from .qpack import XRefs, XSheet, x_note
from .sources import SourceMissing, Sources, day_of, lit, num, text

NAME = "Events"
WIDTHS = {"A": 7.5, "B": 18, "C": 14, "D": 7, "E": 8, "F": 21, "G": 90}
# 공시 유형 — 제목 낱말 규칙(시험, 앞 규칙이 이긴다)
EV = [("잠정실적", r"잠정\)?실적|\(잠정\)"), ("자사주 취득", r"자기주식취득결정|자기주식취득신탁계약체결결정"),
      ("자사주 처분", r"자기주식처분결정|자기주식취득신탁계약해지결정"), ("주식 소각", r"주식소각결정"),
      ("유상증자", r"유상증자결정"), ("무상증자", r"무상증자결정"),
      ("전환사채 등", r"전환사채권발행결정|신주인수권부사채권발행결정|교환사채권발행결정"),
      ("합병 · 분할", r"합병결정|분할결정|분할합병|주식교환|주식이전"), ("공급계약", r"단일판매ㆍ공급계약"),
      ("최대주주 변경", r"최대주주변경"), ("타법인 주식 취득 · 처분", r"타법인주식및출자증권(취득|처분)결정"),
      ("배당", r"배당결정"), ("소송", r"소송"), ("거래정지 · 관리", r"매매거래정지|관리종목|상장폐지|불성실공시"),
      ("5% 대량보유", r"주식등의대량보유상황보고서"),
      ("임원 · 주요주주 소유", r"임원ㆍ주요주주특정증권등(소유상황|거래계획)보고서")]
SKIP = {None, "5% 대량보유", "임원 · 주요주주 소유"}
FUNDS = ("fdpp_fclt", "fdpp_bsninh", "fdpp_op", "fdpp_dtrp", "fdpp_ocsa", "fdpp_etc")
ARAEA, ARAEA_NFKC = "\u318d", "\u119e"          # DART 원문 'ㆍ' · stage NFKC 결과 'ᆞ'


@dataclass
class Disclosure:
    rcept_dt: str                               # 접수일 'YYYYMMDD'
    rcept_no: str
    code: str
    report_nm: str


@dataclass
class Holder:
    """임원 · 주요주주 소유(src elestock) · 5% 대량보유(majorstock) 보고 한 줄."""

    src: str
    repror: str
    rcept_dt: date
    ofcps: str | None                           # 직위('-' · 빈칸 = 주요주주)
    officer: bool                               # 임원(등기 · 비등기) 보고인가 — exec_rgist
    report_resn: str | None
    qty_change: float | None                    # 주식 수 변동
    rate: float | None                          # 보고 뒤 지분(%)
    rate_change: float | None                   # 지분 변화(%p)
    code: str


@dataclass
class Decision:
    """주요사항 결정 한 건 — 자사주 취득 · 처분 · 유상증자 · 전환사채 등."""

    rcept_no: str
    code: str
    amt: float | None                           # 금액(억원)
    desc: str                                   # '[정정] 종류 · 방법 · …'(정정본만 '[정정] ')


@dataclass
class Insider:
    """종목의 1M 임원 보유 순변동 — 주식 수 합 · 보고 건수 · 임원(직위)별 변동, 금액(억원) · 시총 대비(%)."""

    q: float = 0.0
    n: int = 0
    who: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    amt: float | None = None
    pct: float | None = None


@dataclass
class EventsData:
    cal: Calendar                               # 공시 목록 기준(4행)
    hcal: Calendar                              # 지분 보고 기준
    dcal: Calendar                              # 주요사항 결정 기준
    disclosure: list[Disclosure]
    holder: list[Holder]
    decisions: list[Decision]                   # 표 순서(취득 · 처분 · 증자 · 사채) · 원장 순
    close_day: date
    close: dict[str, float | None]


def _dart_day(d: object) -> str:
    """원장 날짜를 DART 원문 모양('2026년 09월 21일')으로 — 빈칸은 '-'(원문 표기)."""
    if d is None:
        return "-"
    x = day_of(d)
    return f"{x.year}년 {x.month:02d}월 {x.day:02d}일"


def _count(x: object) -> str:
    """원장 숫자를 DART 원문 모양('9,817,118')으로 — 빈칸은 '-'."""
    v = num(x)
    return "-" if v is None else f"{int(v):,}"


def _won(x: object) -> float | None:
    """원 → 억원(빈칸 None)."""
    v = num(x)
    return None if v is None else v / 1e8


def _one_line(x: object) -> str:
    return re.sub("\n", " ", "" if x is None else str(x))


DECISION_TABLES = ("stg_event_tsstk_aq", "stg_event_tsstk_dp", "stg_event_piic",
                   "stg_event_cvbd_is")
OFFICERS = ("등기임원", "비등기임원")              # holder_daily exec_rgist — 그 밖('-')은 임원 아닌 주요주주


def _decision_day(con: duckdb.DuckDBPyConnection, src: Sources, upto: date) -> date:
    """주요사항 결정 원천의 최신 접수일(≤ upto) — 접수번호 앞 8자리, 네 표 중 가장 늦은 날."""
    rels = " UNION ALL ".join(f"SELECT rcept_no FROM {src.stage(t)}" for t in DECISION_TABLES)
    got = con.execute(f"SELECT max(substr(rcept_no, 1, 8)) FROM ({rels}) "
                      f"WHERE substr(rcept_no, 1, 8) <= '{upto:%Y%m%d}'").fetchone()
    if got is None or got[0] is None:
        raise SourceMissing(f"주요사항 결정에 {upto} 이하 자료가 없다")
    return date(int(got[0][:4]), int(got[0][4:6]), int(got[0][6:8]))


def _decisions(con: duckdb.DuckDBPyConnection, src: Sources, cmap: dict[str, str], d1m: date,
               day: date) -> list[Decision]:
    """주요사항 결정(접수번호 앞 8자리 = 접수일이 1M 창 안) — 금액 · 글은 DART 원문 모양으로.
    공시 목록과 접수번호로 이어, 뒤에 정정 공시가 나온 판본(비고 '정' = rm_corrected_later)은 빼고
    정정본(is_correction)은 '[정정] ' 을 붙인다(공시 목록에 없는 접수번호는 그대로 둔다). 같은
    접수번호가 한 표에 두 줄이면 원장 순 마지막 줄."""
    ds = src.stage("stg_disclosure")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE dver AS
        SELECT rcept_no, bool_or(coalesce(is_correction, false)) AS corr,
               bool_or(coalesce(rm_corrected_later, false)) AS later
        FROM {ds} WHERE rcept_dt > {lit(d1m)} GROUP BY rcept_no""")
    out: list[Decision] = []

    def fetch(table: str, cols: str) -> list[tuple[str, str, list[object]]]:
        rows_: dict[str, tuple[str, str, list[object]]] = {}
        for r in con.execute(
                f"SELECT e.rcept_no, e.corp_code, coalesce(v.corr, false), {cols} "
                f"FROM {src.stage(table)} e LEFT JOIN dver v USING (rcept_no) "
                f"WHERE substr(e.rcept_no, 1, 8) > '{d1m:%Y%m%d}' "
                f"AND substr(e.rcept_no, 1, 8) <= '{day:%Y%m%d}' "
                f"AND NOT coalesce(v.later, false)").fetchall():
            if text(r[1]) in cmap:
                rows_[text(r[0])] = ("[정정] " if r[2] else "", cmap[text(r[1])], list(r[3:]))
        return [(rn, c, [tag, *vals]) for rn, (tag, c, vals) in rows_.items()]
    for rn, c, (tag, mth, pp, prc, bgd, edd) in fetch(
            "stg_event_tsstk_aq", "aq_mth, aq_pp, aqpln_prc_ostk, aqexpd_bgd, aqexpd_edd"):
        out.append(Decision(rn, c, _won(prc), f"{tag}자사주 취득 · {mth} · {_one_line(pp)} · "
                                              f"{_dart_day(bgd)}~{_dart_day(edd)}"))
    for rn, c, (tag, pp, prc, bgd, edd) in fetch(
            "stg_event_tsstk_dp", "dp_pp, dppln_prc_ostk, dpprpd_bgd, dpprpd_edd"):
        out.append(Decision(rn, c, _won(prc), f"{tag}자사주 처분 · {_one_line(pp)} · "
                                              f"{_dart_day(bgd)}~{_dart_day(edd)}"))
    for rn, c, (tag, mth, cnt, *funds) in fetch(
            "stg_event_piic", "ic_mthn, nstk_ostk_cnt, " + ", ".join(FUNDS)):
        amt = sum(x for x in map(_won, funds) if x)
        out.append(Decision(rn, c, amt or None, f"{tag}유상증자 · {mth}" + (
            f" · 새 주식 {_count(cnt)}주" if cnt is not None else "")))
    for rn, c, (tag, knd, fta, prc) in fetch("stg_event_cvbd_is", "bd_knd, bd_fta, cv_prc"):
        out.append(Decision(rn, c, _won(fta), f"{tag}{knd} · 전환가 {_count(prc)}원"))
    return out


def load(con: duckdb.DuckDBPyConnection, src: Sources, upto: date, uni: Universe) -> EventsData:
    ds = src.stage("stg_disclosure")
    day = latest(con, ds, "rcept_dt", upto, "true", "DART 공시 목록")
    cal = krx_calendar(con, src, day, upto)
    d1m = cal.win["1M"]
    hd = src.equity("holder_daily")
    hcal = krx_calendar(con, src, latest(con, hd, "rcept_dt", upto, "true", "지분 보고"), upto)
    dcal = krx_calendar(con, src, _decision_day(con, src, upto), upto)
    con.execute("CREATE OR REPLACE TEMP TABLE uc (ticker VARCHAR)")
    con.executemany("INSERT INTO uc VALUES (?)", [(c,) for c in uni])
    # stage 는 공시 제목을 NFKC 로 정규화해 DART 원문의 'ㆍ'(U+318D)가 'ᆞ'(U+119E, 조합용 아래아 —
    # 홀로는 깨져 보인다)로 바뀐다. 원문 글자로 되돌린다(유형 규칙 · 표시 모두 원문 기준)
    disc = [Disclosure(text(dt), text(rn), text(t), text(nm)) for dt, rn, t, nm in con.execute(f"""
        SELECT strftime(d.rcept_dt, '%Y%m%d'), d.rcept_no, d.ticker,
               replace(d.report_nm, '{ARAEA_NFKC}', '{ARAEA}')
        FROM {ds} d JOIN uc USING (ticker)
        WHERE d.rcept_dt > {lit(d1m)} AND d.rcept_dt <= {lit(day)}""").fetchall()]
    cmap = {text(c): text(t) for c, t in con.execute(
        f"SELECT m.corp_code, m.ticker FROM {src.stage('stg_corp_map')} m JOIN uc USING (ticker)"
    ).fetchall()}
    hseen: dict[tuple[str, str], Holder] = {}
    for rn, sr, rep, cc, dt, ofc, reg, resn, qch, rate, rch in con.execute(f"""
            SELECT rcept_no, src, repror, corp_code, rcept_dt, ofcps, exec_rgist, report_resn,
                   qty_change_shr, rate_pct, rate_change_pct
            FROM {hd}
            WHERE rcept_dt > {lit(hcal.win['1M'])} AND rcept_dt <= {lit(hcal.anchor)}""").fetchall():
        if cc in cmap:                  # 같은 보고 · 보고자는 마지막 행(원장 순)
            hseen[(text(rn), text(rep))] = Holder(
                text(sr), text(rep), day_of(dt), None if ofc is None else text(ofc),
                reg in OFFICERS, None if resn is None else text(resn), num(qch), num(rate),
                num(rch), cmap[cc])
    cd = close_day(con, src, upto)
    return EventsData(cal, hcal, dcal, disc, list(hseen.values()),
                      _decisions(con, src, cmap, dcal.win["1M"], dcal.anchor), cd,
                      closes(con, src, cd, list(uni)))


def ev_kind(nm: str) -> str | None:
    for k, p in EV:
        if re.search(p, nm):
            return k
    return None


def write(wb: Workbook, e: EventsData, uni: Universe, refs: XRefs, d: date,
          *, index: int | None = None) -> list[tuple[str, str, str]]:
    cal, day = e.cal, e.cal.anchor       # day = 공시 최신 접수일(거래일이 아닐 수 있다)
    win = cal.win
    hday, dday = e.hcal.anchor, e.dcal.anchor
    cday = md(e.close_day)
    xs = XSheet(wb, NAME, WIDTHS, refs, index=index)
    ws = xs.ws
    xs.texts({"A1": "정렬: 블록마다(유형 순 · 날짜 최근 순 · 금액 순)",
              "A2": "Events: 공시 · 지분 · 자사주 · 증자",
              "A3": "자료: DART 공시 목록 · 임원 · 주요주주 소유 보고 · 5% 대량보유 보고 · 주요사항(자사주 · "
                    f"증자 · 사채) · 모델 유니버스 {len(uni)}종목",
              "A4": base_text(day, d), "F4": "출처: quant-ledger 원장 · 시험 양식",
              "A5": f"1W: 접수일 {span(win['1W'], day)} · 1M: {span(win['1M'], day)}",
              "G5": "[정정]: 기재정정 공시"})
    put, header, close, cs = xs.put, xs.header, xs.close, xs.cs
    w1 = f"{win['1W']:%Y%m%d}"
    dsl: list[tuple[Disclosure, str, str | None, bool]] = []   # (공시, 제목, 유형, 1W 안?)
    seen: set[tuple[str, str, str]] = set()
    for x in sorted(e.disclosure, key=lambda x: x.rcept_no):
        nm = re.sub(r"\s+", "", x.report_nm)
        tag = re.match(r"^\[([^\]]*)\]", nm)
        core = re.sub(r"^\[[^\]]*\]", "", nm)
        key = (x.code, x.rcept_dt, core)              # 같은 날 같은 공시의 정정 · 첨부 판본은 한 건
        if key in seen:
            continue
        seen.add(key)
        title = re.sub(r"^\[[^\]]*\]\s*", "", re.sub(r"\s{2,}", " ", x.report_nm).strip())
        dsl.append((x, ("[정정] " if tag and "정정" in tag.group(1) else "") + title, ev_kind(core),
                    x.rcept_dt > w1))
    r0 = 6
    header(r0, [("A", "유형별 공시 건수: 모델 유니버스"), ("C", "1W"), ("E", "1M"),
                ("G", "1W 종목(종합 순위 높은 순)")], ["", "유형", "건수", "종목 수", "건수", "종목 수", "종목"],
           ab=True)
    r = r0 + 2
    for k, _ in [*EV, ("그 밖", None)]:
        g = [x for x in dsl if (x[2] or "그 밖") == k]
        g1 = [x for x in g if x[3]]
        xs.lab(r, k)
        put(r, 3, len(g1), F_CNT)
        put(r, 4, len({x[0].code for x in g1}), F_CNT)
        put(r, 5, len(g), F_CNT)
        put(r, 6, len({x[0].code for x in g}), F_CNT)
        names = by_rank(uni, list({x[0].code for x in g1}))
        put(r, 7, tail_cap(" · ".join(uni[c].name for c in names[:12])
                           + (f" 외 {len(names) - 12}" if len(names) > 12 else "")) or None, text=True)
        r += 1
    e1_end = r - 1
    close(e1_end, 7)
    ws["A7"].comment = x_note("유형: 공시 제목 낱말로 나눔(시험). 그 밖: 투자설명서 · 증권발행실적 · 주주총회 · "
                              "IR 개최 · 채무보증 · 파생결합증권 등")
    t0 = r + 1
    header(t0, [("A", "주요 공시: 1W, 5% · 임원 소유 보고와 그 밖 유형 뺌"), ("C", "분류"), ("E", "공시")],
           ["코드", "이름", "대분류", "종합\n순위", "날짜", "유형", "제목"])
    korder = {k: i for i, (k, _) in enumerate(EV)}

    def rank_key(c: str) -> tuple[bool, int]:
        return (uni[c].rank is None, uni[c].rank or 0)
    l2 = sorted([x for x in dsl if x[3] and x[2] not in SKIP],
                key=lambda x: (korder[str(x[2])], -int(x[0].rcept_dt), rank_key(x[0].code)))
    r = t0 + 2
    for x, nm, k, _ in l2:
        u = uni[x.code]
        xs.stock(r, x.code, u.name, u.big, u.rank, [
            (f"{x.rcept_dt[4:6]}-{x.rcept_dt[6:]}", None, True), (k, None, True),
            (tail_cap(nm, 110), None, True)])
        r += 1
    close(r - 1, 7)
    # 임원 보유 순변동(1M) — 임원(등기 · 비등기)만. 임원을 겸한 주요주주(이사회 의장 등)는 남는다
    ho = [h for h in e.holder if h.code in uni]
    ins: dict[str, Insider] = defaultdict(Insider)
    for h in ho:
        if h.src != "elestock" or h.qty_change is None or not h.officer:
            continue
        s = ins[h.code]
        s.q += h.qty_change
        s.n += 1
        s.who[f"{h.repror}({h.ofcps or '주요주주'})"] += h.qty_change
    for c, s in ins.items():
        p, mcap = e.close.get(c), uni[c].mcap
        s.amt = s.q * p / 1e8 if p else None
        s.pct = s.amt / mcap * 100 if s.amt is not None and mcap else None
    t0 = r + 1
    header(t0, [("A", f"임원 보유 순변동: 1M 순증 상위 15 · 순감 상위 15{until(hday, day)}"),
                ("C", "분류"), ("E", "순변동")],
           ["코드", "이름", "대분류", "종합\n순위", "금액\n(억원)", "시총 대비\n(%)",
            "임원(직위) 주식 수 변동 · 보고 건수"])
    r = t0 + 2
    amts = {c: s.amt for c, s in ins.items() if s.amt}
    pos = sorted([c for c, v in amts.items() if v > 0], key=lambda c: -amts[c])[:15]
    neg = sorted([c for c, v in amts.items() if v < 0], key=lambda c: amts[c])[:15]
    for grp in (pos, neg):
        for c in grp:
            s, amt = ins[c], amts[c]
            who = sorted(s.who.items(), key=lambda kv: -abs(kv[1]))
            desc = (" · ".join(f"{k} {v:+,.0f}주" for k, v in who[:4])
                    + (f" 외 {len(who) - 4}명" if len(who) > 4 else "") + f" · {s.n}건")
            u = uni[c]
            xs.stock(r, c, u.name, u.big, u.rank, [               # 10억원 미만만 소수 1자리
                (amt, F_PCT if abs(amt) < 10 else F_FL, False), (s.pct, "0.00;-0.00", False),
                (tail_cap(desc, 140), None, True)])
            r += 1
        if grp is pos and r > t0 + 2:
            close(r - 1, 7)
    e3_end = r - 1
    close(e3_end, 7)
    cs(f"E{t0 + 2}:E{e3_end}")
    ws[f"E{t0 + 1}"].comment = x_note(
        f"금액(억원): 1M 안 임원(등기 · 비등기) 소유상황 보고의 주식 수 변동 합 × {cday} 종가. 장내 매매 · "
        "장외 · 증여 · 보상 주식을 모두 포함. 임원이 아닌 주요주주(최대주주 · 10% 이상 주주 · 특수관계인 등) "
        "보고는 여러 달 변동을 한꺼번에 내거나 주식 병합 전 수량이 섞여 뺐다")
    # 5% 대량보유(1W) — 지분 변화 0(담보 계약 변경 등) 뺌
    t0 = r + 1
    header(t0, [("A", f"5% 대량보유 보고: 1W, 지분 변화 큰 순 30{until(hday, day)}"), ("C", "분류"),
                ("E", "보고")],
           ["코드", "이름", "대분류", "종합\n순위", "날짜", "지분 변화\n(%p)", "보고자 · 사유 · 보고 뒤 지분"])
    chs = [(h, ch) for h in ho if h.src == "majorstock" and h.rcept_dt > e.hcal.win["1W"]
           and (ch := h.rate_change)]
    r = t0 + 2
    for h, ch in sorted(chs, key=lambda x: -abs(x[1]))[:30]:
        resn = re.sub(r"\s+", " ", h.report_resn or "").strip()
        line = (f"{h.repror} · {resn} · 지분 {h.rate:.2f}%" if h.rate is not None
                else f"{h.repror} · {resn}")
        u = uni[h.code]
        xs.stock(r, h.code, u.name, u.big, u.rank, [
            (md(h.rcept_dt), None, True), (ch, F_PP, False), (tail_cap(line, 140), None, True)])
        r += 1
    close(r - 1, 7)
    # 자사주 · 증자 · 사채 결정(1M)
    t0 = r + 1
    header(t0, [("A", f"자사주 · 증자 · 사채 결정: 1M{until(dday, day)}"), ("C", "분류"),
                ("E", "결정")],
           ["코드", "이름", "대분류", "종합\n순위", "날짜", "금액\n(억원)", "내용"])
    r = t0 + 2
    # 정정된 판본은 원천에서 이미 뺐다(같은 종목 · 금액이라도 접수번호가 다르면 다른 결정)
    for x in sorted(e.decisions, key=lambda x: x.rcept_no, reverse=True):
        u, rn, amt = uni[x.code], x.rcept_no, x.amt
        xs.stock(r, x.code, u.name, u.big, u.rank, [
            (f"{rn[4:6]}-{rn[6:8]}", None, True),               # 10억원 미만만 소수 1자리
            (amt, F_FL if amt and amt >= 10 else F_PCT, False),
            (tail_cap(x.desc, 140), None, True)])
        r += 1
    e5_end = r - 1
    close(e5_end, 7)
    xs.page(f"A1:G{e5_end}")
    return [("Events", "유형별 공시 건수 · 유형",
             "DART 공시 목록(유니버스 종목, 접수일 기준)을 제목 낱말로 나눔(시험) — 잠정실적 · 자사주 · 증자 · "
             "사채 · 합병 · 공급계약 · 최대주주 · 지분 보고 등"),
            ("Events", "임원 보유 순변동",
             f"임원(등기 · 비등기) 소유상황 보고의 주식 수 변동 합 × {cday} 종가(장내 · 장외 · 증여 · 보상 "
             "모두). 임원이 아닌 주요주주 보고는 뺌"),
            ("Events", "5% 대량보유 · 자사주 · 증자 · 사채",
             "대량보유 보고의 지분 변화(%p) · 사유, 주요사항 보고(자사주 취득 · 처분 결정 · 유상증자 · "
             "전환사채 등)의 금액 · 방법. 결정은 뒤에 정정된 판본을 빼고 정정본에 [정정]")]


__all__ = ["EV", "NAME", "Decision", "Disclosure", "EventsData", "Holder", "Insider", "ev_kind",
           "load", "write"]
