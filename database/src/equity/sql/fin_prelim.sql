-- fin_prelim (S27) — 잠정실적 공정공시 판본(N-64 PR ②-1). grain `rcept_no` · receipt_axis(`substr(rcept_no,1,4)`).
-- 입력은 stage 2표: `stg_doc_prelim`(서식 표 값, 파서 p1.7) · `stg_disclosure`(접수 목록 — 종목 · 접수일 · 정정 여부).
--
-- 모집단 = `stg_doc_prelim` 접수번호 전부. 행을 버리지 않고 `prelim_status` 로 가른다 — 월 실적 · 미래 기말 ·
-- 파싱 실패도 남겨야 fi 가 거른 꼴을 셀 수 있다(PG-10). 격리(`_reject`)로 보내면 EG7(0.1%)에 걸리고 집계 재료가 사라진다.
--   ok             당기가 3개월(월초 ~ 월말)이고 기말일 ≤ 접수일
--   not_quarter    당기가 3개월이 아님(백화점 월 실적 · 반기 누계 등)
--   future_period  기말일 > 접수일(기간을 잘못 적은 서식 — 쓰면 아직 안 끝난 분기가 열린다)
--   parse_failed   stage status=failed(단위 · 숫자를 못 읽음 — 값 열은 NULL)
--   no_disclosure  접수 목록에 없음(종목 · 접수일을 모름)
-- 정정은 판본 행을 모두 둔다. 기준일 D 의 판 고르기(늦은 접수가 이김, Q4)는 fi 가 `available_date <= D` 에서 한다.
--
-- PIT: `available_date` = 두 입력 공개일 중 늦은 쪽(derived). 둘 다 접수번호 기준 stage 공개일이다
-- (stg_doc_prelim = stg_rcept_dt_map 조회, stg_disclosure = 원천 rcept_dt 와 접수번호 날짜 중 늦은 쪽).
WITH disc AS (
    SELECT d.rcept_no, d.rcept_dt, d.available_date, d.corp_code, d.ticker, d.is_correction
    FROM stg_disclosure d
    -- append_only 라 같은 접수번호가 재수집 판본만큼 있다 — disclosure_version 과 같은 first_write_wins
    QUALIFY row_number() OVER (
        PARTITION BY d.rcept_no
        ORDER BY d.observed_date NULLS LAST, d.rcept_dt NULLS LAST, d.corp_code NULLS LAST,
                 d.ticker NULLS LAST, d.is_correction NULLS LAST) = 1
),
doc AS (
    SELECT p.*
    FROM stg_doc_prelim p
    -- 접수번호 하나에 서식 멤버가 둘이면(실측 877건 중 0) ok 멤버, 그다음 멤버 이름 순
    QUALIFY row_number() OVER (
        PARTITION BY p.rcept_no ORDER BY (p.status = 'ok') DESC, p.member_name) = 1
)
SELECT
    p.rcept_no,
    d.corp_code,
    d.ticker,
    d.rcept_dt,
    d.is_correction,
    p.fs_div,
    p.period_from,
    p.period_to,
    p.py_from,
    p.py_to,
    p.unit_raw,
    p.revenue_krw,
    p.op_profit_krw,
    p.pretax_krw,
    p.net_income_krw,
    p.ni_owner_krw,
    p.revenue_py_krw,
    p.op_profit_py_krw,
    p.net_income_py_krw,
    CASE
        WHEN p.status <> 'ok' THEN 'parse_failed'
        WHEN d.rcept_no IS NULL THEN 'no_disclosure'
        -- 3개월 = 월초에 시작해 그 달 + 2개월의 말일에 끝난다(분기 · 3월 결산의 회계 분기 모두)
        WHEN p.period_from IS NULL OR p.period_to IS NULL
             OR p.period_from <> date_trunc('month', p.period_from)
             OR p.period_to <> last_day(p.period_from + INTERVAL 2 MONTH) THEN 'not_quarter'
        WHEN p.period_to > d.rcept_dt THEN 'future_period'
        ELSE 'ok'
    END AS prelim_status,
    p.error AS parse_error,
    greatest(coalesce(d.available_date, p.available_date),
             coalesce(p.available_date, d.available_date)) AS available_date,
    'derived' AS available_basis,
    CASE WHEN coalesce(d.available_date, p.available_date) IS NULL THEN 'available_date_missing' END
        AS reject_reason
FROM doc p
LEFT JOIN disc d ON d.rcept_no = p.rcept_no
