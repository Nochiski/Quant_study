-- opinion_daily (S18) — 애널리스트 컨센서스 의견·목표가. DESIGN v1.2 §4-6 · GATES v1.0 §3 ⑳.
-- grain (ticker, obs_date, src). 상보 결합이므로 `src` 가 PK 에 들어간다(DESIGN §3 골격) —
-- 절단본에서 (ticker, obs_date) 가 두 원천에 다 있는 키가 5 이고, 그 5 는 값까지 같다(§10 P37).
-- 그래도 dedup 하지 않는다: 어느 원천이 준 관측인지가 PIT 축(아래)이 서로 다르기 때문이다.
--
-- 원천 두 축:
--   wise = stg_analyst_summary — WISE 화면 c1010001 을 매일 긁은 append_only 스냅샷.
--          obs_date = fetched_date(= 우리가 실제로 긁은 날, stage lag_known=true) 라
--          available_basis 'measured'.
--   v3   = stg_v3_analyst_opinions — kael-system-v3 DB 의 동결 미러(first_write_wins).
--          obs_date = date(= v3 가 붙인 스냅샷 라벨)이고 **수집 시각 컬럼이 없다** —
--          stg_v3_revision_daily 의 collected_date 에 해당하는 컬럼이 이 테이블에는 아예 없다.
--          그래서 DESIGN §4-6 의 v3 규약(collected_date NULL → date 대용 + coverage_degraded)을
--          전 행에 적용한다: available_date = obs_date · basis 'default' · coverage_degraded.
--          basis 를 stage 처럼 'measured' 로 두면 "잰 수집일" 을 참칭하게 된다(결측은 결측).
--
-- 판본 선택(DESIGN §4-6 "덮어쓰기 원천은 최초 관측만"):
--   v3 는 매일 덮어쓰는 원장의 사본이라 같은 (ticker, date) 가 미러 스냅샷마다 다시 실릴 수 있다.
--   그 그룹에서 **observed_date 가 가장 이른 행 하나**만 남긴다(= stage write_mode
--   first_write_wins 를 equity 에서 다시 못박는 축). 동률은 투영 컬럼 순서로 깨서 재현성(EG5a)을
--   지킨다. 접힌 원천 행 수는 n_src_rows 로 싣는다(프레임이 _meta.n_dedup 을 0 으로 고정하므로
--   corp_event 규약을 따른다, GATES §9 S05). wise 는 (ticker, fetched_date) 가 stage key_unique 라
--   n_src_rows = 1 이고, 아니라면 EG3-P01 이 키 중복으로 먼저 폐기한다.
--   최초 관측 선택은 EG6 가 stage 에서 독립 재계산해 확인한다(항진명제 금지, GATES §5-C5).
--
-- 값 컬럼 대응(두 원천의 실명이 다르다 — DESCRIBE 로 확인한 stage 실명):
--   opinion_score    ← wise opinion_score        · v3 opinion_score
--   target_price_krw ← wise target_price_krw     · v3 target_price_krw
--   eps_krw          ← wise eps_krw              · v3 estimated_eps
--   per              ← wise per                  · v3 estimated_per
--   analyst_count    ← wise analyst_count        · v3 analyst_count
--   base_date·no_opinion_note 는 wise 에만 있는 축이라 v3 는 NULL(결측은 결측, 원칙 ④).
--   등급 어휘(buy/hold/sell/other)는 브로커별 원문이 있는 opinion_broker_daily 의 축이다 —
--   여기 opinion_score 는 WISE 가 이미 접은 점수라 임계로 등급을 굽지 않는다(§1 금지).
--
-- 격리(EG7 격리형, 행을 버리지 않고 _reject/<reason>/ 으로):
--   nonpositive_target_price = target_price_krw <= 0 (NULL 은 위반이 아니다 — 결측은 결측)
--   base_date_after_obs      = base_date > obs_date (관측일 역전: 긁은 날보다 뒤의 기준일 =
--                              PIT 위반. 살려 두면 EG2-P02 가 테이블 전체를 폐기한다)
WITH wise AS (
    SELECT
        s.ticker                                     AS ticker,
        s.fetched_date                               AS obs_date,
        'wise'                                       AS src,
        s.opinion_score                              AS opinion_score,
        s.target_price_krw                           AS target_price_krw,
        s.eps_krw                                    AS eps_krw,
        s.per                                        AS per,
        s.analyst_count                              AS analyst_count,
        s.base_date                                  AS base_date,
        s.no_opinion_note                            AS no_opinion_note,
        1                                            AS n_src_rows,
        s.observed_date                              AS observed_date,
        FALSE                                        AS coverage_degraded,
        s.fetched_date                               AS available_date,
        'measured'                                   AS available_basis
    FROM stg_analyst_summary s
),
v3_ranked AS (
    SELECT
        o.ticker                                     AS ticker,
        o.date                                       AS obs_date,
        o.opinion_score                              AS opinion_score,
        o.target_price_krw                           AS target_price_krw,
        o.estimated_eps                              AS eps_krw,
        o.estimated_per                              AS per,
        o.analyst_count                              AS analyst_count,
        o.observed_date                              AS observed_date,
        count(*) OVER (PARTITION BY o.ticker, o.date)            AS n_src_rows,
        row_number() OVER (
            PARTITION BY o.ticker, o.date
            ORDER BY o.observed_date NULLS LAST,
                     o.opinion_score NULLS LAST,
                     o.target_price_krw NULLS LAST,
                     o.estimated_eps NULLS LAST,
                     o.estimated_per NULLS LAST,
                     o.analyst_count NULLS LAST)                 AS rn
    FROM stg_v3_analyst_opinions o
),
v3 AS (
    SELECT
        r.ticker                                     AS ticker,
        r.obs_date                                   AS obs_date,
        'v3'                                         AS src,
        r.opinion_score                              AS opinion_score,
        r.target_price_krw                           AS target_price_krw,
        r.eps_krw                                    AS eps_krw,
        r.per                                        AS per,
        r.analyst_count                              AS analyst_count,
        CAST(NULL AS DATE)                           AS base_date,
        CAST(NULL AS VARCHAR)                        AS no_opinion_note,
        r.n_src_rows                                 AS n_src_rows,
        r.observed_date                              AS observed_date,
        TRUE                                         AS coverage_degraded,
        r.obs_date                                   AS available_date,
        'default'                                    AS available_basis
    FROM v3_ranked r
    WHERE r.rn = 1
),
u AS (
    SELECT * FROM wise
    UNION ALL
    SELECT * FROM v3
)
SELECT
    u.ticker,
    u.obs_date,
    u.src,
    u.opinion_score,
    u.target_price_krw,
    u.eps_krw,
    u.per,
    u.analyst_count,
    u.base_date,
    u.no_opinion_note,
    u.n_src_rows,
    u.observed_date,
    u.coverage_degraded,
    u.available_date,
    u.available_basis,
    CASE WHEN u.target_price_krw <= 0 THEN 'nonpositive_target_price'
         WHEN u.base_date > u.obs_date THEN 'base_date_after_obs'
    END                                              AS reject_reason
FROM u
