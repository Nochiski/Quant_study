# KRX 원장 검수 — 2026-09-09 적재분 (검수일 09-10)

- 대상: `~/quant-ledger/data/raw/krx.db` (읽기 전용 접속), 거래일 `bas_dd_req = 20260909`
- 수집: 2026-09-09T23:10:10~21 UTC (= 2026-09-10 08:10 KST), 7 엔드포인트 전부 `ok`
- 결론: **수집기 버그·결측·중복은 발견되지 않았다.** 원장은 KRX 원천을 무손실로 복제하고 있으며
  (종목 시총합 = 지수 시총, 거래대금합 = 지수 거래대금이 전 자릿수 일치), 발견된 이상은
  전부 원천의 기업행위·거래정지에서 비롯된 값이다. 다만 그중 **액면병합 2건은 하류에서
  조정하지 않으면 +345% / +380% 의 허위 수익률**이 되므로 high 로 분류했다.

---

## 1. 검사 결과 표

| # | 항목 | 판정 | 수치·근거 |
|---|------|------|-----------|
| 1 | 행수 vs 직전 5거래일 | 정상 | stk 943 (09-02~09-08 전부 943), ksq 1,822 (1,821~1,822), stk_base 943, ksq_base 1,822, kospi 51 (고정), kosdaq 40 (고정), etf 1,168 (09-07 1,167 → 09-08 1,168 → 09-09 1,168). 급변 없음 |
| 2 | `BAS_DD <> bas_dd_req` | 정상 | 5개 시세·지수 테이블 **전 기간 0건** (09-09 0건, 2026-08-01 이후 0건, 2010년 이후 전체 0건) |
| 3 | 종목 집합 09-08 → 09-09 | 정상 | stk 신규 0 / 소멸 0, ksq 신규 0 / 소멸 0. 상장·폐지·이전 이벤트 없음 |
| 3b | 시세 ↔ base_info 집합 일치 | 정상 (주의) | `ISU_CD` 로 대조 시 **교집합 0** — 두 테이블의 `ISU_CD` 의미가 다름 (시세=단축코드 6자리 `000020`, base=ISIN 12자리 `KR7000020008`). 올바른 키인 `시세.ISU_CD = base.ISU_SRT_CD` 로 대조하면 **양방향 차집합 0** (stk 943/943, ksq 1,822/1,822 완전 일치) |
| 4 | 결측·비정상 값 | 정상 | TDD_CLSPRC / ACC_TRDVOL / ACC_TRDVAL / MKTCAP / LIST_SHRS 의 NULL·`''`·`'-'` = **전부 0건** (2,765행). 종가 0 = 0건. 시총 0 = 0건. 주식수 0 = 0건 |
| 4b | 값 간 모순 | 정상 | 종가0 & 거래량>0 = 0, 거래량0 & 거래대금>0 = 0, 거래량>0 & 거래대금0 = 0. 거래량 0 = 119건 (stk 30 / ksq 89) 이며 거래대금도 동시에 0 → 모순 없음 |
| 5 | OHLC 정합 | 정상 | H<L = 0, C 범위 밖 = 0, O 범위 밖 = 0. OHL 전부 0 = 119건이며 **거래량 0 집합과 정확히 일치** (OHL=0 & 거래량>0 = 0건, OHL>0 & 거래량=0 = 0건) |
| 6 | `CMPPREVDD_PRC` 정합 | 이상 3건 | 대조 가능 2,765행 중 불일치 3건 — 001290 상상인증권, 273060 와이즈버즈, 475250 하나33호스팩. 전부 원천의 기준가 조정 기인 (§2 참조). 직전 5거래일 베이스라인 2~7건/일 이므로 09-09 는 오히려 평균 이하 |
| 6b | `FLUC_RT` 정합 (±0.01%p) | 이상 2건 | 001290 (표기 −10.89 vs 종가체인 −54.46), 273060 (−4.05 vs −20.25). 나머지 2,763행 전부 ±0.01%p 이내 |
| 6c | `\|FLUC_RT\| > 30` | 이상 1건 | 121850 코이즈 **+140.00%** (200 → 480). 가격제한폭 미적용 구간 |
| 7 | `MKTCAP ≈ 종가 × 주식수` | 정상 | 상대오차 > 0.1% = **0건** / 2,765행 (>1% 도 0건) |
| 7b | 시세 `LIST_SHRS` = base `LIST_SHRS` | 정상 | 불일치 stk 0건, ksq 0건 |
| 8 | 지수 `IDX_NM` 집합 | 정상 | kospi 51 / kosdaq 40, 09-08 대비 양방향 차집합 0 |
| 8b | 지수 종가 결측 | 저위험 1건 | `코스피 (외국주포함)`·`코스닥 (외국주포함)` 2건이 CLSPRC/OPNPRC/HGPRC/LWPRC 전부 공란. **전 기간 4,108건 중 비공란 0건** → 원천이 시총·거래대금만 제공하는 지수 |
| 8c | 대표지수 등락 | 정상 | 코스피 7,051.64 (+1.40%), 코스피 200 1,114.61 (+1.32%), 코스닥 830.37 (+2.28%), 코스닥 150 1,426.18 (+2.80%). `\|FLUC_RT\|>5` 0건. 91개 지수 전부 `CMPPREVDD_IDX = 당일종가 − 전일종가` 통과 |
| 9 | ETF 결측·0 | 정상 | 1,168행 중 종가 결측·0 = 0, NAV 결측·0 = 0, 순자산총액 결측·0 = 0. 거래량 0 = 4건 |
| 9b | ETF 괴리율 `\|종가/NAV−1\|>5%` | 이상 1건 | 265690 ACE 러시아MSCI(합성) **+17,530.7%** (종가 8,535 vs NAV 48.41, 거래량 0). 나머지 1,167건 중 최대 괴리 −2.47% |
| 9c | ETF 신규·소멸 | 정상 | 09-08 대비 신규 0, 소멸 0 |
| 9d | ETF OHLC·등락 | 정상 | H<L 0, C·O 범위 밖 0, `\|FLUC_RT\|>30` 0. OHL 전부 0 = 4건 (거래량 0 과 일치) |
| 10 | 중복 `(BAS_DD, ISU_CD)` | 정상 | stk·ksq·etf **전 기간 0건** (지수도 `(BAS_DD, IDX_NM)` 0건) |
| 11 | `collected_at` 균일성 | 정상 | 7 테이블 전부 테이블당 **단일 타임스탬프**, 2026-09-09T23:10:10 ~ 23:10:21 UTC. 다른 시각 혼입 0행. 재수집 흔적 없음 |
| 12 | `ingest_log` 최근 15일 | 정상 | 105건 전부 `status='ok'`, `note` 공란. `n_rows` vs 실제 행수 **불일치 0건**. (전 기간 status 는 `ok` 28,756 + `holiday` 1,715 두 종류뿐, 오류 status 없음) |

