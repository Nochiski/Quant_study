# 문서층(L1) 설계 v1 — DART 보고서 ZIP 파싱 (2026-09-03, L1-0 표본 검토 결과 · 검수 1회 반영)

> `DOC_LAYER_KICKOFF.md` 의 L1-0(연대별 표본 검토) 산출물. 이 문서는 **실측(§1) → 파서 계약(§2) → 산출 테이블(§3) → 게이트(§4) → 결정 요청(§5) → 단계(§6)** 순이다.
> 원칙은 stage 와 같다(`STAGE_DESIGN.md` §1·§2·§9): 원문 불변 · 접수번호 키 parquet · MANIFEST 경유 · 게이트 · 재현성 · 실측 기록.
> 수치는 전부 서버 원장(`~/quant-ledger/data/raw/documents`, `dart.db`) 읽기 전용 실측이며, **표본 유래 수치는 표본 크기와 추출 술어를 병기**한다(stage 교훈 ④·⑤). 표본 3종의 술어는 §1.0.

## 0. 범위

**포함**: `doc_store.zip_ok=1` 171,179건(사업·반기·분기보고서 + 주식분할·병합결정)의 본문·첨부 XML 파싱 → 문서 메타 · 목차 · 정정 링크 · as-reported 재무표 · (후속) 텍스트. **단 `correction_link` 만은 ZIP 유무와 무관하게 원장의 정정 접수 전건**(§3.3) — ZIP 없는 정정 2,975건(DART `014`)을 빠뜨리면 그 원본이 equity 에서 "정정 없음"으로 읽혀 restated 값이 원본 행세를 한다(silent PIT 오염).
**제외 (사유 명기)**:
- **HTML 주요사항보고서**(주식분할·병합결정, 표본 510 중 5 = 1.0%, 원장 1,403건 = ZIP 1,253 · `014` 150) — DART XML 이 아니라 `<html>` 문서(§1.2 H 세대). 소형(10KB)·표 1개라 별도 소형 파서. v1 은 `doc_meta.format='html'` 만. 입력 `zip_ok=1` 171,179 = 정기보고서 169,929(§1.7 표) + 주요사항 1,253 − 두 술어에 겹치는 접수 3(L1-1 G0 에서 확정).
- **비XBRL 수기 재무표** — XBRL `TABLE-GROUP` 없이 편집기 표로 작성된 재무제표(SPAC·2013 이전 소형사, §1.9). v1 `fin_asreported` 는 XBRL 그룹만, 수기표는 `doc_meta.n_xbrl_groups=0` 으로 존재만 기록.
- **첨부 감사보고서 본문 텍스트**(감사의견 문단·핵심감사사항) — L1-3 텍스트 단계. 헤더 `SUMMARY/EXTRACTION`(감사인·의견코드·총자산) 은 v1 `doc_meta` 에 싣는다(§1.10).
- **정정 전·후 diff 의 해석**(어느 계정이 얼마 바뀌었나) — `correction_link.items` 에 정정사항 표 **전 행** 원문 텍스트를 보존하고 해석은 후속.
- **일일 증분** — stage 와 동일하게 1회 풀 빌드 후 별도 설계. 접수번호 키라 `doc_store` 신규 행만 파싱하면 되는 구조는 유지한다.

## 1. 실측 (L1-0)

