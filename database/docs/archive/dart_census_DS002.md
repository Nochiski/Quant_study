# DART 전수조사 — DS002 정기보고서 주요정보

조사일 2026-08-24 · 실사용 DART 콜 **25콜** (예산 25콜, 초과 없음)
프로브 조건: 삼성전자 `00126380` / 현대자동차 `00164742` / 두산에너빌리티 `00159616`, `bsns_year=2024`, `reprt_code=11011`(사업보고서)

## 0. 목록 확보 — 카테고리 전체는 30개

`https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS002` 에서 apiId 30개를 전부 확보했고,
각 `guide/detail.do?apiGrpCd=DS002&apiId=<id>` 에서 **실제 요청 URL 을 문서로 확인**했다.
후보 이름 추측 프로브는 쓰지 않았다 — 30개 모두 공식 문서에서 경로를 읽었다.

| # | apiId | 한글명 | 엔드포인트 | 상태 |
|---|---|---|---|---|
| 1 | 2020002 | 주식의 총수 현황 | `stockTotqySttus` | 기확인 |
| 2 | 2019005 | 배당에 관한 사항 | `alotMatter` | 기확인 |
| 3 | 2019004 | 증자(감자) 현황 | `irdsSttus` | 기확인 |
| 4 | 2019006 | 자기주식 취득 및 처분 현황 | `tesstkAcqsDspsSttus` | **실측 000** |
| 5 | 2020003 | 채무증권 발행실적 | `detScritsIsuAcmslt` | **실측 000** |
| 6 | 2020004 | 기업어음증권 미상환 잔액 | `entrprsBilScritsNrdmpBlce` | **실측 000** |
| 7 | 2020005 | 단기사채 미상환 잔액 | `srtpdPsndbtNrdmpBlce` | **실측 000** |
| 8 | 2020006 | 회사채 미상환 잔액 | `cprndNrdmpBlce` | **실측 000** |
| 9 | 2020007 | 신종자본증권 미상환 잔액 | `newCaplScritsNrdmpBlce` | **실측 000** |
| 10 | 2020008 | 조건부 자본증권 미상환 잔액 | `cndlCaplScritsNrdmpBlce` | **실측 000** |
| 11 | 2020016 | 공모자금의 사용내역 | `pssrpCptalUseDtls` | **실측 000** |
| 12 | 2020017 | 사모자금의 사용내역 | `prvsrpCptalUseDtls` | **실측 000** |
| 13 | 2020009 | 회계감사인의 명칭 및 감사의견 | `accnutAdtorNmNdAdtOpinion` | **실측 000** |
| 14 | 2020010 | 감사용역체결현황 | `adtServcCnclsSttus` | **실측 000** |
| 15 | 2020011 | 비감사용역 계약체결 현황 | `accnutAdtorNonAdtServcCnclsSttus` | **실측 000** |
| 16 | 2020012 | 독립(사외)이사 및 그 변동현황 | `outcmpnyDrctrNdChangeSttus` | **실측 000** |
| 17 | 2019007 | 최대주주 현황 | `hyslrSttus` | **실측 000** |
| 18 | 2019008 | 최대주주 변동현황 | `hyslrChgSttus` | **실측 000** |
| 19 | 2019009 | 소액주주 현황 | `mrhlSttus` | **실측 000** |
| 20 | 2019010 | 임원 현황 | `exctvSttus` | **실측 000** |
| 21 | 2019011 | 직원 현황 | `empSttus` | **실측 000** |
| 22 | 2020013 | 미등기임원 보수현황 | `unrstExctvMendngSttus` | **실측 000** |
| 23 | 2020014 | 이사·감사 전체 보수(주총 승인금액) | `drctrAdtAllMendngSttusGmtsckConfmAmount` | **실측 000** |
| 24 | 2019013 | 이사·감사 전체 보수(지급금액-전체) | `hmvAuditAllSttus` | **실측 000** |
| 25 | 2020015 | 이사·감사 전체 보수(지급금액-유형별) | `drctrAdtAllMendngSttusMendngPymntamtTyCl` | **실측 000** |
| 26 | 2019012 | 개인별 보수(5억 이상) | `hmvAuditIndvdlBySttus` | **미프로브 (예산)** |
| 27 | 2026001 | 개인별 보수(5억 이상) Ver 2.0 | `hmvAuditIndvdlBySttusV2` | **실측 013** |
| 28 | 2019014 | 개인별 보수지급(5억 이상 상위5인) | `indvdlByPay` | **미프로브 (예산)** |
| 29 | 2026002 | 개인별 보수지급(상위5인) Ver 2.0 | `indvdlByPayV2` | **실측 013** |
| 30 | 2019015 | 타법인 출자현황 | `otrCprInvstmntSttus` | **실측 000** |

