"""S21 본판 — 절단본 체인 위에서 워크벤치 `equity_duckdb` 어댑터·컨테이너·MVP-B 백테스트 왕복
(EQUITY_WORKFLOW §3-5 · DESIGN §7 · §10 P25/P40).

체인 **19테이블**(`trading_calendar`→`corp`→`security`→`security_span`→`corp_ticker`→`price_daily`→
`corp_event`→`adj_factor`→`universe_daily`→`universe_policy`→`disclosure_version`→`fin_std`→
`holder_daily`→`dividend_event`→`consensus_daily`→`opinion_daily`→**`flow_daily`→`short_daily`→
`credit_daily`**) + `catalog.publish`(매크로 8)를 스크래치에 짓고, 같은 모노레포의
`backend/src`(`contract.default_engine_src()`) 에서 워크벤치 어댑터를 import 한다 —
numpy·pyarrow·duckdb 가 필요하다(`uv run --with duckdb --with pyarrow --with numpy`;
컨테이너·백테스트 2건은 ruamel.yaml 까지 — `uv run --project backend pytest …`).

손계산 기대값(절단본 원자료):
  가격(`stg_price_daily`·`stg_listing_daily`) — 005930 2018-04-27 종가 2,650,000(거래) ·
  04-30~05-03 기준가 2,650,000(거래량 0) · 05-04 시가 53,000 종가 51,900 거래량 39,565,391
  거래대금 2,078,017,927,600. 50:1 분할 apply_date 05-04 → **전방 조정**(`v_adj_price_fwd`):
  05-03 adj_close 2,650,000(원주가 그대로), 05-04 51,900 × 50 = 2,595,000 — 창·as_of 에 무관한
  (security, date) 값. list_shrs 05-03 128,386,494 → 05-04 6,419,324,700.
  재무(`stg_fin`) — 삼성전자 2018 사업보고서(접수 2019-04-01) 매출 243,771,415,000,000 ·
  매출총이익 111,377,004,000,000 · 자산총계 339,357,244,000,000. 그 전 세션(03-29)에는 아직
  2018 3분기(접수 2018-11-14, 매출 65,459,993,000,000)가 최신이다 = PIT.
  배당(`stg_dividend`) — 00126380 2017 사업연도 보통주 DPS 42,500(접수 2018-04-02). 종류 축을
  접으므로 우선주 티커 005935 도 같은 값을 받는다(FIELD_MAP §2 부분 판정 ②).
  컨센서스(`stg_v3_revision_daily`) — 005930 2026-08 관측점 EPS 47,929원(target_period 202612,
  접수 2026-08-04). v3 판본이라 est_min·est_max 가 없어 `consensus.eps_dispersion` 은 결측이다.
  의견(`stg_v3_analyst_opinions`) — 005930 2026-08-20 목표주가 491,875 · 의견 4.04 · 24기관.
  임원지분(`stg_holder_elestock`) — 00126380 2026-08-20 접수 2건 합 −410주.
  수급(`stg_flow_daily_kiwoom`) — 005930 2018-05-04 외국인 순매수 −53,845,000,000원(원장 원값,
  stage 가 백만원 ×1e6 을 이미 했다) · 개인 655,449,000,000 · 기관 −591,578,000,000.
  공매도(`stg_short_daily_kiwoom`) — 005930 2018-05-04 거래대금 103,425,481,000원
  (= `shrts_trde_prica_krw`, 거래량 `shrts_qty_shr` 1,964,027주).
  신용(`stg_credit_daily`) — 005930 2018-05-04 융자잔고 8,359,855주.
"""
from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path

import pytest
from equity import (
    build,
    catalog,
    contract,
    rules_s01,
    rules_s02,
    rules_s03,
    rules_s04,
    rules_s05,
    rules_s06,
    rules_s08,
    rules_s09,
    rules_s10,
    rules_s11,
    rules_s12,
    rules_s15,
    rules_s16,
    rules_s17,
    rules_s18,
)
from equity.baseline import Baseline, load

