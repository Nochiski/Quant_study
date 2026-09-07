"""S09 슬라이스 선언 — 공매도·대차 일별 격자 `short_daily` (DESIGN v1.2 §4-3, GATES v1.0 §3 ⑪ ·
§2 13행 · §4 FX-3-001·004·009 · §1 EG7-P06).

3단계 격자 테이블의 첫 구현이다. 격자 정의(DESIGN §4-3)는 `universe_daily`(`status ∈ {listed,
suspended}` ∧ `sec_type <> 'etf'`) 그 자체이고, `trading_calendar` 는 첫 세션(하한)만 준다 —
격자 밖 원장 행을 `pre_calendar`(캘린더 하한 이전)와 `off_grid`(유니버스 밖)로 가르는 축이다.

**원천 결합 규약(사용자 확정 09-06)**: 같은 사실의 두 원천(키움 ka10014 · KIS kis_short_sale
공매도)을 하나로 합치지 않고 접미사 `_kiwoom`·`_kis` 컬럼으로 나란히 둔다. `flow_daily`(S08)처럼
`src` 를 PK 에 넣지 않으므로 grain 은 (date, ticker) 이고 EG1 좌변은 `count(*)` 다(GATES §3 ⑪ 의
「src 가 PK 에 들면 count(DISTINCT (date,ticker))」 단서는 이 테이블에 해당하지 않는다). 두 원천의
커버가 갈라져 있어서 고른 규약이다 — 절단본 실측: KIS 는 폐지 3종(000030·900050·900060), 키움은
존속 8종, 겹치는 (ticker, date) 0행. stage 가 단위를 측정하지 못한 축(`shrts_avg_pric`)은 원값을
`_raw` 로 싣고 `short_avg_price_kiwoom_basis`(= 'unknown')를 동반한다.

입력 — equity 2(`trading_calendar`·`universe_daily`) + stage 원천 3(`stg_short_daily_kiwoom`·
`stg_short_daily_kis`·`stg_loan_daily_kis`) + stage 로그 축 2(`stg_shards_kiwoom`·`stg_units_kis`).
로그 축은 팩트 원천이 아니라 `fill_kind` 판정의 증거다(DESIGN §3·§4-3 — 이 축이 없으면 `fill_kind`
가 `measured`/`not_collected` 두 값으로 무너지고 evidence 어휘가 죽는다). 숫자 상수는 없다.

**설계와 다른 점 — 대차 키움 축 미탑재**: DESIGN §4-3 은 대차를 `lending_balance_kis_shr` ·
`lending_balance_kiwoom_raw` 두 축으로 적었지만 키움 대차 원장(`stg_lending_daily`)이 이 슬라이스의
입력 3원천에 없다(절단본에도 없다). S09 는 KIS 축만 싣고, 키움 축과 「겹침 구간 비율 분포」(= 두
대차 축의 단위 대조)는 `stg_lending_daily` 가 입력에 들어오는 후속 슬라이스 몫이다.
EG3_short_daily 의 겹침 지표는 그동안 **공매도 두 원천**(키움 ↔ KIS)의 겹침 구간에 걸어 둔다 —
같은 사실을 두 원천이 재는 유일한 축이라 단위·정의 어긋남이 여기서 먼저 보인다.

싣지 않는 축과 이유:
  · KIS 누적 6컬럼(`acml_*`)  — 요청 창 첫 행에서 리셋된다(STAGE_SPEC §2-6, 판독 플래그
    `acml_valid`). 수집 창이라는 **수집 산물**이지 시장 사실이 아니라 팩트 테이블에 넣지 않는다.
    창 안 누적이 필요하면 소비자가 `short_volume_*_shr` 를 누적한다. 창 첫 행 건수는
    EG3_short_daily 가 `n_kis_acml_invalid` 로 기록한다.
  · 키움 `ovr_shrts_qty_shr`  — 같은 이유. 절단본 실측으로 **샤드 요청창 시작부터의
    `shrts_qty_shr` 누적합**임을 확인했다(005930 2008-06-23~ 전건 일치)이므로 원값에 새 정보가 없다.
  · 두 원천의 종가·전일대비·거래량 — `price_daily`(KRX 정본)에 이미 있다. 원천 간 종가 대조는
    stage G9(KRX ⋈ 키움 종가 100%)가 이미 본 축이다.

테이블 특화 술어(`extra_gates`, 실행은 EG3 뒤):
  EG1_short_daily — GATES §3 ⑪ (b) **소스별 원장 보존**. 프레임 EG1 은 등식 하나(⑪ (a) 격자)만
                    실을 수 있어 (b) 를 훅으로 뗀다. 원천마다
                    `n_src − n_dedup − n_measured − n_offgrid = 0` 을 본다. `n_offgrid` 는 산출의
                    `_reject/` 를 읽지 않고 stage × `universe_daily` 로 독립 재계산한다
                    (항진명제 금지).
  EG3_short_daily — **위반형은 어휘·격자뿐**: `fill_kind.*` 어휘 폐쇄(kind·evidence, 3원천) ·
                    `*_basis` 어휘 폐쇄 · ticker 폭(EG3-P07) · 산출 ⊆ 격자 ∧ 격자 ⊆ 산출.
                    나머지는 전부 **기록형 metric**(GATES §0-1): 단위 라벨표 · 음수 대차 잔고 건수 ·
                    KIS/키움 공매도 겹침 구간의 상관·비율 분위수 · `fill_kind` 분포 · 로그 근거 없는
                    0 값 건수(EG9-P04 예비 측정) · 샤드 접힘 건수 · 격자의 가격 축 결측.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import (
    BASIS_VOCAB,
    FILL_EVIDENCE,
    FILL_KINDS,
    EquityTable,
    FieldProfile,
    register,
)
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# DESIGN §4-3 격자 정의. `universe_daily.status`·`sec_type` 어휘의 부분집합이며, 값이 rules_s03·
# rules_s01 의 어휘 안에 있는지는 test_equity_s09_short 가 대조한다.
GRID_STATUSES: tuple[str, ...] = ("listed", "suspended")
GRID_EXCLUDED_SEC_TYPES: tuple[str, ...] = ("etf",)
# EG7-P06 — 격자 3종 공통 격리 사유. `_reject/<reason>/` 디렉토리 이름이자 EG3 어휘 폐쇄 대상.
REJECT_REASONS: tuple[str, ...] = ("pre_calendar", "off_grid")

# 원천 3종의 축 — (라벨, stage 테이블, fill_kind 컬럼, 「미수집→0」 검사용 값 컬럼).
# 라벨은 metric 이름의 접미사이자 EG1_short_daily 의 등식 이름이다.
SOURCES: tuple[tuple[str, str, str, str], ...] = (
    ("short_kiwoom", "stg_short_daily_kiwoom", "fill_kind_short_kiwoom",
     "short_volume_kiwoom_shr"),
    ("short_kis", "stg_short_daily_kis", "fill_kind_short_kis", "short_volume_kis_shr"),
    ("loan_kis", "stg_loan_daily_kis", "fill_kind_loan_kis", "lending_balance_kis_shr"),
)

# 단위 라벨표(기록형) — 산출 컬럼 → stage 가 실제로 측정한 단위. `_raw` 는 「단위를 모르면 접미사
# 금지」(STAGE_DESIGN §5)로 남은 축이고 equity 도 재라벨하지 않는다.
UNIT_LABELS: dict[str, str] = {
    "short_volume_kiwoom_shr": "shares",
    "short_value_kiwoom_krw": "krw (stage ×1e3)",
    "short_weight_kiwoom_pct": "percent",
    "short_avg_price_kiwoom_raw": "unknown",
    "short_volume_kis_shr": "shares",
    "short_value_kis_krw": "krw",
    "short_volume_ratio_kis_pct": "percent",
    "short_value_ratio_kis_pct": "percent",
    "short_avg_price_kis_krw": "krw",
    "lending_new_kis_shr": "shares",
    "lending_redeem_kis_shr": "shares",
    "lending_balance_kis_shr": "shares",
    "lending_balance_kis_krw": "krw (stage ×1e6)",
}
# 겹침 구간 비율 분포에 쓰는 분위수(기록형이라 baseline 상수가 아니다).
RATIO_QUANTILES: tuple[float, ...] = (0.05, 0.25, 0.5, 0.75, 0.95)


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _n(ctx: EquityGateContext, sql: str) -> int:
    return int(str(_row(ctx, sql)[0]))


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lits(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과. 위반 항목명·건수를 detail 에 싣는다(rules_s04 규약)."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


_GRID_SQL = (f"SELECT date, ticker FROM universe_daily "
             f"WHERE status IN ({_lits(GRID_STATUSES)}) "
             f"AND sec_type NOT IN ({_lits(GRID_EXCLUDED_SEC_TYPES)})")


def eg1_short_daily(ctx: EquityGateContext) -> GateResult:
    """GATES §3 ⑪ (b) — 소스별 원장 보존. 원천 행은 격자 셀 · 접힘 · 격리 셋 중 하나로만 간다.

    `n_src − n_dedup − n_measured − n_offgrid = 0`.
      · `n_dedup`   재수집 판본 접힘(stage `key_unique=false` 인 KIS 두 테이블) = 원장 행수 −
                    distinct (ticker, date)
      · `n_measured` 산출 격자 행 중 그 원천이 `fill_kind.kind='measured'` 인 셀
      · `n_offgrid`  격자 밖 (ticker, date) 수 — 산출의 `_reject/` 가 아니라 stage ⋈ universe_daily
                     로 **독립 재계산**한다(§5-C 항진명제 금지). 사유별로 갈라 metric 에 남긴다.
    """
    checks: dict[str, int] = {}
    metrics: dict[str, object] = {}
    v = _q(ctx.out_view)
    for label, stg, fk, _value_col in SOURCES:
        n_src, n_key, n_pre, n_off = _row(ctx, f"""
            WITH s AS (SELECT DISTINCT ticker, date FROM {_q(stg)}),
                 g AS ({_GRID_SQL})
            SELECT (SELECT count(*) FROM {_q(stg)}),
                   (SELECT count(*) FROM s),
                   (SELECT count(*) FROM s
                     WHERE s.date < (SELECT min(date) FROM trading_calendar)),
                   (SELECT count(*) FROM s
                     WHERE s.date >= (SELECT min(date) FROM trading_calendar)
                       AND NOT EXISTS (SELECT 1 FROM g
                                       WHERE g.ticker = s.ticker AND g.date = s.date))""")
        n_measured = _n(ctx, f"SELECT count(*) FROM {v} WHERE {_q(fk)}.kind = 'measured'")
        n_src_i, n_key_i = int(str(n_src)), int(str(n_key))
        n_pre_i, n_off_i = int(str(n_pre)), int(str(n_off))
        n_dedup = n_src_i - n_key_i
        checks[f"delta_{label}"] = n_src_i - n_dedup - n_measured - n_pre_i - n_off_i
        metrics[f"n_src_{label}"] = n_src_i
        metrics[f"n_dedup_{label}"] = n_dedup
        metrics[f"n_measured_{label}"] = n_measured
        metrics[f"n_reject_pre_calendar_{label}"] = n_pre_i
        metrics[f"n_reject_off_grid_{label}"] = n_off_i
    metrics["reject_by_reason"] = dict(ctx.reject_by_reason)
    return _result("EG1_short_daily", checks, metrics, "소스별 원장 보존 등식 성립")


eg1_short_daily.gate_name = "EG1_short_daily"       # type: ignore[attr-defined]


def eg3_short_daily(ctx: EquityGateContext) -> GateResult:
    """EG3 특화 — 위반형은 어휘·격자뿐이고 나머지는 기록형(사용자 확정 09-06).

    폐기 조건에 값 검사를 넣지 않는 이유: 대차 잔고 음수(stage 가 `keep` 한 원장 사실, HANDOFF §3)
    · 겹침 구간의 원천 간 비율 · 단위 미측정 축은 전부 **서버 실측 뒤 baseline 승격**을 기다리는
    측정치이지 이 테이블의 결함이 아니다. 지금 임계를 코드에 박으면 사실을 게이트가 지운다.
    """
    v = _q(ctx.out_view)
    checks: dict[str, int] = {}
    metrics: dict[str, object] = {}

    # ① fill_kind 어휘 폐쇄 (kind · evidence, 원천 3) + `*_basis` 어휘 폐쇄
    n_vocab = 0
    for _label, _stg, fk, _value_col in SOURCES:
        n_vocab += _n(ctx, f"""
            SELECT count(*) FROM {v}
             WHERE {_q(fk)}.kind IS NULL
                OR {_q(fk)}.kind NOT IN ({_lits(FILL_KINDS)})
                OR {_q(fk)}.evidence IS NULL
                OR {_q(fk)}.evidence NOT IN ({_lits(FILL_EVIDENCE)})""")
    checks["n_fill_kind_outside_vocab"] = n_vocab
    checks["n_unit_basis_outside_vocab"] = _n(ctx, f"""
        SELECT count(*) FROM {v}
         WHERE short_avg_price_kiwoom_basis IS NOT NULL
           AND short_avg_price_kiwoom_basis NOT IN ({_lits(BASIS_VOCAB)})""")

    # ② 격자 — 산출 = universe_daily 재계산 격자. 양방향으로 본다(빠진 셀 · 격자 밖 셀).
    n_missing, n_extra, n_no_price = _row(ctx, f"""
        WITH g AS ({_GRID_SQL})
        SELECT (SELECT count(*) FROM g
                 WHERE NOT EXISTS (SELECT 1 FROM {v} o
                                   WHERE o.ticker = g.ticker AND o.date = g.date)),
               (SELECT count(*) FROM {v} o
                 WHERE NOT EXISTS (SELECT 1 FROM g
                                   WHERE g.ticker = o.ticker AND g.date = o.date)),
               (SELECT count(*) FROM universe_daily u
                 WHERE u.status IN ({_lits(GRID_STATUSES)})
                   AND u.sec_type NOT IN ({_lits(GRID_EXCLUDED_SEC_TYPES)})
                   AND u.no_trade_run IS NULL)""")
    checks["n_grid_missing"] = int(str(n_missing))
    checks["n_grid_extra"] = int(str(n_extra))
    checks["n_ticker_bad_width"] = _n(ctx, f"""
        SELECT count(*) FROM {v}
         WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR' OR length(ticker) <> {TICKER_LEN}""")

    # ③ fill_kind 분포 · 로그 근거 없는 0(EG9-P04 예비) · 원장 행인데 로그가 empty 인 모순
    for label, _stg, fk, value_col in SOURCES:
        metrics[f"fill_kind_{label}"] = {
            f"{r[0]}:{r[1]}": int(str(r[2])) for r in ctx.con.execute(
                f"SELECT {_q(fk)}.kind, {_q(fk)}.evidence, count(*) FROM {v} "
                "GROUP BY 1, 2 ORDER BY 1, 2").fetchall()}
        metrics[f"n_zero_without_log_evidence_{label}"] = _n(ctx, f"""
            SELECT count(*) FROM {v}
             WHERE {_q(value_col)} = 0 AND {_q(fk)}.kind <> 'measured'
               AND {_q(fk)}.evidence = 'none'""")
        metrics[f"n_measured_with_empty_evidence_{label}"] = _n(ctx, f"""
            SELECT count(*) FROM {v}
             WHERE {_q(fk)}.kind = 'measured'
               AND {_q(fk)}.evidence IN ('shard_empty', 'unit_empty')""")

    # ④ 대차 축(기록형) — 음수 잔고는 stage 가 keep 한 원장 사실이다(HANDOFF §3 「음수 2,691 keep」)
    (n_bal_neg, n_amt_neg, bal_min, n_bal_null, n_avg_kis_null,
     n_avg_kw_null, n_kis_acml_invalid) = _row(ctx, f"""
        SELECT (SELECT count(*) FROM {v} WHERE lending_balance_kis_shr < 0),
               (SELECT count(*) FROM {v} WHERE lending_balance_kis_krw < 0),
               (SELECT min(lending_balance_kis_shr) FROM {v}),
               (SELECT count(*) FROM {v}
                 WHERE fill_kind_loan_kis.kind = 'measured'
                   AND lending_balance_kis_shr IS NULL),
               (SELECT count(*) FROM {v}
                 WHERE fill_kind_short_kis.kind = 'measured' AND short_avg_price_kis_krw IS NULL),
               (SELECT count(*) FROM {v}
                 WHERE fill_kind_short_kiwoom.kind = 'measured'
                   AND short_avg_price_kiwoom_raw IS NULL),
               (SELECT count(*) FROM stg_short_daily_kis WHERE NOT acml_valid)""")

    # ⑤ 겹침 구간(기록형) — 같은 (ticker, date) 를 키움·KIS 가 둘 다 잰 셀의 상관·비율 분위수.
    #     DESIGN §4-3 은 이 지표를 대차 두 축(KIS·키움)에 걸라고 적었지만 키움 대차 원장이 입력에
    #     없다(모듈 docstring) — 같은 사실을 두 원천이 재는 유일한 축인 공매도에 건다.
    q_sel = ", ".join(f"(SELECT quantile_cont(r, {p}) FROM r)" for p in RATIO_QUANTILES)
    overlap = _row(ctx, f"""
        WITH b AS (
          SELECT short_volume_kiwoom_shr AS kw_qty, short_volume_kis_shr AS kis_qty,
                 short_value_kiwoom_krw AS kw_val, short_value_kis_krw AS kis_val
          FROM {v}
          WHERE fill_kind_short_kiwoom.kind = 'measured' AND fill_kind_short_kis.kind = 'measured'),
             r AS (SELECT *, kis_qty / nullif(kw_qty, 0) AS r FROM b WHERE kw_qty > 0)
        SELECT (SELECT count(*) FROM b),
               (SELECT corr(CAST(kw_qty AS DOUBLE), CAST(kis_qty AS DOUBLE)) FROM b),
               (SELECT corr(CAST(kw_val AS DOUBLE), CAST(kis_val AS DOUBLE)) FROM b),
               (SELECT count(*) FROM r),
               {q_sel}""")
    n_overlap, corr_qty, corr_val, n_ratio, *ratio_q = overlap

    n_shard_rows, n_shard_tickers = _row(ctx, """
        SELECT count(*), count(DISTINCT ticker) FROM stg_shards_kiwoom WHERE src_api = 'ka10014'""")

    metrics.update({
        "unit_labels": dict(UNIT_LABELS),
        "n_lending_balance_negative": int(str(n_bal_neg)),
        "n_lending_balance_krw_negative": int(str(n_amt_neg)),
        "lending_balance_min_shr": None if bal_min is None else str(bal_min),
        "n_lending_balance_null_measured": int(str(n_bal_null)),
        "n_short_avg_price_kis_null_measured": int(str(n_avg_kis_null)),
        "n_short_avg_price_kiwoom_null_measured": int(str(n_avg_kw_null)),
        "n_kis_acml_invalid": int(str(n_kis_acml_invalid)),
        "n_grid_without_price_axis": int(str(n_no_price)),
        "n_overlap_src_measured": int(str(n_overlap)),
        "corr_short_volume_kis_kiwoom": None if corr_qty is None else float(str(corr_qty)),
        "corr_short_value_kis_kiwoom": None if corr_val is None else float(str(corr_val)),
        "n_overlap_ratio_rows": int(str(n_ratio)),
        "short_volume_ratio_quantiles": {
            str(p): (None if q is None else float(str(q)))
            for p, q in zip(RATIO_QUANTILES, ratio_q, strict=True)},
        "n_kiwoom_shard_rows": int(str(n_shard_rows)),
        "n_kiwoom_shard_tickers": int(str(n_shard_tickers)),
    })
    return _result("EG3_short_daily", checks, metrics, "fill_kind·basis 어휘 폐쇄 · 격자 일치")


