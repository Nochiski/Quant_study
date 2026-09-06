"""S18 슬라이스 선언 — `opinion_daily` · `opinion_broker_daily` (EQUITY_DESIGN v1.2 §4-6).

애널리스트 **투자의견·목표주가** 두 테이블이다. 5단계(S17 컨센서스와 나란히)이고 선행은 S01 뿐이다
(WORKFLOW v1.2 §3-1·§3-2).

  `opinion_daily`        grain (`ticker`, `obs_date`, `src`) — WISE 요약(`stg_analyst_summary`)과
                         v3 미러(`stg_v3_analyst_opinions`)의 상보 결합. 원천마다 PIT 축이 달라서
                         `src` 가 PK 에 들어간다(DESIGN §3 골격 `src`).
  `opinion_broker_daily` grain (`ticker`, `fetched_date`, `broker`, `opinion_date`) —
                         제공처별 원문 의견·목표주가 1:1 + 파생 `prev_opinion_date`(FX-5-008).

PIT 축(DESIGN §4-6 "수집 관측일 = available_date, 덮어쓰기 원천은 최초 관측만"):
  wise 두 테이블 — `fetched_date` 가 곧 우리가 화면을 긁은 날이라 `available_date = fetched_date`,
                   basis `measured`(stage `lag_known=true`).
  v3            — 이 테이블에는 `stg_v3_revision_daily.collected_date` 에 해당하는 수집 시각 컬럼이
                   **없다**. DESIGN §4-6 의 v3 규약(collected_date NULL → `date` 대용 + degraded)을
                   전 행에 적용해 basis `default` + `coverage_degraded` 로 싣는다. 그리고 덮어쓰기
                   원장의 사본이므로 같은 (ticker, date) 그룹에서 **`observed_date` 최소 판본**만
                   채택한다(EG6).

등급 어휘는 stage 정본이다 — `rules_wise._OPINION_CLASS` 가 원문 17종을 {buy, hold, sell, other}
로 접어 `opinion_class`·`prev_opinion_class` 로 준다. equity 는 다시 계산하지 않고 폐쇄만 판정한다.

숫자 상수는 없다(`baseline_seed_s18.json` 참조). EG8 의 `opinion_daily.src_overlap_agree_min` 만
사람 승인 대기이고 미등재면 `skip(no_baseline)` + 측정치 기록이다(GATES §2 매트릭스 25행).

테이블 특화 술어(`extra_gates`):
  EG3_opinion_daily        — `src` 어휘 폐쇄 · basis↔src 대응 · `coverage_degraded` ⇔ v3 ·
                             `available_date = obs_date` · ticker 폭(EG3-P07) + 기록형 metric
  EG6                      — 최초 관측 선택을 stage 에서 독립 재계산(P01) · `is_latest`/`_current`
                             류 컬럼 부재(P03)
  EG8                      — v3 ⋈ wise 겹친 키의 값 일치율 ≥ baseline (첫 빌드 skip(no_baseline))
  EG9                      — 커버리지 기록형(P06). 시총 분위 축은 `universe_daily` 가 S18 입력이
                             아니라 못 낸다 — `security`·`security_span` 축으로 대신 기록하고 분위는
                             S19 `dataset_profile.coverage_by_mktcap_quintile` 몫이다(GATES §9)
  EG3_opinion_broker_daily — 등급 어휘 폐쇄 · `prev_opinion_date` 불변식(< opinion_date) ·
                             동반 available 독립 재계산(EG2-P05) · ticker 폭 + 기록형 metric
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext, SkipGate, require_const
from .model import EquityTable, register
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# DESIGN §4-6 — `opinion_daily.src` 폐쇄 어휘. 상보 결합이라 PK 에 들어간다(DESIGN §3).
SRC_VOCAB: tuple[str, ...] = ("wise", "v3")
# stage `rules_wise._OPINION_CLASS` 가 내는 폐쇄 어휘. equity 는 계승만 하고 재계산하지 않는다.
OPINION_CLASS_VOCAB: tuple[str, ...] = ("buy", "hold", "sell", "other")
# EG7 격리 사유 어휘(EG3 폐쇄 대상이자 `_reject/<reason>/` 디렉토리 이름).
OPINION_REJECT_REASONS: tuple[str, ...] = ("nonpositive_target_price", "base_date_after_obs")
BROKER_REJECT_REASONS: tuple[str, ...] = ("nonpositive_target_price", "opinion_date_after_fetch")

# EG8 이 겹친 키에서 대조하는 (opinion_daily 컬럼, stage v3 컬럼) 쌍이 아니라 산출 컬럼 이름 —
# 두 원천을 이미 같은 이름으로 접었으므로 산출 위에서 자기끼리 대조한다.
_OVERLAP_COLUMNS: tuple[str, ...] = ("opinion_score", "target_price_krw", "eps_krw", "per",
                                     "analyst_count")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _ints(ctx: EquityGateContext, sql: str) -> list[int]:
    return [int(str(x)) for x in _row(ctx, sql)]


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과. 위반 항목명·건수를 detail 에 싣는다(rules_s01 규약)."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _bad_ticker_len(ctx: EquityGateContext) -> int:
    """EG3-P07 — `ticker` 가 VARCHAR(6). 정수 캐스팅되면 '0001A0' 가 조용히 사라진다."""
    return _ints(ctx, f"SELECT count(*) FROM {_q(ctx.out_view)} WHERE ticker IS NULL "
                      f"OR typeof(ticker) <> 'VARCHAR' OR length(ticker) <> {TICKER_LEN}")[0]


def _latest_like_columns(ctx: EquityGateContext) -> list[str]:
    """EG6-P03 — `is_latest*`·`*_current` 류 컬럼 부재. 현재값 라벨을 팩트 행에 굽지 않는다."""
    return [c for c in ctx.rule.columns if c.startswith("is_latest") or c.endswith("_current")]


# ── opinion_daily ────────────────────────────────────────────────────────────

def eg3_opinion_daily(ctx: EquityGateContext) -> GateResult:
    """EG3 특화 — `src`·basis 대응 어휘 폐쇄와 PIT 축 항등 + 기록형 metric.

    폐기 조건은 선언한 축만 건다. 커버 종목수·NULL 분포·겹친 키 수 같은 사실은 **기록형**
    (GATES §0-1)이라 통과 조건이 아니다 — 서버 실측 뒤 baseline 승격 여부를 사람이 정한다.
    """
    v = _q(ctx.out_view)
    (n_src_vocab, n_basis_mismatch, n_degraded_mismatch, n_available_ne_obs,
     n_src_rows_bad, n_obs_null) = _ints(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v}
            WHERE src IS NULL OR src NOT IN ({_vocab_sql(SRC_VOCAB)})),
          (SELECT count(*) FROM {v}
            WHERE available_basis IS DISTINCT FROM
                  CASE WHEN src = 'wise' THEN 'measured' ELSE 'default' END),
          (SELECT count(*) FROM {v} WHERE coverage_degraded IS DISTINCT FROM (src = 'v3')),
          (SELECT count(*) FROM {v} WHERE available_date IS DISTINCT FROM obs_date),
          (SELECT count(*) FROM {v} WHERE n_src_rows IS NULL OR n_src_rows < 1),
          (SELECT count(*) FROM {v} WHERE obs_date IS NULL)""")
    (n_wise, n_v3, n_score_null, n_target_null, n_count_null, n_note,
     n_overlap_keys, n_dedup) = _ints(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE src = 'wise'),
          (SELECT count(*) FROM {v} WHERE src = 'v3'),
          (SELECT count(*) FROM {v} WHERE opinion_score IS NULL),
          (SELECT count(*) FROM {v} WHERE target_price_krw IS NULL),
          (SELECT count(*) FROM {v} WHERE analyst_count IS NULL),
          (SELECT count(*) FROM {v} WHERE no_opinion_note IS NOT NULL),
          (SELECT count(*) FROM (SELECT ticker, obs_date FROM {v} WHERE src = 'wise'
                                 INTERSECT SELECT ticker, obs_date FROM {v} WHERE src = 'v3')),
          (SELECT coalesce(sum(n_src_rows - 1), 0) FROM {v})""")
    span = _row(ctx, f"SELECT min(obs_date), max(obs_date), min(opinion_score), "
                     f"max(opinion_score) FROM {v}")
    checks = {
        "n_src_outside_vocab": n_src_vocab,
        "n_basis_src_mismatch": n_basis_mismatch,
        "n_coverage_degraded_mismatch": n_degraded_mismatch,
        "n_available_date_ne_obs_date": n_available_ne_obs,
        "n_src_rows_not_positive": n_src_rows_bad,
        "n_obs_date_null": n_obs_null,
        "n_ticker_bad_width": _bad_ticker_len(ctx),
    }
    metrics: dict[str, object] = {
        "src_vocab": list(SRC_VOCAB),
        "n_rows_wise": n_wise, "n_rows_v3": n_v3,
        "n_opinion_score_null": n_score_null, "n_target_price_null": n_target_null,
        "n_analyst_count_null": n_count_null, "n_no_opinion_note": n_note,
        "n_overlap_keys_wise_v3": n_overlap_keys,      # EG8 의 모집단
        "n_dedup_v3": n_dedup,                          # 프레임 _meta.n_dedup 은 0 고정(GATES §9 S05)
        "n_nonpositive_target_price": int(ctx.reject_by_reason.get(
            "nonpositive_target_price", 0)),
        "n_base_date_after_obs": int(ctx.reject_by_reason.get("base_date_after_obs", 0)),
        "obs_date_min": str(span[0]), "obs_date_max": str(span[1]),
        "opinion_score_min": None if span[2] is None else float(str(span[2])),
        "opinion_score_max": None if span[3] is None else float(str(span[3])),
    }
    return _result("EG3_opinion_daily", checks, metrics, "src·basis 어휘 폐쇄 · PIT 축 항등")


eg3_opinion_daily.gate_name = "EG3_opinion_daily"      # type: ignore[attr-defined]


def eg6_first_observation(ctx: EquityGateContext) -> GateResult:
    """EG6-P01·P03 — 최초 관측 선택을 stage 에서 **독립 재계산**한다.

    산출이 고른 행이 그 키 그룹의 `observed_date` 최소 판본인지 stage 쪽에서 다시 세고(P01),
    산출 행의 값이 실제로 그 원천 행에 있는 값인지 역조인으로 확인한다. "min 을 골랐다" 를
    산출 컬럼으로 다시 확인하면 항진명제가 된다(GATES §5-C5).
    """
    v = _q(ctx.out_view)
    n_wise_not_first, n_v3_not_first, n_v3_not_in_source = _ints(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} o WHERE o.src = 'wise'
             AND o.observed_date IS DISTINCT FROM
                 (SELECT min(s.observed_date) FROM stg_analyst_summary s
                   WHERE s.ticker = o.ticker AND s.fetched_date = o.obs_date)),
          (SELECT count(*) FROM {v} o WHERE o.src = 'v3'
             AND o.observed_date IS DISTINCT FROM
                 (SELECT min(x.observed_date) FROM stg_v3_analyst_opinions x
                   WHERE x.ticker = o.ticker AND x.date = o.obs_date)),
          (SELECT count(*) FROM {v} o WHERE o.src = 'v3' AND NOT EXISTS (
             SELECT 1 FROM stg_v3_analyst_opinions x
              WHERE x.ticker = o.ticker AND x.date = o.obs_date
                AND x.observed_date IS NOT DISTINCT FROM o.observed_date
                AND x.opinion_score IS NOT DISTINCT FROM o.opinion_score
                AND x.target_price_krw IS NOT DISTINCT FROM o.target_price_krw
                AND x.estimated_eps IS NOT DISTINCT FROM o.eps_krw
                AND x.estimated_per IS NOT DISTINCT FROM o.per
                AND x.analyst_count IS NOT DISTINCT FROM o.analyst_count))""")
    n_v3_multi, n_wise_multi = _ints(ctx, """
        SELECT
          (SELECT count(*) FROM (SELECT ticker, date FROM stg_v3_analyst_opinions
                                 GROUP BY ALL HAVING count(*) > 1)),
          (SELECT count(*) FROM (SELECT ticker, fetched_date FROM stg_analyst_summary
                                 GROUP BY ALL HAVING count(*) > 1))""")
    latest_like = _latest_like_columns(ctx)
    checks = {"n_wise_not_first_observation": n_wise_not_first,
              "n_v3_not_first_observation": n_v3_not_first,
              "n_v3_payload_not_in_source": n_v3_not_in_source,
              "n_latest_like_columns": len(latest_like)}
    metrics: dict[str, object] = {"latest_like_columns": latest_like,
                                  "n_v3_multi_version_keys": n_v3_multi,
                                  "n_wise_multi_version_keys": n_wise_multi}
    return _result("EG6", checks, metrics, "최초 관측 선택 독립 재계산 일치")


