# 원장 서버 SFTP 동기화 · 실데이터 E2E 백테스트 설계

- **작성일**: 2026-09-19
- **상태**: 구현 완료(2026-09-19) — `database/src/ledger_sync/`, `frontend/e2e/workbench.real-equity.spec.ts`. 실측은 `database/docs/LEDGER_SYNC.md` §4·PR 본문
- **대상**: 카엘 서버(`210.217.23.47`, 계정 `quantshare`, SFTP 전용·읽기 전용)의 `equity` 층을 로컬로
  받아 워크벤치·엔진이 읽게 하고, 이후 매일 증분으로 따라가며, 실데이터 위에서 그래프를 편집한 전략의
  백테스트를 E2E 로 검증한다.

## 배경

서버 운영자가 2026-09-19 SFTP 읽기 전용 접근을 열었다. 서버 루트에는 `equity`·`stage`·`raw` 세 폴더가
있고 요청은 "equity 부터, `_pinned`·`_tmp`·`_failed` 제외, 테이블 폴더 안 `v=…` 최신 파티션만" 이다.
`raw` 는 매일 갱신되는 SQLite 원장이라 복사 중 갱신되면 사본이 깨지므로 범위에서 뺀다.

기존 `database/scripts/fetch_equity_local.sh` 는 `rsync` + ssh alias(`kael-server`) 전제인데,
`quantshare` 계정은 쉘이 막혀 있어(`This service allows sftp connections only`) rsync 를 쓸 수 없다.
로컬(Windows)에도 rsync 가 없다.

## 서버 실측 (2026-09-19)

| 항목 | 값 |
|---|---|
| equity 테이블 | 29개, 각 `<table>/MANIFEST.json` + `v=<build_id>/[year=YYYY/]{part0.parquet,_meta.json}` |
| 판본 보관 | MANIFEST 는 keep=3 이지만 디스크에는 테이블당 7~10 판본 공존(GC 지연). `v=*` glob 금지 |
| 현재 빌드 합계 | **1.47GB / 29표** (전 판본 합계 14.4GB) |
| 큰 표 | price_daily 314MB · credit_daily 283MB · universe_daily 242MB · flow_daily 230MB · price_adj_daily 190MB · short_daily 155MB |
| stage 현재 빌드 합계 | 2.24GB / 66표 (이번 범위 밖, 같은 규약이라 도구는 지원) |
| 빌드 주기 | 저녁 잠정판 `e_` 13:30 UTC(22:30 KST) 전후, 아침 확정판 `m_` 00:19 UTC(09:19 KST) 전후. 테이블 직렬로 약 10분 |
| 무결성 키 | BuildRecord·파티션의 `content_hash` = `{n_rows}:{hex(bit_xor(hash(CAST(row AS VARCHAR))))}` (duckdb, `stage/build.py:_content_hash`) |
| 카탈로그 | `equity.duckdb` 매크로가 **서버 절대경로**를 굽고 있어 로컬에서 `python -m equity catalog` 로 재생성해야 한다. `_catalog_meta.json.snapshot_id` = 전 테이블 build_id 정렬 해시 |

## 요구사항

1. **전량 수신**: equity 29표의 current_build 파티션만 받는다(`_pinned`·`_tmp`·`_failed`·`_asof`·`fixtures`·구판본 제외).
2. **동일성 검증**: 로컬 사본이 서버와 같은지 세 층위로 확인한다 — 판본(MANIFEST current_build), 파일(이름·크기), 내용(파티션 content_hash 재계산 vs MANIFEST).
3. **증분**: 매일 다시 돌리면 판본이 바뀐 테이블만 받고, 바뀐 판본 안에서도 content_hash 가 같은 파티션은 로컬에서 재사용해 내려받지 않는다.
4. **원자성·재개**: 중단돼도 어댑터가 반쪽짜리 빌드를 읽지 않는다. 재실행하면 이어받는다.
5. **소비자 연결**: 받은 루트를 워크벤치(`STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb`)와 커널 어댑터가 그대로 읽는다.
6. **E2E**: 실데이터 위에서 그래프를 편집(노드 추가·입력 재배선)한 전략을 저장하고 백테스트가 완료되는 브라우저 시나리오를 재현 가능하게 남긴다.

## 아키텍처

