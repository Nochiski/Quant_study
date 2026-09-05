"""S21 축소 — 절단본 체인 위에서 워크벤치 `equity_duckdb` 어댑터·컨테이너·MVP-B 백테스트 왕복
(EQUITY_WORKFLOW §3-5 · DESIGN §7 · §10 P25).

체인 9테이블(`trading_calendar`→`security`→`security_span`→`corp_ticker`→`price_daily`→`corp_event`→
`adj_factor`→`universe_daily`→`universe_policy`) + `catalog.publish` 를 스크래치에 짓고, 같은
모노레포의 `backend/src`(`contract.default_engine_src()`) 에서 워크벤치 어댑터를 import 한다 —
numpy·pyarrow·duckdb 가 필요하다(`uv run --with duckdb --with pyarrow --with numpy`; 컨테이너·
백테스트 2건은 ruamel.yaml 까지 — `uv run --project backend pytest …`).

손계산 기대값(절단본 원자료 `stg_price_daily`·`stg_listing_daily`):
  005930 2018-04-27 종가 2,650,000(거래) · 04-30~05-03 기준가 2,650,000(거래량 0) · 05-04 51,900
  50:1 분할 apply_date 05-04 → as_of ≥ 05-04 에서 05-03 adj_close 53,000, 05-04 51,900
  list_shrs 05-03 128,386,494 → 05-04 6,419,324,700
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
)
from equity.baseline import Baseline, load

pytest.importorskip("numpy", reason="strategy_workbench 부팅이 backtest_engine(numpy)을 요구한다")

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
CHAIN = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
         rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT, rules_s06.ADJ_FACTOR,
         rules_s03.UNIVERSE_DAILY, rules_s03.UNIVERSE_POLICY)
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FIELDS = ("price.close", "price.adj_close", "price.market_cap")
SPLIT = date(2018, 5, 4)
HALT_LAST = date(2018, 5, 3)
SHARES_BEFORE, SHARES_AFTER = 128_386_494, 6_419_324_700


def seed() -> Baseline:
    """S01·S02·S03·S05·S06 seed 를 테이블 단위로 병합(S07 테스트와 같은 규약)."""
    merged: dict[str, dict[str, object]] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s03.BASELINE_SEED, rules_s05.BASELINE_SEED, rules_s06.BASELINE_SEED):
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
    assert {p.field_id for p in adapter.list_fields()} == set(FIELDS)


def test_005930_2018_분할_전후_원주가_조정가_시총(adapter) -> None:
    r = _raw(adapter, date(2018, 4, 2), date(2018, 5, 31))
    assert r.ok, r.detail
    assert _value(r, date(2018, 4, 27), "005930:1", "price.close") == 2_650_000
    assert _value(r, HALT_LAST, "005930:1", "price.close") == 2_650_000   # 기준가 행도 실린다
    assert _value(r, SPLIT, "005930:1", "price.close") == 51_900
    assert _value(r, HALT_LAST, "005930:1", "price.adj_close") == pytest.approx(53_000)
    assert _value(r, SPLIT, "005930:1", "price.adj_close") == pytest.approx(51_900)
    assert _value(r, HALT_LAST, "005930:1", "price.market_cap") == 2_650_000 * SHARES_BEFORE
    assert _value(r, SPLIT, "005930:1", "price.market_cap") == 51_900 * SHARES_AFTER
    # 세 필드 모두 공개일 = 세션(랙 0)
    o = next(o for o in r.observations if (o.as_of, o.security_id) == (SPLIT, "005930:1"))
    assert {f.available_date for f in o.fields} == {SPLIT}


def test_krx_common_stock_은_정책표대로_ETF_우선주를_뺀다(adapter) -> None:
    r = _raw(adapter, date(2018, 5, 1), date(2018, 5, 31))
    ids = {o.security_id for o in r.observations}
    assert "005930:1" in ids and "000660:1" in ids
    assert not {i for i in ids if i.startswith(("069500", "005935", "003545", "003547"))}
    everyone = {o.security_id for o in _raw(adapter, date(2018, 5, 1), date(2018, 5, 31),
                                            universe="krx.all").observations}
    assert {"069500:1", "005935:1"} <= everyone


def test_krx_liquid_은_정책표대로_같은날_상위_비율만_남긴다(adapter) -> None:
    """S03B-2 — `krx.liquid` 가 정책표 5행(investable 4 + `adv20_rank_pct >= 0.5`)으로 풀린다.
    2018-05 모집단 5(003540·161890·000030·000660·005930, 손계산 순위 0.2·0.4·0.6·0.8·1.0) 중
    상위 3 만 행 집합에 든다."""
    r = _raw(adapter, date(2018, 5, 1), date(2018, 5, 31), universe="krx.liquid")
    assert r.ok, r.detail
    ids = {o.security_id for o in r.observations}
    assert ids == {"000030:1", "000660:1", "005930:1"}
    common = {o.security_id
              for o in _raw(adapter, date(2018, 5, 1), date(2018, 5, 31)).observations}
    assert ids < common and {"003540:1", "161890:1"} <= common
    # 행 집합 안에서도 universe_member 는 그날 술어값 — 05-03 은 셋 다 true
    members = {o.security_id for o in r.observations
               if o.as_of == HALT_LAST and o.universe_member}
    assert members == ids


def test_미지원_필드는_unavailable_이고_mock_대체가_없다(adapter) -> None:
    from strategy_workbench.domain.equity.facade.research_data import DataLoadStatus
    r = _raw(adapter, date(2018, 5, 1), date(2018, 5, 31), fields=("financial.book_equity",))
    assert r.status is DataLoadStatus.INVALID_QUERY and "unavailable" in str(r.detail)


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
