# PLAN 집계: macOS·Linux·Windows 공용 계산 경로

## 의도와 현재 판정

계획의 PR 상태와 의존성을 한 기준으로 검사해, 실행 OS나 `parallel_window`의 순서 때문에 변경
요청이 가려지거나 다른 진척이 보고되는 일을 막는다. PowerShell 설치를 요구하던 루트 테스트를
단순히 건너뛰지 않고, 실제 집계 입력→출력을 macOS에서도 실행하도록 바꿨다.

`tools/quant_study_dev/plan_progress.py`가 순수 Python 집계·검증·생성의 단일 owner다. 새 의존성은
없으며 Python 3.11 이상으로 script를 직접 실행할 수 있다. 기존 루트 uv 명령도 유지한다.
initiative별 phase 이름·ID 형식과 lang2의
`INTEGRATED` 지원 차이는 `PROFILES`에 선언한다. 각 initiative의 `tools/update-plan-progress.ps1`은
기존 Windows의 `-PlanPath`·`-Check` 호출을 같은 Python 도구로 전달하는 호환 진입점이다.

PR tracker는 사람이 소유한다. 이 도구는 tracker 상태·체크·Dependency·Review·순서를 바꾸지 않고,
이미 기록한 사실에서 frontmatter와 SUMMARY/PHASES 블록만 파생한다. PR을 승인·머지하거나 GitHub에
연결하지 않는다. 이번 변경에서는 기존 PLAN의 PR 상태나 생성 집계를 수정하지 않았다.

## 계산 기준

- `[x]`는 `MERGED`와 일치해야 한다. `strategy-language-2-0`에서만 `INTEGRATED`도 완료 체크다.
  lang2의 done/진척은 둘을 합산하고 main MERGED와 integration 수를 따로 표시한다.
- approved 수는 `APPROVED`+완료 상태다. 진척은 완료 체크 수/전체 tracker 수의 백분율을 기존
  .NET과 같은 가장 가까운 짝수 반올림으로 계산한다. 1/8의 12.5%는 12%다.
- 프로젝트는 전부 `MERGED`일 때만 COMPLETE다. lang2에서 전부 INTEGRATED인 phase는
  INTEGRATED지만, 그것을 프로젝트의 main 머지 완료로 바꾸지 않는다.
- 활성 상태의 심각도는 `CHANGES_REQUESTED > IN_PROGRESS > SELF_CHECK > IN_REVIEW > APPROVED`다.
  프로젝트/phase의 상태를 이 순서로 집계한다. 활성 항목이 없으면 READY, PAUSED, WAITING 순이다.
- 활성 PR이 둘 이상이면 `parallel_window`에 정확히 같은 집합을 선언해야 한다. 셋 이상도 모두
  선언한 stack일 때만 허용한다. `parallel_window` 순서는 current/next PR의 표시 순서만 바꾸며
  blocking 상태의 우선순위는 바꾸지 않는다.
- READY의 dependency는 완료 상태여야 한다. 미지 dependency·상태·phase, 중복 PR id,
  체크/상태 불일치, 선언 phase의 tracker 누락을 거절한다. dependency graph나 새 상태를
  만들어내지 않는다. 기존 도구가 검사하던 의미를 유지한다.
- 갱신 시 last_updated를 KST로 기록하고 `--check`에서는 기존 시각을 그대로 사용한다.
  check는 입력 파일을 쓰지 않으며 stale·계약 위반·읽기 실패는 종료 코드 1이다.
- LF/CRLF, tracker 이후 원문과 사람이 소유한 제목·설명은 보존한다. 파일 읽기는 UTF-8의 BOM 유무를
  모두 받고, 갱신 출력은 기존 PS1과 같이 UTF-8 BOM 없음이다. PowerShell wrapper는 한글 주석이
  Windows PowerShell 5.1에서도 안전하게 해석되도록 UTF-8 BOM을 가진다.

## 실행

저장소 루트에서 설치 없이 검증한다. PATH의 `python`은 Python 3.11 이상이어야 한다.

```sh
python -X utf8 tools/quant_study_dev/plan_progress.py --profile validation-lab --check
```

기존 루트 개발 환경의 `uv run --locked python -m quant_study_dev.plan_progress`도 같은 계산을
호출하지만, 직접 script 실행에는 uv나 editable package 설치가 필요 없다.

tracker를 변경한 뒤 생성 집계를 갱신하고 재검증한다. 기본 입력은 해당 profile의
`docs/planning/<profile>/PLAN.md`다. 임시/다른 경로를 검증할 때는 `--plan-path`를 명시한다.

```sh
uv run --locked python -m quant_study_dev.plan_progress --profile strategy-language-2-0
uv run --locked python -m quant_study_dev.plan_progress --profile strategy-language-2-0 --check
uv run --locked python -m quant_study_dev.plan_progress \
  --profile strategy-workbench-yaml-ui --plan-path "/path with spaces/PLAN.md" --check
```

지원 profile은 `strategy-workbench-yaml-ui`, `strategy-gui-editing`, `ai-assistant`,
`strategy-language-2-0`, `validation-lab`이다. 각 profile은 기존 PS1의 phase 목표·PR ID 패턴을 유지한다.

