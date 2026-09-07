"""S05 기업행위 슬라이스 — `corp_event` MVP-B (DESIGN v1.2 §4-2 · GATES v1.0 §3-⑨ · WORKFLOW §3-5).

MVP 범위는 `split`·`reverse_split`·`bonus`·`capred` 4종이다. `event_type` 어휘 13종은 DESIGN
대로 선언하되(EVENT_TYPE_VOCAB) 나머지 9종의 행은 만들지 않는다 — 배당·락일·유상증자·CB·
자사주는 S16·후속 슬라이스. 산출식은 `sql/corp_event.sql` 하나, 상수는 `baseline_seed_s05.json`.

**모집단(pool) 과 범위(scope)**: 원천 행을 법인 → 티커로 전개한 leg 행 전부가 pool 이고,
조정할 가격이 없는 사건은 **범위 밖**(scope_out) 이라 격리도 산출도 아니다 — 서버 1차 빌드
(09-05)에서 사업보고서 증자(감자)현황이 창립 이래(1963~) 이력을 회고 기재해 격리 8,834 / 산출
3,865 로 EG7 이 깨진 것이 근거. 범위 밖 4종(`SCOPE_OUT_VOCAB`)은 EG3_corp_event 기록형 metric.
`stg_capital.isu_dcrs_stock_knd` 종류 어휘는 아래 **대응표 튜플**이 정본이고 `.sql` 의 IN 리터럴이
그것을 그대로 옮긴다(tests 가 대조) — 오타·동의어·표기 변형을 명시적 문자열로 폐쇄 어휘에 대응하고
모호한 것은 unknown 에 남긴다(공백·개행 정규화는 stage PR #70 몫). 보통주 계열인데 상장 보통주가
없을 때만 `ticker_unresolved` 격리.

EG1 우변은 GATES §3-⑨ 의 `_reg_corp_event_source` 역할을 하는 `SOURCES` 등록표 — **`.sql` 의
pool CTE(마커 앞부분)를 그대로 재사용**해 `scope_out IS NULL` 인 후보 행수를 원천별로 센다.
좌변은 `count(out) + Σ(n_src_rows − 1)`(접힌 행을 되돌린 수) 이므로 `Σ후보 − n_reject` 와 같아야
한다. 모집단 정의를 두 벌 손으로 베끼지 않는 대신, 전개 전 원천 행수(`RAW_SOURCE_ROWS`)는 stage
뷰만으로 독립 산출해 metric 으로 남긴다. 프레임 `_meta.n_dedup` 은 0 으로 고정돼 있어 dedup
건수는 EG3_corp_event 의 metric `n_dedup` 이 낸다.

테이블 특화 술어 EG3_corp_event: 어휘 폐쇄(event_type·effective_basis·source) · 티커 폭 ·
dedup 축 재계산(n_dup = 0) · ratio 양수 · **방향 불변식**(split·bonus → ratio > 1,
reverse_split·capred → ratio < 1 — 서버 실측 007195 2013-05-24 액면 5,000→1,000 인데
주식수 ×0.833 이 'split' 로 나가 S07 EGC-04 에서 거절된 사고의 회귀 게이트; KRX 유형은 주식수
비 방향으로 매기고 액면가 변화는 트리거일 뿐, 주식수 불변 액면 변경은 `krx_par_only` 로 범위 밖)
· KRX 행 announce = effective · event_id 결정성 · **산출 행이 캘린더 밖·상장 전이 아님(독립
재검사)**. 근접 중복(같은 티커·유형이
`near_dup_window_days` 안에 다른 원천으로 2건)은 기록형 — 절단본 실측: 우양에이치씨 2018 감자가
결정공시 cr_std 2018-10-12 · 자본변동 isu_dcrs_de 2018-10-13 으로 하루 어긋나 2행이 된다.
EG8-P04(기준가≠전일종가 recall)는 `price_daily` 가 필요하므로 S06 이후에 붙는다.
"""
from __future__ import annotations

from pathlib import Path

from stage.gates import GateResult, GateStatus

from .gates import EquityGateContext
from .model import EquityTable, FieldProfile, register

SQL_DIR = Path(__file__).parent / "sql"
SQL_PATH = SQL_DIR / "corp_event.sql"

