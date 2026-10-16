# S02 회계·데이터 한계 검산

## 의도와 판정 범위

비용 후 수익 자동매매 연구에서 회계 착시를 알파로 오해하지 않도록, 평가된 신호 → 종목 선정 → TargetTape → 엔진 → 실행 어댑터 결과의 현금·수량·비용·평가액을 손으로 검산한다. 신호의 예측력이나 실데이터 수익성을 인증하는 패킷은 아니다.

새 검산은 `backend/tests/integration/test_s02_accounting.py`가 소유한다. 입력은 2024년의 가공 종목 두 개와 다섯 세션이다. 실제 원장·브로커·비밀을 읽지 않는다. 이미 평가된 `PortfolioFactorValue`를 입력하므로 원천 관측의 팩터 평가나 미래 라벨은 이 검산의 범위 밖이다. 선정과 목표는 실제 `compile_target_tape`, 주문·체결·결과 변환은 실제 `BacktestEngineExecutorAdapter`를 거친다. Python/Rust 두 코어를 같은 독립 기대값으로 검사한다.

## 기존 근거와 보완한 공백

| 기존 테스트 | 이미 검증하는 동작 | 이번 보완 |
|---|---|---|
| `backend/tests/test_engine_golden.py` | ScriptedStrategy의 다음 시가 체결, 현금·수수료·평가, 현금 부족 부분체결 | 실제 선정 compiler와 실행 adapter를 연결 |
| `backend/tests/test_corporate_actions.py`, `test_corporate_action_engine.py` | 분할 수량·원가 조정, 사건 통지, 사건 입력 계약 | 실제 TargetTape의 보유 종목 교체 전 분할 장부 검산 |
| `backend/tests/application/test_benchmark_corporate_actions.py` | 분할·병합 조정 벤치마크 | 전략 원장과 같은 fixture에서 비용 후 수익·초과수익 검산 |
| `backend/tests/application/test_benchmark_suspension.py` | 정지 중 carry, 재개 정산, 상장 종료와 정지 경고 구분 | 매수 후 상장 구간 종료 시 현금 회수 없는 보유 잔액을 명시 |
| `backend/tests/test_adapters_equity_duckdb.py` | 임시 합성 원장 → 원주가 bar, 정지 기준가 제외, 확정 사건, 마지막 bar 뒤 사건 제외·경고 | 기존 경계 검증을 재실행하고 회계 지원 한계에 연결 |

기존 검증과 같은 쿼리·변환을 다시 구현하지 않았다. production 코드, database/src, 공용 로드맵과 HTML은 이 패킷에서 변경하지 않는다.

## 독립 기대값

초기 현금 1,000, 수수료 100bp, 매도세 **custom 200bp**, 슬리피지 0이다. 세율은 계산을 쉽게 만든 합성 입력이며 법정 KRX 세율을 뜻하지 않는다. 거래량 10,000과 `session_volume` 참여율 10%로 유동성 캡은 충분하다. 해당 설정의 기존 경고는 유지한다. 이 fixture는 미래에 알려지는 당일 거래량을 사용한 현실 체결 가능성을 인증하지 않는다.

매 두 세션 리밸런싱이므로 1월 8일과 10일에 판단한다. 8일에는 A 신호가 2, B가 1이고 10일에는 반대다. 상위 한 종목, 목표 100%로 실제 compiler가 A → B를 선정한다.

| 세션 | 사건과 손계산 | 현금 | 보유·평가액 | 자산 |
|---|---|---:|---|---:|
| 01-05 | 리밸런싱 전 | 1,000 | 없음 | 1,000 |
| 01-08 | 종가 A=100에서 A 10주 목표 주문 | 1,000 | 없음 | 1,000 |
| 01-09 | 시가 110, 9주만 가능: 1,000−990−9.9 | 0.1 | A 9주 × 110 = 990 | 990.1 |
| 01-10 | A 2:1 분할: 수량 18, 평균단가 55. B 목표 9주 주문, A 청산 주문 | 0.1 | A 18주 × 55 = 990 | 990.1 |
| 01-11 | A 매도 990−fee9.9−tax19.8, B 9주 매수 900+fee9 | 51.4 | B 9주 × 종가110 = 990 | 1,041.4 |