### 1.0 표본 3종 — 추출 술어
| 표본 | 크기 | 술어 | 쓰인 곳 |
|---|---|---|---|
| S26 구조 표본 | 5년 × 5구분 = 25 + G2 보강 1 | `dart_disclosure ⋈ doc_store(zip_ok=1)`, 연도 = `substr(rcept_no,1,4)` ∈ {2010,2013,2016,2020,2024}, 구분별 `report_nm` 조건(§1.1) 후 `ORDER BY rcept_no LIMIT 1 OFFSET k`(A·B·C k=100, D k=50, E k=20/20/5/5/5, 없으면 k=0). G2(dart3 평면) 보강 = 2022 사업보고서 원본·멤버 3 중 `OFFSET 100` 부터 헤더 래퍼 없는 첫 문서 | §1.1·§1.6~§1.10, G4 픽스처 |
| Y2040 / Y510 연도 표본 | 17년 × 120 = 2,040 / 17년 × 30 = 510 | 연도 디렉토리의 ZIP 파일명(= 접수번호) 정렬 후 `stride = n // N` 등간격 앞 N건 — 연중 고르게 분포(1~2월 편향 없음) | §1.2(xsd·헤더·HTML 은 앞 4KB 정규식 = Y2040, 바이트 인코딩·태그·엔티티·파싱은 전문 = Y510) |
| C340 정정 표본 | 17년 × 20 = 340 | `dart_disclosure ⋈ doc_store(zip_ok=1)`, `report_nm LIKE '[%정정]%'` AND 정기보고서 3종, 연도별 `ORDER BY rcept_no` 후 `stride = n // 20` 등간격 20건 | §1.7, G6·G7 초기 임계 |

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
- **목차·섹션**: `TITLE[@ATOC="Y"][@AASSOCNOTE]` — `AASSOCNOTE` 가 세대를 넘는 **섹션 식별 코드**. `D-0-3-2-0` 연결재무제표 · `D-0-3-3-0` 연결 주석 · `D-0-3-4-0` 재무제표 · `D-0-3-5-0` 주석 · `TTL_CEO_CERT` · `COVER` · `TTL_APPENDIX`. 2010~2013 은 재무제표가 `XI. 재무제표 등`(`D-0-11-0-0`) 아래, 2016~ 은 `III. 재무에 관한 사항` 의 2·4 절 — 전환 연도(2014~2015)는 L1-1 전수 census 로 확정. G3 는 `ATOCID`(문서 내 순번) 추가, 사업의 내용 하위는 `L-0-2-n-L1`.
- **표**: `TABLE[@ACLASS]` = NORMAL | EXTRACTION. `TABLE-GROUP[@ACLASS]` = `{XBRL}BS`·`IS1/IS2/IS3`·`EF`·`CF`·`SA`(2010 이익잉여금처분) + 접미 `_S`(별도)·`_C`(연결, G3 만) — **재무제표 기계 식별자**. 2026 문서는 주석도 `{XBRL}NT_S_D8xxxxx` 그룹으로 온다(L1-3 재료). 그 외 `COVER`·`PB_VAL`·`TOT_STK`·`FUND_SA` 등 표준 서식 표.
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
| 원본 접수번호 명시 | **0/339** | 링크 근거는 날짜뿐 → §3.3 규칙 |

**원장 전수(정의 = `backfill_docs.py` P2·P3 술어: `report_nm` 이 사업/반기/분기보고서 · `연장신고` 제외 · `stock_code <> ''` · DISTINCT rcept_no; 09-03 측정)**:

| 접두 | 접수 | ZIP 있음(`zip_ok=1`) | DART `014`(ZIP 없음) | `doc_store` 행 없음 |
|---|---|---|---|---|
| 없음(원본) | 149,878 | 149,873 | 4 | 1 |
| `[첨부추가]` | 2,454 | 2,453 | 1 | 0 |
| `[기재정정]` | 19,543 | 17,185 | 2,357 | 1 |
| `[첨부정정]` | 1,036 | 418 | 618 | 0 |
| 합 | 172,911 | 169,929 | 2,980 | 2 |

정정(`[기재정정]+[첨부정정]`) = **20,579 = ZIP 있음 17,603 + `014` 2,975 + `doc_store` 행 없음 1**. 접두 집계는 DISTINCT (rcept_no, 접두) 라 같은 접수가 나중에 `[첨부추가]` 로 재명명되면 두 접두에 걸릴 수 있다(합 172,911 vs 킥오프 대상 172,906) — G0 에서 rcept_no 단위로 확정. 킥오프의 "정정 ZIP 18,013/21,138" 은 다른 술어(`stg_disclosure.is_correction` 추정)라 대응 관계는 L1-1 에서 맞춘다. **`[첨부추가]` 는 새 접수가 아니라 원본 rcept_no 에 붙는 라벨**(ZIP 도 본문+첨부 3멤버) — 원본 후보에서 빼면 안 된다.
`rm` 플래그의 `정` = "이 접수에 나중에 정정이 붙었다". 2015~2024 사업보고서 원본(접두 없음+`[첨부추가]`, `stock_code<>''`) 24,671건 × 후속 정정(`[기재정정]|[첨부정정]`, 같은 corp·기간 라벨):

| `rm` 에 `정` | 후속 정정 | 건수 |
|---|---|---|
| 있음 | 있음 | 6,156 (기재정정 5,918 · 첨부정정만 238) |
| 있음 | 없음 | 6 |
| 없음 | 있음 | 5 |
| 없음 | 없음 | 18,504 |

플래그→정정 6,156/6,162 = 99.9%, 정정→플래그 6,156/6,161 = 99.9%. `[첨부정정]` 을 빼면 플래그→정정이 5,918/6,162 = 96.0% 로 떨어진다 — **링크 모집단에 `[첨부정정]` 이 반드시 들어가야 한다.** 현재 시점 값이라 PIT 에는 못 쓰지만 링크 커버리지 검산축(G7)으로는 최적.

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
- 그룹 존재: S26 → 2010 5(BS·IS·SA·EF·CF) · 2013 0~6 · 2016 8(연결 4+별도 4) · 2020 8~10(IS2+IS3 = 손익·포괄손익 2표 추정, §7 미결) · 2022 8 · 2024 8, 반기는 2016·2020 8 / 2024 4(연결 없는 회사). **0 인 원본 문서**: 2013-A 에프엔에스테크·2016-B 디알텍(SPAC) — 편집기 수기표(`◆click◆『XBRL 재무제표』 삽입` 안내문 잔존, "주권상장법인은 XBRL 재무제표를 삽입"). 존재율은 L1-1 census 로 연도·시장별 전수 측정.