```
[서버 SFTP]  /equity/<table>/MANIFEST.json, v=<build>/…            (읽기 전용)
     │  paramiko SFTP · known_hosts 엄격 검증 · 재시도
     ▼
[ledger_sync]  database/src/ledger_sync/   python -m ledger_sync {plan,pull,verify,gc,catalog,sync}
     │  plan   : 원격 MANIFEST 전부 읽기 → 테이블별 (remote_build, local_build, 받을 파일, 재사용 파티션)
     │  pull   : <table>/_incoming/v=<build>/ 에 수신 → 크기 대조 → v=<build>/ 로 rename → MANIFEST 원문 기록
     │  verify : manifest · files · hash 세 층위 대조, 종료 코드로 판정
     │  gc     : 로컬 구판본 정리(keep, current 보호)
     │  catalog: `python -m equity catalog --root <root>/equity` 위임 (매크로 절대경로 재생성)
     │  sync   : pull → verify(manifest·files) → catalog 를 한 번에
     ▼
[로컬 루트]  ~/quant-ledger/data/equity/   (기본. `QL_SYNC_ROOT` 또는 `--root` 로 변경)
     │  <table>/MANIFEST.json (서버 원문 그대로) · v=<build>/… · _sync/state.json · _sync/logs/
     ▼
[소비자]  워크벤치 equity_duckdb 어댑터 · 커널 equity_duckdb 어댑터 · Playwright real-equity E2E
```

### 구성 요소

| 모듈 | 책임 | 의존 |
|---|---|---|
| `ledger_sync/remote.py` | `RemoteFS` 프로토콜(`listdir`, `read_bytes`, `download`)과 paramiko 구현 `SftpRemote`. 접속 정보(`host`·`user`·`key`)는 CLI/환경변수. 호스트키는 `~/.ssh/known_hosts` 에 없으면 거부(`--accept-new` 로만 허용). 전송 오류는 지수 백오프 3회 재시도 후 예외 | paramiko |
| `ledger_sync/layout.py` | MANIFEST 해석(`current_build`, `partitions[].path`, `content_hash`), 테이블 목록 규칙(`_`·`.` 접두 디렉토리와 MANIFEST 없는 디렉토리 제외), 빌드 하나가 가지는 원격 파일 목록 | 없음 |
| `ledger_sync/state.py` | `_sync/state.json`: 테이블별 `{build_id, files: {relpath: {size, origin}}, synced_at_utc}`. `origin` ∈ `downloaded`·`reused` | 없음 |
| `ledger_sync/plan.py` | 원격·로컬 상태 → `TablePlan`(상태 `up_to_date`·`new_build`·`error`, 받을 파일·재사용 파티션·바이트 수) | layout, state |
| `ledger_sync/pull.py` | 계획 실행. `_incoming` 수신·크기 대조·rename·MANIFEST 기록·state 갱신·구판본 gc. 끝나면 원격 MANIFEST 를 다시 읽어 수신 중 판본이 바뀐 테이블을 `drifted` 로 보고 | remote, plan, state |
| `ledger_sync/verify.py` | `manifest`(current_build 일치) · `files`(원격 목록 이름·크기 = 로컬, reused 파티션은 크기 대조 제외) · `hash`(duckdb 로 파티션 content_hash 재계산 = MANIFEST). 결과는 값(`VerifyReport`)이고 CLI 가 종료 코드로 바꾼다 | remote, layout, duckdb(hash 만) |
| `ledger_sync/__main__.py` | argparse CLI. 사람용 표 + `--json` | 위 전부 |
| `database/scripts/ledger_sync.ps1` · `.sh` | `uv run --project backend --with paramiko python -m ledger_sync` 래퍼(backend 환경의 duckdb·pyarrow 를 그대로 쓴다) · `register_daily_sync.ps1` 은 Windows 작업 스케줄러에 `sync` 를 매일 등록 | uv |

### 데이터 흐름 (pull)

