# V5-06: 금액별 비용과 팩터 회귀를 읽는 화면

## 의도와 경계

사용자가 소액 백테스트의 좋은 수치만 보고 운용 금액을 늘리거나, 알려진 팩터 노출을 새 알파로 오해하는 일을
줄인다. 기존 V4-04·V4-05 계산과 API를 읽는 UI 범위다. 새 통계·한계 금액 공식·실행 runner·원장은 만들지 않는다.
이 문서는 구현 계약과 기능 QA 근거다. 실제 수익성이나 PLAN 전체 완료를 선언하지 않는다.

디자인은 기존 [MASTER](../../../design-system/strategy-workbench/MASTER.md)의 결과/근거 위계와 토큰을 따른다.
색만으로 상태를 구별하지 않고, 표는 이름·키보드 초점이 있는 내부 가로 스크롤로 읽는다. 기존 전체 원시 지표는 유지한다.

## 용량 확인 — US-CS-11

- 직접 실행한 결과에서 금액 입력을 명시적으로 펼친다. `getBacktestRequest`의 원본 요청에서 초기 자본만 바꾸는
  기존 capacity preview/create API를 쓴다. 원본 요청을 읽지 못하면 시작하지 않으며 조회 사유와 복구 입구를 보인다.
- 입력을 바꾸면 앞 미리 계산으로 시작하지 않는다. 서버가 답한 N 전후·새 시도 수·실행 수를 먼저 읽고 명시 시작한다.
  기반 시도의 counted 조건과 허용 금액 개수·범위는 backend가 소유한다.
- 접수 후 기존 영속 실험 상세로 이동한다. 목록·진행·취소·재접속은 기존 실험 owner를 사용한다. 용량 결과는 같은
  experiment ID의 별도 query로 읽으며 페이지 재방문/재시작 시 조회할 수 있다.
- 금액별 비용 후 **세션** 샤프·참여율·충격 bp·반올림 오차·세션 미체결과 상태를 표로 보여준다. 시도한 금액 순서의
  곡선은 왼쪽부터 같은 순번의 금액 목록·표와 대응하고 같은 숫자를 그리며, 한계 금액을 보간하지 않는다. 한계·최고 금액·기준 샤프·부재 이유는 서버 값을 읽는다.
- 실패·취소·미완료·legacy 체결 기록 부재는 0이 아닌 값 없음으로 유지한다. 세션 미체결은 해당 세션 주문의 부족분이며
  최종 잔량이 아니다. 일반 trial 목록에서 개별 실행/실패도 계속 읽는다.

완료 기준은 preview/create의 원본 파라미터·환경 보존, 금액 변경 후 재확인, 서버 거절 설명, N·대표 샤프 불변,
숫자/부재/한계 사유와 좁은 화면의 표 읽기다. capacity 생성 API에 새 멱등성 계약을 추가하지 않는다.

## 팩터 회귀 — US-CS-12

- 결과 파일을 이미 읽은 실행에서 사용자가 조회를 시작한다. 원본 run ID·fingerprint와 다른 응답은 표시하지 않는다.
  query 신원은 실행과 fingerprint이며 다른 실행의 늦은 답은 새 화면에 들어오지 않는다.
- `computing` 응답일 때만 5초 간격으로 조회한다. 한 번의 명시 조회 예산은 최대 36개 응답이고 이후 자동 조회를 멈춘다.
  한도를 다 쓰면 자동 확인이 멈췄다는 안내와 명시 재조회 입구를 보인다. 명시 재조회는 새 예산을 연다.
  오류·관측 부족·특이 설계·데이터 판 불일치는 자동 반복하지 않는다.
- 404/409/503 등 조회 오류는 기존 실패 표시를 쓴다. 410이면 앞 결과를 숨기고 자동/명시 재조회 입구를 막는다.
  focus·재접속·재마운트가 조회 예산을 새로 만들지 않는다.
- 연율 알파와 t, 네 팩터 β·t·표준오차, R²·HAC lag·사용/전체 구간 수와 제외 사유를 읽는다. 원래 세션 알파·표준오차,
  연환산 거래일·데이터 판·레지스트리/평가 판본은 근거에서 펼친다. 알파 t 기준과 N은 조회 시점 계열의 응답 값이다.
- 화면에 기록하는 시각은 클라이언트가 응답을 받은 시각이다. 후보 선택 시점이나 원자적인 DB 스냅숏 시각으로 설명하지 않는다.
  결측·상장 종료·일별 비동시 거래와 회귀의 통계적 한계를 함께 표시한다.

