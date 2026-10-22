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
| 주문 가격 | 매수 매도 2호가, 매도 매수 2호가 지정가. 시장가로 호가를 긁지 않는다 |
| 주문 시각 | 실행일 오전에 모두 낸다. 09:05에 내고 11:30까지 10분마다 잔량을 그때 2호가로 다시 낸다. 미루지 않는다(10-21 사용자 결정, 처음 정한 14:00 재주문·다음 주 이월을 대체) |
| 자동 전송 | 첫 4주는 주문안을 사람이 승인한 뒤 전송 |
| 실행 위치 | 서버(배포는 S07-⑤) |
| 평가 | 12주 운영 기준(정지·래치·체결률·비용). 성과는 판정하지 않고 기록만 넘긴다 |

플랜 원문과 근거는 작성자 보고 아티팩트에 있다. 12주로 성과를 판정하지 않는 이유: 주간 추적오차를 3%로
잡으면 12주 누적 초과수익의 표준오차가 약 10%라, 코스피200과 약 20% 넘게 차이 나야 우연과 구별된다.

## PR 나눔

플랜의 다섯 묶음(①~⑤)을 세 PR로 나눈다. ①·②는 같은 유스케이스 노드와 포트를 공유한다.

| PR | 범위 | 상태 |
|---|---|---|
| A | ① 신호 읽기 · ② 목표 계산 · 주문안 남기기·승인 | #603 |
| B | ③ 주간 실행기(매도 먼저, 2호가 지정가, 한 종목 한 주문, 오전 추적) · ④ 기록·복기(장 마감 대사, 체결 차이·미체결 기회비용, 키움 원본 보관, CSV 내보내기) | 이 문서 B절 |
| C | ⑤ 서버 배포와 예약 실행, 장중 웹소켓 체결 기록, 코스피200 비교 | 다음 |

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

## B: 주간 실행기와 기록·복기

### 실행 (`application/weekly_rebalance`의 `WeeklyExecutionService`)

- 주문안이 있어야 하고, 첫 4주는 승인도 있어야 한다(`--without-approval`은 그 뒤에 쓴다). 신호일
  다음 날부터 5일 안에만 낸다. 실행 원장이 없으면 첫 `--send` 때 지금 계좌를 기준 잔고로 잡는다.
- 대사가 맞지 않으면 아무것도 내지 않는다(`halted`).
- 주문은 실행일 오전에 모두 낸다. 미루지 않고 시장가로 호가를 긁지도 않는다(10-21 사용자 결정).
- 주문안 종목만 본다. 목표에서 지금 보유와 살아 있는 주문을 뺀 만큼 낸다.
- 여는 단계(`--phase open`, 09:05): 매도를 먼저 내고 체결을 잠깐(최대 2분) 기다린 뒤, 그때
  현금(예약분 제외)으로 살 수 있는 만큼 순위 순서로 산다. 현금이 모자라 덜 산 수량은 추적 단계가 채운다.
- 추적 단계(`--phase chase`, 09:15~11:30 10분마다): 살아 있는 잔량을 취소하고, 취소가 확정되면 남은
  목표를 그때 2호가로 다시 낸다. 11:30에 낸 주문은 장 마감까지 걸어 둔다. 결과를 모르는 주문은 S05가
  취소하지 않으므로 그대로 두고, 그 종목·방향은 다시 내지 않는다.
- 지정가는 매수 매도 2호가, 매도 매수 2호가다. 호가 사이가 넓어도 미루지 않는다. 호가가 아예 없는
  종목(거래정지 등)만 그 회차를 건너뛰고(`no_quote`) 다음 회차에 다시 본다. 추적 가격 상한은 없다.
- 비용 상한은 매수 0.4%, 매도 0.7%(모의 매도 비용 0.55%)다. 주문안 하나에 주문(다시 낸 주문 포함)은
  200건까지다.
- 의도 ID는 `<주문안>-<종목>-<방향>`, 다시 낸 주문은 `-c<회차>`다. 살아 있는 주문을 목표에서 빼고
  내므로 다시 돌려도 같은 수량을 두 번 내지 않는다. 보내기 전 조회가 체결로 계속 바뀌어 S05가 결과
  불명을 주면, 원장에 의도가 없을 때만 15초 쉬고 같은 의도로 다시 시도한다(`retry`).
