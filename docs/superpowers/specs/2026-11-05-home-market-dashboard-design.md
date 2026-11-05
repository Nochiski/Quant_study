# 홈 시장 대시보드와 DB 새로고침 — 설계

- 날짜: 2026-11-05
- 요청: "대시보드 개편 — 코스피 지수, 외국인·기관·개인 매도 매수, 섹터맵", "DB 새로고침 버튼"
- 디자인보드: https://claude.ai/artifact/N29ZLJFXnq3hdGpxbBenCJ (안 A 채택, Opus 리뷰 3개 반영본)
- 범위: 홈 화면(`/`)에 시장 블록을 더하고, 홈 머리에서 원장 동기화와 앱 반영을 한 번에 하는 버튼을 둔다.

## 1. 목표와 범위 밖

목표
- 정동민이 홈을 열면 마지막 장 마감 기준으로 지수·투자자별 순매수·업종 등락을 한 화면에서 읽는다.
- 명령어 없이 버튼 하나로 서버의 새 원장을 받고, 받은 데이터가 판본을 섞지 않고 앱에 반영된다.
- 기존 홈의 "이어서 할 일"을 먼저 읽는 약속(US-DM-15)을 지킨다.

범위 밖
- 실시간 시세·장중 갱신. 원장은 장 마감 일별이다.
- 종목 상세 화면. 섹터맵 종목 칸은 링크가 아니다(툴팁만).
- 섹터의 기간(5일·20일) 등락. 섹터맵은 하루 등락 고정이고 캡션이 그렇게 말한다.
- 과거 날짜 고르기. 홈은 원장의 최신 장 하나만 보인다.
- 과거 값 변경(EG5c) 승인을 앱에서 하는 것. 앱은 상태와 안내만 보인다.
- 실험의 데이터 판본 고정 기능. 대신 진행 중·대기 중 실험이 있으면 새로고침을 막는다.

## 2. 데이터 정의 (도메인이 소유한다)

모든 값은 로컬 equity 층의 현재 빌드(MANIFEST `current_build`)에서 읽는다. 계산 규칙은
`domain/market` 순수 함수 하나가 소유하고, 어댑터는 원장 행만 나르고, 화면은 표시만 한다.

### 2.1 기준 장(session)
- 홈의 기준 장 = `index_daily` 코스피의 최신 `date`.
- 카드마다 자기 날짜를 가진다. 머리의 날짜는 카드 날짜 중 가장 이른 것이다. 카드 날짜가 머리와 다르면
  그 카드에 "수급은 11-03까지" 배지를 붙인다.

### 2.2 지수 카드
- 대상: `MARKET_REFERENCE_INDICES`(코스피·코스닥, `domain/backtest`) — 지수 이름의 정본을 재사용한다.
- 값: 기준 장의 `close_idx`·`change_idx`·`fluc_pct`·`value_krw`(원).
- 3개월 선: 기준 장 포함 최근 63세션 종가. 최고·최저는 그 선의 종가 최고·최저("3개월 최고 종가").

### 2.3 투자자별 순매수
- 격자: `flow_daily` × `universe_daily`((date, ticker) 조인), `market ∈ {KOSPI, KOSDAQ}`,
  `sec_type ∉ {etf, preferred}`. 우선주는 원장이 수급을 받지 않아서 뺀다(2026년 measured 0건).
- 주체: 개인(`ind_invsr_krw`)·외국인(`frgnr_invsr_krw`)·기관(`orgn_krw`)·기타법인(`etc_corp_krw`) 네 줄,
  각주로 내외국인(`natfor_krw`). 기관 세부 8주체: 금융투자·연기금등·투신·사모·보험·은행·기타금융·국가.
- 기간: 당일 / 최근 5세션 / 최근 20세션. 세션은 `trading_calendar` 기준. **원값을 합한 뒤** 원 단위로
  내려주고, 억·조 반올림은 화면이 한 번만 한다.
