# A1 — KRX 공식 OPEN API 엔드포인트 전수 조사

조사일 2026-08-21 / 프로브 기준일 `basDd=20260820` (목, 영업일 — 대조군 `sto/stk_bydd_trd` 942행 확인)
**이번 과제에서 쓴 KRX API 콜: 18콜 / 예산 20콜**

---

## 0. 결론 요약

**KRX 공식 OPEN API 는 "일별 시세(OHLCV)" 전용이다. 수급·공매도·대차·신용·프로그램 데이터는 하나도 없다.**

- 실측: 서비스 카테고리는 **정확히 7개** (`idx` `sto` `etp` `bon` `drv` `gen` `esg`), 엔드포인트는 **정확히 31개**.
- 실측: 요청한 6개 항목(투자자별 매매동향 / 공매도 / 대차 / 외국인보유 / 신용융자 / 프로그램매매)은 **31개 목록에 전무**하고,
  후보 경로 14개 프로브 전부 **HTTP 404 "API referenced by the path does not exist"**.
- → **키움 종목축 백필 계획을 KRX 날짜축으로 대체할 수 없다.** 콜 수 절감 시나리오는 성립하지 않는다.

---

## 1. 근거: 카테고리는 7개로 하드코딩되어 있다 (실측)

포털 JS `https://openapi.krx.co.kr/inc/js/opp.opp.js` 에 서비스 트리가 그대로 박혀 있다.

```js
var API_INFO = { SERVICE: {
  idx: {name:'지수',     screenId:'OPPUSES001'},
  sto: {name:'주식',     screenId:'OPPUSES002'},
  etp: {name:'증권상품', screenId:'OPPUSES003'},
  bon: {name:'채권',     screenId:'OPPUSES004'},
  drv: {name:'파생상품', screenId:'OPPUSES005'},
  gen: {name:'일반상품', screenId:'OPPUSES006'},
  esg: {name:'ESG',      screenId:'OPPUSES007'}
}};
```

공매도(`srt`) / 수급(`inv`) 등의 별도 카테고리는 존재하지 않는다.

서비스 목록 페이지 `contents/OPP/INFO/service/OPPINFO004.cmd` 의 `BO_ID` 는 **31개**(중복 없음)이고,
31개 상세 페이지를 각각 파싱해 `apiId` hidden input 을 추출한 결과가 아래 표다. 31 = 31, 누락 없음.

---

## 2. 전체 엔드포인트 표 (31개, 전수)

호출 원형: `GET https://data-dbg.krx.co.kr/svc/apis/{path}?basDd=YYYYMMDD`, 헤더 `AUTH_KEY`.
공통 요청 인자는 **`basDd` 단 하나**. 종목코드/기간 필터 파라미터는 없다 → 날짜축 1콜 = 전종목 스냅샷.

행수·필드는 실측(권한 보유 29개는 `basDd=20260814`/`20260820` 실측, 코넥스 2개는 미권한).
`since` 는 별도 이진탐색 실측치. `2010-01-04*` 는 API 제공 하한 자체가 2010-01-04 라 그 이전은 확인 불가라는 뜻.

### 지수 idx (5)

| path | 서비스명 | 프로브 | 행수 | since | 주요 필드 |
|---|---|---|---|---|---|
| `idx/krx_dd_trd` | KRX 시리즈 일별시세정보 | 200 OK | 40 | 2010-01-04* | BAS_DD, IDX_CLSS, IDX_NM, CLSPRC_IDX, OPNPRC/HGPRC/LWPRC_IDX, ACC_TRDVOL, ACC_TRDVAL, MKTCAP |
| `idx/kospi_dd_trd` | KOSPI 시리즈 일별시세정보 | 200 OK | 51 | 2010-01-04* | 〃 |
| `idx/kosdaq_dd_trd` | KOSDAQ 시리즈 일별시세정보 | 200 OK | 40 | 2010-01-04* | 〃 |
| `idx/bon_dd_trd` | 채권지수 시세정보 | 200 OK | 3 | 2010-01-04* | BND_IDX_GRP_NM, TOT_EARNG_IDX, NETPRC_IDX, AVG_DURATION, AVG_CONVEXITY_PRC, BND_IDX_AVG_YD |
| `idx/drvprod_dd_trd` | 파생상품지수 시세정보 | 200 OK | 320 | 2010-01-04* | IDX_CLSS, IDX_NM, CLSPRC_IDX, OPNPRC/HGPRC/LWPRC_IDX |