### 부수 관측 — 백필 이력

`ingest_log.collected_at` 기준으로 **20260821 ~ 20260908 (14 거래일) 이 2026-09-09T07:08~07:09 UTC 에
일괄 재수집**되었다. 20260820 이전은 2026-08-23T18:41 대. 즉 08-21 부터 09-08 까지 공백이 있었고
09-09 일일 크론 가동과 함께 메워졌다. 결과물 자체는 정합하다 (§1 의 모든 항목이 해당 구간에서도 통과).
09-09 분은 정상 크론 시각(08:10 KST)에 단독 수집되었다.

---

## 2. 발견

### DEFECT-A01 (high): 액면병합 2건 — 원장 종가 체인이 +345% / +380% 허위 수익률을 만든다

- **상황**: 두 종목이 액면병합을 위해 거래정지 상태였다가 09-09 에 병합 후 재개되었다.
  09-01 ~ 09-08 내내 OHLC=0 · 거래량 0 으로 정지 전 종가만 반복 적재되어 있었다.
- **인풋**:
  1. `krx_stk_bydd_trd` / `krx_ksq_bydd_trd` 에서 `bas_dd_req='20260908'` 과 `'20260909'` 의
     `TDD_CLSPRC` 를 종목별로 이어 붙여 일간 수익률을 계산
  2. 대상 = `ISU_CD='001290'` (상상인증권, KOSPI), `ISU_CD='273060'` (와이즈버즈, KOSDAQ)
