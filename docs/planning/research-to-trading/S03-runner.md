# S03 후속: 보존 입력을 읽는 최소 연구 실행 명령

## 목적

최종 목적은 비용 후 수익 자동매매다. 이번 조각은 그 전 단계인 “종목에 매긴 점수가 이후 가격 변화와 어떤 관계를 보였는가”를 같은 보존 입력으로 다시 확인하고, 비교하지 못한 종목도 함께 기록하는 실행 경로를 만든다. 결과는 가격 예측의 연구 증거이며 비용 후 계좌 수익, 전략 채택, 실계좌 투입의 근거를 완성하지 않는다.

S03 순수 함수의 수식·순위·coverage 계약은 [S03-signal-evaluation.md](S03-signal-evaluation.md)가 소유한다. 이 문서는 그 함수를 실제 기존 raw/factor 파이프라인에 연결하는 유스케이스·CLI·입력 검증만 추가한다. 기존 FactorResearchService, HTTP, UI, 기존 회계 엔진 의미를 바꾸지 않았다.

## 질문

1. 사용자가 지정한 S01 보존 root가 완성됐고, 파일·테이블·코드·환경 판본이 지금 실행할 것과 일치하는가?
2. 선택한 기존 StrategySpec과 RunEnvironment로 계산한 실제 팩터 값을 미래 가격과 별도로 읽어 비교할 수 있는가?
3. 원장이 실제로 지원하지 않는 가격 조정·상장구간·정산을 채워 넣지 않고, 실패와 비교 가능 비율을 드러내는가?
4. 전략·명세·원시 평가값·가격·상장 근거·상관·제외 건수를 검토 가능한 JSON으로 내보낼 수 있는가?

## 최소 변경과 책임

| 책임 | 소유 위치 | 입력과 결과 |
|---|---|---|
| 연구 실행 순서와 판본 검증 | `application/signal_research` | 기존 StrategySpec·RunEnvironment, 명시한 팩터·라벨 명세 → 결과 또는 명시 실패 |
| 실제 raw/factor/선정 파이프라인 연결 | `adapters/outbound/signal_pipeline` | 기존 PortfolioDesignService 실행 후 raw 관측 키·실제 팩터 평가값·TargetTape snapshot을 대조해 자체 PreparedSignals로 변환 |
| 가격·공통 달력·실제 상장구간 | `adapters/outbound/equity_duckdb/_signal_labels.py` | 기존 raw 가격·trading_sessions·거래 bar 판정 재사용, security_span의 원래 first/last/end_reason 읽기 |
| S01 검증 연결 | `adapters/outbound/research_snapshot` | 기존 `equity.research_snapshot verify --root ... --repo-root ...`를 별도 프로세스로 실행 |
| 요청 JSON·CLI 인자·증거 직렬화 | `adapters/inbound/signal_research_cli` | 기존 hydrate와 RunEnvironment 검증을 사용, 결과 JSON을 새 파일 또는 표준 출력으로 반환 |
| 구체 구현 조립 | `bootstrap/signal_research` | 검증된 보존 DuckDB만 명시적으로 조립. mock 선택·fallback 없음 |

새 application이 다른 application의 서비스를 직접 빌려 쓰지 않도록 자체 SignalPipelinePort를 소유한다. bridge adapter가 실제 기존 서비스 호출과 포트 입출력 변환·경계검증을 맡는다. 가격 조회 계약도 자체 SignalLabelDataPort이며, 기존 백테스트 memberships의 잘린 날짜를 소비하지 않는다. 새 의존성은 각 facade에 선언하고 architecture 검사를 통과시켰다. 후속 개별 조정 공백 처리는 기존 S03 순수 evaluator가 소유하며, adapter는 원장의 MASKED 키를 전달한다.

## 입력·라벨·실패의 의미

### 보존과 재현

CLI는 `--preserved-equity-root`와 `--repo-root`를 필수로 받는다. S01 검증은 어댑터를 열기 전, 파이프라인 실행 전, 평가가 끝난 뒤에 실행한다. 코드·환경 해시 검증 옵션을 생략하지 않는다. 성공한 S01 ledger ID가 adapter snapshot의 원천 ID와 같아야 하고, 신호·TargetTape·가격의 전체 snapshot ID도 같아야 한다. 실행 중 보존 descriptor나 검증 근거가 바뀌면 결과를 반환하지 않는다.

