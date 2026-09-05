"""EG-C 소비자 계약 게이트 ①②③④⑤⑩ — 커널 어댑터를 equity_root 위에서 실행해 술어로 검사한다.

(EQUITY_GATES §1 EG-C · EQUITY_WORKFLOW S07 · DESIGN §7.) 대상 어댑터는
`backend/src/backtest_engine/adapters/equity_duckdb.py`(pyarrow) 이고, 각 항은 어댑터가 준 값을
**duckdb 로 같은 MANIFEST 파티션(또는 `_pinned/` stage 입력)을 독립으로 읽은 값**과 대조한다 —
어댑터 코드 경로를 재호출하는 항진명제가 아니다.

  EGC-01  bars 원주가 동일성 — 표본 티커(`trading_calendar.asof_sample_tickers`) 전 구간 Bar 의
          O/H/L/C/V = `price_daily` 의 **고정 입력** `_pinned/stg_price_daily` ∪
          `stg_etf_price_daily` 행(거래량 > 0 ∧ OHLC 양수 — 어댑터가 읽는 산출이 아니라 stage
          원주가, EG20 과 같은 축),
          `dropped_rows` = 거래량 0·NULL(reference) 행 + GAP-14 류(OHLC NULL·≤0) 행. 정책은
          CLAMP(서버 GAP-14 127행을 거절하지 않고 세기 위해).
          "BUILDERS 등록 → 전 케이스 통과" 축은 backend pytest(`test_bar_source_contract.py`) 몫.
  EGC-02  `UniverseSource.members(d)` 티커 집합 = `_pinned/` stage `stg_listing_daily(d)` ∪
          `stg_etf_price_daily(d)` 티커 집합, d ∈ `universe_daily.contract_probe_dates`.
          `v_universe` 뷰(T9)가 아직 없어 어댑터 축으로 검사한다. 가격·상장류 랙 0.
  EGC-03  재상장 종목(구간 ≥ 2) 수 = `security_span.respan_count` ∧ 티커별 Membership 수 = 구간 수
          ∧ 구간 사이 공백 유지 ∧ `coverage_gap` 구간의 last_session = backfill_end(끊지 않음) ∧
          `UniverseQuery.end > backfill_end` 거절(NO_DATA) ∧ `end = backfill_end` 허용.
  EGC-04  `CorporateActionEvent` 집합 = `adj_factor` factor_ok 행 {(ticker, apply_date |
          effective_date, share_factor, event_id)} — ratio = share_factor, not-ok 행 미방출.
          (GATES 표의 "run 누적수익률 = 조정가 손계산" 은 엔진 run 이 필요해 S21/S22 MVP-B 몫.)
  EGC-05  reference 행이 가장 많은 티커 5 + reference 없는 티커 1 의 다종목 BarQuery → OK ∧
          dropped_rows = Σ reference(+GAP-14 류) > 0.
  EGC-10  `security_span.end_reason='delisted'` 구간 중 `security.delist_sample_seed`·
          `delist_sample_n` 으로 고정 추출한 표본의 `{ticker}:{span_seq}` 전 구간 BarQuery → OK ∧
          반환 종목 집합 = 요청 집합.

경계: 어댑터는 같은 모노레포의 `backend/src` 에 산다. equity 패키지가 backend 를 import 하는 곳은
**이 모듈 하나**다 — `load_adapter(engine_src)` 가 그 경로를 `sys.path` 에 얹는다. 기본 경로는
`<repo>/backend/src`(이 파일에서 4단계 위) 또는 `$QL_ENGINE_SRC`. 서버처럼 backend 체크아웃이 없는
곳은 `--engine-src` 로 준다(엔진은 numpy·pyarrow 를 요구한다). 없으면 skip 이 아니라 예외다
(GATES §9 A13: 같은 모노레포라 `skip(engine_absent)` 를 두지 않는다).

결과: `<equity_root>/_contract_meta.json`(snapshot_id·builds·engine_src·gates[]·status). FAIL 이
하나라도 있으면 `_failed/contract_<snapshot_id>.json` 도 쓴다. 판정은 항별 `EGC-##` 행으로
남긴다(GATES §1 의 "EG-C 1행" 은 항별 metric 을 잃어 세분했다).
"""
from __future__ import annotations

import importlib
import json
import os
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import duckdb
from stage import manifest
from stage.gates import GateResult, GateStatus

