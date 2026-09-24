"""v3 점수 표 대조 — 같은 `score_date` 의 두 sqlite 를 맞춰 보고 차이의 원인을 분류한다.

왼쪽 = v3 자체 `quant.db`(정본 비교 대상), 오른쪽 = 우리 호환 DB(`data/compat/quant.db`) 또는
우리 model 층 산출. 플랜 `docs/plans/2026-09-24-v3-merge.md` §2 G-M2·G-M3 의 측정 도구다.
두 DB 는 **읽기 전용**(`?mode=ro` URI)으로만 연다 — v3 원본에 쓰지 않는다.

대조 대상 열은 v3 `backend/db/schema.py:155-187` + `backend/db/migration_sql.py` 의
`score_history`(48열)·`score_history_v2`(21열). 팩터·서브 raw 열은 양쪽 표에 다 있는 숫자 열에서
총점(`composite_score`/`total_score`)·`rank`·`stock_code`·`score_date`·`*_flag` 를 뺀 나머지다.

판정(임계는 인자): Spearman ≥ `spearman_min` · 상위 50 겹침 ≥ `top50_min` ·
|Δrank| > `rank_alert` 종목이 전부 분류(unclassified 0) → `pass`, 아니면 `fail` + 사유 목록.
분류: `universe`(한쪽만 있음) / `input:<열>`(raw 열이 다름 — 상대 차이 큰 순) /
`unclassified`(raw 는 같은데 순위만 다름 → GAP-7 동점 정렬 의심). `v3_defect` 는 분류를 덮지 않는
**추가 표식**으로, 양쪽 `daily_prices` 의 최근 `adj_close/close` 비율이 다른 종목에 붙는다(GAP-4:
v3 adj_close 는 한 번 채워지면 갱신되지 않아 최근 기업행위 종목의 모멘텀이 v3 쪽에서 틀리다).
GAP-5(v3 입력은 날짜 축 없는 스냅샷)에 따라 **같은 날** 비교만 의미가 있다. 과거 날짜 재실행 차이는
정상이므로 이 도구로 판정하지 않는다.

열 목록 불일치(`columns_left_only`/`columns_right_only`)와 rank NULL 종목 수(`n_rank_null`)는
집계·리포트에 남기지만 판정 규칙(위 3항)은 바꾸지 않는다 — 조용히 넘어가지 않게 보이기만 한다.

사용: PYTHONPATH=src python -m model.compare --date YYYY-MM-DD --left PATH --right PATH --out DIR
      [--table score_history|score_history_v2|both] [--spearman-min 0.97] [--top50-min 45]
      [--rank-alert 50] [--tol 1e-9] [--top-ns 30,50,100]
종료 코드: 0 pass · 1 fail · 2 오류(파일·표·행 없음, 잘못된 인자).
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

TABLES: dict[str, str] = {"score_history": "composite_score", "score_history_v2": "total_score"}
KEY_COLS = ("stock_code", "score_date")
RANK_COL = "rank"
# sqlite 타입 유사성(affinity) — 선언 타입에 이 조각이 있으면 숫자 열로 본다.
_NUMERIC_HINTS = ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")
ADJ_RATIO_TOL = 1e-6  # G-M2 ①: adj_close 비율 |Δ| ≤ 1e-6
MD_ALERT_LIMIT = 200


class CompareError(RuntimeError):
    """대조를 시작할 수 없는 상태(파일·표·행 없음, 잘못된 인자)."""


@dataclass(frozen=True)
class FactorDiff:
    """팩터·서브 raw 열 하나의 차이 분포."""

    column: str
    n_both: int            # 양쪽 다 NOT NULL 인 교집합 행 수
    n_null_mismatch: int   # 한쪽만 NULL
    median_abs: float | None
    p90_abs: float | None
    max_abs: float | None
    n_over_tol: int

    def to_dict(self) -> dict[str, object]:
        return {
            "column": self.column, "n_both": self.n_both,
            "n_null_mismatch": self.n_null_mismatch, "median_abs": self.median_abs,
            "p90_abs": self.p90_abs, "max_abs": self.max_abs, "n_over_tol": self.n_over_tol,
        }


@dataclass(frozen=True)
class RankAlert:
    """|Δrank| 가 큰 종목 하나와 그 원인."""

    stock_code: str
    left_rank: int | None
    right_rank: int | None
    rank_diff: int | None           # 왼쪽 rank − 오른쪽 rank(한쪽만이면 None)
    cause: str                      # universe | input:<열> | unclassified
    input_columns: tuple[str, ...]  # 상대 차이 큰 순
    v3_defect: bool                 # adj_close 비율 불일치 후보(GAP-4)

    def to_dict(self) -> dict[str, object]:
        return {
            "stock_code": self.stock_code, "left_rank": self.left_rank,
            "right_rank": self.right_rank, "rank_diff": self.rank_diff, "cause": self.cause,
            "input_columns": list(self.input_columns), "v3_defect": self.v3_defect,
        }


@dataclass(frozen=True)
class CompareResult:
    """한 날짜·한 표의 대조 결과."""

    date: str
    table: str
    left_db: str
    right_db: str
    n_left: int
    n_right: int
    n_common: int
    left_only: tuple[str, ...]
    right_only: tuple[str, ...]
    spearman: float | None
    n_spearman: int
    top_overlap: dict[int, int]
    score_abs_max: float | None
    score_abs_median: float | None
    factors: tuple[FactorDiff, ...]
    columns_left_only: tuple[str, ...]
    columns_right_only: tuple[str, ...]
    rank_alerts: tuple[RankAlert, ...]
    n_unclassified: int
    n_rank_null: int
    verdict: str
    reasons: tuple[str, ...]
    thresholds: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date, "table": self.table,
            "left_db": self.left_db, "right_db": self.right_db,
            "universe": {
                "n_left": self.n_left, "n_right": self.n_right, "n_common": self.n_common,
                "left_only": list(self.left_only), "right_only": list(self.right_only),
            },
            "spearman": self.spearman, "n_spearman": self.n_spearman,
            "top_overlap": {str(k): v for k, v in self.top_overlap.items()},
            "score_abs_max": self.score_abs_max, "score_abs_median": self.score_abs_median,
            "factors": [f.to_dict() for f in self.factors],
            "columns_left_only": list(self.columns_left_only),
            "columns_right_only": list(self.columns_right_only),
            "rank_alerts": [a.to_dict() for a in self.rank_alerts],
            "n_unclassified": self.n_unclassified, "n_rank_null": self.n_rank_null,
            "verdict": self.verdict, "reasons": list(self.reasons),
            "thresholds": self.thresholds,
        }


def normalize_date(date: str) -> str:
    """v3 `score_date` 형식('YYYY-MM-DD')으로 맞춘다. 'YYYYMMDD' 도 받는다."""
    raw = date.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-" and raw.replace("-", "").isdigit():
        return raw
    raise CompareError(f"날짜 형식이 YYYY-MM-DD/YYYYMMDD 가 아니다 — date={date!r}")


def _open_ro(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    if not p.exists():
        raise CompareError(f"sqlite 파일 없음 — path={p}")
    return sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)


def _table_columns(conn: sqlite3.Connection, table: str, side: str,
                   db: str) -> list[tuple[str, str]]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not rows:
        raise CompareError(f"표 없음 — table={table} side={side} db={db}")
    return [(str(r[1]), str(r[2] or "")) for r in rows]


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(f'PRAGMA table_info("{table}")').fetchall())


def _factor_columns(cols: Sequence[tuple[str, str]], total_col: str) -> list[str]:
    """총점·rank·키·*_flag 를 뺀 숫자 열(원본 열 순서 유지)."""
    skip = {*KEY_COLS, total_col, RANK_COL}
    out: list[str] = []
    for name, decl in cols:
        if name in skip or name.endswith("_flag"):
            continue
        upper = decl.upper()
        if any(h in upper for h in _NUMERIC_HINTS):
            out.append(name)
    return out


def _load_rows(conn: sqlite3.Connection, table: str, date: str,
               cols: Sequence[str]) -> dict[str, dict[str, object]]:
    sel = ", ".join(f'"{c}"' for c in cols)
    sql = f'SELECT {sel} FROM "{table}" WHERE score_date = ?'
    out: dict[str, dict[str, object]] = {}
    for row in conn.execute(sql, (date,)):
        rec = dict(zip(cols, row, strict=True))
        out[str(rec["stock_code"])] = rec
    return out


def _as_float(value: object, column: str, code: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    raise CompareError(f"숫자 열에 숫자가 아닌 값 — column={column} stock_code={code} "
                       f"value={value!r} type={type(value).__name__}")


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return int(value)
    return None


def _average_ranks(values: Sequence[float]) -> list[float]:
    """동점은 평균 순위(1 시작)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    """평균 순위 위의 피어슨 상관. 표본 < 2 이거나 한쪽 순위 분산이 0 이면 None."""
    if len(left) != len(right):
        raise CompareError(f"Spearman 입력 길이 불일치 — left={len(left)} right={len(right)}")
    n = len(left)
    if n < 2:
        return None
    lr, rr = _average_ranks(left), _average_ranks(right)
    lm, rm = sum(lr) / n, sum(rr) / n
    num = sum((a - lm) * (b - rm) for a, b in zip(lr, rr, strict=True))
    den = math.sqrt(sum((a - lm) ** 2 for a in lr) * sum((b - rm) ** 2 for b in rr))
    if den == 0.0:
        return None
    return num / den


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _percentile(values: Sequence[float], q: float) -> float | None:
    """nearest-rank 분위수(보간 없음)."""
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, math.ceil(q * len(s)) - 1))
    return s[idx]