- **에러 위치**: `krx_stk_bydd_trd.TDD_CLSPRC` / `krx_ksq_bydd_trd.TDD_CLSPRC`
  (키: `bas_dd_req IN ('20260908','20260909')`, `ISU_CD IN ('001290','273060')`).
  원장 값 자체는 원천과 일치하며 오류가 아니다. 결함은 **조정 없이 종가를 이어 붙이는 하류 로직**에 있다.

  | 종목 | 09-08 종가 | 09-09 종가 | 원시 체인 수익률 | 원천 `FLUC_RT` | 액면가 | 상장주식수 |
  |------|-----------:|-----------:|----------------:|---------------:|--------|-----------:|
  | 001290 상상인증권 | 1,010 | 4,500 | **+345.54%** | −10.89% | 1,000 → 5,000 | 108,337,120 → 21,667,424 (÷5) |
  | 273060 와이즈버즈 | 864 | 4,145 | **+379.75%** | −4.05% | 100 → 500 | 49,144,280 → 9,828,856 (÷5) |

  두 건 모두 `CMPPREVDD_PRC` 가 시사하는 기준가 = 전일종가 × 5 로 정확히 떨어진다
  (상상인증권 1,010×5 = 5,050, 와이즈버즈 864×5 = 4,320).
- **위험성**: 조정 없이 쓰면 **부호가 반대인 수익률**을 얻는다 (실제 −10.9% / −4.1% 를 +346% / +380% 로).
  모멘텀·변동성 팩터는 이 한 종목 때문에 해당 날짜의 크로스섹션 랭킹이 통째로 뒤집히고,
  백테스트에서는 병합일에 진입한 포지션이 4.5배 수익으로 계상되어 성과가 허위로 부풀려진다.
  카테고리: **silent corrupt / 미조정 기업행위**. 조용히 통과하는 종류이며 건전성 게이트(행수·status)로는 절대 잡히지 않는다.
- **하류 영향**: `stage` 의 `adj_factor` 가 09-09 에 대해 이 두 종목의 계수 0.2 (= 1/5) 를 반드시 산출해야 한다.
  `equity` 층의 수익률·모멘텀·변동성 전부가 이에 의존한다.
  **검산 규칙**: `base_info` 에서 `PARVAL` 또는 `LIST_SHRS` 가 전일 대비 변한 종목을 매일 뽑아
  `adj_factor` 존재 여부와 대조하면 확정적으로 잡힌다 (09-09 전수 = stk 1건 + ksq 5건, 아래 A02).

### DEFECT-A02 (mid): 09-09 상장주식수 변동 6건 — 그중 4건은 가격 조정 불필요, 시총 시계열만 불연속

- **상황**: 09-08 → 09-09 사이 `*_isu_base_info` 의 `LIST_SHRS` / `PARVAL` 이 바뀐 종목 전수.
- **인풋**: `krx_stk_isu_base_info` / `krx_ksq_isu_base_info` 를 `bas_dd_req='20260908'` 과 `'20260909'` 로
  self-join 후 `LIST_SHRS <> LIST_SHRS OR PARVAL <> PARVAL`
- **에러 위치**: `krx_*_isu_base_info.LIST_SHRS`, `.PARVAL` (`bas_dd_req='20260909'`)

  | 시장 | 코드 | 종목 | 액면가 | 주식수 09-08 → 09-09 | 비율 | 성격 |
  |------|------|------|--------|---------------------|-----:|------|
  | KOSPI | 001290 | 상상인증권 | 1,000 → **5,000** | 108,337,120 → 21,667,424 | 0.200000 | 액면병합 (A01) |
  | KOSDAQ | 273060 | 와이즈버즈 | 100 → **500** | 49,144,280 → 9,828,856 | 0.200000 | 액면병합 (A01) |
  | KOSDAQ | 019550 | SBI인베스트먼트 | 1,000 (불변) | 81,033,287 → 80,562,974 | 0.994196 | 주식수 **감소** (소각·감자 추정) |
  | KOSDAQ | 091440 | 한울소재과학 | 500 (불변) | 34,641,175 → 36,390,319 | 1.050493 | 주식수 증가 (전환·증자 추정) |
  | KOSDAQ | 099190 | 아이센스 | 500 (불변) | 28,757,309 → 29,301,512 | 1.018924 | 주식수 증가 |
  | KOSDAQ | 290690 | 아리바이오홀딩스 | 500 (불변) | 52,959,427 → 53,072,805 | 1.002141 | 주식수 증가 |
- **위험성**: 아래 4건은 액면가 불변이므로 **주가 조정은 불필요**하다. 그러나 시총·주식수를 쓰는 팩터
  (시총 가중, 주식수 증가율 = share issuance 팩터, 자사주 소각 시그널)에서는 09-09 에 계단형 점프가 생긴다.
  019550 은 주식수가 줄었으므로 **소각/감자**일 가능성이 높고, 감자라면 가격 조정이 필요한 경우가 있다.
  `FLUC_RT` 와 종가 체인이 이 4건에서는 일치했으므로 (§1 항목 6 에서 불일치 3건에 포함되지 않음)
  **적어도 09-09 시점에는 가격 기준가 조정이 없었다** — 가격은 안전, 주식수 시계열만 불연속.
