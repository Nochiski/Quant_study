"""S16 주식수·자사주·배당 슬라이스 — `shares_outstanding`·`treasury_stock`·`dividend_event`.

DESIGN v1.2 §4-5(4B 확정) · GATES v1.0 §2 매트릭스 20·21·23행 · §3-⑯·⑱ ·
§4 FX-4B-001·004·006 · §9 S16 정정 · WORKFLOW §3-2 S16 행.

세 테이블 다 receipt_axis · `available_date = rcept_dt`(stage 가 derived 로 낸 값 그대로) ·
grain 은 stage 자연키에서 `rcept_no` 를 뺀 것이다. `row_kind='aggregate'` 제외는
`stg_shares`·`stg_tesstk` 둘뿐 — **`stg_dividend` 에는 `row_kind` 열이 아예 없다**
(stage `rules_dart.py` 가 hyslr·shares·tesstk 3개에만 붙인다). GATES §3-⑱ 우변의
`WHERE row_kind IS DISTINCT FROM 'aggregate'` 는 실행되지 않으므로 술어를 뺀 형태가 정본이고,
같은 이유로 WORKFLOW §2-1 의 "비집계" 표현은 `dividend_event` 에 성립하지 않는다(GATES §5-B5).

**EG1 우변은 `.sql` 의 모집단 CTE 를 그대로 재사용한다**(S05 `pool_sql` · S11 `population_sql`
규약) — 모집단 정의를 두 벌 베끼지 않는다. 좌변은 셋 다 `count(*)` 다.

**판본 중복**: grain 에 `rcept_no` 가 없으므로 같은 grain 에 접수가 둘 이상이거나(원본 + 정정
재제출) 한 접수 안에서 같은 키가 되풀이되면 행이 겹친다. S11 `stg_disclosure` 선례대로
`QUALIFY row_number() … = 1` 로 한 행만 남기고, 접힌 수는 산출 컬럼 `n_src_rows` 와 EG3 기록형
metric(`n_collapsed_src_rows`)이 함께 남긴다. 절단본 실측: shares 0 · tesstk 58그룹(전부
`stock_knd='-'` 값 전 결측) · dividend 60그룹((grain, se) 축, 전부 `stock_knd='-'`).

**KRX 대조**: 주식수 정본은 KRX(`price_daily.shares_out`)이고 DART 주식수는 검산·보조다
(DESIGN §4-2). 그래서 값을 덮지 않고 `corp_ticker`·`price_daily` 를 `shares_outstanding` 의
입력으로 **고정만** 해서(S11 이 `stg_doc_meta` 를 고정만 하는 것과 같은 규약) EG3 이 결산일 기준
대조 결과를 기록형으로 싣는다. 종류(보통주/우선주) 판정은 S05 의 어휘 대응표
(`rules_s05.COMMON_KINDS`·`PREFERRED_KINDS`)를 재사용하고 이 표들에 실린 라벨만 대조 대상이다.

**배당 기준일·락일 없음**(DESIGN §4-5): `dividend_event` 는 `corp_event` 에 `cash_dividend`
행을 만들지 않고 락일 축도 내지 않는다 — 배당락 원천이 없어 TR 이 불가하다는 §11 한계 그대로다
(절단본 `stg_disclosure` 현금·현물배당결정 공시 0건).
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, register
from .rules_s05 import COMMON_KINDS as S05_COMMON_KINDS
from .rules_s05 import PREFERRED_KINDS as S05_PREFERRED_KINDS

SQL_DIR = Path(__file__).parent / "sql"
SHARES_SQL = SQL_DIR / "shares_outstanding.sql"
TREASURY_SQL = SQL_DIR / "treasury_stock.sql"
DIVIDEND_SQL = SQL_DIR / "dividend_event.sql"

REJECT_REASONS: tuple[str, ...] = ("rcept_dt_missing",)

# ── 종류 어휘 (S05 대응표 재사용 + 4B 원장에만 나오는 표기) ────────────────────
# S05 는 `stg_capital.isu_dcrs_stock_knd`(자유 텍스트 249종)를 위한 표다. 4B 원장 3종의 종류
# 라벨은 그보다 훨씬 짧고(절단본 shares 8 · tesstk 6 · dividend 8), 겹치는 것은 S05 표가 이미
# 덮으므로 **차이만** 여기서 더한다. S05 규약 그대로 명시적 문자열만 대응하고(패턴 매칭 없음)
# 앞뒤 공백은 trim 으로만 흡수한다. stage 는 09-06 재빌드부터 이 3테이블의 개행도 정규화하므로
# 정본 표기는 개행 없는 형태다 — 대응표가 정규화에 뒤처지면 그 행이 KRX 검산에서 조용히 빠지고
# 게이트는 그대로 pass 한다(실측: '의결권 있는 주식(보통주)' 3행, n_krx_class_rows 103 → 100).
# 회귀 게이트는 tests 의 `test_종류_어휘가_stage_라벨을_전부_덮는다` 다.
#   '의결권 있는 주식(보통주)' : 주식총수 원장의 은행권 표기. stage 정규화 뒤의 정본 표기이고,
#                              개행이 남아 있던 옛 판본 두 형태는 옛 빌드(`_pinned/` keep=3) 방어로
#                              함께 남긴다(S05 `.sql` 의 trim 과 같은 규약).
#   '보통부'                   : S05 COMMON_KINDS 에 이미 있는 한 음절 오타.
SHARES_COMMON_EXTRA: tuple[str, ...] = ("의결권 있는 주식(보통주)",
                                        "의결권 있는 주식\n(보통주)",
                                        "의결권 \n있는 주식\n(보통주)")
COMMON_KINDS: tuple[str, ...] = S05_COMMON_KINDS + SHARES_COMMON_EXTRA
PREFERRED_KINDS: tuple[str, ...] = S05_PREFERRED_KINDS
# 종류로 특정되지 않는 라벨 — 대조 대상이 아니다(격리도 하지 않는다, 원칙 ④).
NON_CLASS_LABELS: tuple[str, ...] = ("비고", "합계", "계", "총계", "종류주", "기타", "-")

# ── `stg_tesstk` 취득방법 축 (EG3_treasury_stock 합산 재계산) ──────────────────
# 원장은 잎(장내직접취득·장외직접취득·공개매수·수탁자보유물량·현물보유량·기타취득) 위에
# '소계'(직접취득 · 신탁계약) 와 '총계'(집계행)를 함께 싣는다. 잎만 더해야 총계와 같다.
TESSTK_SUBTOTAL_LABELS: tuple[str, ...] = ("소계",)
TESSTK_TOTAL_LABELS: tuple[str, ...] = ("총계",)

# ── `stg_dividend.se` 라벨 → 컬럼 대응표 (`.sql` 의 FILTER 리터럴이 이것을 옮긴다) ────
DPS_LABELS: tuple[str, ...] = ("주당 현금배당금(원)",)
CASH_TOTAL_LABELS: tuple[str, ...] = ("현금배당금총액(백만원)",)
YIELD_LABELS: tuple[str, ...] = ("현금배당수익률(%)",)
# 우선순위 순 — 앞에 있는 라벨이 있으면 그것을 쓰고 `payout_basis` 에 출처를 남긴다.
PAYOUT_LABELS: tuple[tuple[str, str], ...] = (
    ("(연결)현금배당성향(%)", "consolidated"),
    ("(별도)현금배당성향(%)", "separate"),
    ("(개별)현금배당성향(%)", "individual"),
    ("현금배당성향(%)", "unlabeled"),
)
PAYOUT_BASIS_VOCAB: tuple[str, ...] = tuple(b for _, b in PAYOUT_LABELS)
# 값 컬럼으로 전개하는 라벨 전부 — EG3 기록형 `n_unmapped_se` 의 여집합 축이다.
MAPPED_SE_LABELS: tuple[str, ...] = (
    DPS_LABELS + CASH_TOTAL_LABELS + YIELD_LABELS + tuple(s for s, _ in PAYOUT_LABELS))

# ── 모집단 SQL 재사용 (각 `.sql` 의 src CTE 까지) ─────────────────────────────
_POOL_MARKER = "-- ==== eg1:"


def _pool_prefix(path: Path) -> str:
    """`.sql` 첫 줄부터 모집단 CTE 를 닫는 괄호까지 — 뒤에 `SELECT … FROM src` 를 붙여 쓴다."""
    head, sep, _ = path.read_text(encoding="utf-8").partition(_POOL_MARKER)
    if not sep:
        raise ValueError(f"pool marker not found in {path}: expected a line starting with "
                         f"{_POOL_MARKER!r} right after the src CTE")
    return head


def pool_sql(path: Path, select: str) -> str:
    """모집단 CTE 위의 단일 SELECT. 예: pool_sql(SHARES_SQL, \"count(*) FROM src\")."""
    return f"{_pool_prefix(path)}\nSELECT {select}"


def _distinct_rhs(path: Path, keys: tuple[str, ...]) -> str:
    """EG1 우변 — 모집단 CTE 의 grain distinct 행수(GATES §3-⑯·⑱ 형태 그대로)."""
    return pool_sql(path, f"count(*) FROM (SELECT DISTINCT {', '.join(keys)} FROM src)")


EG1_LHS_SQL = 'SELECT count(*) FROM "out_pq"'

SHARES_GRAIN: tuple[str, ...] = ("corp_code", "bsns_year", "reprt_code", "se")
TREASURY_GRAIN: tuple[str, ...] = ("corp_code", "bsns_year", "reprt_code",
                                   "acqs_mth1", "acqs_mth2", "acqs_mth3", "stock_knd")
DIVIDEND_GRAIN: tuple[str, ...] = ("corp_code", "bsns_year", "reprt_code", "stock_knd")

# receipt_axis — grain 에 `rcept_no` 가 없어도 산출 행마다 접수번호가 하나씩 실린다(DESIGN §2).
RECEIPT_KEY_EXPR = "CAST(substr(rcept_no, 1, 4) AS INTEGER)"


# ── 게이트 공통 헬퍼 (rules_s05 와 같은 모양) ─────────────────────────────────

def _n(ctx: EquityGateContext, sql: str) -> int:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row — table={ctx.rule.name} sql={sql[:200]}")
    return int(str(row[0]))


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _counts(ctx: EquityGateContext, column: str) -> dict[str, int]:
    return {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f'SELECT "{column}", count(*) FROM "{ctx.out_view}" GROUP BY 1 ORDER BY 1').fetchall()}


