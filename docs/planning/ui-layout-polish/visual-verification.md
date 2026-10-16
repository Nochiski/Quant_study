# UI/UX 배치·가독성 브라우저 검증

## 범위와 기준

이번 작업은 UI/UX에서 멈춘다. 성과 검산, 스트레스 실행, 내보내기, 실자료 연구, 브로커 연결은 포함하지 않는다.
디자인 기준은 [MASTER](../../../design-system/strategy-workbench/MASTER.md), 값의 정본은
`frontend/src/app/styles/tokens.css`다. 화면별 CSS에 색상·폰트 값을 새 정본으로 복사하지 않는다.

일반 실행 폼은 14px 이상의 값 글자와 40px 컨트롤을 사용한다. inspector·도구 모음의 13px 압축
컨트롤과 표의 보조 정보를 모두 14px로 늘리는 검사는 하지 않는다. 실제 브라우저에서 설정 패널의
좌우 경계·가림·내부 넘침, 서로 다른 native 입력 종류의 높이, select 선택에 따른 칸 활성화,
Enter/Space/Escape와 초점 복귀를 함께 확인한다. 글자 크기만으로 화면 품질 전체를 판정하지 않는다.

## 환경과 수정 전 증거

- 작업공간: `menhaden-ui-layout-polish`, 원래 `menhaden`에서 실행 중인 개발 서버와 분리했다.
- frontend `npm ci`, backend의 별도 `.venv`에 parquet/equity/llm extras와 Rust release를 준비했다.
  기존 가상환경을 연결하거나 공유하지 않았다.
- 기존 `npm run test:e2e` runner를 사용했다. 백엔드 포트는 `18041`, preview는 `15184`이며
  strategy/research/continuous/assistant DB·secrets·산출물 여섯 경로는 runner의 임시 폴더다.
- `UV_NO_SYNC=1`, mock 자료와 가짜 공급자로 실행했다. 실제 개발자 DB나 실자료를 열지 않았다.
- 수정 전 macOS Chromium에서 1440×900 light/dark, 360×800 light의 편집 화면·열린 실행 설정을
  각각 촬영했다. 일반 날짜 입력은 세 경우 모두 12px였다. 360px의 팝오버는 왼쪽 화면 밖으로 잘렸다.
- 수정 전 원본 여섯 장과 trace는 로컬 검토 경로 `/tmp/menhaden-ui-layout-before-20261017`에
  따로 보존했다. 임시 검토 자료이며 저장소의 Windows 기준선이나 CI 산출물로 취급하지 않는다.

새 US-DM-05 테스트는 같은 동작을 화면 크기별 별도 스토리로 세지 않고 하나의 테스트에서 단계별로
검사한다. 수정 전 최초 촬영은 세 경우 각각 14px 기준 실패로 문제를 재현했다. 이 실패는 기존
게이트의 회귀가 아니라 새 가독성 수용 기준의 수정 전 관측이다.

## 수정 후 확인

`dm.layout-polish.spec.ts`는 아래를 실제 브라우저에서 확인한다.

- 밝은·어두운 데스크톱과 폭 360px에서 패널 전체가 화면 안에 있고 문서 가로 넘침이 없다.
- 초점이 간 날짜 입력의 중심이 다른 요소에 가려지지 않고, 일반 폼의 값과 컨트롤을 읽을 수 있다.
- native select의 초점·선택과 종속 입력의 disabled 상태가 유지된다.
- Escape로 닫으면 요약에 초점이 돌아오고 Enter/Space로 다시 여닫을 수 있다.
- 360px에서 구조→계약→구조 전환 시 한 패널만 보이고 구조 패널 높이는 320px 이하다.
  마지막 스니펫의 Tab·첫 접기 단추의 Shift+Tab과 추가 이동 여섯 번 동안 초점이 가려지지 않는다.
  데스크톱 구조 필터에 초점을 둔 채 좁혀도 열린 구조와 초점이 유지되며 편집기를 가리지 않는다.
