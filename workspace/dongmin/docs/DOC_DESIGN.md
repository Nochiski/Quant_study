# 문서층(L1) 설계 v1.1 — DART 보고서 ZIP 을 stage 의 여섯 번째 원장 소스로 (2026-09-04)

> `DOC_LAYER_KICKOFF.md` 의 L1-0 산출물 v1(09-03, 검수 2회)을 **stage 규약으로 재배치**한 판. 바뀐 것: 산출이 `data/doc/` 별도 층이 아니라 `data/stage/stg_doc_*` 7테이블(§3), 빌더는 `src/stage/` blob 경로 재사용(§2), 정정→원본 링크는 행 간 조인이라 stage 밖 equity 명세로 이관(§8), 서식표 셀·자유표 좌표 테이블 신설, 어휘 사전 4종과 G12·G13 추가.
> 순서: **실측(§1) → stage 계약(§2) → 테이블(§3) → 게이트(§4) → 결정(§5) → 단계(§6) → 기록(§7) → equity 인계(§8)**. 원칙은 `STAGE_DESIGN.md` §1·§2·§3·§9 그대로: 원문 불변 · 1 원장 파일 → N 행 · 행 간 조인 없음 · 공통 골격 · MANIFEST 경유 parquet · 게이트 · 재현성 · 실측 기록. 표본 유래 수치는 표본 크기와 술어를 병기한다(교훈 ④·⑤).

## 0. 범위

**전체 구조**: 원장(sqlite 5 + **보고서 ZIP**) → stage(parquet, 소스별 테이블, 조인 없음) → equity(duckdb 로 조인·정본·PIT) → factor. 보고서는 원장의 여섯 번째 소스 `doc` 이고 같은 길을 따른다. "stage.db" 는 없다 — stage 는 `data/stage/<table>/v=<build>/year=YYYY/*.parquet` 62개(+7) 디렉토리이고 duckdb 는 엔진이다.

**포함(stage)**: `doc_store.zip_ok=1` 171,179건의 본문·첨부 XML 에서 **원문이 스스로 구조를 선언한 것만** 긁는다 — ① 헤더·표지·`SUMMARY`, ② 서식표의 `TE`/`TU` 코드 셀, ③ `{XBRL}` 재무표, ④ 목차·정정 첫 장·자유표의 **좌표와 헤더**(값은 아님). 산출 7테이블은 §3.
**stage 밖(사유 명기)**:
- **정정→원본 링크** — 같은 회사·종류·기간의 다른 접수와 조인해야 하므로 stage 금지(§1 원칙). stage 는 ZIP 이 있는 정정 문서의 첫 장 원문(`stg_doc_correction`, ZIP 1 → 행 ≤1)만 싣고, **모집단(원장 정정 접수 전건 20,579, §1.7)·링크 규칙·게이트는 §8.1 로 equity 에 인계** — equity 가 `stg_disclosure` 술어로 20,579 를 만들고 `stg_doc_correction` 을 LEFT JOIN 한다. ZIP 없는 2,976건(`014` 2,975 + `doc_store` 행 없음 1)을 빠뜨리면 그 원본이 "정정 없음"으로 읽혀 restated 값이 원본 행세를 한다(silent PIT 오염) — 이 요구는 모집단을 원장에서 잡는 것으로 충족한다.
- **자유표의 값** — 편집기 표(전체 표의 72%, §1.14)는 모양이 회사·연도마다 달라 긁는 기준이 없다. stage 는 좌표·헤더·단위 문맥(`stg_doc_table`)만 싣고, 값은 군집화 + 스키마 판정(골든셋 §1.15) 뒤 별도 층(§8.3).
- **HTML 주요사항보고서**(주식분할·병합결정 1,403건 = ZIP 1,253 · `014` 150, 표본 510 중 5) — DART XML 이 아니라 `<html>`. `stg_doc_meta.format='html'` 만. 입력 `zip_ok=1` 171,179 = 정기보고서 169,929(§1.7 표) + 주요사항 1,253 − 두 술어에 겹치는 접수 3(P1 G0 에서 확정).
- **비XBRL 수기 재무표**(SPAC·2013 이전 소형사, §1.9·§1.13) — `stg_fin_asreported` 는 XBRL 그룹만. 수기표는 `n_xbrl_groups=0` + `stg_doc_table` 좌표로 존재만.
- **첨부 감사보고서 본문 텍스트**·**사업의 개요 등 문단** — P4. 헤더 `SUMMARY/EXTRACTION` 은 P1 `stg_doc_meta` 에 싣는다(§1.10).
- **일일 증분** — stage 와 동일하게 1회 풀 빌드 후 별도 설계. 접수번호 키·ZIP 불변이라 `doc_store` 신규 행만 파싱하면 되는 구조.

## 1. 실측 (L1-0)

### 1.0 표본 3종 — 추출 술어
| 표본 | 크기 | 술어 | 쓰인 곳 |
|---|---|---|---|
| S26 구조 표본 | 5년 × 5구분 = 25 + G2 보강 1 | `dart_disclosure ⋈ doc_store(zip_ok=1)`, 연도 = `substr(rcept_no,1,4)` ∈ {2010,2013,2016,2020,2024}, 구분별 `report_nm` 조건(§1.1) 후 `ORDER BY rcept_no LIMIT 1 OFFSET k`(A·B·C k=100, D k=50, E k=20/20/5/5/5, 없으면 k=0). G2(dart3 평면) 보강 = 2022 사업보고서 원본·멤버 3 중 `OFFSET 100` 부터 헤더 래퍼 없는 첫 문서 | §1.1·§1.6~§1.10, G4 픽스처 |
| Y2040 / Y510 연도 표본 | 17년 × 120 = 2,040 / 17년 × 30 = 510 | 연도 디렉토리의 ZIP 파일명(= 접수번호) 정렬 후 `stride = n // N` 등간격 앞 N건 — 연중 고르게 분포(1~2월 편향 없음) | §1.2(xsd·헤더·HTML 은 앞 4KB 정규식 = Y2040, 바이트 인코딩·태그·엔티티·파싱은 전문 = Y510) |
| C340 정정 표본 | 17년 × 20 = 340 | `dart_disclosure ⋈ doc_store(zip_ok=1)`, `report_nm LIKE '[%정정]%'` AND 정기보고서 3종, 연도별 `ORDER BY rcept_no` 후 `stride = n // 20` 등간격 20건 | §1.7, D6·E-G6a·E-G7 초기 임계 |

### 1.1 S26 — 결과
구분: A 사업보고서 원본(ZIP 멤버 3개) · B 분기 원본(멤버 1개) · C 반기 원본 · D [기재정정]사업보고서 · E 연도별 특수형([첨부추가]·[기재정정]반기·주식분할결정·[첨부정정]·주식분할결정) · G2 보강(2022 A). 회사 규모·시장을 고르지 않고 접수번호 순서로 뽑았다.

| 연도 | 구분 | rcept_no | 회사(시장) | report_nm | 멤버 | 바이트 | xsd·헤더 | ACODE | FV | 목차 | 정정장 | XBRL 그룹 | 정제(&,<) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2010 | A | 20100629000280 | 케이비증권(E) | 사업보고서 (2010.03) | #.xml+#_00760.xml+#_00761.xml | cp949 | dart3·hdr | 11011 | 1.1 | 28 | 0 | 5 | 4,1 |
| 2010 | B | 20100414000675 | 아시아퍼시픽13호선박투자회사(E) | 분기보고서 (2010.02) | #.xml | cp949 | dart3·hdr | 11013 | 1.1 | 28 | 0 | 5 | 6,1 |
| 2010 | C | 20100811000066 | 삼익THK(Y) | 반기보고서 (2010.06) | #.xml | cp949 | dart3·hdr | 11012 | 1.1 | 28 | 0 | 5 | 0,0 |
| 2010 | D | 20100331002629 | 웹케시(K) | [기재정정]사업보고서 (2009.12) | #.xml | cp949 | dart3·hdr | 11011 | 1.1 | 30 | 1 | 0 | 4,19 |
| 2010 | E | 20100326000630 | 제이엠아이(K) | [첨부추가]사업보고서 (2009.12) | #_00760.xml+#_00761.xml+#.xml | cp949 | dart3·hdr | 11011 | 1.1 | 28 | 0 | 5 | 0,2 |
| 2013 | A | 20130329000019 | 에프엔에스테크(K) | 사업보고서 (2012.12) | #_00760.xml+#.xml+#_00761.xml | cp949 | dart3·hdr | 11011 | 2.0 | 28 | 0 | 0 | 1,10 |
| 2013 | B | 20130507000729 | 세아특수강(E) | 분기보고서 (2013.03) | #.xml | cp949 | dart3·hdr | 11013 | 2.0 | 28 | 0 | 6 | 8,62 |
| 2013 | C | 20130813000100 | 미창석유공업(Y) | 반기보고서 (2013.06) | #.xml | cp949 | dart3·hdr | 11012 | 2.0 | 28 | 0 | 4 | 5,0 |
| 2013 | D | 20130401003224 | 대림제지(K) | [기재정정]사업보고서 (2012.12) | #.xml | cp949 | dart3·hdr | 11011 | 2.0 | 30 | 1 | 4 | 0,2 |
| 2013 | E | 20130814001633 | 메디아나(K) | [기재정정]반기보고서 (2013.06) | #.xml | cp949 | dart3·hdr | 11012 | 2.0 | 30 | 1 | 0 | 4,0 |
| 2016 | A | 20160329000533 | 부산산업(Y) | 사업보고서 (2015.12) | #_00761.xml+#_00760.xml+#.xml | cp949 | dart3·hdr | 11011 | 2.8 | 32 | 0 | 8 | 0,19 |
| 2016 | B | 20160511001817 | 디알텍(K) | 분기보고서 (2016.03) | #.xml | cp949 | dart3·hdr | 11013 | 2.9 | 32 | 0 | 0 | 8,5 |
| 2016 | C | 20160811000095 | 에스코넥(K) | 반기보고서 (2016.06) | #.xml | cp949 | dart3·hdr | 11012 | 2.9 | 32 | 0 | 8 | 1,82 |
| 2016 | D | 20160330004609 | 사조해표(E) | [기재정정]사업보고서 (2015.12) | #.xml | cp949 | dart3·hdr | 11011 | 2.8 | 34 | 1 | 8 | 10,1 |
| 2016 | E | 20160223800353 | 넥센(Y) | 주식분할결정 | #.xml | cp949 | html | — | — | — | — | — | —,— |
| 2020 | A | 20200327001141 | 써니전자(Y) | 사업보고서 (2019.12) | #_00760.xml+#_00761.xml+#.xml | cp949 | dart3·hdr | 11011 | 3.9 | 32 | 0 | 8 | 6,72 |
| 2020 | B | 20200512000721 | 유진테크(K) | 분기보고서 (2020.03) | #.xml | cp949 | dart3·hdr | 11013 | 3.9 | 32 | 0 | 10 | 11,1 |
| 2020 | C | 20200812000160 | LIG아큐버(K) | 반기보고서 (2020.06) | #.xml | cp949 | dart3·hdr | 11012 | 4.1 | 32 | 0 | 8 | 21,3 |
| 2020 | D | 20200330000895 | 위세아이텍(K) | [기재정정]사업보고서 (2019.12) | #.xml | cp949 | dart3·hdr | 11011 | 3.9 | 34 | 1 | 4 | 3,4 |
| 2020 | E | 20200330004604 | 한국테크놀로지(E) | [첨부정정]사업보고서 (2019.12) | #_00761.xml | cp949 | dart3·hdr | 00761 | 3.4 | 5 | 1 | 0 | 5,14 |
| 2022 | A(G2) | 20220311000948 | 포스코DX(Y) | 사업보고서 (2021.12) | #_00761.xml+#_00760.xml+#.xml | cp949 | dart3·flat | 11011 | 4.7 | 52 | 0 | 8 | 7,10 |
| 2024 | A | 20240313000011 | 청보(K) | 사업보고서 (2023.12) | #.xml+#_00760.xml+#_00761.xml | utf-8 | dart4·flat | 11011 | 5.5 | 60 | 0 | 8 | 0,99 |
| 2024 | B | 20240510000724 | 마이크로컨텍솔(K) | 분기보고서 (2024.03) | #.xml | utf-8 | dart4·flat | 11013 | 5.6 | 62 | 0 | 8 | 3,3 |
| 2024 | C | 20240809000460 | 피엔티엠에스(K) | 반기보고서 (2024.06) | #.xml | utf-8 | dart4·flat | 11012 | 5.6 | 56 | 0 | 4 | 7,3 |
| 2024 | D | 20240314001248 | 메리츠금융지주(Y) | [기재정정]사업보고서 (2023.12) | #.xml | utf-8 | dart4·flat | 11011 | 5.5 | 60 | 1 | 8 | 17,213 |
| 2024 | E | 20240311901285 | 동화기업(K) | 주식분할결정 | #.xml | utf-8 | html | — | — | — | — | — | —,— |

