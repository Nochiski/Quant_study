# S03 첫 조각: 미래 가격 결과와 예측 순위 비교

## 현재 판정

**구현한 범위:** 독립 도메인의 가격수익 라벨, 날짜별 Rank IC, 상태별 건수·비교 가능 비율, 기존 실제 팩터 평가·종목 선정 경로의 값 연결을 합성 예제로 검증했다.

**아직 완료하지 않은 범위:** 첫 실데이터 연구, 가설 선택·동결, 5/21/63 중 최선 기간 선택, 비용 후 실데이터 성과, 통계적 유의성, 중첩 라벨의 의존성을 반영한 추론, HTTP·화면 연결은 구현하지 않았다. 후속 CLI·bootstrap 연결은 [runner 사양](S03-runner.md)이 소유한다. 이 조각은 기존 M3 전체 완료 여부나 S03 연구 완료를 뜻하지 않는다. 합성 예제의 양의 상관을 실제 예측력 근거로 사용하지 않는다.

## 책임과 데이터 흐름

`domain.signal_evaluation.facade.evaluation`가 미래 평가를 소유한다. `domain.factor.facade.cross_section.cross_sectional_rank`의 동점 평균 순위를 재사용한다. 기존 `PortfolioDesignService.run_pipeline`이 만든 `observations`를 읽으며, 이미 존재하는 `PortfolioFactorValue`를 다시 계산하지 않는다.

```text
원시 관측 → 기존 factor graph 평가 → PortfolioObservation → 기존 선정 compiler → TargetTape
                                              │
                                              └→ 새 evaluate_signal ← 별도 미래 조정종가·공통 거래달력·상장구간
                                                        ↓
                                              날짜·기간별 비교 결과와 제외 사유
```

의존 방향은 새 도메인에서 `domain.factor`, `domain.portfolio`, `domain.backtest` facade로만 향한다. application, adapter, 엔진, DB, 환경변수에 의존하지 않는다. 기존 application/portfolio_design 파일을 수정하지 않았고, 포트폴리오 손익 지표 registry에 IC를 추가하지 않았다. 미래 라벨은 기존 factor graph나 TargetTape에 쓰지 않는다.

실제 연결 검증인 `backend/tests/integration/test_signal_evaluation_pipeline.py`는 작은 합성 raw port와 기존 메타데이터 계약을 사용해 실제 `run_pipeline`을 실행한다. 세 종목의 평가된 값 10·20·30을 그대로 받아 이후 수익 10%·20%·30%와 비교한다. 미래 가격만 바꿔 상관이 +1에서 −1로 바뀌어도 원래 매매계획과 다시 계산한 신호·선정은 같다. 이 예제에는 분할이 없으므로 원종가와 분할 반영 종가가 같다는 조건으로만 입력을 변환한다.

## 가격 라벨 명세

입력 타입 `AdjustedCloseObservation`은 **분할을 반영하고 현금배당을 제외한 종가**다. 원주가나 배당 포함 총수익 가격을 임의로 이 타입에 넣으면 계약 위반이다. 이 첫 조각은 가격의 조정 근거를 DB에서 검증·구축하지 않는다. 실데이터 연결자는 데이터 판본과 가격 조정 계약을 먼저 확인해야 한다.

`PriceLabelSpec`은 다음을 명시한다.

- `horizons_sessions`: 비교 기간. 기본 후보는 `(5, 21, 63)`이며 결과를 본 뒤 하나를 고르는 코드가 없다.
- `entry_delay_sessions`: 신호일에서 종가 기준 진입일까지 늦추는 세션 수. 기본 0이다.
- `minimum_pairs`: 같은 날짜·기간에서 상관을 계산하는 최소 유효 쌍 수. 기본 3, 허용 최소 2다.

공통 거래달력을 `C`, 신호일 위치를 `i`, 진입 지연을 `d`, 비교 기간을 `H`, 분할 반영 종가를 `P`라 하면:

```text
entry_session = C[i + d]
label_end     = C[i + d + H]
return_fraction = P[security, label_end] / P[security, entry_session] - 1
```

