"""S10 슬라이스 선언 — `credit_daily` 신용 일별 격자 (DESIGN v1.2 §4-3, GATES v1.0 §3 ⑪ ·
EG7-P06 · §4 FX-3-008).

3단계 격자 테이블의 첫 구현이다. 사실은 `stg_credit_daily`(KIS 신용잔고) 하나뿐이고, equity 가
더하는 것은 셋이다 — ① 격자(캘린더 × `universe_daily`)로 **없는 날을 행으로 만든다** ②
`stg_units_kis`(dataset='credit') 수집 로그로 그 빈칸의 이유를 `fill_kind` 로 적는다 ③ 쓸 수 없는
원장 행을 버리지 않고 `_reject/{version_folded, pre_calendar, off_grid, balance_over_shares}` 로
격리한다.

**판본 선택(S10 3차, 09-11 · 결정 6-2 · 검수 종합 H3)** — `stg_credit_daily` 는 append_only ·
`key_unique=False` 라 같은 (ticker, date) 에 값이 다른 재수집 판본이 공존한다(서버 실측 536군 ·
2026년 505군 · 29종목). 원인은 KIS 가 `stck_prpr` 등 가격을 조회 시점 수정주가로 **소급 환산**해
주는 것이라 기업행위 뒤 재수집하면 payload 가 달라지는 데 있다. equity 는 (ticker, date) 당
**`observed_date` 최소 = 최초 관측판** 하나만 채택하고(그날 시점에 볼 수 있던 값), 나머지는
`_reject/version_folded/` 로 원값째 격리한다. 선택하지 않으면 격자 조인이 팬아웃해 프레임 EG3 의
격자 유일성에서 빌드가 폐기된다. 정렬 동률은 우리가 나르는 payload 전부로 깨서 재현성(EG5a)을
지킨다 — stage 산출에 `collected_at` 원값 컬럼이 없기 때문이다.

**잔고 이상 격리(S10 2차, 09-06)** — 서버 1차 빌드가 `EG3_credit_daily.n_balance_over_shares_out`
4행으로 폐기됐다(격자 9,201,516 · 다른 술어 전부 0). 잔고주수는 상장주식수를 넘을 수 없으므로 그
원장 행은 데이터 이상이고, 실으면 신용잔고비율 > 100% 셀이 소비층까지 간다. 고친 방향은 게이트를
느슨하게 하는 것이 아니라 **격리**다: 원장 행은 `_reject/balance_over_shares/` 로 원값째 보관하고
(EG7 분모에 들어간다), 그 (date, ticker) 격자 셀은 **값 없이 남겨** 격자 등식 ⑪(a)를 지킨다. 셀의
`fill_kind.kind` 는 `REJECTED_CELL_KIND`(= `empty_response` → 엔진 `MISSING`)이고, 게이트의
`n_balance_over_shares_out` 은 **산출 검사로 그대로 유지**(0 이어야 한다)한 뒤 입력에서 다시 센
위반 행수를 격리 건수와 대조한다.

**빈칸에 0 을 굽지 않는다**(원칙 ④, S10 결정). `src_omitted` 를 0 으로 채우는 규약(FX-3-001 은
`short_daily` 키움 샤드 축에 그렇게 적혀 있다)을 신용 격자에 옮기면 원장 일괄 결측일에 시장 전체가
잔고 0 으로 굳는다 — 절단본 2018-03-28(전 종목 credit 행 없음, 앞뒤 거래일은 정상)과
2026-08-19·20(P16: credit 백필 08-18 종료, 캘린더는 08-20)이 실례다. 유닛 창은 '요청 구간' 이라
그 날짜가 응답에 들었는지까지 말해 주지 않으므로 값을 단정할 근거가 못 된다. 0 의 뜻은
`fill_kind.kind='src_omitted'` 가 나르고 엔진 `CellKind.SOURCE_OMITTED_ZERO`(FIELD_MAP §1)가 소비
시점에 읽는다 — 값 축과 지식 축을 섞지 않는다. 의심일은 EG3_credit_daily 의 `ledger_gap_dates`
기록형이 지목한다.

입력 5 — equity `trading_calendar`·`universe_daily`·`price_daily` + stage `stg_credit_daily`·
`stg_units_kis`. `price_daily` 는 산출에 쓰지 않고 게이트만 읽는다(잔고 ≤ 상장주식수 · KIS
수정종가 대조) — 같은 `_pinned/` 규약이라 판본이 BuildRecord 에 남는다.

컬럼 규약:
  · 측정 축 16개는 **stage 실명 그대로** 나른다(FIELD_MAP §2 가 `credit_daily.
    whol_loan_rmnd_stcn_shr` 로 이미 계약을 걸었다). `_stcn_shr`(주수)·`_pct`(비율)는 stage 단위
    접미사이고, `*_amt` 6개는 **단위 미상**(STAGE_HANDOFF §1·§4)이라 `_krw` 를 붙이지 않고
    `amt_basis='unknown'` 한 컬럼으로 그 사실을 기계가 읽게 한다(FX-3-009 "단위 미확정 컬럼에
    접미사 금지" 와 같은 규약).
  · `stlm_date`(결제일 = date + 2~12일)는 보존 컬럼이다. PIT 축은 `date` 이고
    `available_date = date`(basis `default`) — 공표 랙은 팩트 행에 굽지 않고 `dataset_profile`
    (S19)의 `recommended_lag_sessions` 가 세션 단위로 낸다(FIELD_MAP §1 '랙 단위').
  · KIS 가 같이 주는 가격 에코(close/OHL·prdy_*·acml_vol_shr)는 나르지 않는다 —
    `price_basis_close='adjusted_asof_collect'`(수집 시점 수정종가)라 PIT 가격축이 아니고 원주가
    정본은 `price_daily`(원칙 ②)다. 두 축 차이는 EG3_credit_daily 기록형이 센다.

**`credit.net_buy` 축 판정(S10, FIELD_MAP §2·§3 갱신)**: **부재**. `stg_credit_daily` 39컬럼에
순매수 축이 없고, 유일한 후보인 `신규 − 상환`(`whol_*_new_stcn_shr − whol_*_rdmp_stcn_shr`)은
순매수가 아니라 잔고 증감의 구성요소인데다 실제로 증감과 맞지도 않는다 — 절단본 실측으로 융자는
연속 쌍 24,711 중 17,364(70.3%)만 `잔고[t] − 잔고[t−1] = 신규[t] − 상환[t]` 가 성립하고(대주는
24,672 = 99.8%), 신규·상환이 음수인 행이 6건 있어 방향 해석이 닫히지 않는다. 근거 수치는
EG3_credit_daily 의 `net_buy_axis`·`n_loan_balance_step_*` 기록형 metric 이 매 빌드 갱신한다.

테이블 특화 술어(`extra_gates`, 프레임은 EG3 뒤에 실행한다 — GATES §7-1):
  EG1_credit_daily — GATES §3 ⑪ **(b)** 원장 보존 등식. `count(stg_credit_daily) = measured 셀
                     + reject(version_folded) + reject(pre_calendar) + reject(off_grid)
                     + reject(balance_over_shares)`. ⑪ (a) 격자 행수 등식은 프레임
                     EG1 이 본다(등식 1개 규약) — 좌변 `count(out)`, 우변 `격자 + 격자 밖 원장 행`.
  EG3_credit_daily — 어휘 폐쇄(`fill_kind.kind`·`.evidence`·`amt_basis`) · 격자 양방향 일치 ·
                     measured 셀 원값 보존(stage 재조인) · 잔고 음수 0 · **잔고 ≤ 같은 날
                     `price_daily.shares_out`** · 채움 규약(measured 아닌 셀은 전 축 NULL) ·
                     `stlm_date ≥ date` · **잔고 이상 격리 대조**(입력 재계산 위반 행수 =
                     `_reject/balance_over_shares/` 건수 · 그 키의 산출 셀이 남아 있고 measured 가
                     아니다). 나머지는 기록형 metric(GATES §0-1): fill_kind 분포 ·
                     격리 사유별 건수 · KIS 수정종가 대조 · `*_amt` 단위 추정비 · 잔고비율 대
                     상장주식수 비(**deal_date·stlm_date 두 기준일**, 검수 M3) · 음수
                     신규/상환/증감율 · 원장 일괄 결측 의심일 · net_buy 판정 근거 ·
                     판본 분포(`n_versions_folded`·`n_version_groups`). 판본 대조
                     (`n_version_folded_reject_delta`)는 폐기형이다.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import FILL_EVIDENCE, FILL_KINDS, EquityTable, FieldProfile, register
from .rules_s01 import TICKER_LEN

SQL_DIR = Path(__file__).parent / "sql"

# DESIGN §4-3 격자 술어. `sec_type` NULL 은 격자에 남긴다(`IS DISTINCT FROM`) — 종류 미상 종목을
# 조용히 빼면 게이트가 생존편향을 만든다(GATES §5-C6 부류).
GRID_STATUS: tuple[str, ...] = ("listed", "suspended")
GRID_EXCLUDED_SEC_TYPE = "etf"

# EG7-P06 격리 어휘 — 캘린더 하한 이전 / 격자 밖(상장 전·재상장 공백) / 잔고 이상.
# `_reject/<reason>/` 디렉토리 이름이자 EG3 폐쇄 대상이고, EG1_credit_daily 원장 보존 등식의 항이다.
# `balance_over_shares`(S10 2차, 09-06): 잔고주수 > 그날 `price_daily.shares_out` 인 원장 행. 서버
# 1차 빌드가 이 4행 때문에 EG3_credit_daily 로 폐기됐다 — 잔고는 상장주식수를 넘을 수 없으므로
# 원장 이상이고, 격리해야 신용잔고비율 > 100% 셀이 소비층까지 가지 않는다. 격리되는 것은 **원장
# 행**이고 격자 셀은 값 없이 남는다(§ `sql/credit_daily.sql` `over`·`drop_value`).
# `version_folded`(S10 3차, 09-11 · 결정 6-2): 같은 (ticker, date) 에 값이 다른 재수집 판본이
# 여럿일 때 **최초 관측판이 아닌** 원장 행. 네 사유는 배타적이고 `version_folded` 가 가장 먼저
# 판정된다 — 채택되지 않은 판본은 격자·캘린더 술어를 다시 묻지 않는다.
REJECT_REASONS: tuple[str, ...] = ("pre_calendar", "off_grid", "balance_over_shares",
                                   "version_folded")
# 잔고 이상으로 값을 지운 격자 셀의 `fill_kind.kind`. `FILL_KINDS` 에 'rejected' 가 없고 어휘는
# DESIGN §3 · 엔진 `CellKind` 계약이라 여기서 늘리지 않는다 — 남은 넷 중 `empty_response`(→ 엔진
# `MISSING`)만이 "원천에 물었고 쓸 값이 없다" 를 뜻해 가장 정직하다.
REJECTED_CELL_KIND = "empty_response"
# `stg_units_kis.dataset` 중 이 테이블이 읽는 갈래 (DESIGN §4-3 유닛 dataset 어휘)
UNIT_DATASET = "credit"

# 측정 축 16 — 융자(loan) 8 + 대주(stln) 8, 각각 수량·금액 6(신규·상환·잔고 × 주수·금액)과
# 비율 2(잔고비율 `_rmnd_rate_pct` · 증감율 `_gvrt_pct`, KIS 增減率). 순서는 산출 컬럼 순서와 같고,
# EG3 의 원값 보존·미측정 셀 NULL 검사가 이 목록을 통째로 돈다.
MEASURE_COLUMNS: tuple[str, ...] = (
    "whol_loan_new_stcn_shr", "whol_loan_rdmp_stcn_shr", "whol_loan_rmnd_stcn_shr",
    "whol_loan_new_amt", "whol_loan_rdmp_amt", "whol_loan_rmnd_amt",
    "whol_loan_rmnd_rate_pct", "whol_loan_gvrt_pct",
    "whol_stln_new_stcn_shr", "whol_stln_rdmp_stcn_shr", "whol_stln_rmnd_stcn_shr",
    "whol_stln_new_amt", "whol_stln_rdmp_amt", "whol_stln_rmnd_amt",
    "whol_stln_rmnd_rate_pct", "whol_stln_gvrt_pct")
# 잔고 축 — 음수 0(폐기형)·상장주식수 초과 0(폐기형)을 재는 대상. 신규·상환은 원천에 음수가 있어
# (병합·감자 정정) 폐기형이 아니라 기록형이다.
BALANCE_SHARE_COLUMNS: tuple[str, ...] = ("whol_loan_rmnd_stcn_shr", "whol_stln_rmnd_stcn_shr")
BALANCE_AMOUNT_COLUMNS: tuple[str, ...] = ("whol_loan_rmnd_amt", "whol_stln_rmnd_amt")
# 원천에 음수가 실재하는 축(기록형) — 절단본 6행(003540 2024-07-02·03 · 036220 2013-11-15·
# 2015-06-08 · 101970 2013-07-03 · 247540 2025-01-10)
SIGNED_COLUMNS: tuple[str, ...] = (
    "whol_loan_new_stcn_shr", "whol_loan_rdmp_stcn_shr", "whol_loan_gvrt_pct",
    "whol_stln_new_stcn_shr", "whol_stln_rdmp_stcn_shr", "whol_stln_gvrt_pct")

# `*_amt` 6컬럼의 단위 표식. DESIGN §1 basis 어휘의 'unknown' 을 그대로 쓴다 — 컬럼 전체가 단위
# 미상이므로 행마다 다르지 않은 상수 컬럼이고, 소비자·어댑터가 "금액을 원화로 읽지 말 것" 을
# 기계로 읽는 통로다.
AMT_BASIS = "unknown"

# 기록형 `ledger_gap_dates` 에 실을 날짜 표본 상한 — `_meta.json` 이 부풀지 않게 자른다(건수는
# `n_ledger_gap_dates` 가 전부 센다).
_GAP_DATE_SAMPLE = 20


def _row(ctx: EquityGateContext, sql: str) -> tuple[object, ...]:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row: table={ctx.rule.name} sql={sql[:200]}")
    return row


def _n(ctx: EquityGateContext, sql: str) -> int:
    return int(str(_row(ctx, sql)[0]))


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


# ── 판본 선택 (결정 6-2, 검수 종합 H3) ───────────────────────────────────────
# `stg_credit_daily` 는 `write_mode='append_only'` · `key_unique=False` 라(rules_kis.py) 같은
# (ticker, date) 에 값이 다른 판본이 공존한다 — 서버 실측 536군(2026년 505군 · 29종목, 예:
# 001290 2026-08-18 `close_krw` 969 vs 4,845). 원인은 KIS 가 `stck_prpr` 등 가격을 **조회 시점
# 수정주가로 소급 환산**해 주는 것이고, 기업행위 뒤 재수집하면 payload 가 달라져 stage 가 접지
# 못한다. 판본 선택 규칙이 없으면 equity 격자 조인이 팬아웃해 `gates.py` 격자 유일성(EG3-P01)에서
# 빌드가 폐기된다.
#
# **결정 6-2 = 최초 관측판**: 그날 시점에 볼 수 있던 값을 쓴다. 가격 조정은 `adj_factor`(S06) 몫
# 이고 KIS 가격 컬럼은 가격 축이 아니다(STAGE_SPEC §4 KIS). 최신판을 고르면 과거 격자 셀의 값이
# 뒤에 일어난 기업행위로 소급해 바뀌어 look-ahead 가 된다.
#
# 정렬 키는 `observed_date`(= stage 가 `collected_at` KST 를 날짜로 접은 축) 최소다. stage 산출에
# `collected_at` 원값 컬럼이 없으므로 **같은 날 두 판** 은 이 축으로 갈리지 않는다 — 재현성(EG5a)
# 을 위해 우리가 나르는 payload 전부를 2차 정렬 키로 세운다. 이 목록에서까지 동률이면 두 행은
# 우리가 읽는 모든 컬럼이 같아 어느 쪽을 골라도 산출이 같다.
VERSION_ORDER_COLUMNS: tuple[str, ...] = ("observed_date", "stlm_date", "close_krw",
                                          *MEASURE_COLUMNS)


def _version_order() -> str:
    sep = ",\n" + " " * 39
    return sep.join(f"c.{_q(c)} NULLS LAST" for c in VERSION_ORDER_COLUMNS)


# `sql/credit_daily.sql` 의 `ver` CTE 본문과 **글자 그대로** 같다
# (`test_판본_선택_술어는_rules_선언과_SQL_리터럴이_같다` 가 대조). 들여쓰기까지 맞춘 것은 SQL
# 쪽이 이 문자열을 그대로 품게 해 정의가 두 벌로 갈라지지 않게 하기 위해서다.
VERSION_RANKED_SQL = (
    "    SELECT c.*,\n"
    "           row_number() OVER (PARTITION BY c.ticker, c.date\n"
    f"                              ORDER BY {_version_order()}) AS version_rn\n"
    "    FROM stg_credit_daily c")


def first_version_sql() -> str:
    """(ticker, date) 당 **최초 관측판** 한 행. 게이트가 산출을 읽지 않고 입력에서 다시 고른다."""
    return f"SELECT * FROM (\n{VERSION_RANKED_SQL}\n) WHERE version_rn = 1"


def grid_predicate(alias: str = "") -> str:
    """`universe_daily` 위 격자 술어. `sql/credit_daily.sql`·EG1 우변·EG3 재계산이 같은 정의를
    쓰도록 한 곳에서 만든다(`test_격자_술어는_rules_선언과_SQL_리터럴이_같다` 가 대조)."""
    p = f"{alias}." if alias else ""
    return (f"{p}status IN ({_vocab_sql(GRID_STATUS)}) "
            f"AND {p}sec_type IS DISTINCT FROM '{GRID_EXCLUDED_SEC_TYPE}'")


def _result(name: str, checks: dict[str, int], metrics: dict[str, object],
            detail_ok: str) -> GateResult:
    """`checks` 는 전부 0 이어야 통과. 위반 항목명·건수를 detail 에 싣는다(rules_s01 규약)."""
    bad = {k: v for k, v in checks.items() if v}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult(name, GateStatus.PASS, detail_ok, merged)
    return GateResult(name, GateStatus.FAIL, "; ".join(f"{k}={v}" for k, v in sorted(bad.items())),
                      merged)


def _quantiles(ctx: EquityGateContext, expr: str, source: str) -> dict[str, float] | None:
    """분포를 기록형 metric 으로 남긴다 — 분모 0·전건 NULL 이면 None."""
    row = _row(ctx, f"SELECT quantile_cont(x, [0.05, 0.5, 0.95]), count(x) "
                    f"FROM (SELECT {expr} AS x FROM {source})")
    if row[0] is None or int(str(row[1])) == 0:
        return None
    p05, p50, p95 = (float(str(v)) for v in row[0])          # type: ignore[union-attr]
    return {"p05": p05, "p50": p50, "p95": p95, "n": int(str(row[1]))}


# ── EG1_credit_daily — GATES §3 ⑪ (b) 원장 보존 ──────────────────────────────

def eg1_credit_daily(ctx: EquityGateContext) -> GateResult:
    """⑪ (b): 원장 행은 전부 measured 셀이 되거나 격리된다. 잃어버린 행 0.

    프레임 EG1 은 등식 하나만 받으므로(⑪ (a) 격자 행수) 소스 보존 등식은 여기서 본다. 항은
    서로 다른 곳에서 온다 — 원장은 `_pinned/stg_credit_daily`, measured 는 산출 parquet,
    격리 건수는 `_reject/` 집계 — 라 항진명제가 아니다. 격리 사유 3종(`pre_calendar`·`off_grid`·
    **`balance_over_shares`**)을 `REJECT_REASONS` 에서 그대로 돌므로 사유가 늘면 자동으로 반영된다.
    """
    v = _q(ctx.out_view)
    n_src = _n(ctx, "SELECT count(*) FROM stg_credit_daily")
    n_measured = _n(ctx, f"SELECT count(*) FROM {v} WHERE fill_kind.kind = 'measured'")
    by_reason = {r: int(ctx.reject_by_reason.get(r, 0)) for r in REJECT_REASONS}
    n_rej = sum(by_reason.values())
    checks = {
        "n_ledger_delta": n_src - (n_measured + n_rej),
        # 선언 밖 사유로 격리된 행이 있으면 위 등식이 조용히 성립할 수 있다(프레임 EG3 와 이중 방어)
        "n_reject_outside_declared": ctx.n_reject - n_rej,
    }
    metrics: dict[str, object] = {
        "n_src_rows": n_src, "n_measured_cells": n_measured, "n_reject_by_reason": by_reason,
        "eg1b_sql": ("count(stg_credit_daily) = count(out WHERE fill_kind.kind='measured') "
                     "+ Σ reject(" + ", ".join(REJECT_REASONS) + ")")}
    return _result("EG1_credit_daily", checks, metrics, "원장 보존 등식 성립 (GATES §3 ⑪ (b))")


eg1_credit_daily.gate_name = "EG1_credit_daily"     # type: ignore[attr-defined]


# ── EG3_credit_daily ─────────────────────────────────────────────────────────

def _grid_sql() -> str:
    return f"SELECT date, ticker FROM universe_daily WHERE {grid_predicate()}"


def over_shares_predicate(alias: str, shares: str) -> str:
    """잔고주수가 그날 상장주식수를 넘는가. `sql/credit_daily.sql` `over` CTE 와 같은 정의를
    한 곳에서 만든다(`test_잔고_초과_술어는_rules_선언과_SQL_리터럴이_같다` 가 대조)."""
    return " OR ".join(f"{alias}.{c} > {shares}.shares_out" for c in BALANCE_SHARE_COLUMNS)


def _over_shares_sql() -> str:
    """격자 안이면서 잔고 이상인 **채택 판본 원장 행**의 키 — 입력(`stg_credit_daily`·
    `universe_daily`·`price_daily`)에서만 다시 센다. 산출을 읽지 않으므로 격리 건수 대조가
    항진명제가 아니다. 접힌 판본은 `version_folded` 로 이미 격리되므로 여기서 다시 세면
    사유가 겹쳐 `n_balance_over_shares_reject_delta` 가 거짓으로 어긋난다."""
    return (f"SELECT c.ticker, c.date FROM ({first_version_sql()}) c "
            f"JOIN ({_grid_sql()}) g ON g.ticker = c.ticker AND g.date = c.date "
            "JOIN price_daily p ON p.ticker = c.ticker AND p.date = c.date "
            f"WHERE p.shares_out IS NOT NULL AND ({over_shares_predicate('c', 'p')})")


def _version_group_sql() -> str:
    """(ticker, date) 당 판본 수 — 입력에서만 센다(`n_version_groups`·`n_versions_folded`)."""
    return ("SELECT ticker, date, count(*) AS n_versions "
            "FROM stg_credit_daily GROUP BY ticker, date")


def eg3_credit_daily(ctx: EquityGateContext) -> GateResult:
    """EG3 특화 — 어휘·격자·원값 보존·잔고 불변식·채움 규약. 산출식은 다시 계산하지 않는다.

    폐기형은 "산출이 스스로 만든 값" 이 아니라 **입력에서 독립으로 다시 세어 대조할 수 있는 축**
    에만 건다(§1 "게이트 술어를 산출식으로 재계산" 금지). 원천이 어긋난 사실(KIS 수정종가 ≠ 원주가,
    `*_amt` 단위 미상, 신규·상환 음수, 잔고비율 ≠ 잔고/상장주식수)은 이 테이블의 결함이 아니므로
    **기록**하고 서버 실측 뒤 baseline 승격을 판단한다.
    """
    v = _q(ctx.out_view)
    grid = _grid_sql()
    over_src = _over_shares_sql()
    over_out = over_shares_predicate("c", "p")

    # ① 어휘 폐쇄 + 키 폭
    n_kind_vocab, n_evidence_vocab, n_fill_null, n_amt_basis, n_ticker_bad = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v}
            WHERE fill_kind.kind IS NOT NULL
              AND fill_kind.kind NOT IN ({_vocab_sql(FILL_KINDS)})),
          (SELECT count(*) FROM {v}
            WHERE fill_kind.evidence IS NOT NULL
              AND fill_kind.evidence NOT IN ({_vocab_sql(FILL_EVIDENCE)})),
          (SELECT count(*) FROM {v}
            WHERE fill_kind IS NULL OR fill_kind.kind IS NULL OR fill_kind.evidence IS NULL),
          (SELECT count(*) FROM {v} WHERE amt_basis IS DISTINCT FROM '{AMT_BASIS}'),
          (SELECT count(*) FROM {v}
            WHERE ticker IS NULL OR typeof(ticker) <> 'VARCHAR'
               OR length(ticker) <> {TICKER_LEN})""")

    # ② 격자 양방향 — universe_daily 에서 격자를 다시 만들어 anti-join
    n_grid_missing, n_grid_extra, n_grid = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM ({grid}) g
            WHERE NOT EXISTS (SELECT 1 FROM {v} c
                               WHERE c.date = g.date AND c.ticker = g.ticker)),
          (SELECT count(*) FROM {v} c
            WHERE NOT EXISTS (SELECT 1 FROM ({grid}) g
                               WHERE g.date = c.date AND g.ticker = c.ticker)),
          (SELECT count(*) FROM ({grid}))""")

    # ③ measured 셀 ↔ **채택 판본** 원장 행 양방향 + 원값 보존(17축 전건 재조인).
    #    원장 쪽은 전부 `first_version_sql()` 로 다시 고른다 — 접힌 판본까지 세면 (ticker, date)
    #    조인이 팬아웃해 원값 대조가 거짓으로 어긋나고, 격리된 판본이 "measured 가 없는 원장 행"
    #    으로 잘못 잡힌다. 채택이 최초 관측판인지 자체는 이 재선택이 독립으로 확인한다.
    keep = ("stlm_date", *MEASURE_COLUMNS)
    diff = " OR ".join(f"c.{_q(k)} IS DISTINCT FROM s.{_q(k)}" for k in keep)
    picked = first_version_sql()
    n_measured_no_src, n_src_no_measured, n_value_mismatch = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} c WHERE c.fill_kind.kind = 'measured'
             AND NOT EXISTS (SELECT 1 FROM ({picked}) s
                              WHERE s.ticker = c.ticker AND s.date = c.date)),
          (SELECT count(*) FROM ({picked}) s
             WHERE EXISTS (SELECT 1 FROM ({grid}) g
                            WHERE g.ticker = s.ticker AND g.date = s.date)
               AND NOT EXISTS (SELECT 1 FROM ({over_src}) o
                                WHERE o.ticker = s.ticker AND o.date = s.date)
               AND NOT EXISTS (SELECT 1 FROM {v} c
                                WHERE c.ticker = s.ticker AND c.date = s.date
                                  AND c.fill_kind.kind = 'measured')),
          (SELECT count(*) FROM {v} c JOIN ({picked}) s USING (ticker, date)
            WHERE c.fill_kind.kind = 'measured' AND ({diff}))""")

    # ④ 채움 규약 — measured 아닌 셀은 전 축 NULL(0 을 굽지 않는다) · 결제일은 거래일 이후
    any_notnull = " OR ".join(f"{_q(c)} IS NOT NULL" for c in MEASURE_COLUMNS)
    n_unfilled_value, n_stlm_early = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v}
            WHERE fill_kind.kind <> 'measured'
              AND (({any_notnull}) OR stlm_date IS NOT NULL)),
          (SELECT count(*) FROM {v} WHERE stlm_date < date)""")

    # ⑤ 잔고 불변식 — 음수 0 · 상장주식수 초과 0 (같은 날 price_daily.shares_out 재조인) +
    #    잔고 이상 격리의 양쪽 대조: 입력에서 다시 센 위반 행수 = `_reject/balance_over_shares/`
    #    건수 · 그 키의 산출 셀은 남아 있고(격자 등식) measured 가 아니다.
    neg = " OR ".join(f"{_q(c)} < 0" for c in BALANCE_SHARE_COLUMNS + BALANCE_AMOUNT_COLUMNS)
    (n_balance_neg, n_over_shares, n_shares_null,
     n_over_src, n_over_cell_measured, n_over_cell_missing) = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM {v} WHERE {neg}),
          (SELECT count(*) FROM {v} c JOIN price_daily p USING (ticker, date)
            WHERE p.shares_out IS NOT NULL AND ({over_out})),
          (SELECT count(*) FROM {v} c JOIN price_daily p USING (ticker, date)
            WHERE p.shares_out IS NULL AND c.fill_kind.kind = 'measured'),
          (SELECT count(*) FROM ({over_src})),
          (SELECT count(*) FROM ({over_src}) o JOIN {v} c USING (ticker, date)
            WHERE c.fill_kind.kind = 'measured'),
          (SELECT count(*) FROM ({over_src}) o
            WHERE NOT EXISTS (SELECT 1 FROM {v} c
                               WHERE c.ticker = o.ticker AND c.date = o.date))""")

    # ⑥ 판본 선택 대조(결정 6-2) — 접힌 판본 수를 **입력에서** 다시 세어 격리 건수와 맞춘다.
    #    산출을 읽지 않으므로 항진명제가 아니다. 접힌 판본이 산출에 남아 있으면(= 채택되면)
    #    프레임 EG3 의 격자 유일성이 먼저 잡지만, 여기서도 사유별 건수로 한 번 더 본다.
    n_version_groups, n_versions_folded = _row(ctx, f"""
        SELECT
          (SELECT count(*) FROM ({_version_group_sql()}) WHERE n_versions > 1),
          (SELECT coalesce(sum(n_versions - 1), 0) FROM ({_version_group_sql()}))""")

    checks = {
        "n_fill_kind_outside_vocab": int(str(n_kind_vocab)),
        "n_fill_evidence_outside_vocab": int(str(n_evidence_vocab)),
        "n_fill_kind_null": int(str(n_fill_null)),
        "n_amt_basis_mismatch": int(str(n_amt_basis)),
        "n_ticker_bad_width": int(str(n_ticker_bad)),
        "n_grid_missing": int(str(n_grid_missing)),
        "n_grid_extra": int(str(n_grid_extra)),
        "n_measured_without_src_row": int(str(n_measured_no_src)),
        "n_src_row_without_measured": int(str(n_src_no_measured)),
        "n_measured_value_mismatch": int(str(n_value_mismatch)),
        "n_unmeasured_value_present": int(str(n_unfilled_value)),
        "n_stlm_date_before_date": int(str(n_stlm_early)),
        "n_balance_negative": int(str(n_balance_neg)),
        "n_balance_over_shares_out": int(str(n_over_shares)),
        "n_balance_over_shares_reject_delta":
            int(str(n_over_src)) - int(ctx.reject_by_reason.get("balance_over_shares", 0)),
        "n_balance_over_shares_cell_measured": int(str(n_over_cell_measured)),
        "n_balance_over_shares_cell_missing": int(str(n_over_cell_missing)),
        "n_version_folded_reject_delta":
            int(str(n_versions_folded)) - int(ctx.reject_by_reason.get("version_folded", 0)),
    }

    # ── 기록형 ──────────────────────────────────────────────────────────────
    kinds = {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f"SELECT fill_kind.kind, count(*) FROM {v} GROUP BY 1 ORDER BY 1").fetchall()}
    evidence = {f"{r[0]}/{r[1]}": int(str(r[2])) for r in ctx.con.execute(
        f"SELECT fill_kind.kind, fill_kind.evidence, count(*) FROM {v} "
        "GROUP BY 1, 2 ORDER BY 1, 2").fetchall()}
    # KIS 가 같이 준 수정종가와 원주가 정본의 차이 — 원천 사실이지 결함이 아니다
    n_close_join, n_close_ne, n_close_null = _row(ctx, f"""
        SELECT count(*), count(*) FILTER (WHERE s.close_krw IS DISTINCT FROM p."close"),
               count(*) FILTER (WHERE s.close_krw IS NULL)
        FROM {v} c
        JOIN ({picked}) s USING (ticker, date)
        JOIN price_daily p USING (ticker, date)
        WHERE c.fill_kind.kind = 'measured'""")
    # 원천 사실이라 **판본을 고르기 전 원장 전건**을 센다(접힌 판본의 음수도 원천에 실재한다).
    n_signed = {c: _n(ctx, f"SELECT count(*) FROM stg_credit_daily WHERE {_q(c)} < 0")
                for c in SIGNED_COLUMNS}
    # 원장 일괄 결측 의심일 — 격자에 종목이 있는데 그날 measured 셀이 하나도 없다. 0 채움을
    # 하지 않는 이유의 실측 근거다(절단본 2018-03-28 · 2026-08-19·20 = P16 백필 종료 뒤 이틀).
    gap_dates = [str(r[0]) for r in ctx.con.execute(f"""
        SELECT date FROM {v} GROUP BY date
        HAVING count(*) FILTER (WHERE fill_kind.kind = 'measured') = 0
           AND count(*) FILTER (WHERE fill_kind.kind = 'src_omitted') > 0
        ORDER BY date""").fetchall()]
    # net_buy 판정 근거 — 잔고 증감이 신규 − 상환과 맞는 연속 쌍의 비율(원장 위 독립 계산).
    # `lag()` 가 시계열을 보므로 **채택 판본만** 쓴다 — 접힌 판본이 섞이면 같은 날이 두 번 와서
    # 증감이 0 인 가짜 쌍이 생긴다.
    step = {}
    for side in ("loan", "stln"):
        pairs, match = _row(ctx, f"""
            WITH x AS (
              SELECT whol_{side}_rmnd_stcn_shr AS r, whol_{side}_new_stcn_shr AS n,
                     whol_{side}_rdmp_stcn_shr AS d,
                     lag(whol_{side}_rmnd_stcn_shr) OVER (PARTITION BY ticker ORDER BY date) AS pr
              FROM ({picked}))
            SELECT count(*) FILTER (WHERE pr IS NOT NULL),
                   count(*) FILTER (WHERE pr IS NOT NULL AND r - pr = n - d)
            FROM x""")
        step[side] = {"n_pairs": int(str(pairs)), "n_step_equals_new_minus_rdmp": int(str(match))}
    metrics: dict[str, object] = {
        "n_grid_cells": int(str(n_grid)),
        "fill_kind_counts": kinds,
        "fill_kind_evidence_counts": evidence,
        "fill_kind_vocab": list(FILL_KINDS),
        "fill_evidence_vocab": list(FILL_EVIDENCE),
        "n_pre_calendar": int(ctx.reject_by_reason.get("pre_calendar", 0)),
        "n_off_grid": int(ctx.reject_by_reason.get("off_grid", 0)),
        "n_balance_over_shares_rejected":
            int(ctx.reject_by_reason.get("balance_over_shares", 0)),
        "n_balance_over_shares_src": int(str(n_over_src)),
        # 판본 선택(결정 6-2) — 입력에서 다시 센 접힌 판본 수·판본이 여럿인 좌표 수와,
        # 실제로 `_reject/version_folded/` 로 간 건수. 셋이 어긋나면 위 checks 가 폐기한다.
        "n_versions_folded": int(str(n_versions_folded)),
        "n_version_groups": int(str(n_version_groups)),
        "n_version_folded_rejected": int(ctx.reject_by_reason.get("version_folded", 0)),
        "version_pick_rule": "min(observed_date) — 최초 관측판 (DECISIONS 결정 6-2)",
        "n_measured_evidence_none": int(evidence.get("measured/none", 0)),
        "n_shares_out_null_measured": int(str(n_shares_null)),
        "n_close_compared": int(str(n_close_join)),
        "n_close_ne_price_daily": int(str(n_close_ne)),
        "n_close_null_src": int(str(n_close_null)),
        "n_negative_by_column": n_signed,
        # `*_amt` 단위 추정 — 금액 / (잔고주수 × 원주가). 원화면 1 에 몰려야 한다
        "loan_amt_per_market_value": _quantiles(
            ctx, 'c.whol_loan_rmnd_amt / nullif(c.whol_loan_rmnd_stcn_shr * p."close", 0)',
            f'{v} c JOIN price_daily p USING (ticker, date) '
            "WHERE c.whol_loan_rmnd_stcn_shr > 0"),
        # 잔고비율 대 (잔고주수 / 상장주식수 × 100) — 1 근처면 KIS 비율의 분모가 상장주식수다.
        # **기준일 두 축을 나란히 잰다**(검수 종합 M3, 09-10 재검산): KIS 가 쓰는 분모는
        # deal_date 주식수가 아니라 **공표일(= 결제일 `stlm_date`, T+2) 시점 상장주식수**다 —
        # 같은 콜(09-09 16:33)에서 온 363260 09-03 행 0.40(병합 전 분모)과 09-04 행 0.61(병합 후
        # 분모)이 갈린 것이 근거다. 그래서 `_deal_date` 는 기업행위 구간에서 1 에서 벗어나고
        # `_stlm_date` 가 1 에 붙는다. 폐기형으로 묶지 않고 기록형으로 두는 이유는 이것이
        # 원천 규약이지 이 테이블의 결함이 아니기 때문이다(§1 "원천 사실은 기록").
        "loan_rate_vs_shares_ratio_deal_date": _quantiles(
            ctx, "c.whol_loan_rmnd_rate_pct / nullif(c.whol_loan_rmnd_stcn_shr * 100.0 "
                 "/ nullif(p.shares_out, 0), 0)",
            f"{v} c JOIN price_daily p USING (ticker, date) "
            "WHERE c.whol_loan_rmnd_stcn_shr > 0"),
        "loan_rate_vs_shares_ratio_stlm_date": _quantiles(
            ctx, "c.whol_loan_rmnd_rate_pct / nullif(c.whol_loan_rmnd_stcn_shr * 100.0 "
                 "/ nullif(p2.shares_out, 0), 0)",
            f"{v} c JOIN price_daily p2 ON p2.ticker = c.ticker AND p2.date = c.stlm_date "
            "WHERE c.whol_loan_rmnd_stcn_shr > 0"),
        "loan_rate_shares_basis": "stlm_date (공표일 T+2 상장주식수 — 검수 종합 M3)",
        "loan_balance_over_shares_ratio": _quantiles(
            ctx, "c.whol_loan_rmnd_stcn_shr / nullif(p.shares_out, 0)",
            f"{v} c JOIN price_daily p USING (ticker, date)"),
        "n_ledger_gap_dates": len(gap_dates),
        "ledger_gap_dates": gap_dates[:_GAP_DATE_SAMPLE],
        "loan_balance_step": step["loan"],
        "stln_balance_step": step["stln"],
        # FIELD_MAP §2 `credit.net_buy` 판정. 원천에 순매수 축이 없고 신규−상환은 잔고 증감과
        # 맞지 않는다(loan_balance_step) → 부재. 값이 바뀌면 FIELD_MAP 을 같이 고친다.
        "net_buy_axis": "absent",
    }
    return _result("EG3_credit_daily", checks, metrics,
                   "격자·어휘·원값 보존·잔고 불변식 성립")