검증할 저장소와 실제 실행 코드도 일치해야 한다. 매 S01 검증 전에 현재 프로세스가 이미 불러온
`strategy_workbench`·`backtest_engine`의 모든 모듈 파일 출처(`__file__`·`__spec__.origin`)와
패키지의 하위 import 경로(`__path__`·`submodule_search_locations`)를 `--repo-root`의
`backend/src`에 묶는다. 다른 checkout을 가리키는 검증 인자나 `sys.path` 추가만으로 이미 불러온
코드의 출처를 바꿀 수 없다. 서로 다른 checkout의 모듈 혼용도 실행 전에 거절한다. 이는 S01의
파일 해시 규칙을 복제하지 않고, 그 규칙이 검증한 소스와 실제 신호·평가 실행 경로를 연결해
연구 재현 근거를 보호하는 검사다.

실행은 해당 소스 checkout의 editable 설치를 지원한다. 의존성을 설치한 가상환경은 그대로 사용할 수
있으며, 한글·공백 경로와 같은 checkout을 가리키는 symlink는 실제 경로로 비교해 허용한다.
`site-packages`에 별도 복사된 wheel 코드나 출처를 확인할 수 없는 모듈은 명시적으로 거절한다.
이 검사는 import 출처와 디스크의 S01 검증을 연결하며, 이미 메모리에 올라온 Python 객체의 임의
변경이나 import 후 디스크 변경에 대한 완전한 메모리 증명까지 제공하지 않는다.

S01 검증 규칙을 복제하거나 완화하지 않는다. 나중에 코드를 바꿨다면 기존 사본의 원본 무결성만 맞는다는 이유로 그대로 연구를 실행할 수 없다. 그 사본에 기록된 코드·환경으로 실행하거나, 승인된 원본과 실제 사용할 코드를 기준으로 S01 사본을 새로 만들고 검증해야 한다.

검증 프로세스는 명령 인자 배열을 사용하며 쉘 문자열을 만들지 않는다. 자식 `PYTHONIOENCODING`과 부모 프로세스 교환 encoding을 UTF-8로 지정했다. 한글·공백 경로는 현재 macOS 테스트 환경에서 실제 subprocess 왕복을 확인했으며 Windows 실행을 검증했다는 뜻은 아니다.

### 신호와 미래 결과 분리

신호 창은 RunEnvironment의 start/end, 평가 cutoff은 별도의 `evaluation_as_of`, 만기를 찾을 달력 끝은 `label_calendar_through`다. `start <= end <= evaluation_as_of <= label_calendar_through`를 요구한다. 연구 하한은 기존 `require_environment`·`require_research_window`가 지킨다.

기존 파이프라인을 먼저 실행하고 그 결과의 `PortfolioObservation`을 고정한다. 라벨은 그 뒤 별도 조회한다. 기존 팩터 평가의 기본 파라미터 해소 규칙을 그대로 사용하며, 이번 CLI는 별도 파라미터 override 기능을 제공하지 않는다. 결과의 request에 전체 전략과 기본 파라미터 정의를 함께 남긴다.

가격 라벨은 `price.adj_close`의 **같은 세션 종가**를 사용하므로 이 필드가 가용하고 recommended lag가 0이어야 한다. 필드가 없거나 다른 랙이면 unsupported다. 현금배당을 포함하지 않으며, 원주가·mock으로 대체하지 않는다. 가격은 평가 cutoff까지 읽고, 만기를 찾을 공통 달력은 별도로 더 길게 읽을 수 있다. 달력이 부족하면 pure evaluator의 `calendar_insufficient`, 만기가 평가일 이후면 `not_matured`가 남는다.

정지·기준가·잠정판·무효 OHLC 행을 거래 가격으로 쓰지 않도록 기존 BacktestDataPort의 bar 판정 결과를 읽고, 그 키에 대응하는 조정종가만 라벨 입력으로 변환한다. 이 과정에서 백테스트 memberships는 사용하지 않는다. 따라서 정지일에 기준가가 있더라도 진입·만기 가격은 결측으로 남는다. 가격예측량을 실제 체결 가능성이나 다음 시가 계좌 손익으로 바꾸는 계산은 추가하지 않았다.

### 상폐와 수집 범위

`security_span`의 first/last/end_reason을 **쿼리 날짜로 자르지 않고** 읽는다.