`#` = 접수번호. 멤버는 ZIP 안 순서 그대로(본문이 첫 멤버가 아닌 경우가 흔하다). ACODE = `DOCUMENT-NAME` 코드(11011 사업·11012 반기·11013 분기·00760 감사·00761 연결감사), FV = `FORMULA-VERSION`. 목차 = `TITLE[@ATOC="Y"]` 수. 정제(&,<) = 맨 `&` 치환 수, 화이트리스트 밖 `<` 치환 수. XBRL 그룹 = 재무제표 섹션(§1.6 코드) 안 `TABLE-GROUP[@ACLASS LIKE '{XBRL}%']` 수 — 0 인 XML 문서는 2010-D·2013-A·2013-E·2016-B·2020-E 5건(정정 문서 3건은 정정 페이지+발췌만 담아 0 이 정상, 2013-A·2016-B 는 수기표 §1.9). **26건 전부 파싱 성공**(XML 24 = G1 19 · G2 1 · G3 4, HTML 2, §2.2 정제 5규칙 적용 후). G2 보강 문서(포스코DX)는 `PERIODFROM/TO` 20210101~20211231, 섹션 `D-0-3-2-0`·`D-0-3-4-0` 각 4그룹으로 G1 규칙 그대로 읽힌다 — 목차 수 52 는 G1(28~34)과 G3(56~62) 사이.

### 1.2 세대 — XML 3 + HTML 1 (Y2040 · 바이트 인코딩은 Y510 전문 디코딩)

| 연도 | ZIP 수 | 바이트 인코딩(30건) | xsd·헤더(120건) | HTML(120건) | 멤버명 `/` 접두(30건) |
|---|---|---|---|---|---|
| 2010 | 8,116 | cp949 30 | dart3·hdr 120 | 0 | 0 |
| 2011 | 8,600 | cp949 30 | dart3·hdr 118 | 2 | 0 |
| 2012 | 8,610 | cp949 30 | dart3·hdr 120 | 0 | 0 |
| 2013 | 8,746 | cp949 30 | dart3·hdr 120 | 0 | 0 |
| 2014 | 8,987 | cp949 30 | dart3·hdr 120 | 0 | 0 |
| 2015 | 8,886 | cp949 30 | dart3·hdr 118 | 2 | **30** |
| 2016 | 9,311 | cp949 30 | dart3·hdr 118 · dart3·flat 2 | 0 | **30** |
| 2017 | 9,301 | **utf-8 30** | dart3·hdr 118 | 2 | 0 |
| 2018 | 9,767 | cp949 30 | dart3·hdr 120 | 0 | 0 |
| 2019 | 9,955 | cp949 30 | dart3·hdr 118 · dart3·flat 2 | 0 | 0 |
| 2020 | 10,660 | cp949 30 | dart3·hdr 120 | 0 | 0 |
| 2021 | 11,570 | cp949 30 | dart3·hdr 94 · dart3·flat 26 | 0 | 0 |
| 2022 | 11,832 | cp949 16 · utf-8 14 | dart3·flat 119 | 1 | 0 |
| 2023 | 11,978 | utf-8 30 | dart3·flat 119 | 1 | 0 |
| 2024 | 12,298 | utf-8 30 | dart4·flat 117 · dart3·flat 2 | 1 | 0 |
| 2025 | 12,610 | utf-8 30 | dart4·flat 120 | 0 | 0 |
| 2026 | 9,952 | utf-8 30 | dart4·flat 114 | 6 | 0 |

- **G1 `dart3.xsd` + `DOCUMENT-HEADER`** (2010~2021): `DOCUMENT > DOCUMENT-HEADER{DOCUMENT-NAME, FORMULA-VERSION, COMPANY-NAME, SUMMARY/EXTRACTION} + BODY > INSERTION > LIBRARY > (CORRECTION | SECTION-2 …)`. 재무제표는 `SECTION-1/INSERTION/LIBRARY/…/TABLE-GROUP`.
- **G2 `dart3.xsd` 평면** (2021~2023): 헤더 래퍼 없이 `DOCUMENT-NAME` 등이 `DOCUMENT` 직계. 2021 은 94:26 혼재 — **연도가 아니라 문서 단위로 판정**.
- **G3 `dart4.xsd`** (2024-02~, FV 5.5~6.7): `BODY[@ATOCID]`, `TITLE[@ATOCID]` 목차 ID 추가, `INSERTION` 없이 `SECTION-1/LIBRARY/SECTION-2`, 재무제표 `TABLE-GROUP` 안에 `TITLE[@ATOC="Y"]`("2-1. 연결 재무상태표"), 목차 수 56~62(G1 28~34, G2 52). `SECTION-3`·`XII. 상세표`·사업의 내용 하위 7절(`L-0-2-n`)은 G3 가 아니라 **2021-08 서식 개정(FV 4.5)** 부터다(아래 전환표).
- **구조 전환 시점 (Y1676: 연 100건 × 접수월, `FORMULA-VERSION ADATE` = 서식 개정일)**:

| 접수월 | 개정일(FV) | 바뀐 것 | 표본 실측 |
|---|---|---|---|
| 2011-03 | 2011-02-01 (1.3) | 서식표(`TE`/`TU` 코드 셀) 대량 도입 — K-IFRS 첫 사업보고서 | 문서당 서식 그룹 중앙값 4 → 15, `TE` 60 → 544 |
| 2015-03 | 2015-03-03 (2.5) | 재무제표 위치 `XI. 재무제표 등`(D-0-11-0-0) → `III-2 연결`·`III-4 별도`(D-0-3-2-0/4-0) | 2014-12 까지 XI 0.9~1.0 → 2015-03 III 0.95 |
| 2021-08 | 2021-07-16 (4.5) | 사업의 내용 7개 하위 절(`L-0-2-n`), `SECTION-3`, `XII. 상세표`(`TTL_APPENDIX`) | 세 지표 모두 0 → 0.95 |
| 2021-11 | (같은 4.5) | `DOCUMENT-HEADER` 래퍼 제거(G1 → G2) | 헤더 없음 0 → 1.00 |
| 2024-02 | 2023-12-29 (5.5) | `dart4.xsd`, `ATOCID`(G2 → G3), 코드 셀 증가 | `TE` 중앙값 1,911 → 2,703 |
| 2025-03~ | 2024-12-31 (6.1) | 주석의 XBRL 그룹(`{XBRL}NT_*`) 점진 도입 | 문서 비율 0.12(2024-03) → 0.25~0.32(2025) → 0.43~0.50(2026) |
개정일 어휘는 2009-04-08 부터 2026-07-23 까지 40종(FV 1.0~6.7) — 세대(G1/G2/G3) 판정과 별개로 `formula_date` 를 `doc_meta` 에 싣는 이유.
- **H HTML** (전 연도 산재, 주요사항보고서): `<html><head><meta charset=euc-kr>` 선언이지만 바이트는 연도 따라 cp949/utf-8. `<title>회사/주식분할결정/(날짜)…</title>`, `<table>` 1개, `&nbsp;` 사용. 정기보고서 아님.
- FORMULA-VERSION 은 1.0(2009-04) → 6.7(2026) 단조 증가. 세대 판정 키는 (xsd, 헤더 래퍼 유무, `<html`) 3개면 충분하고 FV 는 기록만.

### 1.3 인코딩 — 헤더를 믿으면 안 된다
XML 선언은 전부 `encoding="utf-8"`, HTML 은 `charset=euc-kr`. 실제 바이트는 **cp949 2010~2016·2018~2021, utf-8 2017(!)·2023~, 2022 혼재(16:14)**. 단조가 아니므로 연도 규칙 금지 — **문서마다 바이트로 판정**: `utf-8` strict → 실패 시 `cp949` strict → 실패 시 `cp949 errors='replace'` + 치환 수 기록. Y510 전문 디코딩에서 strict 두 단계로 전부 판정됐다(치환 0). 표본 앞부분만 디코딩하면 다바이트 경계에서 오판한다(교훈 ②).

### 1.4 ZIP 멤버 — 이름으로 고른다
- 멤버명 = `{rcept_no}.xml`(본문) · `{rcept_no}_00760.xml`(감사보고서, ACODE 00760) · `{rcept_no}_00761.xml`(연결감사보고서, 00761). **2015~2016 은 `/{rcept_no}.xml` 처럼 `/` 접두** — basename 으로 매칭.
- 순서는 무작위(Y510: `#` 380 · `#|760` 34 · `#|760|761` 18 · `760|#` 17 · `#|761|760` 17 · `761|760|#` 12 · `760|761|#` 12 · … · `761` 만 3). 첫 멤버 = 본문 가정 금지(교훈 ①).
- **본문 없는 ZIP**: `[첨부정정]` 은 정정된 첨부(감사보고서)만 담고 첫 장에 `CORRECTION` 이 붙는다(S26 2020-E, Y510 중 3). 이 경우 `member_role='main'` 부재를 정상으로 기록.

### 1.5 XML 이 아닌 XML — 정제 없이는 expat 이 XML 표본 전건에서 죽는다
S26 의 XML 24건: 정제 없음 → 24건 전부 실패(`&cr;` 미정의 엔티티) · 엔티티만 치환 → 6건 실패(맨 `<`·`&`·`<CP>` 류) · "`<`+영문자 = 태그" 규칙 → 2건 실패 · 화이트리스트 + 속성 복구 → 0건.
| 현상 | 실례 | 빈도(Y510 XML 505건) | 처리 |
|---|---|---|---|
| 비표준 엔티티 `&cr;` (줄바꿈) | `<P>&cr;&cr;</P>` | 373건 | `&#10;` 치환. 그 외 엔티티는 HTML 문서의 `&nbsp;` 3건뿐 |
| 맨 `&` | `R&D`, `People & Technology` | 문서당 0~21 | `&amp;` |
| 맨 `<` — 태그 아닌 꺾쇠 | `<당기말>`, `<메리츠화재>`, `<MS AR 사업>`, `<CP>` | 문서당 0~213 | **태그 화이트리스트(§1.6) 밖이면 `&lt;`**. "`<`+영문자 = 태그" 규칙은 `<MS AR 사업>`·`<CP>` 에서 실패했다 |
| 속성 따옴표 오류 | `<TE ENG=""Maximum exposure…">` | 1건 | `=""X"` → `="X"` (빈 속성 `X="" Y="v"` 는 건드리지 않음 — 첫 규칙이 2024 분기 1건을 새로 깨뜨렸다) |
| 제어문자 | — | 0 | 제거 규칙만 둔다 |
§2.2 정제 5규칙 적용 후 **505/505 expat 파싱 성공**(정제 7.45초 + 파싱 16.72초 / 653MB). `xml.etree.XMLParser.entity` 로 엔티티를 넘기는 방법은 expat 이 DTD 없는 문서의 미정의 엔티티를 먼저 거부해 동작하지 않는다(교훈 ③).