pytest.importorskip("numpy", reason="strategy_workbench 부팅이 backtest_engine(numpy)을 요구한다")

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
CHAIN = (rules_s02.TRADING_CALENDAR, rules_s01.CORP, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
         rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT, rules_s06.ADJ_FACTOR,
         rules_s03.UNIVERSE_DAILY, rules_s03.UNIVERSE_POLICY,
         rules_s11.DISCLOSURE_VERSION, rules_s12.FIN_STD,
         rules_s15.HOLDER_DAILY, rules_s16.DIVIDEND_EVENT,
         rules_s17.CONSENSUS_DAILY, rules_s18.OPINION_DAILY,
         rules_s08.FLOW_DAILY, rules_s09.SHORT_DAILY, rules_s10.CREDIT_DAILY)
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FIELDS = ("price.close", "price.adj_close", "price.market_cap")
# 체인이 다 서면 어댑터가 내는 field_id — FIELD_MAP §2 의 42 중 29 + 내부 스코프 price.adj_close.
# 남는 13 은 원천·컬럼이 없거나(11) 굽지 않기로 한 것(2)이라 여기서도 unavailable 이다.
ALL_FIELDS = (
    "price.close", "price.open", "price.volume", "price.market_cap",
    "price.shares_outstanding", "price.trading_value", "price.adj_close",
    "financial.revenue", "financial.gross_profit", "financial.operating_income",
    "financial.net_income", "financial.operating_cash_flow", "financial.total_assets",
    "financial.total_liabilities", "financial.book_equity",
    "consensus.forward_eps", "consensus.forward_sales", "consensus.eps_dispersion",
    "consensus.target_price", "consensus.recommendation", "consensus.analyst_count",
    "flow.foreign_net_buy", "flow.institution_net_buy", "flow.retail_net_buy",
    "short.short_sale_value", "short.borrowed_quantity", "credit.margin_balance",
    "event.dividend_per_share", "event.buyback_amount", "event.insider_net_buy",
)
SPLIT = date(2018, 5, 4)
HALT_LAST = date(2018, 5, 3)
SHARES_BEFORE, SHARES_AFTER = 128_386_494, 6_419_324_700
BACKFILL_END = date(2026, 8, 20)


def seed() -> Baseline:
    """S01~S18 seed 를 테이블 단위로 병합(S07 테스트와 같은 규약)."""
    merged: dict[str, dict[str, object]] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s03.BASELINE_SEED, rules_s04.BASELINE_SEED, rules_s05.BASELINE_SEED,
              rules_s06.BASELINE_SEED, rules_s08.BASELINE_SEED, rules_s09.BASELINE_SEED,
              rules_s10.BASELINE_SEED, rules_s11.BASELINE_SEED, rules_s12.BASELINE_SEED,
              rules_s15.BASELINE_SEED, rules_s16.BASELINE_SEED, rules_s17.BASELINE_SEED,
              rules_s18.BASELINE_SEED):
        for k, v in load(p).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("s21") / "equity"
    bl = seed()
    for t in CHAIN:
        r = build.build_table(t, STAGE_SLICE, root, bl, build_id=f"b_{t.name}")
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    p = catalog.publish(root, bl)
    assert p.ok, [(g.name, g.status.value, g.detail) for g in p.gates]
    return root


@pytest.fixture(scope="module")
def workbench():
    src = contract.default_engine_src()
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    return importlib.import_module(
        "strategy_workbench.adapters.outbound.equity_duckdb.facade.provider")


@pytest.fixture(scope="module")
def adapter(built: Path, workbench):
    return workbench.EquityDuckdbAdapter(built)


def _raw(adapter, start: date, end: date, universe: str = "krx.common-stock",
         fields: tuple[str, ...] = FIELDS):
    from strategy_workbench.application.portfolio_design.facade.ports import RawObservationQuery
    return adapter.load_raw_observations(RawObservationQuery("KRX", universe, start, end, fields))


def _value(result, as_of: date, security_id: str, field_id: str) -> object:
    o = next(o for o in result.observations if (o.as_of, o.security_id) == (as_of, security_id))
    return next(f.value for f in o.fields if f.field_id == field_id)


# ── 어댑터 ────────────────────────────────────────────────────────────────────

def test_snapshot_id_는_카탈로그_meta_와_같다(adapter, built: Path) -> None:
    import json
    meta = json.loads((built / catalog.META_NAME).read_text(encoding="utf-8"))
    assert adapter.snapshot().snapshot_id == meta["snapshot_id"]
    assert {p.field_id for p in adapter.list_fields()} == set(ALL_FIELDS)
    # 카탈로그는 S21 본판이 요구하는 매크로 둘을 새로 싣는다(v_consensus·v_fin_latest)
    names = {sig.split("(", 1)[0] for sig in meta["macros"]}
    assert {"v_adj_price_fwd", "v_consensus", "v_fin_latest"} <= names