선행 backend admission #546은 main에 머지되었고 후보→reports 스택에도 포함되었다. 서로 다른 키의 회귀 요청에 활성 작업 하나만 허용하고 대기 daemon/큐를 만들지 않는다. 따라서 `computing`은 **계산 준비 중이거나 진행 중**이며,
서버 슬롯이 비면 다음 제한 조회/명시 요청에서 접수될 수 있다. UI의 유한 poll이 backend admission 상한을 대신하지 않는다.
기존 팩터 수익률 LRU 32개·수식·N 정책은 변경하지 않는다.

## owner와 검증

- 계산: 기존 analytics/experiment domain, 요청·결과: 기존 backtest_run/experiment_run API.
- wire: 생성 SDK와 `shared/api` gateway. query cache: 해당 backtest/experiment entity.
  사용자 시작은 `features/check-capacity`, 결과 조합은 기존 backtest/experiment page다.
- 타깃 검사는 MSW로 실제 gateway 요청·응답·오류를 읽는다. 회귀 숫자 정상 fixture와 부재·판본/실행 불일치,
  computing 예산·명시 재개·410을 구분하고, 용량은 요청/N·legacy/null·진행/취소/실패 사유를 검증한다.
- `frontend/e2e/stories/cs.validation-reports.spec.ts`는 실제 mock 용량 실행/N·대표값 보존·영속 상세 재방문을 검증하도록 준비했다.
  회귀 기본 mock 3종목의 관측 부족과 별도 합성 숫자 응답의 화면 검증을 명시적으로 구분한다.
  ROOT 최종 mock 브라우저 2개가 56.8초에 통과했다(`browser-mock-final.log`). SVG 선/fill:none과 360px dark 화면을 ROOT가 직접 검토한 뒤 CS11/12를 구현됨-e2e로 등록했다.
  회귀 숫자 정상 fixture의 화면 검증은 실제 mock 관측 부족과 구분하며 실제 수익성 근거로 설명하지 않는다.
- `frontend/e2e/validation-reports/workbench.real-equity.spec.ts`는 기존 real-equity opt-in project에만 들어간다.
  ROOT의 최종 실자료 opt-in QA는 1개 통과(3.6분, `browser-real.log`)했다. 별도 runtime에서 짧은 실원천 용량 비용·N/대표값 보존·recheck3개 추가와 2020~2024 장기 회귀 원수치/판본·조회 전후 원장 불변을 실제 응답과 대조했다. ROOT가 `real-final`의 용량/회귀 PNG를 직접 검토했다.
  초기 세 실패는 `browser-real-fixture-failed.log`(monthly 구간 frame0), `browser-real-daily-enum-failed.log`(허용되지 않는 daily), `browser-real-ledger-contract-failed.log`(정상 recheck 추가를 거절한 잘못된 전체 동일 단언)에 보존했다. 최종 짧은 후보는 기존 허용 every_n_sessions/1, 장기 후보는 monthly다. 재확인 기록3개 추가는 정상이며 N·대표값·기존 기록은 보존되어야 한다. 회귀 조회는 원장 전체를 변경하지 않는 별도 읽기 검증이다.
  원문 JSON attachment는 최종 Playwright HTML report의 내장 ZIP에 실제 보존되어 있었다. 외부 `real-final`에 report HTML/ZIP과 `real-capacity-raw.json`·`real-regression-raw.json`을 추출 보존했으며 `raw-attachment-provenance.json`이 출처 entry와 SHA256을 기록한다.
  금액 두 개는 기존 최소3개 계약의 거절을 확인한 뒤 세 개로 실행한다. 900초 예산은 기존 실자료 QA와 같고,
  두 작업의 PNG와 원문 JSON을 분리한다. API 합성·통계 재계산·데이터 원장 쓰기는 없다.
- 실제 기능 QA는 [REAL-DATA-CHECKS](./REAL-DATA-CHECKS.md)에 따라 별도 runtime에서 2020-01-02 이후만 확인한다.
  서버·브라우저·실자료 QA와 전체 테스트/CI는 루트가 수행한다. 확인 전 수치나 통과를 적지 않는다.

키움·브로커·주문 전송, 봉인 구간 개봉, S01 보존본 갱신, 새로운 팩터/회귀 공식·수익성 판정은 제외한다.
