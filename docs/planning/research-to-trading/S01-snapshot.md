# S01 — 연구 입력 보존과 재현 경로

## 의도와 현재 판정

최종 목적은 감당할 수 있는 위험 안에서 비용 후 수익을 내는 자동매매다. S01은 동일 전략의 결과가
달라졌을 때 데이터 갱신과 코드 결함을 구분할 수 있도록, 연구가 읽은 입력과 코드·계약·환경 출처를
보존한다. 입력 재현은 전략의 수익성이나 회계 정확성을 입증하지 않는다. 다음 S02에서 회계와 데이터
한계를 검산하고 S03에서 연구 사례를 평가한다.

구현 당시의 검증은 합성 fixture에 한정됐다. 이후 2026-10-16 실제 원장 30표의 수신·manifest/파일/
hash 확인과 adapter metadata 읽기 후, 별도 사본의 `create → verify --repo-root → open --repo-root`가
모두 성공했다. current 30표·261파티션을 보존했고 원본 MANIFEST 전후 동일·복사 파일 inode 독립·
pending 해제·adapter snapshot/필드 계약 동일·보존 catalog의 원본 경로 참조 없음도 확인했다.
원장 판은 `2164ba776cbd99f2`다. 독립 `verify --repo-root` 재검증과 기존 S01 database/backend
연결 회귀 **28 passed**도 확인했다. production source·계약·환경은 변경하지 않았다.
이는 원본 분리 보존과 실행 출처 검증의 증거이며 연구 목적별
품질은 별도 판정한다. 원장 확보를 원천 공개시점 정확성·전체 품질
합격·수익성 검증으로 확대하지 않는다. 실제 IC·성과 연구와 계좌 접근은 이 단계에 포함하지 않는다.

후속 로컬 읽기 전용 QC는 완료했고 날짜·키·상장구간·가격 정합성과 기존 무효 bar 거절을 확인했다.
MASKED 공백·PIT 업종·TR·상폐 회수금 한계는 남았다. 정확한 범위와 다음 결정은
[S01 연구 준비 판정](S01-readiness.md)이 소유한다.
2020-01-02 이전 실데이터의 수익률·IC·팩터 값 분포는 계산하거나 열람하지 않는다.

## 책임과 재사용

- 테이블 읽기 계약은 `database/docs/EQUITY_DESIGN.md`와 `EQUITY_WORKFLOW.md`의
  `MANIFEST.current_build → BuildRecord.partitions[].path`다. 디렉터리의 `v=*`를 입력으로 고르지 않는다.
- `ledger_sync.layout.parse_manifest`의 안전한 빌드·파티션 경로 검증과 `snapshot_id`를 재사용한다.
  파티션 검증은 `ledger_sync.hashing.compute_partition_hash`를 재사용한다.
- `equity.inputs.pin`은 빌드 입력을 `_pinned`에 하드링크하고 누적 MANIFEST를 유지하는 기능이다.
  원본 GC에는 견디지만 같은 inode를 덮어쓰면 함께 바뀌며 연구 실행의 계약·환경 전체를 보존하지
  않는다. 연구 snapshot은 별도 root에 **복사**하여 원본의 덮어쓰기와 GC에서 분리한다.
- 카탈로그는 `equity.views.render_macros`와 `equity.catalog.write_catalog`로 보존 root 안에서
  새로 만든다. 원본 catalog의 절대경로를 복사하거나 SQL을 변경하지 않는다. 뷰 입력이 없는 경우
  기존 렌더러의 생략 사유를 descriptor에 남긴다. 이 동작은 EG 게이트나 실데이터 품질 승인이 아니다.
- 원본 current 포인터·동기화 상태·GC·`_pinned`·baseline을 수정하지 않는다. 보존 root는 빌드·동기화·GC
  대상과 분리한다. 현재 원본의 모든 커밋된 테이블을 보존하며 일부 기간/테이블 절단 기능은 제공하지 않는다.

## 경로와 공개 계약

새 진입점은 `database/src/equity/research_snapshot.py`의
`python -m equity.research_snapshot create|verify|open`이다. Python API는
`create_snapshot(source_root, root, *, repo_root)`와 `verify_snapshot(root, *, repo_root=None)`이며
실패는 `SnapshotResult.status=INVALID`·`ok=False`와 진단 `detail`로 반환한다. 성공 `READY`에는
두 식별자가 필수이고 실패에는 사유만 허용하므로 root만 있는 미검증 성공은 생성할 수 없다. CLI는 실패 시 stderr와 종료 코드 1을
사용하고 `open`은 성공할 때만 검증한 절대 root 경로를 stdout에 출력한다.

