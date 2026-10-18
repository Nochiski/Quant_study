# V6-01 일부: 후보·기준 사전등록 준비와 합성 모의 검증

2026-10-18 리드가 승인한 계약이다. 동결 등록·모의 claim·기준 비교·SQLite 영속 core를 구현했으며,
테스트가 제공하는 합성 executor로만 조립한다. HTTP·bootstrap·실제 원천 연결·개봉 화면은 범위 밖이다.
사용자에게 의미 있는 목표는 **후보와 판정 기준을 결과 보기 전에 고정할 준비**다. 합성 모의값으로 실제 후보를
통과시켰다고 표시하지 않는다. 이 범위는 V6-01 전체·V6-02 화면·실제 봉인 개봉·실자료 성과 검증의 완료가 아니다.

정본은 [설계 D11](../../superpowers/specs/2026-09-29-validation-lab-design.md),
[WORKFLOW](./WORKFLOW.md), [홀드아웃 규칙](../../../.claude/rules/holdout-window.md)이다.
이 문서의 전역 권위·불확실 소모·모의 namespace·기준 결합 방식은 기존 D11을 구현하기 위해 명시하는 신규 정책이다.
기존 실행의 연구 하한이나 실제 개봉 권한을 변경하는 예외가 아니다.

## 1. 봉인창의 권위와 모의 범위

- 실제 권위의 식별 단위는 domain의 `SEALED_START`·`SEALED_END`로 식별하는 전역 KRX 봉인창이다.
  계열·전략·리비전·데이터 snapshot은 관측 출처이지 새 미사용 창을 만드는 key가 아니다.
  한 번 관측한 창은 다른 계열·snapshot·새 DB로 바꿔도 미사용으로 되돌아가지 않는다.
- 현재 앱 DB에 개봉 행이 없다는 사실만으로 실제 창을 `unused`라고 판정하지 않는다. 실제 연구 권위와
  대조하기 전에는 실제 권한이 없으며 상태는 미확인/차단이다. 앱 밖의 연구 권위·실행 전 확인 절차는 기존 규칙을 따른다.
- 이번 core는 명시적인 `simulation` namespace와 임시 합성 DB에서만 사용한다. 모의 DB도 namespace 안에서는
  계열·snapshot과 무관한 하나의 창 claim을 지키지만, 그 상태를 실제 봉인 권위로 승격하거나 합치지 않는다.
  새 임시 DB를 만든 것은 새 모의 시험일 뿐 실제 창의 초기화 근거가 아니다.
- 일반 실행·미리보기·추적·팩터 경로의 `require_research_window`와 원천 관문은 그대로 둔다.
  production data/engine adapter를 연결하거나 실제 봉인 관측을 요청하는 경로는 추가하지 않는다.

## 2. 동결 등록본

등록은 입력을 검증한 뒤 불변 기록으로 남긴다. 이후 읽기는 현재 전략이나 현재 선택으로 조용히 재해소하지 않는다.

- 저장 전략의 정확한 리비전·spec/source 신원, 기존 domain owner로 정규화한 전체 파라미터 값.
- 실행 설정과 기존 `environment_hash`, 고정 봉인창 식별자, 연결한 선택 기록의 정확한 내용과 canonical hash.
  선택 기록의 전략·리비전·파라미터가 등록 후보와 다르면 거절한다.
- 판정 기준의 metric ID·scope·단위·비교 방향·유한 threshold와 기준 schema 판본.
  판정에 쓰는 metric registry/evaluator·입력 데이터의 provenance도 함께 보존한다.
- 등록 시각·등록 신원·출처를 보존한다. 등록 시각이 과거에 실제로 그 후보를 선택했거나 데이터를 보지 않았다는
  증명은 아니다. 기존 선택 기록의 시각도 그런 증명으로 바꾸어 해석하지 않는다.
- 기존 canonical/hash owner를 재사용한다. 이 등록의 결합 hash는 새 등록 owner가 소유하며 기존 trial key를 대체하지 않는다.
  기준이나 후보를 바꾸려면 새 등록을 만든다. 이전 등록을 덮어쓰거나 소모한 창을 돌려주지 않는다.

## 3. claim·멱등성·복구

등록 상태와 창 소모 상태를 분리한다. 등록만으로 모의 실행이 시작되지는 않는다.

1. 등록: `registered`인 불변 후보·기준을 보존한다.
2. 명시적 모의 실행 요청: namespace의 고정 창을 원자적으로 claim하고 **commit 성공 뒤에만** fake executor를 호출한다.
3. 관측 완료: 모의 관측 또는 관측 불가를 저장한다. claim 이후에는 어떤 종결 경로도 미사용으로 되돌리지 않는다.

- client request UUID는 durable unique key다. 같은 key·같은 등록의 반복은 같은 상태/결과를 읽고 재계산하지 않는다.
  같은 key를 다른 등록에 쓰면 충돌이다. 다른 key·다른 계열·다른 snapshot도 이미 claim한 창을 다시 열 수 없다.
- DB의 원자적 transaction/unique 제약으로 동시 claim을 하나로 만든다. 프로세스 안의 lock만으로 보장하지 않는다.
- claim 이후 예외·취소·프로세스 중단·응답 유실은 소모 상태를 유지한다. 관측 여부를 확정할 수 없으면
  `interrupted_unknown`으로 보존한다. 재시작·재조회·동일 요청 replay가 executor를 다시 호출하지 않는다.