**예산 한계 고백**: 미확인 3개 = 기확인 3개를 뺀 27개 중 25개만 프로브했다. `hmvAuditIndvdlBySttus`(2019012)·`indvdlByPay`(2019014) 는 **경로만 문서로 확인했고 호출하지 않았다**. 존재 여부·응답 필드·행수 모두 미확인이다. 추가 2콜이면 해소된다.

**모든 30개 엔드포인트의 요청 인자는 동일하다**: `crtfc_key`, `corp_code`, `bsns_year`, `reprt_code`.
`list.json` 같은 기간조회·페이징 파라미터는 **하나도 없다**. `page_no`/`page_count` 없음 → 응답은 항상 전량이다(1,588행도 한 번에 왔다).

---

## 1. 실측 엔드포인트 상세

`stlm_dt`(결산일)·`rcept_no`·`corp_code`·`corp_cls`·`corp_name` 은 전 엔드포인트 공통이라 아래 필드 목록에서 생략했다.
PK 는 **응답 원본에 대해 실제 중복검사를 돌려서** 판정했다.

### 1-1. 지분·주주 (우선순위 최상)

#### `hyslrSttus` — 최대주주 현황
- 삼성전자 FY2024 **26행**
- 필드: `stock_knd`, `nm`, `relate`, `bsis_posesn_stock_co`, `bsis_posesn_stock_qota_rt`, `trmend_posesn_stock_co`, `trmend_posesn_stock_qota_rt`, `rm`
- **자연 PK = (stock_knd, nm)** — 실측 26/26 유일, 완전중복 0. `nm` 단독은 19 distinct/26행이라 **불충분**(보통주·우선주 양쪽에 같은 이름이 등장).
- 소급: 연도별 스냅샷. 기초(`bsis_`)/기말(`trmend_`) 보유수가 같이 와서 **1콜로 해당연도 지분 변동 delta 가 나온다**.
- 쓸모: **최대주주 지분율 팩터, 지배구조, 오너 지분 변동 이벤트.** `majorstock.json`(5% 대량보유)와 상보 — 저쪽은 공시일 기준 이벤트, 이쪽은 결산일 기준 스냅샷 + 특수관계인 전원 나열.

#### `mrhlSttus` — 소액주주 현황
- 삼성전자 FY2024 **1행** (`se='소액주주'` 한 값만)
- 필드: `se`, `shrholdr_co`, `shrholdr_tot_co`, `shrholdr_rate`, `hold_stock_co`, `stock_tot_co`, `hold_stock_rate`
- **자연 PK = (se)**. 실측 1행이라 중복 여지 없음.
- 소급: 연도별 스냅샷.
- 쓸모: **free float 프록시.** `hold_stock_rate`=68.23%(삼성전자 FY2024)가 소액주주 보유비율. `stockTotqySttus` 의 `distb_stock_co`(자기주식 차감 유통주식)보다 **실질 유통물량에 가깝다** — 최대주주·특수관계인 락업 물량이 빠지기 때문. `shrholdr_co`(주주 수 516만명)는 개인 참여도 지표.

#### `hyslrChgSttus` — 최대주주 변동현황
- 삼성전자 FY2024 **1행, 전 필드 `-`** (= 변동 없음. status 는 000)
- 필드: `change_on`, `mxmm_shrholdr_nm`, `posesn_stock_co`, `qota_rt`, `change_cause`, `rm`
- **자연 PK = (change_on, mxmm_shrholdr_nm)** — 다만 실측 표본이 "변동 없음" 1행뿐이라 **다건일 때의 유일성은 미확인**.
- 소급: **미확인.** 보고서 1건에 몇 년치 변동이력이 담기는지 확인하지 못했다. `irdsSttus` 가 보고서마다 3~5년 불규칙 범위였던 전례가 있으므로 **매년 수집을 기본값으로 잡아야 한다**.
- 쓸모: M&A / 경영권 변동 이벤트.

### 1-2. 자기주식 (우선순위 최상)

#### `tesstkAcqsDspsSttus` — 자기주식 취득 및 처분 현황
- 삼성전자 FY2024 **18행**
- 필드: `stock_knd`, `acqs_mth1`, `acqs_mth2`, `acqs_mth3`, `bsis_qy`, `change_qy_acqs`, `change_qy_dsps`, `change_qy_incnr`, `trmend_qy`, `rm`
- **자연 PK = (stock_knd, acqs_mth1, acqs_mth2, acqs_mth3)** — 4단 계층. 실측 완전중복 0.
  - `stock_knd` ∈ {보통주, 우선주}
  - `acqs_mth1` ∈ {배당가능이익범위 이내 취득, 기타취득, **총계**}
  - `acqs_mth2` ∈ {직접취득, 신탁계약에 의한취득, 기타취득, 총계}
  - `acqs_mth3` ∈ {장내직접취득, 장외직접취득, 공개매수, **소계**, 수탁자보유물량, 현물보유량, 기타취득, 총계}