- `delisted`: 원장이 선언한 종료 근거와 last_session을 사용한다. 그 뒤 만기의 회수금은 없으므로 `listing_ended_unsettled`다. 마지막 가격이나 0원으로 정산하지 않는다.
- `coverage_gap`: last_session이 원장 전체 공통 달력의 수집 상한과 같음을 확인한다. 조회 종료일이 membership 끝으로 보인다는 이유로 상폐로 바꾸지 않는다. 수집 상한 이후 만기는 달력 부족으로 남는다. 원장 선언과 수집 상한이 어긋나면 요청을 unsupported로 중단한다.
- `data_gap`: 상폐로 추정하지 않고 해당 ID의 유효 상장구간을 제공하지 않는다. 표본은 원래 모집단에 남고 라벨은 `listing_status_unknown`이며 별도 경고를 남긴다.
- 알 수 없는 end_reason은 unsupported다. security_span에 해당 ID 근거가 없으면 pure evaluator의 unknown 상태로 남긴다.

### 중간 가격 조정 공백

기존 `price.adj_close` 원장 계약은 가린 조정 공백을 사이에 둔 두 시점 비교도 금지한다.
후속 구현은 기존 raw 조회의 `CellKind.MASKED`를
`AdjustmentGapObservation(session, security_id)`로 변환해 `SignalLabelData.adjustment_gaps`에
담는다. 원장 MASKED 술어를 복제하지 않고, 공백은 가격과 별도로 순수 evaluator에 전달한다.

각 라벨은 해당 종목의 `[entry_session, label_end]` 양끝 포함 구간에 공백이 있으면
`adjustment_gap`·수익 `None`이다. 공백이 없는 horizon·다른 종목은 정상 평가한다.
달력 부족·미만기·상장근거/종료/상장 전 상태를 먼저 판정하며, 당시 모집단 분모와 상태별
건수를 보존한다. 원주가·0·전일가 대체나 사후 종목 제외는 하지 않는다.
상세 계약과 첫 실자료 기술 확인 사양은 [개별 조정 공백 사양](S03-adjustment-gaps.md)이다.

과거 runner는 조회 범위 안 MASKED 하나라도 있으면 전체 요청을 `unsupported`로 중단했다.
아래 과거 검증 기록은 당시의 구현 증거이며, 현재 개별 라벨 판정의 통과 증거로 재사용하지 않는다.

`LabelDataLoad`와 `SignalResearchOutcome`은 READY+값과 실패+사유를 구분하며 값 없는 성공·사유 없는 실패·값을 가진 실패·알 수 없는 상태를 거절한다. 다른 원천이나 mock으로 복구하지 않는다. CLI 실패는 종료 코드 2와 한글 사유를 남기며 성공 보고서를 생성하지 않는다. Python traceback이 필요한 내부 버그까지 성공 결과로 바꾸지는 않는다.

## 실행과 내보내기

아래 경로는 사용 예시다. 실제 실행은 현재 코드·계약으로 새 S01 사본을 검증하고 결과 전 로컬 요청 JSON·hash·열람 기록을 남긴 뒤 수행한다. 첫 실자료 실행은 아직 하지 않았다.

```sh
uv run --no-sync --project backend python -m strategy_workbench.bootstrap.signal_research \
  --preserved-equity-root /absolute/path/research-snapshots/study-001 \
  --repo-root "$PWD" \
  --strategy-json /absolute/path/selected-strategy.json \
  --environment-json /absolute/path/signal-environment.json \
  --factor-id selected-factor \
  --horizon-sessions 5 21 63 \
  --entry-delay-sessions 0 \
  --minimum-pairs 3 \
  --evaluation-as-of 2024-06-28 \
  --label-calendar-through 2024-09-30 \
  --output-json /absolute/path/evidence/study-001.json
```

`strategy-json`은 기존 작성용 StrategySpec JSON 문서다(identity 봉투를 추가하지 않는다). `environment-json`은 기존 RunEnvironment JSON이며 start/end는 **신호 평가 창**이다. 문서의 중복 JSON 키와 RunEnvironment 밖의 필드를 거절한다. `factor-id`는 전략 안의 ID만 받고 여러 번 지정할 수 있다. 기간은 명시 인자이므로 5/21/63 중 좋은 결과를 자동 선택하지 않는다.

성공 JSON에는 다음을 함께 담는다.

