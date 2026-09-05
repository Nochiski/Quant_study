"""손 픽스처 equity_root 생성기 — `MANIFEST.json` + `v=<build>/[year=YYYY/]part0.parquet`.

equity 층 DESIGN §2 의 판본 골격(현재 빌드 포인터 `current_build` → `builds[].partitions[].path`)을
테스트 안에서 pyarrow 로 만든다. 산출물 파일에 의존하지 않는다(`.claude/rules/testing.md`).
컬럼 타입은 실물(`rules_s0*.py` 선언)과 같게 둔다 — 가격은 DECIMAL, 계수는 DOUBLE.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

PriceRow = tuple[str, date, float | None, float | None, float | None, float | None, int | None]
"""ticker, date, open, high, low, close, volume_shr — None 은 NULL."""
SpanRow = tuple[str, int, date, date, str]
"""ticker, span_seq, first_date, last_date, end_reason."""
FactorRow = tuple[str, date, str, str, float, bool]
"""ticker, effective_date, event_id, event_type, share_factor, factor_ok."""


def write_equity_table(
    root: Path,
    table: str,
    data: pa.Table,
    *,
    build_id: str = "b_test",
    year_column: str | None = None,
) -> Path:
    """`<root>/<table>/v=<build_id>/…` 에 parquet 를 쓰고 MANIFEST 의 current_build 를 가리킨다.

    year_column 을 주면 `year=YYYY/part0.parquet` 하이브 디렉토리로 나눈다(date_axis 테이블).
    """
    table_root = root / table
    vdir = table_root / f"v={build_id}"
    partitions: list[dict[str, object]] = []
    if year_column is None:
        vdir.mkdir(parents=True, exist_ok=True)
        pq.write_table(data, vdir / "part0.parquet")
        partitions.append({"path": f"v={build_id}", "n_rows": data.num_rows})
    else:
        years = [session.year for session in data[year_column].to_pylist()]
        for year in sorted(set(years)):
            part = data.filter(pa.array([y == year for y in years], type=pa.bool_()))
            pdir = vdir / f"year={year}"
            pdir.mkdir(parents=True, exist_ok=True)
            pq.write_table(part, pdir / "part0.parquet")
            partitions.append({"path": f"v={build_id}/year={year}", "n_rows": part.num_rows})
    write_manifest(table_root, build_id, partitions, n_rows=data.num_rows)
    return table_root


def write_manifest(
    table_root: Path,
    current_build: str | None,
    partitions: list[dict[str, object]],
    *,
    n_rows: int = 0,
    builds: list[dict[str, object]] | None = None,
) -> Path:
    """stage `manifest.py` 와 같은 모양의 MANIFEST.json. `builds` 를 주면 그대로 싣는다."""
    table_root.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "build_id": current_build,
        "snapshot_id": "",
        "rules_version": "e_test",
        "built_at_utc": "2026-09-05T00:00:00+00:00",
        "n_rows": n_rows,
        "content_hash": "",
        "partitions": partitions,
        "gates": [],
        "inputs": {},
    }
    payload = {
        "table": table_root.name,
        "current_build": current_build,
        "keep": 3,
        "builds": [record] if builds is None and current_build is not None else (builds or []),
    }
    path = table_root / "MANIFEST.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _decimal(values: Sequence[float | int | None], scale_type: pa.DataType) -> pa.Array:
    return pa.array(
        [None if v is None else Decimal(str(int(v))) for v in values], type=scale_type
    )


def price_table(rows: list[PriceRow], shares_out: dict[str, int] | None = None) -> pa.Table:
    """`price_daily` 관심 컬럼. `price_kind` 는 S04 규칙대로 volume>0 → trade, =0 → reference.

    `shares_out` 을 주면 `mktcap_krw = close × shares_out[ticker]`(S04 파생) 를 붙인다 — 없는 티커는
    NULL(결측은 결측).
    """
    volumes = [r[6] for r in rows]
    columns: dict[str, pa.Array] = {
        "ticker": pa.array([r[0] for r in rows], type=pa.string()),
        "date": pa.array([r[1] for r in rows], type=pa.date32()),
        "open": _decimal([r[2] for r in rows], pa.decimal128(9, 0)),
        "high": _decimal([r[3] for r in rows], pa.decimal128(9, 0)),
        "low": _decimal([r[4] for r in rows], pa.decimal128(9, 0)),
        "close": _decimal([r[5] for r in rows], pa.decimal128(9, 0)),
        "volume_shr": _decimal(volumes, pa.decimal128(13, 0)),
        "price_kind": pa.array(
            [None if v is None else ("trade" if v > 0 else "reference") for v in volumes],
            type=pa.string(),
        ),
    }
    if shares_out is not None:
        columns["mktcap_krw"] = _decimal(
            [
                None if r[5] is None or r[0] not in shares_out else r[5] * shares_out[r[0]]
                for r in rows
            ],
            pa.decimal128(18, 0),
        )
    return pa.table(columns)


def span_table(rows: list[SpanRow]) -> pa.Table:
    return pa.table(
        {
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "span_seq": pa.array([r[1] for r in rows], type=pa.int64()),
            "first_date": pa.array([r[2] for r in rows], type=pa.date32()),
            "last_date": pa.array([r[3] for r in rows], type=pa.date32()),
            "n_days": pa.array([(r[3] - r[2]).days + 1 for r in rows], type=pa.int64()),
            "end_reason": pa.array([r[4] for r in rows], type=pa.string()),
        }
    )


def calendar_table(sessions: list[date]) -> pa.Table:
    ordered = sorted(sessions)
    return pa.table(
        {
            "date": pa.array(ordered, type=pa.date32()),
            "prev_td": pa.array([None, *ordered[:-1]], type=pa.date32()),
            "next_td": pa.array([*ordered[1:], None], type=pa.date32()),
        }
    )


def security_table(rows: list[tuple[str, str]], names: dict[str, str] | None = None) -> pa.Table:
    """`security` 관심 컬럼 (ticker, sec_type). `names` 를 주면 `name_current` 를 붙인다."""
    columns: dict[str, pa.Array] = {
        "ticker": pa.array([r[0] for r in rows], type=pa.string()),
        "sec_type": pa.array([r[1] for r in rows], type=pa.string()),
    }
    if names is not None:
        columns["name_current"] = pa.array([names.get(r[0]) for r in rows], type=pa.string())
    return pa.table(columns)


def factor_table(
    rows: list[FactorRow],
    apply_dates: list[date | None] | None = None,
    available_dates: list[date] | None = None,
) -> pa.Table:
    """`adj_factor` 관심 컬럼. `apply_dates` 를 주면 `apply_date`, `available_dates` 를 주면
    `available_date` 컬럼을 붙인다(S06 — 뷰 `v_cum_adj` 는 둘 다 읽는다)."""
    columns: dict[str, pa.Array] = {
        "ticker": pa.array([r[0] for r in rows], type=pa.string()),
        "effective_date": pa.array([r[1] for r in rows], type=pa.date32()),
        "event_id": pa.array([r[2] for r in rows], type=pa.string()),
        "event_type": pa.array([r[3] for r in rows], type=pa.string()),
        "price_factor": pa.array([1.0 / r[4] for r in rows], type=pa.float64()),
        "share_factor": pa.array([r[4] for r in rows], type=pa.float64()),
        "factor_ok": pa.array([r[5] for r in rows], type=pa.bool_()),
    }
    if apply_dates is not None:
        columns["apply_date"] = pa.array(apply_dates, type=pa.date32())
    if available_dates is not None:
        columns["available_date"] = pa.array(available_dates, type=pa.date32())
    return pa.table(columns)


UniverseRow = tuple[str, date, str, str]
"""ticker, date, sec_type, status — `universe_daily` 중 정책 술어가 읽는 컬럼."""
PolicyRow = tuple[str, str, int, str]
"""universe_id, policy, rule_seq, predicate."""


def universe_table(rows: list[UniverseRow]) -> pa.Table:
    return pa.table(
        {
            "date": pa.array([r[1] for r in rows], type=pa.date32()),
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "status": pa.array([r[3] for r in rows], type=pa.string()),
            "sec_type": pa.array([r[2] for r in rows], type=pa.string()),
        }
    )


def policy_table(rows: list[PolicyRow]) -> pa.Table:
    return pa.table(
        {
            "universe_id": pa.array([r[0] for r in rows], type=pa.string()),
            "policy": pa.array([r[1] for r in rows], type=pa.string()),
            "rule_seq": pa.array([r[2] for r in rows], type=pa.int64()),
            "predicate": pa.array([r[3] for r in rows], type=pa.string()),
        }
    )


# ── 카탈로그 (`equity.duckdb` + `_catalog_meta.json`) ────────────────────────
# `equity.catalog`·`equity.views`(database/src/equity) 의 테스트 대역. backend 는 그 패키지를 import
# 할 수 없으므로 매크로 본문(DESIGN §5 v_cum_adj·v_adj_price·v_adj_price_fwd)과 snapshot_id 규칙
# (전 테이블 table=build 정렬 sha256 16자리)을 여기 옮겨 적는다 — 본문이 바뀌면 여기도 같이 바꾼다.

_CUM_ADJ_SQL = """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, 0)
),
fac AS (
    SELECT ticker, apply_date, product(price_factor) AS pf, product(share_factor) AS sf
    FROM {adj_factor}
    WHERE factor_ok AND apply_date <= as_of AND available_date <= (SELECT cutoff FROM cut)
    GROUP BY ticker, apply_date
),
suf AS (
    SELECT ticker, apply_date,
           product(pf) OVER w AS cum_price_factor,
           product(sf) OVER w AS cum_share_factor
    FROM fac
    WINDOW w AS (PARTITION BY ticker ORDER BY apply_date
                 ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)
)
SELECT p.ticker, p.date,
       coalesce(s.cum_price_factor, 1) AS cum_price_factor,
       coalesce(s.cum_share_factor, 1) AS cum_share_factor