- **하류 영향**: `stage` 의 주식수·시총 시계열은 전방 조정 없이 그대로 쓰면 되지만,
  "주식수 증가율" 파생 팩터를 만든다면 이 6건이 09-09 에 아웃라이어로 튄다.

### DEFECT-A03 (mid): ETF 265690 ACE 러시아MSCI(합성) — 거래정지 잔존 종가가 NAV 대비 176배

- **상황**: 러시아 제재로 장기 거래정지된 합성 ETF. 09-09 에도 거래량 0 · OHL 전부 0.
- **인풋**: `krx_etf_bydd_trd` `bas_dd_req='20260909'`, `ISU_CD='265690'` 의 `TDD_CLSPRC` 와 `NAV` 비교
- **에러 위치**: `krx_etf_bydd_trd.TDD_CLSPRC` (= 8,535) vs `.NAV` (= 48.41),
  `.OBJ_STKPRC_IDX` = **공란** (1,168행 중 유일한 공란). 순자산총액 89,554,627원
- **위험성**: 종가 8,535 는 정지 직전에 굳어버린 값이고 실질 가치는 NAV 48.41 이다.
  괴리율 +17,530.7% (2위 종목은 −2.47%). ETF 를 유니버스에 넣거나 벤치마크·헤지 수단으로 쓰면
  **가치가 176배 과대평가된 자산을 보유**하게 된다. 카테고리: **stale price / 거래정지 자산**.
- **하류 영향**: ETF 를 쓰는 층에서 `ACC_TRDVOL = 0` 또는 `|종가/NAV−1| > 5%` 를 유동성 필터로 걸어야 한다.
  나머지 거래량 0 ETF 3건 (0000Y0 HK 26-12 회사채, 321410 KODEX 멀티에셋하이인컴(H), 491700 HK 200) 은
  종가 ≈ NAV 로 정상이며, 단순히 당일 체결이 없었을 뿐이다.

### DEFECT-A04 (low): 121850 코이즈 +140% — 가격제한폭 미적용 구간

- **상황**: KOSDAQ 관리종목(소속부없음), 상장주식수 5,056,999 로 09-01 이후 불변 (기업행위 없음).
- **인풋**: `krx_ksq_bydd_trd` `ISU_CD='121850'`, `bas_dd_req='20260909'`
- **에러 위치**: `krx_ksq_bydd_trd.FLUC_RT` = `140.00` (종가 200 → 480, `CMPPREVDD_PRC` = +280).
  종가 체인과 `CMPPREVDD_PRC` 는 **일치**하므로 데이터 정합성 자체는 통과한다.
- **위험성**: ±30% 제한폭을 넘는다. 최근 이력을 보면 09-03 −34.72%, 09-07 −18.46%, 09-08 −24.53%,
  09-09 +140.00% 로 **양방향 모두 제한폭 밖**이다. 정리매매 등 제한폭 미적용 구간으로 추정된다.
  변동성·모멘텀 팩터에서 극단 아웃라이어이며, 정리매매라면 **곧 상장폐지**되어 백테스트에서
  체결 불가능한 수익을 계상할 수 있다. 카테고리: **아웃라이어 / 생존편향 인접**.
- **하류 영향**: `SECT_TP_NM = '관리종목(소속부없음)'` (09-09 KOSDAQ 130건) 과
  `'투자주의환기종목(소속부없음)'` (41건) 은 유니버스 정책표에서 배제 여부를 명시해야 한다.

### DEFECT-A05 (low): 스팩 종가의 기준가 절상 — `FLUC_RT` 와 종가 체인이 최대 3원 어긋난다

- **상황**: 스팩 종목의 종가가 호가단위 5원의 배수가 아닌 값(항상 `종가 % 5 = 2`)으로 체결되는 경우.
- **인풋**: `krx_ksq_bydd_trd` `ISU_CD='475250'` (하나33호스팩) 09-08 종가 2,087,
  09-09 의 `CMPPREVDD_PRC` = −5 · 종가 2,085 → 원천이 쓴 전일 기준가 = **2,090** (2,087 아님)