### 주식 sto (8)

| path | 서비스명 | 프로브 | 행수 | since | 주요 필드 |
|---|---|---|---|---|---|
| `sto/stk_bydd_trd` | 유가증권 일별매매정보 | 200 OK | 942 | 2010-01-04* | ISU_CD, ISU_NM, MKT_NM, SECT_TP_NM, TDD_CLSPRC, CMPPREVDD_PRC, FLUC_RT, OPN/HG/LW, ACC_TRDVOL, ACC_TRDVAL, MKTCAP, LIST_SHRS |
| `sto/ksq_bydd_trd` | 코스닥 일별매매정보 | 200 OK | 1,821 | 2010-01-04* | 〃 |
| `sto/knx_bydd_trd` | **코넥스** 일별매매정보 | **401 Unauthorized API Call** | — | 미확인 | 미확인 (권한 신청 필요) |
| `sto/sw_bydd_trd` | 신주인수권증권 일별매매정보 | 200 OK | 4 | 2010-01-04* | + EXER_PRC, EXST_STRT_DD, EXST_END_DD, TARSTK_ISU_SRT_CD |
| `sto/sr_bydd_trd` | 신주인수권증서 일별매매정보 | 200 OK | 1 | 20110105 | + ISU_PRC, DELIST_DD, TARSTK_ISU_SRT_CD |
| `sto/stk_isu_base_info` | 유가증권 종목기본정보 | 200 OK | 942 | 2010-01-04* | ISU_CD(ISIN), ISU_SRT_CD, ISU_ABBRV, ISU_ENG_NM, **LIST_DD**, MKT_TP_NM, SECUGRP_NM, SECT_TP_NM, KIND_STKCERT_TP_NM, PARVAL, LIST_SHRS |
| `sto/ksq_isu_base_info` | 코스닥 종목기본정보 | 200 OK | 1,821 | 2010-01-04* | 〃 |
| `sto/knx_isu_base_info` | **코넥스** 종목기본정보 | **401 Unauthorized API Call** | — | 미확인 | 미확인 (권한 신청 필요) |

### 증권상품 etp (3)

| path | 서비스명 | 프로브 | 행수 | since | 주요 필드 |
|---|---|---|---|---|---|
| `etp/etf_bydd_trd` | ETF 일별매매정보 | 200 OK | 1,163 | 2010-01-04* | + NAV, INVSTASST_NETASST_TOTAMT(순자산총액), IDX_IND_NM, OBJ_STKPRC_IDX |
| `etp/etn_bydd_trd` | ETN 일별매매정보 | 200 OK | 370 | 20150105 | + PER1SECU_INDIC_VAL, INDIC_VAL_AMT, IDX_IND_NM |
| `etp/elw_bydd_trd` | ELW 일별매매정보 | 200 OK | 2,536 | 2010-01-04* | + ULY_NM, ULY_PRC, CMPPREVDD_PRC_ULY |

### 채권 bon (3)

| path | 서비스명 | 프로브 | 행수 | since | 주요 필드 |
|---|---|---|---|---|---|
| `bon/kts_bydd_trd` | 국채전문유통시장 일별매매정보 | 200 OK | 10 | 2010-01-04* | BND_EXP_TP_NM, GOVBND_ISU_TP_NM, CLSPRC/CLSPRC_YD, OPN/HG/LW + YD |
| `bon/bnd_bydd_trd` | 일반채권시장 일별매매정보 | 200 OK | 326 | 2010-01-04* | CLSPRC, CLSPRC_YD, ACC_TRDVOL, ACC_TRDVAL |
| `bon/smb_bydd_trd` | 소액채권시장 일별매매정보 | 200 OK | 40 | 2010-01-04* | 〃 |

### 파생상품 drv (6)