### 1.10 첨부 감사보고서 헤더 — 구조화 메타
`_00760/_00761` 의 `SUMMARY/EXTRACTION`: `AUDIT_CIK`(감사인 코드) · `SUPV_OPIN`(의견 코드 `100000000000`) · `GMSH_DATE`(주총일) · `CRP_RGS_NO` · `FIN_STAT` · `TOT_ASSETS/TOT_DEBTS/TOT_SALES`(2020~, 백만원 추정 — 실측 필요) · `TOT_EMPL` · `IFRS_YN` · `FIN_YN` · `LINK_FLAG` · `PREV_IACS`. 본문 헤더는 `LINK_FLAG`·`FIN_TYPE`·`IFRS_YN`·`CRP_RGS_NO_TEMP`·`RPT_TP`. `stg_audit`(API) 와 교차 검산 가능한 공짜 열.

### 1.11 성능 — 스트리밍 불필요
Y510 XML 505건 653MB: 정제 7.45초 + expat 16.72초 = **문서당 48ms**, 최대 파일 15.5MB(전문 로드 시 텍스트 ~30MB). 전량 171,179건 ≈ 2.3시간(파싱만) → ZIP 해제·디코딩·산출 포함 ×1.5 ≈ 3.5시간 단일 코어, 3 워커 ≈ 1.2시간. 정정 문서만 재파싱은 17,603 × 50ms ≈ 15분. 킥오프의 "`iterparse` 스트리밍" 문구는 **문서 단위 전체 로드로 대체**(§5 ②) — 트리 전체가 있어야 `AASSOCNOTE`→하위 표 탐색이 단순하다. 관대한 대체 파서(`html.parser`, 순수 파이썬)는 같은 문서에 0.34초 — 실패분에만 쓴다.

### 1.12 무손실 검증 — 텍스트·속성은 등식으로, 표 기하는 L1-2 요구로
Y510 XML 505건에 대해 정제→expat 트리를 원문과 등식 비교(09-03):

| 검증 | 술어 | 결과 |
|---|---|---|
| 텍스트 등식 | `"".join(root.itertext()).strip()` = 정제 원문에서 주석·선언·태그(`<[^>]+>`)를 지우고 `html.unescape` 한 문자열 `.strip()` | **505/505 동일** — 정제 5규칙(엔티티 치환·맨 `&`/`<` 이스케이프·속성 복구)은 글자를 하나도 바꾸지 않는다 |
| 속성 수 등식 | 원문 시작 태그의 `name="…"` 수 = `Σ len(el.attrib)` | 505/505 에서 **정확히 1 차이** = 루트의 `xmlns:xsi` 를 ET 가 네임스페이스 선언으로 빼는 것(`noNamespaceSchemaLocation` 은 남음). 손실 아님 |
| 디코딩 치환 | `errors='replace'` 경로 진입 문서 | 0/505 |
| 태그 구조 | 화이트리스트 밖 토큰은 텍스트로 강등 | 텍스트로는 보존(위 등식에 포함)되지만 **구조는 잃는다**. 표본에서 강등된 토큰은 전부 본문 텍스트(≤2건/505)였고, 전량 census(G3) 가 새 태그를 잡는다 |
| 재무표 셀 병합 | XBRL `TABLE-GROUP` 안 `TD/TH` 의 `ROWSPAN`·`COLSPAN` | **ROWSPAN 8,413 · COLSPAN 19,137 셀, 505 중 430 문서** — 분·반기 손익(`3개월/누적` 2단)·자본변동표(구성요소 2단)의 헤더. 트리는 속성을 그대로 갖고 있으므로 파싱 손실은 없지만, **L1-2 가 격자 전개 없이 열 순서로 기간을 붙이면 값이 옆 기간으로 밀린다** |

따라서 L1-1 산출은 원문 대비 무손실이 등식으로 보장되고(G10 으로 매 문서 판정), 손실이 생길 수 있는 지점은 두 곳뿐이다: ① 화이트리스트 밖의 **진짜** 태그(구조 강등 — G3 어휘 게이트), ② L1-2 의 표 격자 전개(§3.4 요구 + G11). 원천 자체의 손실은 파서와 무관하게 존재한다 — ZIP 에 이미지 파일이 없어 `IMAGE/IMG` 는 참조만 남고, ZIP 없는 정정 2,975건은 원장으로 보완한다(§3.3).

## 2. 파서 계약

