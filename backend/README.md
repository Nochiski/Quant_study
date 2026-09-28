# Strategy Workbench Backend

## 전략 문서 authoring (schema 1.2)

`application.strategy_authoring`이 YAML/JSON 원문의 compile·저장·초안을 맡는다. 원문의 실행 의미는
`domain.strategy`의 `StrategySpec`(현재 schema `1.2`, `CURRENT_SCHEMA_VERSION`)이 소유한다. schema 1.2
문서에는 `data`·`execution`이 없다. 시장·기간·유니버스·체결·비용은 실행 요청의 `RunEnvironment`
(`domain.backtest`)가 받는다. 원문 파싱은 outbound `document_codec` adapter가 한다. 1.0·1.1 원문 업그레이드는
`domain.strategy`의 dict 변환(`_upgrade.py`)이 의미를 정하고 `document_codec`이 주석·순서를 보존해 원문을
다시 쓴다. 두 결과가 어긋나면 `strategy_document.upgrade_drift`로 거절한다.

- `GET /api/v1/strategy-documents/schema`: 작성 문서의 런타임 JSON Schema(ETag = 스키마 해시)
- `GET /api/v1/strategy-documents/contract`: 필드별 작성 계약(타입·enum·범위·단위·기본값·예시)과 팩터·데이터셋
  registry 버전
- `GET /api/v1/strategy-documents/operators`: 그래프 노드 연산자 카탈로그. 팔레트·노드 라벨의 유일한 출처
- `POST /api/v1/strategy-documents/compile`: 원문을 compile해 syntax·structural·semantic 진단을 돌려준다.
  error 진단이 있으면 `spec`·`spec_hash`가 null이다.
- `POST /api/v1/strategy-documents/upgrade`: 은퇴한 schema 원문을 현재 버전으로 다시 쓰고 compile한다.
- `POST /api/v1/strategy-documents`, `POST /api/v1/strategy-documents/{strategy_id}/revisions`: 깨끗하게
  compile된 원문을 새 전략의 revision 1 또는 다음 revision으로 저장한다. `expected_revision`이 낡으면 409다.
- `GET /api/v1/strategies/{strategy_id}/revisions/{revision}/document`: 저장된 revision의 원문
- `GET`·`PUT`·`DELETE /api/v1/strategy-drafts/{draft_id}`: 편집 중 초안의 서버 저장·복구·삭제

## AI 어시스턴트

`application.assistant_chat`이 공급자 프로파일, 채팅 세션·턴, 전략 제안과 백테스트 결과 설명을 맡는다.
공급자 SDK(`anthropic`, `openai`)는 optional extra `llm`(`uv sync --extra llm`)이며 `adapters/outbound/llm_*`
밖에서 import하지 않는다(architecture 테스트). extra가 없어도 서버는 뜨고 공급자는 "설치 필요"로 보인다.
세션 이력은 `assistant_sqlite`, API 키는 `secrets_local`이 저장한다. 계약 정본은
[AI 어시스턴트 설계 spec](../docs/superpowers/specs/2026-09-20-ai-assistant-design.md)과
[결과 설명 spec](../docs/superpowers/specs/2026-09-27-ai-backtest-result-explain.md)이다.

- `GET`·`POST /api/v1/assistant/providers`: 공급자 종류와 등록된 프로파일(키는 꼬리 4자리만), 등록.
  연결 테스트를 통과한 프로파일만 저장된다.
- `DELETE /api/v1/assistant/providers/{profile_id}`, `POST .../{profile_id}/activate`·`.../test`: 삭제(키 포함),
  활성 전환, 저장된 키로 연결 재확인
- `GET`·`POST /api/v1/assistant/sessions`, `GET .../{session_id}`: 문서 하나 또는 백테스트 실행 하나의 세션
  목록·생성, 메시지·턴·이벤트 이력 복구
- `POST .../{session_id}/turns`: 턴 시작(202). 이벤트는 `GET .../{session_id}/events` SSE로 읽고
  `Last-Event-ID`로 재개한다. `POST .../turns/{turn_id}/cancel`로 취소한다.

## M5 Single backtest + analytics