def _result(name: str, checks: dict[str, int], metrics: dict[str, object], ok_detail: str
            ) -> GateResult:
    bad = {k: n for k, n in checks.items() if n}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, ok_detail, merged)
    return GateResult(name, GateStatus.FAIL,
                      "; ".join(f"{k}={n}" for k, n in sorted(bad.items())), merged)


def _class_case(column: str) -> str:
    """trim 한 라벨을 S05 대응표로 common/preferred 에 대응시키는 CASE 식(그 밖은 NULL)."""
    return (f"CASE WHEN trim({column}) IN ({_vocab_sql(COMMON_KINDS)}) THEN 'common' "
            f"WHEN trim({column}) IN ({_vocab_sql(PREFERRED_KINDS)}) THEN 'preferred' END")


def _pool_dedup_metrics(ctx: EquityGateContext, path: Path, grain: tuple[str, ...]
                        ) -> dict[str, object]:
    """모집단에서 접힌 판본 수 — 산출을 보지 않고 입력 뷰만으로 센다(항진명제 회피)."""
    keys = ", ".join(grain)
    n_pool = _n(ctx, pool_sql(path, "count(*) FROM src"))
    n_grain = _n(ctx, pool_sql(path, f"count(*) FROM (SELECT DISTINCT {keys} FROM src)"))
    return {"n_pool_rows": n_pool, "n_pool_grain": n_grain,
            "n_collapsed_src_rows": n_pool - n_grain}