def test_005930_2018_분할_전후_원주가_조정가_시총(adapter) -> None:
    r = _raw(adapter, date(2018, 4, 2), date(2018, 5, 31))
    assert r.ok, r.detail
    assert _value(r, date(2018, 4, 27), "005930:1", "price.close") == 2_650_000
    assert _value(r, HALT_LAST, "005930:1", "price.close") == 2_650_000   # 기준가 행도 실린다
    assert _value(r, SPLIT, "005930:1", "price.close") == 51_900
    assert _value(r, HALT_LAST, "005930:1", "price.adj_close") == pytest.approx(2_650_000)
    assert _value(r, SPLIT, "005930:1", "price.adj_close") == pytest.approx(51_900 * 50)
    assert _value(r, HALT_LAST, "005930:1", "price.market_cap") == 2_650_000 * SHARES_BEFORE
    assert _value(r, SPLIT, "005930:1", "price.market_cap") == 51_900 * SHARES_AFTER
    # 세 필드 모두 공개일 = 세션(랙 0)
    o = next(o for o in r.observations if (o.as_of, o.security_id) == (SPLIT, "005930:1"))
    assert {f.available_date for f in o.fields} == {SPLIT}


def test_adj_close_는_창을_바꿔도_같은_셀이_같다(adapter) -> None:
    """부정 검사 (c) — S21 에이전트가 지적한 모순의 회귀: base = 창 end 였을 때는 분할 전에 끝나는
    창에서 05-03 이 2,650,000, 분할을 지나는 창에서 53,000 으로 갈렸다. 전방 조정은 창 독립."""
    short = _raw(adapter, date(2018, 4, 2), HALT_LAST)            # 분할 전에 끝나는 창
    long = _raw(adapter, date(2018, 4, 2), date(2018, 5, 31))      # 분할을 지나는 창
    assert short.ok and long.ok
    for d, close in ((date(2018, 4, 27), 2_650_000), (HALT_LAST, 2_650_000)):
        assert (_value(short, d, "005930:1", "price.adj_close")
                == _value(long, d, "005930:1", "price.adj_close") == pytest.approx(close))
    # 분할일 이후 창을 다르게 잡아도 같다
    later = _raw(adapter, SPLIT, date(2018, 12, 28))
    assert (_value(later, SPLIT, "005930:1", "price.adj_close")
            == _value(long, SPLIT, "005930:1", "price.adj_close") == pytest.approx(2_595_000))
    # 우선주도 같은 50:1 — 05-03 2,125,000 그대로
    assert _value(_raw(adapter, date(2018, 5, 2), date(2018, 5, 31), universe="krx.all"),
                  HALT_LAST, "005935:1", "price.adj_close") == pytest.approx(2_125_000)


def test_krx_common_stock_은_정책표대로_ETF_우선주를_뺀다(adapter) -> None:
    r = _raw(adapter, date(2018, 5, 1), date(2018, 5, 31))
    ids = {o.security_id for o in r.observations}
    assert "005930:1" in ids and "000660:1" in ids
    assert not {i for i in ids if i.startswith(("069500", "005935", "003545", "003547"))}
    everyone = {o.security_id for o in _raw(adapter, date(2018, 5, 1), date(2018, 5, 31),
                                            universe="krx.all").observations}
    assert {"069500:1", "005935:1"} <= everyone


def test_krx_liquid_은_정책표대로_같은날_상위_비율만_남긴다(adapter) -> None:
    """S03B-2 + **S03C** — `krx.liquid` 가 정책표 6행(investable 4 + `no_trade_reason <> 'illiquid'`
    + `adv20_rank_pct >= 0.5`)으로 풀린다. 05-03 은 005930 이 분할 창 무거래(corp_action_window)로
    suspended 라 모집단이 4(003540 0.25 · 161890 0.5 · 000030 0.75 · 000660 1.0)이고 상위 3 =
    {161890, 000030, 000660}. 005930 은 05-04 재개 뒤 다시 들어와 창 전체의 행 집합은 4종목이다."""
    r = _raw(adapter, date(2018, 5, 1), date(2018, 5, 31), universe="krx.liquid")
    assert r.ok, r.detail
    ids = {o.security_id for o in r.observations}
    assert ids == {"000030:1", "000660:1", "005930:1", "161890:1"}
    common = {o.security_id
              for o in _raw(adapter, date(2018, 5, 1), date(2018, 5, 31)).observations}
    assert ids < common and "003540:1" in common
    # 행 집합 안에서도 universe_member 는 그날 술어값 — 05-03 은 005930 만 false(정지)
    members = {o.security_id for o in r.observations
               if o.as_of == HALT_LAST and o.universe_member}
    assert members == ids - {"005930:1"}
    # 분할 적용일(05-04) 에는 거래가 재개돼 005930 이 다시 모집단(5종목)에 들고, 그만큼 161890 의
    # 순위가 0.4 로 내려가 그날은 빠진다 — 행은 남고 universe_member 만 갈린다(D-001)
    assert {o.security_id for o in r.observations
            if o.as_of == SPLIT and o.universe_member} == ids - {"161890:1"}