### 1.6 어휘 — 태그 41 · 핵심 속성
- **태그 화이트리스트(41)**: A, APPENDIX, BODY, COL, COLGROUP, COMMENT, COMPANY-NAME, CORRECTION, COVER, COVER-TITLE, DOCUMENT, DOCUMENT-HEADER, DOCUMENT-NAME, EXTRACTION, FILENAME, FORMULA-VERSION, IMAGE, IMG, IMG-CAPTION, INSERTION, LIBRARY, LIBRARYLIST, P, PGBRK, SECTION-1, SECTION-2, SECTION-3, SPAN, SUMMARY, TABLE, TABLE-GROUP, TBODY, TD, TE, TH, THEAD, TITLE, TOC, TR, TU, WARNING. Y510 XML 에서 토큰 정규식 `<(/?)([A-Za-z][A-Za-z0-9:_.-]*)` (ASCII 영문자로 시작하는 Name 만, 대소문자 구분) 의 문서 빈도 ≥3%(15건) 기준. 밖의 토큰은 약 60종 전부 ≤2건(`<NICE>`, `<GDP>`, `<Kolon>` 등 본문 텍스트). `<당기말>` 처럼 한글로 시작하면 토큰이 아니고 맨 `<` 로만 센다.
- **목차·섹션**: `TITLE[@ATOC="Y"][@AASSOCNOTE]` — `AASSOCNOTE` 가 세대를 넘는 **섹션 식별 코드**. `D-0-3-2-0` 연결재무제표 · `D-0-3-3-0` 연결 주석 · `D-0-3-4-0` 재무제표 · `D-0-3-5-0` 주석 · `TTL_CEO_CERT` · `COVER` · `TTL_APPENDIX`. 2010~2013 은 재무제표가 `XI. 재무제표 등`(`D-0-11-0-0`) 아래, 2016~ 은 `III. 재무에 관한 사항` 의 2·4 절 — 전환 시점은 2015-03(§1.2 전환표). G3 는 `ATOCID`(문서 내 순번) 추가, 사업의 내용 하위는 `L-0-2-n-L1`.
- **표**: `TABLE[@ACLASS]` = NORMAL | EXTRACTION. `TABLE-GROUP[@ACLASS]` = `{XBRL}BS`·`IS1/IS2/IS3`·`EF`·`CF`·`SA`(2010 이익잉여금처분) + 접미 `_S`(별도)·`_C`(연결, G3 만) — **재무제표 기계 식별자**. 2026 문서는 주석도 `{XBRL}NT_S_D8xxxxx` 그룹으로 온다(P4 재료). 그 외 `COVER`·`PB_VAL`·`TOT_STK`·`FUND_SA` 등 표준 서식 표.
- **추출 셀**: `TE[@ACODE]`(CRP_NM·EST_DT·ADR·INC_CNT…, 문서당 48~2,991개) 와 `TU[@AUNIT][@AUNITVALUE]`(PERIODFROM·PERIODTO·BASE_DT·WON…) — DART 가 기계 추출하려고 심은 값. **`PERIODFROM/PERIODTO` 는 S26 의 XML 세대 G1·G2·G3 전부에 있고 비12월 결산(케이비증권 20090401~20100331)도 정확** → 보고기간 확정 축.

### 1.7 정정 첫 장 — 원본 접수번호는 없다
위치 `BODY/INSERTION/LIBRARY/CORRECTION`(G1·G2) · `BODY/LIBRARY/CORRECTION`(G3). 내용: 제출일 표 → `1. 정정대상 공시서류 : X` → `2. 정정대상 공시서류의 최초제출일 : 날짜` → (`3. 정정사유 : …` 44/339) → 정정사항 표(열 = 항목 · [정정요구ㆍ명령관련 여부(G3)] · [정정사유] · 정정 전 · 정정 후) → 정정 전/후 본문 발췌(최대 1.1MB). 2025~ 는 1·2·3 줄이 `<P>` 가 아니라 `<TD>` 셀에 있다.

C340 실측(전부 ZIP 있는 정정):

| 항목 | 값 | 정의 |
|---|---|---|
| `CORRECTION` 요소 존재 | 339/340 | 없는 1건 = 첨부만 든 `[첨부정정]` ZIP |
| 원장 원본 후보 유일 | **339/340 (99.7%)** | `dart_disclosure` 단독(ZIP·XML 무관)에서 같은 corp_code · 같은 종류(사업/반기/분기) · 같은 기간 라벨 `(YYYY.MM)` · report_nm 접두 ∉ {[기재정정],[첨부정정]} · rcept_no < 정정 rcept_no — 340 전건을 이 규칙 하나로 분류: 유일 339 · 후보 0 = 원본이 2010 이전 1(`20100105000010`, 분기보고서 2009.09) · 후보 2+ = 0. (첫 프로토타입은 XML 단계 실패 시 원장 조회를 건너뛰어 336 으로 셌다 — 교훈 ⑧) |
| 최초제출일 해석 | 291/336(프로토타입이 날짜 단계까지 간 행) | 형식 30종+(`2013년 4월 1일`·`2020.03.30`·`2025 년 3 월 12 일`·오타 `2012년 08년 14일`). 미해석 45 는 대부분 2025~ 의 `<TD>` 배치를 P 기반 추출기가 놓친 것(교훈 ④) → 요소 전체 `itertext` 평문에서 **`최초\s*제출일\s*[:：]?` 뒤 첫 날짜**만 정규식 `(\d{4})\s*[년.\-/월]\s*(\d{1,2})\s*[년월.\-/]\s*(\d{1,2})` 로 잡는다(앵커 없이 첫 날짜를 잡으면 페이지 맨 위 제출일 표의 정정 자신 날짜가 걸린다). C340 프로토타입도 "2. …최초제출일" 줄 안에서만 찾았으므로 불일치 원인 "정정 자신의 날짜를 적음" 은 추출 오류가 아니라 제출자 오기 |
| 최초제출일 vs 후보 `rcept_dt` (해석된 291) | exact 273 · ±1일 3 · 2~7일 5 · 8일+ 10 | **exact+1일 = 276/291 = 94.8%**. 불일치 원인: 정정 자신의 날짜를 적음 · 제출일 vs 접수일 · `[첨부정정]` 이 감사보고서 제출일을 적음 · 오기 |
| 원본 접수번호 명시 | **0/339** | 링크 근거는 날짜뿐 → §8.1 규칙 |

**원장 전수(정의 = `backfill_docs.py` P2·P3 술어: `report_nm` 이 사업/반기/분기보고서 · `연장신고` 제외 · `stock_code <> ''` · DISTINCT rcept_no; 09-03 측정)**:

| 접두 | 접수 | ZIP 있음(`zip_ok=1`) | DART `014`(ZIP 없음) | `doc_store` 행 없음 |
|---|---|---|---|---|
| 없음(원본) | 149,878 | 149,873 | 4 | 1 |
| `[첨부추가]` | 2,454 | 2,453 | 1 | 0 |
| `[기재정정]` | 19,543 | 17,185 | 2,357 | 1 |
| `[첨부정정]` | 1,036 | 418 | 618 | 0 |
| 합 | 172,911 | 169,929 | 2,980 | 2 |

정정(`[기재정정]+[첨부정정]`) = **20,579 = ZIP 있음 17,603 + `014` 2,975 + `doc_store` 행 없음 1**. 접두 집계는 DISTINCT (rcept_no, 접두) 라 같은 접수가 나중에 `[첨부추가]` 로 재명명되면 두 접두에 걸릴 수 있다(합 172,911 vs 킥오프 대상 172,906) — G0 에서 rcept_no 단위로 확정. 킥오프의 "정정 ZIP 18,013/21,138" 은 다른 술어(`stg_disclosure.is_correction` 추정)라 대응 관계는 P1 G0 에서 맞춘다. **`[첨부추가]` 는 새 접수가 아니라 원본 rcept_no 에 붙는 라벨**(ZIP 도 본문+첨부 3멤버) — 원본 후보에서 빼면 안 된다.
`rm` 플래그의 `정` = "이 접수에 나중에 정정이 붙었다". 2015~2024 사업보고서 원본(접두 없음+`[첨부추가]`, `stock_code<>''`) 24,671건 × 후속 정정(`[기재정정]|[첨부정정]`, 같은 corp·기간 라벨):

| `rm` 에 `정` | 후속 정정 | 건수 |
|---|---|---|
| 있음 | 있음 | 6,156 (기재정정 5,918 · 첨부정정만 238) |
| 있음 | 없음 | 6 |
| 없음 | 있음 | 5 |
| 없음 | 없음 | 18,504 |

플래그→정정 6,156/6,162 = 99.9%, 정정→플래그 6,156/6,161 = 99.9%. `[첨부정정]` 을 빼면 플래그→정정이 5,918/6,162 = 96.0% 로 떨어진다 — **링크 모집단에 `[첨부정정]` 이 반드시 들어가야 한다.** 현재 시점 값이라 PIT 에는 못 쓰지만 링크 커버리지 검산축(E-G7)으로는 최적.

### 1.8 목차 구조 (G1 2020 사업보고서 기준, 32개)
`【대표이사 등의 확인】` → `I. 회사의 개요`(1~6) → `II. 사업의 내용` → `III. 재무에 관한 사항`(1 요약재무정보 · **2 연결재무제표** · 3 연결 주석 · **4 재무제표** · 5 주석 · 6 기타) → `IV. 이사의 경영진단` → `V. 감사인의 감사의견` → `VI. 이사회 등` → `VII. 주주` → `VIII. 임원 및 직원`(1 현황 · 2 보수) → `IX. 계열회사` → `X. 이해관계자 거래` → `XI. 그 밖에 투자자 보호` → `【전문가의 확인】`. G3 는 II 하위 7절(`L-0-2-n-L1`)·III 하위 8절·V 하위 2절·`XII. 상세표` 가 추가되고 재무제표 4표가 각각 목차 항목이 된다. 재무제표·주석 절은 `LIBRARY`(삽입 라이브러리) 안에 있다 — **경로가 아니라 `AASSOCNOTE` 로 찾는다.**

