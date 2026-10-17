# V5-02 첫 수직 구현: 실행 결과의 PSR·DSR 검증 카드

## 목적과 경계

최종 목표는 비용 후 수익을 내는 자동매매다. 그 전에 S03의 1주·3개월 유망 종목 신호와 S04의
실제 계좌 성과를 구분해서 검증해야 한다. 이번 작업은 좋은 백테스트 숫자만 보고 자금을 투입하는
실수를 줄이기 위해, 저장된 PSR과 현재 계열의 시도 횟수를 반영한 DSR·근거·부재 이유를 읽게 한다.
코드 구현·mock 검증은 알파나 실자료 수익성 검증 완료를 뜻하지 않는다. 후속 스트레스 검증,
사전 등록, 실자료 판정은 별도 단계다.

이 문서는 V5-02 전체 완료 선언이 아니다. ‘튼튼한지 확인하기’ 실행, 워크포워드 곡선과 전용
US-CS-09 검증, AI 결과 설명의 검증 요약, 추적·미리보기의 parameter_values 연결은 남아 있다.
실제 원장·봉인·키움·외부 계좌에 접근하지 않는다. 기존 S01 보존본·receipt·증거는 갱신하지 않는다.
백엔드·SoT 변경으로 보존 코드/계약 지문이 달라질 수 있으므로 다음 실자료 연구에서는 별도 보존과
사전 검증이 필요하다.

## 읽기 전용 계약

`GET /api/v1/backtests/{run_id}/validation-report` (`getBacktestValidationReport`)는
`BacktestRunService.result`로 완료 상태·산출물 해시·역직렬화를 한 번 검증한 뒤, 검증된 manifest의
실행 입력·출처를 기존 `_lineage`로 해소하고 현재 원장을 읽는다. 은퇴한 리비전을 다시 실행 가능
여부로 해소하지 않는다. 기존 결과의 404/409/410 오류를 그대로 전달한다.
새 503 응답은 정의하지 않으며 이 조회는 데이터 공급자/실행 커널을 호출하지 않는다.

응답의 `run_id`, `run_fingerprint`, `metric_registry_version`은 불변 실행의 신원이다.
`observed_at`은 application의 주입된 시계로 기록한 원장 조회 시점이며 거래·완료·후보 선택 시각이 아니다.
조회는 원장 전체에 대한 새 DB 트랜잭션 스냅숏 보장을 추가하지 않는다. 새 시도나 계열 합치기 후
명시적으로 다시 조회하면 N·DSR이 달라질 수 있다. 저장된 결과·후보 선택 스냅숏은 바꾸지 않는다.

- `psr`: 저장된 FULL `probabilistic_sharpe` MetricValue를 그대로 전달한다. null이면 미기록이며,
  MetricValue의 value가 null이면 기존 unavailable_reason을 보존한다. 재계산하지 않는다.
- `dsr`: 기존 `domain/experiment` 계산의 값과 구조화된 사유, N·대표 자료 수·표본분산 V·운 기준선
  SR₀·실행의 세션 샤프/표준오차·연환산 거래일·계열 신원·대표 판본 집합·미상 판본 수를 제공한다.
  부재 사유를 먼저 판정하므로 계산하지 않은 V·SR₀·세션 샤프/표준오차는 null이다.
  원시 대표 행은 중복 전송하지 않으며 기존 계열 원장 API가 소유한다.
- 대표 판본 목록·미상 수는 분산에 들어가는 대표 샤프가 있는 시도 기준이다. `registry_mixed`는
  이 판본 집합과 해당 실행의 판본을 합쳐 알려진 판본이 둘 이상인지 나타낸다. 미상은 같다고 추측하지 않는다.

## 통계 정책 보존

기존 `run_deflated_sharpe(result, ledger)`의 signature와 scalar 결과를 유지한다. 값·근거·사유는
같은 순수 판정에서 나오며 분산·기준선·확률 공식은 한 곳만 소유한다. 표준오차/PSR은 기존 analytics
owner, DSR은 experiment owner다. HTTP·화면은 수식이나 부재 이유를 추론하지 않는다.

N=1이면 V=0·SR₀=0으로 현재 식의 PSR(기준 0)과 같은 DSR을 계산한다. 저장된 legacy PSR이
동일하거나 존재한다고 가정해 그 값을 대신 쓰지 않는다. N≥2인데 대표 샤프가 둘 미만이면 DSR이 없다.
계열 없음, 시도 없음, 실행 샤프/표준오차 없음, 표준오차 0 이하도 코드화된 사유로 구분한다.
인라인 초안도 유효한 lineage_strategy_id가 있으면 기존 계열 N과 DSR에 참여한다. 계열 없는 새 초안만
no_lineage다(spec D2). 파산한 시도는 N에 남고 대표 샤프가 없어 분산 자료에서 빠지는 기존 규칙을 유지한다.

