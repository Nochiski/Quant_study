"""S07 EG-C 소비자 계약 ①②③④⑤⑩ — 절단본 체인 위에서 워크벤치 어댑터 실행(GATES §1 EG-C·DESIGN §7).

상류 9테이블(`trading_calendar`·`security`·`security_span`·`corp_ticker`·`price_daily`·`corp_event`·
`adj_factor`·`universe_daily`·`universe_policy`)과 카탈로그(사건이 함께 읽는 `v_unfolded_event`)를
같은 equity_root 에 짓고 `contract.run` 을 돌린다. 제품이 쓰는 워크벤치 어댑터를 같은 모노레포의
`backend/src` 에서 facade 로 import 한다 — duckdb 만 있으면 되고 numpy·pyarrow 는 읽지 않는다.

절단본 손계산(원자료 직접 확인): 005930 2018-04-27 close 2,650,000(trade) → 04-30·05-02·05-03
reference(거래량 0, 분할 정지) → 05-04 51,900(trade, 50:1 분할 apply_date) · 036220 구간
2([2010-01-04, 2016-05-04]·[2024-03-13, 2026-08-20]) · adj_factor ok 3.
부정 픽스처: close 를 바꾼 price_daily 사본 → ① FAIL, 재상장 구간을 합친 security_span 사본 → ③
FAIL, 카탈로그 meta 의 snapshot_id 가 어댑터 규칙과 다르면 → bar·사건 항 FAIL.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
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
    views,
)
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from stage import manifest
from stage.gates import GateResult

if TYPE_CHECKING:
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from strategy_workbench.application.backtest_run.facade.ports import (
        BacktestDataQuery,
        BacktestDataset,
    )
    from strategy_workbench.domain.equity.facade.research_data import (
        UniverseHistoryQuery,
        UniverseHistoryResult,
    )

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED_S07 = Path(rules_s06.__file__).parent / "baseline_seed_s07.json"
CHAIN = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
         rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT, rules_s06.ADJ_FACTOR,
         rules_s03.UNIVERSE_DAILY, rules_s03.UNIVERSE_POLICY)
GATE_NAMES = ["EGC-01", "EGC-02", "EGC-03", "EGC-04", "EGC-05", "EGC-10"]
BACKFILL_END = date(2026, 8, 20)


def seed() -> Baseline:
    """S01~S07 seed 를 테이블 단위로 병합 — S07 seed 의 `security` 키가 S01 의
    `security.backfill_end` 와 같은 테이블에 살아 얕은 update 로는 덮인다."""
    merged: dict[str, dict[str, object]] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s03.BASELINE_SEED, rules_s04.BASELINE_SEED, rules_s05.BASELINE_SEED,
              rules_s06.BASELINE_SEED, SEED_S07):
        for k, v in load(p).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


def write_catalog(equity_root: Path) -> None:
    """입력이 갖춰진 매크로로 카탈로그를 쓴다 — 어댑터가 사건과 함께 `v_unfolded_event` 를 읽는다.
    뷰 게이트(EG11 등)는 이 테스트의 관심이 아니라 `catalog.publish` 대신 쓴다."""
    catalog.write_catalog(equity_root, views.render_macros(equity_root)[0])


def build_chain(equity_root: Path, baseline: Baseline) -> None:
    for t in CHAIN:
        r = build.build_table(t, STAGE_SLICE, equity_root, baseline, build_id=f"b_{t.name}")
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]
    write_catalog(equity_root)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("s07") / "equity"
    build_chain(root, seed())
    return root


@pytest.fixture(scope="module")
def result(built: Path) -> contract.ContractResult:
    return contract.run(built, seed())


def _gate(r: contract.ContractResult, name: str) -> GateResult:
    return next(g for g in r.gates if g.name == name)


def _metrics(r: contract.ContractResult, name: str) -> dict[str, Any]:
    # reason: GateResult.metrics 는 dict[str, object] — 테스트는 중첩 dict·list 를 그대로 단언한다
    return dict(_gate(r, name).metrics)


def _status(r: contract.ContractResult) -> dict[str, str]:
    return {g.name: g.status.value for g in r.gates}


# ── 정상 왕복 ────────────────────────────────────────────────────────────────

def test_절단본_체인_위에서_EGC_6항_전부_pass(result: contract.ContractResult) -> None:
    assert [g.name for g in result.gates] == GATE_NAMES
    assert _status(result) == {n: "pass" for n in GATE_NAMES}, [
        (g.name, g.detail, g.metrics) for g in result.gates if g.status is not GateStatus.PASS]
    assert result.ok and result.failed_report is None
    assert result.engine_src == contract.default_engine_src()


def test_EGC01_표본_15티커_bar_는_price_daily_원주가와_같다(
        result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-01")
    assert m["n_tickers"] == 15 and m["n_bars"] == m["n_expected"] == 41066 - 346
    assert m["n_mismatch"] == m["n_missing"] == m["n_extra"] == 0
    assert m["n_reference"] == 346 and m["n_outside_span"] == 0
    assert m["n_invalid"] == m["n_invalid_expected"] == m["n_invalid_diff"] == 0
    assert m["error"] is None and m["spanless_tickers"] == []


def test_EGC02_members_는_고정_입력_listing_etf_와_같다(result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-02")
    assert m["n_dates"] == 7 and m["n_diff"] == 0
    per = m["per_date"]
    assert per["2024-03-12"]["n_adapter"] == per["2024-03-12"]["n_listing"]
    # 15 − 0001A0(2026 상장)·247540(2019 상장)·036220·101970(재상장 공백)·900050·900060(폐지) = 9
    assert per["2018-05-04"]["n_adapter"] == 9


def test_EGC03_재상장_2종_구간_2개_coverage_gap_유지_backfill_end_거절(
        result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-03")
    assert m["n_respan"] == m["expected_respan_count"] == 2
    assert m["respan"]["036220"] == [(1, "2010-01-04", "2016-05-04"),
                                     (2, "2024-03-13", "2026-08-20")]
    assert m["respan"]["101970"] == [(1, "2012-07-26", "2015-03-16"),
                                     (2, "2025-03-28", "2026-08-20")]
    assert m["n_span_mismatch"] == m["n_bar_outside_span"] == m["n_gap_broken"] == 0
    assert m["n_member_probes"] == 8 and m["n_member_mismatch"] == 0   # 2종 × 구간 2 × 첫·끝날
    assert m["n_coverage_gap_spans"] == 12 and m["n_coverage_gap_mismatch"] == 0
    assert m["reject_status"] == "no_data" and "end=2026-08-21" in m["reject_detail"]
    assert m["allow_status"] == "ok"


def test_EGC04_actions_는_factor_ok_3건_ratio_share_factor(result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-04")
    # 절단본 10건(corp_event MVP 8 + S06-2 기준가 신규 unknown_price_only 2, ok=false) 중 ok 3
    # (005930·005935 split, 247540 bonus — 전부 krx_base_price 교체) — 101970 감자 3건은 폐지
    # 기간이라 no_price_match, ratio_null 1, near_dup_suppressed 1 (S06 2차 P23 · S06-2 P27)
    assert m["n_rows"] == 10 and m["n_tickers"] == 3 and m["n_ok"] == m["n_actions"] == 3
    assert m["n_only_adapter"] == m["n_only_factor"] == m["n_not_ok_emitted"] == 0
    assert m["n_factor_outside_span"] == m["n_factor_without_bar_after"] == 0
    assert m["by_type"] == {"split->split": 2, "bonus->split": 1}


def test_EGC04_가격_전용_계수_행이_있어도_어댑터_사건_집합은_그대로(
        built: Path, result: contract.ContractResult) -> None:
    """⑤(e1.26.0): 247540 2022-05-09 계수 행(price_only_factor ≠ 1)이 있는 판에서도 어댑터는
    factor_ok 행만 내보낸다 — not-ok 방출 0, 사건 집합·유형은 ⑤ 전과 같다."""
    con = duckdb.connect()
    try:
        m = manifest.load(built / "adj_factor" / "MANIFEST.json")
        rows = con.execute(
            "SELECT event_id, factor_ok, price_resolution, price_only_factor FROM read_parquet("
            f"'{built / 'adj_factor' / f'v={m.current_build}' / 'year=*' / '*.parquet'}') "
            "WHERE price_resolution = 'price_only' ORDER BY event_id").fetchall()
    finally:
        con.close()
    assert [(e, ok, res) for e, ok, res, _ in rows] == [
        ("247540:krx_base:2022-05-09", False, "price_only"),
        ("900050:krx_base:2011-02-16", False, "price_only")]
    assert all(f != 1.0 for *_, f in rows)
    m4 = _metrics(result, "EGC-04")
    assert m4["n_not_ok_emitted"] == 0 and m4["n_actions"] == 3
    assert m4["by_type"] == {"split->split": 2, "bonus->split": 1}


def test_EGC05_정지_섞인_다종목_질의_기준가_행_제외(result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-05")
    assert m["error"] is None and m["n_bars"] == m["n_expected"] and m["n_reference"] > 0
    assert set(m["tickers"]) >= {"900050", "036220", "101970", "000030", "900060"}


def test_EGC10_폐지_표본_전_구간_질의_반환_요청(result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-10")
    assert m["seed"] == 20260905 and m["n_requested"] == 20 and m["n_candidates"] == 5
    assert m["sample"] == ["000030:1", "036220:1", "101970:1", "900050:1", "900060:1"]
    assert m["returned"] == m["sample"] and m["error"] is None


def test_contract_meta_와_워크벤치_facade_직접_호출(built: Path,
                                                result: contract.ContractResult) -> None:
    meta = json.loads(result.meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "pass" and meta["adapter"] == contract.ADAPTER_MODULE
    assert [g["name"] for g in meta["gates"]] == GATE_NAMES
    wb = contract.load_adapter(contract.default_engine_src())
    adapter = wb.provider.EquityDuckdbAdapter(built)
    data = adapter.load_backtest_dataset(wb.ports.BacktestDataQuery(
        date(2018, 4, 27), date(2018, 5, 31), ("005930:1",), None))
    bars = {b.session: b for b in data.bars}
    # 백테스트 포트는 원주가를 준다 — 조정가(분할 전 2,650,000 → 53,000)를 주면 엔진이 사건으로
    # 수량을 다시 조정해 같은 분할이 두 번 반영된다(DESIGN §7). 분할 정지 기준가 행은 bar 가 없다
    assert sorted(bars)[:2] == [date(2018, 4, 27), date(2018, 5, 4)]
    assert bars[date(2018, 4, 27)].close == 2_650_000.0
    assert bars[date(2018, 5, 4)].close == 51_900.0 and bars[date(2018, 5, 4)].volume == 39_565_391
    got = [(a.action_type, a.ratio, a.session, a.detail) for a in data.corporate_actions]
    assert got == [("split", "50.0", date(2018, 5, 4), "005930:split:2018-05-04")]
    universe = adapter.load_universe(
        wb.universe.UniverseHistoryQuery("XKRX", date(2024, 3, 12), date(2024, 3, 13)))
    assert {p.session: [m.security_id for m in p.members if m.ticker == "036220"]
            for p in universe.points} == {date(2024, 3, 12): [], date(2024, 3, 13): ["036220:2"]}


def test_상수_미등재면_skip_no_baseline(built: Path) -> None:
    r = contract.run(built, Baseline({"trading_calendar": {"asof_sample_tickers": ["005930"]}}))
    assert _status(r) == {"EGC-01": "pass", "EGC-02": "skip", "EGC-03": "skip", "EGC-04": "pass",
                          "EGC-05": "pass", "EGC-10": "skip"}
    assert _gate(r, "EGC-02").detail == "no_baseline"
    assert r.ok                                            # skip 은 실패가 아니다


def test_engine_src_가_없으면_예외(built: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="engine adapter not found"):
        contract.run(built, seed(), engine_src=tmp_path / "nowhere")


# ── 부정 픽스처 ──────────────────────────────────────────────────────────────

def test_FX_N_어댑터의_무효_bar_오분류는_EGC01_FAIL(
    built: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정상 거래일을 무효 bar 로도 내면 값이 같아도 거절한다 — invalid_diff 판정의 회귀."""
    wb = contract.load_adapter(contract.default_engine_src())
    adapter_type = wb.provider.EquityDuckdbAdapter
    load_dataset = adapter_type.load_backtest_dataset

    def misclassify(
        self: EquityDuckdbAdapter, query: BacktestDataQuery,
    ) -> BacktestDataset:
        data = load_dataset(self, query)
        return replace(data, invalid_bars=(
            *data.invalid_bars, wb.ports.InvalidBarRecord(date(2018, 5, 4), "005930:1"),
        ))

    monkeypatch.setattr(adapter_type, "load_backtest_dataset", misclassify)
    result = contract.run(built, seed())
    metrics = _metrics(result, "EGC-01")
    assert metrics["n_mismatch"] == metrics["n_missing"] == metrics["n_extra"] == 0
    assert metrics["n_invalid_diff"] == 1
    assert _gate(result, "EGC-01").status is GateStatus.FAIL
    assert not result.ok