eg6_first_observation.gate_name = "EG6"                # type: ignore[attr-defined]


def eg8_src_overlap(ctx: EquityGateContext) -> GateResult:
    """EG8 — v3 ⋈ wise 겹친 키의 값 일치율 ≥ baseline (WORKFLOW §3-2 5단계 통과 조건).

    두 원천이 같은 (ticker, obs_date) 에 준 값이 서로 다르면 어느 쪽을 팩터가 읽느냐로 결과가
    갈린다. 겹침이 없으면 `skip(no_cross_source)`, 상수 미등재면 `skip(no_baseline)` 이고
    측정치는 어느 쪽이든 metrics 에 남는다.
    """
    v = _q(ctx.out_view)
    pairs = " AND ".join(f"w.{_q(c)} IS NOT DISTINCT FROM x.{_q(c)}" for c in _OVERLAP_COLUMNS)
    per_col = ", ".join(f"count(*) FILTER (WHERE w.{_q(c)} IS NOT DISTINCT FROM x.{_q(c)})"
                        for c in _OVERLAP_COLUMNS)
    row = _ints(ctx, f"""
        WITH w AS (SELECT * FROM {v} WHERE src = 'wise'),
             x AS (SELECT * FROM {v} WHERE src = 'v3')
        SELECT count(*), count(*) FILTER (WHERE {pairs}), {per_col}
        FROM w JOIN x ON x.ticker = w.ticker AND x.obs_date = w.obs_date""")
    n_overlap, n_agree, *per_col_agree = row
    metrics: dict[str, object] = {
        "n_overlap_keys": n_overlap, "n_agree_all_columns": n_agree,
        "overlap_columns": list(_OVERLAP_COLUMNS),
        "n_agree_by_column": dict(zip(_OVERLAP_COLUMNS, per_col_agree, strict=True)),
        "agree_rate": (n_agree / n_overlap) if n_overlap else None}
    if not n_overlap:
        raise SkipGate("no_cross_source", metrics)
    thr = require_const(ctx, "src_overlap_agree_min", metrics)
    rate = n_agree / n_overlap
    ok = rate >= thr
    metrics["threshold"] = thr
    return GateResult("EG8", GateStatus.PASS if ok else GateStatus.FAIL,
                      "v3⋈wise 겹침 일치율 이상" if ok else
                      f"src overlap agree rate {rate:.6f} < threshold {thr} "
                      f"(n_overlap={n_overlap} n_agree={n_agree})", metrics)