eg3_credit_daily.gate_name = "EG3_credit_daily"     # type: ignore[attr-defined]


# ── S19 필드 선언 (DESIGN §4-7 · FIELD_MAP §2 `credit.*`) ────────────────────
# **랙 1 세션**. `stg_credit_daily` 가 stage `lag_known=false` 이고 KIS 신용잔고는 실제로 **T+1
# 공표**다 — 원장 날짜를 당일 지식으로 읽으면 look-ahead 다. STAGE_HANDOFF §2 「lag_known=false 는
# lag 0 을 적용하면 안 된다」 + FIELD_MAP §1 랙 단위 「나머지 전부 1 세션」. 이 테이블의
# `available_rule` 이 「공표 랙(T+1)은 팩트 행이 아니라 dataset_profile.recommended_lag_sessions」
# 라고 적어 둔 그 몫을 여기서 낸다.
# 선언하지 않는 것: `credit.net_buy`(원천에 축이 없다 — 위 docstring 판정) ·
# `credit.collateral_value`·`credit.loan_value`·`credit.forced_liquidation`(원천 없음) ·
# `*_amt` 6컬럼(단위 미상, `amt_basis='unknown'`) · 대주 잔고 `whol_stln_rmnd_stcn_shr`
# (FIELD_MAP §2 어휘에 field_id 가 없다).
_CAXIS: tuple[str, str] = ("ticker", "date")

