# stage 절단본 (equity 로컬 TDD 용)

서버 `~/quant-ledger/data/stage/` 의 current_build 에서 잘라낸 소형 parquet 트리. MANIFEST.json 골격(`current_build`·`builds[].partitions[].path`)은 실물과 같고, 파티션 경로는 `v=<build>/year=YYYY/*.parquet` 또는 `v=<build>/part0.parquet` 다.

- 티커 15: 005930·005935(삼성전자 본주·우선주, 2018-05-04 50:1 분할) · 000660 · 003540·003545·003547(대신증권 1:N) · 036220·101970(재상장 2종) · 900050·900060(HK ISIN 단독 corp) · 000030(우리은행, `stg_delisted_master` 보유) · 069500(ETF) · 0001A0(문자 티커) · 161890·247540
- 테이블 11: stg_listing_daily(36,972) · stg_etf_price_daily(4,094) · stg_delisted_master(3) · stg_corp_map(11) · stg_company(11) · stg_index_daily(코스피·코스닥·코스피 200·코스닥 150, 16,376) · stg_price_daily(36,972) · stg_master_daily(24) · stg_disclosure(정기보고서·거래정지·관리·정리매매·상폐·락일 676) · stg_flow_split_daily(2026-08-20 이후 0행 — gap 축은 credit 만) · stg_credit_daily(24,722)
- 생성: 2026-09-05, 스크립트는 세션 스크래치(`slice_stage.py`) — 재생성 시 티커·where 절을 위 목록으로.