- **함정: `소계`·`총계` 집계행이 데이터행과 같은 리스트에 섞여 있다.** 그대로 SUM 하면 3중 계상된다. 삼성전자 FY2024 보통주 총계 29,700,000 = 직접취득 소계 29,700,000 = 장내직접취득 29,700,000 이 모두 행으로 존재.
- 소급: 연도별 스냅샷. `bsis_qy`(기초)/`trmend_qy`(기말) + `change_qy_acqs`/`change_qy_dsps`/`change_qy_incnr`(소각) → **해당연도 자사주 flow 전량이 1콜로 나온다.**
- 쓸모: **자사주 매입/소각 팩터 (shareholder yield).** `stockTotqySttus.tesstk_co` 는 기말 잔고(stock)뿐이라 매입 vs 소각을 구분 못 한다. 이 엔드포인트만이 **소각(`change_qy_incnr`)을 분리**해준다 — 소각은 영구 감자, 매입은 되팔 수 있으므로 팩터 의미가 전혀 다르다.

### 1-3. 감사·회계 (우선순위 상 — 상폐 리스크)

#### `accnutAdtorNmNdAdtOpinion` — 회계감사인의 명칭 및 감사의견
- 삼성전자 FY2024 **6행 = 3개 사업연도 × 2 (완전중복)**
- 필드: `bsns_year`, `adtor`, `adt_opinion`, `adt_reprt_spcmnt_matter`, `emphs_matter`, `core_adt_matter`
- **자연 PK = (bsns_year)** — 하지만 **실측 완전중복 행 3건**. 제56기/제55기/제54기가 각각 2번씩, `rcept_no` 포함 모든 필드가 바이트 동일하게 반환된다. 구분 필드가 없으므로 **DISTINCT 로 밀어야 하고, 중복을 자연스러운 별도/연결 구분으로 오해하면 안 된다.**
- **소급 특성 (중요): 1콜에 당기/전기/전전기 3개 연도가 온다.** `bsns_year` 는 `"제56기\n(당기)"` 형태의 **문자열**(개행 포함)이지 연도가 아니다 — 기수 숫자를 파싱하거나 `stlm_dt` 로 매핑해야 한다.
- 쓸모: **비적정의견(한정/부적정/의견거절) = 상장폐지 사유.** 백테스트의 생존편향 제거와 이벤트 팩터 양쪽에 직결. `core_adt_matter`(핵심감사사항)는 회계 리스크 텍스트 피처.

#### `adtServcCnclsSttus` — 감사용역체결현황
- 삼성전자 FY2024 **3행 = 3개 사업연도**
- 필드: `bsns_year`, `adtor`, `cn`, `mendng`, `tot_reqre_time`, `adt_cntrct_dtls_mendng`, `adt_cntrct_dtls_time`, `real_exc_dtls_mendng`, `real_exc_dtls_time`
- **자연 PK = (bsns_year)**, 실측 3/3 유일.
- **데이터 품질 결함(실측)**: 라벨이 `제56기(당기)` / `제55기(당기)` / `제54기(전기)` 로 왔다. **제55기가 "(당기)"로 잘못 표기**되어 있다 — 원본 보고서 오타가 그대로 노출된다. `(당기)/(전기)` 문자열로 연도를 결정하면 **두 행이 같은 연도로 붕괴**한다. 반드시 "제NN기" 기수 숫자를 파싱하라.
- 소급: 1콜에 3개 연도.
- 쓸모: 감사보수/감사시간 → 회계 감사강도. 니치.

#### `accnutAdtorNonAdtServcCnclsSttus` — 비감사용역 계약체결 현황
- 삼성전자 FY2024 **9행** (3개 사업연도에 걸친 계약건)
- 필드: `bsns_year`, `cntrct_cncls_de`, `servc_cn`, `servc_exc_pd`, `servc_mendng`, `rm`
- **자연 PK 없음.** (bsns_year, cntrct_cncls_de, servc_cn, servc_exc_pd) 4개를 다 써도 유일하지 않다 — 제54기의 전 필드 `-` 행이 2건 완전중복. **surrogate key 필요.**
- 소급: 1콜에 3개 연도.
- 쓸모: 감사인 독립성 훼손 지표(비감사보수/감사보수). 니치.

#### `outcmpnyDrctrNdChangeSttus` — 독립(사외)이사 및 그 변동현황
- 삼성전자 FY2024 **1행**
- 필드: `drctr_co`(이사 수 9), `otcmp_drctr_co`(사외이사 수 6), `apnt`(선임 2), `rlsofc`(해임 -), `mdstrm_resig`(중도퇴임 1)
- **자연 PK = 없음 (corp_code+bsns_year 당 1행 고정)**
- 소급: 연도별 스냅샷.
- 쓸모: 이사회 독립성 비율(6/9=67%). 거버넌스 팩터 보조.