### 2.1 파이프라인 (문서 = ZIP 1개, 멤버마다 반복)
```
zipfile → 멤버 basename 매칭(role: main | audit(_00760) | audit_cons(_00761) | other)
  → 바이트 인코딩 판정(utf-8 strict → cp949 strict → cp949 replace + n_repl)          §1.3
  → HTML 판정(`<html` in 앞 4KB, 대소문자 무시) → parse_mode=html, <title>·표 수만 기록, 종료
  → 정제 5규칙(§2.2)
  → expat(`xml.etree.ElementTree.fromstring`) → parse_mode=ok
      ↳ ParseError → html.parser 대체 트리 → parse_mode=lenient
      ↳ 대체 트리도 실패 → parse_mode=failed + 오류 위치·문맥 100자 (doc_parse_log)
  → 세대 판정(xsd · 헤더 래퍼) → 헤더·SUMMARY → 목차(TITLE ATOC) → CORRECTION → XBRL 그룹
  → JSON Lines 로 쓰고 duckdb read_json 한 번에 parquet (stage 교훈 ⑥)
```
- `parse_mode` 어휘는 **ok / lenient / failed / html** 넷뿐(§3.1·§3.5·G1 공통). `other` 역할 멤버(정규식 밖 이름)도 파싱은 시도한다 — 건너뛰는 상태는 없다.
- **lenient 규칙**: `html.parser.HTMLParser` 서브클래스로 트리 구축. **태그명과 속성명을 모두 대문자화**(HTMLParser 는 둘 다 소문자로 준다 — 안 올리면 `TITLE[@ATOC]` 탐색이 조용히 0건) 해 화이트리스트와 대조, 밖의 태그는 요소를 만들지 않고 자식을 부모에 붙인다(`lenient_unknown_tags` 기록). 종료 태그는 스택을 거슬러 같은 이름까지 닫고 없으면 무시. **실패 판정** = 예외 발생 · 루트 직계에 `DOCUMENT` 부재 · 최소 내용 미달(`toc_n = 0 ∧ n_tables = 0` — 초반에 잘린 트리). Y510 의 유일한 expat 실패 문서(속성 따옴표)는 이 경로로 19,693 요소·목차 102·XBRL 그룹 48 을 복원했다(속성 복구 규칙 추가 후엔 expat 으로도 통과).
- 표준 라이브러리만(`zipfile`·`re`·`xml.etree`·`html.parser`·`json`) + duckdb(산출). 새 의존성 없음.
- 정제·어휘·정규식 상수는 `src/doc/vocab.py` 한 곳(태그 41 · AASSOCNOTE 맵 · XBRL ACLASS 맵 · 날짜·기간·단위 정규식) — 코드 규칙 "SoT 하나".
- 실패는 값으로(`ParseResult.status`, python 규칙 errors-as-values) — 문서 하나가 죽어도 배치는 계속, 전부 로그에 남는다.
- 전 문서 `rcept_no` 키 · 산출 `~/quant-ledger/data/doc/<table>/v=<build>/year=YYYY/*.parquet` + `MANIFEST.json`(`src/stage/manifest.py` 재사용, keep=3) + `_meta.json`(게이트·카운터). 골든 픽스처는 stage 와 같이 코드 옆 `src/doc/fixtures/<table>.json`.

### 2.2 정제 5규칙 (순서 고정, 전부 카운터 기록)
1. `[\x00-\x08\x0b\x0c\x0e-\x1f]` 제거 → `n_ctrl`
2. `&name;` — 표준 5개 유지 · `cr` → `&#10;` · 그 외 → `[&name;]` 문자열 보존 + `n_ent_other`(G3 어휘 게이트)
3. `&` 뒤에 `(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);` 가 아니면 `&amp;` → `n_bare_amp`
4. `<` 뒤가 `/?(화이트리스트 41)(?=[\s/>])` · `!` · `?` 가 아니면 `&lt;` → `n_bare_lt` + 치환된 토큰(§1.6 정규식에 걸리는 것만) 어휘
5. `=""(?=[^\s"<>])([^"<>=]*)"` → `="\1"` → `n_attr_repair`

## 3. 산출 테이블 v1 (접수번호 키, 모두 `data/doc/`)

### 3.1 `doc_meta` — 1행 / (rcept_no, member)
`rcept_no` · `member_name` · `member_role`(main/audit/audit_cons/other) · `format`(xml/html) · `gen`(dart3_hdr/dart3_flat/dart4/html/unknown) · `xsd` · `formula_version` · `formula_date` · `doc_acode` · `doc_name` · `corp_cik`(AREGCIK) · `company_name_doc` · `byte_enc` · `n_repl` · `n_ctrl` · `n_ent_other` · `n_bare_amp` · `n_bare_lt` · `n_attr_repair` · `parse_mode`(ok/lenient/failed/html) · `n_elements` · `n_tables` · `toc_n` · `period_from`·`period_to`(TU PERIODFROM/TO, DATE) · `has_correction_page` · `n_xbrl_groups` · `xbrl_aclass`(LIST) · `summary`(JSON: SUMMARY EXTRACTION 코드→값) · `cover`(JSON: 표지 TE ACODE→값) · `bytes_xml`
- `available_date` 는 stage `stg_rcept_dt_map` 참조(derived) — 문서층은 새 날짜를 만들지 않는다(STAGE §6).
- 검산: `corp_cik` = `dart_disclosure.corp_code` 100%, `doc_acode` ↔ report_nm 종류 100%(사업 11011·반기 11012·분기 11013), `[첨부정정]` 은 main 부재 허용.