eg3_short_daily.gate_name = "EG3_short_daily"       # type: ignore[attr-defined]


# ── S19 필드 선언 (DESIGN §4-7 · FIELD_MAP §2 `short.*`) ─────────────────────
# **랙 1 세션**. 세 원장(`stg_short_daily_kiwoom`·`stg_short_daily_kis`·`stg_loan_daily_kis`)이
# 전부 stage `lag_known=false` 라 공표 시각을 잰 적이 없다 — STAGE_HANDOFF §2 「lag_known=false 는
# lag 0 을 적용하면 안 된다」 + FIELD_MAP §1 랙 단위 「나머지 전부 1 세션」.
# 원천별 접미사 컬럼을 나란히 두는 테이블이므로(사용자 확정 09-06) **field_id 하나 = 컬럼 하나**로
# 선언한다 — 두 원천을 폴백 병합하면 시계열이 원천을 섞는다(FIELD_MAP §2 `short.short_sale_value`).
# 선언하지 않는 것: `short.short_balance_ratio` 는 공매도량 ÷ 상장주식수라 **비율 계산이 팩터층
# 몫**이고 이 테이블에 그 컬럼이 없다(FIELD_MAP §2 「진짜 잔고 아님」). KIS 공매도 축
# (`short_volume_kis_shr`·`short_value_kis_krw` …)과 대차 금액축(`lending_balance_kis_krw`)도
# field_id 가 없어 선언하지 않는다 — 없는 것을 선언하면 프로파일에 '있는데 늘 빈' 행이 생긴다.
_SAXIS: tuple[str, str] = ("ticker", "date")

