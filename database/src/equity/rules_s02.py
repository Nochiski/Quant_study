"""S02 슬라이스 선언 — 캘린더 · 상장 구간 · 지수 (DESIGN v1.2 §4-1, GATES v1.0 §3 ③⑤⑥).

세 테이블은 한 덩어리다. `trading_calendar` 가 거래일 축을 고정하고, `security_span` 이 그 축
위에서 종목의 존재 구간을 자르고, `index_daily` 가 같은 원천(`stg_index_daily`)의 팩트 사본을
낸다. 셋 다 원천이 KRX 지수 일별이라 캘린더 정의가 어긋날 여지를 코드로 없앤다.

**equity 산출을 입력으로 읽지 않는다.** `security_span` 도 캘린더를 `stg_index_daily` 에서
다시 만든다(층 계약 — 입력은 stage `_pinned/` 뿐). 두 정의의 일치는 EG3x 등식이 지킨다.

테이블 특화 술어는 `EquityTable.extra_gates` 훅으로 붙는다:
  EG17  (`trading_calendar`) — 캘린더 무결성: prev/next 체인 · 주말 0 · 중복 0 · 양 끝 상수
  EG3x  (`security_span`)    — 구간 비중첩(EG3-P03) · 캘린더 완결성 · end_reason 어휘
  EG16a (`security_span`)    — 재상장 티커 수 = baseline (EG16 의 상수 절반. 나머지 반인
                               "공백이 폐지·신규상장 신호로 설명되는가" 는 `universe_daily`·
                               `security` 가 생기는 S03 이후에 붙는다)
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext, require_const, require_const_date
from .model import AVAILABLE_NONE, EquityTable, FieldProfile, register

SQL_DIR = Path(__file__).parent / "sql"

# `end_reason` 폐쇄 어휘 (DESIGN §4-1). 'data_gap' 은 예약 — listing 완결성 실측(P11) 때문에
# 지금 원천으로는 산출되지 않지만, 어휘를 열어 두면 나중에 격리 사유와 뒤섞인다.
END_REASONS: tuple[str, ...] = ("delisted", "coverage_gap", "data_gap")


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _const_date(ctx: EquityGateContext, metric: str) -> str:
    """날짜 상수 — `gates.require_const_date` 위임."""
    return require_const_date(ctx, metric)


# ── trading_calendar ─────────────────────────────────────────────────────────

def eg17_calendar_integrity(ctx: EquityGateContext) -> GateResult:
    """EG17 (GATES §6) — 캘린더 무결성.

    격자 전체가 이 축 위에 얹힌다. `prev_td` 체인이 한 칸 어긋나면 모든 룩백 팩터가 하루씩
    밀리는데 EG1(행수)은 그것을 못 본다. 양 끝은 baseline 상수로 못 박는다 — 원천이 잘리면
    조용히 짧아진 캘린더가 통과하는 것을 막는다.
    """
    v = _q(ctx.out_view)
    start = _const_date(ctx, "calendar_start")
    end = _const_date(ctx, "backfill_end")
    n_next, n_prev, n_weekend, n_dup, n_order, got_min, got_max = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} a WHERE a.next_td IS DISTINCT FROM
             (SELECT min(b.date) FROM {v} b WHERE b.date > a.date)),
          (SELECT count(*) FROM {v} a WHERE a.prev_td IS DISTINCT FROM
             (SELECT max(b.date) FROM {v} b WHERE b.date < a.date)),
          (SELECT count(*) FROM {v} WHERE dayofweek(date) IN (0, 6)),
          (SELECT count(*) FROM (SELECT date FROM {v} GROUP BY date HAVING count(*) > 1)),
          (SELECT count(*) FROM {v} WHERE prev_td IS NOT NULL AND prev_td >= date),
          (SELECT CAST(min(date) AS VARCHAR) FROM {v}),
          (SELECT CAST(max(date) AS VARCHAR) FROM {v})""")
    metrics: dict[str, object] = {
        "n_next_td_broken": int(str(n_next)), "n_prev_td_broken": int(str(n_prev)),
        "n_weekend": int(str(n_weekend)), "n_duplicate_date": int(str(n_dup)),
        "n_not_monotonic": int(str(n_order)), "min_date": str(got_min), "max_date": str(got_max),
        "calendar_start": start, "backfill_end": end}
    why: list[str] = []
    if int(str(n_next)):
        why.append(f"next_td chain broken rows={n_next}")
    if int(str(n_prev)):
        why.append(f"prev_td chain broken rows={n_prev}")
    if int(str(n_weekend)):
        why.append(f"weekend trading days={n_weekend}")
    if int(str(n_dup)):
        why.append(f"duplicate dates={n_dup}")
    if int(str(n_order)):
        why.append(f"prev_td not strictly earlier rows={n_order}")
    if str(got_min) != start:
        why.append(f"min(date) expected {start} got {got_min}")
    if str(got_max) != end:
        why.append(f"max(date) expected {end} got {got_max}")
    return GateResult("EG17", GateStatus.PASS if not why else GateStatus.FAIL,
                      "캘린더 무결" if not why else "; ".join(why), metrics)


