# run 별도 프로세스 실행 기획 패키지

백테스트 run이 API 서버 프로세스의 스레드에서 돌면서 GIL을 쥐는 바람에, 실행 중에는 화면 요청이 느려지고, 동시 실행이 오히려
직렬보다 느려지고, 일부 단계는 취소가 안 되고, 메모리가 서버에 남는다. 이 패키지는 run을 run마다 뜨는 worker 프로세스로 옮기는
계획을 담는다.

## 문서 구성

- [설계 spec](../../superpowers/specs/2026-10-19-run-worker-process-design.md): 결함 4건, 결정 D1~D11, 검증 기준
  V-1~V-7, non-goal, 롤백. 이 initiative의 계약 SoT다
- [BENCHMARK.md](./BENCHMARK.md): 측정 절차, 원 숫자, 해석
- [WORKFLOW.md](./WORKFLOW.md): PR scope packet과 공통 gate
- [PLAN.md](./PLAN.md): PR 상태의 단일 진행 추적 파일

## 한 줄 요약

run 하나의 계산 시간은 바뀌지 않는다. 바뀌는 것은 다음 네 가지다.

- run이 도는 동안의 API 응답: p95 721ms에서 시제품 2.4ms로
- 동시 실행 처리량: run 2개에 83초 걸리던 것이 44초로
- 취소 지연: 일부 단계는 끝날 때까지 기다리던 것이 2초 이하로
- 서버 메모리 잔류

run 하나를 빠르게 하는 일은 tape 열 지향화라는 별도 initiative다. 시제품 기준 6.7년 tape가 247초에서 약 3.4초가 된다(BENCHMARK 5절).

## 기준

- 실행 절차는 [YAML Strategy Workbench WORKFLOW 12~14절](../strategy-workbench-yaml-ui/WORKFLOW.md)을 따른다.
- 리뷰 블로커가 없고 CI 전체가 green이면 main에 머지한다.
- 성능 주장은 BENCHMARK 절차로 다시 잰 숫자로만 한다.
- 홀드아웃 규칙: 측정 구간 시작은 2020-01-02 이후다.