### 3.2 `doc_section` — 1행 / (rcept_no, member, ordinal) — 목차만
`ordinal`(문서 순서) · `section_code`(AASSOCNOTE, NULL 허용) · `atocid` · `level`(SECTION-1/2/3 깊이) · `title`(원문) · `path`(태그 경로, LIBRARY/INSERTION 포함) · `elem_start`(요소 순번) · `n_tables` · `n_xbrl_groups` · `n_chars`(본문 텍스트 길이)
- 용도: L1-2·L1-3 이 표·텍스트를 다시 찾을 때의 좌표. 섹션 본문은 싣지 않는다(가벼운 패스).

### 3.3 `correction_link` — 1행 / 정정 rcept_no, **원장 전건** (equity 4단계 `disclosure_version` 의 입력)
모집단 = `dart_disclosure` 에서 §1.7 전수 술어 + 접두 `[기재정정]|[첨부정정]` = 20,579건. ZIP 유무는 열로만 구분한다.
`corr_rcept_no` · `corr_rcept_dt` · `corp_code` · `kind`(사업/반기/분기) · `period_label`(`YYYY.MM`) · `corr_prefix` · `zip_ok` · `page_found` · `target_raw` · `filed_raw` · `filed_date`(DATE, NULL 허용) · `reason_raw` · `n_items` · `items`(JSON, **전 행**: 정정사항 표의 헤더 라벨을 키로 한 행 목록 — `{"항목":…, "정정요구ㆍ명령관련 여부":…, "정정사유":…, "정정 전":…, "정정 후":…}`, 없는 열은 키 없음) · `orig_rcept_no` · `orig_rcept_dt` · `n_candidates` · `candidate_status` · `date_check` · `prior_corr_count`
- **원본 확정 규칙(§5 ③)**: 후보 = §1.7 정의(`dart_disclosure` 단독). `candidate_status` = `unique`(후보 1 → `orig_rcept_no` 확정) / `none`(후보 0, 2010 이전 원본 등 → NULL) / `multi_resolved`(후보 2+ 중 `filed_date = rcept_dt` 가 정확히 1 → 그것) / `multi_unresolved`(그 외 → NULL). C340 실측(원장 규칙만으로 340 전건): unique 339 · none 1 · multi 0.
- **`date_check`** = XML 최초제출일(`filed_date`) 과 `orig_rcept_dt` 의 비교 딱지, 링크 성립과 독립: `exact` / `off_1d` / `off_2_7d` / `mismatch`(8일+) / `unparsed`(페이지는 있으나 날짜 미해석) / `no_page`(ZIP 은 있으나 CORRECTION 없음) / `no_zip`(`zip_ok IS NOT TRUE` — `doc_store` 행 없음 포함) / `n/a`(`candidate_status ∈ {none, multi_unresolved}` 라 비교 대상 없음). `filed_raw` 는 `최초제출일` 앵커 뒤 부분 문자열(§1.7). `items` 의 원천 = CORRECTION 안에서 헤더 행에 `항목` 셀(공백 제거 후 비교)이 있는 첫 `TABLE`.
- `prior_corr_count` = 같은 (corp_code, kind, period_label) 의 정정 접수 중 rcept_no 가 더 작은 것의 수(ZIP 무관). 정정의 정정(체인)도 `orig_rcept_no` 는 항상 최초 원본(첫 장 문구가 "최초제출일") — 체인 순서는 `prior_corr_count` + rcept_no.
- equity 계약: `has_correction(orig) = EXISTS(correction_link WHERE orig_rcept_no = orig)`, `correction_rcept_no`·`corrected_at = corr_rcept_no·corr_rcept_dt`(복수면 전부 — 판본 선택은 equity), 기준일 D 의 판본 = `rcept_dt ≤ D` 인 것 중 최신(STAGE_HANDOFF §4).

