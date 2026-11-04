# 지수 ETF 타이밍을 엔진에서 표현한다 — 설계 (2026-11-04, 리뷰 반영판)

> 이 문서는 초안을 SoT · 책임 경계 · UI/UX · 잠재 결함 리뷰어 네 명(서브에이전트)이 검토한 뒤 고친 판이다. 리뷰에서 나온 결정은 각 절에 "결정"으로 적었다.

## 1. 배경

사용자 로컬 실험(저장소 밖)에서 "코스피200 ETF 하나를 지수 추세·변동성으로 0~100% 들고 빼는" 규칙(T9 = 200일선 ±2% 밴드 × 조건부 변동성 조절)이
낙폭을 크게 줄였다. 그 계산은 엔진이 아니라 별도 스크립트였다 — 지금 엔진은 이 전략을 표현하지 못한다. 사용자 결정(2026-11-04): 제품 기능으로 채우고 같은 규칙을
엔진으로 다시 확인한다. 엔진 확인은 연구 하한(2020-01-02) 이후 구간에서만 한다(§9).

| # | 필요한 것 | 지금 |
|---|---|---|
| A | 실행을 특정 종목(KODEX 200, 069500)으로 한정 | 유니버스는 원장 정책 단위뿐, 종목 지정 칸 없음 |
| B | 코스피200 지수 시가·종가를 그래프 필드로 | 원천 축이 `TICKER`·`CORP` 뿐. 원장은 `benchmark.close` 프로필을 선언했지만 워크벤치는 미지원 표로 막아 둠 |
| C | 창 안 백분위 순위·중앙값, ±2% 밴드(사이에선 직전 상태 유지) | 시계열 연산은 mean·std·momentum·delta·min·max, 상태 노드 없음 |
| D | 그날 투자 비중을 팩터 출력(0~1)으로 | 총노출은 `risk.gross_exposure` 상수, 레짐은 필드 문턱 하나(종목별 0/100) |
| 0 | 위 그래프를 쓰려면 `pt / pt`, `원 / 원` 같은 같은 단위 나눗셈이 무차원이어야 한다 | 단위 추론이 `(KRW/KRW)` 를 `1` 로 줄이지 않아 "비율 − 1" 이 `strategy.expression.unit_mismatch` 로 거절된다(실험 중 두 번 걸림) |

범위 밖(별도 이슈): 종목별 매도세 구분(ETF 비과세 배선 — 엔진 계약 변경), 레버리지(투자 비중 > 1), 종가 체결, 지수가중 평균(실수 파라미터 속성 필요),
원장 `krx.etf` 정책, 결과 참고선에 코스피200 추가, 두 값의 min·max 연산자(T9 는 조건 분기로 표현한다).

## 2. 0 — 같은 단위 나눗셈은 무차원

- 결정: 단위 추론(`domain/factor/_validation.py` 의 계약 추론 — 단위 규칙의 정본)에서 나눗셈 결과 단위를 정규화한다. 분자·분모가 같은 단위면 `1`, `(1*1)`·`1*X` 처럼
  무차원이 섞인 곱은 무차원을 지운다. 표기 규칙(괄호·곱 순서)은 지금 문자열 표기를 그대로 두고 비교만 정규화된 형태로 한다 → 기존에 통과하던 문서는 그대로 통과한다.
- **표기는 바꾸지 않는다**: 실행 계획 `output_unit` 이 `plan_hash` 에 들고 그 해시가 연속 계좌·예약 실행 후보 신원(`candidate_id`)에 실린다. 비교만 약분한 차원
  (`unit_dimensions`·`same_unit`)으로 한다 → 저장 문서·계획·후보 신원 불변. 지금 `unit_mismatch` 로 거절되던 일부 문서가 통과하게 되는 것이 동작 변화다.