Windows 기존 호출도 유지한다. 실행하는 디렉터리와 무관하게 wrapper가 자기 위치에서 저장소 root와
기본 PLAN 경로를 계산하며, 명시 `-PlanPath`의 상대경로 기준은 호출자의 작업 디렉터리다. wrapper는
PATH의 `python` 하나만 사용하고 먼저 3.11 이상인지 검사한다. 구버전이면 오류와 종료 코드 1을
반환하며, Python이 없으면 명령 탐색 오류로 실패한다. 별도 설치·실행기 탐색·폴백은 하지 않는다.
공용 script 절대경로를 `-X utf8`로 직접 실행해 locale에 따른 출력 인코딩 차이를 줄이고 자식 Python
종료 코드를 그대로 반환한다. 파일 입력/출력의 UTF-8·BOM 규약은 위 계산 기준을 따른다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/planning/validation-lab/tools/update-plan-progress.ps1 -Check
powershell -NoProfile -ExecutionPolicy Bypass -File docs/planning/validation-lab/tools/update-plan-progress.ps1 -PlanPath "C:\work path\PLAN.md"
```

## CI의 실행 환경

`.github/workflows/lang2-plan.yml`의 `generated-progress`는 checkout 뒤 기존 PowerShell wrapper를
호출한다. wrapper는 runner PATH에 이미 있는 Python 3.11 이상으로 stdlib script를 직접 실행한다.
uv·루트 package 설치나 workflow 변경은 필요 없다. 버전 검사가 실패하면 집계도 실행하지 않는다.

첫 통합 실행에서는 wrapper가 uv를 요구해 집계 전에 실패했다. 이를 직접 Python script 호출로
해결하며 별도 토큰 권한이나 workflow 수정 없이 기존 job을 유지한다. stale 검사·공식 수정본
artifact 생성과 Python 종료코드 전달도 유지한다. 로컬 fixture 성공만으로 PowerShell을 거치는
CI 성공까지 주장하지 않고 수정 후 해당 job을 확인한다.

## fixture 명세와 검증

`tools/tests/test_plan_progress.py`는 직접 만든 문자열/임시 파일을 입력으로 사용하며 실제 PLAN 파일의
숫자를 테스트에 고정하지 않는다.

| fixture | 기대 출력·동작 |
|---|---|
| YAML profile: 7 MERGED, P6의 CHANGES_REQUESTED·APPROVED | 프로젝트와 P6는 CHANGES_REQUESTED, merged 7/9, approved 8/9, 진척 78% |
| 위 두 활성 id의 선언 순서 반전 | blocking 상태 불변, current_pr 표시 순서만 반전 |
| 5개 profile의 phase별 합성 tracker | 해당 phase 목표·집계 형식과 실제 계산값 보존 |
| lang2의 완료 INTEGRATED 6개 + READY·APPROVED | main 0, integration/done 6, INTEGRATED dependency 뒤 READY 허용 |
| lang2 외 profile의 INTEGRATED | INVALID, 생성 결과 없음 |
| LF·CRLF / 한글·공백 경로·BOM | 갱신과 check 성공, 원문·줄바꿈 보존, 갱신 출력 BOM 없음 |
| 생성 결과를 기존 시각으로 다시 집계 | 동일 문자열, 정상 check는 파일 바이트 불변 |
| stale 생성 블록·잘못된 상태/의존·없는 파일 | INVALID 또는 CLI exit 1, check는 파일 미변경 |
| COMPLETE/READY/PAUSED/WAITING과 1/8 완료 | 기존 상태 의미와 짝수 반올림(12%) 유지 |
| 활성 3개를 모두 선언한 stack / 선언 누락 | 선언한 순서로 current 표시·blocking 유지 / INVALID |
| lang2 phase의 모든 PR이 INTEGRATED | phase INTEGRATED·진척 100%, 프로젝트 main COMPLETE로 바꾸지 않음 |
| VALID/INVALID 결과와 payload 조합 | VALID에는 집계만, INVALID에는 비어 있지 않은 사유만 허용 |

```sh
uv run --locked python -m unittest discover -s tools/tests -p 'test_plan_progress.py'
uv run --locked python -m unittest discover -s tools/tests -p 'test_*.py'
uv run --project backend ruff check tools
uv run --project backend pyright tools
```

macOS에서 package 설치가 필요 없는 직접 script 집계 fixture·CLI **10건**, 전체 루트 unittest **52건**이 모두 통과했다. tools 전체 ruff와
pyright는 오류 0이다. 기존 다섯 PLAN을 읽기 전용 validator로 집계한 결과는 모두 VALID였으며
생성 문자열이 원문과 같았다(`changed=False`: YAML 52/52, GUI 19/19, AI 19/19, lang2 30/30,
validation-lab 21/28). 이 확인은 일회성 읽기 전용 호환성 증거이며 실제 문서의 현재 숫자를
unittest에 고정하지 않았다. 추가로 저장소 밖 임시 작업 디렉터리에서 `python -I -S -X utf8`와
공용 script 절대경로를 사용해 다섯 profile의 기본 root `--check`를 모두 통과했다. site package와
editable 설치에 기대지 않는 직접 실행 경로를 확인한 것이며, 검증한 로컬 Python은 3.14.3이다.
실제 Windows PowerShell wrapper는
이 환경에서 실행하지 못했으며 Windows에서 기존 인자/경로/종료코드를 한 번 확인하는 일이 남았다.
다음 결정은 tracker 정책을 바꾸는 것이 아니라, 같은 Python 계산을 CI와 Windows 진입점에서도
계속 사용할지 검토하는 것이다. 생성 집계를 갱신해야 할 때만 명시적으로 write 명령을 실행한다.
