# S03 후속: 조정 공백을 지나는 개별 미래 가격 라벨

## 목적

원장이 MASKED로 가린 조정 공백을 사이에 둔 가격 비교는 계속 금지하면서, 공백과 무관한
종목·신호일·horizon은 같은 모집단 안에서 평가한다. 전체 요청 중단 때문에 관측할 수 없던
coverage와 제외 사유를 보존하는 변경이다. 원장 조정 규칙이나 신호 계산을 바꾸지 않는다.

## 질문

1. 각 라벨의 진입일부터 만기일까지 해당 종목의 조정 공백이 있는가?
2. 공백이 있으면 수익을 만들지 않고 명시 상태로 남기며, 없는 다른 라벨은 평가하는가?
3. 미래 공백 정보가 신호·선정·TargetTape 입력에 섞이지 않는가?
4. 미만기·상폐·상장근거 부재의 기존 의미와 당시 모집단 분모를 유지하는가?

## 최소 계약과 책임

- `domain/signal_evaluation`은 `AdjustmentGapObservation(session, security_id)`와
  `PriceLabelStatus.ADJUSTMENT_GAP = 'adjustment_gap'`을 소유한다.
  `evaluate_signal`의 선택 입력 `adjustment_gaps`는 기본 빈 tuple로 기존 호출을 지원한다.
- `SignalLabelData.adjustment_gaps`는 label port로 전달할 공백 tuple이다. DuckDB adapter는
  기존 `price.adj_close` 조회의 `CellKind.MASKED` 키만 옮긴다. 원장 `v_adj_close`나
  `v_unfolded_event`의 MASKED 술어를 SQL·Python에 다시 적지 않는다.
- application은 고정된 기존 pipeline 결과와 별도로 얻은 가격·공백을 순수 evaluator에 전달한다.
  CLI JSON의 label data에도 공백 근거를 남겨 개별 상태를 재현할 수 있게 한다.
- evaluator는 **같은 종목**의 공백이 닫힌 구간 `[entry_session, label_end]`에 하나라도 있으면
  `adjustment_gap`, `return_fraction=None`으로 반환한다. 진입·만기 당일도 포함한다.
  다른 종목이나 구간 밖의 공백은 해당 라벨을 막지 않는다.
- 판정 순서는 기존 달력 부족 → 미만기 → 상장근거/종료/상장 전 → 조정 공백 →
  진입·만기 가격 결측 → 가격 공개시점이다. 미만기 라벨에서 미래 공백 상태를 먼저 노출하지 않는다.
- 공백 관측은 중복 `(session, security_id)`와 공통 달력 밖 날짜를 거절한다. 가격 관측과 같은
  키에 공백이 있어도 공백 상태가 우선한다. 공백을 0·전일가·원주가로 채우지 않는다.
- `universe_count`와 원시 표본은 그대로 유지한다. `label_status_counts`에 공백을 세고,
  신호가 유효한 경우 `exclusions`에 공백을 센다. 신호 자체가 불가하면 기존 신호 사유 우선
  규칙을 지킨다. `paired_count + sum(exclusions) = universe_count`를 유지한다.
- 미래 가격·공백은 라벨 전용이다. factor graph·신호·선정·TargetTape로 되돌려 주거나
  공백 종목을 사후 유니버스에서 삭제하지 않는다. 정지 기준가와 무효 bar의 기존 제외도 유지한다.

기존 snapshot ID·연구 하한·공개시점·보존 검증·실행 코드 출처 계약은
[순수 평가 계약](S03-signal-evaluation.md)과 [runner 계약](S03-runner.md)을 따른다.
소스·계약 문서를 바꾸면 기존 S01 보존본의 코드/계약 검증 실패는 정상이다. 과거 보존본과
그 receipt를 고치지 않고, 새 코드가 동결된 후 별도 새 root를 생성·검증한다. 공개 문서에는
실자료 값·로컬 보존 경로·서버·계정 정보를 옮기지 않는다.

## 증거와 미완료 범위