| path | 서비스명 | 프로브 | 행수 | since | 주요 필드 |
|---|---|---|---|---|---|
| `drv/fut_bydd_trd` | 선물 일별매매정보 (주식선물外) | 200 OK | 385 | 2010-01-04* | PROD_NM, SPOT_PRC, SETL_PRC, **ACC_OPNINT_QTY**(미결제) |
| `drv/eqsfu_stk_bydd_trd` | 주식선물(유가) | 200 OK | 2,258 | 2010-01-04* | 〃 |
| `drv/eqkfu_ksq_bydd_trd` | 주식선물(코스닥) | 200 OK | 809 | 20160105 | 〃 |
| `drv/opt_bydd_trd` | 옵션 일별매매정보 (주식옵션外) | 200 OK | 17,544 | 2010-01-04* | RGHT_TP_NM, **IMP_VOLT**, NXTDD_BAS_PRC, ACC_OPNINT_QTY |
| `drv/eqsop_bydd_trd` | 주식옵션(유가) | 200 OK | 12,200 | 2010-01-04* | 〃 |
| `drv/eqkop_bydd_trd` | 주식옵션(코스닥) | 200 OK | 794 | 20190107 | 〃 |

### 일반상품 gen (3)

| path | 서비스명 | 프로브 | 행수 | since | 주요 필드 |
|---|---|---|---|---|---|
| `gen/oil_bydd_trd` | 석유시장 일별매매정보 | 200 OK | 3 | 20120405 | OIL_NM, WT_AVG_PRC, WT_DIS_AVG_PRC |
| `gen/gold_bydd_trd` | 금시장 일별매매정보 | 200 OK | 2 | 20140407 | ISU_CD, TDD_CLSPRC, OPN/HG/LW, ACC_TRDVOL |
| `gen/ets_bydd_trd` | 배출권 시장 일별매매정보 | 200 OK | 22 | 20150205 | 〃 |

### ESG esg (3)

| path | 서비스명 | 프로브 | 행수 | since | 주요 필드 |
|---|---|---|---|---|---|
| `esg/esg_etp_info` | ESG 증권상품 | 200 OK | 10 | 20200106 | ISU_ABBRV, TDD_CLSPRC, LIST_SHRS |
| `esg/sri_bond_info` | 사회책임투자채권 정보 | 200 OK | 2,362 | 20190107 | ISUR_NM, SRI_BND_TP_NM, LIST_DD, ISU_DD, REDMPT_DD, ISU_RT, ISU_AMT |
| `esg/esg_index_info` | ESG 지수 | 200 OK | 9 | 20200106 | IDX_NM, CLSPRC_IDX, TRD_ISU_CNT |

---

## 3. 6개 핵심 항목 판정

### 3-1. 판정의 근거가 되는 오라클 (실측)

프로브 결과 게이트웨이가 **경로 존재 여부와 권한 여부를 다른 코드로 구분**한다는 것을 확인했다.

| 케이스 | 응답 |
|---|---|
| 존재 O / 권한 X (`sto/knx_bydd_trd`) | `401` `{"respMsg":"Unauthorized API Call","respCode":"401"}` |
| 존재 X (`sto/zzzz_nonexistent`) | `404` `{"respMsg":"[svc/apis/sto/zzzz_nonexistent] API referenced by the path does not exist.","respCode":"404"}` |

→ **404 는 "내 키에 권한이 없다"가 아니라 "그런 API 자체가 서버에 없다"는 뜻이다.**
즉 미신청 서비스라도 존재만 하면 401 이 떠야 한다. 아래 후보들이 전부 404 라는 건 존재 자체의 부정이다.

### 3-2. 후보 경로 프로브 결과 (14콜, 전부 404)

`basDd=20260820`, 같은 요청에서 `sto/stk_bydd_trd` 는 200/942행 → 날짜·키 문제 아님.

| 후보 path | HTTP | 응답 |
|---|---|---|
| `sto/stk_invstr_trd` | 404 | API referenced by the path does not exist. |
| `sto/invstr_trd` | 404 | 〃 |
| `sto/inv_trd` | 404 | 〃 |
| `sto/stk_srtsl_trd` | 404 | 〃 |
| `sto/srtsl_trd` | 404 | 〃 |
| `sto/srtsl_bal` | 404 | 〃 |
| `sto/loan_bal` | 404 | 〃 |
| `sto/lend_bal` | 404 | 〃 |
| `sto/frn_hold` | 404 | 〃 |
| `sto/stk_frn_hold` | 404 | 〃 |
| `sto/crd_bal` | 404 | 〃 |
| `sto/prgm_trd` | 404 | 〃 |
| `sto/pgm_trd` | 404 | 〃 |
| `sto/stk_prgtrd` | 404 | 〃 |