# ── shares_outstanding ────────────────────────────────────────────────────────

def eg3_shares_outstanding(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — 접수번호 단일성 · 판본 접힘 정합 · 주식수 항등·KRX 대조(기록형).

    폐기형 셋은 산출 규칙이 스스로 지켜야 하는 것뿐이다 — `rcept_no` 결측 0(receipt_axis 파티션
    키) · `n_src_rows ≥ 1` · **판본 선택이 실재 원장 행을 골랐는가**(`n_row_not_in_stage`).
    **발행 = 자기 + 유통 항등과 KRX 대조는 기록형**이다 — 값은 DART 원장 그대로 싣고(원칙 ②·④)
    정본은 KRX(`price_daily.shares_out`, DESIGN §4-2)라서 어긋남은 폐기 사유가 아니라 신호다.
    서버 실측 뒤 승격 판단(GATES §0-1 · S04 선례).
    """
    v = ctx.out_view
    checks = {
        # 접수번호는 grain 이 아니지만 receipt_axis 파티션 키라 행마다 하나여야 한다
        "n_rcept_no_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE rcept_no IS NULL'),
        "n_src_rows_lt_1": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE n_src_rows < 1'),
        # 판본 선택이 실재하는 원장 행을 골랐는가 — 산출 행 전부가 stage 축으로 되짚어져야 한다
        "n_row_not_in_stage": _n(
            ctx, f'SELECT count(*) FROM "{v}" o WHERE NOT EXISTS (SELECT 1 FROM stg_shares s '
                 "WHERE s.corp_code = o.corp_code AND s.bsns_year = o.bsns_year "
                 "AND s.reprt_code = o.reprt_code AND s.se = o.se AND s.rcept_no = o.rcept_no "
                 "AND s.available_date IS NOT DISTINCT FROM o.available_date "
                 "AND s.istc_totqy_shr IS NOT DISTINCT FROM o.issued_shr)"),
    }
    # 항등 발행 = 자기 + 유통 (세 값이 다 있는 행만)
    n_identity_pop = _n(ctx, f'SELECT count(*) FROM "{v}" WHERE issued_shr IS NOT NULL '
                             "AND treasury_shr IS NOT NULL AND distributed_shr IS NOT NULL")
    n_identity_bad = _n(ctx, f'SELECT count(*) FROM "{v}" WHERE issued_shr IS NOT NULL '
                             "AND treasury_shr IS NOT NULL AND distributed_shr IS NOT NULL "
                             "AND issued_shr <> treasury_shr + distributed_shr")
    metrics: dict[str, object] = {
        **_pool_dedup_metrics(ctx, SHARES_SQL, SHARES_GRAIN),
        "n_by_se": _counts(ctx, "se"),
        "n_identity_measurable": n_identity_pop,
        "n_share_identity_mismatch": n_identity_bad,
        "n_issued_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE issued_shr IS NULL'),
        "n_non_class_label": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE trim(se) IN '
                                     f"({_vocab_sql(NON_CLASS_LABELS)})"),
        "reject_by_reason": dict(ctx.reject_by_reason),
        **_krx_cross_metrics(ctx),
    }
    return _result("EG3_shares_outstanding", checks, metrics,
                   "주식수 판 접수·판본 축 불변식 성립(항등·KRX 대조는 기록형)")


eg3_shares_outstanding.gate_name = "EG3_shares_outstanding"     # type: ignore[attr-defined]


def _krx_cross_metrics(ctx: EquityGateContext) -> dict[str, object]:
    """DART 발행주식총수 ↔ KRX `price_daily.shares_out`(정본) 결산일 대조 — 전부 기록형.

    법인 → 티커 전개는 S05 `corp_event.sql` 의 `legs` 와 같은 규칙이다(`is_common` 또는 비KR7
    단독 티커면 common). 그 종류의 상장 티커가 정확히 하나인 법인만 비교하고, 여럿이면
    `n_krx_ambiguous` 로만 센다 — 합산 규칙을 여기서 새로 만들지 않는다.

    다섯 갈래(`n_krx_no_ticker`·`n_krx_ambiguous`·`n_krx_no_price`·`n_krx_stale`·
    `n_krx_compared`)는 `n_krx_class_rows` 를 **분할**한다 — 어느 갈래에도 안 들어가는 행이
    생기면 그만큼 대조가 조용히 사라진다(tests 가 합을 대조한다).

    `corp_ticker` 는 시점축이 없는 현재 스냅샷이라(grain `ticker`) 폐지·**티커 재사용** 구간에서
    결산일과 동떨어진 가격 행이 잡힌다 — 절단본 036220·101970 이 그 사례다(마지막 가격이
    2016-05-04 / 2015-03-16 인데 결산일은 2023·2024). 그래서 **결산일과 같은 해의 가격 행**만
    비교하고 나머지는 `n_krx_stale` 로 센다. 임계는 두지 않는다: 정본은 KRX 이고 DART 주식수는
    검산·보조라(DESIGN §4-2) 어긋남은 폐기 사유가 아니라 신호다(상장주식수 ≠ 발행주식총수인
    비상장 종류주·신주 상장 전 구간이 정상적으로 남는다).
    """
    v = ctx.out_view
    rows = ctx.con.execute(f"""
        WITH cls AS (
            SELECT o.corp_code, o.stlm_dt, o.issued_shr, {_class_case('o.se')} AS cls
            FROM "{v}" o
        ),
        legs AS (
            SELECT corp_code, ticker,
                   CASE WHEN is_common OR isin8 NOT LIKE 'KR7%' THEN 'common'
                        ELSE 'preferred' END AS cls
            FROM corp_ticker WHERE corp_code IS NOT NULL
        ),
        one AS (
            SELECT corp_code, cls, min(ticker) AS ticker, count(*) AS n_ticker
            FROM legs GROUP BY corp_code, cls
        ),
        cmp AS (
            SELECT c.issued_shr, c.stlm_dt, o.n_ticker,
                   (SELECT p.date FROM price_daily p
                     WHERE p.ticker = o.ticker AND p.date <= c.stlm_dt
                     ORDER BY p.date DESC LIMIT 1) AS px_date,
                   (SELECT p.shares_out FROM price_daily p
                     WHERE p.ticker = o.ticker AND p.date <= c.stlm_dt
                     ORDER BY p.date DESC LIMIT 1) AS krx_shares
            FROM cls c LEFT JOIN one o ON o.corp_code = c.corp_code AND o.cls = c.cls
            WHERE c.cls IS NOT NULL AND c.issued_shr IS NOT NULL AND c.stlm_dt IS NOT NULL
        ),
        tagged AS (
            SELECT *, n_ticker = 1 AND krx_shares IS NOT NULL
                      AND year(px_date) = year(stlm_dt) AS comparable
            FROM cmp
        )
        SELECT count(*),
               count(*) FILTER (n_ticker IS NULL),
               count(*) FILTER (n_ticker > 1),
               count(*) FILTER (n_ticker = 1 AND krx_shares IS NULL),
               count(*) FILTER (n_ticker = 1 AND krx_shares IS NOT NULL AND NOT comparable),
               count(*) FILTER (comparable),
               count(*) FILTER (comparable AND krx_shares <> issued_shr),
               coalesce(max(date_diff('day', px_date, stlm_dt)) FILTER (comparable), 0)
        FROM tagged""").fetchone()
    if rows is None:
        raise RuntimeError("KRX cross-check query returned no row")
    n_class, n_no_leg, n_amb, n_no_price, n_stale, n_cmp, n_bad, lag = (
        int(str(x)) for x in rows)
    return {"n_krx_class_rows": n_class, "n_krx_no_ticker": n_no_leg,
            "n_krx_ambiguous": n_amb,
            "n_krx_no_price": n_no_price, "n_krx_stale": n_stale,
            "n_krx_compared": n_cmp, "n_krx_mismatch": n_bad,
            "krx_price_lag_days_max": lag}


# ── treasury_stock ────────────────────────────────────────────────────────────

def eg3_treasury_stock(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — 접수·판본 축 + 원장 총계 재계산(기록형, GATES FX-4B-001).

    총계는 산출에 없는 축(`row_kind='aggregate'` 행)이라 **입력 뷰**를 다시 읽어 비교한다.
    잎 행만 더한다 — 원장에는 '소계'(직접취득 · 신탁계약) 도 `detail` 로 실려 있어 그대로 더하면
    이중계상이다.
    """
    v = ctx.out_view
    checks = {
        "n_rcept_no_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE rcept_no IS NULL'),
        "n_src_rows_lt_1": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE n_src_rows < 1'),
        # 집계행은 모집단 밖이어야 한다 — 산출에 총계 라벨이 남으면 모집단 술어가 흔들린 것이다
        "n_total_label_in_out": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE acqs_mth3 IN '
                 f"({_vocab_sql(TESSTK_TOTAL_LABELS)})"),
    }
    leaf = f"acqs_mth3 NOT IN ({_vocab_sql(TESSTK_SUBTOTAL_LABELS)})"
    total = f"acqs_mth3 IN ({_vocab_sql(TESSTK_TOTAL_LABELS)})"
    n_total_groups, n_total_bad = ctx.con.execute(f"""
        WITH out_leaf AS (
            SELECT corp_code, bsns_year, reprt_code, stock_knd,
                   sum(acquired_shr) AS acq, sum(end_shr) AS fin
            FROM "{v}" WHERE {leaf} GROUP BY ALL
        ),
        led AS (
            SELECT corp_code, bsns_year, reprt_code, stock_knd,
                   sum(change_qy_acqs_shr) AS acq, sum(trmend_qy_shr) AS fin
            FROM stg_tesstk WHERE row_kind = 'aggregate' AND {total} GROUP BY ALL
        )
        SELECT count(*), count(*) FILTER (l.acq IS DISTINCT FROM o.acq
                                          OR l.fin IS DISTINCT FROM o.fin)
        FROM led l LEFT JOIN out_leaf o USING (corp_code, bsns_year, reprt_code, stock_knd)
        """).fetchone() or (0, 0)
    metrics: dict[str, object] = {
        **_pool_dedup_metrics(ctx, TREASURY_SQL, TREASURY_GRAIN),
        "n_by_acqs_mth3": _counts(ctx, "acqs_mth3"),
        "n_ledger_total_groups": int(str(n_total_groups)),
        "n_ledger_total_mismatch": int(str(n_total_bad)),
        "n_subtotal_rows": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE acqs_mth3 IN '
                                   f"({_vocab_sql(TESSTK_SUBTOTAL_LABELS)})"),
        "n_all_null_rows": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE begin_shr IS NULL '
                                   "AND acquired_shr IS NULL AND disposed_shr IS NULL "
                                   "AND retired_shr IS NULL AND end_shr IS NULL"),
        "reject_by_reason": dict(ctx.reject_by_reason),
    }
    return _result("EG3_treasury_stock", checks, metrics,
                   "자사주 판 접수·판본 축 불변식 성립(원장 총계 대조는 기록형)")