def _top_codes(rows: dict[str, dict[str, object]], n: int) -> set[str]:
    ranked = [(r, c) for c, rec in rows.items() if (r := _as_int(rec[RANK_COL])) is not None]
    ranked.sort()
    return {c for _, c in ranked[:n]}


def _adj_ratio(conn: sqlite3.Connection, code: str, date: str) -> float | None:
    """date 이하 마지막 거래일의 adj_close/close. 비율은 전방 조정이면 양쪽이 같아야 한다."""
    row = conn.execute(
        "SELECT adj_close, close FROM daily_prices "
        "WHERE stock_code = ? AND trade_date <= ? AND adj_close IS NOT NULL AND close <> 0 "
        "ORDER BY trade_date DESC LIMIT 1", (code, date)).fetchone()
    if row is None:
        return None
    adj, close = row[0], row[1]
    if not isinstance(adj, int | float) or not isinstance(close, int | float):
        return None
    if float(close) == 0.0:
        return None
    return float(adj) / float(close)


def _input_columns(left: dict[str, object], right: dict[str, object], columns: Sequence[str],
                   tol: float) -> tuple[str, ...]:
    """raw 열 중 |Δ| > tol 또는 NULL 불일치인 열 — 상대 차이 큰 순."""
    scored: list[tuple[float, str]] = []
    for col in columns:
        lv = _as_float(left[col], col, str(left["stock_code"]))
        rv = _as_float(right[col], col, str(right["stock_code"]))
        if lv is None and rv is None:
            continue
        if lv is None or rv is None:
            scored.append((math.inf, col))
            continue
        delta = abs(lv - rv)
        if delta > tol:
            scale = max(abs(lv), abs(rv))
            scored.append((delta / scale if scale > 0 else math.inf, col))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return tuple(col for _, col in scored)


