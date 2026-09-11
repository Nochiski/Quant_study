"""S04 슬라이스 선언 — `price_daily` 가격 정본 (DESIGN v1.2 §4-2, GATES v1.0 §3 ⑧ · EG7-P01 · EG20).

원칙 ②("원주가 불변 + 계수 분리")를 코드로 고정하는 슬라이스다. OHLC·거래량·거래대금은 stage 값을
그대로 나르고(수정 없음), 조정은 S06 `adj_factor` 와 뷰가 한다. 파생은 셋뿐이다 —
`mktcap_krw = close × shares_out` · `price_kind`(trade/reference) · `available_date = date`.
S06-2(09-05)가 KRX 기준가 축을 더했다 — `change_krw`(stage 그대로) · `base_price_krw = close −
change`(그날 KRX 기준가). 원주가 축은 그대로이고(EG20 불변), S06 `adj_factor` 의 `krx_base_price`
원천이 읽는다.

e1.15.0(플랜 v2 §4 B.2 · 결정 V2-2)이 **저녁 잠정 T 행**을 더했다 — KRX 에 없는 최신 거래일이
키움 `stg_flow_daily_kiwoom`(ka10060)에 있으면 그 종가·거래량으로 행을 만들고 컬럼 `basis`
('krx'·'evening')가 행마다 원천을 말한다. `corp_action_pending` 은 저녁 행의 기업행위 의심 표식이다.

입력 — stage 4(`stg_price_daily`·`stg_etf_price_daily`·`stg_listing_daily`·
`stg_flow_daily_kiwoom`) + equity `trading_calendar`(캘린더 밖 날짜 격리 축). 상수는 셋
(`evening_jump_abs_max`·`recent_session_window`·`recent_session_row_ratio_min`,
`baseline_seed_s04.json`).

테이블 특화 술어(`extra_gates`):
  EG3_price_daily — 두 원천 (ticker,date) 교집합 0(GATES §3 ⑧ 두 번째 식) · `price_kind` 어휘
                    폐쇄 · ticker 폭. 나머지는 **기록형 metric**(GATES §0-1): GAP-14
                    `open IS NULL ∧ volume>0` · 격리 사유별 건수 · `shares_out`/`par_value_krw`
                    NULL · stage MKTCAP 과의 차이 · `price_kind` NULL · **기준가 축(S06-2)**:
                    직전 행이 있는 거래 행에서 `base_price_krw = 직전 행 close` 비율(기대 ≈ 99.9%)·
                    불일치 건수·NULL 건수
  EG20            — 원주가 불변: 산출 OHLC·`volume_shr`·`value_krw` 를 stage 와 독립 재조인해
                    다른 행 0. **basis='krx' 행만** — 저녁 행은 KRX 원장에 없다
  EG14            — 최신 구간 수집 완결성: 최신 KRX 세션 `recent_session_window` 개의 행수가
                    유니버스 대비 하한 이상이고 종가 NULL 0 (e1.15.0)
  EG8-P01(KIS 수정종가 대조)은 독립 KIS 가격 stage 테이블이 없고 계수(S06)가 있어야 대조가 되므로
  S06 이후로 미룬다 — 여기서는 붙이지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext, require_const
from .model import (
    PRICE_BASIS_EVENING,
    PRICE_BASIS_KRX,
    PRICE_BASIS_VOCAB,
    EquityTable,
    FieldProfile,
    register,
)
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# DESIGN §4-2 — `price_kind` 폐쇄 어휘. 거래 있는 날 trade, `volume_shr = 0` 인 날 reference(기준가·
# 정지일, 종가 보존). `volume_shr` NULL 이면 price_kind 도 NULL — 0 으로 읽어 'reference' 로 굳히지
# 않는다.
PRICE_KINDS: tuple[str, ...] = ("trade", "reference")
# EG7-P01 격리 사유 + 캘린더 밖 날짜. `_reject/<reason>/` 디렉토리 이름이자 EG3 어휘 폐쇄 대상.
REJECT_REASONS: tuple[str, ...] = ("nonpositive_price", "off_calendar")

# EG20 이 대조하는 (산출 컬럼, stage 컬럼) 쌍 — 원칙 ② 가 지키는 원값 축 전부.
_RAW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("close", "close_krw"), ("volume_shr", "volume_shr"), ("open", "open_krw"),
    ("high", "high_krw"), ("low", "low_krw"), ("value_krw", "value_krw"))


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _n(ctx: EquityGateContext, sql: str) -> int:
    return int(str(_row(ctx, sql)[0]))


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과. 위반 항목명·건수를 detail 에 싣는다(rules_s01 규약)."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def eg3_price_daily(ctx: EquityGateContext) -> GateResult:
    """EG3 특화 — 원천 교집합 0 · `price_kind` 어휘 폐쇄 · ticker 폭 + 기록형 metric.

    폐기 조건은 산출식을 다시 계산하지 않는 축만 건다(§1 "게이트 술어를 산출식으로 재계산" 금지).
    `mktcap_krw` 가 stage MKTCAP 과 다른 행·listing 이 없어 `shares_out` 이 빈 주식 행은 KRX 두
    원장이 어긋난 사실이지 이 테이블의 결함이 아니므로 **기록**한다 — 서버 실측 뒤 baseline 승격.
    """
    v = _q(ctx.out_view)
    vocab = ", ".join(f"'{k}'" for k in PRICE_KINDS)
    n_overlap, n_kind_vocab, n_ticker_bad = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM (SELECT ticker, date FROM stg_price_daily
                                 INTERSECT SELECT ticker, date FROM stg_etf_price_daily)),
          (SELECT count(*) FROM {v}
            WHERE price_kind IS NOT NULL AND price_kind NOT IN ({vocab})),
          (SELECT count(*) FROM {v}
            WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR'
               OR length(ticker) <> {TICKER_LEN})""")
    (n_open_null_vol, n_kind_null, n_close_null, n_ref_with_value,
     n_shares_null_stock, n_shares_null_etf, n_par_null_stock,
     n_mktcap_mismatch, n_shares_mismatch, n_change_null, n_base_null,
     n_base_ne_close_minus_change) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE "open" IS NULL AND volume_shr > 0),
          (SELECT count(*) FROM {v} WHERE price_kind IS NULL),
          (SELECT count(*) FROM {v} WHERE "close" IS NULL),
          (SELECT count(*) FROM {v} WHERE price_kind = 'reference' AND value_krw > 0),
          (SELECT count(*) FROM {v} p JOIN stg_price_daily s USING (ticker, date)
            WHERE p.shares_out IS NULL),
          (SELECT count(*) FROM {v} p JOIN stg_etf_price_daily e USING (ticker, date)
            WHERE p.shares_out IS NULL),
          (SELECT count(*) FROM {v} p JOIN stg_price_daily s USING (ticker, date)
            WHERE p.par_value_krw IS NULL),
          (SELECT count(*) FROM {v} p
             LEFT JOIN stg_price_daily s ON s.ticker = p.ticker AND s.date = p.date
             LEFT JOIN stg_etf_price_daily e ON e.ticker = p.ticker AND e.date = p.date
            WHERE p.mktcap_krw IS DISTINCT FROM coalesce(s.mktcap_krw, e.mktcap_krw)),
          (SELECT count(*) FROM {v} p JOIN stg_price_daily s USING (ticker, date)
            WHERE p.shares_out IS DISTINCT FROM s.list_shrs),
          (SELECT count(*) FROM {v} WHERE change_krw IS NULL),
          (SELECT count(*) FROM {v} WHERE base_price_krw IS NULL),
          (SELECT count(*) FROM {v}
            WHERE base_price_krw IS DISTINCT FROM "close" - change_krw)""")
    # 기준가 축(S06-2) — 같은 티커의 직전 **행**(참고가 행 포함) close 대비. 기준가 ≠ 직전 종가는
    # 분할·무상증자·감자·정지 재개(가격 재발견)·ETF 분배락에서 나는 사실이지 이 테이블의 결함이
    # 아니라 기록형.
    (n_with_prev, n_trade_with_prev, n_base_eq_prev_trade, n_base_ne_prev_trade,
     n_base_ne_prev_reference) = _row(ctx, f"""
        WITH x AS (SELECT price_kind, base_price_krw,
                          lag("close") OVER (PARTITION BY ticker ORDER BY date) AS prev_close
                   FROM {v})
        SELECT count(*) FILTER (WHERE prev_close IS NOT NULL),
               count(*) FILTER (WHERE prev_close IS NOT NULL AND price_kind = 'trade'),
               count(*) FILTER (WHERE prev_close IS NOT NULL AND price_kind = 'trade'
                                  AND base_price_krw = prev_close),
               count(*) FILTER (WHERE prev_close IS NOT NULL AND price_kind = 'trade'
                                  AND base_price_krw IS DISTINCT FROM prev_close),
               count(*) FILTER (WHERE prev_close IS NOT NULL AND price_kind = 'reference'
                                  AND base_price_krw IS DISTINCT FROM prev_close)
        FROM x""")
    kinds = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT price_kind, count(*) FROM {v} GROUP BY 1 ORDER BY 1").fetchall()}
    # 저녁 잠정 행 축(e1.15.0 · 결정 V2-2) — 어휘 폐쇄 + "최신 1세션에만" 은 폐기형이다.
    # evening 행이 여러 날에 걸리면 KRX 가 여러 날 비었다는 뜻이고, 그 상태로 격자를 만들면
    # 잠정값이 확정판인 척 이력에 남는다.
    # 이 판이 저녁 잠정판인가 아침 확정판인가 — `build.make_build_meta` 가 올린 세션 테이블.
    build_basis = str(_row(ctx, "SELECT basis FROM _build")[0])
    basis_vocab = ", ".join(f"'{b}'" for b in PRICE_BASIS_VOCAB)
    n_evening_pending_null = _row(ctx, f"""
        SELECT count(*) FROM {v}
         WHERE basis = '{PRICE_BASIS_EVENING}' AND corp_action_pending IS NULL""")[0]
    (n_basis_vocab, n_basis_null, n_evening, n_evening_dates, n_evening_pending,
     n_krx_pending, evening_date, n_evening_mismatch) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE basis NOT IN ({basis_vocab})),
          (SELECT count(*) FROM {v} WHERE basis IS NULL),
          (SELECT count(*) FROM {v} WHERE basis = '{PRICE_BASIS_EVENING}'),
          (SELECT count(DISTINCT date) FROM stg_flow_daily_kiwoom
            WHERE date > (SELECT max(date) FROM stg_price_daily)),
          (SELECT count(*) FROM {v}
            WHERE basis = '{PRICE_BASIS_EVENING}' AND corp_action_pending),
          (SELECT count(*) FROM {v} WHERE basis = '{PRICE_BASIS_KRX}' AND corp_action_pending),
          (SELECT CAST(max(date) AS VARCHAR) FROM {v} WHERE basis = '{PRICE_BASIS_EVENING}'),
          (SELECT count(*) FROM {v} p JOIN stg_flow_daily_kiwoom f USING (ticker, date)
            WHERE p.basis = '{PRICE_BASIS_EVENING}'
              AND (p."close" IS DISTINCT FROM f.close_krw
                   OR p.volume_shr IS DISTINCT FROM f.volume_shr))""")
    checks = {
        "n_src_overlap": int(str(n_overlap)),
        "n_price_kind_outside_vocab": int(str(n_kind_vocab)),
        "n_ticker_bad_width": int(str(n_ticker_bad)),
        "n_basis_outside_vocab": int(str(n_basis_vocab)) + int(str(n_basis_null)),
        # 원천(키움 원장)에서 센다 — 산출은 `kw` 술어가 이미 최신 하루로 접어 항상 ≤ 1 이다(검수 R2-02).
        # 키움이 KRX 보다 이틀 이상 앞서면 중간 세션이 통째로 빠지므로 폐기한다.
        "n_evening_dates_over_one": max(int(str(n_evening_dates)) - 1, 0),
        # `corp_action_pending` 은 2치여야 한다 — NULL 이면 소비자의 `IS NOT TRUE` 거름망을 통과한다(R2-07)
        "n_evening_corp_action_pending_null": int(str(n_evening_pending_null)),
        # 키움 원장 대조 — evening 행의 종가·거래량은 원천 그대로여야 한다(EG20 의 짝)
        "n_evening_value_mismatch": int(str(n_evening_mismatch)),
        # `corp_action_pending` 은 저녁 축 전용이다 — krx 행이 참이면 산출식이 샌 것이다
        "n_krx_rows_corp_action_pending": int(str(n_krx_pending)),
        # 아침 확정판(basis=morning)에 잠정 행이 남아 있으면 KRX 가 안 온 것이다. 그대로 통과시키면
        # 잠정 종가가 '확정' 딱지를 달고 이력에 굳는다(플랜 v2 §4 B.2 §3 게이트).
        "n_evening_rows_in_morning_build": (int(str(n_evening)) if build_basis == "morning" else 0),
    }
    metrics: dict[str, object] = {
        "n_open_null_volume_pos": int(str(n_open_null_vol)),        # GAP-14 (DESIGN §4-2)
        "n_nonpositive_price": int(ctx.reject_by_reason.get("nonpositive_price", 0)),
        "n_off_calendar": int(ctx.reject_by_reason.get("off_calendar", 0)),
        "n_price_kind_null": int(str(n_kind_null)),
        "n_close_null": int(str(n_close_null)),
        "n_reference_with_value": int(str(n_ref_with_value)),
        "n_shares_out_null_stock": int(str(n_shares_null_stock)),
        "n_shares_out_null_etf": int(str(n_shares_null_etf)),
        "n_par_value_null_stock": int(str(n_par_null_stock)),
        "n_mktcap_stage_mismatch": int(str(n_mktcap_mismatch)),
        "n_shares_out_stage_mismatch": int(str(n_shares_mismatch)),
        "n_change_null": int(str(n_change_null)),                    # S06-2 기준가 축
        "n_base_price_null": int(str(n_base_null)),
        "n_base_price_ne_close_minus_change": int(str(n_base_ne_close_minus_change)),
        "n_rows_with_prev_row": int(str(n_with_prev)),
        "n_trade_rows_with_prev_row": int(str(n_trade_with_prev)),
        "n_base_price_eq_prev_close_trade": int(str(n_base_eq_prev_trade)),
        "n_base_price_ne_prev_close_trade": int(str(n_base_ne_prev_trade)),
        "n_base_price_ne_prev_close_reference": int(str(n_base_ne_prev_reference)),
        "base_price_match_rate_trade": (int(str(n_base_eq_prev_trade)) / int(str(n_trade_with_prev))
                                        if int(str(n_trade_with_prev)) else None),
        "price_kind_counts": kinds,
        "price_kind_vocab": list(PRICE_KINDS),
        "n_evening_rows": int(str(n_evening)),                       # 저녁 잠정판 축(기록형)
        "evening_date": None if evening_date is None else str(evening_date),
        "n_evening_corp_action_pending": int(str(n_evening_pending)),
        "basis_vocab": list(PRICE_BASIS_VOCAB), "build_basis": build_basis,
    }
    return _result("EG3_price_daily", checks, metrics, "원천 교집합 0 · price_kind 어휘 폐쇄")