`H`는 종목별 가격 행 수가 아니라 두 종가 사이의 **공통 거래 세션 간격 수**다. 금요일 신호, 다음 월·화·수 거래달력에서 `d=1,H=2`면 월요일 종가부터 수요일 종가까지다. 월요일 20, 화요일 50, 수요일 80이면 라벨은 `80/20−1=3`이다. 특정 종목의 월요일 bar가 없어도 화요일로 진입일을 밀지 않고 `missing_entry`로 표시한다. 휴장일은 호출자가 제공한 공통 달력에 들어가지 않는다. 거래정지일은 공통 달력에는 남고 해당 종목 가격만 없을 수 있다.

이는 **종가에서 종가까지 가격 예측을 비교하는 수익량**이다. 실제 다음 시가 진입, 보유 기간, 리밸런싱, 수수료·거래세·시장충격을 반영하는 계좌 손익은 별도다. IC가 높거나 라벨 수익이 양수라는 이유로 비용 후 수익이라고 부르지 않는다. 계좌 원장 연결의 손계산 근거는 S02다.

## 공개 진입의 검증과 평가 시점

- 신호·가격 데이터의 nonempty snapshot ID가 같아야 한다. 두 입력의 실제 파일 판본은 호출자 책임이며, 동일 문자열만으로 내용의 동일성을 인증하지 않는다.
- `require_research_window`를 기존 `domain.backtest` facade에서 읽어 평가일과 모든 신호일에 적용한다. 2020-01-02 이전 신호의 측정은 새 공개 함수에서도 거절한다. 이번 테스트의 데이터는 전부 2024 합성값이다. 봉인 거절 테스트는 과거 데이터 계산 전에 예외를 확인한다.
- 공통 달력은 비어 있지 않고 중복 없이 오름차순이어야 한다. 가격과 신호의 `(session, security_id)`도 중복을 거절한다. 같은 관측 안의 중복 factor ID, 비유한 신호, 0 이하·비유한 가격을 거절한다.
- 신호의 `available_date <= signal_session`을 만족해야 유효 쌍에 들어간다. 늦게 공개된 값이 크더라도 사용할 수 없다.
- `label_end <= evaluation_as_of`와 진입·만기 가격의 `available_date <= evaluation_as_of`를 만족해야 한다. 입력에 미래 가격이 이미 있어도 아직 만기가 아니면 수익률을 반환하지 않는다.

평가 함수는 개별 데이터의 부재를 예외로 뭉개지 않고 아래 상태로 돌려준다. 구조·숫자 계약 위반만 `ValueError`다.

| 상태 | 의미 |
|---|---|
| `calendar_insufficient` | 진입·만기 위치를 찾을 공통 달력이 부족함. 임의 영업일로 만기를 추정하지 않음 |
| `not_matured` | 달력상 만기는 있으나 평가일 이후임 |
| `listing_status_unknown` | 해당 종목의 상장구간 근거가 없음. 가격만으로 생존을 추정하지 않음 |
| `listing_ended_unsettled` | 만기 전에 상장구간이 끝남. 마지막 가격·0원·임의 회수금으로 대체하지 않음 |
| `outside_listing` | 진입이 확인한 상장구간 시작 전임 |
| `adjustment_gap` | 해당 종목의 진입~만기 양끝 포함 구간에 원장 MASKED 조정 공백이 있음. 수익은 반환하지 않음 |
| `missing_entry` / `missing_exit` | 정해진 진입·만기 세션 가격이 없음. 다음 종목 관측행으로 건너뛰지 않음 |
| `price_not_available` | 가격 공개일이 평가일 이후임 |
| `signal_missing` / `signal_not_available` | 신호가 없거나 판단일에 아직 알려지지 않음 |

라벨 상태의 판정 순서는 달력 부족 → 미만기 → 상장근거/종료/상장 전 → 조정 공백 → 진입·만기 결측 → 공개시점이다. 예를 들어 아직 만기 전인 종목은 상폐 정보를 알고 있어도 우선 `not_matured`로 나타난다. 신호 상태와 라벨 상태는 표본별로 모두 보존한다. 호출자는 실제 상장구간 근거를 주어야 하며, 쿼리 종료일에 잘린 membership을 실제 상폐일로 오해해서는 안 된다. 근거가 없으면 해당 ID의 구간을 생략해 `listing_status_unknown`으로 남긴다.