- 같은 PR 에서 조건 분기의 "상수 가지 + 숫자 시계열 가지" 를 허용한다(T9 의 `scale > 1 ? 1 : scale`). 두 평가기는 이미 상수를 행마다 펼쳐 처리한다(패리티 말뭉치에 이미
  `gate ? neg : 0` 이 있다) — 검증기만 막고 있었다. 참거짓과 숫자 섞기는 그대로 막는다.
- 후속(범위 밖): 시계열 `momentum` 출력 단위가 입력 단위로 남는다(수익률인데 `KRW`). 무차원으로 바꾸면 표기·`plan_hash` 가 바뀌어 후보 신원 마이그레이션이 필요하다.
- 대장 54행(단위 추론 owner) 갱신, 회귀 테스트: `price.adj_close / lag(price.adj_close, 5) − 1` 이 통과, `원 ÷ 원 − 주` 는 여전히 거절.

## 3. A — 실행 설정 "종목 지정"

### 3.1 값과 신원

- `RunEnvironment.tickers: tuple[str, ...] = ()`. 비어 있으면 지금과 같다. 값이 있으면 **관측 모집단**이 "유니버스 정책 ∩ 그 티커들의 모든 상장 구간"으로 줄어든다 —
  후보만이 아니라 횡단면 정규화·결측 채움 동료·기준일 요약·추적 모집단이 함께 줄어든다(문서와 매뉴얼에 적는다).
- **형식 정본**: `domain/backtest/_models.py` 에 `KRX_TICKER_PATTERN = ^[0-9A-Z]{6}$`, 최대 50개(`RUN_ENVIRONMENT_CONSTRAINTS` 행). `__post_init__` 이 strip·대문자·정렬로
  정규화하고(같은 묶음은 같은 값), 중복·형식 위반은 `InvalidRunFieldError("tickers", values=…)` → `backtest.run.field_invalid`. duckdb `_TICKER_RE` 는 id 해석 가드라고
  주석으로 가른다(입력 형식 규칙이 아니다). 대장에 "티커 형식" 행을 둔다.
- **신원(해시)**: 새 칸은 기본값일 때 신원 payload 에서 뺀다. 그 규칙을 `domain/backtest/_canonical.py` 의 한 함수(`canonical_environment_payload`)에 두고 실행 지문 ·
  `environment_hash` · 모의 홀드아웃 결합 해시(`_holdout_registration.py`)가 모두 그것을 쓴다 → 옛 run·기록의 신원이 그대로다(V6-01 저장 기록도 읽힌다).
  시도 키는 `_trial_key._environment_key` 가 이미 기본값을 빼므로 `TRIAL_KEY_ROLES["tickers"] = KEY` 한 줄과 `test_run_environment_ownership.py` 분류만 더한다.
  (손절 브랜치의 `drop_inactive_stop_loss` 와 같은 꼴 — 병합 때 한 함수로 합친다.)

### 3.2 판정 — 사실은 어댑터, 판정은 domain

- 포트 `SecurityDirectoryPort` 에 `resolve_tickers(market, universe_id, start, end, tickers) -> TickerResolution` 을 더한다. 어댑터는 티커마다 사실만 돌려준다:
  `UNKNOWN`(원장에 없음) · `NEVER_MEMBER`(측정 세션 동안 한 번도 유니버스 구성원 아님) · `MEMBER`(구성원 세션 수, 상장 구간별 security_id·이름). 재상장 티커는 모든 구간.
  duckdb 는 `_spans`·`_security_names` 를 재사용, mock 은 fixture 에서. 계약 테스트 `tests/contract/test_security_directory.py` 에 두 어댑터 공통 사례.
- 판정은 `domain/backtest` 순수 함수 `ticker_admission(resolution)` 가 한다. 호출은 `PortfolioDesignService._prepare` 한 곳(실행·미리 계산·추적·미리보기가 같은 판정).
  거절은 벤치마크 선례(`backtest.run.benchmark_unknown`, tape 앞 한 번 조회)처럼 **이른 실패 코드** `backtest.run.ticker_unknown`·`backtest.run.ticker_outside_universe`
  (`RunFailureCode`·`AdmissionRejectionCode` 어휘에 등록, frontend `backtest.error.<code>` 문장). 문장은 유니버스 표시명을 자리표시자로 받는다(id 하드코딩 없음).