eg3_price_daily.gate_name = "EG3_price_daily"   # type: ignore[attr-defined]


def eg20_raw_price(ctx: EquityGateContext) -> GateResult:
    """EG20 (GATES §6) — 원주가 불변. 산출 행을 두 stage 원천에 다시 조인해 원값 축이 다른 행 0.

    GATES 의 식은 close·volume_shr 두 축인데 원칙 ② 는 OHLC 전부이므로 open·high·low·value_krw 까지
    같은 규약으로 본다(컬럼별 건수를 metrics 에). 어느 원천에도 없는 행(산출이 만들어 낸 행)은
    coalesce 가 NULL 이라 `IS DISTINCT FROM` 에 걸려 같이 잡힌다.
    """
    v = _q(ctx.out_view)
    sel = ", ".join(f"count(*) FILTER (WHERE p.{_q(o)} IS DISTINCT FROM coalesce(s.{c}, e.{c}))"
                    for o, c in _RAW_COLUMNS)
    row = _row(ctx, f"""
        SELECT count(*), {sel}
        FROM (SELECT * FROM {v} WHERE basis = '{PRICE_BASIS_KRX}') p
        LEFT JOIN stg_price_daily s ON s.ticker = p.ticker AND s.date = p.date
        LEFT JOIN stg_etf_price_daily e ON e.ticker = p.ticker AND e.date = p.date""")
    n_rows, *changed = (int(str(x)) for x in row)
    checks = {f"n_{o}_changed": n for (o, _), n in zip(_RAW_COLUMNS, changed, strict=True)}
    # 저녁 잠정 행은 KRX 원장에 아예 없다(원천이 키움) — 여기서 세면 전건이 '변조' 로 잡힌다.
    # 그 행들의 원값 불변은 EG14 의 `n_evening_value_mismatch` 가 키움 원장으로 본다.
    n_evening = _n(ctx, f"SELECT count(*) FROM {v} WHERE basis = '{PRICE_BASIS_EVENING}'")
    return _result("EG20", checks, {"n_rows_checked": n_rows, "n_evening_rows_excluded": n_evening,
                                    "raw_columns": [o for o, _ in _RAW_COLUMNS]},
                   "원주가·원거래량 불변")