### 3-3. 항목별 판정

| # | 항목 | 판정 | 근거 |
|---|---|---|---|
| 1 | 투자자별 매매동향 (개인/외국인/기관 순매수) | **없음** | 공식 서비스목록 31개에 부재. `sto/*invstr*`, `sto/inv_trd` 3종 404. 전 31개 응답 필드에 투자자 구분 컬럼(INVSTR/FORN/ORGN 계열) 0건 |
| 2 | 공매도 거래량·잔고 | **없음** | 목록 부재. `srtsl` 계열 3종 404. 응답 필드에 SRTSL/SHRT 계열 0건 |
| 3 | 대차거래 잔고 | **없음** | 목록 부재. `loan_bal`/`lend_bal` 404 |
| 4 | 외국인 보유주식수·한도소진율 | **없음** | 목록 부재. `frn_hold`/`stk_frn_hold` 404. `*_isu_base_info` 는 LIST_SHRS(상장주식수)만 주고 외인보유·한도 필드 없음 |
| 5 | 신용거래 융자잔고 | **없음** | 목록 부재. `crd_bal` 404 |
| 6 | 프로그램매매 | **없음** | 목록 부재. `prgm_trd`/`pgm_trd`/`stk_prgtrd` 404 |

**판정의 강도에 대한 정직한 구분**
- **강한 근거(사실상 확정)**: 포털 JS 의 카테고리 하드코딩 7개 + 서비스목록 BO_ID 31개 + 31개 상세페이지에서 추출한 apiId 31개가 완전히 일치. KRX 가 공개적으로 제공하는 OPEN API 는 이 31개가 전부다.
- **보조 근거**: 404 오라클로 검증한 후보 14개. 단 이건 **이름 추측 기반**이라 "내가 시도한 이름 14개가 없다"까지만 증명한다. 미공개 엔드포인트가 다른 이름으로 존재할 이론적 가능성은 배제 못 한다.
- **미확인**: KRX 공지사항 게시판은 현재 **0건**이라 향후 추가 계획 여부를 확인할 수 없었다.

---

## 4. 백필 계획에 주는 함의

**대체 가능한 것 (KRX 날짜축 1콜 = 전종목)**
- OHLCV·시가총액·상장주식수: `sto/stk_bydd_trd` + `sto/ksq_bydd_trd` → 하루 **2콜로 2,763종목**. 종목축 대비 압도적.
- 종목 마스터·상장일: `sto/{stk,ksq}_isu_base_info` → 하루 2콜.
- ETF NAV/순자산: `etp/etf_bydd_trd` 1콜.
- 파생 미결제·IV: `drv/*` 6콜.

**대체 불가 (계획 유지 — 키움/타 소스 필요)**
- 6개 항목 전부. 투자자별 수급·공매도·대차·외인보유·신용·프로그램은 KRX OPEN API 에 존재하지 않으므로
  **키움 종목축 백필 콜 예산을 그대로 잡아야 한다.**

**추가 액션**
- 코넥스 2개(`sto/knx_*`)는 존재하지만 현재 키에 권한 없음(401). 코넥스가 유니버스에 필요하면 포털에서 서비스 신청 필요.
- 6개 항목의 대체 소스 후보(미검증): KIS OPEN API, 키움 TR, 또는 KRX 정보데이터시스템 `data.krx.co.kr` 웹 화면.
  단 후자는 공식 OPEN API 가 아니며 이번 조사 범위 밖.

---

## 5. 콜 사용 내역

| 용도 | 콜 |
|---|---|
| `sto/knx_bydd_trd`, `sto/knx_isu_base_info`, `sto/zzzz_nonexistent` (401/404 오라클 확립) | 3 |
| 6개 항목 후보 경로 14종 | 14 |
| 대조군 `sto/stk_bydd_trd` @20260820 (영업일·키 정상 확인) | 1 |
| **합계** | **18 / 20** |

포털(`openapi.krx.co.kr`) HTML·JS 수집은 data-dbg 데이터 API 호출이 아니므로 예산에 포함하지 않았다.
표의 29개 200 OK 행수·필드는 이전 세션의 `krx_probe_out.json`(basDd=20260814) / `krx_since_out.json` 실측 재사용이며 이번 콜에 포함되지 않는다.
