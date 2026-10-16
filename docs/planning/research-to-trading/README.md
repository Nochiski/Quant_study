# 연구에서 실행으로: 구현 증거와 다음 결정

감당 가능한 위험 안에서 비용 후 수익을 내는 자동매매를 위해, **의도 → 구현 → 증거 → 다음 결정**을
연결한다. 입력 재현, 회계 정확성, 신호 예측력, 비용 후 성과, 외부 주문 안전은 각각 확인해야 한다.
현재 증거만으로 실데이터 수익성, 배당·상폐 회계 해결, 실브로커 연결 준비 완료를 주장하지 않는다.

기준 HEAD는 `1e780ce7`이다. 아래는 이 기준 이후 추가한 구현과 검증 범위를 연결한다.
테스트 수는 해당 검증 묶음의 실행 결과이며 서로 겹치는 회귀를 합산하지 않는다.

[실행 로드맵 HTML](../../auto-trading-roadmap.html)의 S01–S08 패킷은 향후 작업 지시를 담은 제안
원문이다. 이 README와 각 단계 스펙은 현재 구현·증거·남은 결정을 인계한다. 공식 제품 기능 상태와
의존성의 정본은 [기존 제품 로드맵](../../superpowers/specs/2026-09-03-strategy-workbench-roadmap.md)과
각 initiative PLAN(예: [Validation Lab PLAN](../validation-lab/PLAN.md))이며,
이 문서는 별도 완료율/PR 상태 원장을 만들지 않는다.

## S01 — 연구 입력 보존

- **의도:** 결과 차이가 데이터 갱신 때문인지 코드·전략 때문인지 구분할 재현 입력을 보존한다.
- **현재 구현:** 원장 current BuildRecord와 참조 파티션을 별도 root로 복사하고 코드·계약·환경 출처,
  해시·행 수, 정상 catalog를 보존한다. 공개 전 pending과 최종 검증을 거치고 실제 DuckDB adapter도
  pending root를 거절한다. 보존 CLI와 실제 adapter 재접속을 합성 원장으로 확인했다.
- **증거·아직 못 확인:** 신규 **28 passed**, 영향 회귀 **172 passed**. 실제 원장 root를 확보하지 못해
  실데이터 범위·복사 용량·품질·재실행 결과는 미검증이다. 계약 문서 원문 지문 `contract_sources_sha256`은 기존 워크벤치 필드 계약 ID와 다르다.
  adapter의 pending 차단은 완성 후 파일 해시 재검증까지 수행하지 않는다. 정확한 검증 범위와
  실패·GC·업데이트 사례는 [S01 스펙](S01-snapshot.md),
  [보존 테스트](../../../database/tests/test_research_snapshot.py),
  [adapter 통합 테스트](../../../backend/tests/test_research_snapshot_integration.py)에 있다.
- **실행 진입점:** `PYTHONPATH=database/src backend/.venv/bin/python -m equity.research_snapshot`
  의 `create`, `verify`, `open`. 기존 목적지를 덮어쓰지 않으며 검증한 `open` 결과로 기존
  `STRATEGY_WORKBENCH_EQUITY_ROOT`를 설정한다. 실행 전 `open --repo-root`로 입력·코드·계약을
  재검증하고 실패 시 중단한다. 완성 catalog가 절대경로를 사용하므로 root를 이동하지 않는다.
- **다음 결정:** 실제 로컬 원장을 확보하면 공개·재접속·무결성을 확인하고 S02 한계를 적용한 첫
  연구 입력으로 사용할지 결정한다. 원본 시점·품질이 부족하면 범위를 줄이거나 보류한다.

## S02 — 비용·수량·기업행동 회계 검산

- **의도:** 장부 오류나 회수되지 않은 자산을 전략 수익으로 해석하지 않도록 계산 경계를 확인한다.
- **현재 구현:** 합성 두 종목·다섯 세션에서 실제 선정 compiler → TargetTape → 실행 adapter →
  Python/Rust 커널을 손계산과 비교한다. 전량 목표와 부분체결, 분할, 종목 교체, 수수료·매도세,
  평가액·벤치마크 차이를 확인했다. production 회계 의미를 바꾸지 않았다.