이번 변경의 검증 대상은 경계 당일·중간 공백, 공백 밖 horizon·다른 종목의 정상 평가,
미만기·상폐·상장근거 부재의 우선순위, 분모·coverage·상태 카운트 보존, CLI의 공백 근거
직렬화다. 검증 owner는 기존 domain·application·runner 테스트다. 기존 전체 요청
`unsupported` 검증 기록은 당시 구현의 증거다.

2026-10-16 개별 공백 변경을 포함한 전체 검증 결과는 backend **4,657 passed,
1 skipped, 34 warnings**, database **1,437 passed**다. backend 전체 ruff·pyright와 root tools의
lint·type 검사·story harness도 통과했다. 두 테스트 묶음은 각각 전체 회귀이며 개별 테스트 수와
합산하지 않는다. backend skip은 기존 실공급자 live smoke의 opt-in 미설정이며, 이 결과는 코드와
합성 동작의 검증이지 외부 공급자 연결·실자료 IC·실성과의 증거가 아니다.

개별 정보창 판정은 배당 총수익·상폐 회수금·비용·실제 다음 시가 체결 문제를 해결하지 않는다.
이 문서를 작성한 시점에 첫 실자료 IC·수익·성과·통계 추론은 아직 확인하지 않았다.

## 다음 결정: 첫 실자료 기술 확인 초안

목적은 보존 입력 → 기존 신호 → 개별 미래 라벨 → JSON 경로와 coverage를 한 번 서술하는 것이다.
기존 registry의 `price.momentum_12_1` 하나를 기본 파라미터 그대로 사용한다. 이 팩터의 기존
252세션·21세션 skip·횡단면 순위 계산을 복제하거나 최적화하지 않는다.

신호 창은 `RunEnvironment.start=2024-01-02`, `end=2024-01-08`의 다섯 거래일이며,
`krx.common-stock`의 **각 신호일 전체 당시 집단**을 모집단으로 사용한다. 전략의 해당
factor 방향은 `FactorDirection.HIGH`다. 신호의 부호·파라미터·기존 순위 그래프를 바꾸지 않는다.
업종은 PIT 근거가 없고 consumer에 노출하지 않으므로 기존 graph에 업종 중립화를 넣지 않는다.
전략 template의 `risk.max_sector_weight=1.0`으로 업종 cap을 비활성화하고 다른 risk 기본값은
유지한다. 현재 업종값이 신호·선정에 영향을 주지 않도록 이 설정도 전체 전략 JSON에 명시한다.
라벨은 5/21/63세션을 동시에 관찰하고 `entry_delay_sessions=1`, `minimum_pairs=3`을 쓴다.
`evaluation_as_of=2024-06-28`, `label_calendar_through=2024-06-28`로 모든 라벨에 충분한
만기 창을 둔다. 기존 273세션 warmup은 2022~2023 자료 범위이며 봉인 구간을 열지 않는다.
전체 StrategySpec·RunEnvironment와 위 인자는 첫 결과를 보기 전에 로컬 요청 JSON과
그 hash에 고정하고, 연구 ID·실행 시각·반복 열람 기록·새 S01 receipt·결과 경로를 함께 남긴다.
공개 문서의 사양만으로 로컬 요청 JSON·hash 기록까지 완료됐다고 주장하지 않는다.
환경 비용 설정도 실행 요청 전체 JSON에 기록한다. 이번 라벨 기술 진단은 비용 후 성과를
계산하지 않으며 해당 비용으로 어떤 계좌 효과도 해석하지 않는다.

한 번의 실행에서 모든 신호일·horizon의 원래 분모, 쌍 수, coverage, 상태별 건수와 IC의
계산 가능 여부를 함께 서술한다. 좋은 horizon을 사후 선택하거나 alpha 채택·유의성·수익성을
판정하지 않는다. 기술 확인에 필요 없는 경제 임계값이나 별도 승인 gate를 추가하지 않는다.
지원 범위 밖 입력이면 근거를 기록하고 중단하며 원주가·mock으로 우회하지 않는다.

실자료 실행은 아직 하지 않았다. 새 S01 검증과 결과 전 로컬 명세를 완료한
뒤 1회 기술 확인의 관측 결과로 후속 연구 범위와 남은 데이터 보완을 결정한다.
