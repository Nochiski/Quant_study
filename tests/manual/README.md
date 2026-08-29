# 수동 검증 도구 (tests/manual)

pytest가 수집하지 않는 수동 실행·시각 확인용 산출물을 둔다.

## 엔진 vs Zipline 대조 리포트

커스텀 `backtest_engine`과 Zipline을 같은 데이터(005930 일봉)·같은 전략·같은 비용
가정으로 실행해 equity curve를 세션 단위로 비교한다. Zipline이 설치된
implementation 프로젝트에서 실행한다:

```bash
cd 2026-08-17/sangmok/implementation
uv run python scripts/compare_engines.py
```

- 콘솔에 시나리오별(OK/MISMATCH) diff 요약이 출력되고, tolerance 초과 시 exit 1.
- `compare_results.json` — 파라미터, diff 통계, 양쪽 equity curve 원본.
- `compare_report.html` — 브라우저로 열면 equity 겹침 차트와 상대 오차
  차트, 통계 표를 볼 수 있는 자체완결 페이지 (외부 리소스 없음).

### 정합 규칙 (요약)

- 판단: 세션 T 종가 기준 → 체결: 다음 실제 세션 시가 (Zipline은 하네스 전용
  `NextBarOpenSlippage`로 정렬).
- 수량: `backtest_engine.sizing.floor_delta_shares`를 양쪽이 공유.
- Zipline 캘린더는 XKRX 명시 (기본 XNYS는 한국 휴일을 세션으로 오처리).
- 거래정지 행(가격 0)은 양쪽 모두 세션이 아닌 것으로 처리
  (커스텀: 로더 CLAMP drop / Zipline: 저장소가 0을 NaN으로 읽는 성질 이용).

### 4단계(주문 생명주기) 이후의 대조 범위

- 시장가·전량 체결 경로는 4단계 전후로 동일하다 (기본값 `NoSlippage`, 참여율 무제한).
- **슬리피지 대조(보류)**: 스펙은 `VolumeShareSlippage(0.025, 0.1)`를 양쪽에 두고
  대조하도록 계획했지만, Zipline의 `VolumeShareSlippage`는 다음 bar **종가** 기준
  체결이라 하네스의 `NextBarOpenSlippage`처럼 시가 기준 변형을 별도로 정의해야 하고,
  현재 이 저장소에는 대조용 원본 CSV(`data/raw`)가 없어 실행·검증할 수 없다.
  원본 데이터가 준비되면 `NextBarOpenVolumeShareSlippage`를 추가해 시나리오를 확장한다.
- **공매도 대조(보류)**: 비중 −1.0 단일 종목 buy-hold를 양쪽에서 돌려 세션 equity를 대조하는
  시나리오(`short-hold`)를 계획했으나 같은 이유(원본 CSV 부재)로 실행하지 못했다. Zipline은
  공매도 회계를 기본 지원하므로 기준으로 쓸 수 있다.
- 지정가·스톱은 Zipline이 종가 기준으로 발동을 판정해 규칙이 다르므로 대조 대상이
  아니다. 이 경로는 `tests/test_broker.py`의 규칙표와 `tests/test_order_lifecycle.py`의
  손계산 골든으로 검증한다.