eg3_treasury_stock.gate_name = "EG3_treasury_stock"             # type: ignore[attr-defined]


# ── dividend_event ────────────────────────────────────────────────────────────

def eg3_dividend_event(ctx: EquityGateContext) -> GateResult:
    """EG3-P01 보강 — 라벨 → 컬럼 전개의 정합(폐기형) + 대응표 밖 라벨·값 충돌(기록형).

    폐기형은 전개 규칙이 스스로 지켜야 하는 것뿐이다: 접수번호·`n_src_rows` 하한 ·
    `payout_basis` 어휘 폐쇄 · `payout_pct` ⇔ `payout_basis` 동치.
    **법인 축 값(총액·성향)이 종류 축 행에 실렸는가는 기록형**이다 — 절단본에서는 원장이 그
    라벨을 `stock_knd='-'` 에만 싣지만(그래서 0), 어느 접수가 '보통주' 로 적어 올 수 있고 그건
    원천 서식 신호이지 산출 버그가 아니다(GATES §0-1 기록형 · S04 선례).
    """
    v = ctx.out_view
    dash = _vocab_sql(("-",))
    checks = {
        "n_rcept_no_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE rcept_no IS NULL'),
        "n_src_rows_lt_1": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE n_src_rows < 1'),
        "n_payout_basis_outside_vocab": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE payout_basis IS NOT NULL '
                 f"AND payout_basis NOT IN ({_vocab_sql(PAYOUT_BASIS_VOCAB)})"),
        "n_payout_basis_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" '
                 "WHERE (payout_pct IS NULL) <> (payout_basis IS NULL)"),
    }
    # (grain, se) 축에서 값이 둘 다 있고 서로 다른 그룹 — QUALIFY 가 하나를 버린 자리
    n_conflict = _n(ctx, pool_sql(
        DIVIDEND_SQL,
        "count(*) FROM (SELECT corp_code, bsns_year, reprt_code, stock_knd, se, "
        "count(DISTINCT thstrm) AS d FROM src GROUP BY ALL HAVING d > 1)"))
    n_pool_se = _n(ctx, pool_sql(DIVIDEND_SQL, "count(*) FROM src"))
    n_pool_se_grain = _n(ctx, pool_sql(
        DIVIDEND_SQL, "count(*) FROM (SELECT DISTINCT corp_code, bsns_year, reprt_code, "
                      "stock_knd, se FROM src)"))
    # 한 grain 에 접수가 둘 이상인 수 — 라벨마다 판본이 갈릴 수 있는 자리(절단본 0)
    n_multi_rcept = _n(ctx, pool_sql(
        DIVIDEND_SQL,
        "count(*) FROM (SELECT corp_code, bsns_year, reprt_code, stock_knd, "
        "count(DISTINCT rcept_no) AS d FROM src GROUP BY ALL HAVING d > 1)"))
    unmapped = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(pool_sql(
        DIVIDEND_SQL, f"se, count(*) FROM src WHERE se NOT IN ({_vocab_sql(MAPPED_SE_LABELS)}) "
                      "GROUP BY se ORDER BY se")).fetchall()}
    metrics: dict[str, object] = {
        **_pool_dedup_metrics(ctx, DIVIDEND_SQL, DIVIDEND_GRAIN),
        "n_pool_grain_se": n_pool_se_grain,
        "n_collapsed_grain_se": n_pool_se - n_pool_se_grain,
        "n_value_conflict": n_conflict,
        "n_grain_multi_rcept": n_multi_rcept,
        # 법인 축 라벨(총액·성향)이 종류 축 행에 실린 수 — 원장 서식 신호(절단본 0)
        "n_firm_value_on_class_row": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE trim(stock_knd) NOT IN ({dash}) '
                 "AND (cash_total_krw IS NOT NULL OR payout_pct IS NOT NULL)"),
        "n_by_stock_knd": _counts(ctx, "stock_knd"),
        "n_by_payout_basis": _counts(ctx, "payout_basis"),
        "n_dps_not_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE dps_krw IS NOT NULL'),
        "n_cash_total_not_null": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                         "WHERE cash_total_krw IS NOT NULL"),
        "n_unmapped_se_rows": sum(unmapped.values()),
        "unmapped_se_labels": sorted(unmapped),
        "cash_total_unit_krw": ctx.baseline.get(ctx.rule.name, "cash_total_unit_krw"),
        # 배당락·기준일 축은 없다(DESIGN §4-5 · §11) — corp_event cash_dividend 행도 만들지 않는다
        "has_ex_date_axis": False,
        "reject_by_reason": dict(ctx.reject_by_reason),
    }
    return _result("EG3_dividend_event", checks, metrics,
                   "배당 라벨 전개 불변식 성립(값 충돌·미대응 라벨은 기록형)")


