# tape 열 지향 계산 기획 패키지

백테스트 run 시간의 대부분은 tape 단계가 칸마다 Python 객체를 만들고 옮기는 데 쓴다. 이 패키지는 그 단계를 numpy 열 계산으로 바꾸는
계획을 담는다. 모든 값은 비트 단위로 보존해 판본·해시·지문을 하나도 바꾸지 않는다. 범위는 제품 전체(모든 연산자·컴파일러·trace·미리보기·
신호 연구·연속 계좌·factor-regression 입력)다.

## 문서 구성

- [설계 spec](../../superpowers/specs/2026-10-20-columnar-tape-design.md): 결함 2건, 결정 D1~D9, 대조 하네스, 검증 기준 C-1~C-6. 계약 SoT
- [SEMANTICS.md](./SEMANTICS.md): 보존할 의미 목록(file:line)
- [WORKFLOW.md](./WORKFLOW.md): PR scope packet과 공통 gate, run worker 트랙과 맞물리는 지점
- [PLAN.md](./PLAN.md): PR 상태의 단일 진행 추적 파일
- 측정 근거: [run-worker-process BENCHMARK](../run-worker-process/BENCHMARK.md) 4~5절

## 한 줄 요약

시제품 기준 6.7년 tape가 247초에서 약 3.4초가 되고 80/80 프레임 선정이 같다. 평균·표준편차는 인증 커널로 `statistics` 모듈과 비트 동일을 맞춘다.

## 기준

- 실행 절차는 [YAML Strategy Workbench WORKFLOW 12~14절](../strategy-workbench-yaml-ui/WORKFLOW.md)을 따른다.
- 리뷰 블로커가 없고 CI 전체가 green이면 main에 머지한다.
- 성능 주장은 같은 절차로 다시 잰 숫자로만 한다. 홀드아웃 규칙: 실데이터 구간 시작은 2020-01-02 이후다.