# DESIGN §4-2 — event_type 어휘 13종. MVP 는 앞 4종만 산출한다.
EVENT_TYPE_VOCAB: tuple[str, ...] = (
    "split", "reverse_split", "bonus", "capred", "rights", "spinoff", "merger",
    "stock_dividend", "cash_dividend", "cb_issue", "treasury_buy", "treasury_sell", "other")
MVP_EVENT_TYPES: tuple[str, ...] = ("split", "reverse_split", "bonus", "capred")
EFFECTIVE_BASIS_VOCAB: tuple[str, ...] = (
    "disclosure_body", "krx_shares_change", "krx_notice", "unconfirmed")
SOURCE_VOCAB: tuple[str, ...] = ("event_fric", "event_pifric", "event_cr", "capital",
                                 "krx_listing")
REJECT_REASONS: tuple[str, ...] = ("ticker_unresolved", "effective_unresolved", "ratio_unparsed",
                                   "effective_before_announce")
# 범위 밖 사유(격리 아님, 기록형) — sql/corp_event.sql pool.scope_out
SCOPE_OUT_VOCAB: tuple[str, ...] = ("out_of_calendar", "unlisted_class", "class_unknown",
                                    "pre_listing", "krx_par_only", "share_unchanged")
# 방향 불변식(EG3_corp_event 폐기형, 원천 무관): ratio 는 주식수 배수이므로 유형이 방향을 못 박는다.
RATIO_ABOVE_ONE: tuple[str, ...] = ("split", "bonus")
RATIO_BELOW_ONE: tuple[str, ...] = ("reverse_split", "capred")
TICKER_LEN = 6

