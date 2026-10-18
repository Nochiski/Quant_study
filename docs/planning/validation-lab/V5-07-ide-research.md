# V5-07: 편집 문맥을 지키는 연구 진입점

최종 목표는 비용 후 수익을 검증한 자동매매다. 신호의 유망함과 실제 계좌 성과는 S03/S04에서
구분하며, 편집기에서 실험으로 옮기는 이번 기능은 수익성·실거래 준비를 판정하지 않는다.
기존 lang2 P4-04(#428)·P5-02(#430)의 머지 기록을 확인한 뒤 연구 진입점을 연결한다.

## 행동과 소유권

- 기존 상단 연구 표시 자리를 native disclosure로 재사용한다. 계열 N은 기존 `trialLedgerQuery`의
  `trial_count` 그대로 읽는다. 조회 중·실패·계열 없음은 0으로 바꾸지 않고, 전체 원장은 기존 전략
  이력 화면에서 읽는다. 원장 행·통계·시도 판정의 사본을 만들지 않는다.
  현재 전략 목록 라우트는 계열·원장 탭의 직접 선택을 지원하지 않는다. 링크는 **전략 목록에서 시도 원장 찾기**로
  이동 범위를 명시하며, 목록에서 전략을 펼치고 원장 탭을 선택해야 한다. 현재 계열로 바로 가는 링크라고 설명하지 않는다.
  연구 메뉴 안의 Escape는 메뉴만 닫고 summary로 초점을 돌린다. 외부 초점 이동은 외부 초점을 유지하며 메뉴만 닫는다.
- 실험으로 보내기는 `useRunBacktest`가 만든 현재 `BacktestRunSpec`을 기존 실험 만들기에 전달한다.
  현재 원문과 저장본이 같고, 선택 파라미터가 해소되고, 현재 실행 환경·계산 계획이 준비돼야 한다.
  새 문서·dirty 원문·동결 판본은 저장 또는 업그레이드를 안내한다. 옛 저장본으로 대신 보내지 않는다.
- 선택 실행의 **파라미터 값만** 사용하며 환경은 현재 편집기의 값이다. 과거 실행 전체 재현이 아니다.
  선택 없음은 현재 문서 기본값이다. 원문·환경·선택값을 이 메뉴가 재계산하거나 고치지 않는다.
- 전달 수단은 typed browser history state의 `experimentSeed`다. 서버 캐시나 영구 연구 초안이 아니다.
  `run`/`from` URL이 있으면 그 명시적인 기반을 우선하며 seed를 무시한다. seed도 URL 기반도 없으면
  기존 기반 실행 선택 안내를 보이고 임의 기본값으로 실험을 만들지 않는다.
- 실험 Form의 편집 축·제약·분할은 router가 부여한 `location.state.__TSR_key`와 기반 종류·ID를
  입력 세션 경계로 쓴다. 같은 history entry의 일반 재렌더/조회 갱신은 사용자 편집을 유지한다.
  다른 seed 방문 또는 run/from 기반으로 옮기면 그 기반의 초기값으로 새 Form을 연다. 뒤로/앞으로
  돌아와도 떠나기 전 편집값을 별도 보관·복원하지 않고 해당 entry의 기반 초기값에서 시작한다.
  이는 명시한 초기화 경계이며 새 초안 저장소·클라이언트 캐시를 추가하지 않는다.
- 기존 실험 Form이 탐색·분할과 미리 계산을 소유한다. 명시적인 대기열 접수 전에는 실험 생성 POST가
  없다. 분할이 측정 범위를 소유하므로 기존 Form의 `metric_windows=[]` 규칙을 유지한다.
- 레시피 탐색은 기존 `search.recipe`·`path`·Graph/Recipe projection만 전환한다. 중간 결과에서
  명시한 경우 widget의 편집 작업공간으로 복귀한다. 문서·undo·파라미터·환경 소유자는 유지한다.

## 수용·검증 범위

US-SM-12의 기존 시도 원장 검사를 유지하고 IDE 진입점 검사를 추가한다. 합성 회귀는 dirty/신규/
동결/미해소 선택값 차단, 현재 요청 전달, URL 기반 우선, 명시 생성 전 POST 없음과 원문 보존을 확인한다.
최종 ROOT mock 과업은 **2 passed, 12.8초**다. 1440×640·360×640에서 N·레시피 왕복·미저장 안내·
선택값/현재 환경의 미리 계산 전달을 확인했다. 데스크톱 case에는 짧고 긴 제목 각각의 768·1024·1440px
메뉴 검사를 포함했다. ROOT 전체 frontend **1521 passed, 91.96초**는 마지막 Form CSS·ARIA 보강 전 증거다. 이후 기존 실험 단위
**26 passed**와 최종 실제 과업 2건을 재확인했으며 서로 합산하지 않는다.

### 메뉴 배치와 검사 경계

처음 viewport bbox와 버튼 중심만 확인한 과업은 통과했지만 ROOT의 실제 PNG 검토에서 짧은 제목의
팝업 왼쪽 문장이 sidebar 아래로 잘린 것을 발견했다. 제목 끝에 오른쪽을 맞추던 배치를 폐기하고 기존
IDE header의 왼쪽 여백을 기준으로 main 안에 펼친다. z-index를 올리거나 원문·환경 상태를 바꾸지 않았다.
회귀는 데스크톱 main 경계와 각 텍스트 줄 양끝의 실제 hit를 함께 검사한다. 모바일은 기존 viewport 고정
배치를 유지하므로 main 경계를 강제하지 않고 viewport 경계·줄 양끝 hit를 검사한다. 중간 QA의 모바일 실패는
이 배치 차이를 무시한 helper 기대 오류였으며 제품 폭을 줄이거나 가림 검사를 없애지 않았다.
ROOT는 데스크톱 짧고 긴 제목 768·1440px와 모바일 메뉴 PNG를 직접 확인했다. 최종 메뉴 가림이 해소됐음을 확인했다.

### 기능별 실자료 기술 QA

메타데이터 첨부를 보존한 ROOT opt-in 실자료 과업은 **1 passed, 6.0초**(본문 4.4초)다. 2024-01-08~12의 `price.close`·
3세션 모멘텀·매 1세션 리밸런스 선택 실행에서 파라미터 `scale=0`을 GET 원본으로 읽고, 현재 편집기 환경과 함께
실험 미리 계산에 전달했다. 짧은 기간에는 기존 학습/검증 창이 없어 `experiment.split.no_window` 422를
표시하며 실험 생성 POST는 0이다. 이를 성공한 실험 접수나 수익성 판정으로 기록하지 않는다. 원본 요청·계열 N은 유지됐다.

기존 완료 결과를 읽어 manifest의 실행 ID·데이터 판·실행 지문·저장 원문 출처·해소 파라미터·환경과 원본 요청의
일치를 확인했다. 외부 `validation-evidence/ide-research-20261018/real-metadata-final/`에 `test-results`·
`playwright-report` 원본을 복사하고 내장 첨부를 추출했다. `selected-run-original-request-and-result.json`은
제출 원문·저장 응답·GET 원본 요청·전체 완료 결과를 담으며 SHA-256은
`f23e09a7a4c53d5899f87764174768ebbaf4f4e719c3952c989aa3cefb06e2cf`다. `attachment-provenance.json`에 추출 출처를 남겼다.
데이터 판은 `2164ba776cbd99f2:3d4aa095f7837c29`, 실행 지문은
`02f8fd99c14b5ddcc2d24237483592a5b11b32ad2e614da7b5bc13665c3e3a3d`다.

Form 가독성 마감 뒤 합친 코드에서도 실자료 과업 **1 passed, 7.0초**를 확인했다
(`browser-real-integrated-final.log`). 이 재검증에서 새 JSON을 별도 보존했다고 주장하지 않으며 위 6초 실행의
실제 원본 첨부와 구분한다. 최종 mock 근거는 `browser-mock-form-bounds-final.log`·`mock-form-bounds-final`,
메타데이터 원본 근거는 `browser-real-final.log`·`real-metadata-final`이다. 초기의 가림을 놓친 통과와 메타데이터 첨부 전 실자료 통과를 최종 증거로 대신하지 않는다.
원장·S01 보존본 변경, 봉인 열람·브로커·실계좌 연결은 하지 않았다. 이 기록은 기술 과업 검증이며 실제 사용자 시험·
알파/비용 후 수익성 검증이 아니다. 공식 CI·머지는 해당 PR의 checks·merge 기록에서 확인한다.

### 연결된 실험 Form의 가독성 마감

편집기에서 보낸 현재 입력을 새 실험에서 읽을 때 panel 여백과 라벨 기준선이 맞지 않고 좁은 표 제목이 한 글자씩
세로로 갈라지는 문제가 있었다. 페이지 고유 CSS로 panel·문단·fieldset·legend 위계와 label/control 정렬을 맞추고,
모바일 분할·제약은 한 열로 읽도록 했다. 표는 읽을 수 있는 최소 폭을 두고 이름·키보드 초점·스크롤 안내가 있는
기존 wrapper 안에서만 가로 스크롤한다. Form의 grid 열은 내용 폭에 맞춰 수축하며 표 밖 안내·입력·범례는
panel 경계를 넘지 않는다. 원문·계산·API·Form state/history와 기존 요청·검증 owner는 바꾸지 않았다.

최종 ROOT 과업은 전체 Form locator PNG와 모바일 input/select/legend/안내의 경계, 하단 대기열 버튼까지
실제 스크롤한 뒤 center hit를 검사한다. ROOT가 360px 전체 Form PNG를 직접 보고 안내·범례·입력·버튼의
가림이 없고 표만 내부 스크롤임을 확인했다. 최종 frontend/E2E 타입·lint 및 하네스 **41개 스토리·64개 태그 e2e**도 확인했다.

선택 파라미터 #548은 main `28bafecf`, 후보 지도 #549는 main `45328d0b`로 머지됐다(각12개 검사 통과).
보고서 #550은 head `7c1a7a5b`/base main으로 검토 중이며 이 IDE 문서는 미머지 스택의 완료를 선반영하지 않는다.

### 승인된 Windows 기준선 갱신

Windows run `39084732903`, source head `c9755d8b3cb9065e1aa5d4217dd4624ee4f556ea`의
1440·1920 × light·dark `strategy-workbench-actual.png` 네 장을 ROOT가 actual/diff와 기존 expected를 직접 보고 승인했다.
변경 범위는 좌상단의 수동 연구 badge를 native 연구 disclosure로 바꾼 표시이며 나머지 정렬은 유지됐다.
승인한 Windows 원본 actual 네 장만 정본에 복사했고 원본·정본 SHA256을 대조했다.
다른 PNG·제품 소스·단언·픽셀 허용치는 변경하지 않았다. 해당 CI는 시각 차이 네 건(light167px/dark258px, 두 폭 동일) 이후
기존 sm.experiments 구간을 진행하다20분 job 제한으로 취소되어 나머지 과업의 통과나 전체 완료를 주장하지 않는다.
적용 전 코드 `7e61ce6e`는 #550 알림 검증 정정을 포함하며 ROOT의 실제 `sm.experiments` 두 과업은
**2 passed, 52.7초**로 확인했다(`browser-sm-notice-integrated-final.log`). 이 기준선 갱신과 부분 과업 통과는
최종 새 head 전체 CI 성공을 선반영하지 않는다.