eg3_dividend_event.gate_name = "EG3_dividend_event"             # type: ignore[attr-defined]


# ── 선언 ──────────────────────────────────────────────────────────────────────

SHARES_OUTSTANDING = register(EquityTable(
    name="shares_outstanding",
    grain=SHARES_GRAIN,
    columns={"corp_code": "VARCHAR", "bsns_year": "VARCHAR", "reprt_code": "VARCHAR",
             "se": "VARCHAR", "rcept_no": "VARCHAR", "stlm_dt": "DATE",
             "issued_shr": "DECIMAL(17,0)", "treasury_shr": "DECIMAL(15,0)",
             "distributed_shr": "DECIMAL(17,0)", "n_src_rows": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    # `corp_ticker`·`price_daily` 는 산출식이 읽지 않는다 — KRX 대조(기록형)를 재현 가능하게
    # 하려고 고정만 한다(S11 이 `stg_doc_meta` 를 고정만 하는 것과 같은 규약).
    inputs=("stg_shares", "corp_ticker", "price_daily"),
    partition_class="receipt_axis",
    partition_key_expr=RECEIPT_KEY_EXPR,
    available_rule="column:available_date — DART 접수일 rcept_dt(stage derived)",
    eg1_lhs_sql=EG1_LHS_SQL,
    eg1_rhs_sql=_distinct_rhs(SHARES_SQL, SHARES_GRAIN),
    sql_path=SHARES_SQL,
    input_columns={
        "stg_shares": ("corp_code", "bsns_year", "reprt_code", "se", "rcept_no", "stlm_dt",
                       "istc_totqy_shr", "tesstk_co_shr", "distb_stock_co_shr", "row_kind",
                       "available_date", "available_basis"),
        "corp_ticker": ("ticker", "isin8", "corp_code", "is_common"),
        "price_daily": ("ticker", "date", "shares_out")},
    available_basis=("derived",),
    content_date_column="stlm_dt",
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg3_shares_outstanding,),
))