# ── `stg_capital.isu_dcrs_stock_knd` 종류 어휘 대응표 (S05 후속, 09-05) ─────────────────
# 자유 텍스트(서버 새 판본 b_20260905T105922_786120Z 어휘 249종) → 폐쇄 어휘 common · preferred ·
# unlisted · unknown. **명시적 문자열만** 대응한다(패턴 매칭 없음 — `.sql` 의 IN 리터럴과 tests 가
# 대조). 앞뒤 공백·개행·NFKC 는 stage 가 정규화했고(PR #70) `.sql` 의 trim 은 옛 절단본 방어용이다.
# 판정 원칙:
#   common    머리 명사가 보통주 하나 — 표기 변형(기명식·주식·띄어쓰기·'보통' 약칭), 한 음절
#             오타·탈자(이 열에서 보통주 외 해석이 없다), 각주 표시((*1)·(주1)·주1)), 사건·방향
#             수식어(발행·무상증자·차감·(-)·일반공모·KDR). 의결권 수식어는 보통주 토큰이 있을 때만.
#   preferred 상장 가능한 종류주 — 우선주 표기 변형, 1우·2우·3우·제N종·제N회·A/B/C·3우B 의
#             번호·회차·구형/신형 표기(법인의 상장 우선주 leg 전부에 붙는다; 없으면 unlisted_class),
#             '종류주식(우선주)'·'우선주(종류주)' 처럼 우선주로 특정된 것, 결정공시 어휘 '기타주식'
#             (DART 정의 = 보통주 외 주식 → `.sql` 의 estk leg 와 같은 규칙).
#   unlisted  상환·전환 조항이 있는 비상장 종류주 — 상환전환우선주(RCPS)·전환상환우선주·전환우선주
#             (CPS)·상환우선주·전환주와 그 번호·종·회차·괄호 설명·오타 변형. 두 종류를 함께 적어도
#             둘 다 비상장이면(3,4우선주 · 제1종 및 제2종) 결과가 같으므로 unlisted.
#   unknown   모호한 것 전부 — '〃'(앞 행 참조는 하지 않는다) · '-' · 복수 종류('보통주/우선주',
#             '보통주, 전환우선주') · 종류 불명('종류주식'·'종류주'·'혼합주'·'A종 종류주식') ·
#             보통주 토큰 없는 의결권 표현('의결권 있는 주식' — RCPS 도 의결권이 있을 수 있다) ·
#             집계 행(합계·계·총발행 주식수·'보통주 합계') · 사건·증권 이름(무상증자·전환사채·
#             신주인수권·주식매수선택권·KDR) · 전환 기술('전환상환우선주 보통주 전환') · 숫자 ·
#             잘린 것('보통주,'·'3우선주(전환)'·법인명 붙은 것).
# 서버 실측(MVP 유형 행, 새 판본): 정규 표기 보통주 20,872 · 우선주 2,263 · 상환전환우선주 237 ·
# 전환상환우선주 114 · 전환우선주 100 · RCPS 43. 대응표 밖(unknown)에 남는 MVP 행 253(옛 어휘 9종
# 기준 605) — '-' 63 · 종류주식 43 · 종류주 38 · '〃' 22 · '보통주/우선주' 12 가 대부분.
COMMON_KINDS: tuple[str, ...] = (
    # 정규 · 표기 변형
    "보통주", "보통주식", "기명식보통주", "기명식 보통주", "기명식보통주식", "기명식 보통주식",
    "보 통 주", "보통",
    # 의결권 수식어 — 보통주 토큰이 함께 있는 것만
    "의결권이 있는 보통주", "의결권 있는 보통주", "의결권있는주식(보통주)",
    # 한 음절 오타 · 탈자
    "보퉁주", "보통부", "부통주", "보톧주", "보통중", "보통즈", "보통투", "보동주", "보통수",
    "보총주", "보통주시", "통주",
    # 각주 표시
    "보통주(*1)", "보통주(*2)", "보통주(*3)", "보통주(*4)", "보통주(*5)", "보통주*1)", "보통주*3)",
    "보통주(주1)", "보통주(주3)", "보통주 (주1)", "보통주 (주2)", "보통주주1)", "보통주주2)",
    "보통주주3)",
    # 사건 · 방향 수식어 — 종류는 보통주 하나
    "보통주 발행", "보통주 무상증자", "보통주(차감)", "보통주(-)", "보통주(일반공모)",
    "보통주(KDR)",
)
PREFERRED_KINDS: tuple[str, ...] = (
    # 정규 · 표기 변형 · 우선주로 특정된 것
    "우선주", "우선주식", "기명식우선주", "기명식 우선주", "기명식 우선주식", "우 선 주", "우선",
    "우선주(종류주)", "종류주식(우선주)", "의결권이 없는 우선주", "기타주식",
    # 번호 · 회차 · 구형/신형 표기(1우 · 2우B · 3우B 계열)
    "1우선주", "2우선주", "3우선주", "제1우선주", "제3우선주", "제1종 우선주", "제2종 우선주",
    "제1종우선주", "제2종우선주", "제2회우선주식", "제3회우선주식", "우선주A", "우선주B",
    "우선주C", "3우B", "종류주식(1우선주)", "종류주식(2우선주)",
    # 각주 · 방향 표시
    "우선주(*)", "우선주*1)", "우선주주3)", "우선주주4)", "우선주주5)", "우선주(감소)",
)
UNLISTED_KINDS: tuple[str, ...] = (
    # 상환전환우선주(RCPS) 계열
    "상환전환우선주", "상환전환 우선주", "상환 전환 우선주", "상환전환우선주식", "(상환전환우선주)",
    "상환전환우선주(*1)", "상환전환우선주(제1종)", "상환전환우선주(제2종)",
    "제1종 상환전환우선주", "제2종 상환전환우선주", "제1종 상환전환우선주 (주1)",
    "제1종 상환전환우선주 (주2)", "기명식 상환전환우선주", "기명식 상환전환우선주(제1종)",
    "의결권 있는 상환전환우선주", "의결권 있는상환전환우선주", "의결권 없는 상환전환우선주",
    "상환전환우선주(상환 및 전환에 관하여 특수한 정함이있는 주식이며, 의결권이 있음)",
    "상환전환우선주(상환 및 전환에 관하여 특수한 정함이 있는 주식으로 의결권이 있음)",
    "상환전환우선주(상환 및 전환에 관하여 특수한 정함이있는 주식으로 의결권이 있음)",
    "상환전환1우선주", "상환전환2우선주", "상환전환3우선주", "상환전환4우선주", "상환전환5우선주",
    "상환전환6우선주", "상환전환7우선주", "상환전환8우선주", "상환전환9우선주", "상환전환3,4우선주",
    "상환전환우선주2", "제일상환전환우선주", "제이상환전환우선주", "제삼상환전환우선주",
    "제사상환전환우선주", "제일상환 전환우선주", "제이상환 전환우선주", "제삼상환 전환우선주",
    "제사상환 전환우선주", "상환전환종류주", "상환전환종류주식", "상환전환주",
    "전환상환우선주 및상환우선주",
    "RCPS", "RCPS1", "RCPS2", "RCPS3", "RCPS4", "RCPS5", "RCPS6", "RCPS7", "RCPS8", "RCPS9",
    "RCPS 1종", "RCPS 2종", "RCPS 3종", "RCPS 4종", "RCPS 5종", "RCPS 6종", "RCPS(제1종)",
    "RCPS(제2종)", "RCPS (제1종 및 제2종", "우선주(RCPS)",
    # 전환상환우선주 계열
    "전환상환우선주", "전환상환 우선주", "전환상환우선주식", "전환상환우선주(-)", "전환상환3우선주",
    "기명식전환상환2종우선주",
    # 전환우선주(CPS) 계열
    "전환우선주", "전환 우선주", "기명식 전환우선주", "전환우선주(감소)", "전환우선주(*1)",
    "전환우선주(*)",
    "전환우선주(전환에 관하여 특수한 정함이있는 주식이며, 의결권이 있음)",
    "전환우선주(전환에 관하여 특수한 정함이 있는 주식이며, 의결권이 있음)",
    "전환우선주2(전환에 관하여 특수한 정함이 있는 주식이며, 의결권이 있음)",
    "제1종전환우선주", "제2종전환우선주", "제3종전환우선주", "제4종전환우선주", "제5종전환우선주",
    "제6종전환우선주", "제오전환우선주", "제육전환우선주", "제칠전환우선주", "제팔전환우선주",
    "제구전환우선주", "CPS", "CPS 7종",
    # 상환우선주 · 전환주
    "상환우선주", "전환주",
)
# 정규 표기 — 종류(instrument)당 하나. 이 밖의 대응은 별칭이고 EG3_corp_event 가
# `n_class_mapped_by_alias` 로 센다(별칭 비중이 갑자기 늘면 원천 서식 변화 신호).
CANONICAL_KINDS: tuple[str, ...] = ("보통주", "우선주", "상환전환우선주", "전환상환우선주",
                                    "전환우선주", "상환우선주", "전환주", "RCPS", "CPS")