## 검증 범위

합성 domain 경계·기존 통계 회귀, mock application/HTTP 완료 조회·재시작·오류 전파·현재 원장 변화와
불변 결과 보존을 검증한다. 실제 실행 기록이나 실자료 성과 검증을 대체하지 않는다.
root가 확인한 전체 backend는 4820 passed, 1 skipped(기존 live opt-in), 34 warnings, 약 199초다.
전체 frontend는 111 files, 1434 passed, 83.21초다. 정적 제목 최종 등록본 mock 브라우저 스토리 2건은 14.1초에 통과했다
(1440 light·360 dark): N=1→2, 옛 result 본문 불변, 503에서 마지막 확인 값 보존·복구,
410에서 옛 값 숨김·실제 offline 재접속 후 추가 요청 없음을 확인했다. root는 기본 카드·계산 근거
PNG 4장을 직접 검토해 행·버튼·숫자·긴 실행 지문·모바일 세로 읽기·가로 넘침을 확인했고,
독립 리뷰에서 확정 P1/P2는 없었다.

이 문서는 최종 등록본의 로컬 검증 기록이며 공식 CI·머지 상태는 해당 PR의 checks·merge 기록에서
확인한다. 위 결과는 실자료 검증 완료를 뜻하지 않는다.

## 스토리 추적

US-DM-10은 이번 통계 값·근거·조회 오류·키보드 흐름으로 한정한다. 기존 AI 검증 요약의 수용 기준은
US-DM-12로 보존하며 담당/e2e는 V5-02로 유지한다. 삭제나 완료 간주가 아니라 실제 독립 검증 가능한
과업으로 나눈 것이다. AI 연결 전에는 DM12가 예정이고, V5-02 전체·제품 PLAN도 미완료다.

## 통계 의미의 참고 문헌

[PSR 저자 원문](https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf)은 추정 샤프와 그 불확실성을
기준 샤프와 비교하는 확률 해석을, [DSR 저자 원문(2014)](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)은
여러 번 시도한 선택 효과를 고려하는 의미를 설명한다. 이는 통계 의미의 참고이며 이번 구현이
논문 정책 전부를 검증했다는 뜻이 아니다. 실제 수식·단위·부재 경계는 기존 SoT와 통계 owner가 소유한다.

### backend 실구현 확인

관련 domain/application/HTTP·원장/선택·architecture 회귀 묶음은 150 passed(9.50초, 기존 경고 1건)다.
이 150개는 위 전체 backend 4820개에 포함된 부분집합이다. 신규 테스트 수를 뜻하지 않으며
전체 수와 합산하지 않는다.
backend 및 루트 tools의 Ruff·Pyright와 diff 공백 검사를 통과했다. 격리한 mock 앱으로 생성한
OpenAPI 비교에서 기존 경로·기존 schema 변경은 0이고 validation-report 경로와 새 응답 타입만 추가됐다.
브라우저 근거는 위 로컬 검증 범위를 따르고 공식 CI·머지 상태는 해당 PR의 기록에서 확인한다.

## 둘째 slice: AI 결과 설명의 검증 근거(US-DM-12)

결과 설명 세션이 읽는 요약(`read_backtest_result`)에 `validation` 칸을 더했다. 검증 카드와 같은 근거를
시점별로 나눈다.

- `psr`: 저장된 FULL PSR 값·표본 수·부재 사유를 그대로 옮긴다.
- `current_ledger`: `validation_report`가 `observed_at`에 읽은 계열 원장 근거(DSR 값·사유·N·대표 자료 수·
  분산·SR₀·세션 샤프/표준오차·판본 섞임)다.
- `selection_snapshots`: 이 실행의 trial을 후보로 고를 때 굳힌 N·DSR·원장 대표 샤프와 선택 시각이다.
  실험 서비스의 `run_selections`가 마지막 attempt가 이 실행인 trial의 선택만 고른다.

근거를 못 읽으면 `validation`은 null이고, 프롬프트는 근거 없이 결과를 믿어도 된다고 말하지 않게 한다.
PSR을 미래 확률로, 지금 N을 선택 당시 판단으로 설명하지 않도록 프롬프트에 읽는 법을 더했다. 값은 기존
owner가 계산한 것을 옮기며 요약에서 다시 계산하지 않는다.

알려진 한계: 같은 run을 여러 실험이 이어 쓰면 소유 실험은 `BacktestRunSummary.experiment_id`(가장 앞선
소유자) 하나라 다른 실험의 선택은 싣지 않는다. 남은 V5-02: 스트레스/튼튼한지 확인하기 실행(US-DM-11),
워크포워드 곡선과 US-CS-09, 추적·미리보기 parameter_values. V5-02 전체는 계속 미완료다.