### 1.9 재무제표 표 — XBRL TABLE-GROUP 2표 구조
```
TABLE-GROUP[@ACLASS="{XBRL}BS_S"]
  (TITLE ATOC=Y  "4-1. 재무상태표")            ← G3 / 2010 만
  TABLE[NORMAL, AFIXTABLE=Y]  헤더: ["재무상태표"], ["제 54 기  2019.12.31 현재"], ["제 53 기 …"], ["(단위 : 원)"]
  TABLE[NORMAL, AFIXTABLE=Y]  본문: ["", "제 54 기", "제 53 기", "제 52 기"], ["자산","","",""], ["유동자산","14,563,470,354","22,985,199,930",…] … ["자본과부채총계", …]
```
- 헤더 표: 1열 = 표 이름(G1 2013~ 은 `P[@USERMARK="F-12 B"]`, 2010·G3 는 `TITLE`) · 기간 라벨(`제 N 기 [반기|N분기][말] YYYY.MM.DD [현재 | 부터 YYYY.MM.DD 까지]`, 2010 은 `제N기 YYYY년 MM월 DD일`) · 단위 `(단위 : 원)`.
- 본문 표: 1행 열 라벨(`제 54 기` / 분·반기 손익은 `3개월`·`누적` 2단) · 1열 계정 원문(`Ⅰ.유동자산`·`(1)현금및현금성자산` 등 번호 접두 혼재) · 값 `1,234` / `(1,234)` 음수 / `''` / `-`. 자본변동표는 열이 자본 구성요소·행이 변동 사유(2단 헤더).
- **연결/별도 판정 결정표(`scope`)**: ① 접미 `_C` → C · ② 접미 `_S` → S · ③ 접미 없음 ∧ (섹션 코드 `D-0-3-2-0` ∨ 같은 섹션에 `_S` 형제 그룹 존재) → C · ④ 그 외 → U(미구분). 실측 대응: G3 청보 `BS_C`/`BS_S` → ①②; 2016~2022 `{XBRL}BS`(섹션 `D-0-3-2-0`) → ③ C; 2013-B 세아특수강(섹션 `D-0-11-0-0`, `{XBRL}BS`+`{XBRL}BS_S` 공존) → ③ C / ② S; 2010-A 케이비증권(접미 없는 BS·IS·SA·EF·CF 만, 연결은 첨부 `_00761` 에만) → ④ U = K-GAAP 개별 — equity 가 별도로 해석.
- 그룹 존재: S26 → 2010 5(BS·IS·SA·EF·CF) · 2013 0~6 · 2016 8(연결 4+별도 4) · 2020 8~10(IS2+IS3 = 손익·포괄손익 2표 추정, §7 미결) · 2022 8 · 2024 8, 반기는 2016·2020 8 / 2024 4(연결 없는 회사). **0 인 원본 문서**: 2013-A 에프엔에스테크·2016-B 디알텍(SPAC) — 편집기 수기표(`◆click◆『XBRL 재무제표』 삽입` 안내문 잔존, "주권상장법인은 XBRL 재무제표를 삽입"). 존재율은 P1 census(G8)로 연도·시장별 전수 측정.

### 1.10 첨부 감사보고서 헤더 — 구조화 메타
`_00760/_00761` 의 `SUMMARY/EXTRACTION`: `AUDIT_CIK`(감사인 코드) · `SUPV_OPIN`(의견 코드 `100000000000`) · `GMSH_DATE`(주총일) · `CRP_RGS_NO` · `FIN_STAT` · `TOT_ASSETS/TOT_DEBTS/TOT_SALES`(2020~, 백만원 추정 — 실측 필요) · `TOT_EMPL` · `IFRS_YN` · `FIN_YN` · `LINK_FLAG` · `PREV_IACS`. 본문 헤더는 `LINK_FLAG`·`FIN_TYPE`·`IFRS_YN`·`CRP_RGS_NO_TEMP`·`RPT_TP`. `stg_audit`(API) 와 교차 검산 가능한 공짜 열.

### 1.11 성능 — 스트리밍 불필요
Y510 XML 505건 653MB: 정제 7.45초 + expat 16.72초 = **문서당 48ms**, 최대 파일 15.5MB(전문 로드 시 텍스트 ~30MB). 전량 171,179건 ≈ 2.3시간(파싱만) → ZIP 해제·디코딩·산출 포함 ×1.5 ≈ 3.5시간 단일 코어, 3 워커 ≈ 1.2시간. 정정 문서만 재파싱은 17,603 × 50ms ≈ 15분. 킥오프의 "`iterparse` 스트리밍" 문구는 **문서 단위 전체 로드로 대체**(§5 ②) — 트리 전체가 있어야 `AASSOCNOTE`→하위 표 탐색이 단순하다. 관대한 대체 파서(`html.parser`, 순수 파이썬)는 같은 문서에 0.34초 — 실패분에만 쓴다.

### 1.12 무손실 검증 — 텍스트·속성은 등식으로, 표 기하는 P2 요구로
Y510 XML 505건에 대해 정제→expat 트리를 원문과 등식 비교(09-03):

| 검증 | 술어 | 결과 |
|---|---|---|
| 텍스트 등식 | `"".join(root.itertext()).strip()` = 정제 원문에서 주석·선언·태그(`<[^>]+>`)를 지우고 `html.unescape` 한 문자열 `.strip()` | **505/505 동일** — 정제 5규칙(엔티티 치환·맨 `&`/`<` 이스케이프·속성 복구)은 글자를 하나도 바꾸지 않는다 |
| 속성 수 등식 | 원문 시작 태그의 `name="…"` 수 = `Σ len(el.attrib)` | 505/505 에서 **정확히 1 차이** = 루트의 `xmlns:xsi` 를 ET 가 네임스페이스 선언으로 빼는 것(`noNamespaceSchemaLocation` 은 남음). 손실 아님 |
| 디코딩 치환 | `errors='replace'` 경로 진입 문서 | 0/505 |
| 태그 구조 | 화이트리스트 밖 토큰은 텍스트로 강등 | 텍스트로는 보존(위 등식에 포함)되지만 **구조는 잃는다**. 표본에서 강등된 토큰은 전부 본문 텍스트(≤2건/505)였고, 전량 census(G3) 가 새 태그를 잡는다 |
| 재무표 셀 병합 | XBRL `TABLE-GROUP` 안 `TD/TH` 의 `ROWSPAN`·`COLSPAN` | **ROWSPAN 8,413 · COLSPAN 19,137 셀, 505 중 430 문서** — 분·반기 손익(`3개월/누적` 2단)·자본변동표(구성요소 2단)의 헤더. 트리는 속성을 그대로 갖고 있으므로 파싱 손실은 없지만, **P2 가 격자 전개 없이 열 순서로 기간을 붙이면 값이 옆 기간으로 밀린다** |

따라서 P1 산출은 원문 대비 무손실이 등식으로 보장되고(D10 으로 매 문서 판정), 손실이 생길 수 있는 지점은 두 곳뿐이다: ① 화이트리스트 밖의 **진짜** 태그(구조 강등 — D3 어휘 게이트), ② P2 의 표 격자 전개(§3.5 요구 + D11). 원천 자체의 손실은 파서와 무관하게 존재한다 — ZIP 에 이미지 파일이 없어 `IMAGE/IMG` 는 참조만 남고, ZIP 없는 정정 2,976건은 원장으로 보완한다(§8.1).

### 1.13 양식(서식표) 커버리지 — 연도별 (Y1676 중 사업보고서만, 연 72~100건, 접수연도 기준)
서식표 = `TABLE-GROUP[@ACLASS]` 이름이 있고 셀에 `TE[@ACODE]`/`TU[@AUNIT]` 가 붙은 DART 표준 양식. API 보조원장(배당·주식수·주주·임원·감사)이 여기서 나온다.

| 접수연도 | 양식 어휘(종) | 문서당 양식(중앙값) | `TE` 중앙값 | 배당 | 주식총수 | 최대주주 | 5%주주 | 임원 | 직원 | 감사의견 | 종속회사 | 타법인출자 | 계열회사 | 이사보수 | XBRL 별도 4표 | XBRL 연결 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2010 | 4 | 4 | 57 | 1.00 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.94 | 0 |
| 2011 | 21 | 14 | 476 | 1.00 | 0.96 | 0.94 | 0.93 | 0.94 | 0.86 | 0 | 0.94 | 0.78 | 0 | 0 | 0.92 | 0 |
| 2014 | 33 | 25 | 813 | 1.00 | 0.94 | 0.99 | 0.99 | 0.99 | 0.99 | 0 | 0.63 | 0.87 | 0 | 0 | 0.89 | 0.68 |
| 2015 | 38 | 28 | 799 | 1.00 | 0.98 | 1.00 | 1.00 | 1.00 | 1.00 | 0.94 | 0.69 | 0.89 | 0 | 0 | 0.84 | 0.69 |
| 2019 | 60 | 35 | 945 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.95 | 0.72 | 0.83 | 0 | 0.99 | 0.80 | 0.64 |
| 2022 | 81 | 50 | 1,148 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.99 | 0.98 | 0.99 | 0.97 | 1.00 | 0.94 | 0.78 |
| 2026 | 111 | 62 | 2,976 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 | 0.96 | 0.77 |

양식 첫 등장(표본 문서 20% 이상): 2010 `COVER`·`DIVIDEND`·`INC_STAT`·`FUND` 4종뿐 → **2011-03 에 13종 일괄**(`TOT_STK`·`BSH_SPCL`·`BSH_CHA`·`SH5_PRE_STT`·`SH4_PRE_STT`·`SH5_DRCT_STT`·`EMPLOYEE`·`SUB_CMPN`·`INV_PRT`·`OWN_SHR`·`VOT_STK`·`BW`·`SOPT`) → 2014 채무증권 `SUB_*` 6종·이사 개인별 보수 → 2015 `AUD_OPN`·`AUD_SIGK`·`AUD_SIGL`·`CB`·`CONT_CHG` → 2017 `SUB_CMPT`(보수 총액) → 2019 특례상장·미등기임원·감사조직 10종 → 2022 `AFF_CMP`(계열회사)·요약표·경영진 변동 8종 → 2024 사업목적 4종 → 2025 내부회계·감사인 예측 8종.
결론: 2010 접수분(2009 사업연도)은 배당·증자 빼고 자유 서식. 2011~2014 는 주주·임원·주식수 양식은 있으나 감사의견(2015~)·이사보수(2014/2017~)·계열회사(2022~)가 없다 — API 항목별 하한이 서로 다른 이유. XBRL 연결 60~78% 는 연결 대상이 없는 회사 때문에 100% 가 될 수 없는 항목이고, 별도 76~96% 의 나머지는 수기표.

### 1.14 표 종류와 자유표의 정형성 (Y510 중 정기보고서 본문 502건)
- **표 분류**: `TABLE[@ACLASS="EXTRACTION"]`(서식표) 27,690 = 13.8% · `{XBRL}` 그룹 안 28,437 = 14.2% · 나머지 자유표(`NORMAL`, 그룹 없음) 144,256 = **72.0%**. 문서당 자유표 비중 중앙값 80%.
- **목차 코드 부여율**: `SECTION-2` 제목 11,240/11,717 = 96% 에 `AASSOCNOTE` 있음 · `SECTION-1` 4,887/6,895 = 71%(로마자 상위 절 "I. 회사의 개요" 류는 코드 없음 → 자식 코드로 역산) · `SECTION-3` 318/318. `AASSOCNOTE` 어휘는 세대별 54~59종, 세대 간 공유 53/58·53/60 — 안정.
- **셀 코드 어휘는 세대별로 다르다**: 고유 `ACODE` dart3 헤더형 925 · dart3 평면형 3,771 · dart4 4,732, 세 세대 공통 702, 합 7,930. 2010 은 `A01`~`A26` 불투명 코드 18종, dart4 는 `ifrs-full_ProfitLoss` 같은 IFRS 택소노미 ID 를 코드로 쓰고 코드 없는 `TE` 가 139,890 셀. → 코드→의미 사전은 세대별 census 로 만들어야 하며 파서가 아니라 사전 작업(§2.4).
- **자유표 헤더 서명 집중도**: 숫자 있는 자유표 75,123 / 헤더 서명(절 코드 + 첫 행 셀 정규화) 22,094종. 상위 50 서명이 41%, 200 이 49%, 1,000 이 60%, 2,000 이 65%, 단 1회 서명 14,682(19.5%). 주석 절(`D-0-3-3-0`·`D-0-3-5-0`)이 62%, 사업의 내용(`D-0-2-0-0`)은 2,643종에 1위 서명이 4%. 상위 서명은 `(단위:천원)`·`(구분, 당기, 전기)` 같은 껍데기라 **표의 정체는 앞 문단 제목(예: "12. 재고자산")이 정한다** → 군집 키 = (절 코드, 앞 제목 정규화, 헤더 서명).
- **단위는 표 밖에 있다**: 자유표 76% 에 단위 표기가 없는데, G1 은 `(단위 : 천원)` 만 든 1행 표가 직전 형제로 오고(G3 문서 1행 표 233개 대부분이 단위 줄·꺾쇠 제목), 주석은 문단에 있다. 파서의 단위 문맥 = 직전 형제 1행 표 → 직전 문단 → 헤더 순.
- **소제목은 `TITLE` 이 아니라 `P`** ("가. …", "(1) …", "1) …"). 절 안 세부 항목 좌표는 번호 매김 문단으로 잡는다.
- 참고 산출: 문서 7건의 내용 구조도 `docs/outlines/DOC_OUTLINE_*.md`(사업 G1·G3, 분기, 2010 K-GAAP, 2013 수기표, 삼성전자 2024, 정정) — 절별 소제목·서식표·XBRL·자유표·문단이 문서 순서대로.