from . import inputs, views
from .baseline import Baseline
from .catalog import snapshot_id, table_builds
from .gates import SkipGate

if TYPE_CHECKING:                       # 타입만 — 런타임 import 는 load_adapter() 뒤에만 일어난다
    from backtest_engine.ports.market_data import LoadResult
    from backtest_engine.types.instruments import InstrumentId

META_NAME = "_contract_meta.json"
ADAPTER_MODULE = "backtest_engine.adapters.equity_duckdb"
VENUE = "XKRX"                        # FIELD_MAP §1 — 커널 InstrumentId.venue
SAMPLE_TICKERS = ("trading_calendar", "asof_sample_tickers")
PROBE_DATES = ("universe_daily", "contract_probe_dates")
RESPAN_COUNT = ("security_span", "respan_count")
DELIST_SEED = ("security", "delist_sample_seed")
DELIST_N = ("security", "delist_sample_n")
HALT_TICKERS_N = 5                    # EGC-05 — reference 행 최다 티커 수
SAMPLE_ROWS = 10                      # metrics 에 남기는 불일치 표본 수
ENGINE_SRC_ENV = "QL_ENGINE_SRC"


def default_engine_src() -> Path:
    """`$QL_ENGINE_SRC` 또는 `<repo>/backend/src`(workspace/dongmin/src/equity 에서 4단계 위)."""
    env = os.environ.get(ENGINE_SRC_ENV)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4] / "backend" / "src"


def load_adapter(engine_src: Path) -> ModuleType:
    """엔진 소스 루트를 sys.path 에 얹고 어댑터 모듈을 import 한다 — equity → backend 유일 경계."""
    marker = engine_src / "backtest_engine" / "adapters" / "equity_duckdb.py"
    if not marker.exists():
        raise FileNotFoundError(
            f"engine adapter not found — engine_src={engine_src} expected={marker} "
            f"(--engine-src 또는 ${ENGINE_SRC_ENV} 로 backend/src 를 지정)")
    root = str(engine_src)
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module(ADAPTER_MODULE)


@dataclass(frozen=True)
class ContractResult:
    ok: bool
    snapshot_id: str
    builds: dict[str, str]
    engine_src: Path
    gates: list[GateResult]
    meta_path: Path
    failed_report: Path | None = None


@dataclass
class _Ctx:
    root: Path
    con: duckdb.DuckDBPyConnection
    mod: ModuleType
    baseline: Baseline
    venue: str
    tables: dict[str, str] = field(default_factory=dict)     # 커밋된 테이블 → read_parquet(...)

    def source(self, table: str) -> str:
        """커밋된 테이블의 read_parquet 관계식. 없으면 skip(not_built)."""
        src = self.tables.get(table)
        if src is None:
            raise SkipGate("not_built", {"missing_table": table})
        return src

    def rows(self, sql: str) -> list[tuple[object, ...]]:
        return [tuple(r) for r in self.con.execute(sql).fetchall()]

    def one(self, sql: str) -> tuple[object, ...]:
        row = self.con.execute(sql).fetchone()
        if row is None:
            raise RuntimeError(f"contract query returned no row: {sql[:200]}")
        return tuple(row)

    def constant(self, key: tuple[str, str]) -> object:
        v = self.baseline.get(*key)
        if v is None:
            raise SkipGate("no_baseline", {"missing_metric": ".".join(key)})
        return v

    def instrument(self, symbol: str) -> InstrumentId:
        return self.mod.InstrumentId(
            venue=self.venue, symbol=symbol, asset_class=self.mod.AssetClass.EQUITY,
            currency=self.mod.CURRENCY)

    def backfill_end(self) -> date:
        (v,) = self.one(f"SELECT max(date) FROM {self.source('trading_calendar')}")
        if not isinstance(v, date):
            raise RuntimeError(f"trading_calendar max(date) is not a date: got={v!r}")
        return v