def compare(left_db: str | Path, right_db: str | Path, date: str,
            table: str = "score_history", top_ns: Sequence[int] = (30, 50, 100),
            rank_alert: int = 50, tol: float = 1e-9,
            spearman_min: float = 0.97, top50_min: int = 45) -> CompareResult:
    """두 sqlite 의 같은 표·같은 score_date 를 대조한다.

    Args:
        left_db: 왼쪽 sqlite 경로(v3 `quant.db`).
        right_db: 오른쪽 sqlite 경로(우리 호환 DB 또는 model 산출).
        date: 'YYYY-MM-DD' 또는 'YYYYMMDD'.
        table: `score_history` 또는 `score_history_v2`.
        top_ns: 상위 N 겹침을 잴 N 목록(판정용 50 은 항상 추가된다).
        rank_alert: |Δrank| 가 이 값을 넘는 종목을 원인 분류 대상으로 본다.
        tol: raw 열 차이를 "다르다" 고 볼 절대 허용치.
        spearman_min: 판정 하한.
        top50_min: 상위 50 겹침 판정 하한.

    Returns:
        CompareResult — 유니버스·Spearman·상위 N 겹침·팩터별 분포·원인 분류·판정.

    Raises:
        CompareError: 파일·표가 없거나 어느 한쪽에 그 날짜 행이 없을 때.
    """
    norm_date = normalize_date(date)
    if table not in TABLES:
        raise CompareError(f"모르는 표 — table={table!r} allowed={sorted(TABLES)}")
    total_col = TABLES[table]
    left_path, right_path = str(left_db), str(right_db)

    with closing(_open_ro(left_db)) as lconn, closing(_open_ro(right_db)) as rconn:
        lcols = _table_columns(lconn, table, "left", left_path)
        rcols = _table_columns(rconn, table, "right", right_path)
        lnames, rnames = {c for c, _ in lcols}, {c for c, _ in rcols}
        for side, names, db in (("left", lnames, left_path), ("right", rnames, right_path)):
            missing = [c for c in (*KEY_COLS, total_col, RANK_COL) if c not in names]
            if missing:
                raise CompareError(f"필수 열 없음 — table={table} side={side} "
                                   f"missing={missing} db={db}")
        lfac, rfac = _factor_columns(lcols, total_col), _factor_columns(rcols, total_col)
        factor_cols = [c for c in lfac if c in set(rfac)]
        columns_left_only = tuple(c for c in lfac if c not in set(rfac))
        columns_right_only = tuple(c for c in rfac if c not in set(lfac))

        load_cols = ["stock_code", total_col, RANK_COL, *factor_cols]
        left_rows = _load_rows(lconn, table, norm_date, load_cols)
        right_rows = _load_rows(rconn, table, norm_date, load_cols)
        for side, rows, db in (("left", left_rows, left_path), ("right", right_rows, right_path)):
            if not rows:
                raise CompareError(f"해당 날짜 행 없음 — table={table} score_date={norm_date} "
                                   f"side={side} db={db}")

        common = sorted(set(left_rows) & set(right_rows))
        left_only = tuple(sorted(set(left_rows) - set(right_rows)))
        right_only = tuple(sorted(set(right_rows) - set(left_rows)))

        # 총점: Spearman + |Δ| 분포
        lvals: list[float] = []
        rvals: list[float] = []
        score_deltas: list[float] = []
        for code in common:
            lv = _as_float(left_rows[code][total_col], total_col, code)
            rv = _as_float(right_rows[code][total_col], total_col, code)
            if lv is None or rv is None:
                continue
            lvals.append(lv)
            rvals.append(rv)
            score_deltas.append(abs(lv - rv))
        rho = spearman(lvals, rvals)

        ns = tuple(sorted({*top_ns, 50}))
        top_overlap = {n: len(_top_codes(left_rows, n) & _top_codes(right_rows, n)) for n in ns}

        factors = tuple(_factor_diff(col, left_rows, right_rows, common, tol)
                        for col in factor_cols)

        has_prices = _has_table(lconn, "daily_prices") and _has_table(rconn, "daily_prices")

        def defect(code: str) -> bool:
            if not has_prices:
                return False
            lr, rr = _adj_ratio(lconn, code, norm_date), _adj_ratio(rconn, code, norm_date)
            if lr is None or rr is None:
                return False
            scale = max(abs(lr), abs(rr), 1.0)
            return abs(lr - rr) / scale > ADJ_RATIO_TOL

        alerts: list[RankAlert] = []
        n_rank_null = 0
        for code in common:
            lr_i, rr_i = _as_int(left_rows[code][RANK_COL]), _as_int(right_rows[code][RANK_COL])
            if lr_i is None or rr_i is None:
                n_rank_null += 1
                continue
            if abs(lr_i - rr_i) <= rank_alert:
                continue
            cols = _input_columns(left_rows[code], right_rows[code], factor_cols, tol)
            alerts.append(RankAlert(code, lr_i, rr_i, lr_i - rr_i,
                                    f"input:{cols[0]}" if cols else "unclassified",
                                    cols, defect(code)))
        for code in left_only:
            alerts.append(RankAlert(code, _as_int(left_rows[code][RANK_COL]), None, None,
                                    "universe", (), defect(code)))
        for code in right_only:
            alerts.append(RankAlert(code, None, _as_int(right_rows[code][RANK_COL]), None,
                                    "universe", (), defect(code)))

    alerts.sort(key=lambda a: (a.rank_diff is None, -abs(a.rank_diff or 0), a.stock_code))
    n_unclassified = sum(1 for a in alerts if a.cause == "unclassified")

    reasons: list[str] = []
    if rho is None:
        reasons.append(f"Spearman 계산 불가(표본 {len(lvals)}행·순위 분산 0)")
    elif rho < spearman_min:
        reasons.append(f"Spearman {rho:.4f} < {spearman_min}")
    if top_overlap[50] < top50_min:
        reasons.append(f"상위 50 겹침 {top_overlap[50]} < {top50_min}")
    if n_unclassified:
        reasons.append(f"unclassified {n_unclassified}종목(|Δrank| > {rank_alert})")

    return CompareResult(
        date=norm_date, table=table, left_db=left_path, right_db=right_path,
        n_left=len(left_rows), n_right=len(right_rows), n_common=len(common),
        left_only=left_only, right_only=right_only,
        spearman=rho, n_spearman=len(lvals), top_overlap=top_overlap,
        score_abs_max=max(score_deltas) if score_deltas else None,
        score_abs_median=_median(score_deltas),
        factors=factors, columns_left_only=columns_left_only,
        columns_right_only=columns_right_only,
        rank_alerts=tuple(alerts), n_unclassified=n_unclassified, n_rank_null=n_rank_null,
        verdict="fail" if reasons else "pass", reasons=tuple(reasons),
        thresholds={"spearman_min": spearman_min, "top50_min": float(top50_min),
                    "rank_alert": float(rank_alert), "tol": tol},
    )