eg8_src_overlap.gate_name = "EG8"                      # type: ignore[attr-defined]


def _coverage_metrics(ctx: EquityGateContext, obs_column: str) -> dict[str, object]:
    """EG9-P06 공통 — 커버리지 기록형. 폐기 조건은 없다(값만 남긴다).

    시총 분위별 커버율은 `universe_daily`(S03B) 가 있어야 나오는데 S18 의 입력이 아니다
    (WORKFLOW §3-1 S18 선행 = S01). 여기서는 `security` 종목 축과 `security_span` 날짜 축으로
    커버 모집단을 기록하고, 분위 축은 S19 `dataset_profile.coverage_by_mktcap_quintile` 로 넘긴다.
    """
    v = _q(ctx.out_view)
    n_ticker_out, n_ticker_security, n_ticker_not_in_security = _ints(ctx, f"""
        SELECT
          (SELECT count(DISTINCT ticker) FROM {v}),
          (SELECT count(*) FROM security),
          (SELECT count(*) FROM (SELECT DISTINCT ticker FROM {v}
                                 EXCEPT SELECT ticker FROM security))""")
    n_no_span, n_after_end, n_before_start, n_within = _ints(ctx, f"""
        WITH sp AS (SELECT ticker, min(first_date) AS f, max(last_date) AS l
                    FROM security_span GROUP BY ALL)
        SELECT count(*) FILTER (WHERE sp.ticker IS NULL),
               count(*) FILTER (WHERE sp.ticker IS NOT NULL AND o.obs_axis > sp.l),
               count(*) FILTER (WHERE sp.ticker IS NOT NULL AND o.obs_axis < sp.f),
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM security_span s WHERE s.ticker = o.ticker
                     AND o.obs_axis BETWEEN s.first_date AND s.last_date))
        FROM (SELECT ticker, {_q(obs_column)} AS obs_axis FROM {v}) o
        LEFT JOIN sp ON sp.ticker = o.ticker""")
    by_sec_type = {str(r[0]): [int(str(r[1])), int(str(r[2]))] for r in ctx.con.execute(f"""
        SELECT s.sec_type, count(*),
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM {v} o WHERE o.ticker = s.ticker))
        FROM security s GROUP BY ALL ORDER BY 1""").fetchall()}
    return {
        "n_ticker_out": n_ticker_out, "n_ticker_security": n_ticker_security,
        "n_ticker_not_in_security": n_ticker_not_in_security,
        "cover_ratio_vs_security": (n_ticker_out / n_ticker_security
                                    if n_ticker_security else None),
        "covered_by_sec_type": by_sec_type,           # {sec_type: [n_security, n_covered]}
        "n_row_within_span": n_within, "n_row_after_span_end": n_after_end,
        "n_row_before_span_start": n_before_start, "n_row_ticker_without_span": n_no_span,
        "coverage_by_mktcap_quintile": None,
        "coverage_by_mktcap_quintile_note":
            "universe_daily 는 S18 입력이 아니다 — 분위 축은 S19 dataset_profile 몫(GATES §9)",
    }


