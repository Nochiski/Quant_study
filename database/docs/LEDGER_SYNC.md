# LEDGER_SYNC — 카엘 서버 SFTP → 로컬 equity 층 동기화

> 설계 정본: `docs/superpowers/specs/2026-09-19-ledger-sftp-sync-design.md`. 코드: `database/src/ledger_sync/`.
> 이 문서는 운영 절차와 판단 기준만 적는다.

## 0. 한 문장

서버(`210.217.23.47`, 계정 `quantshare`, SFTP 전용·읽기 전용)의 `equity/<table>/MANIFEST.json` 이
가리키는 current_build 파티션만 받아 `~/quant-ledger/data/equity/` 에 서버와 같은 규약으로 두고,
매일 한 번 새 빌드를 따라가며 세 층위(판본·파일·내용)로 같은지 확인한다.

## 1. 처음 한 번

```powershell
# 1) 접속 확인 — 호스트키가 known_hosts 에 없으면 처음만 --accept-new
database\scripts\ledger_sync.ps1 --accept-new plan

# 2) 전량 수신(equity 29표, 현재 빌드 약 1.5GB) + 판본·파일 검증 + 카탈로그 재생성
database\scripts\ledger_sync.ps1 sync

# 3) 내용 검증(파티션 content_hash 재계산, 원격 접속 없음)
database\scripts\ledger_sync.ps1 verify --offline

# 4) 매일 10:00 KST 에 sync 를 도는 작업 스케줄러 등록
database\scripts\register_daily_sync.ps1
```

래퍼는 backend 프로젝트 환경(`uv sync --extra parquet --extra equity`)에 paramiko 만 얹어
`python -m ledger_sync` 를 돈다. 접속 정보는 `--host/--port/--user/--key` 또는 `QL_SYNC_HOST`·
`QL_SYNC_PORT`·`QL_SYNC_USER`·`QL_SYNC_KEY`, 로컬 루트는 `--root` 또는 `QL_SYNC_ROOT`(기본
`~/quant-ledger/data`).

**신뢰 경계**: 서버가 주는 이름(build_id·파티션 경로·파일 이름)은 그대로 로컬 경로 조각이 되므로
`layout.is_safe_segment` 문법(`[A-Za-z0-9_][A-Za-z0-9._=%-]*`) 밖이면 그 테이블을 받지 않는다. `--accept-new`
는 known_hosts 에 한 줄만 덧붙이고 파일을 다시 쓰지 않는다.

## 2. 동사

| 동사 | 하는 일 | 종료 코드 |
|---|---|---|
| `plan` | 원격 MANIFEST 와 로컬 `_sync/state.json` 대조 — 테이블별 `new_build`/`up_to_date`/`error`, 받을 바이트·재사용 바이트. 로컬 변경 없음 | 0 · 2(해석 불가 테이블) |
| `pull` | 새 빌드를 `<table>/_incoming/v=<build>/` 에 받아 크기 대조 → `v=<build>/` 로 rename → MANIFEST 원문 기록 → state 갱신 → 구판본 GC(`--keep`, 기본 2, build_id 시각순). 전송·로컬 IO 실패는 테이블 단위로 격리하고 `_incoming` 을 남겨 재개한다 | 0 · 2(전송·IO 실패) · 3(수신 중 서버 판본 변경 → 다시 pull) |
| `verify` | `manifest`(current_build·카탈로그 snapshot) · `files`(이름·크기·MANIFEST 바이트) · `hash`(duckdb content_hash 재계산). `--offline` 은 hash 만. **검사 대상이 0건**(state 없음·`--tables` 오타)이면 통과가 아니라 4. stage 층은 파티션 content_hash 가 없어 hash 층위를 `skipped` 로 센다 | 0 · 4(불일치·검사 0건) |
| `gc` | current 를 제외한 `v=*` 중 최신 `keep-1` 개만 남긴다. `_incoming` 잔재 정리 | 0 |
| `catalog` | `python -m equity catalog` 위임 — `equity.duckdb` 매크로가 서버 절대경로를 굽고 있어 로컬에서 재생성해야 재무·컨센서스 필드가 산다 | 0 · 2 |
| `sync` | pull → catalog(전송 실패가 없을 때) → verify(manifest·files + 이번에 받은 표의 hash). 카탈로그를 verify 앞에서 돌려야 verify 의 「카탈로그 stale」 지적이 그 자리에서 해소된다. 판본이 안 바뀐 표의 로컬 손상은 보이지 않으므로 `--hash-all`(전 표 hash, 주 1회 권장)을 따로 돌린다. 같은 층에 pull·gc·sync 가 겹치면 `_sync/lock` 으로 거부(2). `_sync/last_run.json` 에 결과, `_sync/logs/` 에 verb 별 최근 60개 로그 | 첫 비영 코드: pull 실패 2 · drifted 3 · catalog 실패 2 · 불일치 4 |
| `status` | 마지막 실행 결과·테이블별 로컬 빌드. `--remote` 면 서버 current_build 와 대조해 `BEHIND` 표시 | 0 |