선택 입력 `adjustment_gaps`는 `AdjustmentGapObservation(session, security_id)`의 목록이며 기본은
빈 tuple이다. 같은 종목의 `[entry_session, label_end]` 안에 공백이 있으면 가격이 양끝에 모두
있어도 `adjustment_gap`·수익 `None`이다. 다른 종목·구간 밖 공백은 영향을 주지 않는다.
공백 입력의 중복 키·달력 밖 날짜는 거절한다. 분모·상태 카운트·미래 정보 분리의 상세 계약은
[개별 조정 공백 사양](S03-adjustment-gaps.md)이 소유한다.

## 순위 상관과 비교 가능 비율

각 `(signal_session, horizon_sessions)`에서 유효 신호와 유효 라벨이 모두 있는 종목만 쌍으로 만든다. 신호 원값의 순위와 미래 수익의 순위에 Pearson 상관을 계산한 값이 Rank IC다. 동점은 기존 평균 순위를 쓴다. `FactorDirection.LOW` 등의 선호 방향을 사후 뒤집지 않으며, 이 함수의 IC는 전달한 **개별 factor 원값**과 미래 수익의 관계다. 전략의 방향·조합·종목 교체·비용 후 손익은 별도 판정한다.

손계산 예제에서 신호 `[1,2,2,4]`의 평균 순위는 `[1,2.5,2.5,4]`, 미래 수익 `[.3,.1,.1,.2]`의 평균 순위는 `[4,1.5,1.5,3]`이다. 편차곱 합 −1.5를 두 편차제곱 합의 기하평균 4.5로 나눠 `−1/3`을 얻는다. 테스트가 이 값을 독립 정답으로 단언한다.

- `universe_count`는 그 신호일에 전달된 `PortfolioObservation` 중 당시 `universe_member=True`인 전체 행 수다. 선정된 상위 종목만 세지 않는다. 모든 종목 행 자체가 누락된 경우를 함수가 추론할 수는 없으므로 관측 패널의 모집단 완전성은 호출자 계약이다.
- `paired_count`는 유효 신호·만기 라벨의 교집합 수다. `coverage_fraction=paired_count/universe_count`다. 비교할 행이 전혀 없는 날짜에는 가짜 0% 행을 만들지 않는다.
- `signal_status_counts`와 `label_status_counts`는 각 상태의 전체 건수를 별도로 보존한다. `exclusions`는 쌍에서 빠진 각 행을 한 번 세며, 신호 상태가 불가하면 그 사유를 먼저 세고 그렇지 않으면 라벨 사유를 센다. `paired_count + sum(exclusions)=universe_count`다.
- 상장 종료·결측을 분모에서 사후 삭제하지 않는다. 예제의 전체 10종목 중 세 쌍만 유효하면 상관 +1과 **coverage 30%**가 함께 나온다. 상관 하나로 결과를 요약하지 않는다.
- 표본 부족, 상수 신호, 상수 라벨에서는 상관이 정의되지 않으므로 nullable 값과 명시 상태를 반환한다. 상관 0으로 채우면 정보가 없는 단면과 실제 무상관을 혼동한다.

## 검증과 재실행

```sh
uv run --no-sync --project backend pytest \
  backend/tests/domain/test_signal_evaluation.py \
  backend/tests/integration/test_signal_evaluation_pipeline.py \
  backend/tests/integration/test_s02_accounting.py \
  backend/tests/architecture -q
```

2026-10-16 위 명령은 **43 passed, 0 skipped**다. S03 새 domain 20건 + 실제 pipeline 연결 1건, S02 Python/Rust 4건, architecture 18건이다. 새 S03 production·test의 ruff check와 pyright도 **오류 0**으로 최종 확인했다. 실데이터 평가·원천 DB 접근·실제 브로커 주문·가설 채택은 미실행이다.
