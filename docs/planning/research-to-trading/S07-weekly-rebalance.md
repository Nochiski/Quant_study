# S07 운영 관찰: scope 상위 10 주간 리밸런싱

[S07 패킷](../../auto-trading-roadmap.html#session-s07)의 운영 경로(B3)다. 미리 정한 신호와 실행 규칙으로
키움 모의계좌를 매주 운용하며, 신호를 먼저 남기고 연구의 목표와 계좌의 실제 상태 사이 차이를 기록한다.
전략의 실력 판정은 이 문서의 범위가 아니다. 모의 체결은 실전 시장충격·용량의 근거로 쓰지 않는다.

## 승인한 결정 (2026-10-21)

| 항목 | 결정 |
|---|---|
| 신호 | scope_v1.0 장 마감 빌드 순위(모델 DB `model/scope@1.0`) |
| 종목 수·비중 | 상위 10종목 동일비중. 총자산의 2%는 현금 |
| 교체 | 상위 10에 들면 산다. 보유 종목은 30위 밖으로 밀릴 때만 판다 |
| 시점 | 주의 마지막 거래일 장 마감 신호 → 다음 거래일 정규장. 시간외·NXT 없음 |
| 주문 가격 | 매수 매도 2호가, 매도 매수 2호가 지정가. 잔량은 14:00에 한 번 다시 내고 남으면 다음 주로 |
| 자동 전송 | 첫 4주는 주문안을 사람이 승인한 뒤 전송 |
| 실행 위치 | 서버(배포는 S07-⑤) |
| 평가 | 12주 운영 기준(정지·래치·체결률·비용). 성과는 판정하지 않고 기록만 넘긴다 |

플랜 원문과 근거는 작성자 보고 아티팩트에 있다. 12주로 성과를 판정하지 않는 이유: 주간 추적오차를 3%로
잡으면 12주 누적 초과수익의 표준오차가 약 10%라, 코스피200과 약 20% 넘게 차이 나야 우연과 구별된다.

## PR 나눔

플랜의 다섯 묶음(①~⑤)을 세 PR로 나눈다. ①·②는 같은 유스케이스 노드와 포트를 공유한다.

| PR | 범위 | 상태 |
|---|---|---|
| A | ① 신호 읽기 · ② 목표 계산 · 주문안 남기기·승인(이 문서의 아래 절) | 이 PR |
| B | ③ 주간 실행기(매도 먼저, 2호가 지정가, 한 종목 한 주문, 14:00 재주문, 장 마감 대사) · ④ 기록(체결 차이·미체결 기회비용·비용·회전율) | 다음 |
| C | ⑤ 서버 배포와 예약 실행 | 그다음 |

## A: 신호 읽기·목표 계산·주문안

### 신호 (`adapters/outbound/scope_signal`)

모델 DB(서버 quant-ledger `data/model_db`)의 현재 장 마감 빌드만 읽는다.

1. `model/latest_evening.json`의 `status`가 `ok`, `basis`가 `evening`, `date`가 신호일이어야 한다.
   아니면 `not_ready`.
2. 그 빌드의 `specs["scope@1.0"].gates`가 모두 `pass`·`warn`·`skip`이고 `excluded_specs`에 없어야 한다.
   아니면 `gate_failed`.
3. `model/scope@1.0/MANIFEST.json`의 `current_build`가 같은 빌드여야 한다. `scores.parquet`의
   `score_date`가 신호일 하나이고 종목·순위가 겹치지 않아야 한다. 아니면 `unreadable`.
4. 기준가격은 그 빌드가 점수를 계산한 가격 판(`fi_build_id`의 `factor_inputs/fi_prices`)의 신호일
   종가다. 신호일 거래량 0 종목은 매매가 막힌 것으로 본다.

### 목표 계산 (`domain/rebalance`)

- 보유 종목: 순위가 30위 안이면 남기고, 밖이거나 순위가 없으면 전량 판다. 매매가 막혔으면 둔다.
  남긴 종목이 10개를 넘으면 순위가 낮은 것부터 판다.
- 빈 자리: 보유하지 않은 종목을 순위대로 채운다. 막혔거나 가격이 없거나 1주가 목표 금액보다 비싸면
  건너뛰고 다음 순위로 채우되 30위 밖은 사지 않는다.
- 종목당 목표 금액 = (현금 + 기준가 평가액) × 98% ÷ 10. 새 종목은 이 금액, 남긴 종목은 ±25%를 넘게
  벗어날 때만 이 금액으로 맞춘다.
- 주식 수 = 목표 금액 ÷ (기준가 × 1.0035) 내림. 금액은 원 정수, 비율은 bp 정수다.

### 주문안 (`application/weekly_rebalance`, `adapters/outbound/rebalance_files`)

- `prepare`는 신호일마다 주문안 `weekly-<신호일>` 하나를 만든다. 이미 있으면 다시 만들지 않는다.
- 주문안 파일에 순위 전체, 점수 파일 SHA-256, 가격 판 id, 그때의 현금·보유, 줄마다 순위·현재·목표·기준가·
  사유를 남긴다. 임시 파일에 다 쓴 뒤 제자리에 붙이고(덮어쓰기 없음) 600 권한이다.
- 실행 원장이 있으면 먼저 대사하고, 어긋나면 주문안을 만들지 않는다(`not_reconciled`).
- `approve`는 한 번만 기록한다. 주문은 보내지 않는다.

### 사용법 (backend/에서)

```bash
# 맥에서: 서버 현재 빌드 파일만 가져온다(저장소 밖 도구)
~/quant-data/kiwoom-mock/tools/fetch_model_db.sh ~/quant-data/kiwoom-mock/model_db
uv run python -m strategy_workbench.bootstrap.weekly_rebalance prepare \
  --workdir ~/quant-data/kiwoom-mock/strategy/scope-top10 \
  --model-root ~/quant-data/kiwoom-mock/model_db --signal-date 2026-10-23
uv run python -m strategy_workbench.bootstrap.weekly_rebalance approve \
  --workdir ~/quant-data/kiwoom-mock/strategy/scope-top10 --plan weekly-2026-10-23 --approver <이름>
```

키는 설정 화면이 저장한 `secrets.json`에서 읽는다. 작업 폴더가 저장소 안이면 거부한다.

### 확인

- 손계산 사례 12개(`tests/domain/test_rebalance.py`), 서비스 4개, 모델 DB·주문안 저장 11개, 명령줄·
  계좌 읽기 4개(가짜 키움 서버).
- 2026-10-21 실데이터 확인(조회만): 10-20 빌드와 모의계좌로 주문안을 만들었다. 그날 산 10종목이 모두 30위
  안·목표 ±25% 안이라 전부 "유지"였다. 시험 폴더는 지웠다.

## 아직 정하지 않았거나 다음 PR에 있는 것

- 실행 원장 기준 잔고는 첫 정식 리밸런싱일(10-26) 장 시작 전, 주문이 없을 때 잡는다(PR B).
- 신호일의 휴장 처리: 주의 마지막 거래일을 고르는 일은 예약 실행(PR C)이 서버 KRX 달력으로 한다.
  `prepare`는 받은 신호일의 빌드만 읽는다.
- 종목 이름 표시, 신호 기준가 대비 체결 차이·미체결 기회비용 기록은 PR B다.