`--tables a b` 로 일부 테이블만, `--json` 으로 기계용 출력, `--layer stage` 로 stage 층(같은 규약).

## 3. 증분이 도는 방식

- 서버는 매 빌드마다 새 `v=<build>` 디렉토리를 통째로 쓴다(저녁 잠정판 `e_` 13:30 UTC = 22:30 KST
  전후, 아침 확정판 `m_` 00:19 UTC = 09:19 KST 전후). 판본이 바뀐 테이블만 대상이다.
- 바뀐 판본 안에서도 **같은 꼬리(`year=YYYY`/whole)·같은 `content_hash`·같은 parquet 파일 집합**
  파티션은 로컬 직전 빌드의 parquet 를 복사(하드링크)해 쓰고 받지 않는다. 재사용 직전에 로컬 파티션
  해시를 duckdb 로 다시 계산해 MANIFEST 와 맞을 때만 쓴다(손상 전파 차단, `--no-reuse-check` 로 끌 수
  있다). `_meta.json` 은 빌드마다 다르므로 항상 받는다. 보통 하루치 갱신은 `year=2026` 파티션 정도라
  수십 MB 로 끝난다. stage 층 MANIFEST 에는 파티션 content_hash 가 없어 재사용이 되지 않는다(매 빌드
  전량 수신).
- 재사용한 parquet 는 서버 파일과 바이트가 다를 수 있다(duckdb 재작성). 그래서 `verify files` 는
  `origin=reused` 파일의 크기 대조를 건너뛰고 `verify hash` 가 내용을 보증한다. 바이트 동일 사본이
  필요하면 `pull --no-reuse`.
- 수신 중 서버가 새 빌드를 커밋하면 `drifted` 로 보고(종료 3)한다. 다시 `pull` 하면 된다. 서버 빌드
  창(13:15~13:50 UTC, 00:00~00:35 UTC)에 시작하면 경고가 뜬다.

## 4. 내용 검증(hash)이 서버와 같은 값을 내는 이유

서버 `stage/build.py:_content_hash` 는 tmp 경로에서 `read_parquet(..., hive_partitioning=true)` 로
`count(*), bit_xor(hash(CAST(row AS VARCHAR)))` 를 뜬다. 로컬 경로에는 `v=<build>` 가 섞여 hive 를
켜면 `v` 컬럼이 붙어 값이 달라진다. `ledger_sync.hashing.content_hash_sql` 은 hive 를 끄고 꼬리의
`year=YYYY` 만 컬럼으로 덧붙여 같은 struct 문자열을 만든다. 2026-09-19 실측: `security`(whole)·
`adj_factor`(year 파티션) 전부 서버 MANIFEST 값과 일치(duckdb 1.5.5).

## 5. 소비자 연결

```powershell
$env:STRATEGY_WORKBENCH_EQUITY_ADAPTER = "duckdb"
$env:STRATEGY_WORKBENCH_EQUITY_ROOT = "$HOME\quant-ledger\data\equity"
uv run server      # 저장소 루트에서. backend 에서 직접 띄우려면 cd backend && uv run server
```

커널 어댑터(`backtest_engine.adapters.equity_duckdb`)도 같은 루트를 받는다. 실데이터 브라우저
E2E 는 `frontend`에서 `$env:E2E_REAL_EQUITY_ROOT = <루트>; npm run test:e2e`(변수가 있으면 실데이터
project 만, 없으면 mock 릴리스 게이트만 돈다 — `frontend/e2e/README.md`). 서버 `_catalog_meta.json`·
`_contract_meta.json` 원문은 `<루트>/_sync/remote/` 에만 두고, 루트의 `_catalog_meta.json` 은 로컬
`catalog` 산출물이다(서버 것으로 덮으면 워크벤치의 stale-catalog 가드가 무력화된다).

## 6. 하지 않는 것 · 서버 운영자에게 요청할 것

- `raw/`(원장 SQLite) 는 받지 않는다 — 매일 갱신되는 라이브 파일이라 복사 중 갱신되면 깨진다.
  정합 사본이 필요하면 서버의 `backup_raw.sh` 산출(`~/backups/quant-ledger/<D>/`)을 SFTP 로 노출해
  달라고 요청한다.
- 원격에서 해시를 계산할 수 없어(쉘 없음) 바이트 동일성은 크기까지만 본다. 파일별 sha256 목록을
  빌드가 `MANIFEST.json` 또는 파티션 `_meta.json` 에 실어 주면 `verify files` 가 바이트 검증으로 올라간다.
- 아침 체인 완료 마커(`data/deliver/latest_morning.json`)를 SFTP 루트에 노출하면 `status --remote`
  가 "오늘 판이 나왔는지" 를 판본 비교 없이 알 수 있다.