- 부분 구성원(측정 세션 일부만)·구간 둘 이상은 결과 경고(`DataWarning`)로 세션 수와 구간별 이름을 싣는다.
- 포트 `RawObservationQuery.tickers: tuple[str, ...] | None`(None = 전체) — 거르기만 한다. duckdb 행 경로(`load_raw_observations`)와 열 경로(`_raw_panel`) 둘 다 넘긴다.
  공통 계약 테스트: 한 티커 = 그 티커의 모든 구간, 걸러낸 결과 = 거르지 않은 결과를 그 티커로 거른 것과 셀마다 같다(부분집합 불변), 정렬.
- **유니버스 전체가 필요한 소비자는 `tickers=()` 로 되돌린다**: 결과 팩터 회귀(`application/backtest_run/_service.py` 의 회귀 파이프라인·캐시 키). 목록을 대장 실행 설정 행에 적는다.

### 3.3 경고

- 생존편향(정보): `domain/backtest` 순수 함수가 만들고 결과 `DataWarning` 으로 붙인다(`_participation_warnings` 와 같은 자리). 문장 "종목을 직접 골라 돌린 실행입니다.
  지금 아는 종목을 고른 것이라, 그 시점에도 이 종목을 골랐을지는 이 결과가 말해 주지 않습니다."
- **투자되지 않는 예산**(종목 수 × 종목 상한 < 총노출): 식 하나를 `domain/strategy` 함수 `unallocated_budget(spec, candidate_count)` 로 두고 세 표면이 같이 쓴다.
  (1) compile 경고 `strategy.risk.name_cap_leaves_cash`(개수 선택일 때 `selection_count` 기준, 비율 선택·롱숏은 쪽별), (2) 실행 설정 패널의 실행 전 점검(시도 영향 미리보기와
  같은 backend 판정 — `tickers` 가 있으면 그 개수), (3) 컴파일러 사실 경고 `PortfolioWarningCode.BUDGET_UNALLOCATED`(실제로 예산을 다 배분하지 못한 프레임 수).
  문장: "최대 {n}종목 × 종목별 최대 목표 비중 한도 {cap}% = {invested}% 만 투자되고 나머지는 현금입니다. 리스크 제약의 '종목별 최대 목표 비중 한도'를 올리세요."

### 3.4 화면

- 실행 설정 "먼저 기간·종목 범위를 정하세요" 묶음의 유니버스 바로 아래 "종목 코드 지정(선택)" — 접힌 고급 실행이 아니다. 힌트 "비우면 유니버스의 모든 종목을 후보로
  씁니다. ETF 는 매도 거래세가 없습니다 — ETF 만 담는 실행은 '세금 없음'을 고르세요."
- 칸 모델: 스키마 `type: array`, `items.pattern`, `maxItems: 50`, `uniqueItems`, `x-catalog: security`(`x-catalog: universe` 선례). frontend 는 스키마의 pattern·maxItems 로
  빠른 피드백만 한다. 입력은 쉼표·띄어쓰기로 나눈 텍스트 칸, 붙여 넣은 "KODEX 200(069500)" 에서 코드만 뽑는다.
- 칸 아래 **확인 목록**(색이 아니라 글자): "KODEX 200 (069500) · 확인됨" / "999999 · 찾을 수 없는 종목 코드" / "069500 · KRX 보통주 (krx.common-stock) 에 들지 않는 종목입니다.
  유니버스를 KRX 전체 (krx.all) 로 바꾸세요." — 새 HTTP 경로 `GET /api/v1/equity/securities/resolve`(위 `resolve_tickers`)를 `entities/dataset` 훅으로 부른다. 확인 목록에
  거절 사유가 있으면 백테스트 버튼이 막히고 "실행 설정 고치기" 가 해당 칸으로 초점을 옮긴다. 최종 판정은 backend.
