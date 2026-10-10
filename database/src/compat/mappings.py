"""v3 `quant.db` 9표 ← equity/stage/model 매핑 선언 (플랜 `2026-09-24-v3-merge.md` §5 T1.2 2).

표마다 ① 원천(equity 판 · stage 판 · 모델 판 점수 표) ② duckdb SELECT ③ v3 컬럼 순서 ④ PK
⑤ `retire_when`(만료 조건) ⑥ `null_columns`(소스에 재료가 없어 NULL 로 두는 v3 열)을 선언한다.
SQL 은 `str.format` 자리를 쓴다 — 원천 표 실명 자리에는 `read_parquet([...])` 가, 나머지
`{date}`·`{from_date}`·`{snap_from}`·`{consensus_asof}`·`{asof_ym}`·`{exported_at}` 자리에는
`quant_db.export` 가 검증한 스칼라가 들어간다. **정규식에 `{n}` 수량자를 쓰지 않는다**
(`str.format` 이 자리로 읽는다 — `\\d\\d\\d\\d` 로 풀어 쓴다).

단위 환산 상수는 전부 `units.py` 가 정본이다(§1-4 GAP-3). 여기서는 그 상수를 SQL 에 박는다.

공통 규약
  · 날짜는 `CAST(… AS VARCHAR)` 로 'YYYY-MM-DD' 문자열을 만든다 — sqlite3 는 `date` 를
    못 바인딩한다.
  · 숫자는 전부 명시 CAST(BIGINT/DOUBLE) 한다 — duckdb DECIMAL 은 sqlite3 가 못 바인딩한다.
  · 결측은 결측이다. 소스에 없는 v3 열은 NULL 로 두고 `null_columns` 에 적는다(0 채움 금지).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from model.contracts import V2_SCORE_COLUMNS, V3_SCORE_COLUMNS

from . import units
from .units import KRW_PER_EOK, KRW_PER_MN

EQUITY = "equity"
STAGE = "stage"
MODEL = "model"                      # 모델 판 점수 표(QL-C) — 판 고정은 `quant_db._resolve_model`


@dataclass(frozen=True)
class TableMapping:
    """v3 표 1개를 채우는 선언."""

    v3_table: str
    source_kind: str | None          # 'equity' | 'stage' | 'model' | None(안 채움)
    sources: tuple[str, ...]         # 원천 표 실명(source_kind 루트 기준). model 은 spec_id 하나
    columns: tuple[str, ...]         # v3 컬럼 순서 = SELECT 출력 순서
    pk: tuple[str, ...]
    sql: str
    retire_when: str
    null_columns: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""
    # 다른 루트의 원천 — (kind, 표). `financial_summary` 가 stage WISE 와 equity DART 를
    # 함께 읽는다(T1.5 · D-10). SQL 자리 이름은 `sources` 와 같은 규칙이다.
    cross_sources: tuple[tuple[str, str], ...] = ()


# ── v3 `stocks` 집합(D-11) — `stocks`·`daily_prices`·`investor_detail_flows` 가 같이 쓰는 한 곳 ──
# v3 `stocks` 는 KOSPI·KOSDAQ 의 보통주·스팩이다(아래 `stocks` 주석의 09-23 실측). v3 는 이 목록의
# 종목만 가격·수급을 모으므로 두 표도 같은 집합이어야 한다(QL-A — 09-28 그림자에서 compat
# `daily_prices` 3,817행 중 `stocks` 에 있는 것은 2,490행뿐이었다. ETF·우선주 등이 위키 동일가중
# 수익률과 가설 입력에 섞였다). 판정은 같은 날 `universe_daily` 행(별칭 `u`)의 종목 유형·시장이다.
# 시장 어휘는 v3 `stocks.market` CHECK 제약과 같다.
V3_STOCK_FILTER = "u.sec_type IN ('common', 'spac') AND u.market IN ('KOSPI', 'KOSDAQ')"


# ── daily_prices ────────────────────────────────────────────────────────────────────────────
# equity `price_daily`(OHLCV·거래대금·KRX 기준가)를 v3 가 쌓던 모양으로 옮긴다(QL-E · T-18 · T-40 · T-41).
# v3 는 키움 ka10081 수정주가(`upd_stkpc_tp=1`)를 매일 그날 기준일로 받는다. 그 수정주가는 **KRX 기준가 사슬 K** 다
#   (T-40 — 로컬 v3 08-07 사본 932,265행 중 1원·0.15% 밖 0행):
#     ks(e) = 전일 종가 ÷ 기준가   (그날 기준가 ≠ 전일 종가일 때, 아니면 1 — 분할·병합·증자·정지 해제 재평가 모두)
#     K(d)  = Π_{e ≤ d} ks(e)      (그 종목 krx 행 순서. 비율만 쓰므로 시작점은 상관없다)
#   ① adj_close(d) = 종가(d) × K(d) ÷ K(L) — L = 그 종목의 마지막 행(반영 기준일). 최신 행 = 원종가이고 과거 행은
#      그 뒤 사건으로 소급 조정된다. v3 는 사건이 나면 그 종목 전 기간 adj_close 를 다시 쓴다 — 20:05 시세 수집이
#      최근 5행을 adj_close NULL 로 덮고 adj_prices 가 NULL 이 있는 종목을 다시 받아 전 행 UPDATE(v3
#      `scripts/_backfill_mode_helpers.py` run_adj_price_backfill). compat 은 창 안 사건 종목의 창 밖 행도 다시
#      맞춘다(`REBASE_SQL`).
#   ② 시·고·저·종가(d) = 원값 × K(d) ÷ K(c),  거래량(d) = 원값 × K(c) ÷ K(d),  c = min(d 뒤 4번째 행, L)
#      (T-41) — v3 시세 수집은 매일 ka10081 최근 5행(그날 + 앞 4행)을 그날 기준 수정값으로 덮는다(v3
#      `backend/pipeline/collectors.py` collect_prices `items[:5]`). 그래서 행 d 는 마지막으로 덮인 날 c 기준값이다.
#      사건이 없으면 K(c) = K(d) 라 원값이다. 07:00 브리핑이 `(d.close − p.close) / p.close` 로 등락률을 세므로
#      사건 다음 날 가짜 급등락(011930 병합 +900% 등)이 안 뜨게 하는 기존 동작이다.
#      거래대금(amount)은 조정하지 않는다(로컬 사본: 덮인 행 2,129 중 원값 일치 82% · 조정값 45%).
#   K 는 v3 외부 계약의 정의라 equity `price_adj_daily` 계수(g — 기준가 무변화 날 접기·정지 해제 재평가 없음·호가
#   반올림 차이)를 쓰지 않는다. equity·fi·모델의 전방 조정은 그대로다(T-3).
#   계수비를 먼저 계산하므로 사건 없는 행은 정확히 원값이다(1.0 곱). 가격·거래량은 v3 열 타입(INTEGER)대로 반올림한다.
# 장 마감 판(`--basis evening`)은 equity 판이 D' 까지라 사슬 끝에 원장 T 단계를 붙인다(`NO_T_STEP` 주석 · `compat.t_rows`).
# 창 밖 행: 위 ② 의 c 는 d 뒤 4행 안이라 창(증분 11세션 ≥ 5세션 — 제자리 반영은 `MIN_WINDOW_SESSIONS`
#   가드) 안에서 끝난다 — 행이 창을 떠날 때는 이미 최종값이다. 그래도 창 안에 단계가 든 종목은 창 밖 행의 ① adj_close 와
#   ② 시·고·저·종가·거래량을 함께 다시 쓴다(`REBASE_SQL` — 반영이 며칠 끊겨도 자가 복구). 사건 단계까지 창 밖으로 나갈
#   만큼 끊겼거나 equity `price_daily` 원값(종가·기준가)이 바뀌면 `--full` 로 맞춘다.
# v3 사본에는 이 규칙 밖의 옛 행도 있다(과거 일괄 백필 — 행 d 가 d+4 보다 뒤 날짜 기준 수정값, 08-07 사본
#   34,310행·217종목). compat 은 자기가 쓰는 범위(창 · 창 안 사건 종목의 창 밖 행)만 규칙대로 다시 쓴다 — V3-C 가
#   증분 창이라(T-46) 그 밖의 옛 행은 v3 이력에 그대로 남는다(COMPAT_LAYER §7 ②).
# `price_daily.basis`('krx'|'evening')는 v3 스키마에 자리가 없다 — `_compat_meta.basis` 에만 남는다.
# ⚠ GAP-1: v3 `daily_prices` 는 open·high·low·close·volume 이 **NOT NULL** 이고(v3
#   `backend/db/schema.py:16-27`, 완화 ALTER 없음) 우리 저녁 잠정 T 행(`basis='evening'`)은
#   KRX 기본정보가 없어 open/high/low/value_krw 가 NULL 이다. 그대로 넣으면 표 트랜잭션이
#   통째로 깨지므로 **이 SELECT 는 `basis='krx'` 행만 내보낸다**. 건너뛴 저녁 행 수는
#   `_compat_meta.n_evening_rows_skipped` 에 남는다. `--basis evening` 의 T 행은 equity 판이 아니라
#   원장에서 따로 만든다(아래 '장 마감 판 T 행' 절 — QL-D).
#   M1 의 G-M2 비교는 확정판(morning, 전 행 krx)만 쓰므로 영향이 없다.
# 거래정지일 참고가 행(`price_kind='reference'`, 거래량 0): KRX 가 O/H/L 을 '0' 으로 주고 stage 가
#   NULL 로 둔다. v3 는 그날을 open=high=low=close=참고가 · volume 0 · amount 0 으로 싣는다(로컬 v3
#   사본 2026-07~08 정지 행 전부 같은 모양). v3 NOT NULL 에 걸려 조용히 빠지던 행(QL-A2 — 10-01~08
#   재생에서 하루 102~104행)이라 **그 행의 비어 있는 O/H/L 만** 종가로 채운다. v3 외부 계약 때문의
#   채움이고 equity·모델 입력으로는 돌아가지 않는다(원칙 ④ 는 equity 층 규칙).
# 정지 행만이 아니다(QL-F): 정규장 체결 없이 시간외 체결만 있던 날도 KRX 가 O/H/L 을 공란으로 준다
#   (price_kind='trade'). 서버 `v3_post --full`(730일) 게이트 실측 — 145210 2025-03-21 close 1,126 ·
#   거래량 1,015 · O/H/L 공란 1행이 v3 NOT NULL 에 걸려 건너뛰어졌고, v3 10-08 사본의 같은 행은
#   open=high=low=close=1126 이다. 그래서 **O/H/L 이 비고 종가가 있는 모든 krx 행**의 빈 칸을 종가로
#   채운다(v3 와 같은 모양). 종가가 없으면 채우지 않는다(그 행은 NOT NULL 로 빠지고 게이트가 잡는다).
_REF_FILL = "p.close"
# v3 시세 수집이 매일 덮는 최근 행 수 − 1(그날 + 앞 4행 — v3 collectors.py `items[:5]`)
V3_OVERWRITE_ROWS = 4


def ks_sql(prev: str, base: str) -> str:
    """KRX 기준가 단계(T-40) — 전일 종가 ÷ 기준가(둘 다 양수이고 서로 다를 때), 아니면 1. 사슬(`_k_chain`)과 장 마감 판
    T 단계(`compat.t_rows.STEP_SQL`)가 같이 쓴다(P4). 인자는 SQL 식이다."""
    return (f"CASE WHEN {prev} > 0 AND {base} > 0 AND {base} <> {prev} "
            f"THEN CAST({prev} AS DOUBLE) / CAST({base} AS DOUBLE) ELSE 1.0 END")


def _k_chain(start: str) -> str:
    """K 사슬 CTE(`chain`) — 종목의 krx 행 [start, D] 위 기준가 단계 ks · 누적 k_d · d 뒤 4번째 행의 k_lead ·
    마지막 행의 k_last. `start` 는 SQL 날짜 자리 이름이다(`from_date` · `rebase_floor`). 단계는 유니버스로 거르기
    전의 krx 행 순서로 센다(키움 일봉과 같은 행 — 정지 참고가 행 포함). 구간 첫 행의 단계는 1 이다(전일 행이
    구간 밖) — 비율 K(x) ÷ K(d)(x ≥ d)는 d 뒤 단계만 쓰므로 값에 영향이 없다."""
    return f"""