eg20_raw_price.gate_name = "EG20"               # type: ignore[attr-defined]


def eg14_recent_sessions(ctx: EquityGateContext) -> GateResult:
    """EG14 (규칙 e1.15.0 · 플랜 v2 §4 B.2 §2) — **최신 구간 수집 완결성**.

    EG5c 의 as-of 표본(`asof_sample_dates`)은 과거 고정일로 굳혀 재현성 축으로 쓴다. 그러면 어제
    들어온 데이터가 반쯤 비어도 어떤 게이트도 보지 않는다 — 그 자리를 이 게이트가 맡는다.

    술어(폐기형) — 최신 **KRX 세션** `recent_session_window` 개마다:
      ① 행수 ≥ `recent_session_row_ratio_min` × 그날 KRX 마스터 유니버스
         (= `stg_listing_daily` 최신일 종목 수 + `stg_etf_price_daily` 최신일 종목 수)
      ② 종가 NULL 0 — 행은 있는데 값이 빈 세션은 수집 실패이지 휴장이 아니다
    저녁 잠정판(basis='evening')의 T 세션은 **판정 밖**이다. 키움 커버(≈2,655종목)가 KRX 유니버스
    (≈3,924)보다 구조적으로 작아 같은 잣대를 들이대면 매일 저녁 빌드가 폐기된다 — 그 세션의
    커버율은 기록형 `evening_coverage_ratio` 로 남기고, 값 대조는 EG3_price_daily 의
    `n_evening_value_mismatch` 가 키움 원장으로 본다.
    """
    v = _q(ctx.out_view)
    window = int(require_const(ctx, "recent_session_window"))
    ratio_min = require_const(ctx, "recent_session_row_ratio_min")
    universe_n = _n(ctx, """
        SELECT (SELECT count(DISTINCT ticker) FROM stg_listing_daily
                 WHERE date = (SELECT max(date) FROM stg_listing_daily))
             + (SELECT count(DISTINCT ticker) FROM stg_etf_price_daily
                 WHERE date = (SELECT max(date) FROM stg_etf_price_daily))""")
    rows = ctx.con.execute(f"""
        SELECT CAST(date AS VARCHAR), count(*), count(*) FILTER (WHERE "close" IS NULL)
        FROM {v} WHERE basis = '{PRICE_BASIS_KRX}'
        GROUP BY date ORDER BY date DESC LIMIT {window}""").fetchall()
    sessions = [{"date": str(r[0]), "n_rows": int(str(r[1])), "n_close_null": int(str(r[2])),
                 "row_ratio": (int(str(r[1])) / universe_n) if universe_n else None}
                for r in rows]
    thin = [d["date"] for d in sessions
            if universe_n and d["n_rows"] < ratio_min * universe_n]
    null_close = [d["date"] for d in sessions if d["n_close_null"]]
    (n_evening, n_evening_tickers) = _row(ctx, f"""
        SELECT count(*), count(DISTINCT ticker) FROM {v}
         WHERE basis = '{PRICE_BASIS_EVENING}'""")
    checks = {
        "n_thin_sessions": len(thin),
        "n_sessions_with_null_close": len(null_close),
        # 창을 채울 세션이 없으면 원천이 통째로 비었다는 뜻이다 — 통과로 세지 않는다
        "n_missing_sessions": max(window - len(sessions), 0),
    }
    metrics: dict[str, object] = {
        "recent_session_window": window, "recent_session_row_ratio_min": ratio_min,
        "universe_n": universe_n, "sessions": sessions,
        "thin_sessions": thin, "sessions_with_null_close": null_close,
        "n_evening_rows": int(str(n_evening)),
        "evening_coverage_ratio": (int(str(n_evening_tickers)) / universe_n) if universe_n else None,
    }
    return _result("EG14", checks, metrics, "최신 세션 행수·종가 완결")