KNOWN_KINDS: tuple[str, ...] = COMMON_KINDS + PREFERRED_KINDS + UNLISTED_KINDS

# ── 모집단 SQL 재사용 (sql/corp_event.sql 의 pool CTE 까지) ───────────────────
_POOL_MARKER = "-- ==== eg1:"


def _pool_prefix() -> str:
    """`.sql` 의 첫 줄부터 pool CTE 닫는 괄호까지 — 뒤에 `SELECT … FROM pool` 을 붙여 쓴다."""
    text = SQL_PATH.read_text(encoding="utf-8")
    head, sep, _ = text.partition(_POOL_MARKER)
    if not sep:
        raise ValueError(f"pool marker not found in {SQL_PATH}: expected a line starting with "
                         f"{_POOL_MARKER!r} right after the pool CTE")
    return head


def pool_sql(select: str) -> str:
    """pool 위의 단일 SELECT. 예: pool_sql(\"count(*) … WHERE scope_out IS NULL\")."""
    return f"{_pool_prefix()}\nSELECT {select}"


_CAPITAL_MVP_PREDICATE = ("isu_dcrs_stle LIKE '%감자%' OR (isu_dcrs_stle LIKE '%무상증자%' "
                          "AND isu_dcrs_stle NOT LIKE '%유상%')")

# 원천 등록표 (GATES §3-⑨ `_reg_corp_event_source`) — 원천별 **범위 안 후보** 행수.
SOURCES: tuple[tuple[str, str], ...] = tuple(
    (s, pool_sql(f"count(*) FROM pool WHERE scope_out IS NULL AND source = '{s}'"))
    for s in SOURCE_VOCAB)