- **저장**: `tickers` 는 전략 키에만 저장하고 브라우저 "마지막 사용값"(`LAST_USED`)에서 뺀다 — 다른 전략으로 조용히 따라가지 않는다.
- 요약 띠 간략 표시 "종목 지정: KODEX 200 (069500) 외 n", 결과 실행 기록 "실행 설정" 행에 이름을 풀어 쓰고 "결과 한눈에" 위에 "종목 지정 실행 · n종목" 배지.
  run 상세의 이름은 page 가 조합해 props 로 넘긴다(FSD).
- 유니버스 `krx.all` 표시명·설명 등록: "KRX 전체 (ETF·우선주 포함)"(`domain/equity/_models.py` `_UNIVERSE_TEXT`).
- mock: fixture 에 ETF 1종(069500)과 `krx.all` 유니버스를 더한다(e2e 전제).

## 4. B — 시장 지수 필드

### 4.1 원장(정본)

- 결정: 원장의 `benchmark.close` 프로필(값 `close_idx`, 지수 이름 구분 없음)을 **지수별 프로필로 대체**한다: `index.kospi200.open`·`index.kospi200.close`·`index.kospi.close`·
  `index.kosdaq.close`(단위 `pt`, 일별, 공개 지연 0, row_filter `index_class = … AND index_name = …` — 실값 `코스피 200` 처럼 공백 포함, `coverage_axis = table_rows`).
  `RULES_VERSION` 올림. FIELD_MAP §1 에 "날짜 축(시장 수준) 필드" 행, §2 에 필드 행, 35행 사실 정정, `idx:` 예약은 벤치마크 security 주소로만 범위를 좁힌다.
  워크벤치 `UNSUPPORTED_FIELDS["benchmark.close"]` 행을 지운다.
- 게이트: `open_idx` null·0 과 코스닥 지수 커버리지를 원장 evidence 로 잰다.
- 지수 이름 대응(`kospi` ↔ `코스피`)은 결과 참고선 `MARKET_REFERENCE_INDICES` 에도 있다 — 원장 프로필 row_filter 와 묶는 대조 테스트를 둔다(정본은 원장 프로필).

### 4.2 워크벤치

- 새 원천 축 `SourceAxis.MARKET`(키 = 날짜). 지수마다 `SourceSpec` 하나, 부팅 때 날짜 유일성 검사(같은 날 두 행이면 부팅 거절). `_grid_sql` 의 티커 묶음(`key_column IN …`,
  `ON g.k = r.ticker`)과 열 경로 `_GridLookup` 에 MARKET 특례. 커버율 분모는 원장 `table_rows` + row_filter 커버율과 같은 뜻(세션 수).
- **시장 수준 표식**: 필드 계약에 `FieldMetadata.market_level: bool` 을 더한다(어댑터가 선언 → `field_metadata` → 검증기). 검증기(`domain/factor/_validation.py`)가 계약 추론에
  "시장 수준" 전파를 더한다: 잎이 시장 수준 필드·상수뿐이고 횡단면·그룹 연산이 없으면 시장 수준. D 의 정적 판정과 경고가 이것을 쓴다.
- 경고 `strategy.factor.market_level_in_composite`: 시장 수준 출력을 알파 합성에 쓰면 "모든 종목이 같은 값이라 순위에 영향이 없습니다 — 조건·비교나 투자 비중 팩터로 쓰세요".
- mock: `index.*` 는 날짜만의 함수(종목 index 를 씨앗으로 쓰지 않는다), 같은 계약(단위·값 타입·빈도). 공통 계약 테스트 "MARKET 필드는 한 날짜의 모든 행에서 같은 값".
- 화면: 필드 라벨 "코스피200 종가 (지수)" 등, 계약 패널 필드 상세에 "시장 전체 — 날짜마다 모든 종목 같은 값". 필드 목록은 계약 응답만 표시하므로 frontend 코드 변경 없음.
- 알려진 한계(문서): 시계열 연산은 종목 행 위치 기준이라 상장 이력이 모자란 종목 행에서는 시장 필드의 이동평균도 "이력 부족"이다(값이 다른 것이 아니라 없는 것).
  `data_snapshot_id` 는 필드 계약 판이 바뀌어 모두 바뀐다(정상 — 옛 run 팩터 회귀는 `data_snapshot_changed`).

