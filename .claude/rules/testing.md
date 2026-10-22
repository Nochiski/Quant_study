---
paths:
  - "backend/**/*.py"
  - "backend/rust/**/*.rs"
  - "database/**/*.py"
  - "tools/**/*.py"
---

# Testing Rules

테스트는 **언제 쓰나 · 어떻게 쓰나 · 언제 지우나** 세 기준으로 판단한다. 커버리지 수치는 기준이 아니고
CI도 재지 않는다. 핵심은 **쓸데없는 테스트를 만들지 않는 것**이다 — 실패시킬 현실적인 버그를 댈 수 없는
테스트는 실행 시간과 리팩터링 비용만 늘린다. 프론트는 `frontend-testing.md`가 같은 기준을 프론트 경로로 적용한다.

## 언제 쓰나 — 트리거가 있을 때만

"코드를 바꿨으니 테스트를 쓴다"가 아니다. 아래 트리거일 때만 쓴다. 판정은 **바꾼 파일의 경로**로 한다.

1. **로직 위치**의 파일에서 분기(`if`·`match`·예외 경로·반복 종료 조건)나 계산식(반환값·상태를 정하는 식)을
   새로 만들거나 바꿀 때
2. **버그를 고칠 때** — 경로 무관. 버그를 재현하는 실패 테스트를 먼저 쓰고 고친다. 가장 가치가 높은 테스트다
3. **변환 경계 위치**의 파일에서 변환 규칙을 바꿀 때
4. **계약 위치**를 바꿀 때

| 구분 | 위치 |
|---|---|
| 로직 위치 | `backend/src/strategy_workbench/domain/**` · `application/**` · `backend/src/backtest_engine/{engine,data}/**`·`sizing.py` · `backend/rust/backtest_core/**`(Rust 단위 테스트) · `database/src/**`·`database/survey/**`의 적재·판정·집계 · `tools/quant_study_dev/**`의 CI 검사기 · `database/scripts/check_*.py`처럼 CI가 돌리는 검사 스크립트 |
| 변환 경계 위치 | YAML/JSON ↔ `StrategySpec`(`adapters/outbound/document_codec/**`) · 저장 행 ↔ 도메인과 스키마 마이그레이션(`adapters/outbound/*_sqlite/**`) · 원장 SQL 조립·셀 판정(`adapters/outbound/equity_duckdb/**`) · HTTP DTO ↔ 도메인(`adapters/inbound/http_api/_*_contract.py`) · 바 로더 정규화(`backtest_engine/adapters/**`) · run worker 프로토콜 직렬화(`adapters/outbound/run_worker_process/**`) · Python ↔ Rust 경계(`engine/wire.py`·`tape.py`) |
| 계약 위치 | 포트(`backtest_engine/ports/**`, application의 outgoing port) — 구현이 둘 이상이면 `backend/tests/contract/` 공통 스위트로 · HTTP 상태 코드·오류 코드·진단 메시지 키(`error-messages.md`) · 은퇴 버전 문서·revision 판독(`frozen_revision_rows.py`) · 공유 fixture의 owner 계약(yaml12 manifest, runtime schema) · 레지스트리 ↔ 대응표(`backend/FACTORS.md` ↔ `EQUITY_FIELD_MAP`) |

**쓰지 않는 것** — 위 경로 안이라도 아래만 바꿨으면 테스트를 쓰지 않는다(통합 테스트·타입 검사·생성물에 맡긴다):

- `adapters/inbound/http_api/**` 라우트의 배선(의존성 주입·facade 호출·응답 변환 호출) — 상태 코드·오류 코드를
  바꾸면 계약이다. 라우트에 분기·계산이 생기면 application으로 옮기고 거기서 트리거 1로 테스트한다
- HTTP 응답 모델의 필드 추가·이름 — wire 필드 계약은 `openapi.json` 재생성과 frontend 생성 SDK
  (`git diff --exit-code` CI step)가 지킨다
- `bootstrap/**`의 조립(어떤 구현을 어느 포트에 꽂나) · `__init__.py` re-export · 설정값·상수 ·
  분기 없는 위임 함수 · 로그·메시지 문구
- `backend/scripts/bench_*.py` 같은 측정·일회성 스크립트

트리거에 해당하면 테스트를 쓴다. 생략할 수 있는 사유는 "서버 원장·실계좌가 있어야 재현돼 CI에서 돌릴 수 없음"
하나이고, 그때는 PR 테스트 계획에 수동 검증 결과(실데이터 확인 기록)를 적는다.

## 어떻게 쓰나

- **행동을 검증한다** — 반환값, 상태 변화, 외부에 보이는 결과(응답·저장된 값·체결·경고)를 단언한다.
  "몇 번 호출됐나"(`assert_called_once` 류)는 그 호출 자체가 외부 결과일 때(공급자 요청·주문 송신 등)만 쓴다.