`domain.analytics`의 `metric-registry-v1`이 기존 8개 성과 지표와 MDD 기간/회복,
benchmark/excess return, 거래·노출·비용을 합친 21개 정의와 공식을 소유한다.
`application.backtest_run`은 immutable `BacktestRunSpec`을 TargetTape로 컴파일하고 교체 가능한
data/executor/artifact port만 호출한다. 기본 조립은 Equity mock → `TargetTapeStrategy` →
Persistent Rust Engine → atomic local JSON artifact이며 Python reference core도 같은 계약으로 남긴다.

- `GET /api/v1/run-environments/schema`: 실행 설정(`RunEnvironment`)의 런타임 JSON Schema(ETag = 스키마
  해시). frontend 실행 설정 패널이 필드 목록·기본값·범위를 여기서 읽는다.
- `POST /api/v1/backtests`: 실행 설정 `environment`(필수, 없으면 422 `backtest.run.environment_required`),
  Rust/Python core, 초기 자본, benchmark, metric scope와 함께 실행 시작. 사전 검사만 하고 run id를 바로
  돌려주며 TargetTape는 run의 `tape` 단계에서 만든다. `strategy_source`로 저장 revision(`saved_revision`,
  spec_hash 대조) 또는 inline draft를 지정하고(옛 schema revision이면 422
  `backtest.strategy.requires_upgrade`) manifest의 `strategy_provenance`에 출처를 기록 (기존 `strategy` inline도 유지). 실제
  `backtest.run.invalid`·`portfolio.*` 422와 saved-reference 404/409는 OpenAPI/generated SDK의
  discriminated error 계약으로 함께 제공한다.