### 1.15 로컬 LLM 파일럿과 골든셋 (2026-09-03)
- kael-ai(`192.168.0.2` Ollama 0.33.2, RTX 5070; 규약은 kael-system-v3 `docs/local_llm_playbook.md`·`scripts/kael_ollama.py`)에 써니전자 2020 사업보고서 II 절 자유표 20개를 "숫자는 만지지 말고 표 주제·단위·열 역할·합계행만" 스키마 강제로 판정시킴: 스키마 준수 20/20 · 단위 근거 일치 20/20 · 주제 복사 20/20(두 모델 동일), 열 역할은 규칙 검증 가능 셀 5개 중 qwen3.5:9b 4 · gemma4:12b 2. 지연 표당 1.9s / 3.6s(첫 호출 12~13s 로드), 입력 약 600·출력 100 토큰.
- 해석: 문맥(주제·단위)은 LLM 이 완벽, 기간 열 판정은 규칙이 낫다 → **LLM = 군집별 스키마 추론·텍스트 변환, 규칙 = 기간 열·숫자, 숫자 재타이핑 금지**(플레이북 교리와 일치). 자유표 전량 직접 읽기(약 2,570만 표)는 로컬 약 200일·Claude API 수만 달러라 비추천; 군집당 1회(수천 회)면 시간·비용 무시 가능.
- 사용자 결정(09-03): Claude API 미사용, 판정은 이 Claude Code 세션(서브에이전트 활용은 세션 판단). 골든셋 100표 = `workspace/dongmin/eval/table_schema/`(`cases.jsonl`·`cases.md`·`labels_table.csv`·`labels_columns.csv`·`README.md`; 층화 = 사업의 내용 40·주석 30·기타 30 × G1 50·G2 20·G3 30, 2단 헤더 66, 단위 미표기 76, 회사 39곳, 시드 20260903). 입력 승인·라벨링 대기, 분기·반기 표 10~15개 교체 권고.

## 2. stage 소스 계약

### 2.1 층 배치와 공통 골격
```
원장   data/raw/documents/{yyyy}/{rcept_no}.zip  (바이트 불변)  +  dart.db.doc_store (rcept_no·bytes·sha256·n_files·zip_ok·http_status·fetched_at)
  │   스냅샷 = zip_ok=1 접수번호 집합의 해시 (ZIP 은 불변이라 VACUUM INTO 불필요; doc_store 만 스냅샷 사본)
  ▼
stage  data/stage/stg_doc_*/v=<build>/year=YYYY/*.parquet + MANIFEST.json (keep=3) + _meta.json   ← 7테이블, §3
  ▼
equity correction_link · fin_std(vintage 결합) · 자유표 값 층 · 텍스트 팩터   ← §8
```
- **파티션**: 전부 `receipt_axis`, `year = substr(rcept_no,1,4)`. `partition_class` 는 stage 규칙 그대로.
- **공통 골격**(STAGE §3): `available_date`/`available_basis` = `AvailableRule(lookup: stg_rcept_dt_map)` → `derived`(참조표 미스는 stg_fin 과 같이 0 기대) · `observed_date` = `doc_store.fetched_at` 의 KST 날짜(`observed_src`) · `observed_n=1` · `_src='doc_zip'` · `_src_flag` ok/partial · `_cast_fail_cols` · `miss_kind` STRUCT(blank/dash/unit_unknown/non_krw/cast_failed). `ticker`·`date` 없음(stg_fin 과 같은 예외).
- **판본**: ZIP 은 접수번호당 1개·불변 → `write_mode=append_only`, `versioned=True`(관측일 1개), `dedup` 없음(파일당 1회 파싱). 정정은 다른 접수번호라 행이 다르다. `stg_fin_asreported` 의 `vintage_kind` 는 **같은 문서의** `has_correction_page`·`doc_name` 접두로 `original`(원본·`[첨부추가]`) / `corrected`(`[기재정정]`·`[첨부정정]` 문서의 재무표) 를 나눈다 — 정정 문서에도 XBRL 그룹이 있으므로(§1.1 표 2013-D·2016-D·2020-D·2024-D) 전 행을 `original` 로 찍으면 stage 안에서 PIT 오염이 난다. 재무의 원본 판본은 `original` 행에서 처음 성립한다(STAGE §7 의 "재무 원본 판본 부재" 해소).
- **뷰 카탈로그(선택, 결정 ⑬)**: `data/stage/catalog.duckdb` 에 62+7 테이블의 MANIFEST `current_build` 경로를 가리키는 뷰만 둔다(복사 없음, 빌드마다 뷰 갱신). equity·조사 쿼리의 편의.

### 2.2 파서 파이프라인 (stage blob 경로 재사용)
```
FileSource(doc_store 스냅샷 → zip 경로)                     ← BlobSource(WISE) 와 같은 자리
  → 워커 3(multiprocessing, 연도 샤드): 문서 1개 = ZIP 1개
      멤버 basename 매칭(role: main | audit(_00760) | audit_cons(_00761) | other)
      → 바이트 인코딩 판정(utf-8 strict → cp949 strict → cp949 replace + n_repl)                 §1.3
      → HTML 판정(`<html` in 앞 4KB, 대소문자 무시) → parse_mode=html, <title>·표 수만 기록, 종료
      → 정제 5규칙(§2.3) → expat(`ET.fromstring`) → parse_mode=ok
          ↳ ParseError → html.parser 대체 트리 → parse_mode=lenient ↳ 실패 → failed(+오류 위치·문맥 100자)
      → 세대·서식판 → 헤더·SUMMARY → 목차 → CORRECTION → 서식표 셀 → XBRL 그룹(격자 전개) → 자유표 좌표
      → parse_document() 는 7테이블 행을 dict[table → rows] 로 한 번에 반환 (문서 두 번 열지 않음)
  → 테이블 × 연도 샤드 JSON Lines → duckdb read_json(columns=VARCHAR…) 한 번 → CAST(rules_doc) → 게이트 → parquet   (교훈 ⑥)
```
- `parse_mode` 어휘 **ok / lenient / failed / html** 넷뿐(§3·G1 공통). `other` 멤버도 파싱은 시도한다.
- **lenient 규칙**: `html.parser.HTMLParser` 서브클래스. 태그명·속성명 모두 대문자화(HTMLParser 는 둘 다 소문자 — 안 올리면 `TITLE[@ATOC]` 탐색이 조용히 0건), 화이트리스트 밖 태그는 요소를 만들지 않고 자식을 부모에 붙임(`lenient_unknown_tags`), 종료 태그는 스택을 거슬러 닫고 없으면 무시. 실패 = 예외 · 루트 직계 `DOCUMENT` 부재 · `toc_n=0 ∧ n_tables=0`. Y510 의 유일한 expat 실패 문서를 19,693 요소·목차 102·XBRL 48 로 복원(속성 복구 후엔 expat 도 통과).
- 표준 라이브러리(`zipfile`·`re`·`xml.etree`·`html.parser`·`json`·`multiprocessing`) + duckdb. 새 의존성 없음. 실패는 값으로(`ParseResult.status`).
- 성능 근거 §1.11: 48ms/문서 → 전량 파싱 2.3h, ZIP·산출 포함 ×1.5, 3워커 ≈ 1.2h. `stg_fin_asreported`·`stg_doc_form_cell` 의 duckdb 단계는 stg_fin(1,540만 행 11분) 비례로 각 1~2h 추정 — P2·P3 착수 전 S1 식 벤치.

### 2.3 정제 5규칙 (순서 고정, 전부 카운터 기록)
1. `[\x00-\x08\x0b\x0c\x0e-\x1f]` 제거 → `n_ctrl`
2. `&name;` — 표준 5개 유지 · `cr` → `&#10;` · 그 외 → `[&name;]` 문자열 보존 + `n_ent_other`(G3)
3. `&` 뒤에 `(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);` 가 아니면 `&amp;` → `n_bare_amp`
4. `<` 뒤가 `/?(화이트리스트 41)(?=[\s/>])` · `!` · `?` 가 아니면 `&lt;` → `n_bare_lt` + 치환 토큰 어휘(§1.6 정규식)
5. `=""(?=[^\s"<>])([^"<>=]*)"` → `="\1"` → `n_attr_repair`

### 2.4 어휘 사전 — 코드가 아니라 데이터 (`src/stage/doc_vocab/`)
| 파일 | 내용 | 생성 | 미등록 처리 |
|---|---|---|---|
| `tags.json` | 태그 화이트리스트 41(§1.6) | Y510 census, 전량 census(G3)로 검증 | 텍스트로 강등 + `unknown_tags` |
| `aassocnote.json` | 절 코드 → 정규 제목·깊이, 2015-03 전환 매핑(`D-0-11-0-0` 재무제표 등 ↔ `D-0-3-2-0/4-0`) | 전량 census + 손 검수 | 코드 원문 그대로, `section_kind=NULL` |
| `form_aclass.json` | 양식 이름(ACLASS, 111종) → 한글명·도입 접수연도·헤더 서명 | §1.13 census + 손 검수 | 원문 그대로, G12 계상 |
| `acode.json` | 세대별 셀 코드(ACODE/AUNIT) → 라벨(2010 `A01`~ 는 표 헤더에서 역산, dart4 `ifrs-full_*` 는 택소노미 라벨) | 전량 census + 손 검수 | 원문 그대로, G12 계상 |
| `units.json` | 단위 표기 정규식 → `unit_scale`(원 1 · 천원 1e3 · 백만원 1e6 · 억원 1e8 · 주/%/명 → 스케일 없음) | §1.9·§1.14 | `unit_unknown` |
| `xbrl_aclass.json` | `{XBRL}` ACLASS → `stmt`(BS/IS/EF/CF/SA/NT)·`scope` 결정표(§1.9) | S26 + census | `stmt='?'`, 행은 보존 |
사전은 `survey/doc_census.py`(전량, 읽기 전용)가 후보를 내고 사람이 확정한다. 사전에 없는 코드는 **버리지 않고 원문을 싣는다** — 어휘 커버리지는 G12 가 잰다. 코드 규칙 "SoT 하나": 정규식·상수는 이 디렉토리와 `parsers_doc.py` 상단에만.

