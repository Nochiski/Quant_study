# 연구에서 실행으로: 구현 증거와 다음 결정

감당 가능한 위험 안에서 비용 후 수익을 내는 자동매매를 위해, **의도 → 구현 → 증거 → 다음 결정**을
연결한다. 입력 재현, 회계 정확성, 신호 예측력, 비용 후 성과, 외부 주문 안전은 각각 확인해야 한다.
현재 증거만으로 실데이터 수익성, 배당·상폐 회계 해결, 실브로커 연결 준비 완료를 주장하지 않는다.

코드 감사 기준은 `1e780ce7`이며, **1차 기반 `8c3c1803` 검증**과 이후 작업의 검증 시점을 구분한다.
S04 계좌 경계 안내·macOS 공용 PLAN 집계·S03 보존 입력 연구 runner가 후속으로 구현됐다.
S03 runner는 합성 원장 검증에 이어 고정한 작은 실자료 창의 기술 확인을 1회 수행했다.
IC·coverage·상태를 계산·내보냈으며 효능·유의성·비용 후 성과 검증은 별도다.
2026-10-16에는 equity 30표 다운로드와 manifest·파일·해시 검증, adapter metadata 읽기를
확인한 뒤 별도 S01 사본의 create·verify/open과 adapter 재접속도 확인했다(원장 판
`2164ba776cbd99f2`). 당시에는 실자료 IC를 산출하지 않았다. 이후 새 코드·별도 사본의
[첫 기술 확인 증거](S03-adjustment-gaps.md#첫-실자료-기술-확인-증거)를 기록했다. 입력 수신과 보존 무결성
확인을 IC·수익성·데이터 품질 전체 검증으로 확대하지 않는다. 후속 읽기 전용 QC도 완료했으며
[S01 보존 당시 판정과 남은 제약](S01-readiness.md)을 별도로 기록했다.
아래 테스트 수는 해당 검증 묶음의 실행 결과이며 서로 겹치는 회귀를 합산하지 않는다.
S03 runner·S04를 포함한 후속 전체 회귀는 backend 4,610 passed·database 1,437 passed이며,
아래에 1차와 구분해 기록한다. 이 로컬 검증을 전체 GitHub CI 통과로 표현하지 않는다.
[PR #522](https://github.com/Nochiski/Quant_study/pull/522)·
[PR #523](https://github.com/Nochiski/Quant_study/pull/523)·
[PR #524](https://github.com/Nochiski/Quant_study/pull/524)는 검토 근거 링크이며 동적 PR 상태는 링크에서
확인한다. 이 문서에 별도의 PR 상태 원장을 만들지 않는다.

[실행 로드맵 HTML](../../auto-trading-roadmap.html)의 S01–S08 패킷은 향후 작업 지시를 담은 제안
원문이다. 이 README와 각 단계 스펙은 현재 구현·증거·남은 결정을 인계한다. 공식 제품 기능 상태와
의존성의 정본은 [기존 제품 로드맵](../../superpowers/specs/2026-09-03-strategy-workbench-roadmap.md)과
각 initiative PLAN(예: [Validation Lab PLAN](../validation-lab/PLAN.md))이며,
이 문서는 별도 완료율/PR 상태 원장을 만들지 않는다.

## 화면 검토 및 개선

- **의도:** 자동매매 수익을 판단할 때 지표의 평가 기준과 초기자본 대비 계좌 손익을 구분하고,
  조회 실패를 정보가 없는 상태로 오해하지 않게 한다. 로드맵에서 작업의 목적과 현재 증거를
  먼저 찾도록 읽기 순서와 탐색을 보완했다.
- **변경 범위:** 실행 로드맵에 고정 목차·현재 위치·패킷 바로가기와 구현 인계 연결을 추가했다.
  S01–S08 제안 패킷과 복사 프롬프트 원문은 보존했다. 실험 화면은 trial·워크포워드·감사 기록을
  구분하고 서버의 계좌 해석 안내를 성과 숫자보다 먼저 보여준다. 결과·감사 조회 실패에는 안내와
  재시도를 제공한다. 백테스트 결과에는 원본 지표·거래·재현성 정보 목차와 초점 이동을 추가하고,
  좁은 화면에서 표를 키보드로 스크롤하며 긴 숫자·식별자를 잘림 없이 읽도록 했다. 총수익률 설명은
  전체 구간의 첫 snapshot 자산 기준이며 초기자본 대비 손익과 다를 수 있음을 밝힌다. 공통 메뉴는
  낮은 화면에서도 스크롤·접기 후 초점을 유지하고, 입력 글자색은 기존 semantic token을 사용한다.
  backend 수치·계산·계좌 연결 의미는 변경하지 않았다.
- **검증 범위:** 이번 화면 변경의 frontend 전체 단위·통합 테스트는 **105파일·1,350테스트 통과**,
  lint·typecheck·build도 통과했다. 스토리 하네스는 **40개 스토리·48개 e2e 태그**를 확인했다.
  통합 브라우저 e2e는 결과 탐색(US-DM-04)·실험 흐름(US-SM-14·15)·낮은 화면의 메뉴 탐색을 확인한
  **3개 시나리오가 통과**했고, 감사 기록 표의 키보드 탐색을 보강한 SM 흐름의 증분 재검증도
  **1건 통과**했다. CI·PR 결과는 PR의 검증 기록을 따르며 로컬 통과를 전체 GitHub CI 통과로
  해석하지 않는다.
  이 결과는 실제 자료의 수익성 평가가 아니며 기존 S01·S03 증거의 범위를 넓히지 않는다.
- **후속 구현과 다음 범위:** 아래 S04에서 완료된 단일 run의 초기자본 기준 계좌 보고서를 별도
  조회로 구현했다. 위 화면 개선 검증은 그 이전 시점의 증거이며 새 보고서 검증과 합산하지 않는다.
  후속으로 창별 선택·현금/보유 이월을 연결하는 별도 연속 계좌 평가를 mock 자료에 한정해 제품에
  연결했다. 현재 범위와 한계는 아래 [S04 제품 연결 후속](#s04-제품-연결-후속--혼합-후보-단일-계좌)을
  따르며, 실자료 수익성 검증은 별도다.

## S01 — 연구 입력 보존

- **의도:** 결과 차이가 데이터 갱신 때문인지 코드·전략 때문인지 구분할 재현 입력을 보존한다.
- **현재 구현:** 원장 current BuildRecord와 참조 파티션을 별도 root로 복사하고 코드·계약·환경 출처,
  해시·행 수, 정상 catalog를 보존한다. 공개 전 pending과 최종 검증을 거치고 실제 DuckDB adapter도
  pending root를 거절한다. 보존 CLI와 실제 adapter 재접속을 합성 원장으로 확인했다.
- **증거·아직 못 확인:** 당시 신규 **28 passed**, 영향 회귀 **172 passed**. 당시에는 실제 원장
  root를 확보하지 못했다. 이후 실원장 별도 사본의 create·verify/open, 원본 MANIFEST 전후 동일,
  복사 inode 독립, pending 해제, adapter snapshot/필드 계약 동일·catalog 원본 참조 없음을 확인했다.
  독립 `verify --repo-root`와 기존 S01 연결 회귀 **28 passed**도 확인했다. production source·계약·환경은
  변경하지 않았다. 이는 전체 품질·실제 연구 완료의 증거가 아니다. 계약 문서 원문 지문
  `contract_sources_sha256`은 기존 워크벤치 필드 계약 ID와 다르다.
  adapter의 pending 차단은 완성 후 파일 해시 재검증까지 수행하지 않는다. 정확한 검증 범위와
  실패·GC·업데이트 사례는 [S01 스펙](S01-snapshot.md),
  [보존 테스트](../../../database/tests/test_research_snapshot.py),
  [adapter 통합 테스트](../../../backend/tests/test_research_snapshot_integration.py)에 있다.
- **실행 진입점:** `PYTHONPATH=database/src backend/.venv/bin/python -m equity.research_snapshot`
  의 `create`, `verify`, `open`. 기존 목적지를 덮어쓰지 않으며 검증한 `open` 결과로 기존
  `STRATEGY_WORKBENCH_EQUITY_ROOT`를 설정한다. 실행 전 `open --repo-root`로 입력·코드·계약을
  재검증하고 실패 시 중단한다. 완성 catalog가 절대경로를 사용하므로 root를 이동하지 않는다.
- **다음 결정:** [S01 연구 준비 판정](S01-readiness.md)에 따라 별도 사본의 공개·재접속·무결성과
  기존 owner 기반 사전 품질 근거를 구분해 확인한다. S02 한계와 PIT·MASKED·상장구간·현재 업종
  제약을 적용한 뒤 첫 연구 입력으로 사용할지 결정한다. 부족한 근거는 범위를 줄이거나 보류한다.

## S02 — 비용·수량·기업행동 회계 검산

- **의도:** 장부 오류나 회수되지 않은 자산을 전략 수익으로 해석하지 않도록 계산 경계를 확인한다.
- **현재 구현:** 합성 두 종목·다섯 세션에서 실제 선정 compiler → TargetTape → 실행 adapter →
  Python/Rust 커널을 손계산과 비교한다. 전량 목표와 부분체결, 분할, 종목 교체, 수수료·매도세,
  평가액·벤치마크 차이를 확인했다. production 회계 의미를 바꾸지 않았다.
  후속 세션 계약은 필수 keyword-only `BacktestDataset.sessions`로 실제 공통 거래일을 전달한다.
  모든 선택 종목의 bar가 없어도 빈 세션을 유지해 DAY 만료와 벤치마크 선택에 독립적인 실행
  일정을 보장하며 역순·중복 달력과 달력 밖 측정 bar는 거절한다.
  이 일정 변경은 `backtest-engine-v7`로 실행 지문을 구분하며, 양 코어 달력 회귀 **11 passed**와
  관련 회귀 **124 passed**로 확인했다(서로 겹치는 검증이며 합산하지 않는다).
- **증거·아직 못 확인:** 신규 **4 passed, 0 skipped**, 관련 회귀 **172 passed, 0 skipped**.
  [S02 스펙](S02-accounting.md)과 [검산 테스트](../../../backend/tests/integration/test_s02_accounting.py)에
  독립 기대값이 있다. 상장 종료 뒤 회수금 없이 마지막 평가가 남는 한계, 마지막 bar 이후 사건 제외,
  배당일 부재를 명시했다. 배당 포함 총수익·상폐 정산이 해결됐다는 증거가 아니다.
- **실행 진입점:** `backend/.venv/bin/python -m pytest backend/tests/integration/test_s02_accounting.py -q`.
  원천 팩터 평가와 미래 라벨을 검증한 테스트는 아니므로 S03 증거와 구분한다.
- **다음 결정:** 분할을 반영한 비용 후 가격 기준 연구로 제한할 수 있으면 S03을 진행한다. 배당
  총수익이나 상폐 영향의 크기가 결론에 필수라면 원천·정산 계약을 먼저 보완한다.

## S03 — 신호 평가와 보존 입력 연구 runner

- **의도:** 종목 순위를 맞히는 능력과 실제 매매의 비용 후 효과를 별도로 측정하고 같은 입력으로
  연구 증거를 다시 만들 수 있게 한다.
- **현재 구현:** 기존 `domain/signal_evaluation`의 별도 미래 분할조정 가격 라벨·날짜별 Rank IC·
  커버리지 core에 `application/signal_research` 유스케이스와 CLI를 연결했다. S01 보존 검증 → 실제
  DuckDB adapter → 기존 raw/factor pipeline → 별도 label 조회 → 원시 근거·일별 IC/coverage
  JSON 내보내기를 합성 원장으로 검증했다. 미래 라벨은 선정/목표 비중 입력에 넣지 않는다.
  유스케이스 자체 `SnapshotGuardPort`·`SignalPipelinePort`·`SignalLabelDataPort`를 구현하는
  adapter를 `bootstrap/signal_research`가 별도로 조립한다. 기존 평가·조회·봉인 계약을 복제하지 않는다.
  snapshot guard는 지정 저장소의 S01 검증에 더해 현재 로드된 `strategy_workbench`·
  `backtest_engine`의 module origin·package search path를 해당 `backend/src`에 묶는다.
  소스 checkout/editable과 symlink resolve 일치는 지원하며 별도 wheel 위치는 unsupported로
  차단한다. 메모리 변조 attestation은 범위 밖이다.
- **증거·아직 못 확인:** runner 신규 **25 passed**, 기존 S03 21건+architecture 18건 포함
  **64 passed, 0 skipped**, DuckDB·S01·기존 pipeline 관련 회귀 **195 passed, 0 skipped**다.
  미보존·변조 root 차단, 쿼리 끝으로 자르지 않은 실제 `security_span`의 상폐·coverage_gap·data_gap
  구분, 당시 중간 MASKED 조정 공백의 요청 전체 unsupported, 정지 기준가의 거래 가격 제외를 확인했다.
  후속 개별 공백 구현은 해당 종목의 진입~만기 양끝 포함 구간만 `adjustment_gap`·수익 None으로
  남기고 다른 라벨·당시 분모·coverage·상태 카운트를 보존한다. 위 과거 통과 수를 이 후속
  변경의 검증 결과로 재사용하지 않는다. [개별 공백 사양](S03-adjustment-gaps.md)이 계약을 소유한다.
  한글·공백 root의 실제 UTF-8 subprocess 왕복도 확인했다. Windows 실측 증거는 아니다.
  실원장 수신 후 S01 별도 사본의 생성·검증·재접속을 확인했고, 후속 고정 창의 기술 확인에서
  IC·coverage·상태를 실제 내보냈다. 실성과·비용 후 포트폴리오 비교·완전 분석·기존 API/UI
  노출은 **미완료**다.
  가격 라벨은 배당·비용·실제 다음 시가 체결을 반영하지 않으며 봉인 guard를 유지한다.
- **실행 진입점:** `python -m strategy_workbench.bootstrap.signal_research`에
  `--preserved-equity-root`·`--repo-root`·전략/실행 설정 JSON·팩터·기간 명세를 명시한다.
  검증은 adapter 열기 전·pipeline 실행 전·평가 후에 수행하며, 미지원 원천을 mock이나 원주가로
  대체하지 않는다. `--output-json`은 새 파일만 허용하고 `-`는 표준 출력이다.
  [runner 스펙·CLI·검증](S03-runner.md), [순수 평가 계약](S03-signal-evaluation.md),
  [runner 통합 테스트](../../../backend/tests/integration/test_signal_research_runner.py),
  [유스케이스 테스트](../../../backend/tests/application/test_signal_research_service.py)가 근거다.
  기존 `FactorResearchService`의 catalog/validate/explain과 비용 후 `portfolioAnalytics`는
  이 미래 라벨 신호 평가를 대신하지 않는다.
- **다음 결정:** [고정 사양과 1회 기술 확인 증거](S03-adjustment-gaps.md#첫-실자료-기술-확인-증거)의
  독립 무결성 검산을 마쳤다. 기존 `price.momentum_12_1`·각 신호일 전체 당시
  `krx.common-stock`·5/21/63 동시 관찰의 15개 단면에서 IC 계산과 coverage·상태 내보내기를
  확인했으며, 작은 창의 기술 경로 증거로 해석한다. alpha 채택·유의성·계좌 성과는 판단하지 않는다.
  후속 창·후보·비용 후 비교를 정할 때 [결과 전 사양·열람 기록](S01-readiness.md#첫-s03-실행-전에-고정할-기록)을
  남긴다. 배당 총수익·상폐 회수금·중첩 라벨 추론은 여전히 남은 범위다.

## S04 — 계좌 경계 안내·연속 실행 검산과 반증·선택 기록

- **의도:** 기간별 비교 결과를 하나의 실제 계좌 성과로 오해하거나 근거보다 강한 확신을 갖지 않게 한다.
- **현재 구현:** 기존 워크포워드 계산 owner의 `window_restart_returns`와 한글 안내를 실제 API
  `GET /api/v1/experiments/{id}/walk-forward`와 실험 상세 화면에 연결했다. 창마다 같은 초기자본으로
  재시작하고 첫 기록 이전 진입비용·손익을 제외하며 현금·보유 수량을 이월하지 않는다고 명시한다.
  옛 서버가 안내를 주지 않으면 계좌 연결 방식을 확인할 수 없다고 표시한다. 곡선·샤프·유지율·
  trial key·설계 판본·선택 규칙은 유지했다.
  후속 합성 검산은 고정 전략의 단일 TargetTape를 전체 공통 거래일로 한 번 실행한다. 기존
  compiler→실행 adapter→Python/Rust를 재사용하고 보고용 경계에서는 계좌를 재시작하지 않는다.
- **증거·아직 못 확인:** 독립 합성 장부에서 비용 0원일 때 두 평가가 1.21배로 일치하고, 주문당 10원
  비용이면 현 연결은 약 1.190083배, 연속 계좌는 1.16배임을 확인했다. 관련 backend **214 passed**,
  architecture **18 passed**, frontend 전체 **105파일·1,346테스트 통과**, typecheck·lint·build와
  기존 US-SM-14·15 mock 브라우저 e2e **1 passed**다. 실제 연속 계좌 실행기는 **미구현**이며,
  비용·지연·미체결 스트레스와 일반 평가 열람 이력도 남아 있다. 합성 비교는 실데이터 수익성 증거가 아니다.
- **실행 진입점:** 기존 실험 결과 API와 실험 상세 화면을 사용한다. 별도 연속 실행기나 export 형식은
  추가하지 않았다. [S04 스펙·후속 계좌 계약](S04-walk-forward.md),
  [독립 장부 fixture](../../../backend/tests/domain/test_walk_forward_account_boundary.py),
  [API 통합 테스트](../../../backend/tests/integration/test_experiment_http_api.py)가 근거다.
- **후속 합성 검산 증거(2026-10-16):** 연속 계좌 테스트 **12 passed, 0 skipped**, 사건 테스트
  **6 passed, 0 skipped**로 양 코어가 실행됐고 두 파일 재실행도 **18 passed, 0 skipped**다.
  파일별 결과와 같은 테스트이므로 합산하지 않는다. backend 전체·root tools의 ruff·pyright는 오류
  0이며 story harness(40 stories·48 e2e 태그)·충돌 표식 검사도 통과했다. 수수료 1%·매도세 2%의
  종목 교체에서 경계 직전 1081원→최종 1141.3원, 현금 41.3원·B 10주를 손계산과 대조했다.
  기존 기간 조회의 직전 snapshot 기준과 경계 첫 세션 비용·손익 귀속, 두 기간 배수 곱과 전체 배수
  일치도 확인했다. 부분체결 DAY 만료·빈 거래일·정지 중 분할의 재개 시 한 번 정산·미회수 상장 종료
  보유의 마지막 평가 한계도 확인했다. 같은 시점의 backend 전체 회귀는 **4,675 passed, 1 skipped,
  34 warnings**(161.07초)이며 신규 18건이 포함되므로 합산하지 않는다. skip은 기존 실공급자 live smoke의
  opt-in 미설정으로 외부 연결 통과의 증거가 아니다. database·frontend 코드는 바꾸지 않았고 database
  테스트는 이번에 재실행하지 않았다. 위 첫 slice·과거 전체 회귀와 합산하거나 전체 GitHub CI 통과로
  해석하지 않는다.
- **다음 결정:** [단일 계좌 연속 실행 검산의 범위·증거](S04-walk-forward.md#후속-검산--고정-전략의-단일-계좌-연속-실행-검산)를
  바탕으로 단일 run 보고서를 구현했다. 다음에는 동결된 창별 선택 일정의 적용 시점·기존 보유
  교체·미체결 정책을 연결할 범위를 정한다.
  이번 합성 검산은 연속 워크포워드 제품 기능이나 초기상태 이월 API의 구현 완료를 뜻하지 않는다.
  S03 실자료 재실행·봉인 개봉·기존 trial key 의미 변경을 자동으로 수행하지 않는다.

### S04 후속 — 완료 run의 초기자본 기준 계좌 보고서

의도는 첫 snapshot 기준 FULL 지표와 초기자본 대비 비용 후 평가손익을 함께 읽는 것이다.
완료 run의 원본 결과를 바꾸지 않고 초기조건·최종 현금/자산·평가손익·수익률·출처·경고를 별도
`GET /api/v1/backtests/{run_id}/account-report`로 조회한다. 금액·명시 상태·해석은 순수 domain
projection이 소유하고 application은 기존 결과의 완료·해시·역직렬화 검증을 한 번 재사용한다.
초기조건에 날짜를 만들거나 비용을 다시 빼지 않으며 최종자산은 청산 현금이 아니다. 기존 result와
보고서를 함께 조회할 때는 요청마다 검증 읽기를 수행하고 새 캐시를 두지 않는다.

신규 HTTP/application 통합 검증은 **10 passed, 1 warning**(2026-10-16)이다. 직접 만든 임시
저장소 입력의 초기 100·첫 snapshot 99·최종 90/100/110으로 손실·영손익·이익과 FULL 분모 차이,
원본 불변·재시작·단일 읽기·typed unavailable·404/409/410·경로 비노출을 확인했다.

최종 로컬 전체 회귀는 backend **4,708 passed, 1 skipped, 34 warnings**(173.09초), frontend
**106파일·1,364테스트 통과**(82.30초)다. domain·HTTP·S04·architecture 관련 묶음은
**69 passed, 1 warning**(6.32초)이며 신규 domain·HTTP 33건을 포함해 backend 전체와 중복되므로
합산하지 않는다. backend·root tools 전체 ruff·pyright 오류 0과 스토리 하네스 **40/48**을 확인했다.
US-DM-04 mock 브라우저 e2e는 **1 passed**(7.2초)이며 서버 보고서 값·360px 문서 폭·목차 초점·
503 복구·410 재시도 금지·원본 결과 보존을 확인했다. skip은 기존 실공급자 live smoke의 opt-in
미설정이며 외부 연결 통과의 증거가 아니다. 과거 검산·전체 회귀와 합산하지 않고 로컬 통과를 전체
GitHub CI 완료로 해석하지 않는다. CI·머지 결과는 해당 PR의 검증 기록을 따른다.
[구현 책임·제한·증거](S04-walk-forward.md#완료-run의-초기자본-기준-계좌-보고서--구현된-최소-범위)가 상세 계약이다.

다음 결정은 동결된 창별 선택 일정의 적용·기존 보유 교체·미체결 정책이다. 연속 워크포워드 실행기·
실자료 alpha·배당/상폐 회수금 문제를 해결한 증거로 확대하지 않는다. source와 SoT가 바뀌어 기존 S01
보존본 지문과 현재 코드가 다를 수 있으며 과거 보존본·증거·해시를 수정하지 않는다. 새 연구는 결과 전
사양·별도 보존·실행 전 S01 검증을 따른다. 이번에는 실자료 연구를 재실행하지 않았다.

### S04 후속 — 동결된 창별 선택 일정

- **의도·질문:** 학습에서 동결한 후보의 역사적 가용일과 실제 실행일을 구분할 수 있는가를 확인한다.
- **구현:** `domain/experiment`의 순수 불변 `SelectionSchedule`이 선택·달력·정책 신원, 실행일부터의
  읽기 전용 후보 조회와 변경 기록을 소유한다. `known_as_of >= train_end`이고 신호 종가·다음 공통
  세션 시가가 같은 검증 창 안에 있어야 한다. no-cell·늦은 선택·세션 부족은 전체 blocked이며
  역순·중복 날짜·창 gap·overlap은 오류다. `WindowPick.created_at`을 역사 시각으로 쓰지 않는다.
- **증거·제한:** 일정 단위·기존 experiment·계좌 경계·architecture 관련 **127 passed**, 전체
  ruff·pyright 오류 0을 확인했다(2026-10-16). backend 전체 수가 아니며 중복 합산하지 않는다.
  신규 합성 **12 passed, 0 skipped**로 양 코어를 실제 실행했고 관련 **52 passed, 0 skipped**
  (1.52초)를 확인했다. 127건과 52건은 겹치므로 합산하지 않는다. 최종 backend 전체 회귀는
  **4,749 passed, 1 skipped, 34 warnings**(165.17초)이며 관련 묶음·신규 합성 12건과 합산하지 않는다.
  skip은 기존 live provider opt-in 미설정이며 외부 연결 증거가 아니다. backend·root tools의
  ruff·pyright 오류 0과 하네스 **40개 스토리·48개 e2e 태그**, 충돌 표식 통과를 확인했다.
  frontend·database production 코드 변경이 없어 두 전체 테스트는 이번 로컬 검증에서 재실행하지
  않았다. 전체 CI는 별도 확인하며 과거 검증 수치를 현재 결과에 합산하지 않는다. 비용 0의 최종 1210원과 비용 후 1141.3원·현금 41.3원, 수수료 28.9원·세금
  19.8원, 실제 부분체결 2주 매도 후 1120원·현금 20원을 대조했다. 빈 DAY/정지 보유의 잔존 평가
  1100원·현금 0원은 회수금이 아니다. 제한된 helper의 next-open·DAY·
  long-only·동일 gross·buffer 0·최소 거래 0 커널 검산은 production 후보 혼합 runner가 아니다.
  hash는 입력·정책 신원이며 무누수 증명이 아니다. compiler carried 목표와 실제 보유를 구분하고
  후보별 tape를 concat하지 않는다. admission·주문·API 연결과 기존 실험·trial key 변경은 없다.
- **다음 결정:** portfolio 단일 fold·선정 snapshot 입력·후보 신원과 모든 후보 capability 검증을
  갖춘 제품 runner를 설계한다. 월별 cadence의 일반화는 미완료다.
  [상세 계약](S04-walk-forward.md#후속-slice--동결된-창별-선택-일정)을 따른다. 실자료 검증은
  사용자의 2026-10-16 결정에 따라 2026-10-19 주간 예정으로 미루며 이번에는
  실자료·봉인·키움에 접근하지 않는다.

### S04 제품 연결 후속 — 혼합 후보 단일 계좌

앞의 일정 slice와 합성 helper의 제한은 당시 구현 기록이다. 이번 후속은 실제 연속 실행과
진행·계좌 결과·실패 이유를 기존 실험 상세에서 확인하는 별도 평가 흐름을 연결한다.

- **단일 fold:** 후보의 전략·고정 파라미터·원문·팩터 계획에서 ID를 자동 파생하고 공통
  snapshot과 평가 판본을 검증한다. 전체 달력의 기존 월별·매 N cadence를 유지하며 첫 후보와
  변경 후보만 강제 판단한다. 직전 목표 비중을 후보 사이로 전달해 buffer·최소 거래를 보존하고
  실제 수량·현금·DAY 잔량은 기존 커널이 처리한다. 일반 tape·trial key는 유지한다.
- **출처·시점:** 프레임의 선택 근거 창과 실행일 소속 창을 구분하고 새 후보 첫 신호 이후에는
  이전 후보 판단을 추가하지 않는다. 유효한 선택이 늦으면 그 첫 신호 전에는 직전 후보의 cadence
  주문을 허용한다. no-cell·실행 세션 부족으로 일정이 blocked이면 전체 실행을 차단한다.
- **영속 상태:** 별도 SQLite DB가 요청 UUID 멱등성·전역 단일 실행·진행률·불변 완료 결과를
  보존한다. 재시작은 마지막 진행률을 남긴 `interrupted`로 닫고 자동 재실행하지 않는다.
  직접 컨테이너의 기본 저장소는 메모리이며 HTTP 런타임만 명시한 파일 경로를 사용한다.
- **범위:** 현재 mock 자료만 대상으로 연결하며 실제 자료 수익성 검증·봉인 열람·키움 연결은
  하지 않는다. 선택 시각은 `assumed_train_end_close` 재현 가정이며 실제 당시 처리 시각의 증명이
  아니다. 다음 연구는 2026-10-19 주간 예정인 실자료 검증의 사양·스트레스·판정 기준을 결과 전에
  동결하고 별도 S01 보존과 실행 전 검증을 따르는 단계다. 배당·상폐 회수금 한계도 남는다.
- **확정 backend 증거(2026-10-17):** 전체 **4,793 passed, 1 skipped, 34 warnings**(199.89초,
  종료 코드 0). 신규 44건(domain 11·SQLite 20·HTTP 7·service 6)은 전체에 포함하므로 합산하지
  않는다. backend 전체·root tools ruff·pyright 오류 0, 하네스 **40개 스토리·48개 e2e 태그**와
  충돌 표식 검사를 통과했다. OpenAPI JSON 비교에서 **기존 path·schema 변경 0**을 확인했다.
  skip은 기존 live provider opt-in 미설정이며 로컬 통과를 GitHub CI 완료로 표현하지 않는다.
  frontend 최종 전체는 **109파일·1,372 passed**(82.00초, 종료 코드 0)이며 마지막 query 설정
  수정 뒤 typecheck·build·editor 예산 **132 KiB / 200 KiB**도 통과했다. 격리 mock e2e는
  **1 passed**(54.3초)로 계좌 기록 기간과 새로고침 후 재평가 활성화를 확인했다. 그 뒤 발견한
  재연결 시 410 조회 자동 재개는 두 query의 `refetchOnReconnect: false`와 offline→online·기본
  query 양성 대조를 포함한 관련 **13 passed**로 검증했다. 13건은 최종 전체에 포함하며 합산하지
  않는다. 마지막 수정 뒤 브라우저 e2e를 다시 실행한 증거와 구분한다. 이 내용은 로컬 검증 기록이며
  전체 GitHub CI·머지 상태는 해당 PR의 checks·merge 기록에서 확인한다.
- **검증 격리:** 초기 탐색의 연속 DB 환경 변수 누락은 사용자에게 고지하고 테스트 ID 1행을
  확인해 보존 이동했다. 설정과 격리 회귀를 보완한 뒤 새 mock 환경에서 흐름을 확인했으며
  초기 탐색을 최종 e2e 증거와 혼합하지 않는다. 상세 복구 기록과 검증 범위는 아래 문서를 따른다.
  [포트폴리오·영속 실행 계약](S04-walk-forward.md#제품-연결-후속--후보-혼합-단일-fold와-별도-연속-평가)을 따른다.

## S05 — 영속 의도·가짜 브로커·대사

- **의도:** 응답 유실·재시작·중복 이벤트가 중복 주문이나 설명되지 않는 현금·수량으로 번지는 것을 막는다.
- **현재 구현:** 별도 실행 domain/application과 전용 SQLite 원장, 오프라인 fake broker를 추가했다.
  READY 저장 → UNKNOWN 원자 claim 커밋 뒤 한 번만 전송하고 ACK 유실/재시작은 조회로 대사한다.
  실제 보유·미체결·예약현금, 부분체결·취소·거절·중복/역순 사실을 구분하며 중지와 취소를 분리한다.
  fake의 각 증분 체결도 기존 도메인 `classify_event`로 저장 전에 검증해 지정가 위반이 누적
  평균에 가려지지 않게 한다. 실패 시 주문·revision·계좌를 변경하지 않는다.
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

첫 표는 **1차 기반 `8c3c1803` 검증**이며 이후 S03 runner·S04를 포함한 전체 회귀는 다음 표에
별도 시점의 증거로 적는다. Rust 확장을 준비한 backend 환경에서 확인했다. 시점과 묶음이 겹치므로
pass 수를 합산하지 않는다.

| 1차 기반 검증 | 결과 | 범위 |
|---|---|---|
| backend 전체 | **4,583 passed, 1 skipped, 34 warnings** (163.41초) | 1차 기반 시점의 전체 회귀 |
| database 전체 | **1,437 passed** (162.62초) | 실제 원장 품질 인증이 아닌 1차 코드·합성 동작 |
| Rust parity 별도 묶음 | **838 passed** | 1차 backend 전체와 중복되는 재실행 |
| backend 전체 ruff·pyright | **오류 0** | 1차 `src tests examples scripts tools` 범위 |
| root `tools` ruff·pyright | **오류 0** | 1차 루트 도구 정적 검사 |
| 필드 대응표 검사 | **fields=40, map=46, missing=0** | 1차 팩터 요구 필드 대응표 coverage |
| root `tools` unittest | **43건 중 42 통과, 1 실패** | 1차 당시 PowerShell 설치 환경 assertion 실패 |
| 병합 충돌 표식 검사 | **통과** | 1차 `quant_study_dev.conflict_markers` |

1차 backend skip 1건은 외부 공급자 live smoke의 opt-in 조건을 켜지 않아 발생했다. 외부 연결이
통과한 것으로 세지 않는다. 당시 전체 회귀 명령은 다음과 같다.

```sh
uv run --no-sync --project backend pytest backend/tests -q
uv run --no-sync --project backend pytest database/tests -q
uv run --no-sync --project backend pytest backend/tests/test_core_parity.py -q
```

**후속 전체 검증(2026-10-16, S03 runner·S04 포함):**

| 검증 | 결과 | 범위·시점 |
|---|---|---|
| backend 전체 | **4,610 passed, 1 skipped, 34 warnings** (169.50초) | S03 runner·S04 포함 현재 전체 회귀 |
| database 전체 | **1,437 passed** (162.26초) | 같은 후속 시점의 코드·합성 fixture 전체 회귀 |
| backend 전체 ruff·pyright | **오류 0** | 현재 `src tests examples scripts tools` 전체 |
| frontend 전체 | **105파일·1,346테스트 통과** | 앞선 S04 검증 시점, typecheck·lint·build·editor bundle 통과 |
| mock 브라우저 e2e | **1 passed** | 앞선 S04의 기존 US-SM-14·15 흐름, 실제 backend 안내 표시 |

전체 backend·database 명령은 저장소 root에서 각각
`uv run --no-sync --project backend pytest backend/tests -q`와
`uv run --no-sync --project backend pytest database/tests -q`다. 테스트 skip·합성 입력의 통과를
실제 공급자 연결이나 실데이터 수익성 증거로 세지 않는다. S03 runner의 25/64/195건 묶음과 S04의
관련 검증은 각 절에 별도로 적었으며 위 전체 회귀에 더해 합산하지 않는다.
로컬 Node 25의 실험 WebStorage가 jsdom 저장소와 충돌해 최초 frontend 실행이 실패했으며,
`NODE_OPTIONS=--no-experimental-webstorage`로 실행 환경을 맞춘 전체 재실행은 통과했다.
CI는 Node 22를 사용한다. 제품 코드나 테스트 설정을 우회하도록 바꾸지 않았다.

macOS PLAN 집계는 기존 PowerShell 환경 실패를 넘어 순수 Python 공용 owner로 계산·검증하도록
수정했다. 공용 Python core에 대한 후속 root unittest는 **52건 모두 통과**, tools ruff·pyright
오류 0이며 기존 다섯 PLAN을 읽기 전용으로 검증해 모두 VALID·`changed=False`를 확인했다.
실제 tracker·체크행·PR 상태나 의존성은 바꾸지 않았다.

CI의 uv 미설치 환경 실패에 대응한 최종 PS1 다섯 개는 workflow 변경 없이 PATH의 `python`이
3.11 이상인지 먼저 검사하고, `python -X utf8`로 절대경로의 공용 script를 직접 호출한다.
패키지·editable 설치가 없는 `-I -S` 격리 Python에서도 직접 script로 다섯 PLAN을 검증했고,
이 수정 후 부모의 root 전체 52건과 tools ruff·pyright 오류 0을 확인했다.
원격 HEAD `79b9c86d`의
[generated-progress 단일 CI job 성공](https://github.com/Nochiski/Quant_study/actions/runs/38862921708)은
이 호출 경로의 추가 검증 근거다. [공용 PLAN 집계 계약·사용법](../../../tools/PLAN_PROGRESS.md)과
[fixture 테스트](../../../tools/tests/test_plan_progress.py)에 상세 근거가 있다. 실제 Windows
PowerShell 현지 실측은 여전히 남아 있다.

로컬 실행 진입점은 아래와 같으며 과거 PowerShell 1건 실패를 현재 실패로 그대로 적용하지 않는다.

```sh
uv run --locked python -m unittest discover -s tools/tests -p 'test_*.py'
uv run --locked python -m quant_study_dev.plan_progress --profile validation-lab --check
uv run --locked python -m quant_study_dev.conflict_markers
```

**전체 GitHub CI 통과는 주장하지 않는다.** 위 결과는 로컬 전체 회귀와 명시한 HEAD의 단일 job
증거다. 전체 PR 검사·머지 상태는 각 PR 링크가 소유하며 이 문서에 동적 상태 목록을 추가하지 않는다.
1차·후속 회귀를 합산하지 않는다.
위 과거 검증 당시에는 실자료 가격 라벨·IC를 산출하지 않았고 실제 계좌 연결·봉인 구간 개봉도
하지 않았다. 후속 1회 실자료 기술 확인은 [별도 증거](S03-adjustment-gaps.md#첫-실자료-기술-확인-증거)로 구분한다.
Validation Lab PLAN의 MERGED 표시·PR 수·의존성은 이번 인계에서 바꾸지 않는다.

**리뷰 수정 후 로컬 검증(2026-10-16):** 빈 거래일에도 DAY 만료를 유지하고, 연구 실행 코드의
출처를 검증 저장소에 묶으며, fake의 지정가 위반 증분이 누적 평균에 숨지 않도록 보완했다.
수정 후 backend 전체는 **4,642 passed, 1 skipped, 34 DeprecationWarning**(171.03초),
database 전체는 **1,437 passed**(160.66초), root tools는 **52건 통과**했다. backend 전체
`src tests examples scripts tools`와 root tools의 ruff·pyright는 오류 0이며, 충돌 표식·
story harness(40 stories·48 e2e 태그)·필드 대응표 검사도 통과했다. frontend·Rust 코드는
변경하지 않았고 Python/Rust parity는 backend 전체 검증에 포함된다. skip은 실공급자 live
smoke의 opt-in 미설정에 따른 것으로 외부 연결 증거가 아니다. 이 검증 기록 당시 GitHub CI는 진행 전이었다.
당시 다음 결정은 별도 보존본과 결과 전 사양 준비였으며 후속 실자료 기술 확인과 현재 다음
결정은 [별도 증거](S03-adjustment-gaps.md#첫-실자료-기술-확인-증거)를 따른다. 이 과거 기록으로 현재 CI 상태를 추론하지 않는다.