- 편집 화면·실행 설정·전략 목록·공급자 설정 화면을 독립 PNG로 남긴다.

기존 US-DM-04와 US-SM-14·15 흐름에서 실제 mock 결과·실험 화면의 desktop/mobile 캡처를 추가한다.
기존 결과 계산이나 기대 수익을 새로 구현하지 않고, 기존 fixture·API와 화면 흐름을 재사용한다.
대표 화면의 제목 위계·시작선·여백·select 화살표·disclosure 표식은 캡처를 직접 검토한다.

2026-10-17 macOS Chromium에서 대표 세 흐름을 확인했다. 실행 설정 검증에서 날짜/select 높이 차이,
compact 크기 상속과 좁은 화면 전환 시 초점 손실을 재현하고 수정했다. 구조 패널은 좁은 화면에서
편집기를 덮지 않는 최대 320px 일반 배치이며, 최초 모바일 진입은 닫히고 데스크톱에서 열린 채
좁히면 열림과 현재 초점을 유지한다. 마지막 스니펫 Tab·첫 접기 Shift+Tab도 실제 브라우저에서
가려지지 않았다. 당시 소스로 대표 세 흐름을 함께 실행해 모두 통과했다(총 1.1분, 결과 4.9초·실행 설정 6.2초·실험 49.5초). 14px·40px, 패널 경계·가로 넘침·
가림, native select, 키보드 닫기·초점 복귀와 모바일 패널 전환을 확인했다.

수정 후 PNG는 `/tmp/menhaden-ui-layout-after-20261017`에 보존했다. `stories-dm.layout-polish-*`의
`1440-light-*`, `1440-dark-*`, `360-light-*`는 편집 화면·설정 전체·설정 패널이며
`strategy-list.png`, `settings.png`도 같은 폴더에 있다. `stories-dm.backtest-result-*`에는
결과 desktop/mobile, `stories-sm.experiments-*`에는 실험 desktop 사진이 있다. 모바일 설정 패널과
어두운 편집 화면, 실험 화면은 직접 열어 확인했다. 모바일 전체 결과 사진은 긴 문서여서 단일 축소
미리보기만으로 모든 행의 가독성을 판정하지 않았다.

E2E TypeScript 검사와 변경한 세 E2E 파일의 ESLint, 유저 스토리 하네스(스토리 40개·태그 E2E 49개)가
통과했다. 전체 frontend 단위 테스트·정적 검사와 Windows 시각 게이트는 별도 최종 검증으로 보고한다.

## 추가 디버거 경계 검사

기존 `workbench.infrastructure.spec.ts`의 1440px 밝은 테마 검사에서 연결 추적 영역이 패널 하단보다
약 12px 아래로 넘치는 회귀를 기준선 비교 전에 재현했다. 결과 영역이 가용 높이에 맞춰 수축하도록
허용하고 기존 내부 스크롤을 유지했으며, 폼은 subgrid의 공유 행으로 정렬했다. 변경은 디버거 CSS에
한정된다. 1440/1920px × 밝은/어두운 네 프로젝트에서 viewport·폼·탭·연결 추적 containment 단언이 모두
통과했다. 실제 이미지 네 장을 직접 검토해 입력 기준선·버튼 행 정렬·내부 스크롤 경계를 확인했다.

macOS 실행 전체는 네 프로젝트 모두 기존 기준선과의 엄격한 `toHaveScreenshot` 비교에서 실패했다
(`4 failed`). 그 앞의 동작·경계 단언 통과를 시각 게이트 전체 통과로 기록하지 않는다. 로그는
외부 증거 폴더의 `mac-debugger-aligned.log`, 실제 이미지와 trace는 `mac-debugger-aligned`에 보존했다.
Windows 최종 기준선과 릴리스 게이트는 수정 후 head의 CI 산출물로 별도 확인한다.

## 첫 Windows 실행에서 확인한 동작 회귀