### 2.5 코드 배치와 빌더 변경 4건 (결정 ⑧)
`src/stage/rules_doc.py`(7테이블 선언 — `partition_class`·자연키·컬럼맵·(p,s)·miss 마커) · `src/stage/parsers_doc.py`(정제·파서·격자 전개·`parse_document`) · `src/stage/doc_prepass.py`(아래 ①) · `src/stage/doc_vocab/*.json` · `src/stage/fixtures/stg_doc_*.json`(D4) · `survey/doc_census.py`. `manifest.py`·`baseline.py`·`snapshot.py` 는 그대로. 테스트는 표본에서 오린 XML 조각 픽스처(파일 의존 금지, `testing.md`).
현 빌더는 **테이블 1개 = 파서 1회 = `src_all` 1개**(`build.py` `build_table`, `__main__.py` 테이블당 1회)이고 G1 은 `n_src × fanout` 등식, G8 은 `n_parse_failed=0 ∧ emitted=n_src` 등식이며, 전 행을 `stage_all` 하나에 넣고 윈도우 함수를 돈다. 문서는 ZIP 1개가 7테이블에 서로 다른 수의 행을 내므로 그대로는 안 맞는다. 필요한 변경:
1. **파싱 프리패스(`doc_prepass.py`)**: 스냅샷의 ZIP 을 한 번만 파싱해 테이블 × 연도 샤드 JSON Lines 를 `data/stage/_tmp/doc/<snapshot>/<table>/year=YYYY.jsonl` 에 캐시(관측일 `fetched_at` 을 행 컬럼으로 주입). `FileSource` 는 이 캐시를 가리키는 소스다 — 테이블 빌드가 4~7회 돌아도 ZIP 은 1회만 연다. 캐시는 빌드 완료 후 삭제(keep 0), 크기 약 44GB(P2 포함) 는 `_tmp` 에 여유 확인.
2. **문서 전용 등식**: G1 을 `stage 행수 = 프리패스가 그 테이블에 낸 행수(parse_log 집계)` 로, G8 을 `프리패스 문서 수 = ok+lenient+failed+html` 로 대체(D1·D2). 파싱 실패는 예외가 아니라 `parse_log` 행이므로 G8 의 `n_parse_failed=0` 은 항상 성립.
3. **연도 샤드 빌드**: `stg_fin_asreported`·`stg_doc_form_cell`·`stg_doc_table` 은 `stage_all` 한 번이 아니라 연도 샤드 루프(샤드마다 CAST→게이트→COPY, MANIFEST 는 마지막에 원자 교체). stg_fin 1,540만 행에 스필 15~18GB 였으므로 2.2억 행은 샤드 없이는 디스크(여유 297GB)를 넘긴다.
4. **`observed_src=column`**: `fetched_at` 이 원장 테이블이 아니라 프리패스 행 컬럼에서 온다(`AvailableRule` 은 lookup 그대로).

## 3. stage 테이블 7개 (접수번호 키 · `receipt_axis` · 공통 골격 부착)

| 테이블 | 행 단위(자연키) | 원천 층 | 규모 추정(171,179 문서) | 단계 |
|---|---|---|---|---|
| `stg_doc_meta` | (rcept_no, member) | 헤더·표지·SUMMARY | 약 25만 | P1 |
| `stg_doc_section` | (rcept_no, member, ordinal) | 목차 `TITLE[@ATOC]` | 약 800만 | P1 |
| `stg_doc_correction` | (rcept_no) — ZIP 있는 정정 접수 | 첫 장 `CORRECTION` | 17,603 (§1.7 표: `[기재정정]` 17,185 + `[첨부정정]` 418) | P1 |
| `stg_doc_parse_log` | (rcept_no, member) | 파서 | 약 25만 | P1 |
| `stg_fin_asreported` | (rcept_no, member, aclass_raw, row_ord, col_ord) | `{XBRL}` 그룹 2표, 격자 전개 | long 약 2.2억(문서당 8그룹 × 40행 × 4열) / wide 5,500만 | P2 |
| `stg_doc_form_cell` | (rcept_no, member, form_ord, row_ord, col_ord) | 서식표 `TE`/`TU` 셀 | 약 1.5억(서식 그룹 안 `TE`·`TU` 전부, XBRL·COVER 그룹 제외; §1.13 `TE` 중앙값 476~2,976) | P3 |
| `stg_doc_table` | (rcept_no, member, table_ord) | 자유표 좌표·헤더 | 약 4,900만(숫자 유무 불문 자유표, §1.14 144,256/502 = 문서당 287) | P3 |

규모 산식: `stg_fin_asreported` = 문서당 XBRL 그룹 8 × 본문 40행 × 기간 4열 ≈ 1,300행 × 171,179(NT 제외); `stg_doc_form_cell` = `TE`+`TU` 중앙값 약 900(§1.13, XBRL 그룹 안 제외) × 171,179; `stg_doc_table` = 287 × 171,179. 각 테이블 착수 전 S1 식 벤치(연도 샤드 COPY 포함, §2.5 ③)로 확정하고 JSONL 캐시(약 44GB)와 스필을 합쳐 디스크 여유 297GB 안에서 도는지를 통과 조건에 넣는다. `stg_fin_asreported`·`stg_doc_form_cell` 은 stage 가 지금까지 다룬 최대(`stg_fin` 1,540만 행·295MB·스필 15~18GB)의 10배라 **연도 파티션 단위로 나눠 COPY 하고 duckdb `memory_limit` 6GB 안에서 도는지**를 벤치의 통과 조건으로 둔다. 모양(long/wide)은 결정 ⑪.

### 3.1 `stg_doc_meta`
`rcept_no` · `member_name` · `member_role`(main/audit/audit_cons/other) · `format`(xml/html) · `gen`(dart3_hdr/dart3_flat/dart4/html/unknown) · `xsd` · `formula_version` · `formula_date`(DATE, 서식 개정일 — §1.2 전환표) · `doc_acode` · `doc_name` · `corp_cik`(AREGCIK) · `company_name_doc` · `byte_enc` · `n_repl` · `n_ctrl` · `n_ent_other` · `n_bare_amp` · `n_bare_lt` · `n_attr_repair` · `parse_mode` · `n_elements` · `n_tables` · `n_form_groups` · `n_xbrl_groups` · `n_free_tables` · `toc_n` · `period_from`·`period_to`(TU PERIODFROM/TO, DATE) · `has_correction_page` · `xbrl_aclass`(LIST) · `form_aclass`(LIST) · `summary`(JSON: SUMMARY EXTRACTION 코드→값) · `cover`(JSON: 표지 TE ACODE→값) · `bytes_xml`
- 검산(G4·기록): `corp_cik` = `dart_disclosure.corp_code`, `doc_acode` ↔ report_nm 종류(사업 11011·반기 11012·분기 11013), `[첨부정정]` 은 main 부재 허용. `period_from/to` 와 report_nm 기간 라벨 일치율은 기록형.

### 3.2 `stg_doc_section`
`ordinal`(문서 순서) · `section_code`(AASSOCNOTE, NULL 허용) · `section_kind`(사전 §2.4: 개요/사업/재무/주석/감사/기관/주주/임원/기타, NULL) · `atocid` · `level`(1/2/3) · `title` · `path`(LIBRARY/INSERTION 포함) · `elem_start`·`elem_end` · `n_form_tables` · `n_xbrl_groups` · `n_free_tables` · `n_paragraphs` · `n_chars`
- 용도: P2~P4 가 표·문단을 다시 찾는 좌표. 본문 텍스트는 싣지 않는다.

### 3.3 `stg_doc_correction` — ZIP 있는 정정 문서의 첫 장 **원문 필드만** (링크 없음)
원장 = ZIP 파일 1개(`doc_store.zip_ok=1` 이고 `stg_doc_meta.has_correction_page` 판정 대상인 문서). 행 ≤1/ZIP. `corp_code`·`rcept_dt`·`report_nm` 류는 싣지 않는다 — `stg_disclosure` 가 이미 갖고 있고(§1 경계: 다른 원장의 컬럼을 한 행에 합치지 않는다) equity 가 `rcept_no` 로 붙인다.
`rcept_no` · `member_name`(첫 장이 든 멤버 — `[첨부정정]` 은 `_00761` 등) · `page_found` · `target_raw` · `filed_raw`(`최초제출일` 앵커 뒤 부분 문자열) · `filed_date`(DATE, 정규식 §1.7, NULL 허용) · `filed_date_status`(parsed/unparsed/no_page) · `reason_raw` · `n_items` · `items`(JSON 전 행: 정정사항 표(헤더에 `항목` 셀이 있는 첫 TABLE)의 헤더 라벨을 키로 한 행 목록) · `corr_text_chars`
- `page_found=false` 행도 싣는다(`[기재정정]` 인데 첫 장이 없는 문서 — D6 이 센다). ZIP 없는 정정 2,976건은 이 표에 없고 §8.1 의 LEFT JOIN 에서 `no_zip` 이 된다.
- `orig_rcept_no`·`candidate_status`·`date_check`·`prior_corr_count` 는 §8.1(equity).

### 3.4 `stg_doc_parse_log`
`parse_mode` · `error` · `error_ctx`(100자) · `t_decode_ms`·`t_sanitize_ms`·`t_parse_ms` · 정제 카운터 5 · `unknown_tags`(LIST) · `lenient_unknown_tags`(LIST) · `unknown_aclass`(LIST) · `unknown_acode_n`

### 3.5 `stg_fin_asreported` (P2) — XBRL 재무표, 격자 전개
`aclass_raw`(키) · `stmt`(BS/IS/EF/CF/SA — `IS1/IS2/IS3` 는 전부 IS, 구분은 `aclass_raw`) · `scope`(C/S/U — §1.9 결정표) · `stmt_title_raw` · `period_label`(`제 54 기`) · `period_from`·`period_to`(헤더 표 파싱, NULL 허용) · `sub_label`(3개월/누적/NULL) · `row_ord` · `col_ord`(전개 후) · `account_raw` · `account_norm`(공백·선행 번호·로마자·괄호 번호 제거, NFKC) · `value_raw` · `value_krw`(Decimal(38,4) = 숫자 × `unit_scale`; 콤마 제거·괄호 음수·`''`/`-` → NULL + `miss_kind` blank/dash; 비KRW → NULL + non_krw) · `unit_raw`·`unit_scale`(그 외 → NULL + unit_unknown) · `vintage_kind`(original / corrected — 같은 문서의 `has_correction_page` 로 categorize, §2.1) · `row_period_label`(자본변동표 전용, 아래)
- **격자 전개 필수**(§1.12: 505 중 430 문서에 병합 셀). 계정 표준화는 싣지 않는다(결정 ⑦) — `fin_map.py` 재사용은 equity.
- 모양(결정 ⑪): **long** — 한 행 = (aclass_raw, row_ord, col_ord). `period_slot` = 열 라벨의 `제 N 기` 를 N 내림차순으로 매긴 순위(1=최신 기, 2=전기, 3=전전기), 라벨에 N 이 없으면 열 순서. 분·반기 손익은 `sub_label`(3개월/누적)과 조합. **자본변동표(EF)는 기간이 행 축**(§1.9)이라 `period_slot` 은 NULL 이고 `row_period_label`(행 첫 셀의 `YYYY.MM.DD (기초자본)` 류)로 기간을 든다. wide(기간 열 고정)는 표마다 기간 열 수가 2~6 으로 달라 택하지 않는다.
- 범위: `stmt ∈ {BS, IS, EF, CF, SA}` 만. `{XBRL}NT_*`(주석, 2026 문서 43~50%)는 P2 에서 제외하고 P4 에서 다룬다 — 규모 추정 2.2억은 NT 제외 값.

