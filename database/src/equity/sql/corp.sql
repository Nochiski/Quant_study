-- corp (S01) — grain corp_code. 모집단은 stg_corp_map, 속성은 stg_company 현재 스냅샷.
-- DESIGN v1.2 §4-1 · GATES §3-①. 상수는 _const 로만 (financial_ksic_prefix = KSIC 대분류 앞 2자리).
-- stg_company 는 append_only(key_unique=False)라 corp_code 당 판본이 여럿일 수 있다 —
-- observed_date 최신 1행만 쓰고, 동률은 투영 컬럼 순서로 깨서 재현성(EG5a)을 지킨다.
WITH company AS (
    SELECT corp_code, acc_mt, induty_code_current
    FROM stg_company
    QUALIFY row_number() OVER (
        PARTITION BY corp_code
        ORDER BY observed_date DESC NULLS LAST,
                 acc_mt NULLS LAST,
                 induty_code_current NULLS LAST) = 1
),
corp_map AS (
    -- corp_code 당 1행 계약(stage key_unique)이지만 EG1 좌변이 우변(count DISTINCT corp_code)과
    -- 반드시 같도록 집계로 못박는다.
    SELECT corp_code, min(corp_name_current) AS corp_name
    FROM stg_corp_map
    GROUP BY corp_code
)
SELECT
    m.corp_code                                              AS corp_code,
    m.corp_name                                              AS corp_name,
    TRY_CAST(c.acc_mt AS INTEGER)                            AS fiscal_month,
    'current_snapshot'                                       AS fiscal_month_basis,
    nullif(trim(c.induty_code_current), '')                  AS induty_code,
    CASE
        WHEN nullif(trim(c.induty_code_current), '') IS NULL THEN NULL
        WHEN list_contains(str_split(k.financial_ksic_prefix, ','),
                           substr(trim(c.induty_code_current), 1, 2)) THEN 'financial'
        ELSE 'nonfinancial'
    END                                                      AS induty_class,
    NULL::VARCHAR                                            AS reject_reason
FROM corp_map m
LEFT JOIN company c ON c.corp_code = m.corp_code
CROSS JOIN _const k
