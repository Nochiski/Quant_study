"""새 시트 다섯(Analyst · Events · Market · Flow · Short) 공용 — 종목 유니버스 · 거래일 창 · 글.

창(확정 샘플 30 `x5_data.py`): 1D · 1W · 1M · 3M = 1 · 5 · 20 · 60거래일 전 이후, YTD = 전년 마지막
거래일 이후. 거래일 = KRX 지수(코스피) 날짜 + 엑셀 D(모델 판이 있는 날 = 거래일 — 장 마감 엑셀은 지수가
아직 D 를 안 실었다). 블록마다 기준일 = 그 원천의 최신 날짜(≤ 엑셀 D, 사용자 결정 Q-E2). 그 날짜가
거래일이 아니면(WISE 토요일 수집 · 12-31 공시) 창은 그 앞 마지막 거래일에서 센다.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import duckdb

from .sources import SourceMissing, Sources, day_of, lit, num

# WICS 대분류 순서(요약 표 행 순)
BIGS = ("에너지", "소재", "산업재", "경기관련소비재", "필수소비재", "건강관리", "금융", "IT",
        "커뮤니케이션서비스", "유틸리티")
F_PCT, F_CNT, F_WON, F_PP, F_FL = "#,##0.0;-#,##0.0", "#,##0", "#,##0", "+0.00;-0.00;0.00", \
    "#,##0;-#,##0"
WINDOWS = (("1D", 1), ("1W", 5), ("1M", 20), ("3M", 60))     # 창 이름 · 몇 거래일 전
HIST_DAYS = 260             # 그래프 1년 = 260거래일 전 이후
G1 = 30                     # 그래프 원자료 첫 열(AD, 숨김)
HR = 7                      # 그래프 원자료 머리 행
# 키움 투자자별 순매수 열(시장 · 종목 수급 표가 같이 쓴다)
INV: tuple[str, ...] = (
    "ind_invsr_krw", "frgnr_invsr_krw", "natfor_krw", "orgn_krw", "fnnc_invt_krw", "insrnc_krw",
    "invtrt_krw", "etc_fnnc_krw", "bank_krw", "penfnd_etc_krw", "samo_fund_krw", "natn_krw",
    "etc_corp_krw")
# 일부만 수집된 날: 최신일의 자료 행 수가 그 직전 날의 80% 미만이면 그 날을 빼고 직전 날을 쓴다.
# 평소 날마다 흔들림은 수급 ±0.1% · 공매도 값 ±5% 안이다(10-08 로컬 사본 9~10월 실측)
PARTIAL_DAY_RATIO = 0.8


@dataclass(frozen=True)
class Stock:
    """모델 유니버스 한 종목(Score 시트 행) — 대분류 · 종합 순위 · 시총(억원) · 1M 수익률(%)."""

    code: str
    name: str
    big: str
    rank: int | None
    mcap: float | None
    r1m: float | None


Universe = Mapping[str, Stock]          # 코드 → 종목(Score 행 순서)


def by_rank(uni: Universe, codes: Sequence[str]) -> list[str]:
    """종합 순위 높은 순(순위 없는 제외 종목은 뒤)."""
    return sorted(codes, key=lambda c: (uni[c].rank is None, uni[c].rank or 0))


@dataclass(frozen=True)
class Calendar:
    """창을 세는 거래일 — 끝 = 원천 최신일(`anchor`) 이하 마지막 거래일."""

    days: list[date]                    # 거래일 오름차순
    anchor: date                        # 원천 최신일(자료 상한 · 4행 날짜 — 거래일이 아닐 수 있다)

    @property
    def day(self) -> date:
        """창 기준 거래일."""
        return self.days[-1]

    def back(self, n: int) -> date:
        if n >= len(self.days):
            raise SourceMissing(f"거래일이 {len(self.days)}일뿐이다({n}거래일 전 필요)")
        return self.days[-1 - n]

    @property
    def ytd0(self) -> date:
        y = max((d for d in self.days if d.year < self.day.year), default=None)
        if y is None:
            raise SourceMissing(f"{self.day.year - 1}년 거래일이 없다(YTD 창)")
        return y

    @property
    def win(self) -> dict[str, date]:
        return {k: self.back(n) for k, n in WINDOWS} | {"YTD": self.ytd0}


def krx_calendar(con: duckdb.DuckDBPyConnection, src: Sources, anchor: date, d: date) -> Calendar:
    """원천 최신일 `anchor` 까지의 거래일 — KRX 지수(코스피) 날짜 + 엑셀 D(거래일). anchor 가
    거래일이 아니면 그 앞 마지막 거래일에서 끝난다."""
    days = [day_of(r[0]) for r in con.execute(
        f"SELECT DISTINCT date FROM {src.equity('index_daily')} WHERE index_name = '코스피' "
        f"AND date < {lit(d)} ORDER BY 1").fetchall()]
    days = [x for x in [*days, d] if x <= anchor]
    if not days:
        raise SourceMissing(f"{anchor} 이하 거래일이 없다")
    return Calendar(days, anchor)


def latest(con: duckdb.DuckDBPyConnection, rel: str, col: str, upto: date, where: str,
           what: str) -> date:
    """원천의 최신 날짜(≤ upto). 없으면 SourceMissing."""
    got = con.execute(f"SELECT max({col}) FROM {rel} WHERE {col} <= {lit(upto)} AND "
                      f"{where}").fetchone()
    if got is None or got[0] is None:
        raise SourceMissing(f"{what}에 {upto} 이하 자료가 없다")
    return day_of(got[0])


@dataclass(frozen=True)
class Latest:
    """원천의 최신 완전한 날짜와, 일부만 수집돼 뺀 날(없으면 None)."""

    day: date
    partial: date | None = None

    def note(self) -> str:
        """4행에 덧붙일 말 — 뺀 날이 있을 때만."""
        return "" if self.partial is None else f" · {md(self.partial)} 은 일부만 수집돼 뺌"


def latest_complete(con: duckdb.DuckDBPyConnection, rel: str, col: str, upto: date, where: str,
                    what: str) -> Latest:
    """원천의 최신 날짜(≤ upto) — 그 날 자료 행 수가 직전 날의 PARTIAL_DAY_RATIO 미만이면 일부만
    수집된 날로 보고 직전 날을 쓴다."""
    got = con.execute(f"SELECT {col}, count(*) FROM {rel} WHERE {col} <= {lit(upto)} AND {where} "
                      f"GROUP BY 1 ORDER BY 1 DESC LIMIT 2").fetchall()
    if not got:
        raise SourceMissing(f"{what}에 {upto} 이하 자료가 없다")
    if len(got) == 2 and got[0][1] < PARTIAL_DAY_RATIO * got[1][1]:
        return Latest(day_of(got[1][0]), day_of(got[0][0]))
    return Latest(day_of(got[0][0]))


def common_markets(con: duckdb.DuckDBPyConnection, src: Sources, day: date) -> None:
    """`mk`(ticker, market) — 그날 보통주 코스피 · 코스닥."""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE mk AS
        SELECT ticker, market FROM {src.equity('universe_daily')}
        WHERE date = {lit(day)} AND sec_type = 'common' AND market IN ('KOSPI', 'KOSDAQ')""")