### 3.6 `stg_doc_form_cell` (P3) — 서식표 코드 셀
`form_ord` · `form_aclass`(원문) · `form_name`(사전) · `table_ord_in_form` · `row_ord` · `col_ord`(전개 후) · `cell_tag`(TE/TU) · `code`(ACODE 또는 AUNIT 원문) · `code_label`(사전, NULL 허용) · `aunit_value`(TU 의 기계값, 원문) · `text_raw` · `value_num`(Decimal(38,4), 숫자 캐스트 가능 시; 콤마·괄호 음수·`-` 규칙 동일) · `value_date`(DATE, 날짜 캐스트 가능 시) · `row_label`(같은 행 첫 TD/TH 텍스트) · `col_label`(전개된 헤더 텍스트)
- 범위(결정 ⑫): `TABLE-GROUP[@ACLASS]` 가 `{XBRL}` 이 아닌 그룹(§1.13 의 서식표) 안의 **`TE`·`TU` 셀 전부** — 코드 없는 `TE`(dart4 에 많음, §1.14)도 포함하고 `code=NULL`. `{XBRL}` 그룹 안 셀과 `COVER` 그룹(`stg_doc_meta.cover` 와 중복)은 제외. `TABLE[@ACLASS="EXTRACTION"]` 여부는 판정에 쓰지 않는다. 라벨 두 개가 있어 코드 사전이 없어도 사람이 읽을 수 있다.
- 검산: 2015~ API 보조원장과 교차 — `DIVIDEND`↔`stg_dividend`, `TOT_STK`↔`stg_shares`, `OWN_SHR`↔`stg_tesstk`, `BSH_SPCL`/`SH5_*`↔`stg_hyslr`, `AUD_OPN`↔`stg_audit`(D14, 기록형 → baseline). 2011~2014 는 API 가 없어 신규 커버리지.

### 3.7 `stg_doc_table` (P3) — 자유표 좌표·헤더 (값 없음)
`table_ord` · `section_code`(없으면 부모 절 첫 자식 코드의 상위 접두로 역산, 예: `D-0-2-0-0`; `section_code_basis`=direct/derived) · `section_kind` · `heading_raw`(직전 번호 매김 문단/제목, §1.14) · `heading_norm` · `header_rows`(JSON, 전개 후 1~3행) · `header_depth` · `n_rows`·`n_cols` · `n_numeric_cells` · `unit_ctx_raw`(직전 1행 표 → 직전 문단 → 헤더 순 첫 매치) · `unit_scale`(사전, NULL) · `signature_hdr`(§1.14 측정과 동일 정의: hash(section_code + 첫 행 셀 정규화) — 정규화 = 공백 제거, `제N기`·날짜·숫자 → `#`, 셀 20자 절단, 최대 8셀) · `signature_ctx`(hash(section_code + heading_norm + header_rows 정규화) — 군집화용) · `elem_start`
- 용도: §8.3 군집화·골든셋·값 추출의 입력. 셀 값은 싣지 않는다(약 40억 셀).

## 4. 게이트 (STAGE §9 어휘: 폐기형 · 격리형 · 기록형)
stage 공통 게이트 **G0~G9 는 이름·뜻 그대로** 돈다(`gates.py run_all`, MANIFEST 에 같은 이름). 문서 전용 게이트는 이름 충돌을 피해 **D0~D14** 로 둔다. 첫 빌드에 baseline 없는 게이트는 `skip(no_baseline)`, 두 번째 빌드부터 판정. "기록형→격리형/폐기형" 전환 = 첫 풀 빌드 분포를 사람이 승인해 `baseline.json` 에 넣는 시점. 미착수 테이블은 `skip(not_built)`. G0·D0 의 건수는 리터럴이 아니라 스냅샷 술어로 계산한다(09-03 스냅샷 기준 171,179 / 17,603).

**stage 공통 G0~G9 의 기대**: G0 선언 대조 pass · G1 `n_src × fanout` 은 §2.5 ② 로 프리패스 행수 등식으로 대체(D1) · G2 cast_failed 격리(날짜·Decimal 캐스트) 적용 · G3 키 유일성 적용 · G4 픽스처 = D4 · G5 재현성 적용 · G6 판본 중복 = 0(관측일 1개) · **G7 범위**(observed 1999~ · 내용일 1900~2066)는 `period_from/to`·`filed_date`·`formula_date`·`value_date` 에 그대로 적용(격리형) · G8 blob 등식은 D2 로 대체 · G9 교차는 D9·D14.

| # | 이름 | 분류 | 술어 | 임계(초기·근거) |
|---|---|---|---|---|
| D0 | 실물 계약 | 폐기형 | 프리패스 입력 = 스냅샷 `doc_store.zip_ok=1` 전건 · 멤버 basename 정규식 `^\d{14}(_\d{5})?\.xml$` 밖은 `role=other` · ZIP 열기 실패 0 | 위반 0 |
| D1 | 건수 등식 | 폐기형 | 테이블마다 stage 행수 = 프리패스가 낸 행수(parse_log 집계) · rcept_no 마다 `stg_doc_meta` ≥1행 | 등식 |
| D2 | 파싱 계상 | 폐기형+격리형 | 프리패스 문서 수 = ok+lenient+failed+html · `failed/(ok+lenient+failed)` | 등식 · ≤ 0.5% (Y510: 0/505) |
| D3 | 태그·엔티티 어휘 폐쇄 | 폐기형 | 규칙 4 치환 토큰 중 문서 빈도 ≥1% 어휘 = 0 · `n_ent_other` 어휘 중 문서 빈도 ≥1% = 0 | 0 (Y510: 최대 2건/505) |
| D4 | 골든 픽스처 | 폐기형 | S26 26건의 (gen, byte_enc, doc_acode, formula_version, toc_n, has_correction_page, n_xbrl_groups, period_from/to) → `src/stage/fixtures/stg_doc_meta.json`(234건, `scripts/doc_fixtures_from_sample.py`). 정의 주의: `toc_n` 은 `COVER-TITLE` 을 포함해 §1.1 표 + 1(= 그 멤버의 `stg_doc_section` 행수), `n_xbrl_groups` 는 문서 전체(§1.1 표는 재무제표 절 안만 — 2013-B 8 vs 6) | 전부 일치 (09-04 서버 S26 대조: 26/26) |
| D5 | 재현성 보강 | 폐기형 | G5 에 더해 입력 스냅샷 해시를 `_meta.json` 에 기록; `stg_doc_meta`·`parse_log` 는 입력 같으면 Δ=0, 다르면 Δ행수 = 신규 멤버 수 | 등식(두 테이블만) |
| D6 | 정정 첫 장 | 기록형 (폐기형 항은 equity E-G6a) | stage 는 `member_role=main ∧ parse_mode∈{ok,lenient}` 문서의 첫 장 보유 수·`stg_doc_correction` 행수·`filed_date_status=parsed` 수를 `doc_checks` 로 기록한다. "`[기재정정]` 인데 첫 장 없음 = 0" 은 접두가 원장 `report_nm` 에만 있어 stage 문서 표만으로는 판정할 수 없으므로 equity 가 `stg_disclosure` 를 붙여 폐기형으로 판정(§8.1 E-G6a, C340: 339/339) | 기록 (해석률 기대 ≥ 291/336) |
| D8 | XBRL 존재율 | 기록형 | `member_role=main` 사업보고서 중 `n_xbrl_groups ≥ 4` 비율, 연도·시장별 | 기록 (Y1676 사업보고서: 별도 0.76~0.96 전 연도, 연결 0.60~0.78 — §1.13) |
| D9 | 재무 교차(P2) | 기록형→폐기형 | 정정 없는 보고서(원장 `rm` 에 `정` 없음)·2015~·`account_norm` 이 양쪽에 있는 행의 `value_krw = stg_fin.thstrm_amount` 비율. 불일치 행은 격리하지 않고(API 쪽이 restated 일 수 있음) 비율만 | 기록 → 임계 승인 후 폐기형(기대 100%) · 2010~2014 `skip(no_stg_fin)` |
| D10 | 텍스트 등식 | 폐기형 | 문서마다 §1.12 등식(lenient 는 공백 정규화 후) | 위반 0 (Y510: 505/505) |
| D11 | 표 격자(P2·P3) | 격리형 | 전개 후 모든 행 폭 = 헤더 폭 · 값 셀 span 없음 | 위반 행 격리, 비율 기록 → baseline |
| D12 | 어휘 커버리지 | 기록형→폐기형 | 연도별 미등록 `ACLASS` 비율 · 미등록 `ACODE` 셀 비율 · `section_kind=NULL` 비율. 행은 원문 보존(격리 없음). P1 은 `doc_checks` 가 파싱 ok 멤버 중 미등록 태그 보유·lenient 수만 기록, ACLASS·ACODE 비율은 P2·P3 | 기록 → 임계 승인 후 폐기형 (ACLASS 111종 전수 사전화 목표) |
| D13 | 교차 제출 삼각검증(P2) | 기록형→폐기형 | 쌍: 사업보고서 t(FY) 의 `period_slot=2` 값 = 같은 회사·`scope`·`stmt`·`account_norm` 의 직전 사업보고서 t−1(FY−1) `period_slot=1` 값; 분·반기는 같은 `sub_label` 끼리 전년 동기 보고서와. 정정 판정 = 양쪽 문서의 원장 `rm` 에 `정` 없음. 분모 = `account_norm` 이 양쪽에 있는 행(개명 계정은 분모 밖, 비율 기록) | 기록 → 100% 기대. 불일치 = 파싱 오류 또는 정정 신호 |
| D14 | 서식표 교차(P3) | 기록형→폐기형 | §3.6 매핑별 API 보조원장 일치율(2015~) | 기록 → baseline |

## 5. 결정 (①~⑮ 권고안 전부 09-04 사용자 승인)