FIELDS: tuple[FieldProfile, ...] = (
    FieldProfile(
        field_id="short.short_sale_value", columns=("short_value_kiwoom_krw",),
        label="공매도 거래대금(키움)", unit="KRW", value_type="amount", frequency="session",
        recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=False,
        disclosure_basis="원장 날짜 = 매매일. 키움 ka10014 는 공표 시각을 주지 않는다"
                         "(stage lag_known=false) → 익일 지식으로 쓴다",
        evidence="short_daily.short_value_kiwoom_krw ← stg_short_daily_kiwoom."
                 "shrts_trde_prica_krw(stage unit_scale=1e3 완료 → **원 단위**). 원천은 키움 "
                 "하나로 고정이고 미결이 아니다 — KRX 정본이 없는 축이라 둘 중 하나를 골라야 "
                 "했고, 커버가 넓은 쪽을 골랐다(절단본 measured 키움 18,265 vs KIS 3,891). KIS 축 "
                 "`short_value_kis_krw` 는 같은 테이블에 그대로 남아 있지만 이 field_id 로 "
                 "폴백 병합하지 않는다(단위·정의 차가 조용히 섞인다). 결측 사유는 "
                 "fill_kind_short_kiwoom 이 나르고, 키움 공매도 평균가는 단위 미측정이라 "
                 "`short_avg_price_kiwoom_raw` + `_basis='unknown'` 로 따로 남는다(FX-3-009).",
        coverage_axis="grid_session", axis_columns=_SAXIS),
    FieldProfile(
        field_id="short.borrowed_quantity", columns=("lending_balance_kis_shr",),
        label="대차잔고(주식수, KIS)", unit="주", value_type="count", frequency="session",
        recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=False,
        disclosure_basis="원장 날짜 = 대차 잔량 기준일. KIS kis_loan_trans 는 공표 시각을 주지 "
                         "않는다(stage lag_known=false) → 익일 지식으로 쓴다",
        evidence="short_daily.lending_balance_kis_shr ← stg_loan_daily_kis.rmnd_stcn_shr(주수, "
                 "stage 측정 단위). **KIS 축뿐이다**(GAP-04) — 키움 대차 원장 stg_lending_daily "
                 "가 S09 입력에 없어 `lending_balance_kiwoom_raw`(단위 미측정) 는 후속 슬라이스 "
                 "몫이고, 그래서 고를 원천이 하나뿐이라 축 선택이 미결이 아니다. 좁은 커버"
                 "(유닛 dataset='loan' 287 티커)는 조건이 아니라 이 행의 "
                 "estimated_coverage_pct·coverage_from 이 재는 사실이다. 원장이 주는 음수 잔고는 "
                 "자르지 않고 그대로 보존한다(원칙 ④) — 건수는 EG3_short_daily 기록형. 금액축 "
                 "`lending_balance_kis_krw`(stage ×1e6)는 별개 컬럼이고 field_id 가 없다.",
        coverage_axis="grid_session", axis_columns=_SAXIS),
)


