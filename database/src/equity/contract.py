"""EG-C 소비자 계약 게이트 ①②③④⑤⑩ — 제품이 읽는 워크벤치 어댑터를 equity_root 위에서 실행해
술어로 검사한다.

(EQUITY_GATES §1 EG-C · EQUITY_WORKFLOW S07 · DESIGN §7.) 대상은 제품 백테스트가 bar·구간·사건을
받는 워크벤치 equity 어댑터의 공개 facade 다 — `EquityDuckdbAdapter.load_backtest_dataset`·
`load_universe`. 전에는 제품이 쓰지 않는 커널 어댑터를 검사해 제품 경로가 게이트 밖이었다(#372).
각 항은 어댑터가 준 값을 **duckdb 로 같은 MANIFEST 파티션(또는 `_pinned/` stage 입력)을 독립으로
읽은 값**과 대조한다 — 어댑터 코드 경로를 재호출하는 항진명제가 아니다. 백테스트 질의의 끝은
backfill_end 이고 시작은 달력 첫 세션이다(④만 사건일부터).

  EGC-01  bar 원주가 동일성 — 표본 티커(`trading_calendar.asof_sample_tickers`) 전 구간 bar 의
          (종목 id, 세션, O/H/L/C/V) = `price_daily` 의 **고정 입력** `_pinned/stg_price_daily` ∪
          `stg_etf_price_daily` 행(어댑터가 읽는 산출이 아니라 stage 원주가, EG20 과 같은 축).
          거래량 0 행(기준가·정지)은 bar 가 없고, OHLC 가 NULL·0 이하이거나 서로 맞지 않는
          행(GAP-14 류)은 bar 대신 `invalid_bars` 에 같은 세션으로 실려야 한다.
  EGC-02  그날 구성원(`load_universe`) 티커 집합 = `_pinned/` stage `stg_listing_daily(d)` ∪
          `stg_etf_price_daily(d)` 티커 집합, d ∈ `universe_daily.contract_probe_dates`.
  EGC-03  재상장 종목(구간 ≥ 2) 수 = `security_span.respan_count` ∧ 그 종목들의 구간(Membership)
          = `security_span` 구간 ∧ bar 가 제 구간 밖에 없음 ∧ 구간 사이 공백 유지 ∧ 구간 첫날·
          끝날 구성원에 그 구간의 종목 id ∧ `coverage_gap` 구간이 backfill_end 구성원에 남음(끊지
          않음) ∧ backfill_end 를 넘는 질의 거절(NO_DATA) ∧ 끝 = backfill_end 허용.
  EGC-04  원장 계수 사건(detail = event_id) 집합 = `adj_factor` factor_ok 행 {(ticker, apply_date,
          share_factor, event_id)} — 그 종목 구간 안이고, 그 구간에 사건 세션이나 그 뒤의 유효
          거래가 있는 행만(없으면 엔진이 정산할 bar 가 없어 어댑터가 뺀다). not-ok 행은 원장
          계수 사건으로 방출하지 않는다. factor_ok 행이 있는 종목만 첫 사건일부터 부르고, 그
          창에 실린 원장 미접힘 층 이동(#369, detail 이 event_id 가 아닌 사건)은 지표로만 센다.
          (GATES 표의 "run 누적수익률 = 조정가 손계산" 은 엔진 run 이 필요해 S21/S22 MVP-B 몫.)
  EGC-05  기준가 행이 가장 많은 티커 5 + 기준가 없는 티커 1 의 다종목 질의 → 예외 없이 bar 가
          EGC-01 과 같은 축으로 맞고 기준가 행이 1 이상 빠진다.
  EGC-10  `security_span.end_reason='delisted'` 구간 중 `security.delist_sample_seed`·
          `delist_sample_n` 으로 고정 추출한 표본의 `{ticker}:{span_seq}` 전 구간 질의 → 예외 없음
          ∧ bar 를 낸 종목 id 집합 = 요청 집합.

`snapshot_id` 대조: 어댑터는 카탈로그 meta 의 `snapshot_id`(`equity.catalog.snapshot_id` 가 씀)를
자기 사본 규칙으로 다시 계산한 값과 대조해, 다르면 카탈로그를 낡은 것으로 보고 백테스트 데이터를
거절한다(`catalog_stale`). 그래서 두 사본이 갈리면 bar·사건 항(①③④⑤⑩)이 FAIL 한다.

경계: 어댑터는 같은 모노레포의 `backend/src` 에 산다. equity 패키지가 backend 를 import 하는 곳은
**이 모듈 하나**다 — `load_adapter(engine_src)` 가 그 경로를 `sys.path` 에 얹고 워크벤치 facade 만
import 한다. 기본 경로는 `<repo>/backend/src`(이 파일에서 4단계 위) 또는 `$QL_ENGINE_SRC`. 서버처럼
backend 체크아웃이 없는 곳은 `--engine-src` 로 준다(`deploy.sh` 가 `_engine/strategy_workbench` 를
민다). facade 는 import 때 제3자 패키지를 읽지 않고 어댑터가 duckdb 만 지연 import 한다. 엔진이
없거나 어댑터를 세울 수 없으면(필수 테이블 미빌드 등) skip 이 아니라 예외다(GATES §9 A13: 같은
모노레포라 `skip(engine_absent)` 를 두지 않는다). 사건은 카탈로그 뷰 `v_unfolded_event` 를 함께
읽어 catalog 단계 뒤에 돈다.

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
from typing import Any

import duckdb
from stage import manifest
from stage.gates import GateResult, GateStatus

from . import inputs, views
from .baseline import Baseline
from .catalog import connect as duckdb_connect
from .catalog import snapshot_id, table_builds
from .gates import SkipGate
from .model import PRICE_BASIS_KRX

META_NAME = "_contract_meta.json"
ADAPTER_MODULE = "strategy_workbench.adapters.outbound.equity_duckdb.facade.provider"
PORTS_MODULE = "strategy_workbench.application.backtest_run.facade.ports"
UNIVERSE_MODULE = "strategy_workbench.domain.equity.facade.research_data"
VENUE = "XKRX"                        # FIELD_MAP §1 — 워크벤치 유니버스 `venue`
SECURITY_ID_SEP = ":"                 # FIELD_MAP §1 — 종목 id = `{ticker}:{span_seq}`
SAMPLE_TICKERS = ("trading_calendar", "asof_sample_tickers")
PROBE_DATES = ("universe_daily", "contract_probe_dates")
RESPAN_COUNT = ("security_span", "respan_count")
DELIST_SEED = ("security", "delist_sample_seed")
DELIST_N = ("security", "delist_sample_n")
HALT_TICKERS_N = 5                    # EGC-05 — reference 행 최다 티커 수
ACTION_BATCH = 100                    # EGC-04 — 한 질의의 티커 수(전 구간 bar 를 함께 읽는다)
SAMPLE_ROWS = 10                      # metrics 에 남기는 불일치 표본 수
ENGINE_SRC_ENV = "QL_ENGINE_SRC"


def default_engine_src() -> Path:
    """`$QL_ENGINE_SRC` 또는 `<repo>/backend/src`(database/src/equity 에서 3단계 위)."""
    env = os.environ.get(ENGINE_SRC_ENV)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "backend" / "src"


@dataclass(frozen=True)
class Workbench:
    """게이트가 부르는 워크벤치 facade 셋."""

    provider: ModuleType  # EquityDuckdbAdapter
    ports: ModuleType     # BacktestDataQuery
    universe: ModuleType  # UniverseHistoryQuery · DataLoadStatus


def load_adapter(engine_src: Path) -> Workbench:
    """backend 소스 루트를 sys.path 에 얹고 워크벤치 facade 를 import 한다 — equity → backend 유일
    경계."""
    marker = engine_src.joinpath(*ADAPTER_MODULE.split(".")).with_suffix(".py")
    if not marker.exists():
        raise FileNotFoundError(
            f"engine adapter not found — engine_src={engine_src} expected={marker} "
            f"(--engine-src 또는 ${ENGINE_SRC_ENV} 로 backend/src 를 지정)")
    root = str(engine_src)
    if root not in sys.path:
        sys.path.insert(0, root)
    return Workbench(importlib.import_module(ADAPTER_MODULE),
                     importlib.import_module(PORTS_MODULE),
                     importlib.import_module(UNIVERSE_MODULE))


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
    wb: Workbench | None
    adapter: Any                        # EquityDuckdbAdapter — 타입이 backend 쪽이라 묶지 않는다
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

    def workbench(self) -> Workbench:
        if self.wb is None:
            raise RuntimeError("contract context has no workbench facade")
        return self.wb

    def calendar(self) -> tuple[date, date]:
        """(첫 세션, backfill_end) — 백테스트 질의의 전 구간."""
        lo, hi = self.one(f"SELECT min(date), max(date) FROM {self.source('trading_calendar')}")
        return _as_date(lo), _as_date(hi)

    def dataset(self, security_ids: list[str], start: date | None = None) -> tuple[Any, str | None]:
        """`start`(기본 첫 세션) ~ backfill_end 백테스트 데이터(`BacktestDataset`)와 오류 문장.
        포트·원장 모순 예외는 문장으로 돌려 항이 FAIL 로 남긴다. 부를 종목이 없으면 (None, None)."""
        if not security_ids:
            return None, None
        first, end = self.calendar()
        query = self.workbench().ports.BacktestDataQuery(
            first if start is None else max(start, first), end, tuple(security_ids), None)
        try:
            return self.adapter.load_backtest_dataset(query), None
        except (ValueError, RuntimeError) as e:  # 모르는 id·준비 안 됨(낡은 카탈로그)·원장 모순
            return None, f"{type(e).__name__}: {e}"

    def universe(self, start: date, end: date) -> Any:
        """`load_universe` 결과(`UniverseHistoryResult`) — 실패는 status 값으로 온다."""
        return self.adapter.load_universe(self.workbench().universe.UniverseHistoryQuery(
            self.venue, start, end))

    def status(self, name: str) -> object:
        """`DataLoadStatus.<name>`."""
        return getattr(self.workbench().universe.DataLoadStatus, name)


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


def confirmed_source(ctx: _Ctx, table: str) -> str:
    """산출 테이블의 **확정 행만** 읽는 관계식. `basis` 열이 없으면 표 그대로.

    워크벤치 어댑터는 `basis <> 'krx'` 행을 bar 로 내지 않는다(저녁 잠정 행은 확정 전 값이라
    백테스트 바에 못 넣는다 — `equity.provisional_rows_dropped`). 계약의 대조축이 같은 필터를 안
    걸면 저녁 판이 current 인 10시간 40분 동안(평일 22:40 ~ 다음날 09:20) 한쪽만 잠정 행을 세게
    되고, `price_kind='reference'` 집계로 표본을 고르는 EGC-05 는 그때마다 다른 종목을
    고른다(DEFECT-C05, 2026-09-19 감사). `basis` 는 규칙 e1.15.0 부터 있으므로 옛 판에는 없다 —
    그때는 필터 없이 읽어 계약이 옛 산출에서도 선다.
    """
    src = ctx.source(table)
    cols = {str(r[0]) for r in ctx.rows(f"DESCRIBE SELECT * FROM {src}")}
    if "basis" not in cols:
        return src
    return f"(SELECT * FROM {src} WHERE basis = '{PRICE_BASIS_KRX}')"


Span = tuple[int, date, date, str]      # (span_seq, first_date, last_date, end_reason)


def _spans(ctx: _Ctx, tickers: list[str] | None = None) -> dict[str, list[Span]]:
    """`security_span` 을 독립으로 읽은 티커 → 구간(span_seq 순)."""
    where = f"WHERE ticker IN ({_lit(tickers)}) " if tickers is not None else ""
    out: dict[str, list[Span]] = {}
    for t, seq, first, last, reason in ctx.rows(
            f"SELECT ticker, span_seq, first_date, last_date, end_reason "
            f"FROM {ctx.source('security_span')} {where}ORDER BY 1, 2"):
        out.setdefault(str(t), []).append(
            (int(str(seq)), _as_date(first), _as_date(last), str(reason)))
    return out


def _sid(ticker: str, span_seq: int) -> str:
    return f"{ticker}{SECURITY_ID_SEP}{span_seq}"


def _security_ids(spans: dict[str, list[Span]]) -> list[str]:
    return [_sid(t, s[0]) for t, v in sorted(spans.items()) for s in v]


def _valid_ohlc(o: object, h: object, lo: object, c: object) -> bool:
    """bar 로 낼 수 있는 OHLC — 전부 있고 양수이며 고가 ≥ max(시가, 종가) ∧ 저가 ≤ min(시가, 종가).
    제품 어댑터가 bar 와 `invalid_bars` 를 가르는 규칙이다."""
    if o is None or h is None or lo is None or c is None:
        return False
    fo, fh, fl, fc = (float(str(p)) for p in (o, h, lo, c))
    return min(fo, fh, fl, fc) > 0 and fh >= max(fo, fc) and fl <= min(fo, fc)


BarKey = tuple[str, date]               # (종목 id, 세션)


@dataclass
class _Expected:
    """고정 stage 원주가에서 읽은, 어댑터가 내야 할 bar 와 빼야 할 행."""

    bars: dict[BarKey, tuple[object, ...]] = field(default_factory=dict)  # → (o, h, l, c, v)
    invalid: set[BarKey] = field(default_factory=set)    # 거래됐지만 OHLC 무효(GAP-14 류)
    n_reference: int = 0                                  # 거래량 0 — 기준가·정지일
    n_outside_span: int = 0                               # 어느 구간에도 들지 않는 행


def _expected_bars(ctx: _Ctx, tickers: list[str], spans: dict[str, list[Span]]) -> _Expected:
    """`stg_price_daily` ∪ `stg_etf_price_daily` 의 캘린더 안 행(`_reject/off_calendar` 축)을
    어댑터 규칙으로 가른다. 거래량 NULL 행은 기준가가 아니라(`price_kind` NULL) OHLC 로만 가른다.
    """
    cols = "ticker, date, open_krw, high_krw, low_krw, close_krw, volume_shr"
    stock = _pinned_source(ctx, "price_daily", "stg_price_daily")
    etf = _pinned_source(ctx, "price_daily", "stg_etf_price_daily")
    out = _Expected()
    for t, d, o, h, lo, c, v in ctx.rows(
            f"SELECT {cols} FROM (SELECT {cols} FROM {stock} UNION ALL SELECT {cols} FROM {etf}) "
            f"WHERE ticker IN ({_lit(tickers)}) "
            f"AND date IN (SELECT date FROM {ctx.source('trading_calendar')}) ORDER BY 1, 2"):
        session = _as_date(d)
        seq = next((s[0] for s in spans.get(str(t), []) if s[1] <= session <= s[2]), None)
        if seq is None:
            out.n_outside_span += 1
            continue
        key = (_sid(str(t), seq), session)
        if v is not None and int(str(v)) == 0:
            out.n_reference += 1
        elif not _valid_ohlc(o, h, lo, c):
            out.invalid.add(key)
        else:
            out.bars[key] = (*(float(str(p)) for p in (o, h, lo, c)),
                             None if v is None else int(str(v)))
    return out


def _bar_check(ctx: _Ctx, tickers: list[str]) -> tuple[dict[str, object], bool]:
    """`tickers` 전 구간 bar·무효 bar 를 고정 stage 원주가와 대조한 지표와 통과 여부."""
    spans = _spans(ctx, tickers)
    exp = _expected_bars(ctx, tickers, spans)
    dataset, error = ctx.dataset(_security_ids(spans))
    got: dict[BarKey, tuple[object, ...]] = {}
    got_invalid: set[BarKey] = set()
    if dataset is not None:
        got = {(b.security_id, b.session): (b.open, b.high, b.low, b.close, b.volume)
               for b in dataset.bars}
        got_invalid = {(r.security_id, r.session) for r in dataset.invalid_bars}
    mismatch = sorted(k for k in got.keys() & exp.bars.keys() if got[k] != exp.bars[k])
    missing, extra = sorted(exp.bars.keys() - got.keys()), sorted(got.keys() - exp.bars.keys())
    invalid_diff = sorted(got_invalid ^ exp.invalid)
    spanless = sorted(set(tickers) - set(spans))
    samples = [f"{k[0]} {k[1]} adapter={got[k]} price_daily={exp.bars[k]}" for k in mismatch]
    samples += [f"{kind} {k[0]} {k[1]}" for kind, keys in
                (("missing", missing), ("extra", extra), ("invalid", invalid_diff))
                for k in keys]
    metrics: dict[str, object] = {
        "n_tickers": len(tickers), "tickers": tickers, "error": error,
        "n_bars": len(got), "n_expected": len(exp.bars), "n_mismatch": len(mismatch),
        "n_missing": len(missing), "n_extra": len(extra), "n_reference": exp.n_reference,
        "n_invalid": len(got_invalid), "n_invalid_expected": len(exp.invalid),
        "n_invalid_diff": len(invalid_diff), "n_outside_span": exp.n_outside_span,
        "spanless_tickers": spanless, "samples": samples[:SAMPLE_ROWS]}
    # 구간이 하나도 없는 표본 티커는 원장이 종목을 잃은 것이다 — 어댑터가 부를 id 조차 없다
    ok = error is None and not (mismatch or missing or extra or invalid_diff or spanless)
    return metrics, ok


# ── 항별 술어 ────────────────────────────────────────────────────────────────

def egc01_bars_raw_price(ctx: _Ctx) -> GateResult:
    raw = ctx.constant(SAMPLE_TICKERS)
    tickers = sorted(str(t) for t in raw) if isinstance(raw, list) else []
    if not tickers:
        raise SkipGate("no_baseline", {"missing_metric": ".".join(SAMPLE_TICKERS)})
    m, ok = _bar_check(ctx, tickers)
    return GateResult("EGC-01", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"bars 원주가 동일(표본 {len(tickers)}티커, {m['n_bars']}행)" if ok else
                      f"bar/price_daily mismatch: error={m['error']} mismatch={m['n_mismatch']} "
                      f"missing={m['n_missing']} extra={m['n_extra']} "
                      f"invalid_diff={m['n_invalid_diff']}", m)


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


def _members(result: Any, d: date) -> set[str]:
    """`load_universe` 결과에서 세션 d 구성원의 종목 id."""
    return {m.security_id for p in result.points if p.session == d for m in p.members}


def egc02_universe_equals_listing(ctx: _Ctx) -> GateResult:
    raw = ctx.constant(PROBE_DATES)
    dates = sorted(_as_date(d) for d in raw) if isinstance(raw, list) and raw else []
    if not dates:
        raise SkipGate("no_baseline", {"missing_metric": ".".join(PROBE_DATES)})
    ctx.source("security_span")
    listing = _pinned_existence(ctx, dates)
    per_date: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    n_diff = 0
    for d in dates:     # 전 기간을 한 번에 부르면 날마다 전 종목이 실려 탐침 날짜만 부른다
        result = ctx.universe(d, d)
        if result.status is not ctx.status("OK"):
            failures.append(f"{d}: {result.status.value} {result.detail}")
            continue
        got = {sid.partition(SECURITY_ID_SEP)[0] for sid in _members(result, d)}
        only_adapter, only_listing = sorted(got - listing[d]), sorted(listing[d] - got)
        n_diff += len(only_adapter) + len(only_listing)
        per_date[d.isoformat()] = {
            "n_adapter": len(got), "n_listing": len(listing[d]),
            "only_adapter": only_adapter[:SAMPLE_ROWS], "only_listing": only_listing[:SAMPLE_ROWS]}
    metrics: dict[str, object] = {"n_dates": len(dates), "n_diff": n_diff, "per_date": per_date,
                                  "failures": failures[:SAMPLE_ROWS]}
    ok = n_diff == 0 and not failures
    return GateResult("EGC-02", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"members(d) = listing(d) ∪ etf(d), {len(dates)}일" if ok else
                      f"universe/listing differ: n_diff={n_diff} failures={len(failures)}",
                      metrics)


def egc03_relisting_spans(ctx: _Ctx) -> GateResult:
    expected_respan = int(str(ctx.constant(RESPAN_COUNT)))
    spans = _spans(ctx)
    backfill_end = ctx.calendar()[1]
    respan = {t: v for t, v in spans.items() if len(v) >= 2}
    # 재상장 종목만 전 구간을 불러 Membership 을 구간과 대조한다 — 전 종목은 원장 전체 bar 다
    dataset, error = ctx.dataset(_security_ids(respan))
    got: dict[str, list[tuple[int, date, date]]] = {}
    n_bar_outside = 0
    if dataset is not None:
        for m in dataset.memberships:
            ticker, _, seq = m.security_id.partition(SECURITY_ID_SEP)
            got.setdefault(ticker, []).append((int(seq), m.first_session, m.last_session))
        bounds = {_sid(t, s): (a, b) for t, v in got.items() for s, a, b in v}
        for bar in dataset.bars:
            first, last = bounds.get(bar.security_id, (date.max, date.min))
            n_bar_outside += not first <= bar.session <= last
    for v in got.values():
        v.sort()
    n_span_mismatch = sum(1 for t, v in respan.items() if [s[:3] for s in v] != got.get(t))
    n_gap_broken = sum(1 for v in got.values()
                       for a, b in zip(v, v[1:], strict=False) if not a[2] < b[1])
    # 구성원의 종목 id 가 구간을 따른다 — 재상장 구간의 첫날·끝날 구성원에 그 구간 id 가 있다
    probes = sorted({(d, _sid(t, s)) for t, v in respan.items() for s, a, b, _ in v
                     for d in (a, b)})
    members = {d: _members(ctx.universe(d, d), d) for d in sorted({d for d, _ in probes})}
    n_member_mismatch = sum(1 for d, sid in probes if sid not in members[d])
    allowed = ctx.universe(backfill_end, backfill_end)
    last = _members(allowed, backfill_end)
    cov = [(_sid(t, s), b) for t, v in spans.items() for s, _, b, reason in v
           if reason == "coverage_gap"]
    n_cov_mismatch = sum(1 for sid, b in cov if b != backfill_end or sid not in last)
    rejected = ctx.universe(backfill_end, backfill_end + timedelta(days=1))
    reject_ok = (rejected.status is ctx.status("NO_DATA")
                 and "outside coverage" in str(rejected.detail or ""))
    allow_ok = allowed.status is ctx.status("OK")
    metrics: dict[str, object] = {
        "expected_respan_count": expected_respan, "n_respan": len(respan),
        "respan": {t: [(s, a.isoformat(), b.isoformat()) for s, a, b in v]
                   for t, v in sorted(got.items())},
        "error": error, "n_tickers": len(spans), "n_span_mismatch": n_span_mismatch,
        "n_bar_outside_span": n_bar_outside, "n_gap_broken": n_gap_broken,
        "n_member_probes": len(probes), "n_member_mismatch": n_member_mismatch,
        "n_coverage_gap_spans": len(cov), "n_coverage_gap_mismatch": n_cov_mismatch,
        "backfill_end": backfill_end.isoformat(),
        "reject_status": rejected.status.value, "reject_detail": rejected.detail,
        "allow_status": allowed.status.value}
    ok = (len(respan) == expected_respan and error is None and n_span_mismatch == 0
          and n_bar_outside == 0 and n_gap_broken == 0 and n_member_mismatch == 0
          and n_cov_mismatch == 0 and reject_ok and allow_ok)
    return GateResult("EGC-03", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"재상장 {len(respan)}종 구간 대조·coverage_gap 유지·backfill_end 거절"
                      if ok else
                      f"relisting contract broken: respan={len(respan)}/{expected_respan} "
                      f"error={error} span_mismatch={n_span_mismatch} "
                      f"bar_outside={n_bar_outside} gap_broken={n_gap_broken} "
                      f"member_mismatch={n_member_mismatch} "
                      f"coverage_gap_mismatch={n_cov_mismatch} reject_ok={reject_ok} "
                      f"allow_ok={allow_ok}", metrics)


def _last_valid_sessions(ctx: _Ctx, tickers: list[str]) -> dict[tuple[str, int], date]:
    """(ticker, span_seq) → 그 구간의 마지막 유효 거래 세션 — 확정 `price_daily` 에서 읽는다.
    유효 = 거래 행(`price_kind='trade'`) ∧ `_valid_ohlc` 와 같은 조건. 비교 모집단을 가르는 데만
    쓴다(값 대조는 EGC-01)."""
    rows = ctx.rows(
        f"SELECT p.ticker, s.span_seq, max(p.date) FROM {confirmed_source(ctx, 'price_daily')} p "
        f"JOIN {ctx.source('security_span')} s "
        f"  ON s.ticker = p.ticker AND p.date BETWEEN s.first_date AND s.last_date "
        f"WHERE p.ticker IN ({_lit(tickers)}) AND p.price_kind = 'trade' "
        f"  AND p.open IS NOT NULL AND p.high IS NOT NULL "
        f"  AND p.low IS NOT NULL AND p.close IS NOT NULL "
        f"  AND least(p.open, p.high, p.low, p.close) > 0 "
        f"  AND p.high >= greatest(p.open, p.close) AND p.low <= least(p.open, p.close) "
        f"GROUP BY 1, 2")
    return {(str(t), int(str(s))): _as_date(d) for t, s, d in rows}


Factor = tuple[str, date, float, str]   # (ticker, apply_date, share_factor, event_id)


def egc04_actions_equal_factors(ctx: _Ctx) -> GateResult:
    src = ctx.source("adj_factor")
    rows = ctx.rows(f"SELECT ticker, event_id, event_type, apply_date, share_factor, factor_ok "
                    f"FROM {src} ORDER BY 1, 2")
    first_event: dict[str, date] = {}
    for t, _, _, d, _, ok in rows:
        if ok is True:
            first_event[str(t)] = min(first_event.get(str(t), date.max), _as_date(d))
    tickers = sorted(first_event, key=lambda t: (first_event[t], t))
    if not tickers:
        return GateResult("EGC-04", GateStatus.PASS, "adj_factor factor_ok 0행 — 방출할 계수 없음",
                          {"n_rows": len(rows), "n_ok": 0, "n_actions": 0})
    spans = _spans(ctx, tickers)
    last_valid = _last_valid_sessions(ctx, tickers)
    # 어댑터는 사건을 그 종목 구간 안에서, 사건 세션이나 그 뒤에 거래된 bar 가 있을 때만 싣는다 —
    # 커널은 구간 밖에 포지션을 가질 수 없고 뒤에 bar 가 없으면 정산할 세션이 없다. 두 부류는
    # 비교 모집단에서 빼고 건수만 남긴다(서버 실측 09-05: 구간 밖 5건, 예 000360 2018-08-09 감자).
    expected: set[Factor] = set()
    outside: list[Factor] = []
    unsettleable: list[Factor] = []
    for t, eid, _, d, sf, ok in rows:
        if ok is not True:
            continue
        item = (str(t), _as_date(d), float(str(sf)), str(eid))
        seq = next((s[0] for s in spans.get(item[0], []) if s[1] <= item[1] <= s[2]), None)
        last = None if seq is None else last_valid.get((item[0], seq))
        if seq is None:
            outside.append(item)
        elif last is None or last < item[1]:
            unsettleable.append(item)
        else:
            expected.add(item)
    type_of = {str(r[1]): str(r[2]) for r in rows}
    not_ok_ids = {str(r[1]) for r in rows if r[5] is not True}
    got: set[Factor] = set()
    by_type: dict[str, int] = {}
    n_unfolded = 0
    failures: list[str] = []
    # 첫 사건일 순으로 묶어 배치의 첫 사건일부터 읽는다 — 사건을 싣는지는 그날 이후 bar 만으로
    # 정해져 앞 bar 를 읽을 까닭이 없다(실원장 읽는 행 약 4.0M → 2.3M)
    for i in range(0, len(tickers), ACTION_BATCH):
        batch = {t: spans[t] for t in tickers[i:i + ACTION_BATCH] if t in spans}
        dataset, error = ctx.dataset(_security_ids(batch), start=first_event[tickers[i]])
        if error is not None:
            failures.append(error)
        for a in dataset.corporate_actions if dataset is not None else ():
            if a.detail not in type_of:   # 원장이 접지 못한 층 이동(#369) — 계수 행이 아니다
                n_unfolded += 1
                continue
            got.add((a.security_id.partition(SECURITY_ID_SEP)[0], a.session, float(a.ratio),
                     a.detail))
            key = f"{type_of[a.detail]}->{a.action_type}"
            by_type[key] = by_type.get(key, 0) + 1
    only_adapter, only_factor = sorted(got - expected), sorted(expected - got)
    n_not_ok_emitted = sum(1 for *_, e in got if e in not_ok_ids)

    def fmt(items: list[Factor]) -> list[str]:
        return [f"{t} {d} {r} {e}" for t, d, r, e in items[:SAMPLE_ROWS]]

    metrics: dict[str, object] = {
        "n_tickers": len(tickers), "n_rows": len(rows),
        "n_ok": len(expected), "n_actions": len(got), "n_only_adapter": len(only_adapter),
        "n_only_factor": len(only_factor), "n_not_ok_emitted": n_not_ok_emitted,
        "n_unfolded": n_unfolded, "by_type": by_type,
        "n_factor_outside_span": len(outside), "factor_outside_span": fmt(outside),
        "n_factor_without_bar_after": len(unsettleable),
        "factor_without_bar_after": fmt(unsettleable),
        "only_adapter": fmt(only_adapter), "only_factor": fmt(only_factor),
        "failures": failures[:SAMPLE_ROWS]}
    ok = not (only_adapter or only_factor or n_not_ok_emitted or failures)
    return GateResult("EGC-04", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"ratio = share_factor, 세션 = apply_date, {len(got)}건" if ok else
                      f"actions/adj_factor differ: only_adapter={len(only_adapter)} "
                      f"only_factor={len(only_factor)} not_ok_emitted={n_not_ok_emitted} "
                      f"failures={len(failures)}", metrics)


def egc05_halt_mix_query(ctx: _Ctx) -> GateResult:
    # 잠정 행을 세면 `price_kind='reference'` 상위 티커가 저녁 판마다 달라진다(DEFECT-C05)
    src = confirmed_source(ctx, "price_daily")
    halted = [str(r[0]) for r in ctx.rows(
        f"SELECT ticker, count(*) AS n FROM {src} WHERE price_kind = 'reference' "
        f"GROUP BY 1 ORDER BY n DESC, ticker LIMIT {HALT_TICKERS_N}")]
    if not halted:
        raise SkipGate("no_coverage", {"reason": "price_daily has no reference rows"})
    clean = [str(r[0]) for r in ctx.rows(
        f"SELECT ticker FROM {src} GROUP BY 1 "
        "HAVING count(*) FILTER (WHERE price_kind = 'reference') = 0 ORDER BY 1 LIMIT 1")]
    tickers = sorted(set(halted + clean))
    m, ok = _bar_check(ctx, tickers)
    ok = ok and m["n_reference"] != 0
    return GateResult("EGC-05", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"정지 섞인 {len(tickers)}종목 OK, 기준가 {m['n_reference']}행 제외"
                      if ok else
                      f"halt-mixed query: error={m['error']} bars={m['n_bars']}/{m['n_expected']} "
                      f"reference={m['n_reference']} invalid_diff={m['n_invalid_diff']}", m)


def egc10_delisted_sample(ctx: _Ctx) -> GateResult:
    seed = int(str(ctx.constant(DELIST_SEED)))
    n = int(str(ctx.constant(DELIST_N)))
    ctx.source("price_daily")
    candidates = [_sid(str(t), int(str(s))) for t, s in ctx.rows(
        f"SELECT ticker, span_seq FROM {ctx.source('security_span')} "
        "WHERE end_reason = 'delisted' ORDER BY 1, 2")]
    if not candidates:
        raise SkipGate("no_coverage", {"reason": "no delisted span"})
    sample = sorted(random.Random(seed).sample(candidates, min(n, len(candidates))))
    dataset, error = ctx.dataset(sample)
    returned = sorted({b.security_id for b in dataset.bars}) if dataset is not None else []
    metrics: dict[str, object] = {
        "seed": seed, "n_requested": n, "n_candidates": len(candidates), "sample": sample,
        "error": error, "n_bars": len(dataset.bars) if dataset is not None else 0,
        "returned": returned}
    ok = error is None and returned == sample
    return GateResult("EGC-10", GateStatus.PASS if ok else GateStatus.FAIL,
                      f"폐지 표본 {len(sample)}구간 OK, 반환 = 요청" if ok else
                      f"delisted sample: error={error} returned={len(returned)}/{len(sample)}",
                      metrics)


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
    wb = load_adapter(src)
    builds = table_builds(equity_root)
    sid = snapshot_id(builds)
    adapter = wb.provider.EquityDuckdbAdapter(equity_root)
    # 자원 제한은 카탈로그 단계와 같은 값을 쓴다(DEFECT-C04)
    con = duckdb_connect()
    try:
        ctx = _Ctx(equity_root, con, wb, adapter, baseline, venue,
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


__all__ = ["ADAPTER_MODULE", "GATES", "META_NAME", "VENUE", "ContractResult", "Workbench",
           "confirmed_source", "default_engine_src", "load_adapter", "run"]
