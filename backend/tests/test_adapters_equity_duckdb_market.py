"""`MarketDataPort`(홈 시장 블록): duckdb 어댑터가 원장 행을 그대로 나르는가.

- 수급은 코스피·코스닥 보통 격자만 더한다: ETF·우선주는 빠지고, 한 셀에 원천이
  둘이면 백테스트 필드와 같은 규칙(키움 우선)으로 한 행만 센다. 값이 없는 셀은 0으로 치지 않고
  측정 수에서 뺀다. 읽다 실패하면 `MarketDataUnreadableError` 다.
- 섹터는 장 이전 최신 스냅샷과 그 장 가격(전날 종가 = `base_price_krw`)을 붙인다.
- 표가 없으면 빈 결과가 아니라 `MarketTableMissingError` 다.
"""

from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
import pyarrow as pa

from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import EquityDuckdbAdapter
from strategy_workbench.application.market_overview.facade.overview import (
    MarketDataUnreadableError,
    MarketTableMissingError,
)
from strategy_workbench.domain.market.facade.market import FlowMarket, Investor
from tests.equity_fixture import (
    WB_SEC_TYPES,
    WB_SESSIONS,
    WB_SPANS,
    build_workbench_root,
    wb_close,
    write_equity_table,
)

EOK = 100_000_000
LAST, PREV = WB_SESSIONS[-1], WB_SESSIONS[-2]
MARKETS = {"036220": "KOSDAQ"}
MKTCAP = {"005930": 600, "000660": 300, "035420": 100, "036220": 40, "005935": 50, "069500": 10}
FLOW_COLUMNS = (
    "ind_invsr_krw",
    "frgnr_invsr_krw",
    "orgn_krw",
    "etc_corp_krw",
    "natfor_krw",
    "fnnc_invt_krw",
    "penfnd_etc_krw",
    "invtrt_krw",
    "samo_fund_krw",
    "insrnc_krw",
    "bank_krw",
    "etc_fnnc_krw",
    "natn_krw",
)


def _universe(root: Path) -> None:
    rows = [
        (ticker, session)
        for ticker, _seq, first, last, _reason in WB_SPANS
        for session in WB_SESSIONS
        if first <= session <= last
    ]
    write_equity_table(
        root,
        "universe_daily",
        pa.table(
            {
                "date": pa.array([r[1] for r in rows], type=pa.date32()),
                "ticker": pa.array([r[0] for r in rows]),
                "status": pa.array(["listed"] * len(rows)),
                "sec_type": pa.array([WB_SEC_TYPES[r[0]] for r in rows]),
                "market": pa.array([MARKETS.get(r[0], "KOSPI") for r in rows]),
                "mktcap_krw": pa.array(
                    [Decimal(MKTCAP[r[0]]) for r in rows], type=pa.decimal128(18, 0)
                ),
            }
        ),
        year_column="date",
    )


