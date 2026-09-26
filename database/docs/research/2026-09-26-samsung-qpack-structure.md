# 삼성증권 Q.Pack(2021-06-04, 김동영 CFA) 구조 — 우리 엑셀 규격의 참조 (읽기 전용 조사, 2026-09-26)

원본: `~/Desktop/kael-system-v3/docs/references/SamsungQ.Pack-20210607.xlsm` (1.4MB, VBA 포함, 데이터 소스 Quantiwise·Bloomberg·Thomson). 시트 10개(+숨김 `b6` 시계열 덤프). **점수 모델이 아니라 데이터 팩**: 종합 점수 없음, 원자료 + 색 스케일로 눈으로 고르는 형식. 수식은 Earnings 시트의 반기 합·HOH 정도만(2,637개), 나머지 전부 값.

## 시트 구성
| 시트 | 내용 | 크기 |
|---|---|---|
| Market | 글로벌 지수: 지수 변화 = Fwd EPS 변화 + Fwd PER 변화 분해(a=b+c), EPS 성장·PER·PBR·ROE·ERR·추정치 수·시총 | 54×57 |
| FICC / Econ / Industry | 금리·환율·원자재, 경제지표, 산업 지표(나프타·열연 등, 단위·주기·출처) | — |
| Style | Citi Pure Equity / Bloomberg US Pure 팩터 지수(Risk·Quality·Value·Growth·Estimates Momentum·Price Momentum·Size·…)의 1D~5Y 수익률 + 라인차트 2 | 29×27 |
| Fundflow | 시장별 투자자별 누적 매매(1D·2D·1W·2W·MTD·1M·3M·6M·YTD·1Y) | 111×25 |
| Sector | WI26 업종 26행: 지수 절대/상대 성과 8창, 컨센 성장(Sales·OP·NP 21E/22E), PER·PBR(Trail/Fwd/21E/22E), OP·NP 1m 변화, ERR 점수변화율(1w), 투자의견, DY, EV/EBITDA, ROE, ROA, 외국인 비중(현재·1M 증가폭), Beta(60D·52W) | 43×67 |
| **Company** | 종목 500 × 83열(아래) | 507×83 |
| Earnings | 종목 500: 연간(2020·21E·22E)·분기(1Q20~4Q22) 매출·OP·NP·지배NP + y-y, 어닝시즌 정보(발표일·예정일·잠정치·컨센·서프라이즈 비율·OP 서프라이즈 스코어), 반기 HOH/YOY | 507×124 |

## Company 시트 열 구성(83열, 2단 헤더)
식별: Code(A005930)·Name·Listed(KS/KQ)·Sector·WI26 Industry·보통주 시총(Wbn)·**일거래대금 1년평균(Wbn)**·Price → **Price change** 1D·2D·1W·MTD·1M·3M·YTD·1Y → **Fwd EPS change** 1D·2D·1W·MTD·1M·YTD·1Y → **Fwd PER change** 7창 → 추정치 수 → EPS(Fwd·21E·22E) → EPS Growth → PER(Trail·Fwd·21E·22E) → **종목PER/업종PER(Fwd, 배)** → PBR(4) → 종목PBR/업종PBR → 실적추정 기준(FY1·FQ1) → OP Change 1m(FY1·FY2·FQ1·FQ2) → 이익변동성(3년 OP y-y %) → **변동성조정 OP Change**(FY1·FY2 1m) → EPS Change(FY1 1w/1m·FY2 1w/1m·FQ1/FQ2 1m) → **ERR(%)** FY1/FY2 1m → 변동성(60D) → DPS(20·21E) → DY → 배당성향 → EV/EBITDA(21E·22E) → PCR → ROE(21E·22E) → ROA → 외인 지분율 → **누적순매수 규모(시총대비 %)**: 20일 기관·5일 기관·5일 투신·5일 외국인.

## 서식 규약(그대로 가져올 것)
- 1행 `sort` 마커 행, 2~4행 제목 블록(시트명 / 저자·소속 / **기준일 + Source**), 6~7행 2단 헤더(그룹/세부, 줄바꿈), 8행부터 데이터. 틀고정 `Z8`(식별+가격변화 25열 고정), 자동필터 헤더 행.
- 글꼴 맑은 고딕 8, 숫자 `#,##0` / `#,##0.0` / `#,##0.00`, 병합 없음.
- **색 스케일은 변화·점수 열 블록에만**(Excel 3색 63BE7B–FFEB84–F8696B, 초록=높음): Price change, Fwd PER change, EPS change, ERR, OP change, 서프라이즈 스코어, Sector 성과·리비전. 레벨 값(PER·PBR·ROE 등)은 무색.
- 업종 상대값을 열로 제공(종목PER/업종PER) — 우리 "업종 내 백분위"와 같은 취지.
- 리비전 정규화: `변동성조정 OP change = 1m 변화 ÷ 3년 OP y-y 변동성`.
- 수급 정규화: 누적순매수 ÷ 시총(%), 창 5일·20일, 주체별 열.

## 우리 규격에 반영(D-12·T2.5b)
매일 엑셀의 `순위` 시트를 Company 시트 문법으로: 식별(코드·이름·시장·WICS 대분류·중분류·시총·20일 거래대금·주가) → **우리 추가: 종합 순위·6 버킷 순위(유니버스/업종)** → 가격 변화 창 → 선행 EPS 변화 창 → 밸류(후행 E/P·B/M·배당, 종목/업종 비) → 리비전(OP·NP 1m·3m, 변동성조정) → 수급(시총 대비 5·20·60일 주체별) → 퀄리티 원값 → 저위험(60D 변동성·회전율). 색 스케일은 순위·변화 열에만(초록=좋음으로 통일, DUAL FACTOR 의 빨강=1위와 달리). `Earnings` 시트 격의 실적 시트(연간·분기·어닝시즌 정보·서프라이즈)와 `Sector` 격의 업종 시트도 같은 문법으로. 수식 없음(값만).