# 전개 전 원천 행수 — stage 뷰만 읽는 독립 산출(기록형). krx 는 액면가 변경일 수.
RAW_SOURCE_ROWS: tuple[tuple[str, str], ...] = (
    ("event_fric", "SELECT count(*) FROM stg_event_fric"),
    ("event_pifric", "SELECT count(*) FROM stg_event_pifric"),
    ("event_cr", "SELECT count(*) FROM stg_event_cr"),
    ("capital", f"SELECT count(*) FROM stg_capital WHERE {_CAPITAL_MVP_PREDICATE}"),
    ("krx_listing", (
        "SELECT count(*) FROM ("
        "SELECT ticker, date, par_value_krw, lag(par_value_krw) OVER w AS prev_par, "
        "lag(date) OVER w AS prev_date FROM stg_listing_daily "
        "WINDOW w AS (PARTITION BY ticker ORDER BY date)) l "
        "JOIN trading_calendar c ON c.date = l.date "
        "WHERE l.prev_par IS NOT NULL AND l.par_value_krw IS NOT NULL "
        "AND l.par_value_krw <> l.prev_par AND l.prev_date = c.prev_td")),
)

EG1_RHS_SQL = pool_sql("count(*) FROM pool WHERE scope_out IS NULL")
EG1_LHS_SQL = "SELECT count(*) + coalesce(sum(n_src_rows - 1), 0) FROM out_pq"


def _n(ctx: EquityGateContext, sql: str) -> int:
    row = ctx.con.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"gate query returned no row — table={ctx.rule.name} sql={sql[:200]}")
    return int(str(row[0]))