`create`는 기존 목적지를 덮어쓰지 않고, 존재하는 부모 디렉터리 아래 새 root를 독점 예약한다.
source와 destination이 같거나 서로 포함되면 거절한다. 원본 manifest 집합을 먼저 읽고 검증한 뒤,
다음 순서로 공개한다.

1. 새 root를 만들고 `_research_snapshot.pending`을 기록한다.
2. 테이블별 current BuildRecord 한 개와 그 파티션의 일반 파일을 복사한다. `_reject` 같은 하위
   디렉터리는 입력이 아니므로 복사하지 않는다. symlink·루트 이탈·중복 파티션·누락을 거절한다.
3. `_research_provenance/`에 계약·환경 원문, 작업 트리 patch와 미추적 코드를 보존한다.
4. 최종 root의 절대경로를 참조하는 정상 DuckDB catalog를 만든다.
5. 파티션의 원장 content hash와 행 수를 확인하고, 원본 MANIFEST 집합을 다시 읽어 전후 변경을 거절한다.
6. 모든 파일의 SHA256과 출처를 담은 `_research_snapshot.json`을 임시 파일에서 `os.replace`로 교체한다.
7. **pending을 유지한 채** 공개 후와 같은 전체 무결성 검증을 통과시킨다. 성공한 경우에만 pending을
   해제하고 결과를 반환한다.

디렉터리의 존재가 공개 완료를 뜻하지 않는다. 완료 descriptor가 있고 pending이 없는 상태가 공개
계약이다. `verify/open`은 pending이나 완료 descriptor 누락을 거절한다. 실제 `EquityDuckdbAdapter`도
`table_builds` 진입에서 pending을 발견하면 `EquityDuckdbSetupError`로 거절한다. root 예약 직후 marker
생성 전에는 빈 디렉터리여서 adapter의 필수 테이블 검사에 실패한다. 프로세스가 중간 종료되면 pending이
남아 실행을 막는다. 일반 실패는 이번 명령이 만든 root만 정리하며, 기존 root는 보존한다.

파일 시스템 전원 장애까지 보장하는 fsync 프로토콜이나 쓰기 권한을 막는 저장소는 아니다. 외부에서
보존 파일을 수정하면 매번 실행 직전 `open`으로 검증해야 한다. adapter의 pending 차단은 불완전 공개를
막지만, 완성 후 파일 해시를 자동으로 재검증하지 않는다. 검증과 실제 실행 사이에 외부 쓰기를 허용하지
않는 운영 책임은 사용자가 갖는다. marker를 수동으로 제거해 실패한 root를 실행하지 않는다.

catalog가 절대경로를 보존하므로 완성 root는 이동하지 않는다. `verify/open`은 descriptor에 기록한
root와 실제 위치가 다르면 실패한다. 이동이 필요하면 보존 원본에서 새 root를 생성하고 재검증한다.

## 저장하는 증거와 식별자의 의미

| 증거 | 위치·의미 |
|---|---|
| 테이블 빌드·입력 빌드·규칙 판·빌드 시각·파티션 경로·해시·판 구분 | 각 테이블 `MANIFEST.json`의 원래 current BuildRecord 전체. 구판 기록만 제거하고 keep=1로 기록 |
| 달력·유니버스·기업행동·공개시점 | 원본 root에 있는 해당 테이블의 동일 BuildRecord와 파티션을 함께 보존. 없는 테이블을 합성하지 않음 |
| 파티션 해시 | 원장 DuckDB 내용 해시와 `n_rows`를 대조. 참조 content hash가 없으면 생성 실패 |
| 파일 집합과 바이트 | descriptor의 `files_sha256`. manifest·parquet·메타·catalog·계약/환경 원문·patch를 포함하며 직계 descriptor/marker만 제외 |
| 원장 판 | `ledger_snapshot_id`: 정렬한 `table=build` 집합의 기존 16자리 지문. 경로와 무관 |
| 계약 정본 원문 지문 | `provenance.contract_sources_sha256`: `CONTRACT_FILES` 원문 SHA256 목록의 지문. **워크벤치 필드 계약 판이 아님**. 설명 문장 변경도 지문을 바꿈 |
| 워크벤치 실제 필드 계약 판 | 기존 adapter가 기존 canonical revision 규칙으로 `snapshot().snapshot_id`에 결합. 새 계산 owner를 만들지 않음. 통합 테스트에서 원본과 보존 root의 값이 같음을 확인 |
| 코드 출처 | Git HEAD SHA, 해당 코드/계약/환경 경로의 binary patch, 추적·미추적 source 파일 집합의 SHA256. 미추적 source는 원문도 보존 |
| 환경 출처 | 루트·backend의 pyproject/uv.lock 원문과 SHA256, Python·플랫폼·설치 package 버전 목록. 환경변수·비밀은 수집하지 않음 |