- 측정: 값이 있는(`frgnr_invsr_krw IS NOT NULL`) 셀만 더한다. 0으로 채우지 않는다.
- 커버리지: 기준 장의 격자 셀 중 측정 셀의 **시가총액 비중**(`universe_daily.mktcap_krw`). 0.95 미만이면
  합계를 내지 않고 사유(`coverage_low`)로 답한다. 측정 종목 수/격자 종목 수도 함께 준다.
- 한 줄 요약 문장은 backend가 완성한다("외국인이 1조 4,542억 원어치를 가장 많이 더 팔았고, …").

### 2.4 섹터
- 분류: `sector_snapshot`의 최신 `snapshot_date`(WICS L1·L2, 주 1회).
- 등락: `price_daily` 기준 장의 `close / base_price_krw − 1`. `volume_shr = 0`(거래정지 등)은 등락
  계산에서 뺀다(크기에는 넣는다).
- 가중: 크기와 색 **모두** `float_shares_shr × base_price_krw`(유동주식수 × 전날 종가). 전체 시총·당일 가중은
  쓰지 않는다(리뷰 실측: 제약과생물공학 +2.23% → +0.35%, 다각화된금융 부호 반전).
- 묶음: L2별 `size_weight`(크기), `return_weight`(거래 있는 종목의 가중합), `return_pct`, 종목 수, 등락에서 뺀
  종목 수. L1 묶음도 같다. 화면의 "그 밖 N개" 합치기는 `Σ(return_weight × return_pct) / Σ return_weight`로
  같은 정의를 따른다(화면 entity 순수 함수, 테스트로 고정).
- 날짜 둘: "업종 분류 10-30 스냅샷(N일 전) · 색 10-30 하루 등락".
- 드릴다운: L2 코드 하나의 종목 목록(이름·유동시총·등락·거래 없음 여부), 크기 내림차순.

### 2.5 데이터가 다 있지 않을 때
| 상태 | 응답 | 화면 |
|---|---|---|
| 연습용(mock) 원천 | `status=practice_data` | 시장 숫자 0개, "시장 정보는 실제 데이터를 연결해야 보여요" + 연결 방법 |
| 표 없음(`index_daily` 등 미빌드) | 카드별 `unavailable` + 사유 | 그 카드만 "이 데이터는 아직 받지 않았어요" |
| 커버리지 낮음 | 수급 `coverage_low` | 수급 카드만 사유 문장 |
| 받은 원장이 하나도 없음 | 앱이 duckdb로 뜨지 못한다(지금과 같음) | 범위 밖 — 기존 부팅 오류 안내 |

## 3. DB 새로고침

### 3.1 결정
- 버튼 = `ledger_sync sync`(받기 → 정리하기[카탈로그] → 확인하기[verify]) 후 **앱 반영**.
- 앱 반영 = 프로세스 안에서 **컨테이너 통째 교체**. 새 `build_container`를 세우고 HTTP 앱을 바꿔 끼운
  뒤, 옛 앱의 lifespan 정리(run worker 종료 등)를 부른다. 한 컨테이너 안에서 판본이 섞이지 않는다.
  프로세스 재기동(런처 재시작 루프)은 `--reload` uvicorn·`uv run dev`·직접 실행에서 동작이 갈라져 쓰지 않는다.
- 시작 조건(서버가 판정): 실데이터(duckdb) 원천, `QL_SYNC_HOST`·`QL_SYNC_USER` 설정, 앱이 읽는 루트가
  `<QL_SYNC_ROOT>/equity`와 같음, 다른 sync가 잠금을 잡지 않음, **진행 중 백테스트 0 · 끝나지 않은 실험 0
  (대기 포함) · 진행 중 연속 평가 0**.
- 새로고침 중에는 계산을 시작하는 HTTP 요청(백테스트 시작, 실험 만들기, 연속 평가 시작)을 409
  `data.refreshing`으로 막는다. 그래서 교체 시점에 옛 컨테이너가 쓰는 run이 없다.
- 서버 빌드 시간대는 막지 않고 알린다. 시간대 정본은 `ledger_sync`(`plan --json`의 `in_build_window`).
  받는 도중 서버 판이 바뀌면(종료 코드 3) 한 번 자동으로 다시 받는다.