- S01 검증 ledger ID·계약 원문 hash·descriptor hash·저장소 기준 상대 보존 경로
- 전략 hash, 전체 StrategySpec·RunEnvironment, cutoff·달력 끝·라벨 명세·팩터 목록
- 기존 파이프라인의 평가된 신호와 TargetTape, 데이터 경고
- 실제 읽은 가격·공개일·공통 달력, 원래 상장구간·종료 근거, 개별 조정 공백 관측
- 종목·신호일·horizon별 신호 원값·신호 상태·라벨 첫/끝 세션·수익·상태
- 날짜·horizon별 Rank IC, 기존 모집단 수·유효 쌍 수·coverage·상태별 건수·제외 사유
- 현금배당·거래비용·계좌 수익·통계 추론을 포함하지 않는다는 한계

`output-json -`는 표준 출력이다. 파일 출력은 새 경로만 허용하고 같은 디렉터리의 임시 파일을 완성한 뒤 원자적으로 공개한다. 기존 결과를 덮어쓰지 않는다. 데이터 사본을 이동하거나 수정하는 명령은 제공하지 않는다.

## 증거

아래 runner 검증 당시 데이터 읽기는 테스트가 만든 tmp 합성 원장에 한정했다. 실제 원장·브로커·시크릿·네트워크·실데이터 성과에는 접근하거나 실행하지 않았다. 과거 봉인 구간은 거절 테스트의 날짜 상수 외에 열람하지 않았다.

검증은 다음 파일이 소유한다.

- `backend/tests/integration/test_signal_research_runner.py`: 실제 S01 create→verify→DuckDB→기존 factor pipeline→CLI JSON, 미보존/변조 root 차단, 쿼리 끝과 실제 span 구분, coverage_gap/data_gap/delisted, 중간 MASKED, 정지 기준가 제외, 조정종가 원천 부재, 한글·공백 경로.
- `backend/tests/application/test_signal_research_service.py`: 원천/필드/TargetTape snapshot 불일치, 검증 근거 변경, 실패 값, 결과 불가능 조합, 빈 유니버스, 연구 하한·cutoff·잘못된 팩터·달력 입력 차단.

CLI 손계산 예제의 2024-01-02 삼성전자 합성 값 72,000과 다음 세션 72,500은 가격수익 `72,500/72,000−1`로 비교된다. 같은 날짜 두 종목에서는 큰 가격의 비율 상승이 작아 원값 순위와 가격수익 순위 상관이 −1이다. 이 값은 합성 입력의 계산 정답이며 실데이터 예측력이 아니다.

```sh
uv run --no-sync --project backend pytest \
  backend/tests/application/test_signal_research_service.py \
  backend/tests/integration/test_signal_research_runner.py \
  backend/tests/domain/test_signal_evaluation.py \
  backend/tests/integration/test_signal_evaluation_pipeline.py \
  backend/tests/architecture -q
```

2026-10-16 위 묶음은 **64 passed, 0 skipped**다(runner 새 25 + S03 순수/연결 21 + architecture 18). 새 범위 ruff·pyright도 오류 0으로 확인했다. 위 묶음에 기존 DuckDB·S01 보존 연결·truthful pipeline 회귀를 더한 최종 검증도 **195 passed, 0 skipped**이며 의존 라이브러리 DeprecationWarning 1건이다.

같은 날 실행 코드 바인딩 회귀를 추가한 runner 단독 검증은 **18 passed, 0 skipped**다.
다른 임시 checkout의 evaluator가 `999.0`을 반환하도록 바꾸고 그 코드로 S01 보존·검증에 성공해도,
기존 checkout을 import한 CLI는 종료 코드 2로 중단하고 성공 JSON을 출력하지 않음을 확인했다.
검증 checkout을 `sys.path`에 추가해도 같은 결과이며, 정상 최상위 패키지 아래의 모듈 출처 혼용,
하위 패키지 검색 경로 오염을 거절하고 한글·공백 symlink 저장소 경로는 정상 실행한다.

## 다음 결정

이번 조각으로 보존 입력에서 재현 가능한 가격 예측 비교를 **호출·내보낼 경로**를 마련했다. S03 실제 연구 완료나 가설 채택으로 승격하지 않는다.

다음에는 개별 조정 공백 구현의 검증 결과를 확인하고, 같은 코드·환경으로 별도 새 S01 사본을 준비한다. [첫 실자료 기술 확인 사양](S03-adjustment-gaps.md#다음-결정-첫-실자료-기술-확인-초안)에 따라 결과 전 로컬 요청 JSON·hash·열람 기록을 고정한 뒤 1회 기술 경로와 coverage·상태를 서술한다. 배당 총수익·상폐 회수금·비용 후 계좌 성과·통계 추론은 별도 연구이며 이번 기술 확인에서 전략 채택이나 자동매매 투입을 판단하지 않는다.