- **깨뜨릴 현실적인 버그를 댈 수 있어야 한다** — "이 테스트를 실패시키는 현실적인 코드 변경이 뭔가?"에 답하지
  못하면 쓰지 않는다. 퀀트 코드에서는 off-by-one 날짜, look-ahead(공개일·lag 무시), 분모 누락(당시 유니버스),
  결측을 0으로 채우기, 부호·단위 혼동이 대표 답이다.
- **어디에 두나** — 지금 배치를 따른다: backend 단위는 `backend/tests/`의 대상 계층 폴더(`domain/`·`application/`)나
  `test_adapters_<어댑터>.py`, 포트 공통 스위트는 `contract/`, 여러 계층을 엮는 것은 `integration/`, 의존 방향·import 가드는
  `architecture/`, Python ↔ Rust 동치는 `parity/`. database는 `database/tests/`, 검사기는 `tools/tests/`.
- **레벨** — 순수 로직은 단위, 조립·연결은 통합. 같은 행동을 두 레벨에서 겹쳐 검증하지 않는다.
- **의존성은 real > fake > mock** — 아래 §테스트 대역 표를 따른다.
- **케이스 수** — 개수가 아니라 "의미 있는 입력 범주 + 경계값"만. 같은 경로를 값만 바꿔 반복하지 않는다.
  `parametrize` 한 줄 한 줄이 서로 다른 분기·경계를 지나야 한다.
- **이름** — 이름만 읽어도 보장하는 행동이 보이게: `test_fill_uses_next_open_not_signal_close`.
- **성질이 있는 계산은 성질로** — 변환·누적 계산은 기대값을 손으로 나열하는 대신 "역변환하면 원래 값", "비중 합은 1",
  "현금 + 평가액 = 자산" 같은 성질을 단언한다. 새 의존성 없이 고정 seed 난수 입력을 `parametrize`로 돌린다.
  손으로 계산할 수 있는 소형 시계열의 알려진 정답 회귀는 그대로 쓴다.

## 언제 지우나 — 지우는 것도 정상적인 개선이다

아래 테스트는 지워도 되고, 지우는 것을 **정상적인 개선**으로 본다. 기능 변경과 섞지 않고 별도 커밋(또는 별도 PR)으로 지운다.

- mock만 검증하고 실제 코드는 거의 거치지 않는 테스트 (mock이 돌려준 값을 그대로 단언)
- 구현을 리팩터링하면 행동이 같아도 깨지는 테스트 (내부 호출 순서·private 속성에 결합)
- 다른 테스트와 같은 것을 검증하는 중복 테스트 — 같은 입력 조건을 같거나 더 엄격한 단언으로 보는 테스트를
  **댈 수 있을 때**
- 단언이 없거나 "예외 없이 돈다"·"None이 아니다"·"타입이 맞다"만 보는 테스트 — 타입 검사(pyright)가 이미 잡는다
- 운영 코드 어디서도 쓰지 않는 죽은 코드만 검증하는 테스트 — 코드와 함께 지운다

지우지 않는 것:

- **실패 중인 테스트** — 원인을 고쳐 통과시킨 뒤에만 삭제를 판단한다. 빨간 테스트를 "중복"이라며 지우는 것은
  게이트 우회다. 예외: 동작을 **의도적으로** 바꿔 옛 동작을 단언하던 테스트가 깨졌으면 새 동작에 맞게 고치고,
  그 동작 자체가 없어졌으면 함께 지운다(PR에 어떤 동작을 바꿨는지 적는다)
- **외부 계약을 고정하는 테스트** — 오류 코드·저장 문서 판독·원장 셀 종류·SSE 이벤트 순서 단언은 필드를 나열해도
  구현 결합이 아니다
- **정적 가드** — `tests/architecture/`의 의존 방향·금지 import 검사는 코드를 실행하지 않아 뮤테이션으로 잴 수
  없지만 존재 이유가 다르다

지울 때는 PR에 "대신 잡는 테스트" 또는 "지워도 되는 이유"를 적는다. 판단이 애매하면 대상 코드 한 줄을 일부러
망가뜨려(비교 연산 뒤집기, 조건 부정, 반환값 `None`) 그 테스트가 실패하는지 본다 — **표본 뮤테이션**. 덮는 줄의
변이를 하나도 잡지 못하는 테스트는 삭제 후보다.

## 테스트 대역: real > fake > mock

신규·수정 테스트에 적용한다. 기존 테스트는 이 절을 이유로 일괄 수정하지 않는다.