### 1-4. 인력 (우선순위 상)

#### `empSttus` — 직원 현황
- 삼성전자 FY2024 **6행**
- 필드: `sexdstn`, `fo_bbm`, `reform_bfe_emp_co_rgllbr`, `reform_bfe_emp_co_cnttk`, `reform_bfe_emp_co_etc`, `rgllbr_co`, `rgllbr_abacpt_labrr_co`, `cnttk_co`, `cnttk_abacpt_labrr_co`, `sm`, `avrg_cnwk_sdytrn`, `fyer_salary_totamt`, `jan_salary_am`, `rm`
- **자연 PK = (sexdstn, fo_bbm)**. 실측 6/6 유일.
- **함정: `fo_bbm`(사업부문)에 `성별합계` 라는 집계행이 섞여 있다.** 삼성전자 FY2024 = {DX×남/여, DS×남/여, 성별합계×남/여}. 그냥 `sm` 을 SUM 하면 2배가 된다. **그리고 `fyer_salary_totamt`(연간급여총액)·`jan_salary_am`(1인평균급여)은 `성별합계` 행에만 채워지고 사업부문 행은 전부 `-`** 다.
- 소급: 연도별 스냅샷.
- 쓸모: **인당 매출·인당 영업이익 팩터, 평균급여, 평균근속연수(`avrg_cnwk_sdytrn`).** 직원 수 급감은 구조조정 시그널. 사업부문별 분해가 가능한 점이 재무제표 대비 강점.

#### `exctvSttus` — 임원 현황
- 삼성전자 FY2024 **9행**
- 필드: `nm`, `sexdstn`, `birth_ym`, `ofcps`, `rgist_exctv_at`, `fte_at`, `chrg_job`, `main_career`, `mxmm_shrholdr_relate`, `hffc_pd`, `tenure_end_on`
- **자연 PK = (nm)** — 실측 9/9 유일. 동명이인 위험이 있으므로 실무 PK 는 (nm, birth_ym) 권장.
- **실측 주의**: 삼성전자는 `rgist_exctv_at` ∈ {사내이사 3, 사외이사 6} 으로 **등기임원만 9명** 반환됐다. 미등기임원 행은 0건. 모든 기업에서 등기임원만 오는지는 **미확인**(삼성전자 1건 표본).
- 소급: 연도별 스냅샷.
- 쓸모: CEO 교체 이벤트, 임원 평균연령, `tenure_end_on`(임기만료). 팩터 가치는 중간.

### 1-5. 타법인 출자 (우선순위 상)

#### `otrCprInvstmntSttus` — 타법인 출자현황
- 삼성전자 FY2024 **138행**
- 필드: `inv_prm`, `frst_acqs_de`, `invstmnt_purps`, `frst_acqs_amount`, `bsis_blce_qy`, `bsis_blce_qota_rt`, `bsis_blce_acntbk_amount`, `incrs_dcrs_acqs_dsps_qy`, `incrs_dcrs_acqs_dsps_amount`, `incrs_dcrs_evl_lstmn`, `trmend_blce_qy`, `trmend_blce_qota_rt`, `trmend_blce_acntbk_amount`, `recent_bsns_year_fnnr_sttus_tot_assets`, `recent_bsns_year_fnnr_sttus_thstrm_ntpf`
- **자연 PK = (inv_prm)** — 실측 138/138 유일, 완전중복 0.
- **함정: `inv_prm='합계'` 집계행이 마지막에 포함된다.** 게다가 `inv_prm` 값 중에 `'반도체 생태계 일반 \n사모 투자신탁'` 처럼 **개행이 들어간 이름**이 있어서 `'계' in name` 같은 필터는 오탐한다. `inv_prm.strip()=='합계'` 로만 걸러라.
- 소급: 연도별 스냅샷. 기초/기말 잔고가 같이 온다.
- 쓸모: **지주회사·모회사 SOTP/NAV 밸류에이션.** 피출자사 총자산·당기순이익(`recent_bsns_year_fnnr_sttus_*`)까지 붙어 오므로 비상장 자회사 가치평가가 가능하다. `invstmnt_purps` ∈ {경영참여 101, 단순투자 36} 으로 전략적 지분 분리 가능.

### 1-6. 채무증권 (우선순위 중)