def test_미지원_필드는_unavailable_이고_mock_대체가_없다(adapter) -> None:
    from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
    r = _raw(adapter, date(2018, 5, 1), date(2018, 5, 31), fields=("classification.sector",))
    assert r.status is DataLoadStatus.INVALID_QUERY and "unavailable" in str(r.detail)
    assert "현재값 라벨" in str(r.detail)
    # 격자 3테이블이 다 서도 남는 미지원 — 컬럼 부재(S08-2)와 원천 축 부재(S10 판정)를 구분한다
    ownership = _raw(adapter, date(2018, 5, 1), date(2018, 5, 31),
                     fields=("flow.foreign_ownership",))
    assert ownership.status is DataLoadStatus.INVALID_QUERY
    assert "S08-2" in str(ownership.detail)
    net_buy = _raw(adapter, date(2018, 5, 1), date(2018, 5, 31), fields=("credit.net_buy",))
    assert net_buy.status is DataLoadStatus.INVALID_QUERY
    assert "순매수 축이 없다" in str(net_buy.detail)


# ── S21 본판: 필드별 1셀 손검산 ───────────────────────────────────────────────

def _cell(result, as_of: date, security_id: str, field_id: str):
    o = next(o for o in result.observations if (o.as_of, o.security_id) == (as_of, security_id))
    return next(f for f in o.fields if f.field_id == field_id)


def test_2018_05_04_가격_6필드는_KRX_원장_행_그대로다(adapter) -> None:
    """`price.*` 6 — 분할 적용일의 원장 한 행. 조정은 price.adj_close 축에서만 한다."""
    r = _raw(adapter, date(2018, 5, 2), date(2018, 5, 31), fields=ALL_FIELDS)
    assert _value(r, SPLIT, "005930:1", "price.open") == 53_000
    assert _value(r, SPLIT, "005930:1", "price.close") == 51_900
    assert _value(r, SPLIT, "005930:1", "price.volume") == 39_565_391       # 원거래량(무조정)
    assert _value(r, SPLIT, "005930:1", "price.trading_value") == 2_078_017_927_600
    assert _value(r, SPLIT, "005930:1", "price.shares_outstanding") == SHARES_AFTER
    assert _value(r, HALT_LAST, "005930:1", "price.shares_outstanding") == SHARES_BEFORE
    # 정지일(기준가 행)은 시가가 NULL 이라 MISSING 이고 거래량 0 은 실제 관측이다
    from strategy_workbench.domain.equity.facade.research_data import CellKind
    halted = _cell(r, HALT_LAST, "005930:1", "price.open")
    assert (halted.value, halted.kind) == (None, CellKind.MISSING)
    assert _value(r, HALT_LAST, "005930:1", "price.volume") == 0


def test_삼성전자_2018_사업보고서_재무는_접수일부터_보인다(adapter) -> None:
    """`financial.*` — 법인 축 fin_std 를 corp_ticker 로 편 값. 접수 전 세션엔 3분기가 최신이다."""
    r = _raw(adapter, date(2019, 3, 29), date(2019, 4, 5), universe="krx.all",
             fields=ALL_FIELDS)
    filed = _cell(r, date(2019, 4, 1), "005930:1", "financial.revenue")
    assert filed.value == 243_771_415_000_000
    assert filed.available_date == date(2019, 4, 1)
    assert _value(r, date(2019, 4, 1), "005930:1",
                  "financial.gross_profit") == 111_377_004_000_000
    assert _value(r, date(2019, 4, 1), "005930:1",
                  "financial.total_assets") == 339_357_244_000_000
    # PIT — 03-29 에는 사업보고서가 아직 접수되지 않아 2018 3분기(2018-11-14 접수)가 최신이다
    before = _cell(r, date(2019, 3, 29), "005930:1", "financial.revenue")
    assert (before.value, before.available_date) == (65_459_993_000_000, date(2018, 11, 14))
    # 같은 법인의 우선주 티커도 같은 값을 받는다(법인 축 전개 규약)
    assert _value(r, date(2019, 4, 1), "005935:1", "financial.revenue") == 243_771_415_000_000