### 3.4 `fin_asreported` (L1-2) — 1행 / (rcept_no, member, aclass_raw, row_ord, col_ord)
`aclass_raw`(`{XBRL}IS_S1` 등 원문 — 키) · `stmt`(BS/IS/EF/CF/SA — ACLASS 의 문자 접두로 파생, `IS1/IS2/IS3` 는 전부 IS 이고 구분은 `aclass_raw`) · `scope`(C 연결 / S 별도 / U 미구분 — §1.9 규칙) · `stmt_title_raw` · `period_label`(열 라벨 원문 `제 54 기`) · `period_from`·`period_to`(헤더 표 파싱, NULL 허용) · `sub_label`(3개월/누적/NULL) · `row_ord` · `col_ord` · `account_raw`(원문, 번호 접두 보존) · `account_norm`(공백 제거·선행 번호/로마자/괄호 번호 제거·NFKC) · `value_raw` · `value_krw`(Decimal(38,4) = 숫자 × `unit_scale`; 콤마 제거·괄호 음수·`''`/`-` → NULL + `miss_kind`=blank/dash; 비KRW 단위면 NULL + `miss_kind`=non_krw) · `unit_raw`·`unit_scale`(`(단위 : 원)`→1, 천원→1e3, 백만원→1e6, 억원→1e8; 그 외·부재 → `unit_scale` NULL + `value_krw` NULL + `miss_kind`=unit_unknown)
- **격자 전개 필수**: 헤더 표·본문 표 모두 `ROWSPAN/COLSPAN` 을 전개해 (row, col) 격자로 만든 뒤 열 라벨(기간·`3개월/누적`)을 붙인다(§1.12 — 505 중 430 문서에 병합 셀). `col_ord` 는 전개 후 열 번호.
- 계정 표준화(`account_std`)는 싣지 않는다 — 원문 라벨이 as-reported 의 본질. 표준 매핑은 `src/fin_map.py` 재사용해 equity 에서.
- **검산축(G9)**: 정정 없는 보고서(`rm` 에 `정` 없음)의 당기 값 ↔ `stg_fin.thstrm_amount`(같은 rcept_no, `account_norm` = `account_nm` 정규화 정확 일치, `stg_fin` 커버리지 2015~) 100% 일치. 정정 있는 원본은 불일치가 "정정 신호" 라 기대값이 다르다 — 두 집단으로 나눈다.

### 3.5 `doc_parse_log` — 1행 / (rcept_no, member)
`parse_mode`(ok/lenient/failed/html) · `error` · `error_ctx`(100자) · `t_decode_ms`·`t_sanitize_ms`·`t_parse_ms` · 정제 카운터 5개 · `unknown_tags`(치환된 `<Name` 토큰 list) · `lenient_unknown_tags`

## 4. 게이트 (stage §9 어휘 승계)
분류: **폐기형**(실패 시 빌드 폐기) · **격리형**(행 격리 + 임계) · **기록형**(분포를 `_meta.json`·`baseline.json` 에 적고 사람이 승인). 첫 빌드에 baseline 이 없는 게이트는 `skip(no_baseline)` 으로 기록하고 두 번째 빌드부터 판정(stage G5 규약). "기록형→격리형" 은 첫 풀 빌드 분포를 사람이 승인해 `baseline.json` 에 넣는 시점에 전환. L1-2 전에는 G9 `skip(not_built)`.

| # | 이름 | 분류 | 술어 | 임계(초기·근거) |
|---|---|---|---|---|
| G0 | 실물 계약 | 폐기형 | 입력 = `doc_store.zip_ok=1` 171,179 · 멤버 basename 정규식 `^\d{14}(_\d{5})?\.xml$` 밖 이름은 `role=other` 로 세되 ZIP 열기 실패 0 | 위반 0 |
| G1 | 건수 등식 | 폐기형 | rcept_no 마다 `doc_meta` ≥1행 · Σ(ok+lenient+failed+html) = 멤버 수 | 등식 |
| G2 | 파싱 실패율 | 격리형 | `failed / (ok+lenient+failed)` | ≤ 0.5% (Y510: 0/505) |
| G3 | 어휘 폐쇄 | 폐기형 | 규칙 4 로 치환된 `<Name` 토큰 중 문서 빈도 ≥ 1% 인 어휘 = 0 · 규칙 2 의 `n_ent_other` 어휘 중 문서 빈도 ≥ 1% = 0 | 0 (Y510: 최대 2건/505) |
| G4 | 골든 픽스처 | 폐기형 | S26 26건(G1 19 · G2 1 · G3 4 · HTML 2)의 (gen, byte_enc, doc_acode, formula_version, toc_n, has_correction_page, n_xbrl_groups·xbrl_aclass, period_from/to) 를 `src/doc/fixtures/doc_meta.json` 에 고정 | 전부 일치 |
| G5 | 재현성 | 폐기형 | stage `_content_hash` 규약 재사용: `count:bit_xor(hash(row))` — 행 순서·워커 수 무관. 입력 스냅샷 = `zip_ok=1` rcept_no 집합의 해시를 `_meta.json` 에 기록 | 입력 해시 같으면 Δ=0 · 다르면 Δ행수 = 신규 멤버 수(등식) — 두 번째 빌드부터 |
| G6a | 정정 링크 성립 | 기록형→격리형 | 모집단 20,579 전건 1행 · `candidate_status ∈ {unique, multi_resolved}` 비율 | ≥ 99% (C340: 339/340 = 99.7%, 유일 실패 = 2010 이전 원본) |
| G6b | 날짜 검증 | 기록형 | `date_check` 분포(zip 있는 행 기준) · `exact+off_1d / (해석된 행)` | 기록 (C340: 276/291 = 94.8%) → baseline |
| G7 | 원장 교차 | 격리형 | `rm` 에 `정` 인 원본(전 연도·3종) 중 `correction_link.orig_rcept_no` 로 도달되는 비율 | ≥ 99% (2015~2024 사업보고서 6,156/6,162 = 99.9%, `[첨부정정]` 포함) |
| G8 | XBRL 존재율 | 기록형 (L1-1a) | `doc_meta` 만으로 계산: `member_role=main` 사업보고서 중 `n_xbrl_groups ≥ 4` 비율, 연도·시장별 | 기록 → 2016~ ≥ 95% 예상, baseline. L1-2 범위(수기표 비중) 결정의 입력 |
| G9 | 재무 교차(L1-2) | 격리형 | §3.4 분모(정정 없음 · 2015~ · `account_norm` 정확 일치 행) 의 `value_krw = thstrm_amount` 비율 | 100% · 2010~2014 는 `skip(no_stg_fin)` |
| G10 | 텍스트 등식 | 폐기형 | 문서마다 §1.12 텍스트 등식 (lenient 경로 포함 — 대체 트리는 `strip` 대신 공백 정규화 후 비교) | 위반 0 (Y510: 505/505) |
| G11 | 표 격자(L1-2) | 격리형 | `ROWSPAN/COLSPAN` 전개 후 모든 행의 폭 = 헤더 폭 · 값 셀은 span 없음 | 위반 행 격리, 비율 기록 → baseline |