- `--send`가 없으면 무엇을 보낼지만 보인다(매도 대금은 지정가 대금에서 비용 상한을 뺀 값으로 어림).

### 기록·복기 (`adapters/outbound/rebalance_files`)

| 파일 | 내용 |
|---|---|
| `plans/weekly-<신호일>.json` | 신호(순위 전체·점수 지문·가격 판)와 계획. 바뀌지 않는다 |
| `records/events.jsonl` | 실행 사건: 보낸 시각, 그때 호가 3단계, 지정가, 신호 기준가, 결과 |
| `records/day-<날짜>.json` | 장 마감 기록: 현금·보유(장 마감가)·총자산, 종목·방향별 계획·체결 수량·금액·비용, 신호 기준가 대비 체결 차이(bp), 미체결 기회비용 |
| `records/raw-<날짜>.json` | 그날 키움 조회 원본(`kt00007`·`ka10170`·`kt00018`·`kt00001`). 감사·복기용 |
| `exports/*.csv` | `export` 명령: `days`·`orders`·`holdings`·`events` 네 표(UTF-8 BOM, 엑셀에서 바로 열림) |

체결 차이는 평균 체결가가 신호 기준가보다 불리한 정도(bp, 불리하면 양수), 미체결 기회비용은 계획보다
덜 산(판) 수량 × 기준가에서 장 마감가까지의 움직임(놓친 이익이 양수)이다. 모든 파일은 600이다.

### 명령 (backend/에서, 작업 폴더는 저장소 밖)

```bash
W=~/quant-data/kiwoom-mock/strategy/scope-top10
uv run python -m strategy_workbench.bootstrap.weekly_rebalance execute --workdir $W \
  --plan weekly-2026-10-23 --phase open            # 미리보기
uv run python -m strategy_workbench.bootstrap.weekly_rebalance execute --workdir $W \
  --plan weekly-2026-10-23 --phase open --send     # 월 09:05
uv run python -m strategy_workbench.bootstrap.weekly_rebalance execute --workdir $W \
  --plan weekly-2026-10-23 --phase chase --send    # 월 09:15~11:30, 10분마다
uv run python -m strategy_workbench.bootstrap.weekly_rebalance close --workdir $W    # 매일 15:40
uv run python -m strategy_workbench.bootstrap.weekly_rebalance report --workdir $W
uv run python -m strategy_workbench.bootstrap.weekly_rebalance export --workdir $W
```

### 실측으로 고친 것 (2026-10-21)

- 같은 TR을 1초 안에 두 번 부르면 키움이 1700으로 거절한다. 한 프로세스의 주문·호가 어댑터가 따로
  토큰을 받다가, 그리고 명령을 연달아 돌리다가 걸렸다. 호가 어댑터가 주문 어댑터의 접속(토큰·호출 간격)을
  함께 쓰고, 명령은 시작 때 1.5초 쉬며, 조회 TR의 1700은 간격을 지켜 한 번만 다시 읽는다(주문 TR은
  다시 보내지 않는다).

### 확인

- 순수 계산 5, 실행기 10(현금으로 줄인 매수 수량 손계산, 다시 돌려도 한 번만 보냄, 재시도, 넓은 호가도
  그때 2호가로 냄·호가 없음만 건너뜀, 승인·기한·정지 거절, 주문 수 상한, 추적 회차, 결과 불명 주문
  건드리지 않음, 장 마감 품질), 게이트웨이·호가 5(가짜 키움 서버, 1700 재시도),
  기록 파일 1, 명령줄 2(실행·마감·보고·내보내기).
- 2026-10-21 실데이터 확인(주문 없음): 시험 폴더에서 10-20 주문안 → 실행 미리보기(낼 것 없음) → 장 마감
  기록(대사 일치, 키움 원본 4종 보관) → CSV 내보내기. 시험 폴더는 지웠다.

## 아직 남은 것 (PR C)

- 서버 배포와 예약 실행: 금 장 마감 뒤 `prepare`, 월 09:05 `execute --phase open`, 09:15~11:30
  `chase`, 매일 15:40 `close`. 주의 마지막 거래일·휴장은 서버 KRX 달력으로 고른다.
- 장중 웹소켓 체결 기록(체결 건별 시각·가격). 앱 키당 세션이 하나라 서버 한 곳에서만 돌린다.
- 일별 총자산을 코스피200과 함께 보는 보고.