def _flow(root: Path) -> None:
    # 실제 원장 본표처럼 `reject_reason` 칸이 없다 — 격리 행은 빌드가 `_reject/` 에 따로 쓴다.
    # (ticker, date, src, 개인, 외국인) — 나머지 주체는 기관 = 1억, 국가 = None
    rows: list[tuple[str, date, str, int | None, int | None]] = [
        ("005930", PREV, "kiwoom", 5 * EOK, -7 * EOK),
        ("005930", LAST, "kiwoom", 2 * EOK, -3 * EOK),
        # 같은 셀의 KIS 행 — 더하면 이중 집계다
        ("005930", LAST, "kis", 90 * EOK, 90 * EOK),
        # 미측정 셀: 격자 수에는 들고 측정 수·합에는 들지 않는다. 같은 셀의 KIS 값이 있어도 백테스트
        # 필드(`flow.*`)처럼 키움 행을 고른다 — 홈과 백테스트가 같은 셀을 다르게 읽지 않게
        ("000660", LAST, "kiwoom", None, None),
        ("000660", LAST, "kis", 8 * EOK, 8 * EOK),
        # 우선주·ETF 는 수급 합에서 뺀다
        ("005935", LAST, "kiwoom", 40 * EOK, 40 * EOK),
        ("069500", LAST, "kiwoom", 30 * EOK, 30 * EOK),
        ("036220", LAST, "kiwoom", 1 * EOK, -1 * EOK),
    ]
    money = pa.decimal128(15, 0)

    def column(values: list[int | None]) -> pa.Array:
        return pa.array([None if v is None else Decimal(v) for v in values], type=money)

    measured = [r[3] is not None for r in rows]
    columns: dict[str, pa.Array] = {
        "date": pa.array([r[1] for r in rows], type=pa.date32()),
        "ticker": pa.array([r[0] for r in rows]),
        "src": pa.array([r[2] for r in rows]),
        "ind_invsr_krw": column([r[3] for r in rows]),
        "frgnr_invsr_krw": column([r[4] for r in rows]),
    }
    for name in FLOW_COLUMNS[2:]:
        columns[name] = column([None if name == "natn_krw" or not m else EOK for m in measured])
    write_equity_table(root, "flow_daily", pa.table(columns), year_column="date")


def _sector(root: Path) -> None:
    rows = [
        ("005930", WB_SESSIONS[-6], "G45", "IT", "G4530", "WICS 반도체와반도체장비", 100),
        ("005930", PREV, "G45", "IT", "G4530", "WICS 반도체와반도체장비", 1_000),
        ("000660", PREV, "G45", "IT", "G4530", "WICS 반도체와반도체장비", 500),
        ("036220", PREV, "G15", "소재", "G1510", "WICS 소재", 10),
    ]
    write_equity_table(
        root,
        "sector_snapshot",
        pa.table(
            {
                "ticker": pa.array([r[0] for r in rows]),
                "snapshot_date": pa.array([r[1] for r in rows], type=pa.date32()),
                "wics_l1_cd": pa.array([r[2] for r in rows]),
                "wics_l1_nm": pa.array([r[3] for r in rows]),
                "wics_l2_cd": pa.array([r[4] for r in rows]),
                "wics_l2_nm": pa.array([r[5] for r in rows]),
                "float_shares_shr": pa.array([r[6] for r in rows], type=pa.int64()),
            }
        ),
    )


def _index(root: Path) -> None:
    sessions = list(WB_SESSIONS)
    write_equity_table(
        root,
        "index_daily",
        pa.table(
            {
                "index_name": pa.array(["코스피"] * len(sessions) + ["코스닥"]),
                "date": pa.array([*sessions, LAST], type=pa.date32()),
                "close_idx": pa.array(
                    [*(2_000.0 + i for i in range(len(sessions) - 1)), None, 800.0]
                ),
                "change_idx": pa.array([1.5] * len(sessions) + [None]),
                "fluc_pct": pa.array([0.1] * len(sessions) + [None]),
                "value_krw": pa.array(
                    [Decimal(10 * EOK)] * len(sessions) + [None], type=pa.decimal128(16, 0)
                ),
            }
        ),
        year_column="date",
    )


def build_market_root(path: Path) -> Path:
    """손 픽스처 워크벤치 루트에 홈 시장 블록이 읽는 네 표를 더한다(HTTP 통합 테스트도 쓴다)."""
    root = build_workbench_root(path, catalog=False)
    _universe(root)
    _flow(root)
    _sector(root)
    _index(root)
    return root


@pytest.fixture
def market_root(tmp_path: Path) -> Path:
    return build_market_root(tmp_path / "root")


def test_latest_session_is_the_last_trading_session(market_root: Path) -> None:
    assert EquityDuckdbAdapter(market_root).latest_session() == LAST


