"""factor_inputs — 모델 전용 입력 층(플랜 `docs/plans/2026-09-24-v3-merge.md` M2 W1-b · T2.2b).

equity(KRX 확정) 판과 stage WISE 판에서 `model.contracts.FI_TABLES` 8표를 판 기준일 D 마다 굽는다.
엔진(`model.engines`)과 호환 계층(compat, 나중에)은 이 층만 읽는다(결정 D-9). 새 계산은 창 자르기·
시총·신선도 유예(T2.11) 셋뿐이고 단위·정수화는 compat(v3 미러)와 같다.

basis 는 아침 확정판(`--basis morning`)과 장 마감 판(`--basis evening`, 컷오버 T-2 — 직전 거래일
확정판을 고정해 읽고 오늘 T 의 자리를 만든다. T 행 얹기는 PR-5)이다. 설계·게이트·신선도 규칙은
`docs/FACTOR_INPUTS.md`.
"""
from __future__ import annotations

from .build import BuildResult, FactorInputsError, build

__all__ = ["BuildResult", "FactorInputsError", "build"]