FIELDS: tuple[FieldProfile, ...] = (
    FieldProfile(
        field_id="credit.margin_balance", columns=("whol_loan_rmnd_stcn_shr",),
        label="신용융자 잔고(주식수)", unit="주", value_type="count", frequency="session",
        recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=False,
        disclosure_basis="원장 날짜 = 잔고 기준일. KIS 신용잔고는 **T+1 공표**이고 공표 시각 "
                         "컬럼이 없다(stage lag_known=false) → 1 세션 뒤부터 쓴다",
        evidence="credit_daily.whol_loan_rmnd_stcn_shr ← stg_credit_daily(KIS 신용잔고) 무수정. "
                 "**주식수 축**이라 단위가 닫혀 있다 — 금액축 `*_amt` 6컬럼은 단위 미상이라"
                 "(`amt_basis='unknown'`, STAGE_HANDOFF §4) 이 field_id 로 나가지 않고, 대주 잔고 "
                 "`whol_stln_rmnd_stcn_shr` 는 별개 축이다. 격자 빈칸은 0 이 아니라 NULL 이고 "
                 "뜻은 fill_kind.kind 가 나른다(FIELD_MAP §1: src_omitted → "
                 "CellKind.SOURCE_OMITTED_ZERO) — 0 으로 읽을지는 소비자가 셀 종류를 "
                 "보고 정한다. 다만 **워크벤치 어댑터는 지금 그 종류를 MISSING 으로 "
                 "좁힌다**(도메인이 SOURCE_OMITTED_ZERO 셀에 값을 요구한다 — DESIGN §11 "
                 "⑪), 그래서 소비층에서는 두 결측이 구분되지 않는다. 잔고 > 그날 상장주식수인 원장 "
                 "행은 `_reject/balance_over_shares/` 로 격리되고 그 셀은 empty_response"
                 "(→ MISSING)로 남는다(DESIGN §9 결정 9) — 신용잔고비율 > 100% 셀이 소비층까지 "
                 "가지 않는다.",
        coverage_axis="grid_session", axis_columns=_CAXIS),
)