def eg9_coverage_opinion(ctx: EquityGateContext) -> GateResult:
    """EG9-P06 — 커버리지 기록형 + WISE 커버 대장 대조(`stg_wise_coverage`)."""
    v = _q(ctx.out_view)
    status = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        "SELECT status_current, count(*) FROM stg_wise_coverage GROUP BY ALL ORDER BY 1"
    ).fetchall()}
    n_covered_without_row, n_row_without_coverage = _ints(ctx, f"""
        SELECT
          (SELECT count(*) FROM stg_wise_coverage c WHERE c.status_current = 'covered'
             AND NOT EXISTS (SELECT 1 FROM {v} o WHERE o.ticker = c.ticker AND o.src = 'wise')),
          (SELECT count(*) FROM (SELECT DISTINCT ticker FROM {v} WHERE src = 'wise') o
             WHERE NOT EXISTS (SELECT 1 FROM stg_wise_coverage c WHERE c.ticker = o.ticker
                                 AND c.status_current = 'covered'))""")
    metrics: dict[str, object] = {
        **_coverage_metrics(ctx, "obs_date"),
        "wise_coverage_status": status,
        "n_wise_covered_without_row": n_covered_without_row,
        "n_wise_row_without_coverage": n_row_without_coverage,
    }
    # `stg_wise_coverage` 는 upsert(현재값 라벨, available 없음)라 팩트 컬럼으로 내리지 않는다 —
    # 여기서 커버 모집단 기록에만 쓴다(EG6-P03 이 금지하는 `_current` 컬럼을 산출에 두지 않는 이유).
    return _result("EG9", {}, metrics, "커버리지 기록(기록형)")