def test_index_bars_read_the_recent_sessions_and_skip_rows_without_a_close(
    market_root: Path,
) -> None:
    bars = EquityDuckdbAdapter(market_root).index_bars("코스피", LAST, 3)
    # 마지막 세션은 종가가 없어 빠진다 — 3세션 창 안에서 남은 두 행
    assert [bar.session for bar in bars] == list(WB_SESSIONS[-3:-1])
    assert bars[-1].close == 2_000.0 + len(WB_SESSIONS) - 2
    assert (bars[-1].change, bars[-1].change_pct, bars[-1].value_krw) == (1.5, 0.1, 10 * EOK)


def test_flow_days_sum_measured_common_cells_once_per_market(market_root: Path) -> None:
    days = {
        (day.session, day.market): day
        for day in EquityDuckdbAdapter(market_root).flow_days(LAST, 2)
    }
    kospi = days[(LAST, FlowMarket.KOSPI)]
    # 격자 = 005930·000660(보통주). 우선주·ETF 는 없다. 000660 은 미측정
    assert (kospi.grid_securities, kospi.measured_securities) == (2, 1)
    assert (kospi.grid_mktcap_krw, kospi.measured_mktcap_krw) == (900, 600)
    # KIS 중복 행(90억)을 더하지 않는다
    assert kospi.amounts[Investor.INDIVIDUAL] == 2 * EOK
    assert kospi.amounts[Investor.FOREIGN] == -3 * EOK
    assert kospi.amounts[Investor.INSTITUTION] == EOK
    assert kospi.amounts[Investor.GOVERNMENT] == 0
    # 전날 장도 같은 격자로 더한다
    assert days[(PREV, FlowMarket.KOSPI)].amounts[Investor.INDIVIDUAL] == 5 * EOK
    kosdaq = days[(LAST, FlowMarket.KOSDAQ)]
    assert (kosdaq.measured_securities, kosdaq.amounts[Investor.FOREIGN]) == (1, -EOK)


def test_sector_members_join_the_latest_snapshot_with_that_session_prices(
    market_root: Path,
) -> None:
    snapshot = EquityDuckdbAdapter(market_root).sector_members(LAST)
    assert snapshot is not None
    assert (snapshot.snapshot_date, snapshot.session) == (PREV, LAST)
    members = {member.ticker: member for member in snapshot.members}
    assert set(members) == {"005930", "000660", "036220"}
    samsung = members["005930"]
    assert (samsung.name, samsung.l2_code, samsung.l2_name) == (
        "삼성전자",
        "G4530",
        "WICS 반도체와반도체장비",
    )
    assert samsung.float_shares == 1_000
    assert samsung.prev_close == wb_close("005930", PREV)
    assert samsung.close == wb_close("005930", LAST)
    assert samsung.traded
    # 이른 날짜에는 그 이전 스냅샷을 쓴다
    early = EquityDuckdbAdapter(market_root).sector_members(WB_SESSIONS[-4])
    assert early is not None and early.snapshot_date == WB_SESSIONS[-6]


def test_missing_tables_raise_instead_of_answering_empty(tmp_path: Path) -> None:
    adapter = EquityDuckdbAdapter(build_workbench_root(tmp_path / "plain", catalog=False))
    with pytest.raises(MarketTableMissingError) as index_error:
        adapter.index_bars("코스피", LAST, 3)
    assert index_error.value.table == "index_daily"
    with pytest.raises(MarketTableMissingError) as sector_error:
        adapter.sector_members(LAST)
    assert sector_error.value.table == "sector_snapshot"


def test_a_table_whose_build_files_vanished_raises_unreadable(market_root: Path) -> None:
    adapter = EquityDuckdbAdapter(market_root)
    for build in (market_root / "flow_daily").glob("v=*"):
        shutil.rmtree(build)  # 다른 받기의 gc 가 부팅 때 고정한 판을 지운 경우
    with pytest.raises(MarketDataUnreadableError) as error:
        adapter.flow_days(LAST, 2)
    assert error.value.table == "flow_daily"