head `217f7d8feba0fc39821e27ee88dadb247380c645`의 Windows 실행은 기존 20분 job 제한으로
종료됐다. 시각 차이만 난 실행으로 취급하지 않는다. `38936582505`의 로그와 업로드된 visual
산출물에서 아래 세 동작 실패를 확인했다.

- 기존 YAML 전체 흐름의 TargetTape 탭 클릭과 파생 팩터 흐름의 원시 데이터 탭 클릭이 디버거 본문·
  경고·상단 영역에 가려 각각 180초 제한을 넘었다. 앞선 짧은 경계 검사만으로 긴 실제 결과의 접근성을
  충분히 확인하지 못했다.
- 모바일 레시피 편집 흐름에서 폭 360px의 레시피가 222px로 줄어 기존 최소 232px 단언에 실패했다.

디버거 결과에 최소 높이를 두고 탭·경고가 수축하지 않게 하여 기본 크기에서도 내부 스크롤로 결과에
접근하게 했다. 모바일 레시피는 불필요한 좌우 여백을 회수해 읽고 조작할 폭을 확보했다. 수정은 두 CSS
파일에 한정하며 기존 테스트·CI 제한·스냅샷 허용치는 바꾸지 않았다. 수정 후 같은 흐름의 로컬 실행에서
YAML 전체 흐름 13.5초, 파생 팩터 5.0초, 레시피 편집·좁은 화면·뒤로가기 5.7초로 통과했다.

첫 Windows 산출물은 외부 증거 경로
`/Users/sangmok/quant-ledger/validation-evidence/ui-layout-polish-20261017/windows-first`에 보존했다.
실제 이미지 8개는 원본·재시도의 SHA256이 모두 같았으나 그래프 진단 이미지 1개는 미생성됐다.
`review-217f.md`와 `mapping-217f.json`은 이 불완전한 진단 자료의 경로와 해시를 기록한다. 이 head의
이미지를 수정 후 최종 기준선으로 재사용하지 않는다.

## 전체 Mac 실행과 비모달 설정의 초점 이동

추가 수정 뒤 전체 Mac 실행은 8.9분에 `63 passed`, `11 failed`, serial 흐름 1개 미실행으로 끝났다.
실패 11개는 예상한 엄격한 시각 차이 9개, graph-ideas 흐름의 설정 닫기 helper 1개, 스크롤바 전제
1개다. 이를 전체 통과로 보고하지 않는다.

모바일 고정 설정 패널 뒤의 summary를 포인터로 누르던 helper는 패널 안의 실제 닫기 단추를
사용하도록 바꿨다. 키보드 모드도 같은 단추에 초점을 두고 Enter를 누르며, 입력 숨김과 summary
초점 복귀를 확인한다. force click·조건 건너뛰기·timeout 변경은 하지 않았다.

별도 DM05 회귀에서 마지막 OOS 날짜의 native 세그먼트를 Tab 최대 여덟 번으로 통과해 외부로
이동했지만 패널이 남는 문제를 수정 전 소스로 재현했다. 로그와 trace는 외부 증거 폴더의
`mobile-settings-focus-leave-before.log`와 같은 이름의 폴더에 있다. 비모달 설정은 details 밖의
실제 요소로 초점이 이동할 때 패널만 닫고 외부의 원래 초점을 유지하도록 수정했다. 내부·summary
이동과 대상이 없는 blur는 열림을 유지한다. Escape·닫기 단추는 기존대로 summary에 초점을 돌린다.

스크롤바 검사는 macOS에서 고전 스크롤바 폭이 0이라 `Expected > 0 / Received 0` 전제에 실패했다.
main `4451dbe1`의 독립 worktree·venv에서도 같은 결과를 재현해 신규 UI 회귀와 구분했다. 근거는
외부 증거 폴더의 `mac-scrollbar-main-probe/SUMMARY.md`다. 이 테스트나 플랫폼 전제를 변경하지 않았다.