TREASURY_STOCK = register(EquityTable(
    name="treasury_stock",
    grain=TREASURY_GRAIN,
    columns={"corp_code": "VARCHAR", "bsns_year": "VARCHAR", "reprt_code": "VARCHAR",
             "acqs_mth1": "VARCHAR", "acqs_mth2": "VARCHAR", "acqs_mth3": "VARCHAR",
             "stock_knd": "VARCHAR", "rcept_no": "VARCHAR", "stlm_dt": "DATE",
             "begin_shr": "DECIMAL(15,0)", "acquired_shr": "DECIMAL(14,0)",
             "disposed_shr": "DECIMAL(14,0)", "retired_shr": "DECIMAL(10,0)",
             "end_shr": "DECIMAL(15,0)", "n_src_rows": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_tesstk",),
    partition_class="receipt_axis",
    partition_key_expr=RECEIPT_KEY_EXPR,
    available_rule="column:available_date — DART 접수일 rcept_dt(stage derived)",
    eg1_lhs_sql=EG1_LHS_SQL,
    eg1_rhs_sql=_distinct_rhs(TREASURY_SQL, TREASURY_GRAIN),
    sql_path=TREASURY_SQL,
    input_columns={
        "stg_tesstk": ("corp_code", "bsns_year", "reprt_code", "acqs_mth1", "acqs_mth2",
                       "acqs_mth3", "stock_knd", "rcept_no", "stlm_dt", "bsis_qy_shr",
                       "change_qy_acqs_shr", "change_qy_dsps_shr", "change_qy_incnr_shr",
                       "trmend_qy_shr", "row_kind", "available_date", "available_basis")},
    available_basis=("derived",),
    content_date_column="stlm_dt",
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg3_treasury_stock,),
))