def _as_date(v: object) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def _lit(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _pinned_source(ctx: _Ctx, table: str, stage_table: str) -> str:
    """`<table>` 의 MANIFEST `inputs` 에 고정된 stage 입력(`_pinned/`)의 read_parquet — 독립 축.

    어댑터가 읽는 equity 산출과 다른 원천(stage)이라 산출을 변조한 사본이 잡힌다. 고정본이 없으면
    skip(no_cross_source).
    """
    ctx.source(table)
    m = manifest.load(ctx.root / table / "MANIFEST.json")
    rec = next((b for b in m.builds if b.build_id == m.current_build), None)
    if rec is None or stage_table not in rec.inputs:
        raise SkipGate("no_cross_source",
                       {"table": table, "stage_table": stage_table,
                        "inputs": dict(rec.inputs) if rec else {}})
    try:
        pb = inputs.load_pinned(ctx.root, stage_table, rec.inputs[stage_table])
    except FileNotFoundError as e:
        raise SkipGate("no_cross_source", {"table": stage_table, "error": str(e)}) from e
    globs = ", ".join(f"'{g}'" for g in pb.globs)
    return f"read_parquet([{globs}], hive_partitioning=true, union_by_name=true)"


def _price_rows(ctx: _Ctx, tickers: list[str]) -> dict[str, list[tuple[object, ...]]]:
    """(ticker → [(date, open, high, low, close, volume_shr)]) — `price_daily` 의 고정 입력
    `stg_price_daily` ∪ `stg_etf_price_daily`(캘린더 안 날짜만 — `_reject/off_calendar` 축) 을
    읽는다.
    어댑터가 읽는 `price_daily` 자체가 아니라 stage 원주가라 산출 변조가 잡힌다(EG20 과 같은 축)."""
    cols = "ticker, date, open_krw, high_krw, low_krw, close_krw, volume_shr"
    stock = _pinned_source(ctx, "price_daily", "stg_price_daily")
    etf = _pinned_source(ctx, "price_daily", "stg_etf_price_daily")
    out: dict[str, list[tuple[object, ...]]] = {t: [] for t in tickers}
    for r in ctx.rows(
            f"SELECT {cols} FROM (SELECT {cols} FROM {stock} UNION ALL SELECT {cols} FROM {etf}) "
            f"WHERE ticker IN ({_lit(tickers)}) "
            f"AND date IN (SELECT date FROM {ctx.source('trading_calendar')}) ORDER BY 1, 2"):
        out[str(r[0])].append(r[1:])
    return out


Emitted = dict[date, tuple[float, ...]]


def _expected_bars(rows: list[tuple[object, ...]]) -> tuple[Emitted, int, int]:
    """어댑터가 방출해야 할 행(date → (o,h,l,c,v)) · reference 행수(거래량 0·NULL) · GAP-14 류
    (거래량 > 0 인데 OHLC NULL·≤0 — CLAMP 가 세는 행)."""
    emit: dict[date, tuple[float, ...]] = {}
    n_ref = n_gap = 0
    for d, o, h, lo, c, v in rows:
        volume = 0 if v is None else int(str(v))
        if volume == 0:
            n_ref += 1
            continue
        prices = (o, h, lo, c)
        if any(p is None for p in prices) or any(float(str(p)) <= 0 for p in prices):
            n_gap += 1
            continue
        emit[_as_date(d)] = (*(float(str(p)) for p in prices), float(volume))
    return emit, n_ref, n_gap


def _load_bars(ctx: _Ctx, symbols: list[str], **query: object) -> LoadResult:
    ports = importlib.import_module("backtest_engine.ports.market_data")
    q = ports.BarQuery(instruments=tuple(ctx.instrument(s) for s in symbols),
                       ohlc_policy=ports.OhlcPolicy.CLAMP, **query)
    return ctx.mod.EquityBarSource(ctx.root).load_bars(q)


# ── 항별 술어 ────────────────────────────────────────────────────────────────

def egc01_bars_raw_price(ctx: _Ctx) -> GateResult:
    raw = ctx.constant(SAMPLE_TICKERS)
    tickers = sorted(str(t) for t in raw) if isinstance(raw, list) else []
    if not tickers:
        raise SkipGate("no_baseline", {"missing_metric": ".".join(SAMPLE_TICKERS)})
    expected_by_ticker = _price_rows(ctx, tickers)
    n_bars = n_expected = n_mismatch = n_missing = n_extra = 0
    n_ref = n_gap = n_dropped = n_repaired = 0
    samples: list[str] = []
    failures: list[str] = []
    for ticker in tickers:
        emit, ref, gap = _expected_bars(expected_by_ticker[ticker])
        n_ref += ref
        n_gap += gap
        n_expected += len(emit)
        result = _load_bars(ctx, [ticker])
        if not result.ok:
            if emit:
                failures.append(f"{ticker}: {result.status.value} {result.detail}")
            continue
        n_dropped += int(result.dropped_rows)
        n_repaired += int(result.repaired_rows)
        got = {b.ts.date(): (b.open, b.high, b.low, b.close, float(b.volume)) for b in result.bars}
        n_bars += len(got)
        for d, values in got.items():
            exp = emit.get(d)
            if exp is None:
                n_extra += 1
                samples.append(f"extra {ticker} {d}")
            elif exp != values:
                n_mismatch += 1
                if len(samples) < SAMPLE_ROWS:
                    samples.append(f"{ticker} {d} adapter={values} price_daily={exp}")
        n_missing += sum(1 for d in emit if d not in got)
        if result.dropped_rows != ref + gap:
            failures.append(f"{ticker}: dropped={result.dropped_rows} expected={ref}+{gap}")
    metrics: dict[str, object] = {
        "n_tickers": len(tickers), "n_bars": n_bars, "n_expected": n_expected,
        "n_mismatch": n_mismatch, "n_missing": n_missing, "n_extra": n_extra,
        "n_reference": n_ref, "n_gap14_like": n_gap, "n_dropped": n_dropped,
        "n_repaired": n_repaired, "policy": "CLAMP", "samples": samples[:SAMPLE_ROWS],
        "failures": failures[:SAMPLE_ROWS]}
    ok = not failures and n_mismatch == n_missing == n_extra == 0 and n_bars == n_expected
    return GateResult("EGC-01", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"bars 원주가 동일(표본 {len(tickers)}티커, {n_bars}행)" if ok else
                      f"bar/price_daily mismatch: mismatch={n_mismatch} missing={n_missing} "
                      f"extra={n_extra} failures={len(failures)}", metrics)


def _pinned_existence(ctx: _Ctx, dates: list[date]) -> dict[date, set[str]]:
    """security_span 의 고정 입력(listing ∪ etf_price)에서 날짜별 티커 집합 — 독립 축."""
    out: dict[date, set[str]] = {d: set() for d in dates}
    for t in ("stg_listing_daily", "stg_etf_price_daily"):
        src = _pinned_source(ctx, "security_span", t)
        for ticker, d in ctx.rows(
                f"SELECT DISTINCT ticker, date FROM {src} "
                f"WHERE date IN ({', '.join(f'DATE {d.isoformat()!r}' for d in dates)})"):
            out[_as_date(d)].add(str(ticker))
    return out


def egc02_universe_equals_listing(ctx: _Ctx) -> GateResult:
    raw = ctx.constant(PROBE_DATES)
    dates = sorted(_as_date(d) for d in raw) if isinstance(raw, list) and raw else []
    if not dates:
        raise SkipGate("no_baseline", {"missing_metric": ".".join(PROBE_DATES)})
    ctx.source("security_span")
    listing = _pinned_existence(ctx, dates)
    ports = importlib.import_module("backtest_engine.ports.universe")
    result = ctx.mod.EquityUniverseSource(ctx.root).load_universe(
        ports.UniverseQuery(venue=ctx.venue, start=dates[0], end=dates[-1]))
    if not result.ok:
        return GateResult("EGC-02", GateStatus.FAIL,
                          f"universe load failed: {result.status.value} {result.detail}",
                          {"dates": [d.isoformat() for d in dates]})
    per_date: dict[str, dict[str, object]] = {}
    n_diff = 0
    for d in dates:
        got = {i.symbol.split(ctx.mod.SECURITY_ID_SEP)[0] for i in result.members(d)}
        exp = listing[d]
        only_adapter, only_listing = sorted(got - exp), sorted(exp - got)
        n_diff += len(only_adapter) + len(only_listing)
        per_date[d.isoformat()] = {
            "n_adapter": len(got), "n_listing": len(exp),
            "only_adapter": only_adapter[:SAMPLE_ROWS], "only_listing": only_listing[:SAMPLE_ROWS]}
    metrics: dict[str, object] = {"n_dates": len(dates), "n_diff": n_diff, "per_date": per_date,
                                  "n_memberships": len(result.memberships)}
    ok = n_diff == 0
    return GateResult("EGC-02", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"members(d) = listing(d) ∪ etf(d), {len(dates)}일" if ok else
                      f"universe/listing differ: n_diff={n_diff}", metrics)


def egc03_relisting_spans(ctx: _Ctx) -> GateResult:
    expected_respan = int(str(ctx.constant(RESPAN_COUNT)))
    span_src = ctx.source("security_span")
    backfill_end = ctx.backfill_end()
    spans: dict[str, list[tuple[int, date, date, str]]] = {}
    for t, seq, f, last, reason in ctx.rows(
            f"SELECT ticker, span_seq, first_date, last_date, end_reason FROM {span_src} "
            "ORDER BY 1, 2"):
        spans.setdefault(str(t), []).append((int(str(seq)), _as_date(f), _as_date(last),
                                             str(reason)))
    ports = importlib.import_module("backtest_engine.ports.universe")
    source = ctx.mod.EquityUniverseSource(ctx.root)
    result = source.load_universe(ports.UniverseQuery(venue=ctx.venue))
    if not result.ok:
        return GateResult("EGC-03", GateStatus.FAIL,
                          f"universe load failed: {result.status.value} {result.detail}", {})
    got: dict[str, list[tuple[int, date, date]]] = {}
    for mem in result.memberships:
        ticker, _, seq = mem.instrument.symbol.partition(ctx.mod.SECURITY_ID_SEP)
        got.setdefault(ticker, []).append((int(seq), mem.first_session, mem.last_session))
    for v in got.values():
        v.sort()
    respan = {t: v for t, v in got.items() if len(v) >= 2}
    n_span_mismatch = sum(1 for t, v in spans.items()
                          if [(s, f, last) for s, f, last, _ in v] != got.get(t))
    n_gap_broken = sum(1 for v in respan.values()
                       for a, b in zip(v, v[1:], strict=False) if not a[2] < b[1])
    cov = [(t, s, last) for t, v in spans.items() for s, _, last, reason in v
           if reason == "coverage_gap"]
    n_cov_mismatch = sum(1 for t, s, last in cov
                         if last != backfill_end
                         or next((m[2] for m in got.get(t, []) if m[0] == s), None) != backfill_end)
    rejected = source.load_universe(
        ports.UniverseQuery(venue=ctx.venue, end=backfill_end + timedelta(days=1)))
    allowed = source.load_universe(ports.UniverseQuery(venue=ctx.venue, end=backfill_end))
    reject_ok = (not rejected.ok) and "backfill_end" in str(rejected.detail or "")
    metrics: dict[str, object] = {
        "expected_respan_count": expected_respan, "n_respan": len(respan),
        "respan": {t: [(s, f.isoformat(), last.isoformat()) for s, f, last in v]
                   for t, v in sorted(respan.items())},
        "n_tickers": len(spans), "n_memberships": len(result.memberships),
        "n_span_mismatch": n_span_mismatch, "n_gap_broken": n_gap_broken,
        "n_coverage_gap_spans": len(cov), "n_coverage_gap_mismatch": n_cov_mismatch,
        "backfill_end": backfill_end.isoformat(),
        "reject_status": rejected.status.value, "reject_detail": rejected.detail,
        "allow_status": allowed.status.value}
    ok = (len(respan) == expected_respan and n_span_mismatch == 0 and n_gap_broken == 0
          and n_cov_mismatch == 0 and reject_ok and allowed.ok)
    return GateResult("EGC-03", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"재상장 {len(respan)}종 Membership 2구간·coverage_gap 유지·backfill_end 거절"
                      if ok else
                      f"relisting contract broken: respan={len(respan)}/{expected_respan} "
                      f"span_mismatch={n_span_mismatch} gap_broken={n_gap_broken} "
                      f"coverage_gap_mismatch={n_cov_mismatch} reject_ok={reject_ok} "
                      f"allow_ok={allowed.ok}", metrics)


def egc04_actions_equal_factors(ctx: _Ctx) -> GateResult:
    src = ctx.source("adj_factor")
    ctx.source("security_span")
    cols = {str(r[0]) for r in ctx.con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
    has_apply = "apply_date" in cols
    ts_col = "apply_date" if has_apply else "effective_date"
    rows = ctx.rows(f"SELECT ticker, event_id, event_type, {ts_col}, share_factor, factor_ok "
                    f"FROM {src} ORDER BY 1, 2")
    expected = {(str(t), _as_date(d), float(str(sf)), str(eid))
                for t, eid, _, d, sf, ok in rows if ok is True}
    # 어댑터는 기업행위를 그 종목의 상장 구간(Membership) 안에서만 방출한다 — 커널은 구간 밖에
    # 포지션을 가질 수 없다. 구간 밖 apply_date 행(폐지 뒤 회고 기재 등)은 비교 모집단에서 빼고
    # 건수만 남긴다(서버 실측 09-05: 5건, 예 000360 2018-08-09 소각 감자).
    spans: dict[str, list[tuple[date, date]]] = {}
    span_src = ctx.source("security_span")
    for t, a, b in ctx.rows(f"SELECT ticker, first_date, last_date FROM {span_src}"):
        spans.setdefault(str(t), []).append((_as_date(a), _as_date(b)))
    outside = sorted(x for x in expected
                     if not any(a <= x[1] <= b for a, b in spans.get(x[0], [])))
    expected -= set(outside)
    not_ok_ids = {str(eid) for _, eid, _, _, _, ok in rows if ok is not True}
    type_of = {str(eid): str(et) for _, eid, et, _, _, _ in rows}
    tickers = sorted({str(r[0]) for r in rows})
    if not tickers:
        return GateResult("EGC-04", GateStatus.PASS, "adj_factor 0행 — 방출할 계수 없음",
                          {"n_ok": 0, "n_actions": 0, "ts_column": ts_col})
    ports = importlib.import_module("backtest_engine.ports.corporate_actions")
    result = ctx.mod.EquityCorporateActionSource(ctx.root).load_actions(
        ports.CorporateActionQuery(instruments=tuple(ctx.instrument(t) for t in tickers)))
    if not result.ok:
        return GateResult("EGC-04", GateStatus.FAIL,
                          f"actions load failed: {result.status.value} {result.detail}",
                          {"n_ok": len(expected), "ts_column": ts_col})
    got = {(e.instrument.symbol, e.ts.date(), float(e.ratio), e.detail) for e in result.actions}
    only_adapter, only_factor = sorted(got - expected), sorted(expected - got)
    n_not_ok_emitted = sum(1 for e in result.actions if e.detail in not_ok_ids)
    by_type: dict[str, int] = {}
    for e in result.actions:
        key = f"{type_of.get(e.detail, '?')}->{e.action_type.value}"
        by_type[key] = by_type.get(key, 0) + 1
    metrics: dict[str, object] = {
        "ts_column": ts_col, "n_tickers": len(tickers), "n_rows": len(rows),
        "n_ok": len(expected), "n_actions": len(got), "n_only_adapter": len(only_adapter),
        "n_only_factor": len(only_factor), "n_not_ok_emitted": n_not_ok_emitted,
        "by_type": by_type,
        "n_factor_outside_span": len(outside),
        "factor_outside_span": [f"{t} {d} {r} {e}" for t, d, r, e in outside[:SAMPLE_ROWS]],
        "only_adapter": [f"{t} {d} {r} {e}" for t, d, r, e in only_adapter[:SAMPLE_ROWS]],
        "only_factor": [f"{t} {d} {r} {e}" for t, d, r, e in only_factor[:SAMPLE_ROWS]]}
    ok = not only_adapter and not only_factor and n_not_ok_emitted == 0
    return GateResult("EGC-04", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"ratio = share_factor, ts = {ts_col}, {len(got)}건" if ok else
                      f"actions/adj_factor differ: only_adapter={len(only_adapter)} "
                      f"only_factor={len(only_factor)} not_ok_emitted={n_not_ok_emitted}",
                      metrics)


def egc05_halt_mix_query(ctx: _Ctx) -> GateResult:
    src = ctx.source("price_daily")
    halted = [str(r[0]) for r in ctx.rows(
        f"SELECT ticker, count(*) AS n FROM {src} WHERE price_kind = 'reference' "
        f"GROUP BY 1 ORDER BY n DESC, ticker LIMIT {HALT_TICKERS_N}")]
    if not halted:
        raise SkipGate("no_coverage", {"reason": "price_daily has no reference rows"})
    clean = [str(r[0]) for r in ctx.rows(
        f"SELECT ticker FROM {src} GROUP BY 1 "
        "HAVING count(*) FILTER (WHERE price_kind = 'reference') = 0 ORDER BY 1 LIMIT 1")]
    tickers = sorted(set(halted + clean))
    expect_dropped = 0
    for rows in _price_rows(ctx, tickers).values():
        _, ref, gap = _expected_bars(rows)
        expect_dropped += ref + gap
    result = _load_bars(ctx, tickers)
    metrics: dict[str, object] = {
        "tickers": tickers, "status": result.status.value, "detail": result.detail,
        "n_bars": len(result.bars), "dropped_rows": int(result.dropped_rows),
        "expected_dropped": expect_dropped, "repaired_rows": int(result.repaired_rows)}
    ok = result.ok and result.dropped_rows > 0 and result.dropped_rows == expect_dropped
    return GateResult("EGC-05", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"정지 섞인 {len(tickers)}종목 OK, dropped={result.dropped_rows}" if ok else
                      f"halt-mixed query: status={result.status.value} "
                      f"dropped={result.dropped_rows} expected={expect_dropped}", metrics)


def egc10_delisted_sample(ctx: _Ctx) -> GateResult:
    seed = int(str(ctx.constant(DELIST_SEED)))
    n = int(str(ctx.constant(DELIST_N)))
    ctx.source("price_daily")
    candidates = [f"{t}{ctx.mod.SECURITY_ID_SEP}{int(str(s))}" for t, s in ctx.rows(
        f"SELECT ticker, span_seq FROM {ctx.source('security_span')} "
        "WHERE end_reason = 'delisted' ORDER BY 1, 2")]
    if not candidates:
        raise SkipGate("no_coverage", {"reason": "no delisted span"})
    sample = sorted(random.Random(seed).sample(candidates, min(n, len(candidates))))
    result = _load_bars(ctx, sample)
    returned = sorted({b.instrument.symbol for b in result.bars})
    metrics: dict[str, object] = {
        "seed": seed, "n_requested": n, "n_candidates": len(candidates), "sample": sample,
        "status": result.status.value, "detail": result.detail, "n_bars": len(result.bars),
        "dropped_rows": int(result.dropped_rows), "returned": returned}
    ok = result.ok and returned == sample
    return GateResult("EGC-10", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"폐지 표본 {len(sample)}구간 OK, 반환 = 요청" if ok else
                      f"delisted sample: status={result.status.value} "
                      f"returned={len(returned)}/{len(sample)}", metrics)


GATES: tuple[tuple[str, Callable[[_Ctx], GateResult]], ...] = (
    ("EGC-01", egc01_bars_raw_price), ("EGC-02", egc02_universe_equals_listing),
    ("EGC-03", egc03_relisting_spans), ("EGC-04", egc04_actions_equal_factors),
    ("EGC-05", egc05_halt_mix_query), ("EGC-10", egc10_delisted_sample))


def _run_gate(name: str, fn: Callable[[_Ctx], GateResult], ctx: _Ctx) -> GateResult:
    try:
        return fn(ctx)
    except SkipGate as s:
        return GateResult(name, GateStatus.SKIP, s.reason, dict(s.metrics))


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def run(equity_root: Path, baseline: Baseline, *, engine_src: Path | None = None,
        venue: str = VENUE) -> ContractResult:
    """EG-C ①②③④⑤⑩ 을 돌리고 `_contract_meta.json` 을 쓴다. 상수 미등재는 skip(no_baseline)."""
    src = engine_src or default_engine_src()
    mod = load_adapter(src)
    builds = table_builds(equity_root)
    sid = snapshot_id(builds)
    con = duckdb.connect()
    try:
        ctx = _Ctx(equity_root, con, mod, baseline, venue,
                   {t: views.parquet_source(equity_root, t) for t in builds})
        results = [_run_gate(name, fn, ctx) for name, fn in GATES]
    finally:
        con.close()
    failed = [g for g in results if g.status is GateStatus.FAIL]
    payload = {"snapshot_id": sid, "builds": builds, "engine_src": str(src),
               "adapter": ADAPTER_MODULE, "venue": venue,
               "status": "fail" if failed else "pass",
               "gates": [g.as_dict() for g in results],
               "written_at_utc": datetime.now(UTC).isoformat(timespec="seconds")}
    meta_path = equity_root / META_NAME
    _write_json(meta_path, payload)
    report: Path | None = None
    if failed:
        report = equity_root / "_failed" / f"contract_{sid}.json"
        _write_json(report, {**payload, "first_failed_gate": failed[0].name})
    return ContractResult(not failed, sid, builds, src, results, meta_path, report)


__all__ = ["ADAPTER_MODULE", "GATES", "META_NAME", "VENUE", "ContractResult",
           "default_engine_src", "load_adapter", "run"]