FROM (SELECT ticker, date FROM {price_daily} WHERE date <= as_of) p
ASOF LEFT JOIN suf s ON s.ticker = p.ticker AND p.date < s.apply_date
"""
_ADJ_PRICE_SQL = """
SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume_shr, p.price_kind,
       c.cum_price_factor, c.cum_share_factor,
       p.open  * c.cum_price_factor AS adj_open,
       p.high  * c.cum_price_factor AS adj_high,
       p.low   * c.cum_price_factor AS adj_low,
       p.close * c.cum_price_factor AS adj_close
FROM {price_daily} p
JOIN v_cum_adj(as_of, lag_override := lag_override) c ON c.ticker = p.ticker AND c.date = p.date
"""
# 전방 조정(S21 후속) — `equity.views._FWD_CTE` + `v_adj_price_fwd` 본문 사본. 계수는 fold_date =
# greatest(apply_date, available_date) 부터 앞으로 누적해 곱한다(공개 전 계수는 접지 않는다).
_ADJ_PRICE_FWD_SQL = """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, 0)
),
fac AS (
    SELECT ticker, greatest(apply_date, available_date) AS fold_date,
           product(price_factor) AS pf, product(share_factor) AS sf,
           max(available_date) AS available_date
    FROM {adj_factor}
    WHERE factor_ok AND apply_date <= as_of AND available_date <= (SELECT cutoff FROM cut)
    GROUP BY ticker, greatest(apply_date, available_date)
),
pre AS (
    SELECT ticker, fold_date,
           product(pf) OVER w AS cum_price_factor,
           product(sf) OVER w AS cum_share_factor,
           max(available_date) OVER w AS available_date
    FROM fac
    WINDOW w AS (PARTITION BY ticker ORDER BY fold_date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
fwd AS (
    SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume_shr, p.price_kind,
           coalesce(c.cum_price_factor, 1) AS cum_price_factor,
           coalesce(c.cum_share_factor, 1) AS cum_share_factor,
           greatest(p.date, coalesce(c.available_date, p.date)) AS available_date
    FROM (SELECT * FROM {price_daily} WHERE date <= as_of) p
    ASOF LEFT JOIN pre c ON c.ticker = p.ticker AND p.date >= c.fold_date
)
SELECT ticker, date, open, high, low, close, volume_shr, price_kind,
       cum_price_factor, cum_share_factor, available_date,
       open  * cum_share_factor AS adj_open,
       high  * cum_share_factor AS adj_high,
       low   * cum_share_factor AS adj_low,
       close * cum_share_factor AS adj_close
FROM fwd
"""
CATALOG_MACROS = (
    "v_cum_adj(as_of, lag_override := NULL)",
    "v_adj_price(as_of, lag_override := NULL)",
    "v_adj_price_fwd(as_of, lag_override := NULL)",
)
_MACRO_INPUTS = ("price_daily", "adj_factor", "trading_calendar")


def table_builds(root: Path) -> dict[str, str]:
    """`<root>/<table>/MANIFEST.json` 의 current_build (밑줄·점으로 시작하는 디렉토리 제외)."""
    builds: dict[str, str] = {}
    for directory in sorted(root.iterdir()):
        manifest = directory / "MANIFEST.json"
        if directory.name.startswith(("_", ".")) or not manifest.exists():
            continue
        current = json.loads(manifest.read_text(encoding="utf-8")).get("current_build")
        if isinstance(current, str) and current:
            builds[directory.name] = current
    return builds


def snapshot_id(builds: dict[str, str]) -> str:
    payload = "\n".join(f"{table}={build}" for table, build in sorted(builds.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _partition_source(root: Path, table: str, build_id: str) -> str:
    manifest = json.loads((root / table / "MANIFEST.json").read_text(encoding="utf-8"))
    record = next(b for b in manifest["builds"] if b["build_id"] == build_id)
    globs = ", ".join(
        f"'{(root / table / str(p['path'])).resolve() / '*.parquet'}'"
        for p in record["partitions"]
    )
    return f"read_parquet([{globs}], hive_partitioning=false)"


def write_catalog(root: Path, *, snapshot: str | None = None, with_macros: bool = True) -> Path:
    """`equity.duckdb`(매크로 3개) + `_catalog_meta.json` 을 쓴다.

    `snapshot` 을 주면 meta 의 snapshot_id 를 그 값으로 둔다(stale 카탈로그 부정 픽스처).
    `with_macros=False` 면 매크로 없이 `macros_skipped` 만 남긴다.
    """
    import duckdb  # 테스트 전용 — backend optional extra `equity`

    builds = table_builds(root)
    path = root / "equity.duckdb"
    if path.exists():
        path.unlink()
    macros: list[str] = []
    skipped: dict[str, str] = {}
    con = duckdb.connect(str(path))
    try:
        if with_macros:
            sources = {t: _partition_source(root, t, builds[t]) for t in _MACRO_INPUTS}
            con.execute(
                f"CREATE MACRO {CATALOG_MACROS[0]} AS TABLE " + _CUM_ADJ_SQL.format(**sources)
            )
            con.execute(
                f"CREATE MACRO {CATALOG_MACROS[1]} AS TABLE " + _ADJ_PRICE_SQL.format(**sources)
            )
            con.execute(
                f"CREATE MACRO {CATALOG_MACROS[2]} AS TABLE "
                + _ADJ_PRICE_FWD_SQL.format(**sources)
            )
            macros = list(CATALOG_MACROS)
        else:
            skipped = {
                "v_cum_adj": "not_built: inputs=['adj_factor']",
                "v_adj_price": "depends_on_skipped: ['v_cum_adj']",
                "v_adj_price_fwd": "not_built: inputs=['adj_factor']",
            }
    finally:
        con.close()
    (root / "_catalog_meta.json").write_text(
        json.dumps(
            {
                "snapshot_id": snapshot if snapshot is not None else snapshot_id(builds),
                "builds": builds,
                "macros": macros,
                "macros_skipped": skipped,
                "gates": [],
                "asof": {},
                "written_at_utc": "2026-09-05T00:00:00+00:00",
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


# ── 워크벤치 어댑터(S21) 손 픽스처 루트 ──────────────────────────────────────
# 캘린더 13세션(2023-12-26 ~ 2024-01-12, backfill_end = 01-12). 종목:
#   005930 common 전 구간 · 000660 common 전 구간, 2024-01-08 2:1 분할(apply=available=01-08) +
#   01-10 정지(reference 행, status suspended) · 035420 common 01-04 상장, 주식수 미상(mktcap
#   NULL) · 036220 common 재상장 2구간([12-26, 12-29]·[01-08, 01-12]) · 005935 preferred ·
#   069500 etf.
#   adj_factor 에 005930 not-ok 행 1(계수 1) — 사건·조정에 나오면 안 된다.
# 정책: krx.all(TRUE) · krx.common-stock(sec_type='common' ∧ status='listed').

WB_SESSIONS: tuple[date, ...] = (
    date(2023, 12, 26), date(2023, 12, 27), date(2023, 12, 28), date(2023, 12, 29),
    date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5),
    date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10), date(2024, 1, 11), date(2024, 1, 12),
)
WB_SPLIT_DATE = date(2024, 1, 8)
WB_HALT_DATE = date(2024, 1, 10)
WB_SPANS: list[SpanRow] = [
    ("005930", 1, WB_SESSIONS[0], WB_SESSIONS[-1], "coverage_gap"),
    ("000660", 1, WB_SESSIONS[0], WB_SESSIONS[-1], "coverage_gap"),
    ("035420", 1, date(2024, 1, 4), WB_SESSIONS[-1], "coverage_gap"),
    ("036220", 1, WB_SESSIONS[0], date(2023, 12, 29), "delisted"),
    ("036220", 2, WB_SPLIT_DATE, WB_SESSIONS[-1], "coverage_gap"),
    ("005935", 1, WB_SESSIONS[0], WB_SESSIONS[-1], "coverage_gap"),
    ("069500", 1, WB_SESSIONS[0], WB_SESSIONS[-1], "coverage_gap"),
]
WB_SEC_TYPES = {
    "005930": "common", "000660": "common", "035420": "common", "036220": "common",
    "005935": "preferred", "069500": "etf",
}
WB_SHARES = {"005930": 5_969_782_550, "000660": 728_002_365, "036220": 1_000_000,
             "005935": 822_886_700, "069500": 100_000_000}
WB_BASE_CLOSE = {"005930": 70_000, "000660": 100_000, "035420": 200_000, "036220": 10_000,
                 "005935": 60_000, "069500": 30_000}


def wb_close(ticker: str, session: date) -> float:
    """손계산 가능한 종가: base + 500 × 세션 index, 000660 은 분할일부터 절반."""
    index = WB_SESSIONS.index(session)
    close = WB_BASE_CLOSE[ticker] + 500 * index
    if ticker == "000660" and session >= WB_SPLIT_DATE:
        close = close // 2
    return float(close)


def build_workbench_root(root: Path, *, catalog: bool = True) -> Path:
    """워크벤치 어댑터 손 픽스처 equity_root 를 만든다. `catalog=False` 면 equity.duckdb 없음."""
    prices: list[PriceRow] = []
    universe: list[UniverseRow] = []
    for ticker, _seq, first, last, _reason in WB_SPANS:
        for session in WB_SESSIONS:
            if not first <= session <= last:
                continue
            close = wb_close(ticker, session)
            halted = ticker == "000660" and session == WB_HALT_DATE
            prices.append(
                (ticker, session, None, None, None, close, 0)
                if halted
                else (ticker, session, close - 100, close + 200, close - 200, close, 1_000)
            )
            universe.append(
                (ticker, session, WB_SEC_TYPES[ticker], "suspended" if halted else "listed")
            )
    write_equity_table(root, "trading_calendar", calendar_table(list(WB_SESSIONS)))
    write_equity_table(root, "security_span", span_table(WB_SPANS))
    write_equity_table(
        root,
        "security",
        security_table(
            [(t, WB_SEC_TYPES[t]) for t in sorted(WB_SEC_TYPES)],
            names={"005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER"},
        ),
    )
    write_equity_table(
        root, "price_daily", price_table(prices, shares_out=WB_SHARES), year_column="date"
    )
    write_equity_table(root, "universe_daily", universe_table(universe), year_column="date")
    write_equity_table(
        root,
        "universe_policy",
        policy_table(
            [
                ("krx.all", "all", 1, "TRUE"),
                ("krx.common-stock", "common-stock", 1, "sec_type = 'common'"),
                ("krx.common-stock", "common-stock", 2, "status = 'listed'"),
            ]
        ),
    )
    write_equity_table(
        root,
        "adj_factor",
        factor_table(
            [
                ("000660", WB_SPLIT_DATE, "000660:split:2024-01-08", "split", 2.0, True),
                ("005930", date(2024, 1, 3), "005930:capred:2024-01-03", "capred", 1.0, False),
            ],
            apply_dates=[WB_SPLIT_DATE, date(2024, 1, 3)],
            available_dates=[WB_SPLIT_DATE, date(2024, 1, 3)],
        ),
        year_column="effective_date",
    )
    if catalog:
        write_catalog(root)
    return root