DIVIDEND_EVENT = register(EquityTable(
    name="dividend_event",
    grain=DIVIDEND_GRAIN,
    columns={"corp_code": "VARCHAR", "bsns_year": "VARCHAR", "reprt_code": "VARCHAR",
             "stock_knd": "VARCHAR", "rcept_no": "VARCHAR", "stlm_dt": "DATE",
             "dps_krw": "DECIMAL(15,2)", "cash_total_krw": "DECIMAL(38,2)",
             "yield_pct": "DECIMAL(15,2)", "payout_pct": "DECIMAL(15,2)",
             "payout_basis": "VARCHAR", "n_src_rows": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_dividend",),
    partition_class="receipt_axis",
    partition_key_expr=RECEIPT_KEY_EXPR,
    available_rule="column:available_date — DART 접수일 rcept_dt(stage derived)",
    eg1_lhs_sql=EG1_LHS_SQL,
    eg1_rhs_sql=_distinct_rhs(DIVIDEND_SQL, DIVIDEND_GRAIN),
    sql_path=DIVIDEND_SQL,
    input_columns={
        "stg_dividend": ("corp_code", "bsns_year", "reprt_code", "se", "stock_knd", "rcept_no",
                         "stlm_dt", "thstrm", "observed_date", "available_date",
                         "available_basis")},
    available_basis=("derived",),
    content_date_column="stlm_dt",
    reject_reasons=REJECT_REASONS,
    consts=("cash_total_unit_krw",),
    extra_gates=(eg3_dividend_event,),
))

TABLES: tuple[EquityTable, ...] = (SHARES_OUTSTANDING, TREASURY_STOCK, DIVIDEND_EVENT)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s16.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측). 승인 뒤 `baseline.json` 에 병합한다."""

__all__ = ["BASELINE_SEED", "CASH_TOTAL_LABELS", "COMMON_KINDS", "DIVIDEND_EVENT",
           "DIVIDEND_GRAIN", "DIVIDEND_SQL", "DPS_LABELS", "MAPPED_SE_LABELS",
           "NON_CLASS_LABELS", "PAYOUT_BASIS_VOCAB", "PAYOUT_LABELS", "PREFERRED_KINDS",
           "REJECT_REASONS", "SHARES_COMMON_EXTRA", "SHARES_GRAIN", "SHARES_OUTSTANDING",
           "SHARES_SQL", "TABLES", "TESSTK_SUBTOTAL_LABELS", "TESSTK_TOTAL_LABELS",
           "TREASURY_GRAIN", "TREASURY_SQL", "TREASURY_STOCK", "YIELD_LABELS", "pool_sql"]