최종 영향 검사에서는 기본 디버거 13.5초·legacy 6.7초·파생 팩터 5.0초·graph-ideas의 마지막 모바일
설정 흐름 21.8초가 통과했다(`mac-affected-flows-final.log`). 같은 명령의 DM05는 패널이 정상적으로
닫힌 뒤 숨긴 group을 role locator로 다시 찾던 테스트 정의 때문에 실패했다. Tab 전에 확보한 같은
DOM 요소로 이탈 여부를 평가하도록 바로잡고, native summary를 포함한 초점 가능 의미와 외부 초점
노출 단언을 유지했다. DM05 단일 재실행은 9.1초에 통과했다(테스트 6.5초,
`mobile-settings-focus-leave-final.log`). 두 실행 사이 제품 소스는 같고 테스트 정의만 수정했다.
이를 한 명령에서 다섯 개가 모두 통과한 결과로 합쳐 쓰지 않는다.

최종 frontend 단위 검사는 109개 파일·1,378개 테스트가 80.80초에 통과했다
(`frontend-unit-release.log`). lint·typecheck·build·E2E typecheck도 종료 코드 0으로 완료했다.
최종 소스 head `ce11ebe867fcecc8e2152a80f4e8206eba02a7fc`의 Windows run `38942206761`은
11.3분에 `65 passed`, 시각 차이 `9 failed`, serial 1개 미실행으로 끝났다. 실패 9개는 모두 엄격한
`toHaveScreenshot` 차이이며 별도 동작 실패는 없었다. 원본·재시도 실제 이미지 9쌍의 SHA256은
모두 같았다. 외부 증거 폴더 `windows-ce11`의 `mapping.json`·`REVIEW.md`·`browser.log`에
head·run·해시와 실제/기준선/차이 이미지의 대응 관계를 보존했다.

루트 검토자가 실제 이미지 9장과 기존 기준선·차이 이미지 18장을 직접 열어 팔레트·타입·여백·입력
정렬 변경을 확인했다. 1440px 연결 추적의 내부 스크롤과 그래프 이미지의 기존 마젠타 mask도
확인한 뒤 정확한 Windows actual 9개의 복사를 승인했다. 해당 9개 기준선만 갱신했으며 픽셀
허용치·테스트·CI 설정은 변경하지 않았다. 이 갱신 자체를 전체 릴리스 통과로 주장하지 않는다.

최종 전체 통과 여부의 정본은 [PR #534](https://github.com/Nochiski/Quant_study/pull/534)의
최신 head CI다. 기준선 갱신 후 실행 결과는 그 CI에서 확인한다.

## Windows 기준선 갱신 절차

기존 기준선은 Windows Chromium용이며 macOS 사진으로 덮어쓰지 않는다. 영향 대상은
`workbench.infrastructure.spec.ts`의 1440/1920 × light/dark 편집 화면·디버거 8장과
`workbench.workflow.spec.ts`의 `graph-node-diagnostic.png` 한 장이다.

1. 변경된 CSS와 브라우저 동작 검사를 먼저 검토한다. macOS after 캡처는 배치 검토 증거로만 쓴다.
2. Windows CI의 실제 screenshot 실패 산출물에서 actual/diff/expected를 비교한다. 고정 Chromium·
   웹폰트·locale/theme·viewport 조건과 글자 잘림·누락·가림을 확인한다.
3. 의도한 디자인 변경만 확인한 뒤 Windows에서 기존 `npm run test:e2e:update`를 사용하거나,
   검토한 Windows actual 이미지로 해당 기준선만 갱신한다. 다른 파일이나 Mac 이미지는 섞지 않는다.
4. update 옵션 없는 기존 `npm run test:e2e`와 유저 스토리 목록 대조가 통과해야 최종 기준선이다.
   기준선 차이를 무시하거나 픽셀 허용치를 넓혀 통과시키지 않는다.

로컬 캡처와 엄격한 Windows 기준선 통과는 서로 다른 증거로 보고한다. GitHub CI 완료·머지를
로컬 테스트 통과와 동일하게 기록하지 않는다.
