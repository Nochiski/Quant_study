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