- **에러 위치**: `krx_ksq_bydd_trd.CMPPREVDD_PRC` / `.FLUC_RT` (`bas_dd_req='20260909'`, `ISU_CD='475250'`)
- **검증**: 09-08 의 `ACC_TRDVAL` 13,936,152 ÷ `ACC_TRDVOL` 6,684 를 분해하면
  6,678주 @2,085 + 6주 @2,087 = 13,936,152 로 **정확히 떨어진다** → 종가 2,087 은 실제 체결가이며 원장은 정확.
  원천이 다음날 기준가를 호가단위로 절상(2,087 → 2,090)한 것이 원인이다.
- **범위**: 2025년 이후 KOSDAQ 2,000~5,000원 구간 오프틱 종가 350건이 **전부 `% 5 = 2`**
  (2023·2024 년 0건). 2026년 `CMPPREVDD` 불일치 634건 중 214건이 이 유형(gap ≤ 3원)이며
  최근 30일 기준 종목은 전부 스팩이다.
- **위험성**: `FLUC_RT` 로 수익률을 계산하면 종가 체인과 최대 0.24%p 어긋난다. 절대 크기는 작지만
  **두 경로가 항상 일치한다는 가정이 깨지므로** 검증 로직이 이를 오탐으로 잡아낸다.
  카테고리: **경미한 비정합 / 오탐 유발**.
- **하류 영향**: 수익률의 정본은 **종가 체인 + `adj_factor`** 로 고정하고 `FLUC_RT` 는 검산용으로만 쓴다.
  검산 임계는 절대 3원 이하를 허용해야 한다.

### DEFECT-A06 (low): 두 지수의 종가 컬럼이 원천에서 상시 공란

- **상황**: 09-09 지수 91건 중 `CLSPRC_IDX` 가 공란인 것 2건.
- **인풋**: `krx_kospi_dd_trd` / `krx_kosdaq_dd_trd`, `bas_dd_req='20260909'`
- **에러 위치**: `IDX_NM = '코스피 (외국주포함)'`, `'코스닥 (외국주포함)'` 의
  `CLSPRC_IDX` · `OPNPRC_IDX` · `HGPRC_IDX` · `LWPRC_IDX` 가 전부 `''`. `MKTCAP` 과 `ACC_TRDVAL` 은 채워져 있다.
- **범위**: 09-09 만의 문제가 아니다. `코스피 (외국주포함)` 은 **전 기간 4,108건 중 비공란 0건**.
- **위험성**: 낮다. 이 두 항목은 지수가 아니라 시장 전체 시총·거래대금 집계이며, 원천이 지수값을 제공하지 않는다.
  다만 "지수 종가 결측 0" 같은 게이트를 세우면 **매일 2건이 상시 실패**하므로 화이트리스트가 필요하다.
- **유용성**: 오히려 **교차검증용 정본**이다. 실제로 이 값으로 검산한 결과
  `SUM(krx_stk_bydd_trd.MKTCAP)` = `코스피 (외국주포함)`의 `MKTCAP` 이 09-01~09-09 **7일 전부 오차 0.0000%**,
  거래대금도 09-09 기준 코스피 22,625,772,907,400 / 코스닥 7,668,398,047,579 로 **완전 일치**했다.
  이것이 "원장이 원천을 무손실 복제한다"는 이번 검수의 가장 강한 증거다.

### DEFECT-A07 (low): `ISU_CD` 의 의미가 테이블 간 다르다 — 순진한 조인이 조용히 0행을 낸다

- **상황**: 시세 테이블과 종목기본정보 테이블을 종목 코드로 조인.
- **인풋**: `krx_stk_bydd_trd a JOIN krx_stk_isu_base_info b ON b.ISU_CD = a.ISU_CD`
- **에러 위치**: `krx_*_bydd_trd.ISU_CD` = 단축코드 6자리 (`000020`, `0001A0`) /
  `krx_*_isu_base_info.ISU_CD` = ISIN 12자리 (`KR7000020008`, `HK0000057197`).
  단축코드는 `krx_*_isu_base_info.ISU_SRT_CD` 에 따로 있다.
- **위험성**: 두 집합의 **교집합이 정확히 0** 이므로 위 조인은 에러 없이 빈 결과를 낸다.
  INNER JOIN 이면 종목이 통째로 사라지고, LEFT JOIN 이면 기본정보가 전부 NULL 로 채워진다.
  카테고리: **silent data loss**. 올바른 키 `b.ISU_SRT_CD = a.ISU_CD` 로 대조하면
  09-09 기준 stk 943/943, ksq 1,822/1,822 **완전 일치**한다.
