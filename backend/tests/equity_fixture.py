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


def _dec4(values: Sequence[float | int | None]) -> pa.Array:
    """`fin_std`·`consensus_daily` 처럼 소수 자리가 있는 금액 컬럼 (DECIMAL(38,4))."""
    return pa.array(
        [None if v is None else Decimal(str(v)) for v in values], type=pa.decimal128(38, 4)
    )


def price_table(rows: list[PriceRow], shares_out: dict[str, int] | None = None) -> pa.Table:
    """`price_daily` 관심 컬럼. `price_kind` 는 S04 규칙대로 volume>0 → trade, =0 → reference.

    `shares_out` 을 주면 S04 파생 셋을 붙인다 — `shares_out`(KRX 상장주식수, FIELD_MAP
    `price.shares_outstanding`) · `mktcap_krw = close × shares_out` · `value_krw = close × volume`.
    없는 티커는 NULL(결측은 결측 — 035420 이 그 자리다).
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
        "value_krw": _decimal(
            [None if r[5] is None or r[6] is None else r[5] * r[6] for r in rows],
            pa.decimal128(16, 0),
        ),
        "price_kind": pa.array(
            [None if v is None else ("trade" if v > 0 else "reference") for v in volumes],
            type=pa.string(),
        ),
    }
    if shares_out is not None:
        columns["shares_out"] = _decimal(
            [shares_out.get(r[0]) for r in rows], pa.decimal128(13, 0)
        )
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


def profile_table(rows: list[tuple[str, int, str]]) -> pa.Table:
    """`dataset_profile` 중 어댑터가 읽는 세 컬럼 (field_id, 랙 세션, 근거)."""
    return pa.table(
        {
            "field_id": pa.array([r[0] for r in rows], type=pa.string()),
            "recommended_lag_sessions": pa.array([r[1] for r in rows], type=pa.int64()),
            "available_date_basis": pa.array([r[2] for r in rows], type=pa.string()),
        }
    )


# ── S21 본판이 읽는 나머지 테이블 (S01·S05·S11·S12·S15·S16·S17·S18) ──────────
# 컬럼은 `database/src/equity/rules_s*.py` 의 선언 중 어댑터·매크로가 읽는 것만 만든다.

def corp_ticker_table(rows: list[tuple[str, str | None, bool]]) -> pa.Table:
    """`corp_ticker` (ticker, corp_code, is_common) — 법인 축 테이블을 종목 축으로 여는 대응표."""
    return pa.table(
        {
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "isin8": pa.array([f"KR7{r[0]}" for r in rows], type=pa.string()),
            "corp_code": pa.array([r[1] for r in rows], type=pa.string()),
            "common_ticker": pa.array([r[0] if r[2] else None for r in rows], type=pa.string()),
            "is_common": pa.array([r[2] for r in rows], type=pa.bool_()),
            "link_basis": pa.array(["corp_map"] * len(rows), type=pa.string()),
        }
    )


FIN_ACCOUNTS: tuple[str, ...] = (
    "revenue", "gross_profit", "op_profit", "net_income",
    "total_asset", "total_liab", "total_equity", "cf_operating_ytd", "cf_operating_q",
)
FIN_Q4_ACCOUNTS: tuple[str, ...] = ("revenue", "gross_profit", "op_profit", "net_income")


def fin_std_table(rows: list[dict[str, object]]) -> pa.Table:
    """`fin_std` 중 `v_fin_latest` 가 읽는 컬럼. 값 키가 없으면 NULL(결측은 결측)."""
    columns: dict[str, pa.Array] = {
        "corp_code": pa.array([r["corp_code"] for r in rows], type=pa.string()),
        "period_end": pa.array([r["period_end"] for r in rows], type=pa.date32()),
        "report_code": pa.array([r["report_code"] for r in rows], type=pa.string()),
        "fs_div": pa.array([r.get("fs_div", "CFS") for r in rows], type=pa.string()),
        "vintage_kind": pa.array(
            [r.get("vintage_kind", "api_restated") for r in rows], type=pa.string()
        ),
        "bsns_year": pa.array([r["bsns_year"] for r in rows], type=pa.string()),
        "rcept_no": pa.array([r["rcept_no"] for r in rows], type=pa.string()),
        "period_start": pa.array([r.get("period_start") for r in rows], type=pa.date32()),
        "currency": pa.array(["KRW"] * len(rows), type=pa.string()),
        "revenue_basis": pa.array(
            [r.get("revenue_basis", "standard") for r in rows], type=pa.string()
        ),
        "restated_unknown": pa.array([True] * len(rows), type=pa.bool_()),
    }
    for account in FIN_ACCOUNTS:
        columns[account] = _dec4([r.get(account) for r in rows])  # pyright: ignore[reportArgumentType]  # reason: 픽스처 dict 값은 숫자·None
    for account in FIN_Q4_ACCOUNTS:
        key = f"{account}_q4_derived"
        columns[key] = _dec4([r.get(key) for r in rows])  # pyright: ignore[reportArgumentType]  # reason: 위와 같다
    columns["available_date"] = pa.array([r["available_date"] for r in rows], type=pa.date32())
    columns["available_basis"] = pa.array(["derived"] * len(rows), type=pa.string())
    return pa.table(columns)


def disclosure_version_table(rows: list[tuple[str, date | None]]) -> pa.Table:
    """`disclosure_version` 중 `v_fin_latest.has_correction` 이 읽는 (rcept_no, 첫 정정일)."""
    return pa.table(
        {
            "rcept_no": pa.array([r[0] for r in rows], type=pa.string()),
            "first_correction_dt": pa.array([r[1] for r in rows], type=pa.date32()),
        }
    )


ConsensusRow = tuple[str, date, str, str, str, date, float | None, float | None, float | None, bool]
"""ticker, obs_month, target_period, metric, src, obs_date, est_mean, est_min, est_max, degraded."""


def consensus_table(rows: list[ConsensusRow], available: list[date]) -> pa.Table:
    return pa.table(
        {
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "obs_month": pa.array([r[1] for r in rows], type=pa.date32()),
            "target_period": pa.array([r[2] for r in rows], type=pa.string()),
            "metric": pa.array([r[3] for r in rows], type=pa.string()),
            "src": pa.array([r[4] for r in rows], type=pa.string()),
            "obs_date": pa.array([r[5] for r in rows], type=pa.date32()),
            "est_mean": _dec4([r[6] for r in rows]),
            "est_min": _dec4([r[7] for r in rows]),
            "est_max": _dec4([r[8] for r in rows]),
            "unit": pa.array(
                ["원" if r[3] == "eps" else "억원" for r in rows], type=pa.string()
            ),
            "coverage_degraded": pa.array([r[9] for r in rows], type=pa.bool_()),
            "available_date": pa.array(available, type=pa.date32()),
            "available_basis": pa.array(
                ["default" if r[9] else "measured" for r in rows], type=pa.string()
            ),
        }
    )


OpinionRow = tuple[str, date, str, float | None, float | None, float | None, bool]
"""ticker, obs_date, src, opinion_score, target_price_krw, analyst_count, coverage_degraded."""


def opinion_table(rows: list[OpinionRow]) -> pa.Table:
    return pa.table(
        {
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "obs_date": pa.array([r[1] for r in rows], type=pa.date32()),
            "src": pa.array([r[2] for r in rows], type=pa.string()),
            "opinion_score": _dec4([r[3] for r in rows]),
            "target_price_krw": _dec4([r[4] for r in rows]),
            "analyst_count": _dec4([r[5] for r in rows]),
            "coverage_degraded": pa.array([r[6] for r in rows], type=pa.bool_()),
            "available_date": pa.array([r[1] for r in rows], type=pa.date32()),
            "available_basis": pa.array(
                ["default" if r[6] else "measured" for r in rows], type=pa.string()
            ),
        }
    )


DividendRow = tuple[str, str, str, str, float | None, date, date]
"""corp_code, bsns_year, reprt_code, stock_knd, dps_krw, stlm_dt, available_date."""


def dividend_table(rows: list[DividendRow]) -> pa.Table:
    return pa.table(
        {
            "corp_code": pa.array([r[0] for r in rows], type=pa.string()),
            "bsns_year": pa.array([r[1] for r in rows], type=pa.string()),
            "reprt_code": pa.array([r[2] for r in rows], type=pa.string()),
            "stock_knd": pa.array([r[3] for r in rows], type=pa.string()),
            "rcept_no": pa.array([f"D{r[1]}{r[0]}" for r in rows], type=pa.string()),
            "stlm_dt": pa.array([r[5] for r in rows], type=pa.date32()),
            "dps_krw": _dec4([r[4] for r in rows]),
            "available_date": pa.array([r[6] for r in rows], type=pa.date32()),
            "available_basis": pa.array(["derived"] * len(rows), type=pa.string()),
        }
    )


CorpEventRow = tuple[str, str, str, date, float | None]
"""event_id, ticker, event_type, announce_date, amount_krw."""


def corp_event_table(rows: list[CorpEventRow]) -> pa.Table:
    return pa.table(
        {
            "event_id": pa.array([r[0] for r in rows], type=pa.string()),
            "ticker": pa.array([r[1] for r in rows], type=pa.string()),
            "event_type": pa.array([r[2] for r in rows], type=pa.string()),
            "announce_date": pa.array([r[3] for r in rows], type=pa.date32()),
            "amount_krw": pa.array([r[4] for r in rows], type=pa.int64()),
            "available_date": pa.array([r[3] for r in rows], type=pa.date32()),
            "available_basis": pa.array(["derived"] * len(rows), type=pa.string()),
        }
    )


HolderRow = tuple[str, str, str, str, float | None, date]
"""rcept_no, src, repror, corp_code, qty_change_shr, rcept_dt(=available_date)."""


def holder_table(rows: list[HolderRow]) -> pa.Table:
    return pa.table(
        {
            "rcept_no": pa.array([r[0] for r in rows], type=pa.string()),
            "src": pa.array([r[1] for r in rows], type=pa.string()),
            "repror": pa.array([r[2] for r in rows], type=pa.string()),
            "corp_code": pa.array([r[3] for r in rows], type=pa.string()),
            "rcept_dt": pa.array([r[5] for r in rows], type=pa.date32()),
            "qty_change_shr": _dec4([r[4] for r in rows]),
            "available_date": pa.array([r[5] for r in rows], type=pa.date32()),
            "available_basis": pa.array(["derived"] * len(rows), type=pa.string()),
        }
    )


# ── 격자 3테이블 (S08 flow · S09 short · S10 credit) ─────────────────────────
# 셋 다 (ticker, date) 격자 위의 일별 행이고 값 없는 셀은 **NULL + `fill_kind`** 다(0 채움 금지 —
# DESIGN §9 결정 8). `fill_kind` 는 STRUCT(kind VARCHAR, evidence VARCHAR) 이고 어휘는
# `database/src/equity/model.py::FILL_KINDS`·`FILL_EVIDENCE` 다.

_FILL_KIND_TYPE = pa.struct([("kind", pa.string()), ("evidence", pa.string())])


def _fill_kind(values: Sequence[tuple[str, str] | None]) -> pa.Array:
    return pa.array(
        [None if v is None else {"kind": v[0], "evidence": v[1]} for v in values],
        type=_FILL_KIND_TYPE,
    )


FlowRow = tuple[str, date, str | None, float | None, float | None, float | None, tuple[str, str]]
"""ticker, date, src, ind_invsr_krw, frgnr_invsr_krw, orgn_krw, (fill_kind.kind, .evidence)."""


def flow_table(rows: list[FlowRow]) -> pa.Table:
    """`flow_daily` 중 어댑터가 읽는 컬럼. grain 은 (date, ticker, **src**) 다 — 같은 셀에 두
    원천이 다 있으면 2행이고 어댑터가 `pick_order` 로 하나를 고른다."""
    money = pa.decimal128(15, 0)
    return pa.table(
        {
            "date": pa.array([r[1] for r in rows], type=pa.date32()),
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "src": pa.array([r[2] for r in rows], type=pa.string()),
            "ind_invsr_krw": _decimal([r[3] for r in rows], money),
            "frgnr_invsr_krw": _decimal([r[4] for r in rows], money),
            "orgn_krw": _decimal([r[5] for r in rows], money),
            "fill_kind": _fill_kind([r[6] for r in rows]),
            "available_date": pa.array([r[1] for r in rows], type=pa.date32()),
            "available_basis": pa.array(["default"] * len(rows), type=pa.string()),
        }
    )


ShortRow = tuple[
    str, date, float | None, float | None, float | None, tuple[str, str], tuple[str, str]
]
"""ticker, date, short_volume_kiwoom_shr, short_value_kiwoom_krw, lending_balance_kis_shr,
fill_kind_short_kiwoom, fill_kind_loan_kis — 결측 사유가 원천마다 하나다(S09)."""


def short_table(rows: list[ShortRow]) -> pa.Table:
    return pa.table(
        {
            "date": pa.array([r[1] for r in rows], type=pa.date32()),
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "short_volume_kiwoom_shr": _decimal([r[2] for r in rows], pa.decimal128(10, 0)),
            "short_value_kiwoom_krw": _decimal([r[3] for r in rows], pa.decimal128(15, 0)),
            "lending_balance_kis_shr": _decimal([r[4] for r in rows], pa.decimal128(11, 0)),
            "fill_kind_short_kiwoom": _fill_kind([r[5] for r in rows]),
            "fill_kind_loan_kis": _fill_kind([r[6] for r in rows]),
            "available_date": pa.array([r[1] for r in rows], type=pa.date32()),
            "available_basis": pa.array(["default"] * len(rows), type=pa.string()),
        }
    )


CreditRow = tuple[str, date, float | None, tuple[str, str]]
"""ticker, date, whol_loan_rmnd_stcn_shr, (fill_kind.kind, .evidence)."""


def credit_table(rows: list[CreditRow]) -> pa.Table:
    return pa.table(
        {
            "date": pa.array([r[1] for r in rows], type=pa.date32()),
            "ticker": pa.array([r[0] for r in rows], type=pa.string()),
            "whol_loan_rmnd_stcn_shr": _decimal([r[2] for r in rows], pa.decimal128(10, 0)),
            "amt_basis": pa.array(["unknown"] * len(rows), type=pa.string()),
            "fill_kind": _fill_kind([r[3] for r in rows]),
            "available_date": pa.array([r[1] for r in rows], type=pa.date32()),
            "available_basis": pa.array(["default"] * len(rows), type=pa.string()),
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
# 전방 조정(S21 후속 · S23 구간 제한) — `equity.views._FWD_CTE` + `v_adj_price_fwd` 본문 사본.
# 계수는 fold_date = greatest(apply_date, available_date) 부터 앞으로 누적해 곱하고(공개 전 계수는
# 접지 않는다), 누적은 `security_span` 구간 안에서만 한다(재상장 종목의 이전 구간 계수 누출 방지).
# 계수 쪽 구간 부여만 **엄격 부등호**라 구간 첫날에 접히는 계수는 어떤 행에도 곱해지지 않는다.
_ADJ_PRICE_FWD_SQL = """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, 0)
),
vis AS (
    SELECT ticker, greatest(apply_date, available_date) AS fold_date,
           price_factor, share_factor, available_date
    FROM {adj_factor}
    WHERE factor_ok AND apply_date <= as_of AND available_date <= (SELECT cutoff FROM cut)
),
spn AS (
    SELECT f.ticker, coalesce(s.span_seq, -1) AS span_seq, f.fold_date,
           f.price_factor, f.share_factor, f.available_date
    FROM vis f
    ASOF LEFT JOIN {security_span} s ON s.ticker = f.ticker AND f.fold_date > s.first_date
),
fac AS (
    SELECT ticker, span_seq, fold_date,
           product(price_factor) AS pf, product(share_factor) AS sf,
           max(available_date) AS available_date
    FROM spn
    GROUP BY ticker, span_seq, fold_date
),
pre AS (
    SELECT ticker, span_seq, fold_date,
           product(pf) OVER w AS cum_price_factor,
           product(sf) OVER w AS cum_share_factor,
           max(available_date) OVER w AS available_date
    FROM fac
    WINDOW w AS (PARTITION BY ticker, span_seq ORDER BY fold_date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
px AS (
    SELECT p.*, coalesce(s.span_seq, 0) AS span_seq
    FROM (SELECT * FROM {price_daily} WHERE date <= as_of) p
    ASOF LEFT JOIN {security_span} s ON s.ticker = p.ticker AND p.date >= s.first_date
),
fwd AS (
    SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume_shr, p.price_kind,
           coalesce(c.cum_price_factor, 1) AS cum_price_factor,
           coalesce(c.cum_share_factor, 1) AS cum_share_factor,
           greatest(p.date, coalesce(c.available_date, p.date)) AS available_date
    FROM px p
    ASOF LEFT JOIN pre c
      ON c.ticker = p.ticker AND c.span_seq = p.span_seq AND p.date >= c.fold_date
)
SELECT ticker, date, open, high, low, close, volume_shr, price_kind,
       cum_price_factor, cum_share_factor, available_date,
       open  * cum_share_factor AS adj_open,
       high  * cum_share_factor AS adj_high,
       low   * cum_share_factor AS adj_low,
       close * cum_share_factor AS adj_close
FROM fwd
"""

# `price_adj_daily`(S23) 산출 사본 — `database/src/equity/sql/price_adj_daily.sql` 과 같은 식이다.
# 매크로가 아니라 **표**라 카탈로그와 무관하게 산다(`price.adj_close` 가 여기서 나온다).
_PRICE_ADJ_DAILY_SQL = """
WITH px AS (
    SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume_shr,
           coalesce(s.span_seq, 0) AS span_seq
    FROM {price_daily} p
    ASOF LEFT JOIN {security_span} s ON s.ticker = p.ticker AND p.date >= s.first_date
),
okf AS (
    SELECT f.ticker, greatest(f.apply_date, f.available_date) AS fold_date,
           f.price_factor, f.share_factor, f.available_date
    FROM {adj_factor} f WHERE f.factor_ok
),
oks AS (
    SELECT o.ticker, coalesce(s.span_seq, -1) AS span_seq, o.fold_date,
           o.price_factor, o.share_factor, o.available_date
    FROM okf o
    ASOF LEFT JOIN {security_span} s ON s.ticker = o.ticker AND o.fold_date > s.first_date
),
fac AS (
    SELECT ticker, span_seq, fold_date,
           product(price_factor) AS pf, product(share_factor) AS sf,
           count(*) AS n_fac, max(available_date) AS avail
    FROM oks GROUP BY ticker, span_seq, fold_date
),
cum AS (
    SELECT ticker, span_seq, fold_date,
           product(pf) OVER w AS cum_price_factor,
           product(sf) OVER w AS cum_share_factor,
           sum(n_fac)  OVER w AS n_factors_applied,
           max(avail)  OVER w AS factor_available_date
    FROM fac
    WINDOW w AS (PARTITION BY ticker, span_seq ORDER BY fold_date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
badf AS (SELECT e.ticker, e.apply_date FROM {adj_factor} e WHERE NOT e.factor_ok),
bads AS (
    SELECT b.ticker, coalesce(s.span_seq, -1) AS span_seq, b.apply_date
    FROM badf b
    ASOF LEFT JOIN {security_span} s ON s.ticker = b.ticker AND b.apply_date >= s.first_date
),
bad AS (
    SELECT ticker, span_seq, apply_date, count(*) AS n_bad FROM bads
    GROUP BY ticker, span_seq, apply_date
),
badcum AS (
    SELECT ticker, span_seq, apply_date,
           sum(n_bad) OVER (PARTITION BY ticker, span_seq ORDER BY apply_date
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
             AS n_unadjusted_events
    FROM bad
),
joined AS (
    SELECT p.ticker, p.date, p.span_seq, p.open, p.high, p.low, p.close, p.volume_shr,
           coalesce(c.cum_price_factor, 1)  AS cum_price_factor,
           coalesce(c.cum_share_factor, 1)  AS cum_share_factor,
           coalesce(c.n_factors_applied, 0) AS n_factors_applied,
           c.factor_available_date
    FROM px p
    ASOF LEFT JOIN cum c
      ON c.ticker = p.ticker AND c.span_seq = p.span_seq AND p.date >= c.fold_date
),
unadj AS (
    SELECT j.*, coalesce(b.n_unadjusted_events, 0) AS n_unadjusted_events
    FROM joined j
    ASOF LEFT JOIN badcum b
      ON b.ticker = j.ticker AND b.span_seq = j.span_seq AND j.date >= b.apply_date
)
SELECT u.ticker, u.date,
       u.open       * u.cum_share_factor  AS adj_open,
       u.high       * u.cum_share_factor  AS adj_high,
       u.low        * u.cum_share_factor  AS adj_low,
       u.close      * u.cum_share_factor  AS adj_close,
       u.volume_shr * u.cum_price_factor  AS adj_volume_shr,
       u.cum_price_factor, u.cum_share_factor,
       CAST(u.n_factors_applied AS BIGINT)   AS n_factors_applied,
       CAST(u.n_unadjusted_events AS BIGINT) AS n_unadjusted_events,
       greatest(u.date, coalesce(u.factor_available_date, u.date)) AS available_date,
       'derived' AS available_basis
FROM unadj u
ORDER BY u.ticker, u.date
"""
# 컨센서스 뷰(S17) — `equity.views.TEMPLATES['v_consensus']` 본문 사본. `available_date` 로만
# 자르고 겹치는 달의 wise·v3 2행을 먼저 알 수 있던 한 행으로 접는다(obs_month 로 자르지 않는다).
_CONSENSUS_SQL = """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, 0)
),
vis AS (
    SELECT c.*, row_number() OVER (
               PARTITION BY c.ticker, c.obs_month, c.target_period, c.metric
               ORDER BY c.available_date, c.src) AS rn
    FROM {consensus_daily} c
    WHERE c.available_date <= (SELECT cutoff FROM cut)
)
SELECT ticker, obs_month, target_period, metric, src, obs_date,
       est_mean, est_min, est_max, unit, coverage_degraded,
       available_date, available_basis
FROM vis
WHERE rn = 1
"""
# 재무 판본 뷰(S21 본판) — `equity.views.TEMPLATES['v_fin_latest']` 본문 사본. 판본(vintage)과
# 재무제표 구분(CFS 우선)만 접고 기간 축은 남긴다. TTM 은 4분기가 전부 보일 때만 선다.
_FIN_LATEST_SQL = """
WITH cut AS (
    SELECT k.date AS cutoff
    FROM (SELECT date, row_number() OVER (ORDER BY date DESC) - 1 AS n
          FROM {trading_calendar} WHERE date <= as_of) k
    WHERE k.n = coalesce(lag_override, 0)
),
vis AS (
    SELECT f.*
    FROM {fin_std} f
    WHERE f.available_date <= (SELECT cutoff FROM cut)
      AND CASE WHEN vintage = 'restated' THEN f.vintage_kind = 'api_restated'
               WHEN vintage = 'pit'      THEN f.vintage_kind IN ('original', 'corrected')
               ELSE f.vintage_kind = vintage END
),
pick AS (
    SELECT * FROM (
        SELECT v.*, row_number() OVER (
                   PARTITION BY v.corp_code, v.period_end, v.report_code
                   ORDER BY CASE WHEN v.fs_div = 'CFS' THEN 0 ELSE 1 END, v.fs_div,
                            v.available_date DESC, v.rcept_no DESC) AS rn
        FROM vis v)
    WHERE rn = 1
),
q AS (
    SELECT p.*,
           CASE WHEN p.report_code = '11011' THEN p.revenue_q4_derived
                ELSE p.revenue END                                   AS q_revenue,
           CASE WHEN p.report_code = '11011' THEN p.gross_profit_q4_derived
                ELSE p.gross_profit END                              AS q_gross_profit,
           CASE WHEN p.report_code = '11011' THEN p.op_profit_q4_derived
                ELSE p.op_profit END                                 AS q_op_profit,
           CASE WHEN p.report_code = '11011' THEN p.net_income_q4_derived
                ELSE p.net_income END                                AS q_net_income,
           p.cf_operating_q                                          AS q_cf_operating
    FROM pick p
),
ttm AS (
    SELECT q.*,
           count(*) OVER w                                           AS ttm_n_rows,
           max(q.available_date) OVER w                              AS ttm_max_available,
           min(q.period_end) OVER w                                  AS ttm_first_period_end,
           sum(q.q_revenue) OVER w                                   AS ttm_sum_revenue,
           count(q.q_revenue) OVER w                                 AS ttm_cnt_revenue,
           sum(q.q_gross_profit) OVER w                              AS ttm_sum_gross_profit,
           count(q.q_gross_profit) OVER w                            AS ttm_cnt_gross_profit,
           sum(q.q_op_profit) OVER w                                 AS ttm_sum_op_profit,
           count(q.q_op_profit) OVER w                               AS ttm_cnt_op_profit,
           sum(q.q_net_income) OVER w                                AS ttm_sum_net_income,
           count(q.q_net_income) OVER w                              AS ttm_cnt_net_income,
           sum(q.q_cf_operating) OVER w                              AS ttm_sum_cf_operating,
           count(q.q_cf_operating) OVER w                            AS ttm_cnt_cf_operating
    FROM q
    WINDOW w AS (PARTITION BY q.corp_code ORDER BY q.period_end, q.report_code
                 ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)
),
ok AS (
    SELECT t.*,
           (t.ttm_n_rows = 4
            AND t.ttm_max_available <= t.available_date
            AND date_diff('day', t.ttm_first_period_end, t.period_end)
                BETWEEN 240 AND 400) AS ttm_window_ok
    FROM ttm t
)
SELECT o.corp_code, o.period_end, o.report_code, o.fs_div AS fs_div_used,
       o.bsns_year, o.rcept_no, o.period_start, o.currency,
       o.revenue, o.revenue_basis, o.gross_profit, o.op_profit, o.net_income,
       o.total_asset, o.total_liab, o.total_equity, o.cf_operating_ytd, o.cf_operating_q,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_revenue = 4
            THEN o.ttm_sum_revenue END                               AS ttm_revenue,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_gross_profit = 4
            THEN o.ttm_sum_gross_profit END                          AS ttm_gross_profit,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_op_profit = 4
            THEN o.ttm_sum_op_profit END                             AS ttm_op_profit,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_net_income = 4
            THEN o.ttm_sum_net_income END                            AS ttm_net_income,
       CASE WHEN o.ttm_window_ok AND o.ttm_cnt_cf_operating = 4
            THEN o.ttm_sum_cf_operating END                          AS ttm_cf_operating,
       coalesce(d.first_correction_dt <= (SELECT cutoff FROM cut), FALSE) AS has_correction,
       o.available_date, o.available_basis
FROM ok o
LEFT JOIN (SELECT rcept_no, first_correction_dt FROM {disclosure_version}) d
       ON d.rcept_no = o.rcept_no
"""

# 시그니처 → (본문, 읽는 테이블). `equity.views.SIGNATURES`·`MACRO_INPUTS` 의 사본이다.
_PRICE_INPUTS = ("price_daily", "adj_factor", "trading_calendar")
_FWD_INPUTS = ("price_daily", "adj_factor", "trading_calendar", "security_span")
_CATALOG_BODIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("v_cum_adj(as_of, lag_override := NULL)", _CUM_ADJ_SQL, _PRICE_INPUTS),
    ("v_adj_price(as_of, lag_override := NULL)", _ADJ_PRICE_SQL, _PRICE_INPUTS),
    ("v_adj_price_fwd(as_of, lag_override := NULL)", _ADJ_PRICE_FWD_SQL, _FWD_INPUTS),
    (
        "v_consensus(as_of, lag_override := NULL)",
        _CONSENSUS_SQL,
        ("consensus_daily", "trading_calendar"),
    ),
    (
        "v_fin_latest(as_of, lag_override := NULL, vintage := 'restated')",
        _FIN_LATEST_SQL,
        ("fin_std", "disclosure_version", "trading_calendar"),
    ),
)
CATALOG_MACROS = tuple(signature for signature, _, _ in _CATALOG_BODIES)


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


def price_adj_table(root: Path) -> pa.Table:
    """`price_adj_daily`(S23) — 이미 쓴 `price_daily`·`adj_factor`·`security_span` 위에서 굽는다.

    `price.adj_close` 의 산출처다. 손으로 값을 적지 않고 실물과 같은 식(`_PRICE_ADJ_DAILY_SQL`)을
    돌린다 — 그래야 분할·재상장 구간의 기대값이 어댑터 테스트의 손계산과 갈리지 않는다.
    """
    import duckdb  # 테스트 전용 — backend optional extra `equity`

    builds = table_builds(root)
    tables = ("price_daily", "adj_factor", "security_span")
    absent = [name for name in tables if name not in builds]
    if absent:
        raise ValueError(f"price_adj_daily needs these equity tables first: missing={absent} "
                         f"root={root} built={sorted(builds)}")
    sources = {name: _partition_source(root, name, builds[name]) for name in tables}
    con = duckdb.connect()
    try:
        return con.execute(_PRICE_ADJ_DAILY_SQL.format(**sources)).to_arrow_table()
    finally:
        con.close()


def write_catalog(root: Path, *, snapshot: str | None = None, with_macros: bool = True) -> Path:
    """`equity.duckdb`(입력이 갖춰진 매크로 전부) + `_catalog_meta.json` 을 쓴다.

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
        for signature, body, inputs in _CATALOG_BODIES:
            name = signature.split("(", 1)[0]
            absent = [table for table in inputs if table not in builds]
            if not with_macros or absent:
                skipped[name] = f"not_built: inputs={absent or list(inputs)}"
                continue
            sources = {t: _partition_source(root, t, builds[t]) for t in inputs}
            con.execute(f"CREATE MACRO {signature} AS TABLE " + body.format(**sources))
            macros.append(signature)
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

# 법인 축(S12·S15·S16) — 005930 과 우선주 005935 는 **같은 법인**이다. 035420 은 법인 대응은
# 있으나 재무·배당·지분 행이 하나도 없고(관측 없음 → 셀 없음), 069500(ETF)은 법인 자체가 없다.
WB_CORP = {"005930": "C05930", "005935": "C05930", "000660": "C00660", "035420": "C35420",
           "036220": "C36220"}
WB_FIN_RCEPT = {  # (corp, period_end) → 접수번호. `disclosure_version` 이 정정 여부를 붙인다
    ("C05930", date(2023, 3, 31)): "R05930Q1",
    ("C05930", date(2023, 6, 30)): "R05930Q2",
    ("C05930", date(2023, 9, 30)): "R05930Q3",
    ("C05930", date(2023, 12, 31)): "R05930FY",
    ("C00660", date(2023, 6, 30)): "R00660Q2",
    ("C36220", date(2023, 12, 31)): "R36220FY",
}
# 005930 2023 4분기 = 연간 − 3분기 누계. 연간 460 = 100 + 110 + 120 + 130 이라 TTM 이 연간과 같다.
WB_FIN_ROWS: list[dict[str, object]] = [
    {"corp_code": "C05930", "period_end": date(2023, 3, 31), "report_code": "11013",
     "bsns_year": "2023", "rcept_no": "R05930Q1", "available_date": date(2023, 5, 15),
     "revenue": 100, "gross_profit": 40, "op_profit": 30, "net_income": 20,
     "total_asset": 1000, "total_liab": 400, "total_equity": 600,
     "cf_operating_ytd": 25, "cf_operating_q": 25},
    {"corp_code": "C05930", "period_end": date(2023, 6, 30), "report_code": "11012",
     "bsns_year": "2023", "rcept_no": "R05930Q2", "available_date": date(2023, 8, 14),
     "revenue": 110, "gross_profit": 44, "op_profit": 33, "net_income": 22,
     "total_asset": 1010, "total_liab": 405, "total_equity": 605,
     "cf_operating_ytd": 60, "cf_operating_q": 35},
    {"corp_code": "C05930", "period_end": date(2023, 9, 30), "report_code": "11014",
     "bsns_year": "2023", "rcept_no": "R05930Q3", "available_date": date(2023, 11, 14),
     "revenue": 120, "gross_profit": 48, "op_profit": 36, "net_income": 24,
     "total_asset": 1020, "total_liab": 410, "total_equity": 610,
     "cf_operating_ytd": 100, "cf_operating_q": 40},
    {"corp_code": "C05930", "period_end": date(2023, 12, 31), "report_code": "11011",
     "bsns_year": "2023", "rcept_no": "R05930FY", "available_date": date(2024, 1, 4),
     "revenue": 460, "gross_profit": 184, "op_profit": 138, "net_income": 92,
     "total_asset": 1030, "total_liab": 415, "total_equity": 615,
     "cf_operating_ytd": 150, "cf_operating_q": 50,
     "revenue_q4_derived": 130, "gross_profit_q4_derived": 52,
     "op_profit_q4_derived": 39, "net_income_q4_derived": 26},
    # 000660 — 같은 grain 에 CFS·OFS 2행. v_fin_latest 는 CFS 를 고른다(OFS 값은 나오면 안 된다).
    {"corp_code": "C00660", "period_end": date(2023, 6, 30), "report_code": "11012",
     "bsns_year": "2023", "rcept_no": "R00660Q2", "available_date": date(2023, 8, 14),
     "fs_div": "CFS", "revenue": 200, "gross_profit": 80, "op_profit": 60, "net_income": 40,
     "total_asset": 2000, "total_liab": 800, "total_equity": 1200,
     "cf_operating_ytd": 70, "cf_operating_q": 30},
    {"corp_code": "C00660", "period_end": date(2023, 6, 30), "report_code": "11012",
     "bsns_year": "2023", "rcept_no": "R00660Q2", "available_date": date(2023, 8, 14),
     "fs_div": "OFS", "revenue": 999, "gross_profit": 999, "op_profit": 999, "net_income": 999,
     "total_asset": 9999, "total_liab": 9999, "total_equity": 9999,
     "cf_operating_ytd": 999, "cf_operating_q": 999},
    # 036220 — 값이 일부만 있는 행(매출 결측 → MISSING, 순이익 7 → OBSERVED). 창 안 공개일.
    {"corp_code": "C36220", "period_end": date(2023, 12, 31), "report_code": "11011",
     "bsns_year": "2023", "rcept_no": "R36220FY", "available_date": date(2024, 1, 9),
     "net_income": 7, "total_asset": 70, "total_liab": 30, "total_equity": 40},
]
WB_CORRECTION = date(2024, 1, 11)  # R05930FY 의 첫 정정 접수일 — v_fin_latest.has_correction 축
WB_CONSENSUS_ROWS: list[ConsensusRow] = [
    # 2023-12 관측점: FY1 = 202312(5,000원), FY2 = 202412(6,000원)
    ("005930", date(2023, 12, 1), "202312", "eps", "wise", date(2023, 12, 29),
     5_000, 4_000, 6_000, False),
    ("005930", date(2023, 12, 1), "202412", "eps", "wise", date(2023, 12, 29),
     6_000, 5_000, 7_000, False),
    # 2024-01 관측점: 202312 은 이미 지난 기간이라 FY1 = 202412(6,500원)
    ("005930", date(2024, 1, 1), "202412", "eps", "wise", date(2024, 1, 5),
     6_500, 6_000, 7_200, False),
    # 매출(억원) — v3 판본이라 min/max 가 없다(dispersion 은 eps 축에서만 선다)
    ("005930", date(2024, 1, 1), "202412", "revenue", "v3", date(2024, 1, 5),
     3_000_000, None, None, True),
    ("000660", date(2023, 12, 1), "202412", "eps", "wise", date(2023, 12, 27),
     1_200, 1_000, 1_500, False),
]
WB_CONSENSUS_AVAILABLE = [
    date(2023, 12, 29), date(2023, 12, 29), date(2024, 1, 5), date(2024, 1, 5),
    date(2023, 12, 27),
]
WB_OPINION_ROWS: list[OpinionRow] = [
    # 같은 (ticker, obs_date) 에 v3·wise 공존 — 잰 판본(wise)이 이긴다
    ("005930", date(2024, 1, 4), "v3", 3.8, 90_000, 25, True),
    ("005930", date(2024, 1, 4), "wise", 4.1, 95_000, 28, False),
    ("005930", date(2024, 1, 9), "v3", 4.0, 97_000, 30, True),
    ("000660", date(2024, 1, 4), "wise", 4.5, 150_000, 20, False),
]
WB_DIVIDEND_ROWS: list[DividendRow] = [
    # 2022 사업연도 — 종류 축 3행. 접는 규칙(값 있는 행 · 최신 연도 · stock_knd 사전순)상 보통주.
    ("C05930", "2022", "11011", "보통주", 361, date(2022, 12, 31), date(2023, 3, 7)),
    ("C05930", "2022", "11011", "우선주", 362, date(2022, 12, 31), date(2023, 3, 7)),
    ("C05930", "2022", "11011", "-", None, date(2022, 12, 31), date(2023, 3, 7)),
    # 2023 사업연도 — 창 안(01-09)에 공개돼 as-of 가 갈린다
    ("C05930", "2023", "11011", "보통주", 400, date(2023, 12, 31), date(2024, 1, 9)),
    ("C00660", "2022", "11011", "보통주", 1_200, date(2022, 12, 31), date(2023, 3, 8)),
]
WB_EVENT_ROWS: list[CorpEventRow] = [
    # 같은 공시일 2건 → 합 1,500,000
    ("E1", "005930", "tsstk_aq", date(2024, 1, 5), 1_000_000),
    ("E2", "005930", "tsstk_aq", date(2024, 1, 5), 500_000),
    # 유형이 다른 행은 event.buyback_amount 에 섞이지 않는다
    ("E3", "005930", "split", date(2024, 1, 8), None),
    ("E4", "000660", "tsstk_aq", date(2023, 12, 27), 2_000_000),
]
# 격자 3테이블(S08~S10) — 셀 종류 4갈래를 한 창 안에서 다 낸다.
#   measured + 값       → OBSERVED      · measured + 0      → OBSERVED(진짜 0)
#   src_omitted (NULL)  → **MISSING**   · empty_response    → MISSING
#   not_collected(NULL) → NOT_COLLECTED
# 01-12 에는 아무 행도 없다 — 격자 원천은 행이 없으면 셀 자체를 내지 않는다(합성 금지).
WB_FLOW_ROWS: list[FlowRow] = [
    # 같은 셀에 두 원천 — 어댑터 pick_order 가 키움을 고른다(KIS 값 9,999 가 나오면 안 된다)
    ("005930", WB_SPLIT_DATE, "kiwoom", 3_000_000, -1_000_000, -2_000_000, ("measured", "none")),
    ("005930", WB_SPLIT_DATE, "kis", 9_999, 9_999, 9_999, ("measured", "none")),
    ("005930", date(2024, 1, 9), "kiwoom", None, None, None, ("src_omitted", "shard_done")),
    ("005930", WB_HALT_DATE, None, None, None, None, ("not_collected", "none")),
    ("005930", date(2024, 1, 11), "kiwoom", 0, 0, 0, ("measured", "none")),
    # 키움이 없는 셀은 KIS 단독 행이다(원천이 상보적이라 셀당 행은 여전히 하나)
    ("000660", WB_SPLIT_DATE, "kis", 500_000, -200_000, -300_000, ("measured", "none")),
    ("000660", date(2024, 1, 9), "kiwoom", None, None, None, ("empty_response", "shard_empty")),
]
WB_SHORT_ROWS: list[ShortRow] = [
    ("005930", WB_SPLIT_DATE, 1_000, 70_000_000, None,
     ("measured", "shard_done"), ("not_collected", "none")),
    ("005930", date(2024, 1, 9), None, None, 12_345,
     ("src_omitted", "shard_done"), ("measured", "unit_ok")),
    # 대차 잔고 음수는 원장 값 그대로 보존한다(GAP-04)
    ("005930", WB_HALT_DATE, None, None, -50,
     ("empty_response", "shard_empty"), ("measured", "unit_ok")),
    ("000660", WB_SPLIT_DATE, 2_000, 100_000_000, 7_000,
     ("measured", "shard_done"), ("measured", "unit_ok")),
]
WB_CREDIT_ROWS: list[CreditRow] = [
    ("005930", WB_SPLIT_DATE, 8_359_855, ("measured", "unit_ok")),
    ("005930", date(2024, 1, 9), None, ("src_omitted", "unit_ok")),
    ("005930", WB_HALT_DATE, None, ("not_collected", "none")),
    # 잔고 > 상장주식수로 격리된 원장 행의 자리 — 셀은 남고 종류는 empty_response 다(결정 9)
    ("005930", date(2024, 1, 11), None, ("empty_response", "unit_ok")),
    ("000660", WB_SPLIT_DATE, 1_234, ("measured", "unit_ok")),
]
WB_HOLDER_ROWS: list[HolderRow] = [
    ("H1", "elestock", "홍길동", "C05930", 1_000, date(2024, 1, 9)),
    ("H1", "elestock", "김철수", "C05930", -400, date(2024, 1, 9)),
    # majorstock 축은 event.insider_net_buy 에 섞이지 않는다
    ("H2", "majorstock", "국민연금", "C05930", 99_999, date(2024, 1, 9)),
    # 값이 결측인 보고 하나뿐 → 합도 결측(MISSING)
    ("H3", "elestock", "박영희", "C00660", None, date(2024, 1, 4)),
]


def wb_close(ticker: str, session: date) -> float:
    """손계산 가능한 종가: base + 500 × 세션 index, 000660 은 분할일부터 절반."""
    index = WB_SESSIONS.index(session)
    close = WB_BASE_CLOSE[ticker] + 500 * index
    if ticker == "000660" and session >= WB_SPLIT_DATE:
        close = close // 2
    return float(close)


# `dataset_profile`(S19) 이 확정한 필드별 공개시차 — 서버 실측 모양 그대로다(랙 0 은 장중 가격
# 축뿐이고 나머지는 1세션). 어댑터는 이 표를 정본으로 읽고, 표가 없을 때만 원천 상수로 폴백한다.
WB_PROFILE_LAG_ZERO = (
    "price.close",
    "price.open",
    "price.volume",
    "price.trading_value",
    "price.adj_close",
)
WB_PROFILE_FIELDS = (
    *WB_PROFILE_LAG_ZERO,
    "price.market_cap",
    "price.shares_outstanding",
    "financial.revenue",
    "financial.gross_profit",
    "financial.operating_income",
    "financial.net_income",
    "financial.operating_cash_flow",
    "financial.total_assets",
    "financial.total_liabilities",
    "financial.book_equity",
    "consensus.forward_eps",
    "consensus.forward_sales",
    "consensus.eps_dispersion",
    "consensus.target_price",
    "consensus.recommendation",
    "consensus.analyst_count",
    "flow.foreign_net_buy",
    "flow.institution_net_buy",
    "flow.retail_net_buy",
    "short.short_sale_value",
    "short.borrowed_quantity",
    "credit.margin_balance",
    "event.dividend_per_share",
    "event.buyback_amount",
    "event.insider_net_buy",
)
WB_PROFILE_ROWS = [
    (
        field_id,
        0 if field_id in WB_PROFILE_LAG_ZERO else 1,
        "session_close" if field_id in WB_PROFILE_LAG_ZERO else "next_session_open",
    )
    for field_id in WB_PROFILE_FIELDS
]


def build_workbench_root(root: Path, *, catalog: bool = True, profile: bool = True) -> Path:
    """워크벤치 어댑터 손 픽스처 equity_root 를 만든다.

    `catalog=False` 면 equity.duckdb 없음, `profile=False` 면 `dataset_profile` 없음
    (어댑터가 원천 상수로 폴백하는 구판 루트).
    """
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
                # S06-2 KRX 기준가 원천 행 — corp_event 에 없어 유형을 모른다. 방향은
                # share_factor 가 정한다(서버 factor_ok 55행이 이 유형이다).
                ("036220", date(2024, 1, 9), "036220:krx_base:2024-01-09",
                 "unknown_krx", 0.5, True),
            ],
            apply_dates=[WB_SPLIT_DATE, date(2024, 1, 3), date(2024, 1, 9)],
            available_dates=[WB_SPLIT_DATE, date(2024, 1, 3), date(2024, 1, 9)],
        ),
        year_column="effective_date",
    )
    write_equity_table(
        root,
        "corp_ticker",
        corp_ticker_table(
            [(t, WB_CORP.get(t), t != "005935") for t in sorted(WB_SEC_TYPES)]
        ),
    )
    # 전방 조정가 표(S23) — price_daily·adj_factor·security_span 이 먼저 있어야 한다
    write_equity_table(root, "price_adj_daily", price_adj_table(root), year_column="date")
    write_equity_table(root, "fin_std", fin_std_table(WB_FIN_ROWS), year_column="period_end")
    write_equity_table(
        root,
        "disclosure_version",
        disclosure_version_table(
            [
                (rcept, WB_CORRECTION if rcept == "R05930FY" else None)
                for rcept in sorted(set(WB_FIN_RCEPT.values()))
            ]
        ),
    )
    write_equity_table(
        root,
        "consensus_daily",
        consensus_table(WB_CONSENSUS_ROWS, WB_CONSENSUS_AVAILABLE),
        year_column="obs_month",
    )
    write_equity_table(
        root, "opinion_daily", opinion_table(WB_OPINION_ROWS), year_column="obs_date"
    )
    write_equity_table(root, "dividend_event", dividend_table(WB_DIVIDEND_ROWS))
    write_equity_table(
        root, "corp_event", corp_event_table(WB_EVENT_ROWS), year_column="announce_date"
    )
    write_equity_table(root, "holder_daily", holder_table(WB_HOLDER_ROWS))
    write_equity_table(root, "flow_daily", flow_table(WB_FLOW_ROWS), year_column="date")
    write_equity_table(root, "short_daily", short_table(WB_SHORT_ROWS), year_column="date")
    write_equity_table(root, "credit_daily", credit_table(WB_CREDIT_ROWS), year_column="date")
    if profile:
        write_equity_table(root, "dataset_profile", profile_table(WB_PROFILE_ROWS))
    if catalog:
        write_catalog(root)
    return root