def test_FX_N_재상장_경계일_구성원_누락은_EGC03_FAIL(
    built: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """구간·bar 는 맞아도 구간 첫날 종목 id 가 빠지면 거절한다 — member_mismatch 판정의 회귀."""
    wb = contract.load_adapter(contract.default_engine_src())
    adapter_type = wb.provider.EquityDuckdbAdapter
    load_universe = adapter_type.load_universe

    def omit_member(
        self: EquityDuckdbAdapter, query: UniverseHistoryQuery,
    ) -> UniverseHistoryResult:
        result = load_universe(self, query)
        return replace(result, points=tuple(
            replace(point, members=tuple(
                member for member in point.members
                if not (point.session == date(2012, 7, 26) and member.security_id == "101970:1")
            )) for point in result.points
        ))

    monkeypatch.setattr(adapter_type, "load_universe", omit_member)
    result = contract.run(built, seed())
    metrics = _metrics(result, "EGC-03")
    assert metrics["n_span_mismatch"] == metrics["n_bar_outside_span"] == 0
    assert metrics["n_coverage_gap_mismatch"] == 0
    assert metrics["n_member_mismatch"] == 1
    assert _gate(result, "EGC-03").status is GateStatus.FAIL
    assert not result.ok


def _copy_root(src: Path, dst: Path) -> Path:
    """사본 루트 — 카탈로그 매크로는 절대 경로로 원본을 가리키므로 사본 위에서 다시 쓴다."""
    shutil.copytree(src, dst, symlinks=False)
    write_catalog(dst)
    return dst


def _rewrite_partition(root: Path, table: str, sql: str) -> None:
    """current_build 의 파티션을 `sql`(read_parquet 위 SELECT) 결과로 덮어쓴다 — 결함 주입."""
    m = manifest.load(root / table / "MANIFEST.json")
    rec = next(b for b in m.builds if b.build_id == m.current_build)
    con = duckdb.connect()
    try:
        for p in rec.partitions:
            pdir = root / table / str(p["path"])
            files = sorted(pdir.glob("*.parquet"))
            tmp = pdir / "_rewrite.parquet"
            globs = ", ".join(f"'{f}'" for f in files)
            con.execute(f"COPY ({sql.format(src=f'read_parquet([{globs}])')}) TO '{tmp}' "
                        "(FORMAT PARQUET)")
            for f in files:
                f.unlink()
            tmp.rename(pdir / "part0.parquet")
    finally:
        con.close()


def test_FX_N_close_변조_사본은_EGC01_만_FAIL(built: Path, tmp_path: Path) -> None:
    root = _copy_root(built, tmp_path / "equity")
    _rewrite_partition(root, "price_daily",
                       "SELECT * REPLACE (CASE WHEN ticker = '005930' AND date = DATE '2018-05-04' "
                       "THEN close + 100 ELSE close END AS close) FROM {src}")
    r = contract.run(root, seed())
    assert _status(r) == {"EGC-01": "fail", "EGC-02": "pass", "EGC-03": "pass", "EGC-04": "pass",
                          "EGC-05": "pass", "EGC-10": "pass"}
    m = _metrics(r, "EGC-01")
    assert m["n_mismatch"] == 1 and m["samples"][0].startswith("005930:1 2018-05-04")
    assert not r.ok and r.failed_report is not None and r.failed_report.exists()


@pytest.mark.parametrize("price", ["open", "high", "low", "close"])
def test_EGC04_NULL_OHLC_행은_사건_정산_bar가_아니다(
    built: Path, tmp_path: Path, price: str,
) -> None:
    """사건 뒤 거래 행의 OHLC 하나가 NULL이면 제품이 뺀 사건을 게이트도 기대하지 않는다.

    DuckDB의 least/greatest는 NULL을 건너뛰므로 open·close 결측도 명시적으로 제외해야 한다.
    """
    root = _copy_root(built, tmp_path / "equity")
    _rewrite_partition(
        root, "price_daily",
        "SELECT * REPLACE (CASE WHEN ticker = '005930' AND date >= DATE '2018-05-04' "
        f"THEN NULL ELSE {price} END AS {price}) FROM {{src}}",
    )
    wb = contract.load_adapter(contract.default_engine_src())
    data = wb.provider.EquityDuckdbAdapter(root).load_backtest_dataset(
        wb.ports.BacktestDataQuery(date(2018, 5, 4), BACKFILL_END, ("005930:1", "005935:1"), None),
    )
    assert not any(bar.security_id == "005930:1" for bar in data.bars)
    assert any(row.security_id == "005930:1" for row in data.invalid_bars)
    assert not any(action.detail == "005930:split:2018-05-04" for action in data.corporate_actions)
    result = contract.run(root, seed())
    metrics = _metrics(result, "EGC-04")
    assert metrics["n_factor_without_bar_after"] == 1
    assert metrics["n_only_factor"] == metrics["n_only_adapter"] == 0
    assert _gate(result, "EGC-04").status is GateStatus.PASS


def test_FX_N_재상장_구간_합친_사본은_EGC03_FAIL(built: Path, tmp_path: Path) -> None:
    """036220 두 구간을 하나로 뭉치면 ③ 이 재상장 종목 수로 잡는다. 구성원은 `universe_daily` 가
    정하므로 공백 기간에 036220 이 나타나지 않고(② pass), 합친 구간의 bar 는 구간 안이라 ①·⑤ 도
    그대로다."""
    root = _copy_root(built, tmp_path / "equity")
    _rewrite_partition(root, "security_span",
                       "SELECT ticker, span_seq, first_date, "
                       "CASE WHEN ticker = '036220' THEN DATE '2026-08-20' ELSE last_date END "
                       "AS last_date, n_days, end_reason FROM {src} "
                       "WHERE NOT (ticker = '036220' AND span_seq = 2)")
    r = contract.run(root, seed())
    assert _status(r) == {"EGC-01": "pass", "EGC-02": "pass", "EGC-03": "fail", "EGC-04": "pass",
                          "EGC-05": "pass", "EGC-10": "pass"}
    m = _metrics(r, "EGC-03")
    assert m["n_respan"] == 1 and m["expected_respan_count"] == 2
    assert m["respan"] == {"101970": [(1, "2012-07-26", "2015-03-16"),
                                      (2, "2025-03-28", "2026-08-20")]}


def test_FX_N_카탈로그_snapshot_id_가_다르면_bar_사건_항이_FAIL(built: Path,
                                                            tmp_path: Path) -> None:
    """어댑터는 카탈로그 meta 의 snapshot_id(`equity.catalog.snapshot_id` 가 씀)를 자기 사본
    규칙으로 다시 센 값과 대조한다. 두 사본이 갈린 상태를 meta 값을 바꿔 흉내 내면 백테스트
    데이터가 거절돼 bar·사건 항이 FAIL 하고, 유니버스만 읽는 ② 는 그대로다."""
    root = _copy_root(built, tmp_path / "equity")
    meta_path = root / catalog.META_NAME
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_path.write_text(json.dumps({**meta, "snapshot_id": "0" * 16}), encoding="utf-8")
    r = contract.run(root, seed())
    assert _status(r) == {"EGC-01": "fail", "EGC-02": "pass", "EGC-03": "fail", "EGC-04": "fail",
                          "EGC-05": "fail", "EGC-10": "fail"}
    assert "catalog_stale" in str(_metrics(r, "EGC-10")["error"])


# ── DEFECT-C05: 저녁 잠정 행·신설 표에서도 계약이 선다 (감사 09-19) ────────────


def _ctx(con: duckdb.DuckDBPyConnection, src: str) -> Any:
    return contract._Ctx(root=Path("/tmp/unused"), con=con, wb=None, adapter=None,
                         baseline=Baseline(), venue=contract.VENUE, tables={"price_daily": src})


def test_계약의_price_daily_대조축은_확정_행뿐이다() -> None:
    """DEFECT-C05 — 워크벤치 어댑터는 `basis <> 'krx'` 행을 bar 로 내지 않는다(잠정 행은 확정 전
    값이라 백테스트 바에 못 넣는다). 계약의 대조축이 같은 필터를 안 걸면 저녁 판이 current 인
    10시간 40분 동안 `price_kind='reference'` 집계가 잠정 행까지 세어 표본 선택이 흔들린다.
    """
    con = duckdb.connect()
    try:
        src = ("(SELECT * FROM (VALUES ('A', DATE '2026-08-20', 'trade', 'krx'), "
               "('B', DATE '2026-08-21', 'reference', 'evening')) "
               "AS t(ticker, date, price_kind, basis))")
        got = contract.confirmed_source(_ctx(con, src), "price_daily")
        assert con.execute(f"SELECT count(*) FROM {got}").fetchone()[0] == 1
        assert con.execute(
            f"SELECT count(*) FROM {got} WHERE price_kind = 'reference'").fetchone()[0] == 0
        # `basis` 열이 없는 옛 판은 그대로 읽는다(계약이 옛 산출에서도 서야 한다)
        old = ("(SELECT * FROM (VALUES ('A', DATE '2026-08-20', 'trade')) "
               "AS t(ticker, date, price_kind))")
        assert con.execute(
            f"SELECT count(*) FROM {contract.confirmed_source(_ctx(con, old), 'price_daily')}"
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_신설_표가_늘어도_계약_대상은_커밋된_표_전건이다(built: Path,
                                                      result: contract.ContractResult) -> None:
    """`coverage_daily`(S24, 09-10 신설)처럼 표가 늘어도 `table_builds` 가 기계적으로 줍는다 —
    계약이 표 수를 코드에 박아 두지 않았다는 회귀 테스트(DEFECT-C05 의 '28표 고정' 오해 방지)."""
    from equity.catalog import table_builds

    assert result.builds == table_builds(built)
    assert set(result.builds) >= {t.name for t in CHAIN}
