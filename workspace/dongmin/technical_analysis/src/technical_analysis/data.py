"""연구 DB(equity 층) 읽기 — 종목·지수 찾기와 일봉 불러오기.

표 폴더에는 옛 판(v=<build>/)이 같이 남아 있어 전부 읽으면 날짜가 겹친다. 표마다 MANIFEST.json 의
`current_build` 판만 읽는다. 연구 DB 위치는 환경 변수 `TA_EQUITY_ROOT`, 없으면 `~/quant-ledger/data/equity`.
"""

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

import duckdb
import numpy as np

from .signals import Bars

Kind = Literal["stock", "index"]
SEC_TYPE_ORDER = {"common": 0, "preferred": 1, "etf": 2}


def equity_root() -> Path:
    """연구 DB 위치."""
    return Path(os.environ.get("TA_EQUITY_ROOT", "~/quant-ledger/data/equity")).expanduser()


def current_files(root: Path, table: str) -> list[str]:
    """표의 current_build 판 parquet 파일 목록."""
    manifest_path = root / table / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    build_id = manifest["current_build"]
    builds = [b for b in manifest["builds"] if b["build_id"] == build_id]
    if not builds:
        raise RuntimeError(f"MANIFEST 에 current_build 판이 없다: table={table} current_build={build_id} path={manifest_path}")
    files: list[str] = []
    for part in builds[0]["partitions"]:
        files.extend(sorted(str(p) for p in (root / table / part["path"]).glob("*.parquet")))
    if not files:
        raise RuntimeError(f"current_build 판에 parquet 이 없다: table={table} build={build_id}")
    return files


@dataclass(frozen=True)
class Match:
    kind: Kind
    code: str  # 종목은 티커, 지수는 지수명
    name: str
    sec_type: str = ""


class LookupStatus(Enum):
    OK = "ok"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class LookupResult:
    status: LookupStatus
    query: str
    match: Match | None = None
    candidates: list[Match] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is LookupStatus.OK


def _securities(con: duckdb.DuckDBPyConnection, root: Path) -> list[Match]:
    rows = con.execute(
        "SELECT ticker, name_abbrv_current, name_current, sec_type FROM read_parquet(?)",
        [current_files(root, "security")],
    ).fetchall()
    return [Match("stock", str(t), str(abbr or full), str(st or "")) for t, abbr, full, st in rows] + [
        Match("stock", str(t), str(full), str(st or "")) for t, abbr, full, st in rows if full and full != abbr
    ]


def _index_names(con: duckdb.DuckDBPyConnection, root: Path) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT index_name FROM read_parquet(?)", [current_files(root, "index_daily")]
    ).fetchall()
    return sorted(str(r[0]) for r in rows)


def _unique(matches: list[Match]) -> list[Match]:
    seen: dict[tuple[str, str], Match] = {}
    for m in matches:
        seen.setdefault((m.kind, m.code), m)
    return sorted(seen.values(), key=lambda m: (m.kind != "index", SEC_TYPE_ORDER.get(m.sec_type, 9), m.name))


def lookup(root: Path, query: str) -> LookupResult:
    """이름 또는 코드로 종목·지수를 찾는다.

    순서: 지수명 정확히 → 티커 → 종목명(약칭·정식) 정확히 → 이름에 포함. 포함 검색에서 여럿이면 AMBIGUOUS.
    """
    q = query.strip()
    con = duckdb.connect()
    indices = _index_names(con, root)
    if q in indices:
        return LookupResult(LookupStatus.OK, q, Match("index", q, q))
    secs = _securities(con, root)
    by_code = [m for m in secs if m.code == q]
    if by_code:
        return LookupResult(LookupStatus.OK, q, _unique(by_code)[0])
    exact = _unique([m for m in secs if m.name == q])
    if exact:
        return LookupResult(LookupStatus.OK, q, exact[0])
    partial = _unique([Match("index", n, n) for n in indices if q in n] + [m for m in secs if q in m.name])
    if len(partial) == 1:
        return LookupResult(LookupStatus.OK, q, partial[0])
    if partial:
        return LookupResult(LookupStatus.AMBIGUOUS, q, None, partial[:20])
    return LookupResult(LookupStatus.NOT_FOUND, q)


