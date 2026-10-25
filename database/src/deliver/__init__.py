"""모델 산출물 전달층 (플랜 `docs/plans/2026-09-24-v3-merge.md` M2 W2 T2.5b · 결정 D-12).

model 판(`data/model/`)과 factor_inputs 판(`data/factor_inputs/`)을 **읽기만** 해서
매일 엑셀 · 주간 엑셀을 만들고 텔레그램으로 보낸다. 점수를 다시 계산하지 않는다 — 엔진이 쓴 값을
옮기고, 표시용 가공(백분위 재순위 · 윈저라이즈 · 부호 전환 표식)만 여기서 한다.
규격 정본은 `docs/MODEL_EXCEL_SPEC.md`, 운영 문서는 `docs/MODEL_DELIVER.md`.
"""