- **증거·아직 못 확인:** 신규 **4 passed, 0 skipped**, 관련 회귀 **172 passed, 0 skipped**.
  [S02 스펙](S02-accounting.md)과 [검산 테스트](../../../backend/tests/integration/test_s02_accounting.py)에
  독립 기대값이 있다. 상장 종료 뒤 회수금 없이 마지막 평가가 남는 한계, 마지막 bar 이후 사건 제외,
  배당일 부재를 명시했다. 배당 포함 총수익·상폐 정산이 해결됐다는 증거가 아니다.
- **실행 진입점:** `backend/.venv/bin/python -m pytest backend/tests/integration/test_s02_accounting.py -q`.
  원천 팩터 평가와 미래 라벨을 검증한 테스트는 아니므로 S03 증거와 구분한다.
- **다음 결정:** 분할을 반영한 비용 후 가격 기준 연구로 제한할 수 있으면 S03을 진행한다. 배당
  총수익이나 상폐 영향의 크기가 결론에 필수라면 원천·정산 계약을 먼저 보완한다.

## S03 — 신호 평가의 최소 기반

- **의도:** 종목 순위를 맞히는 능력과 실제 매매의 비용 후 효과를 별도로 측정한다.
- **현재 구현:** `domain/signal_evaluation`의 순수 평가 함수가 기존 선정 전 관측을 소비하고 별도
  미래 분할조정 종가→종가 가격 라벨과 날짜별 Rank IC·커버리지·제외 사유를 만든다. 공통 거래 달력, 진입 지연,
  만기, 상장 구간·결측·동점·상수 단면을 명시하며 실제 raw observation → 기존 factor pipeline의
  출력을 합성 통합 테스트로 연결했다. 미래 라벨은 선정/목표 비중 입력에 넣지 않는다.
- **증거·아직 못 확인:** 신규 **21 passed, 0 skipped**(domain 20·통합 1), S03+S02+architecture
  묶음 **43 passed, 0 skipped**, 기존 pipeline을 포함한 관련 회귀 **182 passed, 0 skipped**.
  [S03 스펙](S03-signal-evaluation.md)에 라벨·분모·시점·검증 계약이 있다. 실데이터 첫 연구, 비용 후
  포트폴리오 비교, ICIR·quantile spread·turnover·decay의 완전 분석, 기존 제품 API/UI 노출은
  완료되지 않았다. 이 가격 라벨은 배당·비용·실제 다음 시가 체결을 반영하지 않는다.
  함수는 기존 봉인 guard를 사용하며 2020-01-02 이전 신호/평가 결과를 차단한다.
- **실행 진입점:** `strategy_workbench.domain.signal_evaluation.facade.evaluation.evaluate_signal`.
  [도메인 테스트](../../../backend/tests/domain/test_signal_evaluation.py)와
  [raw pipeline 통합 테스트](../../../backend/tests/integration/test_signal_evaluation_pipeline.py)는
  합성 입력으로 진입점을 검증한다. 기존 `FactorResearchService`의 catalog/validate/explain과
  `portfolioAnalytics`는 미래 라벨 기반 신호 평가를 대신하지 않는다.
- **다음 결정:** S01로 보존한 실제 입력과 S02 제한 범위 위에서 무튜닝 신호·유니버스·주 평가량·비용을
  결과 전에 동결하고 첫 연구를 수행할지 결정한다. 신호 예측력과 비용 후 성과가 모두 준비된 뒤
  진행·보류·기각을 기록한다. 좋은 horizon을 사후 선택했다면 그 선택도 남긴다.

## S04 — 반증·스트레스·선택 기록

- **의도:** 낙관적 체결이나 반복 선택에 기대는 결과를 걸러내고 근거보다 강한 확신을 막는다.
- **현재 구현:** 이번 작업 트리에서는 새 S04 기능에 착수하지 않았다. 기존 Validation Lab의
  trial·실험·워크포워드 기능은 각 PLAN/스펙의 기존 상태를 따른다.