def test_삼성전자_2017_배당은_종류_축을_접어_전_종류주에_같은_값이다(adapter) -> None:
    r = _raw(adapter, date(2018, 3, 29), date(2018, 4, 5), universe="krx.all",
             fields=ALL_FIELDS)
    cell = _cell(r, date(2018, 4, 2), "005930:1", "event.dividend_per_share")
    assert (cell.value, cell.available_date) == (42_500.0, date(2018, 4, 2))
    assert _value(r, date(2018, 4, 2), "005935:1", "event.dividend_per_share") == 42_500.0
    # 접수 전 세션은 직전 사업연도(2016) 값이다
    earlier = _cell(r, date(2018, 3, 30), "005930:1", "event.dividend_per_share")
    assert (earlier.value, earlier.available_date) == (28_500.0, date(2017, 3, 31))


def test_2026_08_컨센서스_의견_임원지분_1셀(adapter) -> None:
    """`consensus.*`·`event.insider_net_buy` — 절단본의 마지막 세션에서 잰다."""
    from strategy_workbench.domain.equity.facade.research_data import CellKind
    r = _raw(adapter, date(2026, 8, 18), BACKFILL_END, fields=ALL_FIELDS)
    eps = _cell(r, BACKFILL_END, "005930:1", "consensus.forward_eps")
    assert (eps.value, eps.available_date) == (47_929.0, date(2026, 8, 4))
    # v3 판본은 est_min·est_max 가 없어 범위가 결측이다(FIELD_MAP §2 부분 판정)
    dispersion = _cell(r, BACKFILL_END, "005930:1", "consensus.eps_dispersion")
    assert (dispersion.value, dispersion.kind) == (None, CellKind.MISSING)
    assert _value(r, BACKFILL_END, "005930:1", "consensus.target_price") == 491_875.0
    assert _value(r, BACKFILL_END, "005930:1", "consensus.recommendation") == 4.04
    assert _value(r, BACKFILL_END, "005930:1", "consensus.analyst_count") == 24.0
    # 같은 접수일의 임원 2건 합 (−410주). majorstock 축은 섞이지 않는다
    insider = _cell(r, BACKFILL_END, "005930:1", "event.insider_net_buy")
    assert (insider.value, insider.available_date) == (-410.0, BACKFILL_END)
    # 절단본 corp_event 에 tsstk_aq 행이 없어 자사주 필드는 목록엔 있어도 셀이 없다(합성 금지)
    o = next(o for o in r.observations
             if (o.as_of, o.security_id) == (BACKFILL_END, "005930:1"))
    assert "event.buyback_amount" not in {f.field_id for f in o.fields}