- **하류 영향**: `stage` 매핑 계층에서 이 키 규칙을 명시적으로 문서화·강제해야 한다.

### 인접 기간 관측 (09-09 적재분 아님, 참고)

- **09-03 에코프로비엠(247540)·엘앤씨바이오(290650) 기준가 −1,700원**: 두 종목 모두 09-02 종가 대비
  원천의 09-03 기준가가 **정확히 1,700원씩** 낮다 (108,000 → 106,300 / 54,000 → 52,300).
  가격 수준이 2배 차이나는 두 종목에 같은 절대값이 적용된 점이 특이하다. 원장 측 오류는 배제된다 —
  09-02 의 `MKTCAP` 이 `종가 × 주식수` 와 오차 0 이고, 09-02 KOSDAQ 시총 합계가
  `코스닥 (외국주포함)` 지수 시총과 완전히 일치하기 때문이다. **원천의 기준가 조정이며 사유는 미규명.**
  2026년 중 "같은 날 2종목 이상이 동일 gap" 사례는 04-27(+5,760), 05-06(+6,777),
  07-27(−230, 계양전기 보통주·우선주 = 동일 DPS 배당락으로 설명됨), 09-03(−1,700) 총 4일이다.
- **`CMPPREVDD` 불일치의 정상 베이스라인**: 2026년 전체 634건이며
  기준가 절상(≤3원) 214건 / 액면병합 207건 / 액면분할 22건 / 기타(권리락·배당락·거래재개) 191건 으로 분류된다.
  즉 **일 2~7건은 상시 발생하는 정상 현상**이고, 09-09 의 3건은 평균 이하다.
  이 불일치는 곧 "기업행위 발생 신호"이므로 `adj_factor` 산출의 1차 탐지기로 쓸 수 있다.
- **KOSPI 의 `SECT_TP_NM` 은 상시 공란**: 09-09 stk 943행 전부, `stk_isu_base_info` 도 943행 전부 공란.
  2025-09-02·2026-01-02 도 100% 공란이므로 회귀가 아니라 원천 특성(소속부는 KOSDAQ 전용 개념)이다.
  KOSDAQ 은 8개 구분값(중견기업부 511 / 우량기업부 466 / 벤처기업부 339 / 기술성장기업부 255 /
  관리종목 130 / SPAC 65 / 투자주의환기종목 41 / 외국기업 15)이 정상 적재되어 있다.

---

## 3. 확인하지 못한 것과 이유

1. **원천 원본과의 직접 대조 불가**: 읽기 전용·API 호출 금지 제약으로 KRX 에 재조회할 수 없었다.
   따라서 "원장 값 = 원천 값" 은 **내부 정합성**(시총 = 종가×주식수, 거래대금 = 체결 분해,
   종목 시총합 = 지수 시총, 거래대금합 = 지수 거래대금)으로만 입증했다. 전부 통과했으므로
   원장 측 변조·유실 가능성은 사실상 배제되지만, 원천 자체가 틀린 경우는 검출할 수 없다.
2. **DEFECT-A02 의 4건이 감자인지 증자인지 미확정**: 019550·091440·099190·290690 의 주식수 변동 사유는
   KRX 시세·기본정보만으로는 구분되지 않는다. DART 공시(주요사항보고서) 대조가 필요하다.
   특히 주식수가 **감소**한 019550 SBI인베스트먼트는 소각이면 가격 조정 불필요, 감자면 필요할 수 있다.
3. **121850 코이즈의 정리매매 여부 미확정**: `SECT_TP_NM` 은 `관리종목(소속부없음)` 으로만 나오고
   정리매매 플래그는 이 API 응답에 없다. 상장폐지 일정은 DART 또는 KRX 상장폐지 공시 확인이 필요하다.
4. **09-03 −1,700원 기준가 조정의 사유 미규명** (위 참고 항목). 원장 결함은 배제했으나
   원인은 DART 공시 대조 없이는 확정할 수 없다.
5. **`stage` / `equity` 층의 실제 반영 여부 미확인**: 이번 검수 범위는 `data/raw/krx.db` 원장뿐이다.
   DEFECT-A01 의 `adj_factor` 0.2 가 실제로 생성되었는지는 `stage` 층을 별도로 확인해야 한다.
6. **거래정지 119건의 정지 사유 미분류**: OHLC=0 이라는 사실만 확인했고,
   액면병합 대기 / 관리종목 / 불성실공시 등 사유 구분은 원장에 없다.

---

## 4. 사용한 질의 (재현용 핵심 SQL)