# ── 선언 ─────────────────────────────────────────────────────────────────────

SHORT_DAILY = register(EquityTable(
    name="short_daily",
    grain=("date", "ticker"),
    columns={
        "date": "DATE", "ticker": "VARCHAR",
        # 공매도 — 키움 ka10014 (`shrts_avg_pric` 는 stage 가 단위를 못 잰 축이라 `_raw` + `_basis`)
        "short_volume_kiwoom_shr": "DECIMAL(10,0)",
        "short_value_kiwoom_krw": "DECIMAL(15,0)",
        "short_weight_kiwoom_pct": "DECIMAL(7,2)",
        "short_avg_price_kiwoom_raw": "DECIMAL(9,0)",
        "short_avg_price_kiwoom_basis": "VARCHAR",
        # 공매도 — KIS kis_short_sale
        "short_volume_kis_shr": "DECIMAL(9,0)",
        "short_value_kis_krw": "DECIMAL(14,0)",
        "short_volume_ratio_kis_pct": "DECIMAL(8,2)",
        "short_value_ratio_kis_pct": "DECIMAL(7,2)",
        "short_avg_price_kis_krw": "DECIMAL(9,0)",
        # 대차 — KIS kis_loan_trans (키움 축은 `stg_lending_daily` 가 입력에 들어올 때, docstring)
        "lending_new_kis_shr": "DECIMAL(11,0)",
        "lending_redeem_kis_shr": "DECIMAL(10,0)",
        "lending_balance_kis_shr": "DECIMAL(11,0)",
        "lending_balance_kis_krw": "DECIMAL(15,0)",
        # 결측 3분류 — 원천마다 하나 (DESIGN §3 격자 테이블 규약)
        "fill_kind_short_kiwoom": "STRUCT(kind VARCHAR, evidence VARCHAR)",
        "fill_kind_short_kis": "STRUCT(kind VARCHAR, evidence VARCHAR)",
        "fill_kind_loan_kis": "STRUCT(kind VARCHAR, evidence VARCHAR)",
        "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("trading_calendar", "universe_daily", "stg_short_daily_kiwoom", "stg_short_daily_kis",
            "stg_loan_daily_kis", "stg_shards_kiwoom", "stg_units_kis"),
    partition_class="date_axis",
    partition_key_expr="year(date)",
    available_rule="column:date — 세 원천 모두 stage lag_known=false·basis default, 공표 시각은 "
                   "dataset_profile(S19) 몫",
    # GATES §3 ⑪ (a): 좌변 = 산출 행수, 우변 = 격자 재계산 + 격자 밖 원장 셀. 프레임이 우변에서
    # n_reject 를 빼므로 등식은 `count(out) = 격자` 로 닫힌다. (b) 소스별 보존은 EG1_short_daily.
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=f"""
        WITH g AS ({_GRID_SQL}),
             s AS (SELECT ticker, date FROM stg_short_daily_kiwoom
                   UNION SELECT ticker, date FROM stg_short_daily_kis
                   UNION SELECT ticker, date FROM stg_loan_daily_kis)
        SELECT (SELECT count(*) FROM g)
             + (SELECT count(*) FROM s
                 WHERE NOT EXISTS (SELECT 1 FROM g
                                   WHERE g.ticker = s.ticker AND g.date = s.date))""",
    sql_path=SQL_DIR / "short_daily.sql",
    input_columns={
        "trading_calendar": ("date",),
        "universe_daily": ("date", "ticker", "status", "sec_type", "no_trade_run"),
        "stg_short_daily_kiwoom": ("ticker", "date", "shrts_qty_shr", "shrts_trde_prica_krw",
                                   "trde_wght_pct", "shrts_avg_pric", "observed_date",
                                   "observed_n"),
        "stg_short_daily_kis": ("ticker", "date", "ssts_cntg_qty_shr", "ssts_tr_pbmn_krw",
                                "ssts_vol_rlim_pct", "ssts_tr_pbmn_rlim_pct", "avrg_prc_krw",
                                "acml_valid", "observed_date", "observed_n"),
        "stg_loan_daily_kis": ("ticker", "date", "new_stcn_shr", "rdmp_stcn_shr", "rmnd_stcn_shr",
                               "rmnd_amt_krw", "observed_date", "observed_n"),
        "stg_shards_kiwoom": ("src_api", "ticker", "req_start", "req_end", "status"),
        "stg_units_kis": ("dataset", "ticker", "status", "window_from", "window_to")},
    available_basis=("default",),
    content_date_column="date",
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg1_short_daily, eg3_short_daily),
    field_profiles=FIELDS,
))

TABLES: tuple[EquityTable, ...] = (SHORT_DAILY,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s09.json"

__all__ = ["FIELDS", "GRID_EXCLUDED_SEC_TYPES", "GRID_STATUSES", "REJECT_REASONS",
           "SHORT_DAILY", "SOURCES", "TABLES", "UNIT_LABELS"]