## 5. 결정 요청 (권고안 — 승인 후 L1-1 착수)

| # | 결정 | 권고 | 대안·근거 |
|---|---|---|---|
| ① | 파서 | **화이트리스트 정제 + expat, 실패분만 `html.parser`** | `html.parser` 단일은 7배 느리고(0.34s vs 0.05s) 태그 무시 정책이 필요. 정제 없이 expat 은 절반이 죽는다(§1.5) |
| ② | 메모리 | **문서 단위 전체 로드(최대 15.5MB)** | 킥오프 "iterparse 스트리밍" 폐기 — 총 650MB/505건이지 문서당은 작고, 트리가 있어야 코드→표 탐색이 단순 |
| ③ | 정정 원본 확정 | **원장 후보 우선, XML 최초제출일은 검증 딱지** | XML 날짜 우선은 불일치 18/291(§1.7)을 링크 오류로 만든다. 원장 후보 유일 99.7% |
| ④ | 정정 링크 모집단 | **원장 전건 20,579(ZIP 없는 2,975 포함)** | ZIP 있는 것만 만들면 2015~2019 정정 원본이 "정정 없음" 으로 읽힘(§0) |
| ⑤ | HTML 주요사항보고서 | **v1 범위 밖, `format=html` 만** | 주식분할결정 1,403건은 equity 2단계 조정계수 검산축 — 별도 소형 파서(표 1개)로 L1-1 뒤 |
| ⑥ | 첨부 헤더 | **`SUMMARY/EXTRACTION` 을 `doc_meta.summary` 에 싣는다** | 비용 0, `stg_audit` 교차 검산 |
| ⑦ | 계정 표준화 | **원문 라벨 보존, 표준 매핑은 equity(`fin_map.py`)** | as-reported 층이 매핑을 품으면 판본이 둘이 된다 |
| ⑧ | 코드 위치 | **`src/doc/` 새 패키지(vocab·sanitize·parse·build), `src/stage/manifest.py`·`gates.py` 골격 재사용** | stage 패키지 안에 넣으면 blob 파서 계약(ParseResult)과 섞인다 |
| ⑨ | 산출 위치 | `data/doc/<table>/` + `MANIFEST.json`, `data/doc/baseline.json` | stage 와 동일 규약 |
| ⑩ | 병렬 | `multiprocessing` 3 워커, 연도 샤드 단위 JSON Lines → 연도 파티션 | 서버 CPU 4·RAM 15GB, duckdb 는 산출 단계만. 해시가 순서 무관이라 재현성과 충돌 없음 |

## 6. 단계