- 뒤처짐 = 서버 판과 같은가(`plan --json`의 `new_build` 표 수). 홈을 열 때 마지막 확인이 10분보다 오래됐으면
  한 번 확인하고, 버튼을 누를 때 다시 확인한다. 날짜는 달력일 "N일 전"만 쓴다(휴장 달력이 없다).

### 3.2 기존 잠재 결함 수정(같은 PR)
- `EquityDuckdbAdapter._connect`는 질의마다 카탈로그 파일을 새로 연다. sync가 카탈로그를 바꾼 뒤에도 옛
  어댑터가 새 매크로(새 `v=` 경로)를 읽어 판본이 섞인다. 연결 전에 카탈로그 meta의 snapshot이 부팅 때와
  같은지 보고, 다르면 매크로 원천을 `data_not_ready`(앱 반영 대기)로 막는다.

### 3.3 상태
| code | 뜻 | 화면 |
|---|---|---|
| `unavailable` + reason(`practice_data`·`not_configured`·`root_mismatch`) | 누를 수 없음 | 버튼 숨김 또는 비활성 + 이유 |
| `busy_work` | 진행 중 작업 있음 | 비활성 + "진행 중인 백테스트 N건·실험 M개가 끝나야…" |
| `locked` | 다른 곳에서 sync 중 | 비활성 + 시작 시각·경과 |
| `idle` + remote(`behind`/`up_to_date`/`unknown`) | 누를 수 있음 | 배지(경고/회색) |
| `running` + stage(`download`·`catalog`·`verify`·`apply`) | 진행 | 단계·경과, 받기만 바이트 막대 |
| `succeeded` | 반영 끝 | 완료 알림(어느 화면에서든) |
| `failed` + kind(`connect`·`transfer`·`disk`·`mismatch`·`unexpected`) | 아무것도 반영 안 함 | 원인·다음 행동 |
| `partial` + kind(`catalog_gate`·`tables_failed`) | 표 일부만 바뀜 | "확인 필요", 앱 다시 시작 금지 안내 |

화면 문장은 backend가 한글로 완성해 `code`와 함께 보낸다(`.claude/rules/error-messages.md`). 서버 주소·계정은
어떤 문장에도 싣지 않는다.

### 3.4 database 쪽 변경(`ledger_sync`)
- `sync --progress-file PATH`: 단계·받은 표 수/전체·받은 바이트/전체를 JSON으로 원자 교체해 쓴다.
- `last_run.json`에 `error_kind`(`connect`·`transfer`·`disk`·`locked`·`unexpected`)를 더한다.
- `plan --json`에 `in_build_window`를 더한다.
- backend는 database 패키지를 import하지 않고, 래퍼와 같은 명령(`uv run --project backend --with paramiko
  python -m ledger_sync --root <equity_root.parent> --layer equity …`, `PYTHONPATH=database/src`,
  `PYTHONUTF8=1`)을 UTF-8 subprocess로 부른다.

## 4. 구조

### backend
- `domain/market`: 값 타입과 순수 계산(지수 요약, 기간 순매수 합·커버리지·요약 문장, 섹터 가중 묶음).
- `application/market_overview`: `MarketOverviewService`, outgoing `MarketDataPort`(지수 행, 일별·시장별 수급
  합계 행, 섹터 구성 행, 드릴다운 종목 행). mock이면 포트가 없고 서비스가 `practice_data`로 답한다.
- `application/ledger_refresh`: `LedgerRefreshService`(상태 기계·스레드), outgoing `LedgerSyncPort`(plan·sync
  실행·진행 읽기·마지막 결과), `ActiveWorkPort`(진행 중 작업 수), `ApplyDataPort`(앱 반영).
- `adapters/outbound/equity_duckdb`: `MarketDataPort` 구현(같은 노드의 MANIFEST·parquet 규약 재사용).
- `adapters/outbound/ledger_sync_process`: `LedgerSyncPort` 구현(subprocess, 결과 JSON 해석).
- `adapters/inbound/http_api`: `GET /api/v1/market/overview`, `GET /api/v1/market/sectors/{wics_l2_cd}`,
  `GET /api/v1/ledger-refresh`, `POST /api/v1/ledger-refresh/check`, `POST /api/v1/ledger-refresh`, 계산 시작
  라우트의 409 가드.