#### `detScritsIsuAcmslt` — 채무증권 발행실적
- **현대차 FY2024 1,588행 (단일 콜, 페이징 없음)**
- 필드: `isu_cmpny`, `scrits_knd_nm`, `isu_mth_nm`, `isu_de`, `facvalu_totamt`, `intrt`, `evl_grad_instt`, `mtd`, `repy_at`, `mngt_cmpny`
- **자연 PK 없음.** (isu_cmpny, scrits_knd_nm, isu_de, facvalu_totamt, mtd) 5개를 써도 **완전중복 48행**. 같은 발행사가 같은 날 같은 조건으로 별건 발행한 것이 구분자 없이 나열된다. → **surrogate key 필수.**
- **범위: 해당 사업연도 발행분만** (`isu_de` 실측 1,587행 전부 2024년). 만기 `mtd` 는 2024~2054 로 흩어진다. → **소급이력을 원하면 연도별로 전부 받아야 한다.**
- **`isu_cmpny='합 계'` 집계행 1건 포함** (전 필드 `-`, `facvalu_totamt`만 138조). 발행사가 **연결 종속회사 단위**로 나온다(HCA 1,013건, 현대카드 361, 현대캐피탈 159, 유동화SPC 다수) — 모회사 corp_code 로 조회해도 그룹 캡티브 금융사 발행분이 전부 딸려온다.
- `repy_at` ∈ {상환 1,204, 미상환 383}.
- 쓸모: 조달금리(`intrt`) 시계열 = 신용스프레드 대용. 단 **행이 너무 무겁다** — 금융 자회사 있는 그룹은 종목당 1,500행+. 요약이 목적이면 아래 미상환잔액 5종이 훨씬 싸다.

#### 미상환 잔액 5종 — 구조가 전부 동일
`entrprsBilScritsNrdmpBlce`(기업어음) / `srtpdPsndbtNrdmpBlce`(단기사채) / `cprndNrdmpBlce`(회사채) / `newCaplScritsNrdmpBlce`(신종자본증권) / `cndlCaplScritsNrdmpBlce`(조건부자본증권)

- **전부 현대차 FY2024 정확히 3행**, status 000
- **자연 PK = (remndr_exprtn2)** ∈ {**공모, 사모, 합계**}. `remndr_exprtn1` 은 `'미상환잔액'` 상수라 무의미. 실측 3/3 유일, 완전중복 0.
- **함정: `합계` 행이 데이터행과 같은 리스트에 있다** (공모+사모=합계).
- 공통 필드: `sm`(총계) + 잔존만기 버킷들. **버킷 컬럼명이 엔드포인트마다 다르다**:
  | 엔드포인트 | 만기 버킷 |
  |---|---|
  | `entrprsBilScritsNrdmpBlce` | `de10_below`, `de10_excess_de30_below`, `de30_excess_de90_below`, `de90_excess_de180_below`, `de180_excess_yy1_below`, `yy1_excess_yy2_below`, `yy2_excess_yy3_below`, `yy3_excess` |
  | `srtpdPsndbtNrdmpBlce` | `de10_below` … `de180_excess_yy1_below` + `isu_lmt`(발행한도), `remndr_lmt`(잔여한도) |
  | `cprndNrdmpBlce` | `yy1_below`, `yy1_excess_yy2_below`, `yy2_excess_yy3_below`, `yy3_excess_yy4_below`, `yy4_excess_yy5_below`, `yy5_excess_yy10_below`, `yy10_excess` |
  | `newCaplScritsNrdmpBlce` | `yy1_below`, `yy1_excess_yy5_below`, `yy5_excess_yy10_below`, `yy10_excess_yy15_below`, `yy15_excess_yy20_below`, `yy20_excess_yy30_below`, `yy30_excess` |
  | `cndlCaplScritsNrdmpBlce` | `yy1_below`, `yy1_excess_yy2_below`, `yy2_excess_yy3_below`, `yy3_excess_yy4_below`, `yy4_excess_yy5_below`, `yy5_excess_yy10_below`, `yy10_excess_yy20_below`, `yy20_excess_yy30_below`, `yy30_excess`
- **현대차 FY2024 실측값**: 회사채 합계 115.6조(공모 51.5 + 사모 64.1), CP 합계 5.9조. 단기사채·신종자본·조건부자본은 **status 000 인데 전 버킷이 `-`** (= 해당 없음). 즉 **013 이 아니라 "빈 값 3행"으로 온다** — status 만 보고 존재 판정하면 안 된다.
- 소급: 연도별 스냅샷. 결산일 기준 잔액.
- 쓸모: **만기 월wall / 리파이낸싱 리스크 팩터.** `yy1_below`(1년 내 만기) / 총부채 비율은 재무제표에서 안 나오는 정보다. 신종자본증권은 회계상 자본이지만 실질 부채 → 조정 레버리지 계산에 필요.

### 1-7. 자금 사용내역 (우선순위 하)