eg14_recent_sessions.gate_name = "EG14"         # type: ignore[attr-defined]


# ── S19 필드 선언 (DESIGN §4-7 · FIELD_MAP §2) ────────────────────────────────
# 랙 정본은 **세션**이다. OHLCV·거래대금은 값 원천이 stage `lag_known=true`(`stg_price_daily`·
# `stg_etf_price_daily`)이고 "가격류는 당일 실시간 관측 실증"(STAGE_DESIGN §6 표)이라 **0 세션** —
# S06 `views.FACTOR_LAG_SESSIONS` 0 과 S17 `CONSENSUS_LAG_SESSIONS` 이 근거로 든 그 값이다.
# `shares_out`·`mktcap_krw` 는 값이 `stg_listing_daily.list_shrs`(KRX 일별 마스터, stage
# `lag_known=false`)에서 오므로 **1 세션**으로 확정한다 — STAGE_HANDOFF §2 「lag_known=false 는
# lag 0 을 적용하면 안 된다」 + FIELD_MAP §2 「랙 1세션(익일 지식)」. S21 축소 어댑터의 본문 상수
# `PRICE_LAG_SESSIONS=0` 은 본판에서 이 값으로 교체된다(DESIGN §11 ②).
_AXIS: tuple[str, str] = ("ticker", "date")