- `bootstrap`: 바꿔 끼울 수 있는 ASGI 앱(`build_runtime_http_app`이 돌려준다), 새로고침 서비스는 교체를
  넘어 하나로 산다. `ActiveWorkPort`는 현재 컨테이너의 서비스에서 센다.

### frontend
- `shared/ui/signed-value`: 낭독 문구 짝 prop(`direction="flow"` → 순매수/순매도).
- `app/styles/tokens.css`: `--return-up-fill`·`--return-down-fill`·`--text-on-return`.
- `entities/market`: 조회(overview·sector), 숫자 표기(조·억), squarify·작은 칸 합치기 순수 함수.
- `widgets/market-overview`: 지수 카드·순매수 카드·섹터맵 카드(지도/표, 카드 안 드릴다운 + `?sector=`).
- `features/refresh-ledger`: 버튼·팝오버·상태 조회/폴링·완료/실패 알림.
- `pages/research-home`: 머리(기준일·새로고침), 할 일 요약 줄, 시장 블록(접기, localStorage), 연구 블록.
  제목 "연구 홈" → "홈". 저장한 전략이 없으면 첫 전략 카드가 시장보다 위.
- `design-system/strategy-workbench/MASTER.md`: 매수·매도 방향색, 홈 읽기 순서 행, 시장 시각화 문단.

## 5. 접근성·반응형
- 방향은 색이 아니라 부호와 낭독 문구로 전한다(`SignedValue`). 막대·칸 면은 `aria-hidden` 또는 이름을 가진다.
- 섹터맵은 Tab 정지 1개(roving tabindex), 방향키로 칸 이동, Enter로 드릴다운. 표 보기가 낭독 대안.
- 카드 폭 560px 미만이면 지도 대신 "많이 오른/내린 세부 업종"(유동시총 10조 이상) 목록 + 표 링크.
- 칸 배치는 카드 실제 크기를 재서(ResizeObserver) 다시 계산한다. 이름·부호 수익률이 안 들어가는 칸은 같은 L1
  안에서 "그 밖 N개"로 합친다. 묶음조차 들어가지 않으면 크기를 부풀리지 않고 이름 없는 빈 영역(초점 없음)으로
  남긴 뒤 "표에서 읽어요"라고 알린다. 가장 큰 세부 업종도 안 들어가면 L1 하나를 한 칸으로 그린다.

## 6. 테스트
- domain: 기간 합(원값 합 뒤 반올림), 커버리지 문턱, 요약 문장, 섹터 가중(거래정지 제외·부호), 0 처리.
- adapter: 손 픽스처 equity_root(`tests/equity_fixture.py`)로 지수·수급(ETF·우선주 제외, 미측정 제외)·섹터·
  드릴다운, 표 없음 사유. 카탈로그 snapshot 불일치 가드.
- ledger_refresh: 가짜 `LedgerSyncPort`로 상태 전이(성공·drifted 재시도·실패·부분), 시작 조건, 409 가드,
  반영 호출. 실제 subprocess 어댑터는 가짜 실행 파일로 진행 파일·결과 해석.
- database: `--progress-file`·`error_kind`·`in_build_window`.
- frontend: entity 순수 함수(표기·squarify·합치기), 위젯 렌더(vitest), 홈 페이지 위계.
- e2e(스토리): 연습용 원천 게이트에서 시장 안내·새로고침 숨김·할 일 요약 줄. 실데이터는 opt-in.

## 7. 유저 스토리
- US-DM-15 수용 기준 추가(할 일 요약 줄, 첫 전략이 시장보다 위).
- 새 스토리: US-DM-17(홈 시장 보기), US-DM-18(버튼 하나로 데이터 받기), US-SM-19(받아도 판본이 섞이지 않음),
  US-CS-15(시장 숫자의 정의 확인). 담당 PR은 이 작업의 PR 번호로 채운다.