- `GET /api/v1/backtests`: 실행 이력 목록
- `GET /api/v1/backtests/{run_id}`: 상태·진행률·artifact hash 조회
- `GET /api/v1/backtests/{run_id}/request`: 서버가 수락한 실행 요청. 감사와 같은 조건 재실행에 쓴다
- `GET /api/v1/backtests/{run_id}/events`: SSE progress stream. 진행률은 `tape` 2~80%(원시 로딩·팩터
  평가·TargetTape 컴파일), `data` 82%, `engine` 84~92%, `artifact` 93% 구간이다. `tape` 안에서는 선택
  능력 `ProgressReportingRawObservationPort`(duckdb 구현)의 로딩 진행, 계약 검증·팩터 입력 변환·
  포트폴리오 관측 변환 루프, 팩터 평가기(노드 종류별 가중치, 시계열은 관측 단위), TargetTape 컴파일
  진행이 1% 이상 오를 때마다 이벤트가 된다. 구간 폭은 실데이터 4년 구간 실측 시간 비율을 따른다
  (이슈 #162).
- `GET /api/v1/backtests/{run_id}/result`: versioned metrics, 차트 series, raw artifact, manifest 조회
- `POST /api/v1/backtests/{run_id}/cancel`: 협력적 취소 요청

실데이터는 `BacktestDataPort` 구현만 바꿔 붙인다(`equity_duckdb`, 아래 의존성 방향 절). domain/application과 engine executor는
OHLCV·universe membership·corporate action의 중립 계약을 유지한다. 로컬 실행 artifact는
`backend/.local/backtest-runs`에 저장되며 git에서 제외된다.

## M4 Portfolio pipeline + TargetTape

`domain.portfolio`가 PIT eligibility, 합성 score/rank/regime, long-only/long-short 선택,
equal/factor-score/rank/risk weight, exposure cap·neutralization, turnover/liquidity와 리밸런싱
달력을 소유한다. `application.portfolio_design`은 교체 가능한 observation/engine port만 알고,
결과를 snapshot/spec/tape hash와 T 종가→T+1 실행일이 고정된 immutable `TargetTape`로 반환한다.

- `POST /api/v1/portfolio/preview`: 세션별 후보, 편입·제외 이유, 목표 비중과 engine compatibility
- `POST /api/v1/strategies/debug/trace`: saved revision 또는 inline draft의 date/security/factor/node를
  제한해 raw·node·TargetTape projection과 `spec_hash/snapshot_id/registry_version/plan_hash`를 반환.
  trace와 executable factor output은 한 번의 node-cache 계산에서 파생되며 페이지·행 cap과
  cancellable raw capability/evaluator/TargetTape materialization·streaming hash의 client-disconnect
  협력적 취소를 적용. 기존 `RawObservationPort.load_raw_observations(query)`는 preview/backtest
  호환 계약으로 유지하고 trace는 별도 `CancellableRawObservationPort`를 계산 전에 협상한다.
  비세션 날짜와 snapshot에 없는 종목, 모든 StrategySpec numeric leaf의 non-finite 값과 유한
  피연산자의 산술 overflow는 빈 성공값·NaN tape 대신 coded 422로 실패한다. 404/409/422/499
  envelope, 요청 cap과 `trace.capability.unsupported` 진단은 OpenAPI/generated SDK에 명시된다.
  Raw port의 모든 숫자와 opening book은 계산 전 finite 계약을 통과해야 하고, starting holding은
  domain compiler가 TargetTape와 공유하는 실제 첫 signal frame에 존재할 때만 적용된다.
- `equity_mock`: portfolio observation port를 구현하는 deterministic adapter(기본). 실데이터는
  `equity_duckdb`가 같은 port를 구현한다.
- `engine_portfolio`: `StrategyRequirements`를 사전 협상하고 `SetPortfolioTarget(REPLACE)`로 변환

## M3 Factor research surface

`domain.factor`가 50개 factor ID, 표현식 타입, validation, PIT execution plan, hash/cache
fingerprint, evaluator와 전문 진단 지표의 단일 소유자다. `application.factor_research`는 다음 HTTP
use case를 제공한다. outgoing port는 기본 `equity_mock`이, 실데이터에서는 `equity_duckdb`가 구현한다.

- `GET /api/v1/factors/catalog`: 50개 versioned 정의 검색·카테고리·구현 상태 필터
- `POST /api/v1/factors/validate`: cycle/type/unit/min-history/reference/missing-policy 검증
- `POST /api/v1/factors/explain`: topological PIT plan과 재현성 hash 설명
- `POST /api/v1/factors/preview`: IC, Rank IC, quantile spread, coverage, turnover, decay

사람이 검토하는 전체 ID/Equity field mapping은 [FACTORS.md](./FACTORS.md)에 있다. 실행 의미의
SoT는 항상 registry 코드이며 문서 정합성은 테스트로 고정한다.

전략 생성·팩터 계산·실험 관리 API가 들어갈 백엔드 애플리케이션이다. 기존
`backend/src/backtest_engine`과 `backend/rust/backtest_core`는 이 애플리케이션이 사용하는 실행 커널이며,
백엔드의 도메인·유스케이스·저장소 책임과 섞지 않는다.

## 의존성 방향

```text
adapters/inbound  ─┐
                   ├─> application ─> domain
adapters/outbound ─┘          │
                              └─> application/*/ports/outgoing

bootstrap ─> application + adapters
```

- `domain/`: 프레임워크·DB·HTTP·백테스트 엔진을 모르는 순수 정책과 값 타입.
- `application/`: 사용자 유스케이스와 필요한 outbound port. concrete adapter를 모른다.
- `adapters/inbound/`: FastAPI/OpenAPI 같은 입력 변환. business rule은 두지 않는다.
- `adapters/outbound/`: Equity DB, mock, 실행 엔진, 결과 저장소 구현.
- `bootstrap/`: concrete 구현을 고르는 유일한 composition root.
- 각 책임 노드는 `facade/<책임>.py`로만 외부 심볼을 노출하고
  `facade/__init__.py`에 허용 의존성 `DEPENDS_ON`을 선언한다.

`equity_mock`이 기준 adapter이고, equity 층(`database/docs/EQUITY_DESIGN.md` §7 소비자 계약)을 읽는
`equity_duckdb` adapter가 같은 application port를 구현한다 —
`build_container(equity_adapter="duckdb", equity_root=<data/equity>)`, optional extra
`equity`(`uv sync --extra equity`, duckdb). 답하는 field_id의 정본은 선언표
`adapters/outbound/equity_duckdb/_specs.py`(`FIELD_SPECS`)이고 지금 30개다(price 7·financial 8·consensus 6·
flow 3·short 2·credit 1·event 3). 새 필드는 이 표에 한 행을 더한다. 표에 없는 field_id는 `list_fields()`
밖이고 질의하면 `INVALID_QUERY`이며, 사유는 `UNSUPPORTED_FIELDS`가 적는다. 필드별 공개 랙의 정본은 원장
`dataset_profile`의 `recommended_lag_sessions`이고 adapter가 부팅할 때 읽는다. 표가 없거나 행이 빠진
필드는 `_specs.py`의 폴백 랙(`SourceSpec.lag_sessions`·`FieldSpec.lag_sessions`)으로 읽는데, 이 값은
원장 선언의 사본이고 `tests/contract/test_equity_fallback_lag.py`가 원장 선언과 대조한다. 폴백을 쓰면
부팅 로그에 `profile_lag_fallback` 경고가 한 번 남고, 해법은 `ledger_sync`로 `dataset_profile`을
동기화하는 것이다(#255). domain/application은 수정하지 않는다. adapter 자체는 mock으로 조용히
fallback하지 않고 bootstrap에서 명시적으로 고른다.

## 현재 골격

```text
backend/
├─ src/backtest_engine/                    # Python reference/public engine
├─ rust/backtest_core/                     # Persistent Rust core
├─ tests/                                  # engine + workbench + architecture
├─ examples/ · scripts/ · benchmarks/
├─ tools/                                  # 런타임 schema·어시스턴트 대본/프롬프트 export 도구
├─ ops/                                    # 원장 → 공유용 Parquet 리빌드(rebuild_share.py, 서버에서 실행)
├─ reference/                              # 2026-08-17 Zipline 관찰 아카이브
└─ src/strategy_workbench/
   ├─ domain/equity/                       # PIT 데이터 값 타입과 query/result
   │  └─ facade/research_data.py
   ├─ domain/strategy/                     # StrategySpec(schema 1.2), hash, validation, explanation, 업그레이더
   ├─ domain/factor/                       # 팩터 registry·표현식·연산자·PIT plan·evaluator
   ├─ domain/portfolio/                    # TargetTape compiler와 portfolio policy
   ├─ domain/analytics/                    # 21개 versioned metric 정의·공식
   ├─ domain/backtest/                     # RunEnvironment, run/manifest/raw artifact 계약
   ├─ domain/assistant/                    # 어시스턴트 순수 값 타입(메시지·이벤트·도구·제안 검증 결과)
   ├─ application/equity_workspace/        # 검색 catalog·universe/panel PIT preview
   │  ├─ ports/outgoing/equity_data.py     # EquityDataPort
   │  └─ facade/{ports,workspace}.py
   ├─ application/strategy_design/         # create/get/revise/validate/explain
   │  └─ ports/outgoing/strategy_repository.py
   ├─ application/strategy_authoring/      # 문서 compile·저장·업그레이드·서버 초안
   ├─ application/factor_research/         # 팩터 catalog·validate·explain·preview
   ├─ application/portfolio_design/        # portfolio preview·trace use case와 outgoing ports
   ├─ application/backtest_run/            # compile/run/status/result/cancel orchestration
   ├─ application/assistant_chat/          # 공급자 프로파일·세션·턴·결과 설명
   ├─ adapters/inbound/http_api/           # FastAPI/OpenAPI wire adapter
   ├─ adapters/outbound/equity_mock/       # 결정적 in-memory Equity v0.2 mock
   ├─ adapters/outbound/equity_duckdb/     # equity 층 parquet + equity.duckdb 카탈로그
   │  └─ facade/provider.py
   ├─ adapters/outbound/document_codec/    # YAML/JSON 원문 파싱과 업그레이드 원문 재작성
   ├─ adapters/outbound/strategy_memory/   # contract reference/test adapter
   ├─ adapters/outbound/strategy_sqlite/   # durable immutable strategy revisions
   ├─ adapters/outbound/engine_portfolio/  # capability 협상과 target action 변환
   ├─ adapters/outbound/backtest_engine/   # TargetTape → Rust/Python engine
   ├─ adapters/outbound/artifact_local/    # atomic local result commit
   ├─ adapters/outbound/assistant_sqlite/  # 어시스턴트 세션·프로파일 저장
   ├─ adapters/outbound/secrets_local/     # 공급자 API 키 로컬 저장
   ├─ adapters/outbound/llm_anthropic/ · llm_openai/  # 공급자 SDK adapter(extra `llm`)
   ├─ adapters/outbound/llm_scripted/      # e2e용 대본 공급자
   └─ bootstrap/                           # adapter 조립
      └─ facade/{container,http,server}.py
```

테스트 실행:

```powershell
cd backend
uv sync --extra parquet --extra equity --extra llm
uv run maturin develop --manifest-path rust/backtest_core/Cargo.toml --release
uv run pytest -q
uv run ruff check src tests examples scripts tools
uv run pyright
uv run server
uv run python scripts/export_openapi.py openapi.json
```

extra 목록과 ruff 대상은 CI(`.github/workflows/ci.yml` backend job)와 같다. CI의 `backend-no-extras` job은
extra 없이 어시스턴트 계약 테스트와 architecture 테스트를 따로 돌린다. `database/tests`는 저장소 루트에서
`uv run --project backend pytest database/tests -q`로 돈다.

`uv run server`의 전략 revision 저장소는 기본적으로 `.local/strategy-revisions.sqlite3`이며
프로세스를 재시작해도 원문·hash·provenance를 복원한다. 배포별 저장 위치는
`STRATEGY_WORKBENCH_DB_PATH` 환경 변수로 지정할 수 있다. 기본 Workbench run은 `rust` core를
선택하므로 실제 백테스트와 browser E2E 전에는 위 확장을 설치한다. 확장이 없을 때는 성능·실행
의미를 숨기는 Python fallback 대신 `CoreUnavailable`로 실패한다.

브라우저가 이 서버를 부를 수 있는 origin 은 `STRATEGY_WORKBENCH_ALLOWED_ORIGINS`(쉼표로 구분)로
정한다. 설정하지 않으면 개발 서버 하나(`http://localhost:5173`)다. 워크트리별로 preview 포트를
옮겨 e2e 를 돌릴 때는 그 origin 을 여기에 함께 넘겨야 한다 — 목록에 없으면 서버는 정상인데
브라우저 요청만 CORS 로 막혀 화면이 빈다. 값이 있는데 origin 이 하나도 없으면 조용히 기본값으로
돌아가지 않고 기동에 실패한다.

서버 환경 변수(`bootstrap/_http.py`):

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `STRATEGY_WORKBENCH_DB_PATH` | `.local/strategy-revisions.sqlite3` | 전략 revision·초안 저장소 |
| `STRATEGY_WORKBENCH_ALLOWED_ORIGINS` | `http://localhost:5173` | CORS 허용 origin(쉼표로 구분) |
| `STRATEGY_WORKBENCH_EQUITY_ADAPTER` | `mock` | `mock` 또는 `duckdb` |
| `STRATEGY_WORKBENCH_EQUITY_ROOT` | 없음 | duckdb adapter가 읽을 equity 층 루트. `duckdb`면 필수 |
| `STRATEGY_WORKBENCH_ASSISTANT_DB_PATH` | `.local/assistant.sqlite3` | 어시스턴트 프로파일·세션 저장소 |
| `STRATEGY_WORKBENCH_ASSISTANT_SECRETS_PATH` | OS 기본 사용자 설정 위치 | 공급자 API 키 파일 |
| `STRATEGY_WORKBENCH_ASSISTANT_ALLOW_INSECURE_BASE_URL` | 꺼짐 | 정확히 `1`일 때만 로컬 프록시용 비보안 base URL 허용 |
| `STRATEGY_WORKBENCH_ASSISTANT_FAKE_PROVIDER` | 꺼짐 | 정확히 `1`일 때만 공급자를 대본 adapter(`llm_scripted`)로 바꾼다. 브라우저 e2e 전용 |

Equity HTTP 계약은 다음 네 경로로 분리한다. mock과 duckdb adapter가 같은 계약을 따른다.

- `GET /api/v1/equity/catalog`: 검색·dataset/unit/frequency 필터·pagination과 snapshot/field capability
- `POST /api/v1/equity/universe/preview`: 과거 시점별 구성 종목과 세션 coverage summary
- `POST /api/v1/equity/panel/preview`: row/column 제한, 비용 추정, PIT cell과 확인이 필요한 위험
- `POST /api/v1/equity/preview`: panel query 하나로 유니버스 이력과 PIT panel을 함께 읽는다

권장 lag보다 짧은 override와 불완전 coverage는 backend가 구조화된 warning으로 판정한다.
확인 전에는 panel cell을 반환하지 않으므로 frontend가 같은 규칙을 복제하지 않는다.