- **아직 못 확인:** 첫 실제 후보의 비용·진입 지연·미체결 스트레스, 일반 평가 열람 이력, 계좌
  연속성의 추가 요구와 선택 기준은 미확정이다. 기존 기능 전체를 미완료로 돌리는 의미는 아니다.
- **실행 진입점:** 현재 새 실행 진입점은 없다. [HTML S04 패킷](../../auto-trading-roadmap.html#session-s04)과
  [Validation Lab PLAN](../validation-lab/PLAN.md)의 선행 조건을 확인한다.
- **다음 결정:** S03 실제 후보와 결과가 준비되면 소수 반증 시험·선택 기록의 범위를 먼저 동결한다.
  봉인 개봉이나 기존 trial key 의미 변경을 자동으로 수행하지 않는다.

## S05 — 영속 의도·가짜 브로커·대사

- **의도:** 응답 유실·재시작·중복 이벤트가 중복 주문이나 설명되지 않는 현금·수량으로 번지는 것을 막는다.
- **현재 구현:** 별도 실행 domain/application과 전용 SQLite 원장, 오프라인 fake broker를 추가했다.
  READY 저장 → UNKNOWN 원자 claim 커밋 뒤 한 번만 전송하고 ACK 유실/재시작은 조회로 대사한다.
  실제 보유·미체결·예약현금, 부분체결·취소·거절·중복/역순 사실을 구분하며 중지와 취소를 분리한다.
- **증거·아직 못 확인:** 신규 동작과 아키텍처 검증 **43 passed**. 저장 실패 zero-send,
  UNKNOWN 커밋 직후 실제 프로세스 종료, ACK 유실과 수동 잔고 변경을 확인했다.
  [S05 스펙](S05-offline-execution.md)과 [장애 테스트](../../../backend/tests/test_offline_execution.py)가
  근거다. 정수 KRW·목표 주식 수·완전한 누적 fake snapshot으로 제한한다. 목표 비중 sizing,
  실제 브로커 계약·외부 연결·Windows 잠금 실측은 미검증이다.
- **실행 진입점:** `OfflineExecutionService`에 명시 경로의 `ExecutionSqliteLedger`와 `FakeBroker`를
  주입한다. 스펙의 임시 DB 실행 예제는 직접 실행해 `submit_calls == 1`을 확인했다. 기존
  bootstrap/HTTP·compiler buffer·backtest 실행 경로에는 연결하지 않았다.
- **다음 결정:** offline 장애 기반을 검토한 뒤 S06의 공식 공급자 계약 확인 범위를 정한다. 이 증거는
  실브로커 준비 완료나 연결·주문 권한을 뜻하지 않는다. UNKNOWN 조회부재는 자동 재발주하지 않는다.

## S06 — 외부 모의 브로커 계약 확인

- **의도:** 로컬 상태 모델과 실제 주문·체결·조회·취소 사실의 차이를 확인한다.
- **현재 구현:** 미착수다. 이번 작업은 실제 URL·키·계좌·네트워크 주문을 추가하지 않았다.
- **아직 못 확인:** 공급자의 idempotency·식별·조회 완전성·호출 한도·재접속·누적/증분 체결 계약과
  모의/실전 환경 분리가 미확인이다. fake revision을 공급자 보장으로 사용하면 안 된다.
- **실행 진입점:** 새 실행 진입점은 없다. [S06 패킷](../../auto-trading-roadmap.html#session-s06)을 참조한다.
- **다음 결정:** 공식 계약 조사·안전 설계 후, 그 시점의 명시적 계좌 연결·주문 지시가 있는 범위에서만
  모의 연결과 주문을 수행한다.

## S07 — 전향 확인과 운영 관측

- **의도:** 동결 후보를 이후 자료에서 확인하고 연구와 운영 차이를 기록한다.
- **현재 구현:** 미착수다. 새 전향 평가·운영 알림·자동 판정은 없다.
- **아직 못 확인:** 동결 후보, 주 평가량·판정 시점, 전향 기간, 운영 관측 기준이 정해지지 않았다.
- **실행 진입점:** 새 실행 진입점은 없다. [S07 패킷](../../auto-trading-roadmap.html#session-s07)을 참조한다.
- **다음 결정:** S03/S04에서 동결한 후보와 운영 증거가 있을 때 평가 시점·중단 기준을 결과 전에 정한다.

## S08 — 자본 결정과 제한된 실거래 검증

- **의도:** 실제 비용·체결 차이를 감당 가능한 손실예산 안에서 학습하고 규모·유지·중단을 결정한다.
- **현재 구현:** 미착수다. 자본 배정·실계좌 주문은 수행하지 않았다.
- **아직 못 확인:** 사용자가 승인할 자본·손실예산·주문/노출 한도, 실현 비용과 복구 절차가 미정이다.
- **실행 진입점:** 새 실행 진입점은 없다. [S08 패킷](../../auto-trading-roadmap.html#session-s08)을 참조한다.
- **다음 결정:** 연구·모의·운영 증거를 검토한 뒤 사용자가 자본과 주문 범위를 별도로 결정한다.

## 검증 범위의 해석

2026-10-16 현재 작업 트리에서 확인한 결과다. Rust 확장을 준비한 backend 환경을 사용했다.
아래 묶음은 서로 겹치므로 pass 수를 합산하지 않는다. 특히 Rust parity는 backend 전체 검증에
포함된 별도 재실행이며 신규 테스트 수가 아니다.

| 검증 | 결과 | 범위 |
|---|---|---|
| backend 전체 | **4,583 passed, 1 skipped, 34 warnings** (163.41초) | 기존 기능과 새 합성 검증의 전체 회귀 |
| database 전체 | **1,437 passed** (162.62초) | 실제 원장 품질 인증이 아닌 코드·합성 fixture 동작 |
| Rust parity 별도 묶음 | **838 passed** | backend 전체와 중복되는 재실행 |
| backend 전체 ruff·pyright | **오류 0** | `src tests examples scripts tools` 범위 |
| root `tools` ruff·pyright | **오류 0** | 루트 도구 정적 검사 |
| 필드 대응표 검사 | **fields=40, map=46, missing=0** | 팩터 레지스트리 요구 필드의 대응표 coverage |
| root `tools` unittest | **43건 중 42 통과, 1 실패** | 기존 `test_plan_progress`의 PowerShell 설치 환경 assertion |
| 병합 충돌 표식 검사 | **통과** | `quant_study_dev.conflict_markers` |


backend 전체의 skip 1건은 외부 공급자 live smoke의 opt-in 실행 조건을 켜지 않아 발생했다.
외부 연결이 통과한 것으로 세지 않는다. 위 테스트의 정확한 실행 명령은 저장소 루트 기준이다.

```sh
uv run --no-sync --project backend pytest backend/tests -q
uv run --no-sync --project backend pytest database/tests -q
uv run --no-sync --project backend pytest backend/tests/test_core_parity.py -q
```

루트 도구의 unittest는 PowerShell이 없는 이 환경에서 기존 `test_plan_progress` 1건이 실패했다.
해당 파일만 아래 명령으로 격리 재실행해 같은 환경 실패를 확인했고, 그 파일은 수정하지 않았다.
이는 통과나 skip으로 바꾸어 세지 않으며 **전체 CI 통과를 주장하지 않는다**.

```sh
uv run --locked python -m unittest discover -s tools/tests -p 'test_*.py'
uv run --locked python -m unittest discover -s tools/tests -p 'test_plan_progress.py'
uv run --locked python -m quant_study_dev.conflict_markers
```

실데이터 수익률·IC를 산출하거나 실제 계좌를 연결하거나 봉인 구간을 개봉하지 않았다.
Validation Lab PLAN의 MERGED 표시·PR 수·의존성은 이번 인계에서 바꾸지 않는다.