FIELDS: tuple[FieldProfile, ...] = (
    FieldProfile(
        field_id="price.close", columns=("close",), label="종가(원주가)", unit="KRW",
        value_type="price", frequency="session", recommended_lag_sessions=0,
        recommended_lag_days=0, point_in_time=True, requires_confirmation=False,
        disclosure_basis="정규장 종가 확정(세션 마감)",
        evidence="price_daily.close ← stg_price_daily ∪ stg_etf_price_daily (EG20 원주가 불변). "
                 "분할·증자 조정 없음(원칙 ②) — 조정 축은 price.adj_close(전방 조정). "
                 "**evening 판은 키움 종가·OHLC NULL** — 저녁 잠정판(basis='evening')의 최신 "
                 "거래일 행은 KRX 가 아직 안 와서 키움 ka10060 종가로 채우고 시·고·저가와 "
                 "거래대금·주식수·시총은 NULL 이다. 그 행은 `basis`·`corp_action_pending` 두 "
                 "컬럼으로 표시되며 아침 확정판에서 KRX 행으로 자연 교체된다(결정 V2-2).",
        coverage_axis="grid_session", axis_columns=_AXIS),
    FieldProfile(
        field_id="price.open", columns=("open",), label="시가(원주가)", unit="KRW",
        value_type="price", frequency="session", recommended_lag_sessions=0,
        recommended_lag_days=0, point_in_time=True, requires_confirmation=False,
        disclosure_basis="정규장 시가 확정(세션 개장)",
        evidence="stage 가 KRX '0' 을 NULL 로 두는 정책이라 결측 행이 있다 — 그 크기는 이 행의 "
                 "estimated_coverage_pct 가 재므로 확인 대상 조건이 아니다(GAP-14 의 '결측 건수 "
                 "미측정' 은 S19 가 닫았다). 엔진 `Bar.open` 필수·>0 제약은 커널 어댑터 "
                 "`OhlcPolicy` 몫이고 팩터 필드 조건이 아니다.",
        coverage_axis="grid_session", axis_columns=_AXIS),
    FieldProfile(
        field_id="price.high", columns=("high",), label="고가(원주가)", unit="KRW",
        value_type="price", frequency="session", recommended_lag_sessions=0,
        recommended_lag_days=0, point_in_time=True, requires_confirmation=False,
        disclosure_basis="정규장 고가 확정(세션 마감)",
        evidence="equity 내부 스코프 — 레지스트리 42 필드에 없다(FIELD_MAP §2). FACTORS 정본 M02"
                 "(52주 신고가 근접도)의 재료이고 price.open 과 같은 NULL 유지 정책을 탄다.",
        coverage_axis="grid_session", scope="internal", axis_columns=_AXIS),
    FieldProfile(
        field_id="price.low", columns=("low",), label="저가(원주가)", unit="KRW",
        value_type="price", frequency="session", recommended_lag_sessions=0,
        recommended_lag_days=0, point_in_time=True, requires_confirmation=False,
        disclosure_basis="정규장 저가 확정(세션 마감)",
        evidence="equity 내부 스코프. price.high 와 같은 NULL 유지 정책.",
        coverage_axis="grid_session", scope="internal", axis_columns=_AXIS),
    FieldProfile(
        field_id="price.volume", columns=("volume_shr",), label="거래량", unit="주",
        value_type="count", frequency="session", recommended_lag_sessions=0,
        recommended_lag_days=0, point_in_time=True, requires_confirmation=False,
        disclosure_basis="정규장 마감 집계",
        evidence="원거래량. 조정 거래량 축은 뷰 v_adj_volume(base=as_of)·v_adj_volume_fwd"
                 "(전방)이며 같은 field_id 로 노출한다(FIELD_MAP §2).",
        coverage_axis="grid_session", axis_columns=_AXIS),
    FieldProfile(
        field_id="price.trading_value", columns=("value_krw",), label="거래대금", unit="KRW",
        value_type="amount", frequency="session", recommended_lag_sessions=0,
        recommended_lag_days=0, point_in_time=True, requires_confirmation=False,
        disclosure_basis="정규장 마감 집계",
        evidence="price_daily.value_krw ← KRX 원장 그대로.",
        coverage_axis="grid_session", axis_columns=_AXIS),
    FieldProfile(
        field_id="price.market_cap", columns=("mktcap_krw",), label="시가총액", unit="KRW",
        value_type="amount", frequency="session", recommended_lag_sessions=1,
        recommended_lag_days=1, point_in_time=True, requires_confirmation=False,
        disclosure_basis="정규장 종가 확정 + KRX 일별 마스터(상장주식수) 게시 — 게시 시각 미측정",
        evidence="mktcap_krw = close × shares_out. 종가는 lag_known=true 지만 주식수가 "
                 "stg_listing_daily(lag_known=false)라 보수적으로 1 세션. 우선주 합산이 아니다"
                 "(법인 시총은 뷰 v_firm_mktcap).",
        coverage_axis="grid_session", axis_columns=_AXIS),
    FieldProfile(
        field_id="price.shares_outstanding", columns=("shares_out",), label="상장주식수",
        unit="주", value_type="count", frequency="session", recommended_lag_sessions=1,
        recommended_lag_days=1, point_in_time=True, requires_confirmation=False,
        disclosure_basis="KRX 일별 마스터 게시 — 게시 시각 미측정",
        evidence="정본은 KRX 상장주식수(stg_listing_daily.list_shrs). DART 발행주식총수"
                 "(financial.shares_issued, S16)는 뜻이 다른 별개 필드다.",
        coverage_axis="grid_session", axis_columns=_AXIS),
)