eg17_calendar_integrity.gate_name = "EG17"      # type: ignore[attr-defined]


# ── security_span ────────────────────────────────────────────────────────────

def eg3x_span_invariants(ctx: EquityGateContext) -> GateResult:
    """EG3x — 구간 불변식. 상수를 안 쓰므로 baseline 유무와 무관하게 항상 돈다.

    P01 같은 티커 구간 비중첩 (GATES §1 EG3-P03 의 교차 형태)
    P02 캘린더 완결성: `stg_listing_daily` distinct date = 캘린더 거래일 수 (EG17 의 짝).
        listing 이 하루라도 비면 그 날짜에 전 종목이 사라져 구간이 둘로 갈린다 = 가짜 재상장
    P03 존재일이 전부 캘린더 위에 있는가. 아니면 그 행은 조인에서 조용히 빠져 Σ n_days 가
        줄고 EG1 이 깨진다 — 그때 원인을 지목하는 진단축이다
    P04 `end_reason` 어휘 폐쇄 (EG3-P13)
    """
    v = _q(ctx.out_view)
    vocab = ", ".join(f"'{r}'" for r in END_REASONS)
    n_overlap, n_listing_dates, n_calendar_days, n_off_cal, n_bad_reason = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} a JOIN {v} b
             ON a.ticker = b.ticker AND a.span_seq < b.span_seq
            WHERE a.last_date >= b.first_date AND b.last_date >= a.first_date),
          (SELECT count(DISTINCT date) FROM stg_listing_daily),
          (SELECT count(DISTINCT date) FROM stg_index_daily),
          (SELECT count(*) FROM (
             (SELECT DISTINCT date FROM stg_listing_daily
              UNION SELECT DISTINCT date FROM stg_etf_price_daily)
             EXCEPT SELECT DISTINCT date FROM stg_index_daily)),
          (SELECT count(*) FROM {v} WHERE end_reason NOT IN ({vocab}))""")
    metrics: dict[str, object] = {
        "n_overlapping_spans": int(str(n_overlap)),
        "n_listing_dates": int(str(n_listing_dates)),
        "n_calendar_days": int(str(n_calendar_days)),
        "n_dates_off_calendar": int(str(n_off_cal)),
        "n_end_reason_outside_vocab": int(str(n_bad_reason)),
        "end_reason_vocab": list(END_REASONS)}
    why: list[str] = []
    if int(str(n_overlap)):
        why.append(f"overlapping spans for same ticker={n_overlap}")
    if int(str(n_listing_dates)) != int(str(n_calendar_days)):
        why.append(f"listing distinct dates {n_listing_dates} != calendar trading days "
                   f"{n_calendar_days}")
    if int(str(n_off_cal)):
        why.append(f"existence dates outside calendar={n_off_cal}")
    if int(str(n_bad_reason)):
        why.append(f"end_reason outside vocabulary rows={n_bad_reason} "
                   f"allowed={list(END_REASONS)}")
    return GateResult("EG3x", GateStatus.PASS if not why else GateStatus.FAIL,
                      "구간 불변식 성립" if not why else "; ".join(why), metrics)


eg3x_span_invariants.gate_name = "EG3x"         # type: ignore[attr-defined]


def eg16a_respan_count(ctx: EquityGateContext) -> GateResult:
    """EG16a (GATES §6 EG16 의 상수 절반) — 구간 2개 이상 티커 수 = `respan_count`.

    span 은 "캘린더 기준 연속 존재 구간의 최대 run" 이라 **원장 결손 하루가 재상장 2구간으로
    읽힌다**. 실측 재상장은 2종(036220·101970)뿐이고, 수집 사고가 이 수를 늘리면 Membership 이
    쪼개져 백테스트 포지션이 강제 청산된다. 공백이 폐지·신규상장 신호로 설명되는지(EG16 의
    나머지 반)는 `universe_daily`·`security` 가 생기는 S03 이후에 붙는다.
    """
    expect = int(require_const(ctx, "respan_count"))
    v = _q(ctx.out_view)
    (got,) = _row(ctx, f"SELECT count(*) FROM (SELECT ticker FROM {v} GROUP BY ticker "
                       "HAVING count(*) >= 2)")
    n_multi = int(str(got))
    metrics: dict[str, object] = {"n_multi_span_tickers": n_multi, "respan_count": expect,
                                  "delta": n_multi - expect}
    ok = n_multi == expect
    return GateResult("EG16a", GateStatus.PASS if ok else GateStatus.FAIL,
                      "재상장 티커 수 일치" if ok else
                      f"multi-span tickers={n_multi} expected={expect} "
                      f"(baseline security_span.respan_count)", metrics)


eg16a_respan_count.gate_name = "EG16a"          # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

TRADING_CALENDAR = register(EquityTable(
    name="trading_calendar",
    grain=("date",),
    columns={"date": "DATE", "prev_td": "DATE", "next_td": "DATE"},
    inputs=("stg_index_daily",),
    partition_class="whole",
    partition_key_expr=None,
    available_rule=AVAILABLE_NONE,      # 차원 테이블 — 거래일이 열렸다는 사실에 공개시점이 없다
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql="SELECT count(DISTINCT date) FROM stg_index_daily",
    sql_path=SQL_DIR / "trading_calendar.sql",
    input_columns={"stg_index_daily": ("date",)},
    extra_gates=(eg17_calendar_integrity,),
))

SECURITY_SPAN = register(EquityTable(
    name="security_span",
    grain=("ticker", "span_seq"),
    columns={"ticker": "VARCHAR", "span_seq": "BIGINT", "first_date": "DATE",
             "last_date": "DATE", "n_days": "BIGINT", "end_reason": "VARCHAR"},
    inputs=("stg_listing_daily", "stg_etf_price_daily", "stg_index_daily"),
    partition_class="whole",
    partition_key_expr=None,
    available_rule=AVAILABLE_NONE,      # 차원 테이블 — 구간은 상태이지 관측 팩트가 아니다
    # EG1 ③(a): Σ n_days = 존재 (ticker, date) 수. 좌변이 행수가 아니라 합인 유일한 테이블이다
    # — 구간이 하나 통째로 사라져도 행수 등식으로는 안 보이기 때문.
    eg1_lhs_sql="SELECT coalesce(sum(n_days), 0) FROM out_pq",
    eg1_rhs_sql=("SELECT count(*) FROM (SELECT ticker, date FROM stg_listing_daily "
                 "UNION SELECT ticker, date FROM stg_etf_price_daily)"),
    sql_path=SQL_DIR / "security_span.sql",
    input_columns={"stg_listing_daily": ("ticker", "date"),
                   "stg_etf_price_daily": ("ticker", "date"),
                   "stg_index_daily": ("date",)},
    extra_gates=(eg3x_span_invariants, eg16a_respan_count),
))

# ── S02-2 벤치마크 필드 (R04 시장 베타) ──────────────────────────────────────
# 막혀 있던 것은 데이터가 아니라 **선언**이었다. 서버 `index_daily` 는 347,821행 · 코스피 51지수 ·
# 코스닥 40지수 · 2010-01-04 ~ 2026-08-20 이고 벤치마크로 쓸 코스피·코스닥·코스피 200·코스닥 150
# 전부 종가가 하루도 빠짐없이 차 있다.
#
# 선언하지 않았던 이유는 **주소 체계**다 — 소비 규약이 모든 값을 `security_id = {종목코드}:{구간}`
# 으로 부르는데 지수는 종목이 아니다. 그래서 커버 축을 종목 격자가 아니라 `table_rows`(표 행수
# 분모)로 둔다(`fin_std` 계열과 같은 축).
#
# **어댑터가 `idx:` 주소를 서빙하는 것은 이 선언과 별개다.** 선언은 「재료가 카탈로그에 있다」는
# 뜻이고, 실제 소비는 워크벤치 어댑터가 그 접두를 알아보아야 가능하다(엔진 계약 변경 — 예약 접두
# `idx:` 는 FIELD_MAP §1 에 이미 어휘로 적혀 있고, 소비자 그릇인
# `BacktestDataQuery.benchmark_security_id` 도 이미 있다). 이 층은 재료를 카탈로그에 올려 두고
# 통로 개설은 소비층에 넘긴다.
INDEX_FIELDS: tuple[FieldProfile, ...] = (
    FieldProfile(
        field_id="benchmark.close", columns=("close_idx",),
        label="지수 종가", unit="pt", value_type="price", frequency="session",
        recommended_lag_sessions=0, recommended_lag_days=0, point_in_time=True,
        requires_confirmation=False,
        disclosure_basis="지수는 가격류라 stage 가 공표 시각을 안다(lag_known=true) — 가격 축과 "
                         "같은 0 세션. 장 마감 뒤 확정값이고 그날 안에 쓸 수 있다",
        evidence="index_daily.close_idx ← stg_index_daily.close_idx. 커버 축은 **표 행수**"
                 "(`table_rows`)다 — 지수는 종목이 아니라 종목 격자에 분모가 없다. "
                 "서버 347,821행 · 코스피 51지수 · 코스닥 40지수 · 2010-01-04 ~ 2026-08-20, "
                 "벤치마크 4종(코스피·코스닥·코스피 200·코스닥 150) 종가 결측 0. "
                 "**소비하려면 어댑터가 `idx:` 주소를 알아보아야 한다** — 이 선언은 재료가 "
                 "카탈로그에 있다는 뜻이고 통로 개설은 소비층 몫이다(FIELD_MAP §1 예약 접두).",
        coverage_axis="table_rows", axis_columns=("index_class", "index_name", "date")),
)

INDEX_DAILY = register(EquityTable(
    name="index_daily",
    grain=("index_class", "index_name", "date"),
    columns={"index_class": "VARCHAR", "index_name": "VARCHAR", "date": "DATE",
             "close_idx": "DECIMAL(10,2)", "change_idx": "DECIMAL(9,2)",
             "fluc_pct": "DECIMAL(6,2)", "open_idx": "DECIMAL(10,2)",
             "high_idx": "DECIMAL(10,2)", "low_idx": "DECIMAL(10,2)",
             "volume_shr": "DECIMAL(12,0)", "value_krw": "DECIMAL(16,0)",
             "mktcap_krw": "DECIMAL(18,0)", "available_date": "DATE",
             "available_basis": "VARCHAR"},
    inputs=("stg_index_daily",),
    partition_class="date_axis",
    partition_key_expr="year(date)",
    available_rule="column:date — 지수는 가격류(stage lag_known=true), 공표 시각 미제공",
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql="SELECT count(*) FROM stg_index_daily",
    sql_path=SQL_DIR / "index_daily.sql",
    input_columns={"stg_index_daily": (
        "index_class", "index_name", "date", "close_idx", "change_idx", "fluc_pct",
        "open_idx", "high_idx", "low_idx", "volume_shr", "value_krw", "mktcap_krw")},
    available_basis=("default",),
    content_date_column="date",
    field_profiles=INDEX_FIELDS,
))

TABLES: tuple[EquityTable, ...] = (TRADING_CALENDAR, SECURITY_SPAN, INDEX_DAILY)

__all__ = ["INDEX_DAILY", "SECURITY_SPAN", "TABLES", "TRADING_CALENDAR"]