kraw AS (
    SELECT q.*, lag(q.close) OVER (PARTITION BY q.ticker ORDER BY q.date) AS prev_close
    FROM {{price_daily}} q
    WHERE q.basis = 'krx' AND q.date >= DATE '{{{start}}}' AND q.date <= DATE '{{date}}'
),
kstep AS (
    SELECT r.*, {ks_sql("r.prev_close", "r.base_price_krw")} AS ks
    FROM kraw r
),
kcum AS (
    SELECT s.*, product(s.ks) OVER (PARTITION BY s.ticker ORDER BY s.date
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS k_d
    FROM kstep s
),
chain AS (
    SELECT k.*,
           lead(k.k_d, {V3_OVERWRITE_ROWS}) OVER (PARTITION BY k.ticker ORDER BY k.date) AS k_lead,
           last_value(k.k_d) OVER (PARTITION BY k.ticker ORDER BY k.date
                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                            AND UNBOUNDED FOLLOWING) AS k_last
    FROM kcum k
)"""


# 제자리 반영 창의 최소 세션 수(MINOR-1) — 행이 창을 떠나기 전에 덮어쓰기 기준일 c(d 뒤 4번째 행)가 창 안에 들어와야
# 그 행이 최종값으로 남는다. 판정은 `quant_db._guard_window_sessions`(daily.calendar).
MIN_WINDOW_SESSIONS = V3_OVERWRITE_ROWS + 1
# 장 마감 판 T 단계(MAJOR-A · T-41 보완) 자리 — 종목별 `ks_t`(원장 T 행의 KRX 기준가 단계: D' 종가 ÷ (종가_T −
# 전일대비_T), 기준가 = D' 종가면 1, 모르면 NULL). 장 마감 판이면 `compat.t_rows.STEP_TABLE`, 아니면 이 빈 관계다.
# 사슬이 D' 에서 끝나므로 T 를 붙이면 K(L) = K(D') × ks_t, d 뒤 4번째 행이 T 인 행(D'−3..D')의 K(c) 도 같은 값이다.
# 모르는 종목(전일대비 없음 등)은 1 로 둔다 — 창 행은 D' 기준이고 T 행 adj_close 는 NULL(`t_rows`).
NO_T_STEP = "(SELECT CAST(NULL AS VARCHAR) AS ticker, CAST(NULL AS DOUBLE) AS ks_t WHERE FALSE)"
# 행별 배수 — f_px = K(d) ÷ K(c)(가격, 거래량은 역수), f_adj = K(d) ÷ K(L)(adj_close)
_V3ROW = """
v3row AS (
    SELECT c.*,
           c.k_d / coalesce(c.k_lead, c.k_last * coalesce(ts.ks_t, 1.0)) AS f_px,
           c.k_d / (c.k_last * coalesce(ts.ks_t, 1.0))                    AS f_adj
    FROM chain c
    LEFT JOIN {t_step} ts ON ts.ticker = c.ticker
)"""
# T-41 가격·거래량 — 창 행 SELECT 와 창 밖 자가 복구(REBASE_SQL)가 같이 쓴다
_V3_OHLCV = f"""
    CAST(round(coalesce(p.open, {_REF_FILL}) * p.f_px) AS BIGINT)    AS open,
    CAST(round(coalesce(p.high, {_REF_FILL}) * p.f_px) AS BIGINT)    AS high,
    CAST(round(coalesce(p.low, {_REF_FILL}) * p.f_px) AS BIGINT)     AS low,
    CAST(round(p.close * p.f_px) AS BIGINT)                          AS close,
    CAST(round(p.volume_shr / p.f_px) AS BIGINT)                     AS volume,"""
_ADJ = "CAST(CAST(p.close AS DOUBLE) * p.f_adj AS DOUBLE)"
_DAILY_PRICES_SQL = f"""
WITH {_k_chain('from_date')},{_V3ROW}
SELECT
    p.ticker                                                         AS stock_code,
    CAST(p.date AS VARCHAR)                                          AS trade_date,{_V3_OHLCV}
    CAST(round(p.value_krw / {KRW_PER_MN}.0) AS BIGINT)              AS amount,
    {_ADJ}                AS adj_close
FROM v3row p
JOIN {{universe_daily}} u ON u.ticker = p.ticker AND u.date = p.date
WHERE {V3_STOCK_FILTER}
"""

# ── 창 밖 다시 맞춤·자가 복구(QL-E · T-40 · T-41) ────────────────────────────────────────────
# adj_close 는 창 안 마지막 행 L 에 묶여 있어 창 안에 사건 단계(ks ≠ 1, 장 마감 판이면 T 단계 포함)가 들면 대상 파일의
#   창 밖 옛 행이 옛 기준으로 남아 창 경계에서 끊긴다. v3 는 사건 뒤 종목 전 기간 adj_close 를 다시 쓰므로 같은 결과가
#   되게 **그 종목만** 창 밖 행을 다시 쓴다(`quant_db._rebase_outside` — 대상에 이미 있는 `trade_date < from_date`
#   행만, 거래대금·행 수는 그대로). adj_close 와 함께 시·고·저·종가·거래량도 T-41 값으로 다시 쓴다(MINOR-1) — 반영이
#   며칠 끊겨 사건 직전 행이 창 밖으로 밀려도 사건이 창 안에 있는 동안 다음 반영이 그 행을 바로잡는다. 값은 equity
#   원값에서 계산한다(대상 close 는 덮어쓰기·옛 백필 값일 수 있다).
# 대상 = 창 안 행이 v3 종목 집합(`V3_STOCK_FILTER`)에 드는 종목 중 창 안에 단계가 든 종목.
# 출력: 대상 종목마다 (종목, 날짜, 시, 고, 저, 종, 거래량, adj_close) — 날짜는 [rebase_floor, from_date) 의 equity krx 행
#   중 종가·거래량이 있는 행. 그런 행이 없는 종목도 (종목, NULL…) 로 나온다(목록에는 든다). 사슬을 rebase_floor(대상
#   `daily_prices` 의 가장 이른 날)부터 이으므로 창 첫 행의 단계도 실제 전일 종가로 센다.
REBASE_SQL = f"""
WITH {_k_chain('rebase_floor')},{_V3ROW},
win AS (
    SELECT c.ticker, bool_or(c.ks <> 1.0) AS stepped,
           coalesce(bool_or(u.ticker IS NOT NULL AND {V3_STOCK_FILTER}), FALSE) AS in_v3
    FROM chain c
    LEFT JOIN {{universe_daily}} u ON u.ticker = c.ticker AND u.date = c.date
    WHERE c.date >= DATE '{{from_date}}'
    GROUP BY c.ticker
),
moved AS (
    SELECT w.ticker
    FROM win w
    LEFT JOIN {{t_step}} ts ON ts.ticker = w.ticker
    WHERE w.in_v3 AND (w.stepped OR coalesce(ts.ks_t, 1.0) <> 1.0)
)
SELECT m.ticker,
       CAST(p.date AS VARCHAR)                                          AS trade_date,{_V3_OHLCV}
       {_ADJ} AS adj_close
FROM moved m
LEFT JOIN v3row p ON p.ticker = m.ticker AND p.date < DATE '{{from_date}}'
                 AND p.close IS NOT NULL AND p.volume_shr IS NOT NULL
ORDER BY 1, 2
"""

# 같은 창에서 `basis='evening'` 이라 제외한 행 수 — `_compat_meta.n_evening_rows_skipped`.
# D-8 결정 전까지 "저녁 T 행이 v3 표에 없다" 는 사실을 숫자로 남긴다(조용한 실패 금지).
EVENING_SKIPPED_SQL = """
SELECT count(*) FROM {price_daily} p
WHERE p.basis = 'evening'
  AND p.date >= DATE '{from_date}' AND p.date <= DATE '{date}'
"""

# ── stocks ──────────────────────────────────────────────────────────────────────────────────
# 종목당 1행 스냅샷. 유니버스는 `universe_daily` 의 as_of 이하 최신 세션 행
# (status listed·suspended). 시총은 같은 창의 `price_daily.mktcap_krw` 최신 값 → 억원.
# 이름·상장일·폐지일은 `security`.
# **D-11 유니버스 미러**: v3 `stocks` 는 KOSPI·KOSDAQ 의 `sec_type IN ('common', 'spac')` 다.
#   09-23 그림자 실측 — v3 active 2,533 = common 2,413 + spac 117. 우리가 더 넣었던 236 은
#   preferred 114 · reit 23 · foreign 12 · dr 10 · fund 3 이고 v3 수집기(키움 ka10099 + KIS MST)가
#   애초에 담지 않는 종류다. 유니버스를 v3 와 같게 맞춰야 G-M2 ①의 티커 집합 차이가 선다.
# `sector` 는 v3 와 같은 KRX 업종명이다(QL-B · T-19 — U24 의 WICS L1 을 대체). v3 는 키움 ka10099
#   `upName` 을 그대로 넣는다(v3 `clients/kiwoom/client.py` get_stock_list →
#   `pipeline/daily_pipeline.py` _fetch_kiwoom_stocks, 공란은 `strip() or None`). 같은 원천이 stage
#   `stg_master_daily.up_name`(06:00 마스터 스냅샷, 2026-09-01~ 누적)이고 equity 에는 이 열이 없어
#   stage 를 직독한다. as-of D 이하 최신 스냅샷 한 행 — 공란이면 NULL(v3 와 같다), 행이 없으면 NULL.
# `updated_at` 은 v3 가 `datetime('now')` 로 채우던 자리 — 우리는 export 시각을 넣어
# 신선도를 남긴다.
_STOCKS_SQL = f"""
WITH uni AS (
    SELECT u.ticker, u.market, u.sec_type,
           row_number() OVER (PARTITION BY u.ticker ORDER BY u.date DESC) AS rn
    FROM {{universe_daily}} u
    WHERE u.status IN ('listed', 'suspended')
      AND u.date >= DATE '{{snap_from}}' AND u.date <= DATE '{{date}}'
),
cap AS (
    SELECT p.ticker, p.mktcap_krw,
           row_number() OVER (PARTITION BY p.ticker ORDER BY p.date DESC) AS rn
    FROM {{price_daily}} p
    WHERE p.mktcap_krw IS NOT NULL
      AND p.date >= DATE '{{snap_from}}' AND p.date <= DATE '{{date}}'
),
sect AS (
    SELECT m.ticker, nullif(m.up_name, '') AS up_name,
           row_number() OVER (PARTITION BY m.ticker ORDER BY m.date DESC) AS rn
    FROM {{stg_master_daily}} m
    WHERE m.date <= DATE '{{date}}'
)
SELECT
    u.ticker                                              AS stock_code,
    coalesce(v.name_abbrv_current, v.name_current)        AS stock_name,   -- v3 도 약명
    u.market                                              AS market,
    sect.up_name                                          AS sector,
    CAST(round(cap.mktcap_krw / {KRW_PER_EOK}.0) AS BIGINT)
                                                          AS market_cap,
    CAST(v.list_date AS VARCHAR)                          AS listed_date,
    CASE WHEN v.delist_date IS NULL OR v.delist_date > DATE '{{date}}' THEN 1 ELSE 0 END
                                                          AS is_active,
    CAST(v.delist_date AS VARCHAR)                        AS delisted_date,
    '{{exported_at}}'                                     AS updated_at
FROM uni u
JOIN {{security}} v ON v.ticker = u.ticker
LEFT JOIN cap  ON cap.ticker = u.ticker AND cap.rn = 1
LEFT JOIN sect ON sect.ticker = u.ticker AND sect.rn = 1
WHERE u.rn = 1
  AND {V3_STOCK_FILTER}
"""

# ── investor_detail_flows ───────────────────────────────────────────────────────────────────
# equity `flow_daily` 13주체 → v3 12열(원 → 백만원). `natfor_krw`(내외국인)는 v3 에 자리가 없어
# 버린다. `orgn_krw`(기관계)는 기관 7주체 합과 **다르다**(FIELD_MAP GAP-03) — v3
# `institution_total` 도 같은 성격의 합계 컬럼이라 그대로 나른다. 재계산하지 않는다.
# `flow_daily` grain 은 (date, ticker, src) 라 셀 하나에 두 원천 행이 올 수 있다(실측 겹침 0).
# v3 PK 는 (stock_code, trade_date) 하나뿐이므로 키움 우선으로 **결정적으로** 하나를 고른다.
# 미측정 셀(전 주체 NULL)은 v3 에 행을 만들지 않는다 — v3 는 수집한 행만 가진다.
# ⚠ 저녁 판에는 T 행이 아예 없다 — `flow_daily` 격자가 T-1 까지라 T 원장 행이 `off_grid` 로
#   격리된다. `--basis evening` 의 T 행은 원장에서 따로 만든다(아래 '장 마감 판 T 행' 절 — QL-D).
_FLOW_COLS = units.FLOW_SUBJECTS        # 주체 대응의 정본은 units.py 다(중복 선언 금지)
_FLOW_SELECT = ",\n       ".join(
    f"CAST(round(f.{src} / {KRW_PER_MN}.0) AS BIGINT) AS {dst}" for dst, src in _FLOW_COLS)
_FLOW_ANY = ", ".join(f"f.{src}" for _, src in _FLOW_COLS)
_INVESTOR_FLOWS_SQL = f"""
WITH picked AS (
    SELECT f.*, row_number() OVER (
               PARTITION BY f.ticker, f.date
               ORDER BY CASE WHEN f.src = 'kiwoom' THEN 0 ELSE 1 END, f.src) AS rn
    FROM {{flow_daily}} f
    JOIN {{universe_daily}} u ON u.ticker = f.ticker AND u.date = f.date
    WHERE f.date >= DATE '{{from_date}}' AND f.date <= DATE '{{date}}'
      AND coalesce({_FLOW_ANY}) IS NOT NULL
      AND {V3_STOCK_FILTER}
)
SELECT f.ticker                   AS stock_code,
       CAST(f.date AS VARCHAR)    AS trade_date,
       {_FLOW_SELECT}
FROM picked f
WHERE f.rn = 1
"""

# ── 장 마감 판 T 행 (QL-D · N-42 Q3 · T-2) ──────────────────────────────────────────────────
# `--basis evening` 의 위 두 표 T 행은 equity 판이 아니라 원장에서 만든다. 원천·대상·채움 규칙과
# SQL 의 정본은 `compat.t_rows` 다 — 원장 파싱에 stage 규칙 객체를 쓰므로 이 모듈과 떼어 둔다
# (장 마감 수집기 `daily.postclose` 가 이 모듈의 `V3_STOCK_FILTER` 만 읽는다 — 16:00 창이라
# import 를 가볍게).
# 아래는 날짜 단위로 갈아 끼우는 표와 그 날짜 열이다(QL-C 와 같은 규칙 — `quant_db._replace_date`).
# 저녁에 원장으로 만든 T 행은 다음 날 아침 `--basis morning --date T` 가 KRX 행으로 통째로 바꾼다.
DATE_REPLACED: dict[str, str] = {"daily_prices": "trade_date",
                                 "investor_detail_flows": "trade_date"}

# ── 컨센서스 리비전 (한시 예외) ──────────────────────────────────────────────────────────────
# equity `consensus_daily` 에 영업이익·순이익이 없어(B-23 · 플랜 §1-4 GAP-6) stage
# `stg_consensus_matrix` 를 **직독**한다. equity 층을 건너뛰는 유일한 매핑이다.
#   만료: M2 T2.6 이 equity `consensus_revision`(S17b)을 올리면 그 표로 바꾸고 이 예외를 없앤다.
# 좌표: (ticker, fetched_date, target_period, acc_cd, lookback_idx).
# acc_cd 9종(STAGE_DESIGN §4 WISE 실측 09-02):
#   610100 투자의견 · 121000 매출액 · 121500 영업이익 · 122710 순이익 · 312000 EPS ·
#   382000 PER · 314000 BPS · 382400 PBR · 211500 ROE
# lookback: VAL1=current · VAL2=1w · VAL3=1m · VAL4=3m · VAL5=1y (`parsers.py:268`).
# as-of: `fetched_date <= {consensus_asof}` 의 종목별 **최신 fetched_date** 한 판.
#   G-M2 에서 v3 09-23 점수의 리비전 입력이 09-22 자료였으므로 `--consensus-asof` 로 맞춘다.
# target_period: WISE 는 종목마다 3개(당해·차기·차차기 — 09-23 실측)를 준다.
#   **as_of 이상인 것 중 최솟값**
#   = 아직 끝나지 않은 가장 가까운 결산기를 고른다(비12월 결산 202605·202903 실재).
#   v3 가 네이버에서 어떤 기를 골랐는지는 문서화돼 있지 않다 — T1.3 서버 대조의 확인 대상이다.
_ACC: tuple[tuple[str, str], ...] = (
    ("opinion", "610100"), ("revenue", "121000"), ("op", "121500"), ("ni", "122710"),
    ("eps", "312000"), ("per", "382000"), ("bps", "314000"), ("pbr", "382400"),
    ("roe", "211500"),
)
_ACC_INT = {"eps", "bps"}          # v3 DDL 이 INTEGER 인 둘
_LOOKBACKS = ("1w", "1m", "3m", "1y")

_MATRIX_PICK = """
WITH cur AS (
    SELECT m.ticker, m.fetched_date, m.target_period, m.target_label, m.base_date,
           m.acc_cd, m.lookback, m.value
    FROM {stg_consensus_matrix} m
    WHERE m.fetched_date <= DATE '{consensus_asof}'
),
latest AS (
    SELECT ticker, max(fetched_date) AS fetched_date FROM cur GROUP BY ticker
),
snap AS (
    SELECT c.* FROM cur c
    JOIN latest l ON l.ticker = c.ticker AND l.fetched_date = c.fetched_date
),
per AS (
    SELECT ticker, min(target_period) AS target_period
    FROM snap WHERE target_period >= '{asof_ym}' GROUP BY ticker
),
sel AS (
    SELECT s.* FROM snap s JOIN per p ON p.ticker = s.ticker
                                     AND p.target_period = s.target_period
)
"""

_REVISION_DAILY_SQL = _MATRIX_PICK + """
SELECT ticker                                 AS stock_code,
       CAST(max(base_date) AS VARCHAR)        AS base_date,
       max(target_label)                      AS target_period,
""" + ",\n".join(
    f"       CAST(max(CASE WHEN acc_cd = '{code}' AND lookback = 'current' THEN value END) AS "
    f"{'BIGINT' if name in _ACC_INT else 'DOUBLE'}) AS {name}" for name, code in _ACC
) + """,
       CAST(max(fetched_date) AS VARCHAR)     AS collected_date
FROM sel
GROUP BY ticker
"""

_REVISION_COMPARE_SQL = _MATRIX_PICK + """
SELECT ticker                                 AS stock_code,
       max(target_label)                      AS target_period,
""" + ",\n".join(
    f"       CAST(max(CASE WHEN acc_cd = '{code}' AND lookback = '{h}' THEN value END) AS "
    f"{'BIGINT' if name in _ACC_INT else 'DOUBLE'}) AS {name}_{h}"
    for name, code in _ACC for h in _LOOKBACKS
) + """
FROM sel
GROUP BY ticker
"""

# ── consensus_annual ────────────────────────────────────────────────────────────────────────
# stage `stg_consensus_annual`(c1050001 T2Y). 종목별 최신 fetched_date 한 판.
# v3 형식: period 'YYYY/MM' · period_type 는 **월이 12 면 annual, 아니면 quarter**
#   (v3 `_wisereport_parsers.py:89-98` 의 규칙 그대로 — 비12월 결산은 v3 에서도 quarter 로 들어가
#    scoring 의 `period_type='annual'` 창에서 빠진다. 같은 동작을 재현해야 비교가 선다).
# data_type: period_kind 'E' → 'estimate', 'A' → 'actual'
#   (v3 `v2_data_loader.py:50-57` 이 'estimate' 만 문자열로 본다).
# 같은 period 에 A·E 가 둘 다 오면 E 를 고른다(v2 의 'estimate 우선'과 같은 방향).
_CONSENSUS_ANNUAL_SQL = """
WITH latest AS (
    SELECT ticker, max(fetched_date) AS fetched_date
    FROM {stg_consensus_annual}
    WHERE fetched_date <= DATE '{consensus_asof}'
    GROUP BY ticker
),
sel AS (
    SELECT a.*, row_number() OVER (
             PARTITION BY a.ticker, a.period
             ORDER BY CASE WHEN a.period_kind = 'E' THEN 0 ELSE 1 END, a.period_label) AS rn
    FROM {stg_consensus_annual} a
    JOIN latest l ON l.ticker = a.ticker AND l.fetched_date = a.fetched_date
    WHERE a.period IS NOT NULL
)
SELECT ticker                                              AS stock_code,
       substr(period, 1, 4) || '/' || substr(period, 5, 2) AS period,
       CASE WHEN substr(period, 5, 2) = '12' THEN 'annual' ELSE 'quarter' END AS period_type,
       CASE WHEN period_kind = 'E' THEN 'estimate' ELSE 'actual' END          AS data_type,
       CAST(revenue AS DOUBLE)                             AS revenue,
       CAST(yoy_pct AS DOUBLE)                             AS yoy,
       CAST(op AS DOUBLE)                                  AS op,
       CAST(ni AS DOUBLE)                                  AS ni,
       CAST(round(eps) AS BIGINT)                          AS eps,
       CAST(round(bps) AS BIGINT)                          AS bps,
       CAST(per AS DOUBLE)                                 AS per,
       CAST(pbr AS DOUBLE)                                 AS pbr,
       CAST(roe_pct AS DOUBLE)                             AS roe,
       CAST(ev_ebitda AS DOUBLE)                           AS ev_ebitda,
       fs_basis                                            AS accounting_standard
FROM sel
WHERE rn = 1
"""

# ── financial_summary (두 원천 합성 — T1.5 · D-10) ──────────────────────────────────────────
# ① stage `stg_fin_wise`(WISE cF3002 재무제표 + cF4002 투자지표, 결정 D-5)
#      → revenue · op · ni · eps · bps · per · pbr · ev_ebitda · dividend_yield · shares ·
#        gross_profit · accounting_standard
# ② equity `fin_std`(DART 표준계정 PIT, S12) — **v3 quality 팩터 입력**
#      → total_assets · roa · debt_ratio · fcf · capex · roe
#   4일 그림자 실측에서 quality 상관이 0.48 뿐이었던 원인이 ②의 부재였다(나머지 팩터는
#   momentum 0.997 · flow 0.996 · revision 0.985 · valuation 0.977). WISE cF3002 는
#   손익계산서 전용이라 재무상태표·현금흐름표 계정이 없다.
#   ①의 판은 **(종목, ep) 단위** `fetched_date <= {consensus_asof}` 최신이다(배포 묶음 7 D7-4,
#   fi `fin_summary_sql` 과 같은 규칙). stage 2.7.0 이 같은 원문의 연속 판을 접어 cF3002·cF4002
#   판 날짜가 다를 수 있다 — 종목 한 날짜로 고르면 cF4002 만 새 판인 날 손익 열이 조용히 빈다.
#
# 정의(단위는 v3 `financial_summary` 기준 — 금액 억원 · 비율 %):
#   total_assets = total_asset / 1e8                              (원 → 억원)
#   roa          = net_income / total_asset   × 100               (%)
#   debt_ratio   = total_liab / total_equity  × 100               (%)
#   roe          = net_income / total_equity  × 100               (%)
#   capex        = abs(capex_ytd) / 1e8                           (원 → 억원)
#   fcf          = (cf_operating_ytd − abs(capex_ytd)) / 1e8      (원 → 억원)
#   `capex_ytd`(유형자산 취득, 연초누계)는 DART 제출사마다 유출을 음수로 적기도 양수로 적기도
#   한다. CAPEX 는 **취득액(크기)** 이므로 `abs()` 로 부호를 지우고 FCF 에서 뺀다 — 부호 규약을
#   실측으로 확인하기 전까지의 보수적 선택이며, 서버 대조(v3 네이버 CAPEX)로 검증할 항목이다.
#
# 조인·PIT:
#   `fin_std` grain 은 (corp_code, period_end, report_code, fs_div, vintage_kind) 라
#   `security.corp_code` 로 티커에 붙인다(보통주·우선주가 같은 corp_code 를 공유하면 둘 다
#   같은 재무를 받는다 — v3 네이버도 그렇다).
#   연간 = `report_code='11011'`(사업보고서). `period` 는 `period_end` 의 'YYYY/MM' 이라
#   비12월 결산도 자기 결산월로 들어간다(v3 규칙대로 월이 12 가 아니면 period_type='quarter').
#   PIT — `available_date <= {date}` 인 판본만 본다. 같은 (corp_code, period_end) 에 여러
#   판본이면 **연결(CFS) 우선**(v3·WISE 가 IFRS연결 기준이다) → 그 안에서 available_date 최신
#   (정정 공시 반영) → rcept_no 최신.
#   `gross_profit` 은 WISE 값 우선, 없으면 DART(원 → 억원). v3 원천에 가까운 쪽을 남긴다.
#   WISE 확정 행과 DART 행은 (ticker, period) FULL OUTER JOIN 이다 — 한쪽에만 있는 기도
#   행으로 남긴다(DART 만 있는 종목은 quality 입력이라도 채워진다).
# ⚠ 여전히 **없음(NULL)**: op_margin · ni_margin · yoy. 계산은 팩터층 몫이라 두지 않는다.
_FIN_SLOTS = "\n    UNION ALL\n    ".join(
    "SELECT ticker, ep, accode, p_accode, acc_nm, fs_basis, "
    f"period_label_{i} AS label, val_{i} AS val FROM cur" for i in range(1, 7))


def _fin_pick(ep: str, accode: str, top_only: bool = False,
              acc_nm: str | None = None) -> str:
    """accode 피벗 한 칸. `acc_nm` 을 주면 계정명까지 맞는 행만 센다.

    R1 — WISE 는 업종에 따라 **같은 accode 에 다른 계정**을 싣는다. 절단본 실측:
    003540(대신증권)의 cF3002 `200000` 은 '순이자이익' 이고 `200810`·`203170` 은
    하위 계정('단기매매금융자산매매이익'·'자산재평가이익')이다. accode 만 보면 금융업의
    순이자이익이 v3 `financial_summary.revenue` 로 들어가 밸류·퀄리티가 오염된다.
    계정명이 다르면 값을 만들지 않는다(NULL) — 잘못 채우느니 비운다(원칙 ④).
    """
    # DQ-6(2026-09-26 실측): 금융업 템플릿은 같은 계정을 **다른 accode** 에 싣는다 — 당기순이익이
    # 제조업 203170 · 증권 203730 · 은행 202550 · 보험 203250 · 신탁 202290. accode 를 고정하면
    # 은행·증권·보험 33종목의 순이익이 NULL 이 된다. 그래서 `acc_nm` 이 주어지면 **계정명 + 최상위
    # (p_accode IS NULL)** 로 고르고 accode 는 문서용 힌트로만 남긴다(R1 의 반대 방향 오염도 막힌다:
    # 이름이 다르면 안 센다). 계정명이 없으면(cF4002 지표) 종전대로 accode 로 고른다.
    if acc_nm is not None:
        cond = f"ep = '{ep}' AND acc_nm = '{acc_nm}' AND p_accode IS NULL"
    else:
        cond = f"ep = '{ep}' AND accode = '{accode}'"
        if top_only:
            cond += " AND p_accode IS NULL"
    return f"max(CASE WHEN {cond} THEN val END)"


# WISE 확정·추정 두 갈래가 함께 쓰는 v3 컬럼(DART 와 무관한 자리)
_W_COLS = """       CAST(round(w_revenue) AS BIGINT)                       AS revenue,
       CAST(round(w_op) AS BIGINT)                            AS op,
       CAST(round(w_ni) AS BIGINT)                            AS ni,
       CAST(round(w_eps) AS BIGINT)                           AS eps,
       CAST(round(w_bps) AS BIGINT)                           AS bps,
       CAST(w_per AS DOUBLE)                                  AS per,
       CAST(w_pbr AS DOUBLE)                                  AS pbr,"""
_W_TAIL = """       CAST(NULL AS DOUBLE)                                   AS op_margin,
       CAST(NULL AS DOUBLE)                                   AS ni_margin,
       CAST(w_dividend_yield AS DOUBLE)                       AS dividend_yield,
       CAST(round(w_shares) AS BIGINT)                        AS shares,
       CAST(w_ev_ebitda AS DOUBLE)                            AS ev_ebitda,
       CAST(NULL AS DOUBLE)                                   AS yoy,"""

# DQ-11(2026-09-26, 기록만): 아래 `period_type` 은 **v3 미러**다 — 월(mm)이 12 면 annual, 아니면
# quarter. 그래서 비12월 결산 12종목의 사업보고서가 `quarter` 로 들어간다. v3 scoring 의
# `period_type='annual'` 창이 그 종목을 빼는 동작까지 그대로 재현해야 그림자 비교가 서므로 여기서
# 고치지 않는다. 결산월을 옳게 보는 판정은 **모델 층이 `freq` 로** 한다(그림자 컷오버 뒤).
_FINANCIAL_SUMMARY_SQL = f"""
WITH latest AS (
    SELECT ticker, ep, max(fetched_date) AS fetched_date
    FROM {{stg_fin_wise}}
    WHERE fetched_date <= DATE '{{consensus_asof}}'
    GROUP BY ticker, ep
),
cur AS (
    SELECT w.* FROM {{stg_fin_wise}} w
    JOIN latest l ON l.ticker = w.ticker AND l.ep = w.ep AND l.fetched_date = w.fetched_date
),
slots AS (
    {_FIN_SLOTS}
),
parsed AS (
    SELECT ticker, ep, accode, p_accode, acc_nm, fs_basis, val,
           regexp_extract(label, '(\\d\\d\\d\\d)[./](\\d\\d)', 1) AS yyyy,
           regexp_extract(label, '(\\d\\d\\d\\d)[./](\\d\\d)', 2) AS mm,
           regexp_matches(label, '\\(E\\)')                      AS is_est
    FROM slots
    WHERE label IS NOT NULL AND regexp_matches(label, '\\d\\d\\d\\d[./]\\d\\d')
),
wise AS (
    SELECT ticker,
           yyyy || '/' || mm                                   AS period,
           CASE WHEN mm = '12' THEN 'annual' ELSE 'quarter' END AS period_type,
           is_est,
           {_fin_pick('cF3002', '200000', True, '매출액(수익)')}  AS w_revenue,
           {_fin_pick('cF3002', '201370', True, '영업이익')}      AS w_op,
           {_fin_pick('cF3002', '203170', True, '당기순이익')}    AS w_ni,
           {_fin_pick('cF4002', '312000', True)}                  AS w_eps,
           {_fin_pick('cF4002', '314000', True)}                  AS w_bps,
           {_fin_pick('cF4002', '382100')}                        AS w_per,
           {_fin_pick('cF4002', '382500')}                        AS w_pbr,
           {_fin_pick('cF4002', '331000')}                        AS w_ev_ebitda,
           {_fin_pick('cF4002', '431800')}                        AS w_dividend_yield,
           {_fin_pick('cF4002', '701250')}                        AS w_shares,
           {_fin_pick('cF3002', '200810', True, '매출총이익')}    AS w_gross_profit,
           max(CASE WHEN ep = 'cF3002' THEN fs_basis END)         AS w_fs_basis
    FROM parsed
    GROUP BY ticker, yyyy, mm, is_est
),
fin AS (
    SELECT f.corp_code, f.period_end, f.total_asset, f.total_liab, f.total_equity,
           f.net_income, f.cf_operating_ytd, f.capex_ytd, f.gross_profit,
           row_number() OVER (
               PARTITION BY f.corp_code, f.period_end
               ORDER BY CASE WHEN f.fs_div = 'CFS' THEN 0 ELSE 1 END,
                        f.available_date DESC, f.rcept_no DESC) AS rn
    FROM {{fin_std}} f
    WHERE f.report_code = '11011'
      AND f.available_date <= DATE '{{date}}'
),
dart AS (
    SELECT s.ticker,
           strftime(f.period_end, '%Y/%m')                      AS period,
           CASE WHEN strftime(f.period_end, '%m') = '12' THEN 'annual' ELSE 'quarter' END
                                                                AS period_type,
           f.total_asset  AS d_total_asset,
           f.total_liab   AS d_total_liab,
           f.total_equity AS d_total_equity,
           f.net_income   AS d_net_income,
           f.cf_operating_ytd AS d_cf_op,
           f.capex_ytd    AS d_capex,
           f.gross_profit AS d_gross_profit
    FROM fin f
    JOIN {{security}} s ON s.corp_code = f.corp_code
    WHERE f.rn = 1
),
act AS (
    SELECT coalesce(w.ticker, d.ticker)           AS ticker,
           coalesce(w.period, d.period)           AS period,
           coalesce(w.period_type, d.period_type) AS period_type,
           w.w_revenue, w.w_op, w.w_ni, w.w_eps, w.w_bps, w.w_per, w.w_pbr,
           w.w_ev_ebitda, w.w_dividend_yield, w.w_shares, w.w_gross_profit, w.w_fs_basis,
           d.d_total_asset, d.d_total_liab, d.d_total_equity, d.d_net_income,
           d.d_cf_op, d.d_capex, d.d_gross_profit
    FROM (SELECT * FROM wise WHERE NOT is_est) w
    FULL OUTER JOIN dart d ON d.ticker = w.ticker AND d.period = w.period
)
SELECT ticker                                                 AS stock_code,
       period, period_type,
{_W_COLS}
       CAST(d_net_income / nullif(d_total_equity, 0) * 100 AS DOUBLE)  AS roe,
       CAST(d_net_income / nullif(d_total_asset, 0) * 100 AS DOUBLE)   AS roa,
       CAST(d_total_liab / nullif(d_total_equity, 0) * 100 AS DOUBLE)  AS debt_ratio,
       CAST(round((d_cf_op - abs(d_capex)) / {KRW_PER_EOK}.0) AS BIGINT) AS fcf,
       CAST(round(abs(d_capex) / {KRW_PER_EOK}.0) AS BIGINT)           AS capex,
{_W_TAIL}
       CAST(NULL AS VARCHAR)                                  AS data_type,
       w_fs_basis                                             AS accounting_standard,
       CAST(round(coalesce(w_gross_profit, d_gross_profit / {KRW_PER_EOK}.0)) AS BIGINT)
                                                              AS gross_profit,
       CAST(round(d_total_asset / {KRW_PER_EOK}.0) AS BIGINT)  AS total_assets
FROM act

UNION ALL

-- (E) 슬롯 — 추정치에는 DART 확정 재무가 없다. v3 도 data_type='estimate' 로 갈라 두고
-- scoring 창(`data_type IS NULL`)에서 뺀다.
SELECT ticker                                                 AS stock_code,
       period, period_type,
{_W_COLS}
       CAST(NULL AS DOUBLE)                                   AS roe,
       CAST(NULL AS DOUBLE)                                   AS roa,
       CAST(NULL AS DOUBLE)                                   AS debt_ratio,
       CAST(NULL AS BIGINT)                                   AS fcf,
       CAST(NULL AS BIGINT)                                   AS capex,
{_W_TAIL}
       'estimate'                                             AS data_type,
       w_fs_basis                                             AS accounting_standard,
       CAST(round(w_gross_profit) AS BIGINT)                  AS gross_profit,
       CAST(NULL AS BIGINT)                                   AS total_assets
FROM wise
WHERE is_est
-- 같은 (종목, 기) 에 (E) 슬롯과 확정 슬롯이 둘 다 오면 v3 PK 가 하나뿐이라 뒤에 넣은 행이 남는다.
-- 확정치(data_type NULL)가 scoring 창이므로 NULLS LAST 로 그쪽을 마지막에 넣는다.
ORDER BY stock_code, period, data_type NULLS LAST
"""

# ── 모델 유니버스(사용자 결정 09-24) ────────────────────────────────────────────────────────
# "v3 유니버스는 추정치 데이터가 있는 종목만". v3 엔진 원본은 `stocks.market_cap >= min_market_cap`
# 으로만 유니버스를 자르므로(v3 `backend/scoring/engine.py:71-78`, v2 는 `market_cap > 0`),
# **추정치가 없는 종목의 `market_cap` 을 NULL 로 내보내면** v3 코드를 한 줄도 고치지 않고 같은
# 효과를 낸다. 이름·시장 열은 남으므로 브리핑·뉴스·unitelegram 의 조인은 깨지지 않는다.
# 판정: as_of 이하 최신 `stg_consensus_annual` 판에서 **당해 12월기 추정(period_kind='E')의
# op·ni 가 둘 다 있는** 종목.
ESTIMATE_TICKERS_SQL = """
WITH latest AS (
    SELECT ticker, max(fetched_date) AS fetched_date
    FROM {stg_consensus_annual}
    WHERE fetched_date <= DATE '{consensus_asof}'
    GROUP BY ticker
)
SELECT DISTINCT a.ticker
FROM {stg_consensus_annual} a
JOIN latest l ON l.ticker = a.ticker AND l.fetched_date = a.fetched_date
WHERE a.period_kind = 'E' AND a.period = '{asof_fy}'
  AND a.op IS NOT NULL AND a.ni IS NOT NULL
"""

# ── score_history · score_history_v2 (QL-C · T-16) ──────────────────────────────────────────
# 원천은 모델 판 점수 표 `<model_root>/<spec_id>/v=<build_id>/scores.parquet` 다. 판은
# `--date D --basis` 의 모델 성공 판 하나로 고정한다(`quant_db._resolve_model`).
# 최신 판으로 대체하지 않는다(P1).
#   score_history    ← scope@1.0(메인 모델 scope_v1.0, 엔진 v3_zscore)
#   score_history_v2 ← v2_percentrank@1.0
# 모델 점수 열 계약(`model.contracts.V3_SCORE_COLUMNS`·`V2_SCORE_COLUMNS`)은 v3 DDL 과 이름·순서가
# 같다(엔진이 v3 원본 이식 — G-M3). 그래서 열은 **같은 이름끼리** 옮기고, 타입만 v3 DDL 에 맞춰 명시
# CAST 한다(TEXT → VARCHAR · INTEGER → BIGINT · REAL → DOUBLE).
# `null_columns` 는 원천 열이 있어도 옮기지 않고 NULL 로 둔다(임의로 채우지 않는다):
#   · score_history 6열(growth·sentiment·volatility·size·foreign·shareholder_score) — v3 에서도 항상
#     NULL(플랜 §8-1). 엔진도 NULL 이지만(MG3) 여기서 한 번 더 못 박는다.
#   · score_history.val_ev_ebitda — scope@1.0 은 EV/EBITDA 를 밸류에서 뺐다. 원본 v3 에서 데이터가
#     3/1,321 종목뿐이라 비어 있던 계산과 같게 만든 spec 이다(`config/models/scope_v1_0.toml`).
#     엔진은 원값을 싣지만 v3 열 의미('밸류 입력')와 달라 비운다. v3 실물도 비어 있다
#     (로컬 사본 08-07 0/1,283).
# 종목 수는 v3 보다 적다(scope 593 · v2 625 vs v3 1,329 · 2,526) — 10-05 유니버스 결정, T-17 수용.
# 쓰기는 날짜 단위 교체다(그 score_date 행 전부 삭제 → 삽입, 한 트랜잭션 — `quant_db`).
SCORE_SPEC = "scope@1.0"
SCORE_V2_SPEC = "v2_percentrank@1.0"
_SCORE_NULL = ("growth_score", "sentiment_score", "volatility_score", "size_score",
               "foreign_score", "shareholder_score", "val_ev_ebitda")


def _score_type(col: str) -> str:
    """v3 점수 표 DDL 타입 → duckdb CAST 타입."""
    if col == "rank":
        return "BIGINT"
    if col in ("stock_code", "score_date") or col.endswith("_flag"):
        return "VARCHAR"
    return "DOUBLE"


def _score_sql(columns: tuple[str, ...], null_columns: tuple[str, ...]) -> str:
    """같은 이름 열을 v3 타입으로 옮기는 SELECT. `{scores}` 자리에 판의 read_parquet 이 온다."""
    sel = ",\n       ".join(
        f'CAST(NULL AS {_score_type(c)}) AS "{c}"' if c in null_columns
        else f'CAST(s."{c}" AS {_score_type(c)}) AS "{c}"' for c in columns)
    return f"SELECT {sel}\nFROM {{scores}} s\n"


# ── 선언 ────────────────────────────────────────────────────────────────────────────────────
MAPPINGS: tuple[TableMapping, ...] = (
    TableMapping(
        v3_table="daily_prices",
        source_kind=EQUITY,
        sources=("price_daily", "universe_daily"),
        columns=("stock_code", "trade_date", "open", "high", "low", "close", "volume",
                 "amount", "adj_close"),
        pk=("stock_code", "trade_date"),
        sql=_DAILY_PRICES_SQL,
        retire_when="브리핑 kr_market·리서치센터 S1/S2/S11·가설 store·unitelegram 이 "
                    "equity price_daily 직독으로 옮겨진 뒤",
        note="basis='krx' 행만 내보낸다 — 저녁 잠정 T 행은 v3 NOT NULL(open·high·low)을 "
             "못 채운다(GAP-1). 장 마감 판 T 행은 compat.t_rows 가 원장에서 만든다(QL-D · T-32)",
    ),
    TableMapping(
        v3_table="stocks",
        source_kind=EQUITY,
        sources=("universe_daily", "security", "price_daily"),
        columns=("stock_code", "stock_name", "market", "sector", "market_cap", "listed_date",
                 "is_active", "delisted_date", "updated_at"),
        pk=("stock_code",),
        sql=_STOCKS_SQL,
        retire_when="브리핑·리서치센터·뉴스 preview/naver_ir·api health·unitelegram kael_db 가 "
                    "equity security/universe_daily 직독으로 옮겨진 뒤",
        note="sector 는 v3 와 같은 KRX 업종명(키움 ka10099 upName) — stage stg_master_daily 직독(T-19)",
        cross_sources=((STAGE, "stg_master_daily"),),
    ),
    TableMapping(
        v3_table="investor_detail_flows",
        source_kind=EQUITY,
        sources=("flow_daily", "universe_daily"),
        columns=("stock_code", "trade_date") + tuple(c for c, _ in _FLOW_COLS),
        pk=("stock_code", "trade_date"),
        sql=_INVESTOR_FLOWS_SQL,
        retire_when="리서치센터 S1/S11·drilldown·가설·unitelegram 이 equity flow_daily "
                    "직독으로 옮겨진 뒤",
        note="natfor_krw(내외국인)는 v3 에 자리가 없어 버린다. orgn 은 7주체 합과 다르다(GAP-03)",
    ),
    TableMapping(
        v3_table="consensus_revision_daily",
        source_kind=STAGE,
        sources=("stg_consensus_matrix",),
        columns=("stock_code", "base_date", "target_period") + tuple(n for n, _ in _ACC) +
                ("collected_date",),
        pk=("stock_code", "base_date"),
        sql=_REVISION_DAILY_SQL,
        retire_when="만료: M2 T2.6 equity consensus_revision(S17b) 승격 시 — "
                    "그때 이 stage 직독 예외를 없앤다(B-23 종결)",
        note="한시 예외: equity 층을 건너뛰고 stage 를 직독하는 유일한 매핑(GAP-6)",
    ),
    TableMapping(
        v3_table="consensus_revision_compare",
        source_kind=STAGE,
        sources=("stg_consensus_matrix",),
        columns=("stock_code", "target_period") +
                tuple(f"{n}_{h}" for n, _ in _ACC for h in _LOOKBACKS),
        pk=("stock_code",),
        sql=_REVISION_COMPARE_SQL,
        retire_when="만료: M2 T2.6 equity consensus_revision(S17b) 승격 시 — "
                    "그때 이 stage 직독 예외를 없앤다(B-23 종결)",
        note="한시 예외: 위와 같은 좌표에서 lookback 1w/1m/3m/1y 축만 뽑는다",
    ),
    TableMapping(
        v3_table="consensus_annual",
        source_kind=STAGE,
        sources=("stg_consensus_annual",),
        columns=("stock_code", "period", "period_type", "data_type", "revenue", "yoy", "op",
                 "ni", "eps", "bps", "per", "pbr", "roe", "ev_ebitda", "accounting_standard"),
        pk=("stock_code", "period", "period_type"),
        sql=_CONSENSUS_ANNUAL_SQL,
        retire_when="v2 엔진이 model 층 입력(equity consensus)으로 옮겨진 뒤 — "
                    "consensus_annual 소비자는 scoring 뿐이다(플랜 §8-3)",
    ),
    TableMapping(
        v3_table="financial_summary",
        source_kind=STAGE,
        sources=("stg_fin_wise",),
        columns=("stock_code", "period", "period_type", "revenue", "op", "ni", "eps", "bps",
                 "per", "pbr", "roe", "roa", "debt_ratio", "fcf", "capex", "op_margin",
                 "ni_margin", "dividend_yield", "shares", "ev_ebitda", "yoy", "data_type",
                 "accounting_standard", "gross_profit", "total_assets"),
        pk=("stock_code", "period", "period_type"),
        sql=_FINANCIAL_SUMMARY_SQL,
        retire_when="v3 엔진이 model 층으로 은퇴한 뒤(D-4) — financial_summary 소비자는 "
                    "scoring 뿐이다(플랜 §8-3)",
        null_columns=("op_margin", "ni_margin", "yoy"),
        note="WISE(손익·투자지표) + DART fin_std(재무상태표·현금흐름) 합성 — T1.5/D-10. "
             "quality 입력 roa·debt_ratio·fcf·total_assets 는 DART 쪽에서 온다",
        cross_sources=((EQUITY, "fin_std"), (EQUITY, "security")),
    ),
    TableMapping(
        v3_table="score_history",
        source_kind=MODEL,
        sources=(SCORE_SPEC,),
        columns=V3_SCORE_COLUMNS,
        pk=("stock_code", "score_date"),
        sql=_score_sql(V3_SCORE_COLUMNS, _SCORE_NULL),
        retire_when="브리핑 상위 8·export·api health·unitelegram get_signal_insights 가 "
                    "model 판 직독으로 옮겨진 뒤",
        null_columns=_SCORE_NULL,
        note="scope@1.0 점수 표 → 같은 이름 열(T-16). score_date 단위 교체. "
             "val_ev_ebitda 는 scope 가 밸류에 안 써 NULL",
    ),
    TableMapping(
        v3_table="score_history_v2",
        source_kind=MODEL,
        sources=(SCORE_V2_SPEC,),
        columns=V2_SCORE_COLUMNS,
        pk=("stock_code", "score_date"),
        sql=_score_sql(V2_SCORE_COLUMNS, ()),
        retire_when="리서치센터 S6·export 가 model 판 직독으로 옮겨진 뒤",
        note="v2_percentrank@1.0 점수 표 → 같은 이름 21열 전부(T-16). score_date 단위 교체",
    ),
)

BY_TABLE: dict[str, TableMapping] = {m.v3_table: m for m in MAPPINGS}