| 단계 | 산출 | 게이트 | 비고 |
|---|---|---|---|
| L1-1a | `src/doc/vocab.py`·`sanitize.py`·`parse.py`(헤더·목차·CORRECTION 좌표·XBRL 그룹 좌표) + TDD(픽스처 = 표본에서 오린 XML 조각, 파일 의존 금지) | G0~G5·G10 · G8 기록 · G3 census 표를 이 문서 §1.6 에 추기 | 전량 1회 실행 → `doc_meta`·`doc_section`·`doc_parse_log` |
| L1-1b | `correction_link` 빌더 — 원장 20,579 전건 + ZIP 있는 17,603 은 정정 문서만 **재파싱**(≈15분, §1.11)해 첫 장 텍스트 추출 | G6a·G6b·G7 · 분포 baseline | **equity 4단계 첫 고객** |
| L1-2 | `fin_asreported`(XBRL 그룹 2표 파서 · 격자 전개 · 기간 라벨 · 단위 · 값) | G9·G11 | 수기표는 `n_xbrl_groups=0` 존재만 |
| L1-3 | 섹션 텍스트 parquet(사업의 개요·주석 `{XBRL}NT_*`) | 팩터 요구 확정 후 | HTML 주요사항보고서 소형 파서도 여기 |

첫 세션(승인 후): `git checkout -b doc/l1-parse origin/main` → `src/doc/vocab.py` 상수 + 정제기 TDD(§2.2 다섯 규칙 각각 실패→통과) → 서버에서 `parse.py --sample` 로 S26 를 돌려 G4 픽스처 값 고정 → 전량 빌드.

## 7. 결정 기록 · 교훈

| # | 결정 | 일자 |
|---|---|---|
| — | L1-0 표본 S26 + Y2040/Y510 + C340 실측 완료, 정제 5규칙·태그 41·세대 4 확정 | 09-03 |
| — | 검수 1회 반영: `correction_link` 모집단 = 원장 전건(ZIP 무관), `candidate_status`·`date_check` 분리, G6·G7 상수 분자·분모 재도출, `fin_asreported` 키에 `aclass_raw`, `parse_mode` 어휘 4개, 정제 5규칙 명칭 통일, 표본 술어 §1.0 | 09-03 |
| — | 검수 2회 반영: C340 을 원장 규칙만으로 재분류(339/340), `scope` 결정표, `filed_raw` 앵커, G2 보강 표본(S26), G5 입력 스냅샷 조건, G8 을 L1-1a 로, lenient 속성명 대문자화·최소 내용 조건, `date_check` `n/a`·`no_zip` 정의, HTML ZIP 산술 | 09-03 |
| — | 무손실 검증 추가(§1.12): 텍스트 등식 505/505 · 속성 등식(네임스페이스 1 차이) · 재무표 병합 셀 430/505 → G10·G11, L1-2 격자 전개 요구 | 09-03 |
| — | §5 ①~⑩ 권고안 승인 대기 | — |

**교훈 (L1-0)**:
① **ZIP 첫 멤버 = 본문 가정** — 2013·2016·2020 사업보고서에서 감사보고서를 본문으로 읽었다. 멤버는 이름(basename)으로.
② **앞부분만 디코딩한 인코딩 판정** — 300KB 절단이 다바이트 경계에 걸려 utf-8 문서 6~17%를 "판정 불가"로 냈다. 전문 strict 디코딩으로만 판정.
③ **`XMLParser.entity` 는 DTD 없는 문서에 무력** — expat 이 먼저 거부. 정제 단계에서 텍스트로 치환.
④ **`<P>` 기반 필드 추출** — 2025~ 정정 첫 장은 `<TD>` 셀이라 45/336 을 놓쳤다. 요소 `itertext` 평문에 정규식.
⑤ **속성 복구 정규식의 부작용** — `=""X"` 복구가 빈 속성 `X="" Y="v"` 를 깨 2024 분기 1건이 새로 실패. 규칙은 전수 표본으로 재검증 후 확정(505/505).
⑥ **"`<`+영문자 = 태그"** — `<MS AR 사업>`·`<CP>` 로 깨짐. 태그는 화이트리스트로만.
⑦ **게이트 상수의 방향** — "99.9% 일치" 를 한 방향으로만 적어 반대 방향 술어(96.0%)에 그대로 붙였다. 2×2 표를 통째로 적고 분자·분모를 술어 옆에 둔다.
⑧ **측정 술어 ≠ 게이트 술어** — 프로토타입이 XML 단계에서 실패한 행은 원장 조회를 건너뛰어 "후보 유일 336" 이 나왔는데, 게이트 G6a 는 원장만 본다. 게이트 상수는 게이트와 같은 술어(같은 코드)로 잰 값만 쓴다.

**미결**: 재무제표 섹션 코드 전환 연도(2014~2015) · 첨부 `TOT_ASSETS` 단위 · XBRL 그룹 존재율 연도별 · `IS2/IS3` 정확한 의미(손익계산서 vs 포괄손익계산서 분리 추정) · `[첨부정정]` 의 정정대상이 감사보고서일 때 `kind` 매칭(현재는 report_nm 의 보고서 종류로) · 2010~2012 K-GAAP `SA` 표·`scope='U'` 의 equity 해석 · 킥오프 "정정 ZIP 18,013/21,138" 과 §1.7 전수(17,603/20,579) 의 술어 대응.
