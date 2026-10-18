# 금액별 용량 확인

기존 capacity preview/create/report API를 읽는 사용자 행동이다. 원본 실행 요청을 유지하고 초기 자본만 바꾸며, N·대표 수익률·용량 한계는 backend 값을 표시한다. 회귀 화면·query는 backtest entity가 소유한다.

ROOT의 최종 mock 브라우저 검증은 2개 통과(56.8초), 근거는 외부 검증 폴더의 `validation-capacity-regression-20261018/browser-mock-final.log`다. 용량 생성·영속 상세·N 불변과 실제 mock 회귀 부족/별도 합성 숫자 표시를 구분해 확인했다. ROOT가 SVG fill:none과 360px dark PNG를 직접 검토했다. CS11/12의 스토리·태그·traceability를 함께 등록했다.

ROOT 최종 실자료 opt-in QA는 1개 통과(3.6분, 외부 `validation-capacity-regression-20261018/browser-real.log`)했다. `real-final` 용량/회귀 PNG를 ROOT가 직접 확인했다. 초기 monthly frame0·daily enum 거절·정상 recheck 추가를 거절한 QA 단언 실패 세 건은 각각 별도 실패 로그로 보존했다. 최종 QA는 N·대표값·기존 기록 보존과 새 recheck3개 추가를 구분하고, 회귀 조회 전후 원장 불변을 확인한다. 기능·표시 QA이며 수익성·봉인 구간·키움 연결의 완료를 주장하지 않는다. 계약 정본은 [V5-06](../../../../docs/planning/validation-lab/V5-06-capacity-factor-reports.md)이다.