접속은 전부 읽기 전용:
`ssh kael-server 'cd ~/quant-ledger && timeout 300 sqlite3 -header -column "file:data/raw/krx.db?mode=ro"' < query.sql`

```sql
-- [1] 행수 추이
SELECT bas_dd_req,
  (SELECT COUNT(*) FROM krx_stk_bydd_trd t WHERE t.bas_dd_req=d.bas_dd_req) stk,
  (SELECT COUNT(*) FROM krx_ksq_bydd_trd t WHERE t.bas_dd_req=d.bas_dd_req) ksq,
  (SELECT COUNT(*) FROM krx_etf_bydd_trd t WHERE t.bas_dd_req=d.bas_dd_req) etf
FROM (SELECT DISTINCT bas_dd_req FROM krx_stk_bydd_trd WHERE bas_dd_req>='20260901') d ORDER BY 1;

-- [3b] 시세 <-> base_info 집합 대조 (올바른 키는 ISU_SRT_CD)
SELECT COUNT(*) FROM (SELECT ISU_CD FROM krx_stk_bydd_trd WHERE bas_dd_req='20260909'
                      EXCEPT SELECT ISU_SRT_CD FROM krx_stk_isu_base_info WHERE bas_dd_req='20260909');

-- [4][5] 결측/0/OHLC 정합 (컬럼이 TEXT 이므로 '' 와 '-' 를 결측에 포함)
WITH u AS (SELECT 'stk' m,* FROM krx_stk_bydd_trd WHERE bas_dd_req='20260909'
           UNION ALL SELECT 'ksq',* FROM krx_ksq_bydd_trd WHERE bas_dd_req='20260909'),
p AS (SELECT m,ISU_CD,ISU_NM,
      CAST(REPLACE(TDD_OPNPRC,',','') AS REAL) o, CAST(REPLACE(TDD_HGPRC,',','') AS REAL) h,
      CAST(REPLACE(TDD_LWPRC,',','') AS REAL) l, CAST(REPLACE(TDD_CLSPRC,',','') AS REAL) c,
      CAST(REPLACE(ACC_TRDVOL,',','') AS REAL) v FROM u)
SELECT 'OHL 전부0' k,COUNT(*) n FROM p WHERE o=0 AND h=0 AND l=0
UNION ALL SELECT 'H<L',COUNT(*) FROM p WHERE NOT(o=0 AND h=0 AND l=0) AND h<l
UNION ALL SELECT 'C 범위밖',COUNT(*) FROM p WHERE NOT(o=0 AND h=0 AND l=0) AND (c<l OR c>h)
UNION ALL SELECT 'OHL=0 인데 거래량>0',COUNT(*) FROM p WHERE o=0 AND h=0 AND l=0 AND v>0;

-- [6] CMPPREVDD_PRC / FLUC_RT 정합 (기업행위 탐지기로도 쓸 수 있다)
WITH t AS (SELECT 'stk' m,ISU_CD,ISU_NM,CAST(REPLACE(TDD_CLSPRC,',','') AS REAL) c,
                  CAST(REPLACE(CMPPREVDD_PRC,',','') AS REAL) d,CAST(REPLACE(FLUC_RT,',','') AS REAL) f
           FROM krx_stk_bydd_trd WHERE bas_dd_req='20260909'
           UNION ALL SELECT 'ksq',ISU_CD,ISU_NM,CAST(REPLACE(TDD_CLSPRC,',','') AS REAL),
                  CAST(REPLACE(CMPPREVDD_PRC,',','') AS REAL),CAST(REPLACE(FLUC_RT,',','') AS REAL)
           FROM krx_ksq_bydd_trd WHERE bas_dd_req='20260909'),
y AS (SELECT 'stk' m,ISU_CD,CAST(REPLACE(TDD_CLSPRC,',','') AS REAL) pc FROM krx_stk_bydd_trd WHERE bas_dd_req='20260908'
      UNION ALL SELECT 'ksq',ISU_CD,CAST(REPLACE(TDD_CLSPRC,',','') AS REAL) FROM krx_ksq_bydd_trd WHERE bas_dd_req='20260908')
SELECT t.m,t.ISU_CD,t.ISU_NM,y.pc prev_cls,t.c cls,t.d cmpprev,ROUND(t.c-t.d,2) implied_prev,t.f
FROM t JOIN y ON y.m=t.m AND y.ISU_CD=t.ISU_CD WHERE ABS(t.d-(t.c-y.pc))>0.0001;

-- [A02] 기업행위 전수 탐지 — 매일 돌릴 값어치가 있다
SELECT a.ISU_SRT_CD,a.ISU_ABBRV,a.PARVAL p0,b.PARVAL p1,a.LIST_SHRS s0,b.LIST_SHRS s1,
       ROUND(CAST(b.LIST_SHRS AS REAL)/CAST(a.LIST_SHRS AS REAL),6) ratio
FROM krx_ksq_isu_base_info a JOIN krx_ksq_isu_base_info b
  ON b.ISU_SRT_CD=a.ISU_SRT_CD AND b.bas_dd_req='20260909'
WHERE a.bas_dd_req='20260908' AND (a.LIST_SHRS<>b.LIST_SHRS OR a.PARVAL<>b.PARVAL);

-- [7] MKTCAP 자기정합
WITH p AS (SELECT ISU_CD,ISU_NM,CAST(REPLACE(TDD_CLSPRC,',','') AS REAL) c,
                  CAST(REPLACE(LIST_SHRS,',','') AS REAL) s,CAST(REPLACE(MKTCAP,',','') AS REAL) k
           FROM krx_stk_bydd_trd WHERE bas_dd_req='20260909')
SELECT COUNT(*) FROM p WHERE k>0 AND ABS(c*s-k)/k>0.001;

-- [A06] 최강 교차검증: 종목 시총합 == 지수 시총, 거래대금합 == 지수 거래대금
SELECT (SELECT SUM(CAST(REPLACE(MKTCAP,',','') AS REAL)) FROM krx_stk_bydd_trd WHERE bas_dd_req='20260909') stk_sum,
       (SELECT CAST(REPLACE(MKTCAP,',','') AS REAL) FROM krx_kospi_dd_trd
        WHERE bas_dd_req='20260909' AND IDX_NM='코스피 (외국주포함)') idx_cap;

-- [9] ETF 괴리율
WITH p AS (SELECT ISU_CD,ISU_NM,CAST(REPLACE(TDD_CLSPRC,',','') AS REAL) c,
                  CAST(REPLACE(NAV,',','') AS REAL) n FROM krx_etf_bydd_trd WHERE bas_dd_req='20260909')
SELECT ISU_CD,ISU_NM,c,n,ROUND(100.0*(c/n-1),3) disc_pct FROM p
WHERE n>0 AND c>0 AND ABS(c/n-1)>0.05 ORDER BY ABS(c/n-1) DESC;

-- [10] 중복
SELECT COUNT(*) FROM (SELECT BAS_DD,ISU_CD FROM krx_stk_bydd_trd GROUP BY 1,2 HAVING COUNT(*)>1);

-- [11][12] 수집 시각 / 로그 정합
SELECT collected_at,COUNT(*) FROM krx_stk_bydd_trd WHERE bas_dd_req='20260909' GROUP BY 1;
SELECT l.endpoint,l.bas_dd,l.n_rows,a.c actual FROM ingest_log l
LEFT JOIN (SELECT 'sto/stk_bydd_trd' ep,bas_dd_req d,COUNT(*) c FROM krx_stk_bydd_trd
           WHERE bas_dd_req>='20260818' GROUP BY 2) a ON a.ep=l.endpoint AND a.d=l.bas_dd
WHERE l.bas_dd>='20260818' AND l.endpoint='sto/stk_bydd_trd' AND CAST(l.n_rows AS INT)<>a.c;
```

---

## 권고 (우선순위)

1. **[A01]** `stage` 에서 09-09 자 `adj_factor` 가 001290 = 0.2, 273060 = 0.2 로 산출되었는지 즉시 확인.
   없으면 두 종목의 09-09 이전 가격 전량이 미조정 상태다.
2. **[A02]** `PARVAL` / `LIST_SHRS` 일일 변동 탐지 질의를 건전성 게이트에 추가.
   행수·status 게이트로는 기업행위를 절대 잡을 수 없다는 것이 이번 검수의 핵심 교훈이다.
3. **[A03]** ETF 사용 시 `ACC_TRDVOL = 0` 또는 `|종가/NAV−1| > 5%` 를 배제 필터로 강제.
4. **[A07]** `시세.ISU_CD = base.ISU_SRT_CD` 조인 규칙을 스키마 문서와 `stage` 매핑에 명문화.
5. **[A05/A06]** 검증 게이트에 화이트리스트 추가 — `FLUC_RT` 검산은 3원 이하 허용,
   `(외국주포함)` 지수 2건은 종가 결측 예외.