1. 원격 `<layer>/` 디렉토리 목록 → 테이블 후보 → 각 `MANIFEST.json` 을 메모리로 읽어 current_build 와 파티션 목록을 얻는다. `_catalog_meta.json`·`baseline.json`·`_contract_meta.json` 은 항상 받는다.
2. 로컬 `state.json` 과 대조. 같은 build_id 이고 state 의 파일이 전부 디스크에 같은 크기로 있으면 `up_to_date`.
3. 새 빌드면 파티션마다: 로컬 직전 빌드에 **같은 파티션 경로 꼬리(`year=YYYY` 또는 whole)이고 content_hash 가 같은** 파티션이 있으면 `reused`(로컬 복사, 하드링크 가능하면 하드링크), 아니면 원격 `listdir` 로 파일 목록을 얻어 `_incoming/v=<build>/…` 로 받는다. `_incoming` 에 같은 크기의 파일이 이미 있으면 건너뛴다(재개).
4. 받은 파일 크기가 원격 stat 과 다르면 그 테이블은 실패로 남기고 다음 테이블로 간다(부분 실패 격리). 성공하면 `_incoming/v=<build>` → `v=<build>` rename, MANIFEST 원문을 `MANIFEST.json` 에 `os.replace`, state 갱신.
5. 테이블 처리 뒤 원격 MANIFEST 를 다시 읽어 current_build 가 바뀌었으면 `drifted` 로 보고(종료 코드 3). 사용자는 다시 `pull` 하면 된다.
6. 로컬 gc: `v=*` 중 state 가 아는 current 를 제외하고 최신 `keep-1` 개만 남긴다(기본 keep=2). `_incoming` 의 다른 빌드 잔재도 지운다.

### 재사용(reused) 규칙의 근거와 한계

같은 content_hash 는 서버 자신의 재현성 게이트(EG5a)가 쓰는 동일성 기준이다. 재사용한 파티션의 parquet 바이트는 서버 파일과 다를 수 있으므로 `verify files` 는 reused 파티션의 크기 대조를 건너뛰고 `verify hash` 가 내용을 보증한다. 바이트 동일 사본이 필요하면 `pull --no-reuse`.

### 에러 처리

- 접속·인증·호스트키 실패: 즉시 예외(메시지에 host·user·key 경로). 알 수 없는 호스트키는 `--accept-new` 없이는 거부.
- 원격 MANIFEST 가 JSON 이 아니거나 current_build 가 builds 에 없음: 그 테이블만 `error` 로 기록하고 계속.
- 전송 오류(EOF·소켓): 3회 재시도, 그래도 실패면 테이블 `error`. 종료 코드 2.
- 디스크 여유가 계획 바이트 × 1.2 미만이면 시작 전에 중단(종료 코드 2).
- 서버 빌드 창(13:15~13:50 UTC, 00:00~00:35 UTC)에 시작하면 경고만 낸다 — drifted 로 잡힌다.

### 검증(verify) 층위와 종료 코드

| 층위 | 무엇 | 실패 시 |
|---|---|---|
| manifest | 테이블 집합·current_build 가 원격과 같다, 로컬 `_catalog_meta.json.snapshot_id` 가 로컬 빌드 집합 해시와 같다 | 4 |
| files | current_build 파티션 파일 이름·크기가 원격과 같다(reused 제외), MANIFEST 원문 바이트 동일 | 4 |
| hash | 파티션마다 duckdb 재계산 `content_hash` = MANIFEST(`_meta.json` 의 `partition_content_hash`) | 4 |

세 층위 다 통과하면 0. 원격 접속 없이도 `hash` 는 돌 수 있다(`--offline`).

### E2E (실데이터 백테스트)

- `frontend/e2e/workbench.real-equity.spec.ts`, 새 Playwright project `real-equity`(1440×900 light 한 개). `STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb` 와 `STRATEGY_WORKBENCH_EQUITY_ROOT` 가 없으면 `test.skip` 한다 — CI 는 mock 그대로.
- 시나리오: 워크벤치에서 골든 fixture(252 세션 모멘텀, 유니버스 `krx.common-stock`)의 기간만 2024-01-02~2024-06-28 로 바꿔 저장 → Graph 편집기에서 `price.open` field 노드 추가·`mom_252` 입력 재배선 → YAML 재검증·리비전 2 저장(spec_hash = compile 결과) → 백테스트 실행(`POST /api/v1/backtests` 는 TargetTape 를 동기로 만들어 실데이터에서 약 80초 뒤 202) → 완료 상태, 실데이터 snapshot id(16 hex), 체결·스냅샷·자본곡선 > 0, `total_return` 값 존재 확인.
- backend 는 Playwright webServer 가 `process.env` 를 그대로 넘기므로 `STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb` + ROOT 만 설정하면 된다. 실행: `npm run test:e2e -- --project real-equity`.