| # | 결정 | 권고 | 대안·근거 |
|---|---|---|---|
| ① | 파서 | **화이트리스트 정제 + expat, 실패분만 `html.parser`** | `html.parser` 단일은 7배 느림. 정제 없이 expat 은 전건 실패(§1.5) |
| ② | 메모리 | **문서 단위 전체 로드(최대 15.5MB)** | 킥오프 "iterparse 스트리밍" 폐기(§1.11) |
| ③ | 정정 원본 확정 | **원장 후보 우선, XML 최초제출일은 검증 딱지** — equity 명세 §8.1 | XML 날짜 우선은 불일치 18/291 을 링크 오류로 만듦. 원장 후보 유일 339/340 |
| ④ | 정정 모집단 | **equity 가 `stg_disclosure` 로 원장 전건 20,579 를 만들고 `stg_doc_correction`(ZIP 있는 17,603) 을 LEFT JOIN** | stage 표에 원장 세 개를 합치면 §1 경계 위반. ZIP 있는 것만 모집단이면 2015~2019 정정 원본이 "정정 없음"으로 읽힘(§0) |
| ⑤ | HTML 주요사항보고서 | **범위 밖, `format=html` 만** | 1,403건은 equity 조정계수 검산축 — 별도 소형 파서(표 1개) |
| ⑥ | 첨부 헤더 | **`SUMMARY/EXTRACTION` 을 `stg_doc_meta.summary` 에** | 비용 0, `stg_audit` 교차 |
| ⑦ | 계정 표준화 | **원문 라벨 보존, 표준 매핑은 equity(`fin_map.py`)** | as-reported 층이 매핑을 품으면 판본이 둘 |
| ⑧ | 코드 위치 | **`src/stage/` 안 (`rules_doc`·`parsers_doc`·`doc_vocab`), `FileSource` 추가** | v1 의 `src/doc/` 별도 패키지 철회 — stage 빌더·게이트·MANIFEST 를 그대로 쓰는 편이 규약 하나 |
| ⑨ | 산출 위치 | **`data/stage/stg_doc_*`** | v1 의 `data/doc/` 철회 — 같은 층, 같은 리더 |
| ⑩ | 병렬 | `multiprocessing` 3워커·연도 샤드 JSON Lines | CPU 4·RAM 15GB, 해시 순서 무관 |
| ⑪ | `stg_fin_asreported` 모양 | **long(기간 라벨별 행) + `period_slot`**, 약 2.2억 행 | wide(5,500만)는 기간 열 수가 표마다 달라(2~6) 스키마가 흔들림. 크기는 벤치로 확인 |
| ⑫ | `stg_doc_form_cell` 범위 | **코드 있는 셀(TE/TU)만 + 행·열 라벨**, 약 1.5억 | 양식 안 모든 셀은 약 3배. 코드 없는 셀은 라벨로 복원 가능 |
| ⑬ | 뷰 카탈로그 | `data/stage/catalog.duckdb` 뷰 전용 파일 (P1 에 포함) | 복사 없음. 안 만들면 MANIFEST 경로 직접 |
| ⑭ | PR 묶음 | **P1 + P2 한 PR**(파서 골격 공유, equity 가 둘 다 대기) | 분리하면 파서를 두 번 검수 |
| ⑮ | 골든셋 | 분기·반기 표 10~15개 교체 후 라벨링 | 현재 100표에 분·반기 없음(§1.15) |

## 6. 단계

| 단계 | 산출 | 게이트 | 고객 | 시간(추정) |
|---|---|---|---|---|
| P1 | 사전 census(`survey/doc_census.py`) · `doc_prepass`·`rules_doc`·`parsers_doc`·`doc_vocab` · 빌더 변경 ①②④(§2.5) · `stg_doc_meta`·`section`·`correction`·`parse_log` 전량 · 카탈로그 뷰 | G0~G9 + D0~D6·D8·D10·D12 | equity 4단계 정정 링크(§8.1) | 프리패스 1.2h + duckdb 수 분 |
| P2 | `stg_fin_asreported`(격자 전개·기간·단위·값·판본) · 빌더 변경 ③ 연도 샤드 | D9·D11·D13 | equity 4단계 PIT 재무 원본 | 벤치 후 확정(1~2h 추정) |
| P3 | `stg_doc_form_cell` · `stg_doc_table` | D11·D12·D14 | 2011~2014 주주·임원·주식수, as-reported 검산, §8.3 입력 | 1~2h 추정 |
| P4 | 자유표 값 층(§8.3) · 텍스트 절 · HTML 주요사항보고서 | 골든셋 정확도 | 팩터 | 골든셋 결과 후 |

P1 첫 세션(승인 후): `git checkout -b stage/doc-p1 origin/main` → `doc_vocab` 후보 생성(census) → 정제기·파서 TDD(§2.3 다섯 규칙 각각 실패→통과, S26 조각 픽스처) → `rules_doc` 4테이블 선언 → 서버 S26 로 G4 고정 → 전량 빌드 → baseline 승인.

## 7. 결정 기록 · 교훈

| # | 결정 | 일자 |
|---|---|---|
| — | L1-0 표본 S26 + Y2040/Y510 + C340 실측, 정제 5규칙·태그 41·세대 4 확정 | 09-03 |
| — | 검수 1·2회 반영(모집단 원장 전건, `candidate_status`·`date_check`, G6·G7 분자·분모, `scope` 결정표, `filed_raw` 앵커, G2 표본, G5 조건, lenient 규칙) | 09-03 |
| — | 무손실 검증(§1.12) → G10·G11 | 09-03 |
| — | 구조 전환 시점(§1.2 전환표, Y1676) · 양식 커버리지(§1.13) · 자유표 정형성(§1.14) · LLM 파일럿·골든셋(§1.15) · 내용 구조도 7건 | 09-03~04 |
| — | **v1.1: stage 소스로 재배치** — `data/stage/stg_doc_*` 7테이블, `src/stage/` 빌더 재사용(변경 4건 §2.5), 정정 링크 equity 이관(§8.1), 어휘 사전 6종, 문서 게이트 D0~D14, 결정 ⑧⑨ 철회·⑪~⑮ 신설 | 09-04 |
| — | 최종 검수(3회) 반영: `stg_doc_correction` = ZIP 있는 것만(원장 3개 합치기 금지) · `vintage_kind` original/corrected · 빌더 변경 4건 명시 · 게이트 D 명명과 stage G0~G9 기대 · `period_slot`·EF 행 축·NT 제외 · `signature_hdr/ctx` · form_cell 범위 · 규모 산식·스필 조건 · D13 술어 | 09-04 |
| — | 사용자 결정: Claude API 미사용, 골든셋 판정은 이 세션에서 | 09-03 |
| **①~⑮** | **사용자 승인 — 권고안 그대로 진행** (아티팩트 "문서층 결정 15"로 검토). 다음: PR #37 병합 → `stage/doc-p1` 에서 P1+P2 구현 | **09-04 사용자 확정** |

**교훈**:
⑪ **stage 표 하나에 원장을 셋 합치지 않는다** — 정정 표에 원장 메타·ZIP 메타·첫 장 원문을 한 행에 넣으려다 §1 경계를 넘었다. 다른 원장의 컬럼은 equity 가 `rcept_no` 로 붙인다. ⑫ **판본 열은 문서 자신에서** — 정정 문서의 재무표를 `original` 로 찍으면 stage 안에서 PIT 오염. ⑬ **빌더 계약은 코드로 확인** — "FileSource 하나만 추가" 는 `build_table` 이 테이블당 파서 1회인 구조를 못 본 주장이었다.
① ZIP 첫 멤버 = 본문 가정 — 멤버는 basename 으로. ② 앞부분만 디코딩한 인코딩 판정 — 전문 strict 로만. ③ `XMLParser.entity` 는 DTD 없는 문서에 무력 — 정제로 치환. ④ `<P>` 기반 필드 추출 — 2025~ 는 `<TD>`; 요소 평문에 정규식. ⑤ 속성 복구 정규식 부작용 — 전수 표본 재검증 후 확정. ⑥ "`<`+영문자 = 태그" — 화이트리스트로만. ⑦ 게이트 상수의 방향 — 2×2 표 통째로. ⑧ 측정 술어 ≠ 게이트 술어 — 같은 코드로 잰 값만. ⑨ **세대 특징의 오귀속** — `SECTION-3`·`XII`·`L-0-2` 를 G3 로 적었다가 접수월×개정일 실측으로 2021-08 로 정정. 구조 전환은 "세대" 가 아니라 서식 개정일(`formula_date`)로 잰다. ⑩ 셸 안 파이썬은 파일로 — 작은따옴표가 ssh 인용을 깨뜨려 f-string 이 변수로 해석됐다.

**미결**: STAGE_HANDOFF §4 의 "정정 있음 그룹 20,759" 가 §1.7 정정 접수 20,579 보다 커서 술어가 다름(그룹 술어에 `[첨부추가]`·`stg_disclosure.is_correction` 포함 추정) — E-G7 분모 확정 전에 대응 · 첨부 `TOT_ASSETS` 단위 · `IS2/IS3` 의미(손익·포괄손익 분리 추정) · `[첨부정정]` 정정대상이 감사보고서일 때 `kind` 매칭 · 2010~2012 K-GAAP `SA` 표·`scope='U'` 의 equity 해석 · 킥오프 "정정 ZIP 18,013/21,138" 과 §1.7 전수의 술어 대응 · `stg_fin_asreported` long 2.2억 행 벤치 · 2010 `A01`~`A26` 코드 라벨 역산.

## 8. equity 인계 명세 (stage 밖)

### 8.1 `correction_link` — 정정 rcept_no 1행, 원장 전건 (equity 4단계 `disclosure_version` 의 입력)
모집단 = `stg_disclosure` 에서 §1.7 전수 술어(정기보고서 3종·연장신고 제외·`stock_code<>''`) + 접두 `[기재정정]|[첨부정정]` = 20,579(09-03). 여기에 `stg_doc_correction`(ZIP 있는 17,603) 을 `rcept_no` 로 LEFT JOIN — 없으면 `date_check='no_zip'`. 후보 = 같은 `corp_code` · 같은 `kind` · 같은 `period_label` · report_nm 접두 ∉ {[기재정정],[첨부정정]}(`[첨부추가]` 는 원본 라벨이라 포함) · `rcept_no` < 정정 `rcept_no`.
- `candidate_status`: `unique`(후보 1 → `orig_rcept_no`) / `none`(후보 0, 2010 이전 원본 → NULL) / `multi_resolved`(후보 2+ 중 `filed_date = rcept_dt` 가 정확히 1) / `multi_unresolved`(→ NULL). C340 실측(원장 규칙만): unique 339 · none 1 · multi 0.
- `date_check`(링크 성립과 독립): `exact` / `off_1d` / `off_2_7d` / `mismatch`(8일+) / `unparsed` / `no_page` / `no_zip` / `n/a`(후보 미확정). C340: exact 273 · off_1d 3 · off_2_7d 5 · mismatch 10 · unparsed 45.
- `prior_corr_count` = 같은 (corp_code, kind, period_label) 의 정정 접수 중 `rcept_no` 가 더 작은 것의 수(ZIP 무관). 정정의 정정도 `orig_rcept_no` 는 최초 원본.
- 게이트(equity): **E-G6a** 링크 성립 `candidate_status ∈ {unique, multi_resolved}` ≥ 99%(C340 339/340) · **E-G6b** `exact+off_1d / 해석된 행` 기록(C340 276/291 = 94.8%) · **E-G7** `rm` 에 `정` 인 원본(전 연도·3종) 중 링크로 도달되는 비율 ≥ 99%(2015~2024 사업보고서 6,156/6,162 = 99.9%, `[첨부정정]` 포함 — 빼면 96.0%).
- 계약: `has_correction(orig) = EXISTS(correction_link WHERE orig_rcept_no = orig)`, `correction_rcept_no`·`corrected_at`(복수면 전부), 기준일 D 의 판본 = `rcept_dt ≤ D` 인 것 중 최신(STAGE_HANDOFF §4).

### 8.2 `fin_std` 의 판본 결합
`stg_fin`(API, `restated_unknown`) 과 `stg_fin_asreported`(`original` / `corrected`) 를 `vintage_kind` 로 한 시계열에. 계정 매핑은 `fin_map.py`. 정정 없는 보고서는 두 소스가 100% 일치해야 하고(G9), 정정 있는 원본의 차이가 곧 정정 폭이다.

### 8.3 자유표 값 층 (P4)
입력 `stg_doc_table`(좌표·헤더·서명·단위 문맥). 절차: 서명 군집화(§1.14, 상위 1,000 군집 = 60%) → 군집당 1회 스키마 판정(열 역할·단위·주제·합계행; 판정자는 이 세션의 Claude, 골든셋 §1.15 로 정확도 측정 후) → 사람이 확인해 규칙으로 고정 → 코드가 셀에서 값 복사 → 검산(원문 대조·항등식·API 교차·삼각검증) 통과분만 값, 나머지 `miss_kind`. 긴 꼬리는 커버리지·검산 통과율을 지표로 관리하고 100% 를 목표로 삼지 않는다.