#### `pssrpCptalUseDtls` — 공모자금의 사용내역
- 두산에너빌리티 FY2024 **6행**
- 필드: `se_nm`, `tm`, `pay_de`, `pay_amount`, `on_dclrt_cptal_use_plan`, `real_cptal_use_sttus`, `rs_cptal_use_plan_useprps`, `rs_cptal_use_plan_prcure_amount`, `real_cptal_use_dtls_cn`, `real_cptal_use_dtls_amount`, `dffrnc_occrrnc_resn`
- **자연 PK = (se_nm, tm, pay_de)** — 실측 6/6 유일. `se_nm` 단독은 3 distinct/6행이라 불충분. `se_nm` 값에도 개행 포함(`'외화공모사채\n(녹색채권)'`).
- **소급 특성: 과거 조달분이 함께 온다.** FY2024 보고서에 2022.02 유상증자, 2023.07 녹색채권, 2024 공모사채 78·79회가 섞여 있다. 범위 규칙은 **미확인** — `irdsSttus` 처럼 불규칙할 수 있으므로 매년 수집 가정.
- 쓸모: 유상증자 자금의 실제 사용처, 계획 대비 괴리(`dffrnc_occrrnc_resn`). 니치.

#### `prvsrpCptalUseDtls` — 사모자금의 사용내역
- 두산에너빌리티 FY2024 **5행** (사모사채 제71·72·75·76·77회)
- 필드: `se_nm`, `tm`, `pay_de`, `pay_amount`, `real_cptal_use_sttus`, `real_cptal_use_dtls_cn`, `real_cptal_use_dtls_amount`, `dffrnc_occrrnc_resn`, `cptal_use_plan`, `mtrpt_cptal_use_plan_useprps`, `mtrpt_cptal_use_plan_prcure_amount`
- **자연 PK = (se_nm, tm)** — 실측 5/5 유일 (`tm` = 회차).
- 소급: 위와 동일, 과거 조달분 혼재. 규칙 미확인.
- 쓸모: 니치.

### 1-8. 보수 (우선순위 하)

#### `hmvAuditAllSttus` — 이사·감사 전체 보수(지급금액 - 전체)
- 삼성전자 FY2024 **1행**
- 필드: `nmpr`(9), `jan_avrg_mendng_am`(27.06억), `mendng_totamt`(297.7억), `rm`, `fscl_year`, `stk_bsd_pd_mendng_totamt`, `stk_opt_exrcsbl_qty`, `stk_opt_unexrcsbl_qty`, `stk_opt_rmn_blce`, `othr_stk_bsd_cmpn_unpyd_qty`, `othr_stk_bsd_cmpn_mkt_vl`
- **PK 없음 (corp_code+bsns_year 당 1행)**. `fscl_year` 는 실측 `-` (미사용 필드).
- 쓸모: 경영진 보수/영업이익 비율. **주식보상 필드 6개**가 붙어 있어 희석 리스크 추정 가능(삼성전자는 전부 `-`).

#### `drctrAdtAllMendngSttusMendngPymntamtTyCl` — 유형별 보수지급금액
- 삼성전자 FY2024 **4행**
- 필드: `se`, `nmpr`, `pymnt_totamt`, `psn1_avrg_pymntamt`, `rm`, `fscl_year`, + 위와 동일한 주식보상 6필드
- **자연 PK = (se)** ∈ {등기이사(사외이사, 감사위원회 위원 제외), 사외이사(감사위원회 위원 제외), 감사위원회 위원, 감사}. 실측 4/4 유일. **집계행 없음.**

#### `drctrAdtAllMendngSttusGmtsckConfmAmount` — 주총 승인금액
- 삼성전자 FY2024 **4행**
- 필드: `se`, `nmpr`, `gmtsck_confm_amount`, `rm`, `fscl_year`
- **자연 PK = (se)** ∈ {등기이사, 사외이사, 감사위원회 위원, **계**}. 실측 4/4 유일.
- **함정: `se='계'` 집계행 포함.** 게다가 삼성전자는 **`계` 행에만 금액(430억)이 있고 나머지 3행은 `-`** 다 — 유형별 분해가 안 되는 경우가 흔하다.

#### `unrstExctvMendngSttus` — 미등기임원 보수현황
- 삼성전자 FY2024 **1행** (`se='미등기임원'`, 총액 6,533.9억, 1인평균 6.71억, 인원 1,003명)
- 필드: `se`, `fyer_salary_totamt`, `jan_salary_am`, `nmpr`, `rm`
- **자연 PK = (se)**.
- 쓸모: 미등기임원 1,003명 규모 = 조직 비대화 지표. 니치.

