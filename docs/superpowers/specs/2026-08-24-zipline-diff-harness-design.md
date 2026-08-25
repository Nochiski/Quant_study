# Zipline 대조 하네스 설계와 발견 (2026-08-24)

백테스트 엔진 v1의 회계를 외부 기준(Zipline)과 대조해 검증하는 하네스.
설계 노트 검증 기준의 "Zipline 대조: 동일 데이터·동일 전략·동일 비용 가정에서
equity curve 차이 비교"를 구현한다.

## 아키텍처

- **단일 프로세스**: Zipline이 설치된 `2026-08-17/sangmok/implementation` 프로젝트에
  하네스를 두고, 루트 `backtest-engine`을 editable path 의존성으로 추가.
- 모듈: `engine_compare.py`(양쪽 실행), `engine_diff.py`(순수 diff 로직),
  `compare_report.py`(자체완결 HTML 렌더), `scripts/compare_engines.py`(오케스트레이션).
- 산출물: 리포 루트 `tests/manual/`에 `compare_results.json` + `compare_report.html`.
  pytest가 아닌 수동 validator 스크립트다 (산출물 단언 테스트 금지 규칙).

## 정합 규칙

| 항목 | 커스텀 엔진 | Zipline 정렬 방법 |
|---|---|---|
| 체결 시점 | T 종가 판단 → T+1 시가 | 하네스 전용 `NextBarOpenSlippage` (기본 FixedSlippage는 T+1 종가) |
| 수량 | `floor_delta_shares` | 같은 함수를 import해 명시적 주식 수로 `order()` (기본 `order_target_percent`는 round) |
| 수수료 | 체결 금액 × bps | `PerDollar(bps/10000)` |
| 캘린더 | CSV 세션 | `trading_calendar=XKRX` 명시 |
| 거래정지 행 | 로더 CLAMP가 drop | 저장소가 가격 0을 NaN으로 읽는 성질 + open NaN 세션 스킵 |
| 현금 경계 | cash-cap | 목표 비중 0.7로 경계 회피 (Zipline은 음수 현금 허용) |

시나리오: ① Buy&Hold(1회 진입 — 순수 회계 격리) ② 골든크로스(20/60 MA,
밴드·긴급청산 없음 — 리밸런싱 경로). 허용 오차: 세션별 equity 상대 오차 1e-6.

## 결과

005930 일봉 1,244세션(2015–2020), 수수료 15bps:

- **buy-hold: 최대 상대 오차 0.000e+00** (완전 일치)
- **golden-cross: 최대 상대 오차 3.226e-16** (배정밀도 말단 자릿수)

v1 엔진의 체결·수수료·평가·리밸런싱 회계가 Zipline과 일치함을 확인.

## 하네스가 발견한 결함 (수정 완료)

### DEFECT-001: 거래정지 행에서 0원 체결

- **상황**: PyKRX 수정주가 CSV에 거래정지 기간 행이 `open=high=low=0, close=직전가,
  volume=0`으로 존재 (예: 삼성전자 액면분할 2018-04-30~05-03).
- **인풋**: 골든크로스 전략이 정지 직전 드리프트 리밸런싱 매도 주문 생성 →
  다음 세션(정지일) 시가 0원에 체결.
- **에러 위치**: `src/backtest_engine/types/market.py` Bar가 0 가격을 허용 +
  `engine/broker.py`가 bar.open을 무검증 사용.
- **위험성**: 보유 주식이 0원에 매도되어 equity가 세션마다 주가만큼 증발
  (silent corrupt). 대조에서 세션당 정확히 -53,000원 차이로 발견.
- **수정**: Bar·FillEvent에 가격 > 0 불변조건 추가, 로더 CLAMP 정책이 비양수
  가격 행을 drop하고 `dropped_rows`로 보고. 재현 테스트
  `tests/test_data.py::TestCsvLoader::test_clamp_policy_drops_halt_rows`.

### DEFECT-002: 웹 백테스트가 XNYS(뉴욕) 캘린더로 실행됨

- **상황**: `run_algorithm()`은 `trading_calendar` 미지정 시 XNYS가 기본값.
- **인풋**: 기존 앱 `backtest.py`의 모든 웹 백테스트 호출.
- **에러 위치**: `2026-08-17/sangmok/implementation/src/quant_study/backtest.py`
  `run_moving_average_backtest()` — `trading_calendar` 인자 누락.
- **위험성**: 설날·추석 등 한국 휴일이 세션으로(유령 70일), MLK데이·굿프라이데이 등
  미국 휴일이 휴장으로(실거래일 38일 소실) 처리되어 신호 시점과 체결이 왜곡됨.
- **수정**: `trading_calendar=get_calendar("XKRX")` 명시.

## 후속 (로드맵 연결)

- 이 diff 하네스는 6단계(Rust 포팅) 결과 대조에 그대로 재사용한다.
- `engine_diff.diff_equity_curves`는 파일 입력으로도 확장 가능 (2-프로세스 diff).
