-- dividend_event (S16) — 정기보고서 「배당에 관한 사항」. DESIGN v1.2 §4-5 · GATES §3-⑱.
-- grain (corp_code, bsns_year, reprt_code, stock_knd) · receipt_axis · available_date = 접수일.
--
-- 모집단 = `stg_dividend` 전건. **`row_kind` 열이 없다**(stage `rules_dart.py` 는 hyslr·shares·
-- tesstk 3개에만 붙인다) — GATES §3-⑱ 우변의 `WHERE row_kind IS DISTINCT FROM 'aggregate'` 는
-- 실행 불가라 술어를 뺀 형태가 정본이다(§5-B5 · §9 S16 정정). 아래 마커 줄 앞까지가 모집단
-- 정의이고 `rules_s16.py:pool_sql` 이 EG1 우변으로 재사용한다.
--
-- **`se` wide → 컬럼 전개**: 원장은 (grain × 라벨) 롱 포맷이고 값은 `thstrm`(당기)이다.
-- 라벨 대응표는 `rules_s16.py` 의 튜플이 정본이고 아래 리터럴이 그것을 그대로 옮긴다(tests 대조).
--   dps_krw        '주당 현금배당금(원)'
--   cash_total_krw '현금배당금총액(백만원)' × `_const.cash_total_unit_krw`(백만원 → 원)
--   yield_pct      '현금배당수익률(%)'
--   payout_pct     '(연결)현금배당성향(%)' → '(별도)' → '(개별)' → 무접두 순, 출처는 payout_basis
-- 법인 축 라벨(총액·성향·순이익)은 원장에서 `stock_knd='-'` 로 실리므로 그 값은 **'-' 행에만**
-- 실린다. 보통주·우선주 행으로 퍼뜨리지 않는다 — grain 을 넘는 값 복제는 층 계약 밖이고(§1),
-- 소비자는 같은 (법인, 사업연도, 보고서)의 '-' 행에서 읽는다.
--
-- **기준일·락일 없음**(DESIGN §4-5). 그래서 `corp_event` 에 `cash_dividend` 행을 만들지 않고
-- 배당락 축도 내지 않는다 — 배당락 원천이 없어 TR 계산이 불가하다는 §11 한계 그대로다.
-- 절단본 `stg_disclosure` 에 현금·현물배당결정 공시 0건(실측)이라 결정공시 경유 보완도 없다.
--
-- **판본 중복**: (grain, se) 가 되풀이될 수 있다 — 절단본 60그룹, 전부 `stock_knd='-'` 이고
-- 한쪽만 값이 있다(SK하이닉스 2016 주당 현금배당금 600 / NULL). 값 있는 행을 먼저 집도록
-- `thstrm IS NULL` 을 정렬 첫 축에 두고, 그 뒤는 S11 first_write_wins(`observed_date` →
-- `rcept_no`)다. 값이 둘 다 있고 서로 다른 그룹은 EG3 기록형 `n_value_conflict` 가 센다
-- (절단본 0 — 승격 판단은 서버 실측 뒤).
-- 한 grain 에 접수가 둘 이상이면(원본 + 정정 재제출) 라벨마다 판본이 갈릴 수 있다: 값 축은
-- 위 규칙대로 라벨별로 고르고, **식별 축(`rcept_no`·`stlm_dt`·`available_date`)은 `max`** 로
-- 기여한 접수 중 가장 나중 것을 싣는다 — PIT 은 어느 기여 행보다도 이르지 않아야 한다.
-- 그런 grain 수는 EG3 기록형 `n_grain_multi_rcept` 가 센다(절단본 0).
WITH src AS (
    SELECT corp_code,
           bsns_year,
           reprt_code,
           stock_knd,
           se,
           rcept_no,
           stlm_dt,
           thstrm,
           observed_date,
           available_date,
           available_basis,
           count(*) OVER (PARTITION BY corp_code, bsns_year, reprt_code, stock_knd) AS n_src_rows
    FROM stg_dividend
)
-- ==== eg1: 모집단 정의 끝. 위 CTE 만으로 EG1 우변이 선다(rules_s16.py:pool_sql) ====
, picked AS (
    SELECT * FROM src
    QUALIFY row_number() OVER (
        PARTITION BY corp_code, bsns_year, reprt_code, stock_knd, se
        ORDER BY thstrm IS NULL, observed_date NULLS LAST, rcept_no NULLS LAST,
                 thstrm NULLS LAST) = 1
)
SELECT
    p.corp_code,
    p.bsns_year,
    p.reprt_code,
    p.stock_knd,
    max(p.rcept_no)                                              AS rcept_no,
    max(p.stlm_dt)                                               AS stlm_dt,
    max(p.thstrm) FILTER (p.se = '주당 현금배당금(원)')           AS dps_krw,
    max(p.thstrm) FILTER (p.se = '현금배당금총액(백만원)')
        * max(k.cash_total_unit_krw)                             AS cash_total_krw,
    max(p.thstrm) FILTER (p.se = '현금배당수익률(%)')             AS yield_pct,
    coalesce(max(p.thstrm) FILTER (p.se = '(연결)현금배당성향(%)'),
             max(p.thstrm) FILTER (p.se = '(별도)현금배당성향(%)'),
             max(p.thstrm) FILTER (p.se = '(개별)현금배당성향(%)'),
             max(p.thstrm) FILTER (p.se = '현금배당성향(%)'))     AS payout_pct,
    CASE WHEN max(p.thstrm) FILTER (p.se = '(연결)현금배당성향(%)') IS NOT NULL
              THEN 'consolidated'
         WHEN max(p.thstrm) FILTER (p.se = '(별도)현금배당성향(%)') IS NOT NULL
              THEN 'separate'
         WHEN max(p.thstrm) FILTER (p.se = '(개별)현금배당성향(%)') IS NOT NULL
              THEN 'individual'
         WHEN max(p.thstrm) FILTER (p.se = '현금배당성향(%)') IS NOT NULL
              THEN 'unlabeled' END                               AS payout_basis,
    max(p.n_src_rows)                                            AS n_src_rows,
    max(p.available_date)                                        AS available_date,
    max(p.available_basis)                                       AS available_basis,
    CASE WHEN max(p.available_date) IS NULL THEN 'rcept_dt_missing' END AS reject_reason
FROM picked p
CROSS JOIN _const k
GROUP BY ALL