@dataclass(frozen=True, eq=False)
class Series:
    match: Match
    dates: np.ndarray  # datetime64[D]
    bars: Bars
    price_scale: float = 1.0  # 원장 수정주가 × 이 값 = 차트 가격(마지막 봉 = 실제 종가)


def _to_series(match: Match, rows: list[tuple]) -> Series:
    """가격이 빈 행(거래정지일 등)은 뺀다 — 봉이 없는 날이고, 남기면 평활 지표가 그 뒤로 끊긴다."""
    rows = [r for r in rows if all(v is not None for v in r[1:5])]
    if not rows:
        return Series(match, np.array([], dtype="datetime64[D]"), Bars(*(np.array([]) for _ in range(5))))
    cols = list(zip(*rows, strict=True))
    dates = np.array(cols[0], dtype="datetime64[D]")
    arr = [np.array([np.nan if v is None else float(v) for v in c]) for c in cols[1:]]
    return Series(match, dates, Bars(open=arr[0], high=arr[1], low=arr[2], close=arr[3], volume=arr[4]))


def _rescale_to_raw(con: duckdb.DuckDBPyConnection, root: Path, s: Series) -> Series:
    """전방 조정 수정주가를 마지막 봉 = 실제 종가가 되게 한 배율로 맞춘다(비율·수익률은 그대로)."""
    if s.dates.shape[0] == 0:
        return s
    raw = con.execute(
        "SELECT close, volume_shr FROM read_parquet(?) WHERE ticker = ? AND date = ?",
        [current_files(root, "price_daily"), s.match.code, s.dates[-1].astype(object)],
    ).fetchone()
    if raw is None or raw[0] is None:
        return s
    b = s.bars
    p = float(raw[0]) / float(b.close[-1])
    v = float(raw[1]) / float(b.volume[-1]) if raw[1] and b.volume[-1] > 0 else 1.0
    bars = Bars(open=b.open * p, high=b.high * p, low=b.low * p, close=b.close * p, volume=b.volume * v)
    return Series(s.match, s.dates, bars, price_scale=p)


@dataclass(frozen=True, eq=False)
class Extras:
    """최근 수급·공매도·신용(종목, 또는 코스피·코스닥 시장 합계)."""

    dates: np.ndarray
    foreign_krw: np.ndarray  # 외국인 순매수(원)
    institution_krw: np.ndarray  # 기관 순매수(원)
    mktcap_krw: np.ndarray  # 시가총액(원, 시장 합계는 종목 합)
    short_weight_pct: np.ndarray  # 공매도 거래 비중(%) — 시장 합계에선 NaN
    lending_shr: np.ndarray  # 대차잔고(주) — 시장 합계에선 NaN
    credit_rate_pct: np.ndarray  # 신용잔고율(%) — 시장 합계에선 NaN
    credit_shr: np.ndarray  # 신용잔고(주) — 시장 합계에선 NaN
    scope: str  # "종목" / "시장 합계(코스피)" 등


MARKET_OF_INDEX = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}


def _col(rows: list[tuple], i: int) -> np.ndarray:
    return np.array([np.nan if r[i] is None else float(r[i]) for r in rows])