## 5. C — 시계열 연산자

### 5.1 `rank`(기간 안 백분위) · `median`(기간 중앙값)

- `TimeSeriesOperator` 에 값 둘(새 속성 없음 → 기존 노드 해시 불변). 창 칸이 모두 숫자일 때만 값(부분 창 없음, 기존 규칙).
- `rank`: 창 값에 기존 횡단면 순위 정의 `_rank_definition`((평균 순위 − 1)/(n − 1), 동점 평균)을 적용한 **마지막(현재) 원소**. 값 [0, 1], 무차원.
- `median`: `statistics.median`(짝수면 가운데 둘의 평균), 열 경로는 `segment_median` 과 같은 규칙. 입력 단위. −0.0 은 +0.0 으로 접는다.
- 단위: 검증기 `_validation.py` 에 시계열 무차원 집합 `{RANK}` 를 둔다(단위 추론 owner). 레지스트리(`_operators.py`) 행은 `reads_past_sessions=True`·표시값.
- `FACTOR_EVALUATION_VERSION` 고정 digest: 기존 6개 연산자를 명시 튜플로 고정하고 새 둘은 따로 고정(#608 선례).
- 화면 이름: "기간 안 백분위"(공식 "기간 값 중 현재 값의 순위 비율, 0~1"), "기간 중앙값". 설명에 "백분위·중앙값" 낱말.
- 성능 메모: 창 756 은 행마다 정렬 — 시장 수준 부분 그래프를 날짜당 한 번 평가하는 최적화는 범위 밖, 구현은 정확 커널.

### 5.2 `state.band`(두 문턱 신호) — 새 노드 종류

- 노드: `{kind: state, operator: band, input_node_id, upper_node_id, lower_node_id, state_window}`. `kind: state` 를 새로 두고 레지스트리에 `(state, band)` 행을 올린다
  (`reads_past_sessions=True` — 원주가 경고가 band 도 본다). 세 입력은 노드 참조(상수는 constant 노드), 숫자여야 하고 단위는 확인하지 않는다(비교 노드와 같다).
- 의미: 오늘을 포함한 최근 `state_window` 세션에서 **마지막 돌파**가 "입력 > 위 문턱" 이면 1, "입력 < 아래 문턱" 이거나 돌파가 없으면 0. (창 첫 세션부터 상태 0 으로
  재생한 히스테리시스와 같다.) 출력 boolean — 산술에 쓰려면 조건 분기로 0/1 을 고른다.
- 창 칸(세 입력)이 모두 숫자일 때만 값. 창 시작이 이력 밖이면 None(부분 재생 금지 — 값이 질의 시작일에 따라 바뀌지 않는다).
- 읽기 창: `node_read_window(band) = SessionReadWindow(0, state_window − 1)`(대장 51행 정본). 검증 이력 = max(세 입력의 이력) + state_window − 1 — 검증기 전용 분기.
  가린 칸 구간(`_value_spans`)은 세 입력 구간의 hull 을 state_window − 1 만큼 넓힌다. `node_dependencies` 에 band 를 넣고 모르는 kind 는 거절한다(조용한 `()` 금지).
- `state_window` 는 정수 ≥ 1(`minimum(1)` 선언, 대장 55행). 진단 코드는 `factor.graph.*` 와 `EXPRESSION_CODES` 대응에 등록.
- 두 평가기(객체·열) 같은 정확 커널, parity 말뭉치·퍼즈에 추가.
- 화면: 종류 이름 "두 문턱 신호", 설명 "입력이 위 문턱보다 크면 켜지고(1), 아래 문턱보다 작으면 꺼집니다(0). 사이에서는 직전 상태를 유지해 평균선 근처에서 신호가 자꾸
  바뀌는 것(휩쏘)을 막습니다. 히스테리시스 밴드라고도 합니다." `state_window` 라벨 "상태를 되짚는 기간(세션)", 설명 "최근 N세션만 처음부터 따라가 켜짐·꺼짐을 정합니다.
  N세션 내내 두 문턱 사이에 머물면 꺼짐으로 봅니다. 평균선 기간보다 넉넉히 두세요(예: 252)." 키는 `strategy.operator.state.band`.
- 추적: boolean 값을 한글("참·거짓", band 는 "켜짐·꺼짐")로 보이고, band 행에 까닭 — "위 문턱 넘음" / "사이 — 직전 상태 유지(마지막으로 바뀐 날 …)" / "아래 문턱 밑" /
  "이력 부족". backend 추적 status 가 까닭과 마지막 바뀐 날을 싣는다(frontend 가 추론하지 않는다). 기준일 하나의 값만 보인다(워밍업 세션 값을 보고하지 않는다).

## 6. D — 투자 비중 팩터

### 6.1 문법·검증

- `RiskStep.exposure_factor_id: str | None`(`REFERENCE_FACTOR`, 단계 RISK). 늘 합성 점수에서 빠진다 — 제외 집합 owner `composite_factors` 에 더한다.
  None 이면 정규 JSON 에서 뺀다(`domain/strategy/_canonical.py` tie_breaker 꼴) → 기존 `spec_hash` 불변. 의미 해시의 팩터 번호 바꾸기는 메타데이터로 자동.
- 검증: 참조 없음 `strategy.risk.exposure_factor_missing`, 위험·보조 팩터와 같은 id 금지, 제외 안내 `strategy.risk.exposure_factor_excluded`(정보, 문장에 `팩터=<label>`),
  **정적 시장 수준 판정** — 출력이 시장 수준이 아니면 compile error `strategy.risk.exposure_factor_not_market_level`("이 팩터는 종목마다 다른 값(<필드 이름>)을 읽습니다.
  코스피200 종가 같은 시장 지수 필드와 상수로만 만드세요."). "검증 통과 = 실행 가능"(대장 60행)을 지킨다.
- **알파 없음**: 알파 팩터가 0개이고 `exposure_factor_id` 가 있으면 허용한다 — 이때는 순위로 자르지 않고 **자격 있는 후보 전부**를 선정 방식의 가중(동일·위험)으로 담는다
  (순서가 결과에 영향을 주지 않으니 사전순 선정 결함이 생기지 않는다). 그 밖에는 지금처럼 `strategy.signal.no_alpha_factor` error. 같은 사실을 설명 문장에 적는다.
- 코드 등록: `SEMANTIC_ONLY_CODES`(`_constraints.py`), 대장 123행 목록.

### 6.2 실행

- 프레임마다 스칼라 s: 평가기는 투자 비중 팩터 출력을 **결측 채움 대상에서 뺀다**(채움 owner = 평가기, 대장 46행). 컴파일러(`_target_weights`)는 그날 `universe_member` 행 중
  값이 있는 행의 원시값을 읽고(`_risk_value` 의 공개일·유한성 판정 재사용), 정적으로 시장 수준이므로 값은 비트까지 같다 — 방어선으로 정확 일치를 확인하고 다르면
  domain 예외 → `run_pipeline` 에서 한 번 흡수 → `RunFailureCode` `backtest.run.exposure_not_market_level`.
- 범위: s ∈ [0, 1](경계 허용 1e-12 안이면 경계로). 밖이면 domain 예외 → `backtest.run.exposure_out_of_range`("{날짜}에 값 {v}가 0~1 밖입니다. 조건 분기로 1보다 큰 값은 1로,
  0보다 작은 값은 0으로 자르세요.").
- 결측(값 있는 행 없음): s = 0(현금 — 보수적) + `PortfolioWarningCode.EXPOSURE_MISSING_CASH`(세션 수·첫 날짜·투자 비중 팩터 이름). 이력은 검증기가 계산한 필요 이력만큼
  측정 시작 전 원천을 읽으므로, 측정 구간 결측은 원천 공백이다 — 경고 문장이 그것을 말한다.
- 예산: 롱 예산 = `gross_exposure × s`(롱숏이면 쪽별 예산에 곱한다). 종목 상한은 절대값. **예산 축소로 생긴 매도는 최소 거래 비중 판정의 예외**(늘 체결, trace 사유
  `EXPOSURE_BUDGET`) — s 가 줄 때 작은 매도만 비대칭으로 남는 일을 막는다.
- s 는 trace 의 프레임 요약(`PortfolioFrameSummary`)에만 싣는다 — `TargetFrame` 정규 payload(tape 해시)는 그대로. 결과의 노출도 차트와 보유 화면 현금 비중은 스냅숏 투영이
  자동으로 반영한다(frontend 가 s 로 다시 계산하지 않는다).
- s 는 리밸런싱 날에만 바뀐다(월간이면 월말까지) — 칸 설명에 적는다. 레짐(종목별 0/100)과 투자 비중 팩터(포트폴리오 전체 0~1)의 설명이 서로를 가리킨다.

### 6.3 화면

- 리스크 카드에 "투자 비중 팩터" 선택(팩터 참조 드롭다운은 `x-reference: factor` 로 자동). 고르면 그 팩터 카드의 가중치·방향 칸이 꺼지고 "투자 비중에만 씀" 배지.
  카드 문장 "그날 투자 비중은 {팩터} 값만큼(0~1), 나머지는 현금". 용어는 결과 지표와 같은 "투자 비중"(총노출·익스포저를 새로 쓰지 않는다).

## 7. 책임 경계

| 무엇 | 소유 |
|---|---|
| 단위 정규화 | `domain/factor/_validation.py` |
| `tickers` 형식·정규화·신원 payload | `domain/backtest/_models.py`·`_canonical.py` |
| 티커 사실(모름·구성원 아님·구성원) | 어댑터 `SecurityDirectoryPort.resolve_tickers` |
| 티커 판정·생존편향 경고 | `domain/backtest` 순수 함수, 호출은 `PortfolioDesignService._prepare` |
| 투자되지 않는 예산 식 | `domain/strategy` 함수 하나(compile·실행 전 점검·컴파일러가 공유) |
| 지수 필드 계약 | 원장 `dataset_profile` ↔ `_specs.py`(parity) |
| 시장 수준 표식 전파 | 어댑터 선언 → `FieldMetadata` → `domain/factor/_validation.py` |
| `rank`·`median`·`state.band` 의미 | `domain/factor`(레지스트리·두 평가기) |
| 투자 비중 채움 제외 | 평가기 / s 판정·예산 | `domain/portfolio/_compiler.py` |
| 실행 실패 문장 | `RunFailureCode` → frontend `backtest.run.error.<code>` |
| compile·결과 경고 문장 | backend 진단·경고 `message`(한글 완성) |

## 8. 유저 스토리

- 갱신: US-DM-05(종목 지정 칸), US-DM-06(새 실패 문장), US-DM-16(투자 비중·현금 표시), US-CS-01(팔레트 새 항목), US-CS-02(지수 필드 파생), US-CS-03(두 문턱 신호 까닭·s),
  US-CS-06(투자 비중 팩터 제외), US-CS-07, US-SM-02(단위 `pt`, 무차원 정규화).
- 새 스토리(예정): US-DM-17 실행할 종목을 직접 정한다, US-CS-15 시장 지수 필드로 신호를 만든다, US-CS-16 기간 안 백분위·중앙값과 두 문턱 신호를 쓴다,
  US-CS-17 투자 비중 팩터로 들고 빼기를 정한다(정본 YAML 을 붙여 넣으면 검증 통과 후 백테스트가 끝까지 돈다). 수용 기준은 각 PR 에서 확정, e2e 는 `frontend/e2e/stories/`.

## 9. 확인 실험 — T9 엔진판(IT-09)

- 구간: **2020-01-02 ~ 2026-08-31**(연구 하한 이후, 봉인 관문 예외 없음). 스크립트 대조도 같은 구간·같은 정의로 다시 계산한다.
- 실행 설정: 유니버스 `krx.all` + `tickers=[069500]`, 매일 리밸런싱(`every_n_sessions` 1), 수수료 5bp + 슬리피지 5bp(편도 10bp), `sell_tax: none`(ETF), 최소 거래 비중 0.
- 전략(정본 YAML `backend/tests/fixtures/strategy_documents/ideas/index_timing.yaml`): 알파 없음, `max_name_weight 1.0`, 투자 비중 팩터 =
  - px = `index.kospi200.close`, ratio = px / mean(px, 200), trend = state.band(ratio, 1.02, 0.98, state_window 252)
  - σ = std(px / lag(px, 1), 21) × √252, hi = rank(σ, 756) > 0.8, med = median(σ, 756), scale = med / σ
  - t3 = conditional(hi, conditional(scale > 1, 1, scale), 1), exposure = conditional(trend, t3, 0)
- 스크립트 T9 와의 차이(미리 적는다): EWMA 대신 21세션 표준편차, 요일 분할 리밸런싱 대신 매일 판정, 밴드는 252세션 고정 창, 백분위·중앙값은 위 정의, 현금 이자 0.

## 10. PR 계획

| ID | 내용 | 의존 | 스토리 |
|---|---|---|---|
| IT-01 | 설계 문서(이 문서) + 단위 정규화(§2) | — | US-SM-02 |
| IT-02 | `tickers` backend: 값·정규화·신원 payload·`resolve_tickers` 포트·판정·이른 실패 코드·관측 거르기(행·열)·팩터 회귀 되돌리기·생존편향 경고·mock ETF·`krx.all` 표시명 | IT-01 | US-DM-05 |
| IT-03 | `tickers` 화면: resolve HTTP·확인 목록·`LAST_USED` 제외·요약 띠·실행 기록·배지·ETF 세금 힌트 + e2e | IT-02 | US-DM-17, US-DM-06 |
| IT-04 | 투자되지 않는 예산: 식 함수·compile 경고·실행 전 점검·컴파일러 경고 + e2e | IT-03 | US-DM-17 |
| IT-05a | 원장 지수 프로필(`database/`) — `benchmark.close` 대체, FIELD_MAP | — | — |
| IT-05b | MARKET 축·지수 필드·`market_level` 표식·mock·parity·합성 경고 | IT-05a(원장 빌드·동기화) | US-CS-15, US-CS-02 |
| IT-06 | 시계열 `rank`·`median` | IT-01 | US-CS-16, US-CS-01 |
| IT-07 | `state.band` 노드·추적 까닭 | IT-06 | US-CS-16, US-CS-03 |
| IT-08 | 투자 비중 팩터·알파 없음 규칙·정적 시장 수준 판정·s 실행·정본 YAML | IT-05b, IT-07 | US-CS-17, US-CS-06, US-DM-16 |
| IT-09 | T9 엔진판 확인(코드 변경 없음, 실험 기록) | IT-02~08 | — |

- 각 PR: 대장(`strategy-workbench-sot.md`) 해당 행 갱신, OpenAPI·SDK 재생성(생성 파일은 직렬 머지 + 머지 직전 main 병합·재생성), 리뷰어 셋(SoT · 책임 경계 · 잠재 결함)
  병렬 리뷰, CI 전체 green 후 머지.
- 손절 브랜치(`Nochiski/stop-loss-exit-p1-language`, 미머지)와 `RiskStep`·`_canonical.py`·`_constraints.py`·`runtime-schema.json`·`messages.ts` 가 겹친다 — 먼저 머지되는 쪽에
  맞춰 다른 쪽이 rebase 한다.