def test_2018_05_04_격자_3테이블은_stage_원장_값_그대로다(adapter) -> None:
    """`flow.*`·`short.*`·`credit.*` — 절단본 원장 한 행씩 손검산(어댑터가 다시 스케일하지 않는다).

    기대값 출처는 stage 절단본이다: `stg_flow_daily_kiwoom`(005930 2018-05-04 외국인
    −53,845,000,000 = 원장 원값, stage 가 백만원 ×1e6 을 이미 마쳤다) · `stg_short_daily_kiwoom`
    (`shrts_trde_prica_krw` 103,425,481,000 = 천원 ×1e3 완료) · `stg_credit_daily`
    (`whol_loan_rmnd_stcn_shr` 8,359,855).
    """
    from strategy_workbench.domain.equity.facade.research_data import CellKind
    r = _raw(adapter, date(2018, 5, 2), date(2018, 5, 31), fields=ALL_FIELDS)
    assert r.ok, r.detail
    # ① 수급 — 절단본은 겹침 0 이라 키움 행 하나가 그대로 나간다(12주체 합 0 항등식의 그 행)
    foreign = _cell(r, SPLIT, "005930:1", "flow.foreign_net_buy")
    assert (foreign.value, foreign.available_date, foreign.kind) == (
        -53_845_000_000.0, SPLIT, CellKind.OBSERVED)
    assert _value(r, SPLIT, "005930:1", "flow.retail_net_buy") == 655_449_000_000.0
    assert _value(r, SPLIT, "005930:1", "flow.institution_net_buy") == -591_578_000_000.0
    # ② 공매도 거래대금 — 키움 축 고정(같은 셀의 KIS 축은 not_collected 라 섞이면 결측이 된다)
    short = _cell(r, SPLIT, "005930:1", "short.short_sale_value")
    assert (short.value, short.kind) == (103_425_481_000.0, CellKind.OBSERVED)
    # 대차는 KIS 축뿐이고 이 셀은 수집 로그가 덮지 않는다 — 결측이되 '안 물어봤다' 로 남는다
    lending = _cell(r, SPLIT, "005930:1", "short.borrowed_quantity")
    assert (lending.value, lending.kind) == (None, CellKind.NOT_COLLECTED)
    # ③ 신용융자 잔고(주식수) — 금액축은 단위 미상이라 내지 않는다
    credit = _cell(r, SPLIT, "005930:1", "credit.margin_balance")
    assert (credit.value, credit.available_date, credit.kind) == (
        8_359_855.0, SPLIT, CellKind.OBSERVED)
    # ④ 백필 끝 세션의 신용 셀은 `src_omitted`(유닛 창 안인데 원장 행이 없다) — 0 이 아니라 NULL
    #    이고 SOURCE_OMITTED_ZERO 대신 MISSING 으로 접힌다(_specs 의 사유 문장 참조)
    last = _raw(adapter, date(2026, 8, 18), BACKFILL_END, fields=ALL_FIELDS)
    omitted = _cell(last, BACKFILL_END, "005930:1", "credit.margin_balance")
    assert (omitted.value, omitted.kind) == (None, CellKind.MISSING)
    assert omitted.available_date == BACKFILL_END


def test_모든_셀이_공개일_이후에만_보인다(adapter) -> None:
    """PIT 전수 — 30필드 × 절단본 두 창에서 available_date ≤ as_of 위반 0."""
    for start, end in ((date(2018, 4, 2), date(2018, 5, 31)),
                       (date(2026, 8, 3), BACKFILL_END)):
        r = _raw(adapter, start, end, universe="krx.all", fields=ALL_FIELDS)
        assert r.ok, r.detail
        for o in r.observations:
            for f in o.fields:
                assert f.available_date <= o.as_of, (o.as_of, o.security_id, f)


# ── 컨테이너 부팅 · MVP-B 백테스트 ───────────────────────────────────────────
# `build_container` 는 backend 의존성(ruamel.yaml — 문서 코덱)을 끌어온다. 어댑터만 쓰는 위 테스트와
# 달리 backend venv(`uv run --project backend …`) 또는 `--with ruamel.yaml` 이 필요하다.

BACKEND_DEP_REASON = ("build_container 가 backend 의존성 ruamel.yaml 을 요구한다 — "
                      "`uv run --project backend pytest …` 또는 `--with ruamel.yaml`")


def test_컨테이너가_duckdb_어댑터로_뜬다(built: Path, workbench, tmp_path: Path) -> None:
    pytest.importorskip("ruamel.yaml", reason=BACKEND_DEP_REASON)
    from strategy_workbench.bootstrap.facade.container import build_container
    c = build_container(equity_adapter="duckdb", equity_root=built, artifact_root=tmp_path / "r")
    assert isinstance(c.equity_data, workbench.EquityDuckdbAdapter)
    assert c.equity_workspace.catalog().snapshot.schema_version == "equity-v1.2"


def test_MVP_B_모멘텀_월간_백테스트가_절단본에서_완주한다(built: Path, tmp_path: Path) -> None:
    """`scripts/run_mvp_backtest.py` 를 in-process 로 — 2018 한 해(분할 포함), adj_close 축."""
    pytest.importorskip("ruamel.yaml", reason=BACKEND_DEP_REASON)
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    mvp = importlib.import_module("run_mvp_backtest")
    mvp.load_backend(contract.default_engine_src())
    s = mvp.run(built, date(2018, 1, 2), date(2018, 12, 28), "krx.common-stock",
                artifact_root=tmp_path / "runs")
    assert s.ok, s.error
    assert s.n_rebalances == 11 and s.n_rebalances_with_positions >= 1
    assert s.n_securities >= 5 and s.total_return is not None
    assert s.data_snapshot_id == catalog.snapshot_id(catalog.table_builds(built))
    assert s.artifact_uri is not None
