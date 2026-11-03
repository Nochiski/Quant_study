"""S27 잠정실적 판본 — `fin_prelim` (N-64 PR ②-1, 계획 `plans/2026-10-31-prelim-earnings.md` 9-2).

grain `rcept_no` · receipt_axis `substr(rcept_no, 1, 4)`. 입력은 stage `stg_doc_prelim`(잠정실적 공정공시 서식 표,
파서 p1.7) · `stg_disclosure`(종목 · 접수일 · 정정 여부) 2표다.

**무엇을 주나** — 잠정 공시 접수 한 건의 당해 당기 매출 · 영업이익 · 법인세차감전 · 순이익 · 지배 순이익(원)과
전년동기 값(단위 검산용), 당기 기간, 연결/별도. 메인 엑셀 Earnings 분기 칸의 'P'(잠정) 원천이다. 모델 점수에는
쓰지 않는다(N-64 Q5) — fi 는 엑셀 전용 표 `fi_fin_prelim` 으로만 낸다.

**고르지 않는다** — 행을 버리지 않고 `prelim_status`(ok · not_quarter · future_period · parse_failed ·
no_disclosure)로 가른다. 정정 접기(늦은 접수가 이김) · 확정 > 잠정 · 연결/별도 고르기 · 공개일 자르기는 기준일 D 가
있어야 정해지므로 fi 몫이다.

테이블 특화 술어(`extra_gates`):
  EG3_fin_prelim — 폐기형: 상태 어휘 폐쇄 · ok 행인데 기간이 3개월 아님 · ok 행인데 기말일 > 접수일 ·
                   parse_failed 행인데 값이 있음 · ok 행인데 종목 폭이 6 아님. 기록형: 상태별 · 기말 분기별 행 수.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, register
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

PRELIM_STATUS: tuple[str, ...] = ("ok", "not_quarter", "future_period", "parse_failed", "no_disclosure")
REJECT_REASONS: tuple[str, ...] = ("available_date_missing",)
_VALUES = ("revenue_krw", "op_profit_krw", "pretax_krw", "net_income_krw", "ni_owner_krw",
           "revenue_py_krw", "op_profit_py_krw", "net_income_py_krw")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def eg3_fin_prelim(ctx: EquityGateContext) -> GateResult:
    """상태 어휘 · ok 행 불변식(3개월 · 기말일 ≤ 접수일 · 종목 폭) · 실패 행 값 없음."""
    v = _q(ctx.out_view)
    vocab = ", ".join(f"'{s}'" for s in PRELIM_STATUS)
    any_value = " OR ".join(f"{c} IS NOT NULL" for c in _VALUES)
    n_vocab, n_not3, n_future, n_failed_val, n_ticker = _row(ctx, f"""
        SELECT
          count(*) FILTER (WHERE prelim_status IS NULL OR prelim_status NOT IN ({vocab})),
          count(*) FILTER (WHERE prelim_status = 'ok'
                           AND date_diff('month', period_from, period_to + INTERVAL 1 DAY) <> 3),
          count(*) FILTER (WHERE prelim_status = 'ok' AND period_to > rcept_dt),
          count(*) FILTER (WHERE prelim_status = 'parse_failed' AND ({any_value})),
          count(*) FILTER (WHERE prelim_status = 'ok' AND (ticker IS NULL OR length(ticker) <> {TICKER_LEN}))
        FROM {v}""")
    checks = {"n_status_outside_vocab": int(str(n_vocab)), "n_ok_not_3_months": int(str(n_not3)),
              "n_ok_period_after_receipt": int(str(n_future)),
              "n_failed_with_values": int(str(n_failed_val)), "n_ok_ticker_bad_width": int(str(n_ticker))}
    by_status = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT prelim_status, count(*) FROM {v} GROUP BY 1 ORDER BY 1").fetchall()}
    by_period = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT strftime(period_to, '%Y-%m'), count(*) FROM {v} WHERE prelim_status = 'ok' "
        "GROUP BY 1 ORDER BY 1 DESC LIMIT 6").fetchall()}
    metrics: dict[str, object] = {**checks, "by_status": by_status, "ok_by_period_end": by_period}
    bad = {k: n for k, n in checks.items() if n}
    if not bad:
        return GateResult("EG3_fin_prelim", GateStatus.PASS, "상태 어휘 · ok 행 불변식 성립", metrics)
    return GateResult("EG3_fin_prelim", GateStatus.FAIL,
                      "; ".join(f"{k}={n}" for k, n in sorted(bad.items())), metrics)


eg3_fin_prelim.gate_name = "EG3_fin_prelim"     # type: ignore[attr-defined]


FIN_PRELIM = register(EquityTable(
    name="fin_prelim",
    grain=("rcept_no",),
    columns={"rcept_no": "VARCHAR", "corp_code": "VARCHAR", "ticker": "VARCHAR", "rcept_dt": "DATE",
             "is_correction": "BOOLEAN", "fs_div": "VARCHAR",
             "period_from": "DATE", "period_to": "DATE", "py_from": "DATE", "py_to": "DATE",
             "unit_raw": "VARCHAR",
             **{c: "DECIMAL(20,0)" for c in _VALUES},
             "prelim_status": "VARCHAR", "parse_error": "VARCHAR",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_doc_prelim", "stg_disclosure"),
    partition_class="receipt_axis",
    partition_key_expr="CAST(substr(rcept_no, 1, 4) AS INTEGER)",
    available_rule=("column:available_date — stg_doc_prelim · stg_disclosure 공개일 중 늦은 쪽(derived, "
                    "둘 다 접수번호 기준 stage 공개일)"),
    eg1_lhs_sql='SELECT count(*) FROM "out_pq"',
    eg1_rhs_sql="SELECT count(DISTINCT rcept_no) FROM stg_doc_prelim",
    sql_path=SQL_DIR / "fin_prelim.sql",
    input_columns={
        "stg_doc_prelim": ("rcept_no", "member_name", "fs_div", "period_from", "period_to", "py_from",
                           "py_to", "unit_raw", *_VALUES, "status", "error", "available_date"),
        "stg_disclosure": ("rcept_no", "rcept_dt", "available_date", "corp_code", "ticker",
                           "is_correction", "observed_date")},
    available_basis=("derived",),
    # 내용일 = 접수일. 기말일(period_to)로 두면 future_period 행(기말일 > 접수일)이 EG2 를 깨는데, 그 행은
    # 버리지 않고 상태로 남기는 것이 이 표의 규약이다
    content_date_column="rcept_dt",
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg3_fin_prelim,),
    # S19 `dataset_profile` 등재는 보류(S25 와 같은 사정 — 이 표가 없는 equity 루트에서 S19 가 죽는다)
    field_profiles=(),
))

TABLES: tuple[EquityTable, ...] = (FIN_PRELIM,)

__all__ = ["FIN_PRELIM", "PRELIM_STATUS", "REJECT_REASONS", "TABLES"]