def _vocab_sql(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _outside_vocab(ctx: EquityGateContext, column: str, values: tuple[str, ...]) -> int:
    return _n(ctx, f'SELECT count(*) FROM "{ctx.out_view}" WHERE "{column}" IS NULL '
                   f'OR "{column}" NOT IN ({_vocab_sql(values)})')


def _counts(ctx: EquityGateContext, column: str) -> dict[str, int]:
    return {str(r[0]): int(str(r[1])) for r in ctx.con.execute(
        f'SELECT "{column}", count(*) FROM "{ctx.out_view}" GROUP BY 1 ORDER BY 1').fetchall()}


def _scope_counts(ctx: EquityGateContext) -> dict[str, dict[str, int]]:
    """pool 을 (scope, source) 로 센다. 범위 안은 'in_scope'."""
    out: dict[str, dict[str, int]] = {}
    for scope, source, n in ctx.con.execute(pool_sql(
            "coalesce(scope_out, 'in_scope'), source, count(*) FROM pool "
            "GROUP BY 1, 2 ORDER BY 1, 2")).fetchall():
        out.setdefault(str(scope), {})[str(source)] = int(str(n))
    return out


def eg3_corp_event(ctx: EquityGateContext) -> GateResult:
    """EG3-P07·P13 + GATES §3-⑨ dedup 재계산 + 범위 밖·원천별 행수·n_dedup 기록."""
    v = ctx.out_view
    checks = {
        "n_event_type_outside_vocab": _outside_vocab(ctx, "event_type", EVENT_TYPE_VOCAB),
        "n_event_type_outside_mvp": _outside_vocab(ctx, "event_type", MVP_EVENT_TYPES),
        "n_effective_basis_outside_vocab": _outside_vocab(ctx, "effective_basis",
                                                          EFFECTIVE_BASIS_VOCAB),
        "n_source_outside_vocab": _outside_vocab(ctx, "source", SOURCE_VOCAB),
        "n_ticker_bad_width": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE ticker IS NULL '
                                      f"OR typeof(ticker) <> 'VARCHAR' OR length(ticker) <> "
                                      f"{TICKER_LEN}"),
        # GATES §3-⑨ — 선언 dedup 축 (ticker, event_type, effective_date) 재계산
        "n_dup_key": _n(ctx, "SELECT count(*) FROM (SELECT ticker, event_type, effective_date, "
                             f'count(*) AS c FROM "{v}" GROUP BY ALL HAVING c > 1)'),
        "n_ratio_nonpositive": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                       "WHERE ratio IS NOT NULL AND ratio <= 0"),
        # 방향 불변식 — 서버 실측 007195:split:2013-05-24 share_factor 0.833(액면 5,000→1,000 인데
        # 주식수 27,011→22,505)이 S07 EGC-04 에서 format_error 로 거절된 사고의 회귀 게이트
        "n_direction_violation": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE ratio IS NOT NULL AND ('
                 f"(event_type IN ({_vocab_sql(RATIO_ABOVE_ONE)}) AND ratio <= 1) OR "
                 f"(event_type IN ({_vocab_sql(RATIO_BELOW_ONE)}) AND ratio >= 1))"),
        "n_krx_announce_ne_effective": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE source = \'krx_listing\' '
                 "AND (announce_date <> effective_date "
                 "OR effective_basis <> 'krx_shares_change')"),
        "n_src_rows_lt_1": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE n_src_rows < 1'),
        "n_event_id_mismatch": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE event_id <> ticker || \':\' || event_type '
                 "|| ':' || CAST(effective_date AS VARCHAR)"),
        "n_available_ne_announce": _n(ctx, f'SELECT count(*) FROM "{v}" '
                                           "WHERE available_date <> announce_date"),
        # 범위 규칙의 독립 재검사 — 산출 행은 캘린더 안이고 그 티커의 첫 존재일 이후여야 한다
        "n_out_off_calendar": _n(
            ctx, f'SELECT count(*) FROM "{v}" WHERE effective_date < '
                 "(SELECT min(date) FROM trading_calendar) OR effective_date > "
                 "(SELECT max(date) FROM trading_calendar)"),
        "n_out_pre_listing": _n(
            ctx, f'SELECT count(*) FROM "{v}" e JOIN (SELECT ticker, min(first_date) AS fd '
                 "FROM security_span GROUP BY ticker) s USING (ticker) "
                 "WHERE e.effective_date < s.fd"),
    }
    src_counts = {name: _n(ctx, sql) for name, sql in SOURCES}
    raw_counts = {name: _n(ctx, sql) for name, sql in RAW_SOURCE_ROWS}
    scope = _scope_counts(ctx)
    window = ctx.baseline.get(ctx.rule.name, "near_dup_window_days")
    n_near_dup: int | None = None
    if window is not None:
        w = int(str(window))
        n_near_dup = _n(ctx, f'SELECT count(*) FROM "{v}" a JOIN "{v}" b '
                             "ON a.ticker = b.ticker AND a.event_type = b.event_type "
                             "AND a.source <> b.source AND a.effective_date < b.effective_date "
                             f"AND date_diff('day', a.effective_date, b.effective_date) <= {w}")
    # 종류 어휘 대응표 밖의 문자열(MVP 유형 행) — 새 문자열이 늘면 여기서 보인다. trim 은 `.sql` 과
    # 같은 방어(정규화 자체는 stage 몫).
    kind_rows = ctx.con.execute(
        "SELECT coalesce(trim(isu_dcrs_stock_knd), '<NULL>') AS k, count(*) AS c "
        f"FROM stg_capital WHERE {_CAPITAL_MVP_PREDICATE} GROUP BY 1 ORDER BY c DESC, k"
    ).fetchall()
    unknown_kinds = [str(k) for k, _ in kind_rows if str(k) not in KNOWN_KINDS]
    n_class_unknown_mvp = sum(int(str(c)) for k, c in kind_rows if str(k) not in KNOWN_KINDS)
    n_class_mapped_by_alias = sum(int(str(c)) for k, c in kind_rows
                                  if str(k) in KNOWN_KINDS and str(k) not in CANONICAL_KINDS)
    metrics: dict[str, object] = {
        "n_src_by_source": src_counts, "n_src_total": sum(src_counts.values()),
        "n_raw_rows_by_source": raw_counts,
        "n_pool_by_scope": {k: sum(d.values()) for k, d in scope.items()},
        "n_pool_by_scope_source": scope,
        "n_krx_par_only": sum(scope.get("krx_par_only", {}).values()),
        "n_share_unchanged": sum(scope.get("share_unchanged", {}).values()),
        "krx_share_change_tol": ctx.baseline.get(ctx.rule.name, "krx_share_change_tol"),
        "n_dedup": _n(ctx, f'SELECT coalesce(sum(n_src_rows - 1), 0) FROM "{v}"'),
        "n_out_by_source": _counts(ctx, "source"),
        "n_by_event_type": _counts(ctx, "event_type"),
        "n_by_effective_basis": _counts(ctx, "effective_basis"),
        "n_ratio_null": _n(ctx, f'SELECT count(*) FROM "{v}" WHERE ratio IS NULL'),
        "max_announce_minus_effective_days": _n(
            ctx, f"SELECT coalesce(max(date_diff('day', effective_date, announce_date)), 0) "
                 f'FROM "{v}"'),
        "n_near_dup_cross_source": n_near_dup, "near_dup_window_days": window,
        "class_unknown_kinds": unknown_kinds,
        "n_class_unknown_mvp": n_class_unknown_mvp,
        "n_class_mapped_by_alias": n_class_mapped_by_alias,
        "reject_by_reason": dict(ctx.reject_by_reason),
        "event_type_vocab": list(EVENT_TYPE_VOCAB), "mvp_event_types": list(MVP_EVENT_TYPES),
        "scope_out_vocab": list(SCOPE_OUT_VOCAB),
    }
    bad = {k: n for k, n in checks.items() if n}
    merged: dict[str, object] = {**checks, **metrics}
    if not bad:
        return GateResult("EG3_corp_event", GateStatus.PASS,
                          "기업행위 어휘·dedup 축·범위 불변식 성립", merged)
    return GateResult("EG3_corp_event", GateStatus.FAIL,
                      "; ".join(f"{k}={n}" for k, n in sorted(bad.items())), merged)