A 최초 매수의 1주 DAY 잔량은 다음 세션에 체결되지 않는다. 목표 주문 생성 순서는 B 매수 → A 매도이며, 실제 체결은 A 매도 → B 매수라 매도 대금으로 진입한다. 체결 원장에는 수수료 9.9, 9.9, 9와 매도세 19.8이 남는다. 비용 원장의 매도세 행은 장부에 세금을 추가로 두 번 차감하는 의미가 아니다. 세션별 원장과 최종 수익률이 이를 함께 검증한다.

전략 수익률은 4.14%이다. A 벤치마크는 1,000 → 1,000 → 1,100 → 1,100 → 1,100으로 분할에 따른 가짜 손실이 없다. 벤치마크 10%, 초과수익 −5.86%다. 모두 합성 장부 정답이며 실전 성과가 아니다.

## 사용 가능한 결과와 남은 한계

- **분할·병합:** 기존 확인된 수량 조정 계약은 검증된다. 모든 종류의 기업행동·단주 현금 정산을 지원했다는 뜻은 아니다.
- **상장 종료·회수금:** 별도 테스트에서 A의 membership을 01-09에 끝내고 이후 bar를 없앤다. B가 새 목표여도 A를 매도할 bar가 없어 A 9주와 마지막 평가가 110, 현금 0.1이 유지된다. 최종 자산 990.1은 현금화 가능한 회수금이 아니다. 벤치마크에는 `benchmark.delisted_sessions_frozen` 경고가 남는다. 이 테스트는 현재 한계를 노출하며 상폐 회계의 정확성을 인증하지 않는다. 회수금·청산일을 만들어 넣지 않는다.
- **정지 중 사건:** 다음 bar가 있으면 그 bar에서 정산한다. 창 안 마지막 bar 뒤 사건은 DuckDB 어댑터가 제외하고 `equity.corporate_action_without_bar_dropped` 경고를 낸다는 기존 테스트를 재검증한다. 제외는 해당 보유의 경제적 가치가 정확하다는 증거가 아니다.
- **배당:** `database/src/equity/sql/dividend_event.sql`의 계약은 기준일·락일이 없어서 `corp_event`의 `cash_dividend`와 총수익을 만들지 않는다고 명시한다. 보고서의 주당배당금을 보유자 현금에 지급할 날짜로 바꾸지 않는다. 현재 전략과 벤치마크를 배당 포함 총수익으로 부르지 않는다.
- **연구 진행 조건:** 분할을 반영한 비용 후 가격 기준 연구로 한정하고, 상폐·정지 평가 동결과 마지막 bar 뒤 사건 제외 경고를 결과 판단에 반드시 남긴다. 이들 결과만으로 실계좌 투입이나 배당 포함 알파를 주장할 수 없다. 총수익 또는 상폐 영향의 크기가 결론에 필요한 연구는 배당일·회수금 원천과 정산 계약을 마련하기 전 보류한다. 상폐 종목을 사후 삭제한 결과로 한계를 감추지 않는다.

## 검증 기록

2026-10-16, 부모가 준비한 backend uv 환경과 Rust 확장 사용. 새 테스트는 Python 2건 + Rust 2건 **4 passed, 0 skipped**다. 초기 확장 설치 전 skip은 완료 근거로 사용하지 않았다. 새 파일의 ruff check와 pyright는 오류 0이다.

관련 회귀 명령:

```sh
uv run --no-sync --project backend pytest \
  backend/tests/integration/test_s02_accounting.py \
  backend/tests/test_engine_golden.py \
  backend/tests/test_corporate_actions.py \
  backend/tests/test_corporate_action_engine.py \
  backend/tests/application/test_benchmark_corporate_actions.py \
  backend/tests/application/test_benchmark_suspension.py \
  backend/tests/test_adapters_equity_duckdb.py -q
```

관련 회귀는 **172 passed, 0 skipped**, 의존 라이브러리의 DeprecationWarning 1건이다. 실데이터 성과 측정, 실제 브로커 주문, 2020년 이전 실데이터 열람은 실행하지 않았다.
