"""S07 EG-C 소비자 계약 ①②③④⑤⑩ — 절단본 체인 위에서 커널 어댑터 실행 (GATES §1 EG-C · DESIGN §7).

상류 7테이블(`trading_calendar`·`security`·`security_span`·`corp_ticker`·`price_daily`·`corp_event`·
`adj_factor`)을 같은 equity_root 에 짓고 `contract.run` 을 돌린다. 엔진 어댑터는 같은 모노레포의
`backend/src`(pyarrow + numpy 필요 — `uv run --with numpy` 를 더해야 한다) 에서 import 한다.

절단본 손계산(원자료 직접 확인): 005930 2018-05-03 close 2,650,000(reference) → 05-04 51,900(trade)
· 036220 구간 2([2010-01-04, 2016-05-04]·[2024-03-13, 2026-08-20]) · adj_factor ok 6.
부정 픽스처: close 를 바꾼 price_daily 사본 → ① FAIL, 재상장 구간을 합친 security_span 사본 → ③
FAIL.
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
from equity import build, contract, rules_s01, rules_s02, rules_s04, rules_s05, rules_s06
from equity.baseline import Baseline, load
from equity.gates import GateStatus
from stage import manifest
from stage.gates import GateResult

STAGE_SLICE = Path(__file__).parent / "fixtures" / "stage_slice"
SEED_S07 = Path(rules_s06.__file__).parent / "baseline_seed_s07.json"
CHAIN = (rules_s02.TRADING_CALENDAR, rules_s01.SECURITY, rules_s02.SECURITY_SPAN,
         rules_s01.CORP_TICKER, rules_s04.PRICE_DAILY, rules_s05.CORP_EVENT, rules_s06.ADJ_FACTOR)
GATE_NAMES = ["EGC-01", "EGC-02", "EGC-03", "EGC-04", "EGC-05", "EGC-10"]
BACKFILL_END = date(2026, 8, 20)

pytest.importorskip("numpy", reason="backtest_engine.types.market 가 numpy 를 요구한다")


def seed() -> Baseline:
    """S01·S02·S05·S06·S07 seed 를 테이블 단위로 병합 — S07 seed 의 `security` 키가 S01 의
    `security.backfill_end` 와 같은 테이블에 살아 얕은 update 로는 덮인다."""
    merged: dict[str, dict[str, object]] = {}
    for p in (rules_s01.BASELINE_SEED, Path(rules_s02.__file__).parent / "baseline_seed_s02.json",
              rules_s05.BASELINE_SEED, rules_s06.BASELINE_SEED, SEED_S07):
        for k, v in load(p).data.items():
            if not k.startswith("_") and k != "measured_at" and isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
    return Baseline(dict(merged))


def build_chain(equity_root: Path, baseline: Baseline) -> None:
    for t in CHAIN:
        r = build.build_table(t, STAGE_SLICE, equity_root, baseline, build_id=f"b_{t.name}")
        assert r.ok, [(g.name, g.status.value, g.detail) for g in r.gates]


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
    assert m["n_reference"] == 346 and m["n_gap14_like"] == 0 and m["n_dropped"] == 346
    assert m["n_repaired"] == 0


def test_EGC02_members_는_고정_입력_listing_etf_와_같다(result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-02")
    assert m["n_dates"] == 7 and m["n_diff"] == 0
    per = m["per_date"]
    assert per["2024-03-12"]["n_adapter"] == per["2024-03-12"]["n_listing"]
    # 15 − 0001A0(2026 상장)·247540(2019 상장)·036220·101970(재상장 공백)·900050·900060(폐지) = 9
    assert per["2018-05-04"]["n_adapter"] == 9


def test_EGC03_재상장_2종_Membership_2구간_coverage_gap_유지_backfill_end_거절(
        result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-03")
    assert m["n_respan"] == m["expected_respan_count"] == 2
    assert m["respan"]["036220"] == [(1, "2010-01-04", "2016-05-04"),
                                     (2, "2024-03-13", "2026-08-20")]
    assert m["respan"]["101970"] == [(1, "2012-07-26", "2015-03-16"),
                                     (2, "2025-03-28", "2026-08-20")]
    assert m["n_span_mismatch"] == 0 and m["n_gap_broken"] == 0
    assert m["n_coverage_gap_spans"] == 12 and m["n_coverage_gap_mismatch"] == 0
    assert m["reject_status"] == "no_data" and "backfill_end=2026-08-20" in m["reject_detail"]
    assert m["allow_status"] == "ok"


def test_EGC04_actions_는_factor_ok_3건_ratio_share_factor(result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-04")
    assert m["ts_column"] == "apply_date"           # S06 2차: 계수는 apply_date 세션에 적용
    # 절단본 8건 중 ok 3 (005930·005935 split, 247540 bonus) — 101970 감자 3건은 폐지 기간이라
    # no_price_match, ratio_null 1, near_dup_suppressed 1 (S06 2차 P23)
    assert m["n_rows"] == 8 and m["n_ok"] == m["n_actions"] == 3
    assert m["n_only_adapter"] == m["n_only_factor"] == m["n_not_ok_emitted"] == 0
    assert m["by_type"] == {"split->split": 2, "bonus->split": 1}


def test_EGC05_정지_섞인_다종목_BarQuery_OK_dropped_양수(result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-05")
    assert m["status"] == "ok" and m["dropped_rows"] == m["expected_dropped"] > 0
    assert set(m["tickers"]) >= {"900050", "036220", "101970", "000030", "900060"}


def test_EGC10_폐지_표본_전_구간_BarQuery_반환_요청(result: contract.ContractResult) -> None:
    m = _metrics(result, "EGC-10")
    assert m["seed"] == 20260905 and m["n_requested"] == 20 and m["n_candidates"] == 5
    assert m["sample"] == ["000030:1", "036220:1", "101970:1", "900050:1", "900060:1"]
    assert m["returned"] == m["sample"] and m["status"] == "ok"


def test_contract_meta_와_어댑터_직접_호출(built: Path, result: contract.ContractResult) -> None:
    meta = json.loads(result.meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "pass" and meta["adapter"] == contract.ADAPTER_MODULE
    assert [g["name"] for g in meta["gates"]] == GATE_NAMES
    mod = contract.load_adapter(contract.default_engine_src())
    from backtest_engine.ports.corporate_actions import CorporateActionQuery
    from backtest_engine.ports.market_data import BarQuery
    from backtest_engine.ports.universe import UniverseQuery

    samsung = mod.InstrumentId(venue="XKRX", symbol="005930", asset_class=mod.AssetClass.EQUITY,
                               currency="KRW")
    bars = mod.EquityBarSource(built).load_bars(
        BarQuery(instruments=(samsung,), start=date(2018, 5, 1), end=date(2018, 5, 31)))
    assert bars.ok and bars.dropped_rows == 2            # 05-02·05-03 분할 정지(reference)
    assert [b.ts.date() for b in bars.bars][:1] == [date(2018, 5, 4)]
    assert bars.bars[0].close == 51_900.0 and bars.bars[0].volume == 39_565_391
    universe = mod.EquityUniverseSource(built).load_universe(UniverseQuery(venue="XKRX"))
    assert sorted(m.instrument.symbol for m in universe.memberships
                  if m.instrument.symbol.startswith("036220")) == ["036220:1", "036220:2"]
    actions = mod.EquityCorporateActionSource(built).load_actions(
        CorporateActionQuery(instruments=(samsung,)))
    got = [(a.action_type.value, str(a.ratio), a.ts.date(), a.detail) for a in actions.actions]
    assert got == [("split", "50.0", date(2018, 5, 4), "005930:split:2018-05-04")]


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

def _copy_root(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst, symlinks=False)
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
    assert m["n_mismatch"] == 1 and m["samples"][0].startswith("005930 2018-05-04")
    assert not r.ok and r.failed_report is not None and r.failed_report.exists()


def test_FX_N_재상장_구간_합친_사본은_EGC03_FAIL(built: Path, tmp_path: Path) -> None:
    """036220 두 구간을 하나로 뭉치면 ③ 이 잡는다. 공백 기간(2016-05-05~2024-03-12)에 036220 이
    유니버스에 나타나므로 ② 도 probe 2018-05-04·2024-03-12 에서 같이 잡는다(항 간 중복 검출은
    의도)."""
    root = _copy_root(built, tmp_path / "equity")
    _rewrite_partition(root, "security_span",
                       "SELECT ticker, span_seq, first_date, "
                       "CASE WHEN ticker = '036220' THEN DATE '2026-08-20' ELSE last_date END "
                       "AS last_date, n_days, end_reason FROM {src} "
                       "WHERE NOT (ticker = '036220' AND span_seq = 2)")
    r = contract.run(root, seed())
    st = _status(r)
    assert st["EGC-03"] == "fail" and st["EGC-02"] == "fail"
    assert {st[n] for n in ("EGC-01", "EGC-04", "EGC-05", "EGC-10")} == {"pass"}
    m = _metrics(r, "EGC-03")
    assert m["n_respan"] == 1 and m["expected_respan_count"] == 2
    assert m["respan"] == {"101970": [(1, "2012-07-26", "2015-03-16"),
                                      (2, "2025-03-28", "2026-08-20")]}
    per = _metrics(r, "EGC-02")["per_date"]
    assert per["2018-05-04"]["only_adapter"] == ["036220"]
    assert per["2024-03-12"]["only_adapter"] == ["036220"]
