"""duckdb 어댑터의 홈 시장 블록 행(`MarketDataPort`) — 지수·투자자별 순매수·섹터 구성.

원장 행만 나른다. 기간 합·가중·측정 비중 판정은 `domain.market`이 한다. 표는 부팅 때 고정한 판본
(`TableBuild.parquet_source`)으로 읽고 카탈로그 매크로를 쓰지 않는다 — sync 가 카탈로그를 바꿔도
이 질의는 옛 판본 하나만 본다.

- 수급 격자: `flow_daily` × `universe_daily`, 코스피·코스닥, ETF·우선주 제외(우선주는 원장에
  수급이 없다). 격자 밖 격리 행(`reject_reason`)은 넣지 않는다. 한 셀에 원천이 둘이면 백테스트
  필드와 같은 규칙(flow 원천 선언의 `pick_order`)으로 한 행만 고른다 — 두 행을 더하면 이중
  집계이고, 다른 규칙으로 고르면 홈과 백테스트가 같은 셀을 다르게 읽는다. 값이 없는 셀은 0으로
  치지 않고 측정 수에서 뺀다.
- 섹터: 장 이전(포함) 최신 `sector_snapshot` 과 그 장의 `price_daily`(`base_price_krw` 가 전날
  종가). `volume_shr = 0` 은 거래 없음이다.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import duckdb

from strategy_workbench.application.market_overview.facade.overview import (
    MarketDataUnreadableError,
    MarketTableMissingError,
    SectorSnapshot,
)
from strategy_workbench.domain.market.facade.market import (
    FlowDay,
    FlowMarket,
    IndexBar,
    Investor,
    SectorMember,
)

from ._adapter import EquityDuckdbAdapter, _as_date, _open
from ._specs import (
    FLOW_TABLE,
    INDEX_TABLE,
    PRICE_TABLE,
    SECTOR_TABLE,
    SECURITY_TABLE,
    SOURCE_BY_NAME,
    UNIVERSE_TABLE,
)

logger = logging.getLogger(__name__)

# 원장 열 → 주체. 13주체 중 화면이 쓰는 것만(대응 없는 열은 버린다).
FLOW_COLUMNS: tuple[tuple[Investor, str], ...] = (
    (Investor.INDIVIDUAL, "ind_invsr_krw"),
    (Investor.FOREIGN, "frgnr_invsr_krw"),
    (Investor.INSTITUTION, "orgn_krw"),
    (Investor.OTHER_CORPORATION, "etc_corp_krw"),
    (Investor.RESIDENT_FOREIGN, "natfor_krw"),
    (Investor.FINANCIAL_INVESTMENT, "fnnc_invt_krw"),
    (Investor.PENSION, "penfnd_etc_krw"),
    (Investor.INVESTMENT_TRUST, "invtrt_krw"),
    (Investor.PRIVATE_EQUITY, "samo_fund_krw"),
    (Investor.INSURANCE, "insrnc_krw"),
    (Investor.BANK, "bank_krw"),
    (Investor.OTHER_FINANCIAL, "etc_fnnc_krw"),
    (Investor.GOVERNMENT, "natn_krw"),
)
# 측정 여부를 가르는 열. 키움·KIS 두 원천 모두 이 열을 채운다(flow_daily.sql).
MEASURED_COLUMN = "frgnr_invsr_krw"
EXCLUDED_SEC_TYPES = ("etf", "preferred")
# 한 셀의 원천 행 고르기 — 백테스트 필드(`flow.*`)와 같은 선언을 읽는다.
_FLOW_PICK_ORDER = SOURCE_BY_NAME["flow"].pick_order


def _int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return int(value)
    return int(str(value))


def _float(value: object) -> float | None:
    return None if value is None else float(str(value))


def _require(adapter: EquityDuckdbAdapter, *tables: str) -> None:
    for table in tables:
        if table not in adapter._tables:  # noqa: SLF001  # reason: 같은 노드의 판본 고정표를 읽는다
            raise MarketTableMissingError(table)


@contextmanager
def _connection(card: str) -> Iterator[duckdb.DuckDBPyConnection]:
    """질의 연결. 표를 읽지 못하면(판 파일이 지워짐·열이 바뀜 등) 로그를 남기고 포트 오류로 바꾼다 —
    카드 하나만 실패한다. `card` 는 그 카드의 주 표 이름이다."""
    con = _open(None)
    try:
        yield con
    except duckdb.Error as error:
        logger.warning("market card query failed — card=%s error=%r", card, error, exc_info=True)
        raise MarketDataUnreadableError(card, repr(error)) from error
    finally:
        con.close()


def _window(sessions: Sequence[date], end: date, count: int) -> tuple[date, date] | None:
    """`end` 이전(포함) 최근 `count` 세션의 첫날·끝날."""
    stop = bisect_right(sessions, end)
    picked = sessions[max(0, stop - count) : stop]
    return (picked[0], picked[-1]) if picked else None


def latest_session(adapter: EquityDuckdbAdapter) -> date | None:
    sessions = adapter._sessions  # noqa: SLF001  # reason: 부팅 때 읽은 거래일 달력
    return sessions[-1] if sessions else None


def index_bars(
    adapter: EquityDuckdbAdapter, index_name: str, end: date, sessions: int
) -> tuple[IndexBar, ...]:
    _require(adapter, INDEX_TABLE)
    window = _window(adapter._sessions, end, sessions)  # noqa: SLF001  # reason: 달력
    if window is None:
        return ()
    with _connection(INDEX_TABLE) as con:
        rows = con.execute(
            "SELECT date, close_idx, change_idx, fluc_pct, value_krw "
            f"FROM {adapter._source(INDEX_TABLE)} "  # noqa: SLF001
            "WHERE index_name = ? AND date BETWEEN ? AND ? AND close_idx IS NOT NULL ORDER BY date",
            [index_name, *window],
        ).fetchall()
    return tuple(
        IndexBar(
            session=_as_date(day, "index_daily.date"),
            close=float(str(close)),
            change=_float(change),
            change_pct=_float(pct),
            value_krw=None if value is None else _int(value),
        )
        for day, close, change, pct, value in rows
    )


def flow_days(adapter: EquityDuckdbAdapter, end: date, sessions: int) -> tuple[FlowDay, ...]:
    _require(adapter, FLOW_TABLE, UNIVERSE_TABLE)
    window = _window(adapter._sessions, end, sessions)  # noqa: SLF001  # reason: 달력
    if window is None:
        return ()
    columns = ", ".join(column for _, column in FLOW_COLUMNS)
    sums = ", ".join(f"sum({column})" for _, column in FLOW_COLUMNS)
    excluded = ", ".join(f"'{sec_type}'" for sec_type in EXCLUDED_SEC_TYPES)
    markets = ", ".join(f"'{market.value}'" for market in FlowMarket)

    with _connection(FLOW_TABLE) as con:
        rows = con.execute(
            # 원천 행 고르기는 수급 표 안에서 먼저 한다 — 선언의 열 이름이 그 표의 것이다.
            "WITH picked AS ("
            f"  SELECT date, ticker, {MEASURED_COLUMN} IS NOT NULL AS measured, {columns}"
            f"  FROM {adapter._source(FLOW_TABLE)} "  # noqa: SLF001
            "   WHERE date BETWEEN ? AND ? AND reject_reason IS NULL"
            "   QUALIFY row_number() OVER ("
            f"    PARTITION BY date, ticker ORDER BY {_FLOW_PICK_ORDER}) = 1"
            "), cells AS ("
            "  SELECT f.*, u.market AS market, coalesce(u.mktcap_krw, 0) AS mktcap"
            "  FROM picked f "
            f"  JOIN {adapter._source(UNIVERSE_TABLE)} u "  # noqa: SLF001
            "     ON u.date = f.date AND u.ticker = f.ticker"
            f"  WHERE u.market IN ({markets}) AND u.sec_type NOT IN ({excluded})"
            ") "
            "SELECT date, market, count(*), count(*) FILTER (WHERE measured), sum(mktcap), "
            f"coalesce(sum(mktcap) FILTER (WHERE measured), 0), {sums} "
            "FROM cells GROUP BY date, market ORDER BY date, market",
            list(window),
        ).fetchall()
    return tuple(
        FlowDay(
            session=_as_date(row[0], "flow_daily.date"),
            market=FlowMarket(str(row[1])),
            grid_securities=_int(row[2]),
            measured_securities=_int(row[3]),
            grid_mktcap_krw=_int(row[4]),
            measured_mktcap_krw=_int(row[5]),
            amounts={
                investor: _int(value)
                for (investor, _), value in zip(FLOW_COLUMNS, row[6:], strict=True)
            },
        )
        for row in rows
    )


def sector_members(adapter: EquityDuckdbAdapter, session: date) -> SectorSnapshot | None:
    _require(adapter, SECTOR_TABLE)
    names = (
        f"LEFT JOIN {adapter._source(SECURITY_TABLE)} sec ON sec.ticker = s.ticker"  # noqa: SLF001
        if SECURITY_TABLE in adapter._tables  # noqa: SLF001
        else ""
    )
    name = "sec.name_current" if names else "NULL"

    with _connection(SECTOR_TABLE) as con:
        snapshot = con.execute(
            f"SELECT max(snapshot_date) FROM {adapter._source(SECTOR_TABLE)} "  # noqa: SLF001
            "WHERE snapshot_date <= ?",
            [session],
        ).fetchone()
        if snapshot is None or snapshot[0] is None:
            return None
        snapshot_date = _as_date(snapshot[0], "sector_snapshot.snapshot_date")
        rows = con.execute(
            f"SELECT s.ticker, {name}, s.wics_l1_cd, s.wics_l1_nm, s.wics_l2_cd, s.wics_l2_nm, "
            "s.float_shares_shr, p.base_price_krw, p.close, coalesce(p.volume_shr, 0) > 0 "
            f"FROM {adapter._source(SECTOR_TABLE)} s "  # noqa: SLF001
            f"LEFT JOIN {adapter._source(PRICE_TABLE)} p ON p.ticker = s.ticker AND p.date = ? "  # noqa: SLF001
            f"{names} WHERE s.snapshot_date = ?",
            [session, snapshot_date],
        ).fetchall()
    members = tuple(
        SectorMember(
            ticker=str(ticker),
            name=str(security_name) if security_name is not None else str(ticker),
            l1_code=str(l1_code),
            l1_name=str(l1_name),
            l2_code=str(l2_code),
            l2_name=str(l2_name),
            float_shares=_int(shares),
            prev_close=_float(prev_close),
            close=_float(close),
            traded=bool(traded),
        )
        for (
            ticker,
            security_name,
            l1_code,
            l1_name,
            l2_code,
            l2_name,
            shares,
            prev_close,
            close,
            traded,
        ) in rows
    )
    return SectorSnapshot(snapshot_date=snapshot_date, session=session, members=members)
