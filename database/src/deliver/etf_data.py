"""ETF 시트 계산 — stage `stg_etf_price_daily`(KRX ETF 일별 2010-01~) · equity `index_daily`.

정의(DECISIONS §7 #10 · 샘플 28 `etf_calc.py` · `etf_hist.py` 그대로):
  순자산 = NAV × 상장좌수 ÷ 1억(억원) — KRX 순자산 칸과 같은 값(그 칸이 0 으로 오는 날이 있다).
  자금 유입 = 날마다 (상장좌수 − 전날 좌수) × 그날 NAV ÷ 1억(설정 − 환매 추정). 액면분할 · 병합
    (좌수 비율 ≥ 1.9 또는 ≤ 0.55 이고 비율 × NAV 비율이 1 ± 3%)은 0.
  시계열(유형별 일 · 주 · 월 · 연): 신규 상장 = 첫날 순자산 전부 유입(자료 첫날 제외), 상장폐지 =
    다음 거래일에 마지막 순자산만큼 유출. 지금 상장 ETF 표(요약 · 목록)는 창 첫날 좌수를 기준으로 셈.
  수익률 = 종가 ÷ 창 첫날 종가 − 1(분배금 미반영). 창 = 1 · 5 · 20 · 60거래일 전 · 전년 마지막 거래일.

행 단위 계산(분류 조인 · 전날 좌수 · 분할 판정 · 합)은 DuckDB 로 한다. 합은 샘플과 같은 순서
(종목 코드 오름차순 · 날짜 오름차순)로 왼쪽부터 더한다(`list_reduce`) — 그래프 값 사본이 샘플과
비트 단위로 같아야 해서다(DuckDB SUM 은 더하는 순서가 정해지지 않는다).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import duckdb
import pyarrow as pa

from .etf_classify import classify, classify2
from .sources import SourceMissing, Sources, connect, day_of, lit, num, num0, text

WINDOWS = (("1D", 1), ("1W", 5), ("1M", 20), ("3M", 60))
WEEKS_SPARK, WEEKS_TABLE, MONTHS_TABLE = 12, 26, 12      # 요약 셀 안 막대 · 주간 표 · 월별 표
HIST_CHUNKS = 4                                           # 시계열 행 계산을 종목 묶음 몇 개로 나누나
# 왼쪽부터 0.0 에 차례로 더한다(파이썬 `x = 0.0; x += …` 와 같은 순서 · 같은 값)
SEQ_SUM = "list_reduce(list_prepend(0.0::DOUBLE, list({x} ORDER BY {o})), lambda a, b: a + b)"
# 분할 · 병합(배수가 맞는 날만 — 레버리지 급락일 대량 설정은 유입). s0 · n0 = 전날 좌수 · NAV
SPLIT = ("(s / s0 >= 1.9::DOUBLE OR s / s0 <= 0.55::DOUBLE) "
         "AND abs((s / s0) * (n / n0) - 1) < 0.03::DOUBLE")


def _seq(x: str, order: str) -> str:
    return SEQ_SUM.format(x=x, o=order)


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


@dataclass
class EtfRec:
    """지금(기준일) 상장 ETF 한 줄."""

    code: str
    name: str
    idx: str                                    # 기초지수 이름
    big: str                                    # 유형(레버리지(롱) · 인버스는 나눔)
    sub: str                                    # 세부(국내 · 해외주식만)
    aum: float                                  # 순자산(억원)
    px: float | None                            # 종가
    nav: float
    prem: float | None                          # 괴리율(%)
    ret: dict[str, float | None]                # 창 → 종가 수익률(%)
    flow: dict[str, float]                      # 창 → 자금 유입(억원)
    tv20: float | None                          # 거래대금 20일 평균(억원)


@dataclass
class Brand:
    """운용사(이름 첫 낱말) — 기준일 순자산 · 종목 수, 1M · YTD 유입(억원)."""

    aum: float = 0.0
    n: int = 0
    f1m: float = 0.0
    fytd: float = 0.0


@dataclass
class _Hist:
    flow: dict[tuple[date, int], float]          # (날짜, 유형 번호) → 유입
    aum: dict[tuple[date, int], float]           # (월말, 유형 번호) → 순자산
    tv: dict[date, float]                        # 날짜 → ETF 거래대금
    bflow: list[tuple[date, int, float]]         # (날짜, 운용사 번호, 유입) — 키가 처음 나온 순
    sflow: list[tuple[date, int, float]]         # (날짜, 유형|세부 번호, 유입) — 같은 순
    splits: int
    tv_from: date


@dataclass
class EtfData:
    """ETF 시트가 쓰는 값. 날짜 키는 `date`, 월 'YYYYMM' · 연 'YYYY' · 주 'YYYY-Www'."""

    day: date                                   # 기준일 = 원천 최신일(≤ 엑셀 D)
    first_day: date                             # 자료 첫날
    days: list[date]
    win: dict[str, date]                        # 1D · 1W · 1M · 3M · YTD 첫날
    recs: list[EtfRec]                          # 지금 상장 ETF(이름 순 — KRX 일별 응답 순서)
    cats: list[str]
    splits: int
    netasset_empty: bool                        # 기준일 KRX 순자산 칸이 전부 0 · 빈칸
    weekly: dict[str, dict[str, float]] = field(default_factory=dict)
    weekly_end: dict[str, date] = field(default_factory=dict)
    monthly: dict[str, dict[str, float]] = field(default_factory=dict)
    yearly: dict[str, dict[str, float]] = field(default_factory=dict)
    aum_month_end: dict[str, dict[str, float]] = field(default_factory=dict)
    aum_year_end: dict[str, dict[str, float]] = field(default_factory=dict)
    tv_share_month: dict[str, float | None] = field(default_factory=dict)
    brand: dict[str, Brand] = field(default_factory=dict)
    weekly_sub: dict[str, dict[str, float]] = field(default_factory=dict)


def load(src: Sources, upto: date) -> EtfData:
    con = connect()
    con.execute("SET preserve_insertion_order = false")    # 순서는 모든 질의가 ORDER BY 로 정한다
    try:
        return _load(con, src, upto)
    finally:
        con.close()


def _load(con: duckdb.DuckDBPyConnection, src: Sources, upto: date) -> EtfData:
    con.execute(f"""CREATE OR REPLACE TEMP VIEW raw AS
        SELECT ticker, date, name, obj_index_name AS idx, close_krw, nav_krw, list_shrs,
               value_krw, netasset_krw
        FROM {src.stage('stg_etf_price_daily')} WHERE date <= {lit(upto)}""")
    got = con.execute("SELECT max(date) FROM raw WHERE nav_krw IS NOT NULL").fetchone()
    if got is None or got[0] is None:
        raise SourceMissing(f"KRX ETF 일별에 {upto} 이하 자료가 없다")
    day: date = got[0]
    # 종목마다 마지막 행의 이름 · 기초지수로 분류(시계열 유형) — 샘플 etf_hist 와 같다
    last = [(text(t), text(nm), text(ix), day_of(d)) for t, nm, ix, d in con.execute(
        f"SELECT ticker, arg_max(coalesce(name, ''), date), arg_max(coalesce(idx, ''), date), "
        f"max(date) FROM raw WHERE date <= {lit(day)} GROUP BY ticker ORDER BY ticker").fetchall()]
    cats_of = [classify2(nm, ix)[0] for _, nm, ix, _ in last]
    subs_of = ["|".join(classify(nm, ix)) for _, nm, ix, _ in last]
    brands_of = [nm.split(" ")[0] for _, nm, _, _ in last]
    cats, subs, brands = sorted(set(cats_of)), sorted(set(subs_of)), sorted(set(brands_of))
    con.register("cls", pa.table({
        "ticker": [t for t, _, _, _ in last],
        "tid": list(range(len(last))),                       # 종목 코드 오름차순 번호
        "cat": [cats.index(c) for c in cats_of], "sub": [subs.index(c) for c in subs_of],
        "brand": [brands.index(c) for c in brands_of], "last": [d for _, _, _, d in last]}))
    # 계산 행 — 숫자만(메모리). 이름 · 기초지수는 기준일 행만 따로 읽는다
    con.execute(f"""CREATE OR REPLACE TEMP TABLE e AS
        SELECT c.tid, r.date, CAST(r.close_krw AS DOUBLE) AS px, CAST(r.nav_krw AS DOUBLE) AS n,
               CAST(r.list_shrs AS DOUBLE) AS s, CAST(r.value_krw AS DOUBLE) AS v
        FROM raw r JOIN cls c USING (ticker) WHERE r.date <= {lit(day)}""")
    days = [r[0] for r in con.execute("SELECT DISTINCT date FROM e ORDER BY 1").fetchall()]
    if len(days) < 61:
        raise SourceMissing(f"KRX ETF 일별 거래일이 {len(days)}일뿐이다(3M 창 60거래일 필요)")
    ytd0 = max((d for d in days if d.year < day.year), default=None)
    if ytd0 is None:
        raise SourceMissing(f"KRX ETF 일별에 {day.year - 1}년 자료가 없다(YTD 창)")
    win = {k: days[-1 - n] for k, n in WINDOWS} | {"YTD": ytd0}
    weeks = sorted({iso_week(d) for d in days})
    w12 = set(weeks[-WEEKS_SPARK:])
    m12 = set(sorted({f"{d:%Y%m}" for d in days})[-MONTHS_TABLE:])
    keyed_from = min(days[-21], ytd0, min(d for d in days if iso_week(d) in w12))
    tv_from = min(d for d in days if f"{d:%Y%m}" in m12)
    hist = _hist(con, day, days, keyed_from, tv_from)
    na = con.execute(f"SELECT count(*) FILTER (WHERE netasset_krw > 0) FROM raw "
                     f"WHERE date = {lit(day)}").fetchone()
    out = EtfData(day, days[0], days, win, _calc(con, day, win), cats, hist.splits,
                  netasset_empty=not (na and na[0]))
    _fold(out, hist, cats, brands, subs, con, src)
    return out


def _hist(con: duckdb.DuckDBPyConnection, day: date, days: list[date], keyed_from: date,
          tv_from: date) -> _Hist:
    """시계열 원재료 — (날짜, 유형) 별 유입(전 기간) · 월말 순자산, 날짜별 거래대금(`tv_from` 부터),
    (날짜, 운용사) · (날짜, 유형|세부) 별 유입(`keyed_from` 부터)과 그 키가 처음 나온 순서.
    행은 좌수 · NAV 가 0 · 빈칸이 아닌 것만(샘플과 같다)."""
    con.execute("CREATE OR REPLACE TEMP TABLE nxt (d DATE, d2 DATE)")
    con.executemany("INSERT INTO nxt VALUES (?, ?)",
                    [(days[i], days[i + 1]) for i in range(len(days) - 1)])
    month_end = {f"{d:%Y%m}": d for d in days}
    con.execute("CREATE OR REPLACE TEMP TABLE mend (d DATE)")
    con.executemany("INSERT INTO mend VALUES (?)", [(d,) for d in month_end.values()])
    # o = 더하는 순서(정규 행 → 폐지 유출, 각각 종목 코드 순). 종목 묶음마다 따로 지어 창 정렬의
    # 메모리를 줄인다(전날 좌수는 종목 안에서만 본다)
    con.execute("""CREATE OR REPLACE TEMP TABLE hr (o INTEGER, date DATE, cat SMALLINT,
        sub SMALLINT, brand SMALLINT, a DOUBLE, tv DOUBLE, f DOUBLE, is_split BOOLEAN)""")
    valid = "e.s IS NOT NULL AND e.s <> 0 AND e.n IS NOT NULL AND e.n <> 0"
    n_tid = con.execute("SELECT count(*) FROM cls").fetchone()
    step = max(1, -(-int(n_tid[0] if n_tid else 0) // HIST_CHUNKS))
    for lo in range(0, int(n_tid[0] if n_tid else 0), step):
        con.execute(f"""INSERT INTO hr
            SELECT tid, date, cat, sub, brand, s * n / 1e8, coalesce(v, 0) / 1e8,
                   CASE WHEN s0 IS NULL THEN (CASE WHEN date = {lit(days[0])} THEN 0.0::DOUBLE
                                                   ELSE s * n / 1e8 END)
                        WHEN {SPLIT} THEN 0.0::DOUBLE
                        ELSE (s - s0) * n / 1e8 END,
                   (s0 IS NOT NULL AND {SPLIT})
            FROM (SELECT e.tid, e.date, e.s, e.n, e.v, c.cat, c.sub, c.brand,
                         lag(e.s) OVER w AS s0, lag(e.n) OVER w AS n0
                  FROM e JOIN cls c USING (tid)
                  WHERE {valid} AND e.tid >= {lo} AND e.tid < {lo + step}
                  WINDOW w AS (PARTITION BY e.tid ORDER BY e.date))""")
    # 상장폐지: 마지막 행 다음 거래일에 마지막(유효 행) 순자산만큼 유출
    con.execute(f"""INSERT INTO hr
        SELECT 1000000 + l.tid, x.d2, c.cat, c.sub, c.brand, NULL, NULL, -(l.s * l.n / 1e8), false
        FROM (SELECT tid, arg_max(s, date) AS s, arg_max(n, date) AS n FROM e WHERE {valid}
              GROUP BY tid) l
        JOIN cls c USING (tid) JOIN nxt x ON x.d = c.last
        WHERE c.last <> {lit(day)}""")
    splits = con.execute("SELECT count(*) FROM hr WHERE is_split").fetchone()
    flow: dict[tuple[date, int], float] = {}
    for y in sorted({d.year for d in days}):           # 해마다 따로 — 순서 있는 집계의 메모리를 줄인다
        flow.update({(r[0], r[1]): r[2] for r in con.execute(
            f"SELECT date, cat, {_seq('f', 'o')} FROM hr WHERE date >= {lit(date(y, 1, 1))} "
            f"AND date <= {lit(date(y, 12, 31))} GROUP BY 1, 2").fetchall()})
    aum = {(r[0], r[1]): r[2] for r in con.execute(
        f"SELECT date, cat, {_seq('a', 'o')} FROM hr WHERE o < 1000000 "
        f"AND date IN (SELECT d FROM mend) GROUP BY 1, 2").fetchall()}
    tv = {r[0]: r[1] for r in con.execute(
        f"SELECT date, {_seq('tv', 'o')} FROM hr WHERE o < 1000000 AND date >= {lit(tv_from)} "
        f"GROUP BY 1").fetchall()}

    def keyed(col: str) -> list[tuple[date, int, float]]:
        # (날짜, 키) 별 합 — 키가 처음 나온 순서(샘플 dict 삽입 순서 = 코드 · 날짜 순 첫 행, 폐지
        # 유출로만 생긴 키는 맨 뒤)로 늘어놓는다. 키 순서는 키마다 정해져 앞 날짜를 잘라도 같다
        return [(r[0], r[1], r[2]) for r in con.execute(
            f"SELECT date, {col}, {_seq('f', 'o')} AS f, min(o) AS o1 FROM hr "
            f"WHERE date >= {lit(keyed_from)} GROUP BY 1, 2 ORDER BY o1, date").fetchall()]
    out = _Hist(flow, aum, tv, keyed("brand"), keyed("sub"), int(splits[0]) if splits else 0,
                tv_from)
    con.execute("DROP TABLE hr")
    return out


def _calc(con: duckdb.DuckDBPyConnection, day: date, win: dict[str, date]) -> list[EtfRec]:
    """지금(기준일) 상장 ETF 한 줄씩 — 순자산 · 수익률 · 창별 자금 유입 · 거래대금 20일 · 괴리율."""
    con.execute("CREATE OR REPLACE TEMP TABLE w (k VARCHAR, d0 DATE)")
    con.executemany("INSERT INTO w VALUES (?, ?)", list(win.items()))
    recs = con.execute(f"""
        WITH now AS (
            SELECT c.tid, r.ticker, coalesce(r.name, '') AS name, coalesce(r.idx, '') AS idx,
                   CAST(r.close_krw AS DOUBLE) AS px, CAST(r.nav_krw AS DOUBLE) AS nav,
                   CAST(r.list_shrs AS DOUBLE) AS sh
            FROM raw r JOIN cls c USING (ticker)
            WHERE r.date = {lit(day)} AND r.nav_krw <> 0 AND r.list_shrs <> 0),
        tv AS (
            SELECT tid, sum(v) AS tv_sum, count(v) AS tv_n FROM (
                SELECT tid, v, row_number() OVER (PARTITION BY tid ORDER BY date DESC) AS k
                FROM e WHERE tid IN (SELECT tid FROM now)) WHERE k <= 20 GROUP BY tid)
        SELECT now.tid, now.ticker, now.name, now.idx, now.px, now.nav, now.sh, tv.tv_sum, tv.tv_n
        FROM now LEFT JOIN tv USING (tid) ORDER BY now.name, now.ticker""").fetchall()
    # 창별: 첫날(d0) 좌수 · NAV 를 씨앗으로, d0 다음 날부터 좌수 · NAV 가 있는 행을 날짜순으로
    # (좌수 − 전날 좌수) × NAV 를 더한다. 전날 좌수가 없거나 0 이면 그날 좌수 전부가 유입
    split = ("p0 <> 0 AND n0 IS NOT NULL AND n0 <> 0 AND (s / p0 >= 1.9::DOUBLE "
             "OR s / p0 <= 0.55::DOUBLE) AND abs((s / p0) * (n / n0) - 1) < 0.03::DOUBLE")
    flows: dict[tuple[str, int], float] = {(k, t): f for k, t, f in con.execute(f"""
        WITH r AS (
            SELECT w.k, e.tid, e.date, e.s, e.n, lag(e.s) OVER x AS ps, lag(e.n) OVER x AS pn,
                   row_number() OVER x AS i
            FROM w JOIN e ON e.date > w.d0
            WHERE e.s IS NOT NULL AND e.n IS NOT NULL
              AND e.tid IN (SELECT tid FROM e WHERE date = {lit(day)})
            WINDOW x AS (PARTITION BY w.k, e.tid ORDER BY e.date)),
        t AS (
            SELECT r.k, r.tid, r.date, r.s, r.n,
                   coalesce(CASE WHEN r.i > 1 THEN r.ps WHEN sd.tid IS NULL THEN 0.0::DOUBLE
                                 ELSE sd.s END, 0.0::DOUBLE) AS p0,
                   CASE WHEN r.i > 1 THEN r.pn ELSE sd.n END AS n0
            FROM r LEFT JOIN (SELECT w.k, e.tid, e.s, e.n FROM w JOIN e ON e.date = w.d0) sd
                   ON sd.k = r.k AND sd.tid = r.tid)
        SELECT k, tid, {_seq(f"CASE WHEN {split} THEN 0.0::DOUBLE ELSE (s - p0) * n / 1e8 END",
                             'date')}
        FROM t GROUP BY k, tid""").fetchall()}
    p0s: dict[tuple[str, int], float | None] = {(k, t): num(px) for k, t, px in con.execute(
        "SELECT w.k, e.tid, e.px FROM w JOIN e ON e.date = w.d0").fetchall()}
    out = []
    for tid, code, name, idx, px, nav, sh, tv_sum, tv_n in recs:
        big, sub = classify(name, idx)
        if big == "레버리지·인버스":         # 요약 · 목록은 레버리지(롱) · 인버스로 나눠 싣는다
            big, sub = classify2(name, idx)[0], ""
        ret: dict[str, float | None] = {}
        for k in win:
            p0 = p0s.get((k, tid))
            ret[k] = (px / p0 - 1) * 100 if p0 and px else None
        out.append(EtfRec(
            code, name, idx, big, sub, aum=nav * sh / 1e8, px=px, nav=nav,
            prem=(px / nav - 1) * 100 if px and nav else None, ret=ret,
            flow={k: flows.get((k, tid), 0.0) for k in win},
            tv20=num0(tv_sum) / tv_n / 1e8 if tv_n else None))
    return out


def _fold(out: EtfData, h: _Hist, cats: list[str], brands: list[str], subs: list[str],
          con: duckdb.DuckDBPyConnection, src: Sources) -> None:
    """원재료를 주 · 월 · 연 · 운용사로 접는다 — 샘플 etf_hist 의 반복과 같은 순서."""
    weekly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    monthly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    yearly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    ame: dict[str, dict[str, float]] = defaultdict(dict)
    aye: dict[str, dict[str, float]] = defaultdict(dict)
    for dd in out.days:
        m, y, w = f"{dd:%Y%m}", f"{dd:%Y}", iso_week(dd)
        out.weekly_end[w] = dd
        for i, c in enumerate(cats):
            f = h.flow.get((dd, i), 0.0)
            weekly[w][c] += f
            monthly[m][c] += f
            yearly[y][c] += f
            ame[m][c] = h.aum.get((dd, i), 0.0)     # 그 달(해) 마지막 거래일 값이 남는다
            aye[y][c] = h.aum.get((dd, i), 0.0)
    out.weekly, out.monthly, out.yearly = weekly, monthly, yearly
    out.aum_month_end, out.aum_year_end = ame, aye
    # ETF 거래대금 ÷ (코스피 + 코스닥 주식 거래대금) — 지수 거래대금이 있는 날만
    mk: dict[date, float] = {}
    for _, d, v in con.execute(
            f"SELECT index_name, date, value_krw FROM {src.equity('index_daily')} "
            f"WHERE index_name IN ('코스피', '코스닥') AND date <= {lit(out.day)} "
            f"ORDER BY CASE index_name WHEN '코스피' THEN 0 ELSE 1 END, date").fetchall():
        mk[d] = mk.get(d, 0) + num0(v) / 1e8
    mt: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for dd in out.days:
        if dd in mk and dd >= h.tv_from:
            mt[f"{dd:%Y%m}"][0] += h.tv.get(dd, 0.0)
            mt[f"{dd:%Y%m}"][1] += mk[dd]
    out.tv_share_month = {m: (a / b * 100 if b else None) for m, (a, b) in mt.items()}
    # 운용사(이름 첫 낱말) — 기준일 순자산 · 종목 수, 1M(20거래일) · YTD 유입
    d20, ytd0 = out.days[-21], out.win["YTD"]
    br: dict[str, Brand] = defaultdict(Brand)
    # 더하는 순서 = 이름 순(KRX 일별 응답 순서 — 샘플이 원장 행 순서로 더했다)
    for b, a in con.execute(
            f"SELECT c.brand, coalesce(CAST(r.list_shrs AS DOUBLE), 0) "
            f"* coalesce(CAST(r.nav_krw AS DOUBLE), 0) / 1e8 FROM raw r JOIN cls c USING (ticker) "
            f"WHERE r.date = {lit(out.day)} ORDER BY r.name, r.ticker").fetchall():
        br[brands[b]].aum += a
        br[brands[b]].n += 1
    for dd, b, f in h.bflow:
        if dd > d20:
            br[brands[b]].f1m += f
        if dd > ytd0:
            br[brands[b]].fytd += f
    out.brand = br
    ws_: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for dd, k, f in h.sflow:
        ws_[iso_week(dd)][subs[k]] += f
    out.weekly_sub = ws_


__all__ = ["MONTHS_TABLE", "WEEKS_SPARK", "WEEKS_TABLE", "Brand", "EtfData", "EtfRec", "iso_week",
           "load"]