eg3_corp_event.gate_name = "EG3_corp_event"     # type: ignore[attr-defined]

# ── S19 필드 선언 (DESIGN §4-7 · FIELD_MAP §2 `event.buyback_amount`) ────────
# **두 필드 다 MVP 4유형(split·reverse_split·bonus·capred) 밖이라 지금은 행이 0 이다** — 원천
# (`stg_event_tsstk_aq` 1,951 · `piic` 5,538 · `cvbd_is` 5,386)은 실재하지만 `corp_event` 가 아직
# 적재하지 않는다(DESIGN §4-2 "나머지 9종의 행은 만들지 않는다"). 선언을 지우지 않고 남기는 이유는
# S19 가 커버율 0 을 재고 S20 EG10 이 그것을 blocked(no_observations)로 드러내게 하기 위해서다 —
# 선언이 없으면 "필드가 없다" 와 "필드는 있는데 값이 없다" 가 구별되지 않는다(GATES §6 EG10 의 왜).
# 랙 1 세션: 원천이 전부 stage `lag_known=false` 이고 공시는 장중·장후 어느 쪽이든 접수된다.
FIELDS_CORP_EVENT: tuple[FieldProfile, ...] = (
    FieldProfile(
        field_id="event.buyback_amount", columns=("amount_krw",), label="자사주 취득 결정 금액",
        unit="KRW", value_type="amount", frequency="event", recommended_lag_sessions=1,
        recommended_lag_days=1, point_in_time=True, requires_confirmation=False,
        disclosure_basis="자기주식취득결정 공시 접수일(rcept_dt)",
        evidence="corp_event.amount_krw WHERE event_type='treasury_buy' ← stg_event_tsstk_aq. "
                 "S19 실측 커버율 0 — MVP 적재 범위 밖(S05 후속). 사업보고서 확정치 축은 "
                 "event.treasury_acquired(S16)로 축·시점이 다르다.",
        coverage_axis="table_rows", row_filter="event_type = 'treasury_buy'"),
    FieldProfile(
        field_id="event.capital_raise_amount", columns=("amount_krw",),
        label="유상증자·CB 발행 금액", unit="KRW", value_type="amount", frequency="event",
        recommended_lag_sessions=1, recommended_lag_days=1, point_in_time=True,
        requires_confirmation=False,
        disclosure_basis="유상증자결정·전환사채발행결정 공시 접수일(rcept_dt)",
        evidence="equity 내부 스코프(레지스트리 42 밖). FACTORS 정본 E06 의 재료이고 "
                 "corp_event.amount_krw WHERE event_type IN ('rights','cb_issue') 다. "
                 "S19 실측 커버율 0 — MVP 적재 범위 밖.",
        coverage_axis="table_rows", scope="internal",
        row_filter="event_type IN ('rights', 'cb_issue')"),
)