## 제약사항

- 서버 쪽 코드·크론은 손대지 않는다(읽기 전용 계정). 수신 기록은 서버 로그에 남는다.
- `raw` 는 받지 않는다(운영자 요청). `stage` 는 같은 규약이라 `--layer stage` 로 지원하되 기본은 equity.
- `database/` 는 코드·문서만 추적한다(`data/`·`logs/` 는 git 제외). 로컬 루트 기본값은 저장소 밖 `~/quant-ledger/data`.
- 테스트는 산출물 파일에 의존하지 않는다(`.claude/rules/testing.md`) — `RemoteFS` 가짜 구현과 임시 디렉토리로 검증한다.
- Windows 우선(사용자 환경). 경로는 `pathlib`, 하드링크 실패 시 복사로 폴백.

## 설계 결정

### 1. 전송 계층

- **선택**: paramiko SFTP 를 Python 에서 직접 호출.
- **대안**: (a) OpenSSH `sftp -b` 배치 호출 (b) WinSCP 스크립트 (c) rclone sftp 백엔드.
- **이유**: 쉘이 막혀 rsync 가 불가하고, 증분 판단(MANIFEST 해석·content_hash 재사용)은 어차피 Python 이 해야 한다. (a) 는 stat·재개·부분 실패 격리를 제어하기 어렵고, (b)(c) 는 추가 설치가 필요하다.

### 2. 증분 단위

- **선택**: 테이블 = 판본(build_id), 파티션 = content_hash 재사용.
- **대안**: 파일 mtime/size 기반 rsync 식 미러링.
- **이유**: 서버는 매 빌드마다 새 `v=` 디렉토리를 통째로 쓰므로 mtime 미러링은 매일 1.5GB 를 다시 받는다. 파티션 content_hash 는 서버가 이미 계산해 MANIFEST 에 실어 주는 동일성 키라 재사용 판정이 공짜다.

### 3. 로컬 MANIFEST

- **선택**: 서버 MANIFEST 원문을 그대로 둔다(로컬에 없는 구판본 항목 포함).
- **대안**: 로컬에 있는 판본만 남기도록 재작성.
- **이유**: 어댑터는 `current_build` 만 따라가고 `builds[]` 의 다른 항목은 읽지 않는다. 원문을 두면 `verify files` 가 바이트 동일로 대조할 수 있고 `snapshot_id` 도 서버와 같다.

### 4. 도구 위치

- **선택**: `database/src/ledger_sync/` + `database/scripts/ledger_sync.{ps1,sh}`.
- **대안**: `tools/quant_study_dev` 의 `uv run` 스크립트, backend 패키지.
- **이유**: 데이터 층 코드는 `database/src` 에 두고 `python -m <pkg>` 로 부르는 것이 이 폴더의 규약(`stage`·`equity`). backend 는 데이터 수송을 알 필요가 없다(헥사고날 경계).

## 테스트 계획

- `database/tests/test_ledger_sync_layout.py`: MANIFEST 해석·테이블 제외 규칙·파일 목록.
- `database/tests/test_ledger_sync_plan.py`: up_to_date / new_build / reused 판정, 바이트 집계.
- `database/tests/test_ledger_sync_pull.py`: `FakeRemote` 로 수신·원자 rename·재개·크기 불일치 격리·drifted·gc.
- `database/tests/test_ledger_sync_verify.py`: 세 층위 판정, reused 제외, hash 재계산(duckdb 로 만든 소형 parquet).
- `database/tests/test_ledger_sync_cli.py`: 종료 코드·`--json`.
- 실서버 대상: `sync` 1회 전량 → `verify` 0 → 다음 날 `pull` 로 증분 확인 → 워크벤치 기동·E2E project 통과.

## 관련

- `docs/superpowers/specs/2026-08-25-quant-ledger-sharing-design.md` (Phase 2~3 "공개 경로·접근" 을 SFTP 로 대체)
- `database/docs/EQUITY_DESIGN.md` §2 판본 규약, `EQUITY_HANDOFF.md` §13 소비자 기동
- `database/scripts/fetch_equity_local.sh` (rsync 전제, 운영자 본인 계정용으로 남긴다)