소스 파일 집합은 `git ls-files --cached --others --exclude-standard`의 `database/src`, `backend/src`,
`backend/rust` 범위다. `.gitignore`된 빌드·로컬 파일은 코드 복원 계약 밖이다. 코드 checkout은 기록한
Git SHA에 patch와 미추적 source 원문을 적용해 재구성한다. 자동 checkout·의존성 설치·Rust 빌드는
수행하지 않는다. `verify/open --repo-root`는 현재 관련 source 파일 **집합과 해시**, 계약 원문과 lockfile
해시를 보존 값과 대조한다. 이 옵션을 생략하면 보존 입력의 무결성만 확인하며 현재 코드와의 동일성을
주장하지 않는다. 기록된 설치 package 목록과 운영체제를 비교하여 같은 환경을 별도로 준비해야 한다.
DuckDB 내용 해시는 DuckDB 버전에 의존할 수 있으므로 보존 당시 버전을 사용한다.

SHA256은 우발적 변경을 찾는 무결성 증거다. descriptor 자체의 신뢰를 외부 서명이나 별도 신뢰 저장소로
보장하는 기능은 이 범위에 없다.

## 사용과 재현

저장소 루트에서 실행한다. 아래 source 경로는 사용자가 이미 확보한 로컬 원장으로 지정한다. 실제 자료가
없으면 원격 자료를 가져오는 대신 합성 테스트만 실행하고 실데이터 검증을 미완료로 남긴다.

```bash
mkdir -p /absolute/path/research-snapshots
PYTHONPATH=database/src backend/.venv/bin/python -m equity.research_snapshot create \
  --source-root /absolute/path/existing-equity \
  --root /absolute/path/research-snapshots/study-001 \
  --repo-root "$PWD"

PYTHONPATH=database/src backend/.venv/bin/python -m equity.research_snapshot verify \
  --root /absolute/path/research-snapshots/study-001 --repo-root "$PWD"

PYTHONPATH=database/src backend/.venv/bin/python -m equity.research_snapshot open \
  --root /absolute/path/research-snapshots/study-001 --repo-root "$PWD"
```

`open`의 성공 경로를 `STRATEGY_WORKBENCH_EQUITY_ROOT`로 설정하고 기존
`STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb`로 실행한다. CLI 실패 시 실행을 중단해야 하며 이전
환경변수의 root를 그대로 사용하지 않는다. 첫 연구의 실행 증거에는 기존 run manifest의 snapshot ID와
보존 root·descriptor를 함께 연결한다. 연구 실행 파라미터·비용 설정·결과 보존은 기존 run manifest
책임이며 이번 CLI가 별도 실행기나 전략 결과를 만들지 않는다.

재현 시에는 보존 당시 코드·환경을 준비하고 `open --repo-root`에 통과한 같은 root로 다시 연결한다.
당시 실행 요청과 파라미터·비용도 기존 실행 manifest에서 복원한다. 입력 변경·누락·해시 불일치를 최신
원장이나 mock으로 대체하지 않는다.

## 합성 검증

```bash
PYTHONPATH=database/src:backend:backend/src backend/.venv/bin/pytest -q \
  database/tests/test_research_snapshot.py backend/tests/test_research_snapshot_integration.py
```

테스트는 함수가 직접 만든 임시 파일만 사용한다. current 교체·구판 GC 이후 동일 관측값 접근, 원본
in-place 손상과의 분리, 누락/손상/추가 파일·안전하지 않은 경로·중복 파티션의 거절, 원본 갱신 경합,
기존 목적지 보존, 복사/최종 검증 실패의 비공개, 불완전 descriptor와 pending 차단, 코드 파일 추가 및
계약 변경을 검증한다. backend 통합 테스트는 실제 adapter를 다시 만들어 raw/조정가 관측과 기존
snapshot·필드 계약이 같고, 보존 catalog에 원본 경로가 남지 않음을 확인한다.

실제 사본의 생성·재검증·재접속 증거는 위 현재 판정과 [연구 준비 판정](S01-readiness.md)에 별도로
기록한다. 원천 시점 정확성·비용 후 성과·실제 연구 재현 결과는 그 보존 성공과 구분한다.