CORP_EVENT = register(EquityTable(
    name="corp_event",
    grain=("event_id",),
    columns={"event_id": "VARCHAR", "ticker": "VARCHAR", "corp_code": "VARCHAR",
             "event_type": "VARCHAR", "announce_date": "DATE", "effective_date": "DATE",
             "effective_basis": "VARCHAR", "ratio": "DOUBLE", "amount_krw": "BIGINT",
             "rcept_no": "VARCHAR", "source": "VARCHAR", "n_src_rows": "BIGINT",
             "available_date": "DATE", "available_basis": "VARCHAR"},
    inputs=("stg_event_fric", "stg_event_pifric", "stg_event_cr", "stg_capital",
            "stg_listing_daily", "corp_ticker", "trading_calendar", "security_span"),
    partition_class="receipt_axis",
    # receipt 축이지만 키 식은 year(announce_date) — KRX 파생행은 rcept_no 가 없고, DART 행은
    # rcept_no 앞 4자리 = 접수연도 = year(rcept_dt) 라 같은 값이다 (DESIGN §4-2).
    partition_key_expr="year(announce_date)",
    available_rule="column:announce_date — DART rcept_dt(derived) / KRX 관측일(default)",
    eg1_lhs_sql=EG1_LHS_SQL,
    eg1_rhs_sql=EG1_RHS_SQL,
    sql_path=SQL_PATH,
    input_columns={
        "stg_event_fric": ("rcept_no", "corp_code", "nstk_asstd", "nstk_ascnt_ps_ostk_ratio",
                           "nstk_ascnt_ps_estk_ratio", "nstk_ostk_cnt", "nstk_estk_cnt",
                           "bfic_tisstk_ostk", "available_date", "available_basis"),
        "stg_event_pifric": ("rcept_no", "corp_code", "fric_nstk_asstd",
                             "fric_nstk_ascnt_ps_ostk_ratio", "fric_nstk_ascnt_ps_estk_ratio",
                             "fric_nstk_ostk_cnt", "fric_nstk_estk_cnt", "fric_bfic_tisstk_ostk",
                             "available_date", "available_basis"),
        "stg_event_cr": ("rcept_no", "corp_code", "cr_std", "bfcr_tisstk_ostk",
                         "atcr_tisstk_ostk", "cr_rt_ostk_pct", "bfcr_tisstk_estk",
                         "atcr_tisstk_estk", "cr_rt_estk_pct", "crstk_estk_cnt",
                         "available_date", "available_basis"),
        "stg_capital": ("rcept_no", "corp_code", "isu_dcrs_de", "isu_dcrs_stle",
                        "isu_dcrs_stock_knd", "available_date", "available_basis"),
        "stg_listing_daily": ("ticker", "date", "par_value_krw", "list_shrs",
                              "available_basis"),
        "corp_ticker": ("ticker", "isin8", "corp_code", "is_common"),
        "trading_calendar": ("date", "prev_td"),
        "security_span": ("ticker", "first_date")},
    available_basis=("derived", "default"),
    content_date_column="announce_date",
    reject_reasons=REJECT_REASONS,
    consts=("effective_before_announce_max_days", "krx_share_change_tol"),
    extra_gates=(eg3_corp_event,),
    field_profiles=FIELDS_CORP_EVENT,
))

TABLES: tuple[EquityTable, ...] = (CORP_EVENT,)

BASELINE_SEED = Path(__file__).parent / "baseline_seed_s05.json"
"""이 슬라이스가 요구하는 상수의 초기값(절단본 실측). 승인 뒤 `baseline.json` 에 병합한다."""

__all__ = ["BASELINE_SEED", "CANONICAL_KINDS", "COMMON_KINDS", "CORP_EVENT", "EVENT_TYPE_VOCAB",
           "KNOWN_KINDS", "MVP_EVENT_TYPES", "PREFERRED_KINDS", "RATIO_ABOVE_ONE",
           "RATIO_BELOW_ONE", "RAW_SOURCE_ROWS", "SCOPE_OUT_VOCAB", "SOURCES", "TABLES",
           "UNLISTED_KINDS", "pool_sql"]