def load_extras(root: Path, match: Match, since: str) -> Extras | None:
    """since(YYYY-MM-DD) 이후 수급·공매도·신용. 코스피·코스닥 밖의 지수는 None(구성 종목 표가 없다)."""
    con = duckdb.connect()
    flow = current_files(root, "flow_daily")
    price = current_files(root, "price_daily")
    if match.kind == "index":
        market = MARKET_OF_INDEX.get(match.code)
        if market is None:
            return None
        rows = con.execute(
            """WITH u AS (SELECT date, ticker FROM read_parquet(?) WHERE date >= ? AND market = ?)
               SELECT f.date, sum(f.frgnr_invsr_krw), sum(f.orgn_krw), sum(p.mktcap_krw)
               FROM read_parquet(?) f JOIN u USING (date, ticker)
               LEFT JOIN (SELECT date, ticker, mktcap_krw FROM read_parquet(?) WHERE date >= ?) p USING (date, ticker)
               WHERE f.date >= ? AND f.src = 'kiwoom' GROUP BY f.date ORDER BY f.date""",
            [current_files(root, "universe_daily"), since, market, flow, price, since, since],
        ).fetchall()
        nan = np.full(len(rows), np.nan)
        return Extras(
            dates=np.array([r[0] for r in rows], dtype="datetime64[D]"),
            foreign_krw=_col(rows, 1),
            institution_krw=_col(rows, 2),
            mktcap_krw=_col(rows, 3),
            short_weight_pct=nan,
            lending_shr=nan,
            credit_rate_pct=nan,
            credit_shr=nan,
            scope=f"시장 합계({match.code})",
        )
    rows = con.execute(
        """SELECT p.date, f.frgnr_invsr_krw, f.orgn_krw, p.mktcap_krw,
                  s.short_weight_kiwoom_pct, s.lending_balance_kiwoom_shr,
                  c.whol_loan_rmnd_rate_pct, c.whol_loan_rmnd_stcn_shr
           FROM (SELECT date, mktcap_krw FROM read_parquet(?) WHERE ticker = ? AND date >= ?) p
           LEFT JOIN (SELECT date, frgnr_invsr_krw, orgn_krw FROM read_parquet(?) WHERE ticker = ? AND src = 'kiwoom') f USING (date)
           LEFT JOIN (SELECT date, short_weight_kiwoom_pct, lending_balance_kiwoom_shr FROM read_parquet(?) WHERE ticker = ?) s USING (date)
           LEFT JOIN (SELECT date, whol_loan_rmnd_rate_pct, whol_loan_rmnd_stcn_shr FROM read_parquet(?) WHERE ticker = ?) c USING (date)
           ORDER BY p.date""",
        [
            price, match.code, since,
            flow, match.code,
            current_files(root, "short_daily"), match.code,
            current_files(root, "credit_daily"), match.code,
        ],
    ).fetchall()
    return Extras(
        dates=np.array([r[0] for r in rows], dtype="datetime64[D]"),
        foreign_krw=_col(rows, 1),
        institution_krw=_col(rows, 2),
        mktcap_krw=_col(rows, 3),
        short_weight_pct=_col(rows, 4),
        lending_shr=_col(rows, 5),
        credit_rate_pct=_col(rows, 6),
        credit_shr=_col(rows, 7),
        scope="종목",
    )


def load(root: Path, match: Match) -> Series:
    """일봉 전체. 종목은 수정주가를 마지막 봉 = 실제 종가로 맞춘 값(지금 상장일부터 — 티커 재사용 방지), 지수는 지수값."""
    con = duckdb.connect()
    if match.kind == "index":
        rows = con.execute(
            """SELECT date, open_idx, high_idx, low_idx, close_idx, volume_shr FROM read_parquet(?)
               WHERE index_name = ? ORDER BY date""",
            [current_files(root, "index_daily"), match.code],
        ).fetchall()
        return _to_series(match, rows)
    listed = con.execute(
        "SELECT max(list_date) FROM read_parquet(?) WHERE ticker = ?", [current_files(root, "security"), match.code]
    ).fetchone()
    since = listed[0] if listed and listed[0] is not None else None
    rows = con.execute(
        """SELECT date, adj_open, adj_high, adj_low, adj_close, adj_volume_shr FROM read_parquet(?)
           WHERE ticker = ? AND (? IS NULL OR date >= ?) ORDER BY date""",
        [current_files(root, "price_adj_daily"), match.code, since, since],
    ).fetchall()
    return _rescale_to_raw(con, root, _to_series(match, rows))