#### `hmvAuditIndvdlBySttusV2` / `indvdlByPayV2` — 개인별 보수 (Ver 2.0)
- **삼성전자 FY2024 둘 다 status 013 "조회된 데이타가 없습니다."**
- 삼성전자는 5억 이상 개인별 보수 공시 대상이 확실히 존재하므로(한종희 등), 013 의 원인은 **"데이터 없음"이 아니라 V2 가 FY2024 를 커버하지 않는 것**일 가능성이 높다. Ver 2.0 은 2026년 신설이므로 **FY2025 이후만 적재**되었을 개연성이 크다 — 다만 이건 **추론이고 미검증**이다.
- **재시도(다른 연도/종목)는 콜 예산 소진으로 하지 못했다.** V1(`hmvAuditIndvdlBySttus`, `indvdlByPay`)도 미프로브다.
- **결론 유보**: 개인별 보수 4종(V1 2개 + V2 2개)은 **전부 미해결**. 후속 4콜 필요.

---

## 2. 우리 계획에 없는데 받아야 할 것 — 우선순위

콜 수 추정 전제: **유니버스 2,600종목(폐지 포함), FY2015~FY2025 11개 연도, 사업보고서(11011)만.**
DS002 는 전부 `corp_code × bsns_year × reprt_code` 단위 = 종목·연도당 1콜. 2,600 × 11 = **28,600콜/엔드포인트**(일 한도 20,000 기준 약 1.5일).

### Tier 1 — 반드시 받아야 함 (팩터·리스크 직결)

| 순위 | 엔드포인트 | 왜 필요한가 | 예상 콜 |
|---|---|---|---|
| 1 | `tesstkAcqsDspsSttus` | **자사주 매입 vs 소각 분리.** `stockTotqySttus.tesstk_co`(잔고)만으로는 shareholder yield 를 계산할 수 없다. `change_qy_incnr`(소각)는 여기밖에 없다 | 28,600 |
| 2 | `hyslrSttus` | 최대주주 지분율 + 기초/기말 delta. `majorstock.json`(5% 이벤트)과 상보 — 이쪽은 결산일 스냅샷 + 특수관계인 전원 | 28,600 |
| 3 | `mrhlSttus` | **free float.** `distb_stock_co` 보다 실질 유통물량에 가깝다. 1행/콜로 가장 싸다 | 28,600 |
| 4 | `accnutAdtorNmNdAdtOpinion` | **비적정의견 = 상폐 사유.** 생존편향 제거의 근거 데이터 | **10,400** (1콜=3개연도 → 3년 간격 4회면 11년 커버. 단 전전기 데이터 신뢰도는 미검증이므로 매년이면 28,600) |
| 5 | `empSttus` | 인당매출/인당이익, 평균급여, 직원수 급감(구조조정) 시그널. 사업부문별 분해 | 28,600 |
| 6 | `otrCprInvstmntSttus` | **지주사·모회사 SOTP.** 피출자사 총자산·순이익까지 딸려와 비상장 자회사 평가 가능 | 28,600 |

**Tier 1 합계 ≈ 153,400 ~ 171,600콜 → 일 20,000 한도로 8~9일.**

### Tier 2 — 받으면 좋음

| 순위 | 엔드포인트 | 왜 | 예상 콜 |
|---|---|---|---|
| 7 | `cprndNrdmpBlce` | 회사채 만기 wall. `yy1_below`/총부채 = 리파이낸싱 리스크 | 28,600 |
| 8 | `entrprsBilScritsNrdmpBlce` | CP 잔액 = 단기 유동성 스트레스 | 28,600 |
| 9 | `hyslrChgSttus` | 경영권 변동 이벤트. 1행/콜로 저렴 | 28,600 |
| 10 | `newCaplScritsNrdmpBlce` | 신종자본증권 = 회계상 자본/실질 부채. 조정 레버리지 | 28,600 |
| 11 | `hmvAuditAllSttus` + `drctrAdt...TyCl` | 경영진 보수/이익 비율, 주식보상 희석 | 57,200 |

### Tier 3 — 선택 / 후순위

- `exctvSttus` (CEO 교체 이벤트용. 등기임원만 오는지 표본 1건이라 재검증 먼저)
- `srtpdPsndbtNrdmpBlce`, `cndlCaplScritsNrdmpBlce` (대부분 금융사·해당없음)
- `adtServcCnclsSttus`, `accnutAdtorNonAdtServcCnclsSttus`, `outcmpnyDrctrNdChangeSttus` (거버넌스 보조)
- `pssrpCptalUseDtls`, `prvsrpCptalUseDtls` (증자·사채 자금용도. `irdsSttus` 보완)

### 불필요 판정