| 단계 | 정의 | 다음 단계로 내려가는 경우 |
|---|---|---|
| real | 실제 객체에 테스트 데이터를 넣어 돌린다(인메모리 SQLite, `make_stage_tree`가 만든 가짜 stage 트리, 소형 KRX fixture) | 서버 원장·네트워크·실공급자가 필요해 테스트에서 돌릴 수 없다 |
| fake | 같은 포트를 인메모리로 구현한 작은 대역(예: `equity_mock`, `llm_scripted`, `fake_broker`). 성공·실패 경로 모두 real과 같게 동작한다 | 한 번만 쓰는 경계라 fake를 만들고 유지하는 비용이 더 크다 |
| mock | `unittest.mock.patch`·`MagicMock`·`monkeypatch.setattr`로 함수·메서드를 대체한다 | 최후 수단 |

- 포트가 있는 경계는 포트 구현(fake)을 주입하지, 어댑터 메서드를 patch하지 않는다.
- real과 계약이 다른 fake는 대역이 아니라 결함이다. 반환값만 흉내 내고 예외·상태 변화를 빠뜨리면 mock과 같다.
  fake가 포트 계약을 지키는지는 `tests/contract/` 공통 스위트에 fake도 넣어 확인한다.
- mock을 쓰면 그 자리에 real·fake가 맞지 않는 이유를 주석 한 줄로 남긴다.
  예: `# mock: 공급자 SDK의 네트워크 타임아웃 주입 1회라 fake 불요`.
- 테스트는 프로세스 상태를 받은 그대로 돌려준다. 모듈 속성·환경 변수·cwd를 바꿨으면 `monkeypatch`로 되돌린다.
  실행 순서·xdist 배정에 따라 결과가 갈리면 결함이다.

## 테스트의 역할

테스트는 **코드 동작**을 검증하는 용도다. 산출물(artifact) 파일의 존재나 내용을 검증하는 테스트는 작성하지 않는다.

### 금지

- 수집한 시세 CSV/parquet, 백테스트 결과 JSON, 리포트 파일 같은 **데이터/산출물 파일이 디스크에 존재하는지** 확인하는 테스트
- 산출물 파일을 로드해서 그 내부 구조(특정 컬럼 값, 행 수, 특정 종목 포함 여부 등)를 단언하는 테스트
- 예: `assert path.exists()`, `assert "005930" in universe_json["tickers"]`, `assert len(df) == 1234`

이런 테스트는 데이터가 갱신·리네이밍·이동될 때마다 깨지고, 사용자 작업물을 CI가 강제하는 형태가 되어 정리/리팩토링을 방해한다.

### 허용

- 함수/클래스의 동작 검증 (입력 → 출력) — 지표 계산, 시그널 생성, 포지션 사이징, 손익 집계 등
- 스키마·검증 로직 자체에 대한 테스트 (인라인 픽스처/임시 파일 사용)
- 테스트 안에서 **직접 만든** 임시 데이터프레임/파일을 입력으로 주는 테스트
- 알려진 정답이 있는 계산의 회귀 테스트 (손으로 계산 가능한 소형 시계열 픽스처)

## 런타임 상태 경로 격리 (backend)

테스트가 개발자 로컬의 런타임 상태(`backend/.local/`의 strategy·assistant DB, 백테스트 산출물,
OS 사용자 설정 디렉터리의 공급자 비밀)를 열면 실제 대화 이력 DB가 업그레이드되어 브랜치를 바꾼
뒤 옛 코드의 테스트가 깨진다(#211, #215).

- `backend/tests/conftest.py`의 autouse fixture `isolate_runtime_state_paths`(와 session 범위
  짝)가 기본 경로를 테스트마다 tmp로 옮기고 런타임 설정 환경 변수를 지운다. 경로가 필요한
  테스트는 이 fixture 값을 받아 쓴다.
- `backend/tests/runtime_path_guard.py`의 감사 훅이 이 프로세스가 감시 경로를 열면 그 테스트를
  실패시킨다. 하위 프로세스와 네이티브 라이브러리가 C 코드에서 직접 여는 파일(in-memory 연결의
  SQLite `ATTACH`, `duckdb.connect(path)`)은 보지 못한다. 그런 경로를 쓰는 테스트는 가드에 기대지
  말고 경로를 직접 격리한다. 예: run worker 통합 테스트(`tests/integration/test_run_worker_process.py`)는
  하위 프로세스가 쓰는 산출물 루트·연구 DB를 tmp로 명시한다.
- 프로세스 전송(`ProcessRunExecution`)은 만들 때 예비 worker를 띄운다. 같은 conftest의 autouse fixture
  `_close_process_run_executions`가 테스트마다 만든 전송을 끝에 닫아, lifespan을 거치지 않는 앱이 남긴
  유휴 worker가 쌓이지 않게 한다.

## 산출물 검증이 필요하다면

테스트가 아니라 **별도 validator 스크립트**나 **lint 단계**로 옮긴다 — 데이터 품질 검증과 코드 동작 검증을 분리하자.

## 위반 발견 시

산출물 파일에 의존하는 테스트가 CI를 깨뜨리면, 테스트를 고치지 말고 **삭제**한다. 데이터 변경이 코드 회귀를 의미하지 않기 때문이다.