# ── 선언 ─────────────────────────────────────────────────────────────────────

CREDIT_DAILY = register(EquityTable(
    name="credit_daily",
    grain=("date", "ticker"),
    # 순서 = DESIGN §4-3: 키 → 결제일 → 융자 8 → 대주 8(가산 6 · 비율 2 씩) → 단위 표식 →
    # fill_kind → PIT 2. `*_amt` 는 단위 미상이라 `_krw` 접미사를 붙이지 않는다(FX-3-009).
    columns={"date": "DATE", "ticker": "VARCHAR", "stlm_date": "DATE",
             "whol_loan_new_stcn_shr": "DECIMAL(10,0)",
             "whol_loan_rdmp_stcn_shr": "DECIMAL(10,0)",
             "whol_loan_rmnd_stcn_shr": "DECIMAL(10,0)",
             "whol_loan_new_amt": "DECIMAL(11,0)",
             "whol_loan_rdmp_amt": "DECIMAL(11,0)",
             "whol_loan_rmnd_amt": "DECIMAL(11,0)",
             "whol_loan_rmnd_rate_pct": "DECIMAL(6,2)",
             "whol_loan_gvrt_pct": "DECIMAL(8,2)",
             "whol_stln_new_stcn_shr": "DECIMAL(10,0)",
             "whol_stln_rdmp_stcn_shr": "DECIMAL(8,0)",
             "whol_stln_rmnd_stcn_shr": "DECIMAL(9,0)",
             "whol_stln_new_amt": "DECIMAL(9,0)",
             "whol_stln_rdmp_amt": "DECIMAL(9,0)",
             "whol_stln_rmnd_amt": "DECIMAL(9,0)",
             "whol_stln_rmnd_rate_pct": "DECIMAL(5,2)",
             "whol_stln_gvrt_pct": "DECIMAL(7,2)",
             "amt_basis": "VARCHAR",
             "fill_kind": "STRUCT(kind VARCHAR, evidence VARCHAR)",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    # `price_daily` 는 산출에 안 쓴다 — EG3_credit_daily 의 상장주식수·원주가 대조축이다
    # (rules_s03 이 `adj_factor.factor_ok` 를 기록형으로만 읽는 것과 같은 규약).
    inputs=("trading_calendar", "universe_daily", "price_daily", "stg_credit_daily",
            "stg_units_kis"),
    partition_class="date_axis",
    partition_key_expr="year(date)",
    build_by_year=False,
    available_rule=("column:date — KIS 신용잔고 일별 스냅샷(stage 가 date·default 로 확정). "
                    "공표 랙(T+1)은 팩트 행이 아니라 dataset_profile.recommended_lag_sessions"),
    # GATES §3 ⑪ (a): 좌변 = 격자 행수. 우변은 격자 + 격자 밖 **채택 판본** 원장 행(= 격리 후보)
    # + 잔고 이상 + 접힌 판본이고 프레임이 n_reject 를 빼므로 두 축이 한 등식에서 닫힌다.
    # 격자 밖 항이 채택 판본만 세는 것이 중요하다 — 원장 전건으로 세면 접힌 판본이 두 항에 겹쳐
    # 잡혀 우변이 부풀고 등식이 깨진다. (b) 원장 보존은 EG1_credit_daily.
    eg1_lhs_sql="SELECT count(*) FROM out_pq",
    eg1_rhs_sql=(f"SELECT (SELECT count(*) FROM universe_daily WHERE {grid_predicate()}) "
                 f"+ (SELECT count(*) FROM ({first_version_sql()}) c WHERE NOT EXISTS ("
                 "SELECT 1 FROM universe_daily u WHERE u.ticker = c.ticker AND u.date = c.date "
                 f"AND {grid_predicate('u')})) "
                 f"+ (SELECT count(*) FROM ({_over_shares_sql()})) "
                 f"+ (SELECT coalesce(sum(n_versions - 1), 0) FROM ({_version_group_sql()}))"),
    sql_path=SQL_DIR / "credit_daily.sql",
    input_columns={
        "trading_calendar": ("date",),
        "universe_daily": ("date", "ticker", "status", "sec_type"),
        "price_daily": ("ticker", "date", "close", "shares_out"),
        # `observed_date` 는 판본 선택 축이다(결정 6-2) — 값으로 나르지 않고 고르는 데만 쓴다.
        "stg_credit_daily": ("ticker", "date", "stlm_date", "close_krw", *MEASURE_COLUMNS,
                             "observed_date"),
        "stg_units_kis": ("dataset", "ticker", "status", "window_from", "window_to")},
    available_basis=("default",),
    content_date_column="date",
    reject_reasons=REJECT_REASONS,
    extra_gates=(eg1_credit_daily, eg3_credit_daily),
    field_profiles=FIELDS,
))

TABLES: tuple[EquityTable, ...] = (CREDIT_DAILY,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s10.json"
"""이 슬라이스가 요구하는 baseline 상수 — 격리 비율 임계 하나. 승인 뒤 baseline.json 에 병합."""

__all__ = ["BALANCE_SHARE_COLUMNS", "BASELINE_SEED", "CREDIT_DAILY", "FIELDS",
           "MEASURE_COLUMNS", "REJECTED_CELL_KIND", "REJECT_REASONS", "TABLES", "UNIT_DATASET",
           "VERSION_ORDER_COLUMNS", "VERSION_RANKED_SQL", "first_version_sql", "grid_predicate",
           "over_shares_predicate"]