def closes(con: duckdb.DuckDBPyConnection, src: Sources, day: date,
           codes: Sequence[str]) -> dict[str, float | None]:
    """종목 종가(price_daily) — `day` 행."""
    con.execute("CREATE OR REPLACE TEMP TABLE uc (ticker VARCHAR)")
    con.executemany("INSERT INTO uc VALUES (?)", [(c,) for c in codes])
    return {r[0]: num(r[1]) for r in con.execute(
        f"SELECT p.ticker, p.close FROM {src.equity('price_daily')} p JOIN uc USING (ticker) "
        f"WHERE p.date = {lit(day)}").fetchall()}


def close_day(con: duckdb.DuckDBPyConnection, src: Sources, upto: date) -> date:
    """종가가 있는 최신일(≤ upto)."""
    return latest(con, src.equity("price_daily"), "date", upto, "close IS NOT NULL",
                  "종목 일별 종가")


def md(d: date) -> str:
    return f"{d:%m-%d}"


def span(d0: date, day: date) -> str:
    """'09-30 다음 날~10-08' — 창 첫날(제외) 다음 날부터 기준일까지."""
    return f"{md(d0)} 다음 날~{md(day)}"


def base_text(day: date, d: date) -> str:
    """4행 기준일 — 원천 최신일이 엑셀 D 보다 이르면(장 마감 데이터 등) 그렇다고 적는다."""
    return (f"기준일: {day.isoformat()}" if day == d
            else f"기준일: {day.isoformat()}(원천 최신 · 엑셀 {md(d)})")


def until(day: date, base: date) -> str:
    """블록 제목 꼬리 — 그 블록 원천 날짜가 시트 4행 날짜와 다르면 ', MM-DD까지'."""
    return "" if day == base else f", {md(day)}까지"


def tail_cap(text: str, n: int = 120) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


__all__ = ["BIGS", "F_CNT", "F_FL", "F_PCT", "F_PP", "F_WON", "G1", "HIST_DAYS", "HR", "INV",
           "PARTIAL_DAY_RATIO", "WINDOWS", "Calendar", "Latest", "Stock", "Universe", "base_text",
           "by_rank", "close_day", "closes", "common_markets", "krx_calendar", "latest",
           "latest_complete", "md", "span", "tail_cap", "until"]