- SQLite의 정상 ACID·unique 제약과 정상 저장소를 전제로 한다. claim commit이 성공을 반환한 뒤에만 executor를
  호출한다. commit 예외인 그 호출에서는 executor가 0회이며 자동 재시도도 없다. 해당 service는 `storage_unknown`으로
  차단하고 수동 복구 조회를 운영 경계로 둔다. SQL과 별개의 파일 fence나 두 번째 claim 권위는 만들지 않는다.
- 재시작 때 DB에 claim이 있으면 `interrupted_unknown`인 소모 상태로 보며 재실행하지 않는다. 정상 DB에 claim이
  없는 경우와 claim이 남은 경우를 구별한다. 직전 commit 예외 호출에서 executor를 호출하지 않았다는 제어 흐름은
  보장하지만, DB 행 부재 자체를 실제 봉인창의 미사용 증거로 삼지는 않는다.
- 임의 DB 삭제·백업 복원·변조·새 DB 생성까지 simulation core가 완전히 방어한다고 주장하지 않는다.
  이런 상황의 실제 봉인 권위는 계속 미확인/차단이며 별도 연구 권위 확인이 필요하다.
- 결과 파일/행을 잃거나 읽을 수 없어도 창은 소모된 채 관측 불가로 남는다. 삭제·수동 초기화·새 snapshot으로
  다시 개봉하는 복구 기능은 만들지 않는다.

## 4. 모의 관측과 기준 판정

- fake executor가 반환하는 것은 명시적인 모의 관측이다. 실제 후보 run/result 신원으로 위장하거나 실제 결과에 쓰지 않는다.
  응답/기록은 `simulation` 범위와 모의 판정을 구분하며 실제 개봉 허용이나 실제 후보 통과를 뜻하지 않는다.
- 기준 비교는 새 순수 domain 정책이다. 등록한 필수 기준 전부가 계산 가능하고 충족되어야 모의 충족 판정이다.
  필수 지표가 없거나 null·표본 부족·legacy 부재이면 전체는 `UNAVAILABLE`이다. 관측이 없는 경우를 통과로 채우지 않는다.
  전부 계산 가능하나 일부 기준을 충족하지 못하면 모의 미충족이다. 항목별 근거도 보존한다.
- 창 소모 여부와 기준 충족 여부는 별개다. 모의 미충족·관측 불가도 창을 소모한다.
  통계 계산·표본/단위 해석은 기존 analytics owner를 재사용하며 새 수익성 점수나 자동매매 승인 기준을 만들지 않는다.

## 5. 책임 경계와 기존 기록 보존

- domain/backtest가 동결 등록·소모 전이·기준 비교의 순수 정책을 소유한다.
  application/holdout_registration은 자체 frozen-source·simulation-executor·sealed-window-ledger 포트로 제어한다.
  다른 application 서비스나 concrete adapter를 직접 import하지 않는다.
- 기존 research SQLite owner가 신규 등록·claim·감사 기록의 schema와 transaction을 맡고 공용 SQLite 기반을 재사용한다.
  schema v9 migration·payload 무결성·불변 등록·전역 창 unique를 검증한다. 기본 DB를 여는 bootstrap 연결은 없다.
- 기존 `sealed_window_blocks`와 `record_blocked_attempt`는 거절한 실제 백테스트 요청의 정본으로 유지한다.
  모의 개봉을 fake blocked-attempt로 적지 않는다. 같은 기록 owner의 별도 개봉 감사 기록으로 보존한다.
- 모의 실행은 일반 `BacktestRunService.start`나 `record_trial_result`를 호출하지 않는다. 따라서 기존 trial/N·대표 샤프와
  선택 스냅숏을 바꾸지 않는다. 나중에 계열별 감사 표시가 필요하면 개봉 원장을 조회로 투영하고 trial에 중복 기록하지 않는다.

## 6. 이번 구현의 검증 종료선

공개 실제 개봉 route 없이 **core fake 조립 → 영속 기록 → 재시작 후 조회/replay**까지만 우선 검증한다.
실제 앱의 등록 노출·V6-02 화면은 V5 마무리 뒤 다시 판단한다.

- 임시 연구 DB에 저장 리비전·선택 fixture를 만들고 실제 registration application과 SQLite adapter를 관통한다.
- fake executor 한 번의 호출, 동시 두 claim 중 하나, 같은 UUID replay, 다른 등록/계열/snapshot의 중복 claim 거절.
- claim 직후 중단·관측 예외·응답 유실·재시작·결과 손실에서 소모 유지와 자동 재실행 없음.
  commit 예외 호출에서는 executor 0회·자동 재시도 0회·service 차단을 확인하고, 재시작 시 claim 존재/부재를 구분한다.
- 해소된 파라미터·선택 hash·기준 변조/불일치 거절, 지표 부재의 `UNAVAILABLE`, 기존 blocked/N/선택 기록 불변.
- normal bootstrap에 실제 개봉 route/executor가 생기지 않았고 기존 연구 하한 거절이 유지됨을 확인한다.

위 검사 통과는 사전등록 절차의 합성 검증 근거다. 실제 봉인 구간의 관측·성과·일회성 검증 완료라는 의미로
PLAN이나 사용자 화면에 기록하지 않는다. 실제 원장 열람, 키움/브로커 연결, 실제 개봉 승인과 실자료 성과 판정은 제외한다.