def _factor_diff(column: str, left_rows: dict[str, dict[str, object]],
                 right_rows: dict[str, dict[str, object]], common: Sequence[str],
                 tol: float) -> FactorDiff:
    deltas: list[float] = []
    n_null_mismatch = 0
    for code in common:
        lv = _as_float(left_rows[code][column], column, code)
        rv = _as_float(right_rows[code][column], column, code)
        if lv is None and rv is None:
            continue
        if lv is None or rv is None:
            n_null_mismatch += 1
            continue
        deltas.append(abs(lv - rv))
    return FactorDiff(
        column=column, n_both=len(deltas), n_null_mismatch=n_null_mismatch,
        median_abs=_median(deltas), p90_abs=_percentile(deltas, 0.9),
        max_abs=max(deltas) if deltas else None,
        n_over_tol=sum(1 for d in deltas if d > tol),
    )


def _fmt(value: float | None, digits: int = 6) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _render_md(result: CompareResult) -> str:
    r = result
    lines = [
        f"# 점수 대조 {r.date} — {r.table}",
        "",
        f"- 왼쪽(v3): `{r.left_db}`",
        f"- 오른쪽: `{r.right_db}`",
        f"- **판정: {r.verdict}**" + ("" if r.verdict == "pass" else " — " + " / ".join(r.reasons)),
        f"- 임계: Spearman ≥ {r.thresholds['spearman_min']} · 상위 50 겹침 ≥ "
        f"{int(r.thresholds['top50_min'])} · |Δrank| > {int(r.thresholds['rank_alert'])} 분류 · "
        f"tol={r.thresholds['tol']}",
        "",
        "## 요약",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 종목 수(왼쪽/오른쪽/교집합) | {r.n_left} / {r.n_right} / {r.n_common} |",
        f"| 한쪽만(왼쪽/오른쪽) | {len(r.left_only)} / {len(r.right_only)} |",
        f"| Spearman 순위 상관 (n={r.n_spearman}) | {_fmt(r.spearman, 4)} |",
    ]
    for n in sorted(r.top_overlap):
        lines.append(f"| 상위 {n} 겹침 | {r.top_overlap[n]} |")
    lines += [
        # 표 셀 안의 파이프는 markdown 열 구분자라 이스케이프한다
        f"| 총점 \\|Δ\\| max / median | {_fmt(r.score_abs_max)} / {_fmt(r.score_abs_median)} |",
        f"| \\|Δrank\\| > {int(r.thresholds['rank_alert'])} 또는 한쪽만 | {len(r.rank_alerts)} |",
        f"| unclassified | {r.n_unclassified} |",
        f"| rank NULL(비교 제외) | {r.n_rank_null} |",
        f"| 열 목록 차이(왼쪽만/오른쪽만) | {len(r.columns_left_only)} / "
        f"{len(r.columns_right_only)} |",
        "",
        "## 팩터·서브 raw 열",
        "",
        "| 열 | 양쪽 값 | NULL 불일치 | median | p90 | max | >tol |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for f in r.factors:
        lines.append(f"| {f.column} | {f.n_both} | {f.n_null_mismatch} | {_fmt(f.median_abs)} | "
                     f"{_fmt(f.p90_abs)} | {_fmt(f.max_abs)} | {f.n_over_tol} |")
    if r.columns_left_only or r.columns_right_only:
        lines += ["", f"왼쪽에만 있는 열: {list(r.columns_left_only)} / "
                      f"오른쪽에만 있는 열: {list(r.columns_right_only)}"]
    shown = r.rank_alerts[:MD_ALERT_LIMIT]
    lines += [
        "",
        f"## |Δrank| > {int(r.thresholds['rank_alert'])} 종목 "
        f"(총 {len(r.rank_alerts)}개, {len(shown)}개 표시)",
        "",
        "| 종목 | 왼쪽 rank | 오른쪽 rank | Δrank | 분류 | 근거 열 | v3_defect(adj_close) |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for a in shown:
        cols = ", ".join(a.input_columns[:5]) if a.input_columns else "—"
        lines.append(f"| {a.stock_code} | {a.left_rank if a.left_rank is not None else '—'} | "
                     f"{a.right_rank if a.right_rank is not None else '—'} | "
                     f"{a.rank_diff if a.rank_diff is not None else '—'} | {a.cause} | {cols} | "
                     f"{'예' if a.v3_defect else '아니오'} |")
    lines.append("")
    return "\n".join(lines)


def write_report(result: CompareResult, out_dir: str | Path) -> tuple[Path, Path]:
    """`<out_dir>/<date>_<table>.md`(한글 요약)와 `.json`(전체)을 쓴다."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"{result.date}_{result.table}.md"
    json_path = out / f"{result.date}_{result.table}.json"
    md_path.write_text(_render_md(result), encoding="utf-8")
    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return md_path, json_path


def _parse_top_ns(raw: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in raw.split(",") if x.strip())
    except ValueError as exc:
        raise CompareError(f"--top-ns 는 쉼표로 구분한 정수 — value={raw!r}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="model.compare", description="v3 점수 표 대조")
    ap.add_argument("--date", required=True, help="score_date (YYYY-MM-DD 또는 YYYYMMDD)")
    ap.add_argument("--left", required=True, help="왼쪽 sqlite (v3 quant.db)")
    ap.add_argument("--right", required=True, help="오른쪽 sqlite (compat/model 산출)")
    ap.add_argument("--out", required=True, help="리포트 디렉터리")
    ap.add_argument("--table", default="score_history",
                    choices=["score_history", "score_history_v2", "both"])
    ap.add_argument("--spearman-min", type=float, default=0.97)
    ap.add_argument("--top50-min", type=int, default=45)
    ap.add_argument("--rank-alert", type=int, default=50)
    ap.add_argument("--tol", type=float, default=1e-9)
    ap.add_argument("--top-ns", default="30,50,100")
    args = ap.parse_args(argv)

    tables = list(TABLES) if args.table == "both" else [args.table]
    rc = 0
    try:
        top_ns = _parse_top_ns(args.top_ns)
        for table in tables:
            result = compare(args.left, args.right, args.date, table=table, top_ns=top_ns,
                             rank_alert=args.rank_alert, tol=args.tol,
                             spearman_min=args.spearman_min, top50_min=args.top50_min)
            md_path, _ = write_report(result, args.out)
            print(f"{result.table} {result.date} verdict={result.verdict} "
                  f"spearman={_fmt(result.spearman, 4)} top50={result.top_overlap[50]} "
                  f"common={result.n_common} alerts={len(result.rank_alerts)} "
                  f"unclassified={result.n_unclassified} → {md_path}")
            for reason in result.reasons:
                print(f"  ✗ {reason}")
            if result.verdict != "pass":
                rc = 1
    except CompareError as exc:
        print(f"model.compare 오류: {exc}", file=sys.stderr)
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
