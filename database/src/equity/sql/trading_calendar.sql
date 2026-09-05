-- trading_calendar (S02) — 거래일 축. DESIGN v1.2 §4-1.
-- 캘린더 = stg_index_daily 의 distinct date 가 전부다(2010-01-04 ~ backfill_end).
-- gap 축 없음 — 09-05 실측 P16: backfill_end 이후 date 를 가진 stage 팩트는 stg_master_daily
-- 스냅샷뿐이고 flow_split·credit·short_kis·loan_kis 는 그보다 앞에서 끝난다. 스냅샷 날짜를
-- 거래일로 승격하면 전 격자가 그 날짜만큼 늘어난다(격자 결손 = 레짐 편향).
-- 하한·상한과 체인 무결성은 EG17 이 baseline 상수로 검사한다 — 본문에 날짜를 쓰지 않는다.
-- prev_td/next_td 는 캘린더 안에서만 정의된다: 첫날 prev NULL, 마지막날 next NULL.
SELECT
    d.date                              AS date,
    lag(d.date) OVER (ORDER BY d.date)  AS prev_td,
    lead(d.date) OVER (ORDER BY d.date) AS next_td,
    NULL::VARCHAR                       AS reject_reason
FROM (SELECT DISTINCT date FROM stg_index_daily) d