eg9_coverage_opinion.gate_name = "EG9"                 # type: ignore[attr-defined]


# ── opinion_broker_daily ─────────────────────────────────────────────────────

def eg3_opinion_broker_daily(ctx: EquityGateContext) -> GateResult:
    """EG3 특화 — 등급 어휘 폐쇄 · `prev_opinion_date` 불변식 · 동반 available 독립 재계산."""
    v = _q(ctx.out_view)
    vocab = _vocab_sql(OPINION_CLASS_VOCAB)
    (n_class_vocab, n_prev_class_vocab, n_prev_not_before, n_available_ne_fetched,
     n_basis_bad) = _ints(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v}
            WHERE opinion_class IS NOT NULL AND opinion_class NOT IN ({vocab})),
          (SELECT count(*) FROM {v}
            WHERE prev_opinion_class IS NOT NULL AND prev_opinion_class NOT IN ({vocab})),
          (SELECT count(*) FROM {v}
            WHERE prev_opinion_date IS NOT NULL AND prev_opinion_date >= opinion_date),
          (SELECT count(*) FROM {v} WHERE available_date IS DISTINCT FROM fetched_date),
          (SELECT count(*) FROM {v} WHERE available_basis IS DISTINCT FROM 'measured')""")
    # EG2-P05 — 파생 컬럼 동반 available 은 "구성 행 available 의 max". 산출 컬럼을 쓰지 않고
    # stage 에서 다시 집계해 대조한다(항진명제 금지).
    n_prev_av_mismatch, n_prev_av_orphan = _ints(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} o
             WHERE o.prev_opinion_date IS NOT NULL
               AND o.prev_opinion_date_available_date IS DISTINCT FROM (
                   SELECT greatest(o.fetched_date, max(p.fetched_date))
                   FROM stg_analyst_broker p
                   WHERE p.ticker = o.ticker AND p.broker = o.broker
                     AND p.opinion_date = o.prev_opinion_date
                     AND p.fetched_date <= o.fetched_date)),
          (SELECT count(*) FROM {v}
             WHERE (prev_opinion_date IS NULL)
                   <> (prev_opinion_date_available_date IS NULL))""")
    (n_prev_date_null, n_change_null, n_prev_target_null, n_class_null,
     n_prev_class_null) = _ints(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE prev_opinion_date IS NULL),
          (SELECT count(*) FROM {v} WHERE change_pct IS NULL),
          (SELECT count(*) FROM {v} WHERE prev_target_price_krw IS NULL),
          (SELECT count(*) FROM {v} WHERE opinion_class IS NULL),
          (SELECT count(*) FROM {v} WHERE prev_opinion_class IS NULL)""")
    classes = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT opinion_class, count(*) FROM {v} GROUP BY ALL ORDER BY 1").fetchall()}
    raw = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT opinion, count(*) FROM {v} GROUP BY ALL ORDER BY 1").fetchall()}
    checks = {
        "n_opinion_class_outside_vocab": n_class_vocab,
        "n_prev_opinion_class_outside_vocab": n_prev_class_vocab,
        "n_prev_opinion_date_not_before": n_prev_not_before,
        "n_available_date_ne_fetched_date": n_available_ne_fetched,
        "n_available_basis_not_measured": n_basis_bad,
        "n_prev_available_mismatch": n_prev_av_mismatch,
        "n_prev_available_null_mismatch": n_prev_av_orphan,
        "n_ticker_bad_width": _bad_ticker_len(ctx),
    }
    metrics: dict[str, object] = {
        "opinion_class_vocab": list(OPINION_CLASS_VOCAB),
        "opinion_class_counts": classes, "opinion_raw_counts": raw,
        "n_prev_opinion_date_null": n_prev_date_null,   # FX-5-008 — 간격 미상이면 change_pct 해석 금지
        "n_change_pct_null": n_change_null, "n_prev_target_price_null": n_prev_target_null,
        "n_opinion_class_null": n_class_null, "n_prev_opinion_class_null": n_prev_class_null,
        "n_nonpositive_target_price": int(ctx.reject_by_reason.get(
            "nonpositive_target_price", 0)),
        "n_opinion_date_after_fetch": int(ctx.reject_by_reason.get(
            "opinion_date_after_fetch", 0)),
        "latest_like_columns": _latest_like_columns(ctx),
    }
    return _result("EG3_opinion_broker_daily", checks, metrics,
                   "등급 어휘 폐쇄 · prev_opinion_date 불변식")


eg3_opinion_broker_daily.gate_name = "EG3_opinion_broker_daily"   # type: ignore[attr-defined]


def eg9_coverage_broker(ctx: EquityGateContext) -> GateResult:
    """EG9-P06 — 브로커 축 커버리지 기록형. 제공처 수·종목당 제공처 분포를 남긴다."""
    v = _q(ctx.out_view)
    n_broker, n_ticker, n_fetch_date = _ints(ctx, f"""
        SELECT (SELECT count(DISTINCT broker) FROM {v}),
               (SELECT count(DISTINCT ticker) FROM {v}),
               (SELECT count(DISTINCT fetched_date) FROM {v})""")
    per_ticker = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(f"""
        SELECT n, count(*) FROM (SELECT ticker, fetched_date, count(DISTINCT broker) AS n
                                 FROM {v} GROUP BY ALL) GROUP BY ALL ORDER BY 1""").fetchall()}
    metrics: dict[str, object] = {
        **_coverage_metrics(ctx, "fetched_date"),
        "n_broker": n_broker, "n_ticker": n_ticker, "n_fetched_date": n_fetch_date,
        "broker_count_per_ticker_date": per_ticker,
    }
    return _result("EG9", {}, metrics, "브로커 커버리지 기록(기록형)")


eg9_coverage_broker.gate_name = "EG9"                  # type: ignore[attr-defined]


# ── 선언 ─────────────────────────────────────────────────────────────────────

OPINION_DAILY = register(EquityTable(
    name="opinion_daily",
    grain=("ticker", "obs_date", "src"),
    # 순서 = DESIGN §4-6 컬럼 순서. 값 5축 → wise 전용 2축 → 판본·관측 축 → PIT 2축.
    columns={"ticker": "VARCHAR", "obs_date": "DATE", "src": "VARCHAR",
             "opinion_score": "DECIMAL(6,2)", "target_price_krw": "DECIMAL(14,0)",
             "eps_krw": "DECIMAL(14,0)", "per": "DECIMAL(10,2)",
             "analyst_count": "DECIMAL(5,0)",
             "base_date": "DATE", "no_opinion_note": "VARCHAR",
             "n_src_rows": "BIGINT", "observed_date": "DATE",
             "coverage_degraded": "BOOLEAN",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_analyst_summary", "stg_v3_analyst_opinions", "stg_wise_coverage",
            "security", "security_span"),
    partition_class="date_axis",
    partition_key_expr="year(obs_date)",
    available_rule=("column:obs_date — wise 는 fetched_date(수집일) measured, v3 는 수집 시각 "
                    "컬럼이 없어 date 대용 default + coverage_degraded (DESIGN §4-6)"),
    # GATES §3 ⑳: 좌변 행수 = wise 전건 + v3 distinct(ticker, date) − reject.
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=("SELECT (SELECT count(*) FROM stg_analyst_summary) "
                 "+ (SELECT count(*) FROM (SELECT DISTINCT ticker, date "
                 "FROM stg_v3_analyst_opinions))"),
    sql_path=SQL_DIR / "opinion_daily.sql",
    input_columns={
        "stg_analyst_summary": ("ticker", "fetched_date", "base_date", "opinion_score",
                                "target_price_krw", "eps_krw", "per", "analyst_count",
                                "no_opinion_note", "observed_date"),
        "stg_v3_analyst_opinions": ("ticker", "date", "opinion_score", "target_price_krw",
                                    "estimated_eps", "estimated_per", "analyst_count",
                                    "observed_date"),
        "stg_wise_coverage": ("ticker", "status_current", "checked_date_current"),
        "security": ("ticker", "sec_type"),
        "security_span": ("ticker", "first_date", "last_date")},
    available_basis=("measured", "default"),
    # 내용일 = 기준일(WISE '[기준: YYYY.MM.DD]'). v3 는 NULL 이라 술어에 걸리지 않는다.
    content_date_column="base_date",
    reject_reasons=OPINION_REJECT_REASONS,
    extra_gates=(eg3_opinion_daily, eg6_first_observation, eg8_src_overlap,
                 eg9_coverage_opinion),
))

OPINION_BROKER_DAILY = register(EquityTable(
    name="opinion_broker_daily",
    grain=("ticker", "fetched_date", "broker", "opinion_date"),
    columns={"ticker": "VARCHAR", "fetched_date": "DATE", "broker": "VARCHAR",
             "opinion_date": "DATE",
             "target_price_krw": "DECIMAL(14,0)", "prev_target_price_krw": "DECIMAL(14,0)",
             "change_pct": "DECIMAL(10,2)",
             "opinion": "VARCHAR", "opinion_class": "VARCHAR",
             "prev_opinion": "VARCHAR", "prev_opinion_class": "VARCHAR",
             "prev_opinion_date": "DATE", "prev_opinion_date_available_date": "DATE",
             "observed_date": "DATE",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_analyst_broker", "security", "security_span"),
    partition_class="date_axis",
    partition_key_expr="year(fetched_date)",
    available_rule="column:fetched_date — WISE 화면 수집일(stage lag_known=true), basis measured",
    # GATES §3 ㉑: 좌변 행수 = stage 전건 − reject (1:1, 판본 선택 없음).
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql="SELECT count(*) FROM stg_analyst_broker",
    sql_path=SQL_DIR / "opinion_broker_daily.sql",
    input_columns={
        "stg_analyst_broker": ("ticker", "fetched_date", "broker", "opinion_date",
                               "target_price_krw", "prev_target_price_krw", "change_pct",
                               "opinion", "prev_opinion", "opinion_class", "prev_opinion_class",
                               "observed_date"),
        "security": ("ticker", "sec_type"),
        "security_span": ("ticker", "first_date", "last_date")},
    available_basis=("measured",),
    content_date_column="opinion_date",
    reject_reasons=BROKER_REJECT_REASONS,
    extra_gates=(eg3_opinion_broker_daily, eg9_coverage_broker),
))

TABLES: tuple[EquityTable, ...] = (OPINION_DAILY, OPINION_BROKER_DAILY)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s18.json"
"""이 슬라이스가 요구하는 baseline 상수 — EG8 의 `opinion_daily.src_overlap_agree_min` 하나이고
사람 승인 대기라 seed 에 넣지 않는다. 파일이 그 사실과 로컬 실측을 기록한다."""

__all__ = ["BROKER_REJECT_REASONS", "OPINION_BROKER_DAILY", "OPINION_CLASS_VOCAB",
           "OPINION_DAILY", "OPINION_REJECT_REASONS", "SRC_VOCAB", "TABLES"]