# ── 선언 ─────────────────────────────────────────────────────────────────────

PRICE_DAILY = register(EquityTable(
    name="price_daily",
    grain=("ticker", "date"),
    # 순서 = DESIGN §4-2 컬럼 순서(S06-2: `change_krw`·`base_price_krw` 는 `shares_out` 다음).
    # OHLC 는 FIELD_MAP `price.close`·`price.open` 대응이라 접미사 없음(원주가 KRW 는 자명), 나머지
    # 수량·금액 컬럼은 stage 단위 접미사 규약(_shr·_krw) 유지.
    columns={"ticker": "VARCHAR", "date": "DATE",
             "open": "DECIMAL(9,0)", "high": "DECIMAL(9,0)", "low": "DECIMAL(9,0)",
             "close": "DECIMAL(9,0)", "volume_shr": "DECIMAL(13,0)",
             "value_krw": "DECIMAL(16,0)", "mktcap_krw": "DECIMAL(18,0)",
             "shares_out": "DECIMAL(13,0)",
             # S06-2: KRX 전일 대비(stage 그대로) · 기준가 = close − change(DECIMAL 차, (10,0))
             "change_krw": "DECIMAL(9,0)", "base_price_krw": "DECIMAL(10,0)",
             "par_value_krw": "DECIMAL(9,2)",
             "price_kind": "VARCHAR",
             # e1.15.0 — 행 원천 축(krx·evening)과 저녁 기업행위 의심 표식 (결정 V2-2)
             "basis": "VARCHAR", "corp_action_pending": "BOOLEAN",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_price_daily", "stg_etf_price_daily", "stg_listing_daily",
            "stg_flow_daily_kiwoom", "trading_calendar"),
    partition_class="date_axis",
    partition_key_expr="year(date)",
    available_rule="column:date — 가격류(stage lag_known=true), 공표 시각 미제공 → basis default",
    # GATES §3 ⑧: 좌변 행수 = 두 원천 행수 합 − reject. 교집합 0 은 EG3-P01·EG3_price_daily 가 본다.
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    # 세 번째 항 = 저녁 잠정 T 행. `sql/price_daily.sql` 의 `kw` CTE 와 **글자 그대로 같은 술어**
    # 여야 한다 — 한쪽만 고치면 등식이 조용히 깨진다(e1.15.0).
    eg1_rhs_sql=("SELECT (SELECT count(*) FROM stg_price_daily) "
                 "+ (SELECT count(*) FROM stg_etf_price_daily) "
                 "+ (SELECT count(*) FROM stg_flow_daily_kiwoom f "
                 "    WHERE f.date > (SELECT max(date) FROM stg_price_daily) "
                 "      AND f.date = (SELECT max(date) FROM stg_flow_daily_kiwoom))"),
    sql_path=SQL_DIR / "price_daily.sql",
    input_columns={
        "stg_price_daily": ("ticker", "date", "open_krw", "high_krw", "low_krw", "close_krw",
                            "change_krw", "volume_shr", "value_krw", "mktcap_krw", "list_shrs"),
        "stg_etf_price_daily": ("ticker", "date", "open_krw", "high_krw", "low_krw", "close_krw",
                                "change_krw", "volume_shr", "value_krw", "mktcap_krw",
                                "list_shrs"),
        "stg_listing_daily": ("ticker", "date", "par_value_krw", "list_shrs"),
        # 저녁 잠정 T 행의 원천 — 키움 ka10060 은 종가·거래량 두 축만 쓴다(수급 컬럼은 S08 몫)
        "stg_flow_daily_kiwoom": ("ticker", "date", "close_krw", "volume_shr"),
        "trading_calendar": ("date",)},
    available_basis=("default",),
    content_date_column="date",
    reject_reasons=REJECT_REASONS,
    # `_const` 는 **SQL 이 읽는 상수만** 싣는다 — 여기 올린 키는 미등재면 빌드가 예외로 죽는다.
    # EG14 의 두 상수는 게이트가 `require_const` 로 읽어 미등재를 skip(no_baseline) 으로 흘린다.
    consts=("evening_jump_abs_max",),
    extra_gates=(eg3_price_daily, eg20_raw_price, eg14_recent_sessions),
    field_profiles=FIELDS,
))

TABLES: tuple[EquityTable, ...] = (PRICE_DAILY,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s04.json"
"""이 슬라이스가 요구하는 baseline 상수 — 없다. 파일은 그 사실과 이유를 기록한다."""

__all__ = ["FIELDS", "PRICE_DAILY", "PRICE_KINDS", "REJECT_REASONS", "TABLES"]