- **`detScritsIsuAcmslt`** — 종목당 1,500행+ (금융 자회사 보유 그룹). 조달금리 시계열이 꼭 필요한 게 아니면 미상환잔액 5종이 같은 정보를 3행으로 준다. **적재 비용 대비 효용 낮음.**
- **개인별 보수 4종** (`hmvAuditIndvdlBySttus`, `indvdlByPay`, `+V2`) — 개인 단위 보수는 종목 팩터로 쓰이지 않는다. 단, **V2 013 원인 규명 4콜은 별도로 해두는 게 좋다** (Ver 2.0 이 V1 을 대체하는 중이면 다른 API 에도 V2 전환이 올 수 있다).
- `unrstExctvMendngSttus`, `drctrAdtAllMendngSttusGmtsckConfmAmount` — 위와 동일.

---

## 3. 수집 파이프라인에 반드시 반영할 함정 (전부 실측)

1. **집계행이 데이터행에 섞여 있다 — 무조건 필터링.**
   | 엔드포인트 | 집계행 식별 |
   |---|---|
   | `tesstkAcqsDspsSttus` | `acqs_mth1='총계'` 또는 `acqs_mth3 in ('소계','총계')` |
   | `empSttus` | `fo_bbm='성별합계'` |
   | `otrCprInvstmntSttus` | `inv_prm.strip()=='합계'` (부분문자열 매칭 금지 — 정상 종목명에 '계' 포함) |
   | 미상환잔액 5종 | `remndr_exprtn2='합계'` |
   | `detScritsIsuAcmslt` | `isu_cmpny.strip()=='합 계'` (공백 포함) |
   | `drctrAdt...GmtsckConfmAmount` | `se='계'` |

2. **`accnutAdtorNmNdAdtOpinion` 은 모든 행이 2번씩 온다.** 구분 필드 없는 완전중복. DISTINCT 필수.

3. **`adtServcCnclsSttus` 의 `(당기)/(전기)` 라벨은 신뢰 불가** — 실측에서 제55기가 `(당기)`로 왔다. "제NN기" 기수로만 연도를 결정하라.

4. **자연 PK 가 없는 2개는 surrogate key 필수**: `detScritsIsuAcmslt`(완전중복 48행), `accnutAdtorNonAdtServcCnclsSttus`(완전중복 1행).

5. **status 000 ≠ 데이터 있음.** 미상환잔액 5종은 해당사항 없어도 3행을 `-`로 채워 000 을 준다. `sm=='-'` 체크 필요.

6. **필드에 개행(`\n`)이 들어온다**: `bsns_year`(`"제56기\n(당기)"`), `se_nm`, `inv_prm`, `cn`, `core_adt_matter`. CSV 적재 시 quoting 주의.

7. **페이징 파라미터가 없다.** 1,588행도 한 번에 온다 — timeout 여유를 두되, 응답 잘림을 페이징으로 해결할 수 없다.

8. **`detScritsIsuAcmslt` 의 `isu_cmpny` 는 연결 종속회사 단위**다. 모회사 corp_code 로 조회해도 캡티브 금융사·유동화SPC 발행분이 전부 포함된다. 모회사 단독 조달로 오해 금지.

---

## 4. 실사용 콜 회계

| 항목 | 콜 |
|---|---|
| status 000 (데이터 수신) | 23 |
| status 013 (무자료) | 2 (`hmvAuditIndvdlBySttusV2`, `indvdlByPayV2`) |
| **합계** | **25 / 예산 25** |

웹 조회(카테고리 1 + 상세 30)는 DART API 콜이 아니므로 예산 외.

## 5. 미확인 항목 정리

| # | 미확인 내용 | 해소 비용 |
|---|---|---|
| 1 | `hmvAuditIndvdlBySttus`(2019012)·`indvdlByPay`(2019014) 응답 구조 — 경로만 문서 확인, **호출 안 함** | 2콜 |
| 2 | V2 2종의 013 원인 (FY2025 커버리지 문제인지, 종목 문제인지) | 2콜 (삼성전자 FY2025) |
| 3 | `reprt_code` 11012/11013/11014(분·반기)에서 DS002 항목들이 데이터를 주는지 | 항목당 1콜 |
| 4 | `hyslrChgSttus` 가 변동 다건일 때의 PK 유일성 및 소급 범위 (삼성전자 표본은 "변동 없음" 1행) | 2~3콜 (경영권 변동 이력 있는 종목) |
| 5 | `pssrpCptalUseDtls`/`prvsrpCptalUseDtls` 의 과거 조달분 포함 범위 규칙 (`irdsSttus` 처럼 불규칙한지) | 3~4콜 (동일 종목 다년도) |
| 6 | `exctvSttus` 가 항상 등기임원만 주는지 (삼성전자 1건 표본) | 2콜 (타 종목) |
| 7 | FY2015 하한 여부 — `fnlttSinglAcntAll` 은 FY2015 하한이었으나 DS002 도 같은지 | 항목당 1~2콜 |
