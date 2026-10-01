# M3-a 매일 모델 자동화 플랜 — 아침 확정 빌드 뒤 factor_inputs → 모델 → 엑셀 (2026-10-01, 승인 대기)

> 모 플랜: [`2026-09-24-v3-merge.md`](2026-09-24-v3-merge.md) §M3(T3.1~T3.3). 그중 **아침 모델 자동화만** 떼어 먼저 한다.
> compat(v3 호환 DB)·저녁 판·v3 컷오버는 M3 나머지·M4 몫이라 이 플랜 밖이다. 계기: 10-01 아침 수동 3단계를 놓쳐
> 09-30 모델 엑셀이 15:05 에야 나갔다.

## 0. 한눈에

| 태스크 | 고치는 것 | 파일 |
|---|---|---|
| **T-M1** 모델 일괄 스크립트 | 수동 3단계(factor_inputs → model → 엑셀 발송)를 한 스크립트로 + 중복 발송 방지 + 주간 엑셀 | `scripts/model_daily.sh`(신규) · `tests/test_model_daily_sh.py`(신규) |
| **T-M2** 아침 체인 연결 | 확정판 완료 신호 뒤 T-M1 을 부른다. 실패는 warn(rc 1) — equity 판은 유효하므로 crit 아님 | `scripts/build_chain.sh` |
| **T-M3** 워치독 | 10:00 `morning_build` 점검에 모델 판·발송 표식 판정을 더한다 | `scripts/watchdog.sh` |
| **T-M4** 문서 | 운영 절차·끄는 법·재실행 | `docs/MODEL_DELIVER.md` · `README`(크론표 — 새 크론 없음) |

## 1. 사실 (10-01 확인)

- 지금은 아침 체인(`daily_build.sh` 08:10 KST **매일** → `build_morning.sh` → `build_chain.sh morning`)이 끝난 뒤(09:41 안팎)
  사람이 세 줄을 친다: `factor_inputs build --date D --basis morning` → `model build --date D --basis morning` →
  `deliver model-daily --date D --basis morning --send`(텔레그램 AIPLAYGROUND, 모델 엑셀 — 로그 알림 아님).
- `daily_build.sh` 는 D(직전 거래일)가 이미 지어졌으면 건너뛴다 → 금요일 D 는 **토요일 아침**에 짓고 일·월 아침은 건너뛴다.
- `deliver model-daily --send` 에는 **중복 발송 방지가 없다**(같은 D 를 다시 돌리면 다시 보낸다).
- `factor_inputs` 는 equity 에 저녁 판(`e_`)이 섞이면 거절한다 → 아침 체인 안(빌드 락을 쥔 채, 저녁 체인 21:20 전)에서 돌면 안전하다.
- 실패 알림: 10-01 사용자 지시로 quant-ledger 로그 알림은 텔레그램에 안 간다(`logs/notify.log` 만). **사용자가 보는 성공 신호는
  모델 엑셀 도착 자체**이고, 실패 확인은 Claude 가 `logs/notify.log`·`logs/model/` 를 읽어 한다.
- 결정 D-8: 모델은 아침 확정판만 쓴다 — 저녁 모델 단계는 두지 않는다.

## 2. 설계

### T-M1 `scripts/model_daily.sh <D> [--no-send] [--force]`
1. `QL_MODEL_DAILY=0` 이면 바로 끝(rc 0, "꺼짐" 한 줄) — 운영 중 끄는 스위치.
2. `factor_inputs build --date D --basis morning` → 실패면 멈춤(rc 2).
3. `model build --date D --basis morning` → 게이트(MG0~MG4) 실패면 멈춤(rc 2). **틀린 점수는 보내지 않는다.**
4. `deliver model-daily --date D --basis morning --send` — 발송 표식 `data/deliver/model_sent/<D>_daily.json`(시각·판 id)이
   있으면 건너뛴다(`--force` 로만 다시). 성공하면 표식을 쓴다. `--no-send` 는 `--dry-run` 으로 엑셀만 만든다.
5. **주간**: D 가 그 주(ISO)의 마지막 거래일이면(달력의 다음 거래일이 다른 주) `deliver model-weekly --week YYYY-Www --send`
   + 표식 `<YYYY-Www>_weekly.json`. 금요일이 휴장이면 목요일 D 에서 나간다.
6. 로그 `logs/model/<D>_morning.log`, 단계별 `시작/종료 rc`(다른 체인과 같은 형식).

### T-M2 `build_chain.sh`
- `basis=morning` 이고 `완료 신호` 까지 성공했을 때만 `step "모델" bash scripts/model_daily.sh "$D"`.
- 실패는 `FAILED` → 체인 rc 1(warn) — stage·equity 판정(crit)은 그대로. 빌드 락 안에서 돈다(동시 재빌드 없음).
- 소요는 SUMMARY 에 `모델 N초` 로 남긴다(G-M4 의 model ≤ 3분 판정 근거).

### T-M3 `watchdog.sh morning_build`(10:00 KST)
- 기존 판정에 더해 `data/model/_runs/<D>_morning.json` 의 status ok · 발송 표식 존재를 본다. 없으면 warn(로그 기록 — 텔레그램 미발송).

## 3. 검증 게이트

| 게이트 | 기준 | 시점 |
|---|---|---|
| **GM1** | 셸 테스트(대역 python): ① 전부 ok → 3단계 + 표식 ② model 실패 → 발송 안 함·rc 2 ③ 표식 있으면 재발송 안 함 ④ 주 마지막 거래일 → 주간 발송 ⑤ `QL_MODEL_DAILY=0` → 아무것도 안 함. + 전체 테스트·ruff·`bash -n` | 커밋 전 |
| **GM2** | 서버 `scripts/model_daily.sh 20261001 --no-send`: 판·엑셀 생성, 텔레그램 0건 | 배포 직후 |
| **GM3** | 첫 자동 실행: 10-03(토) 08:10 체인(D=10-02) → 모델 판 ok · 일간 엑셀 + **주간 엑셀(W40)** AIPLAYGROUND 도착 · 체인 rc 0 · 모델 ≤ 3분 | 10-03 아침 |
| **GM4** | 5거래일 수동 개입 0 · 중복 발송 0 | 10-03 ~ |

## 4. 일정

1. 오늘(10-01): 구현·GM1·커밋. **배포는 하지 않는다** — 내일 아침 WISE 분기 첫 모델(GQ5)을 사람이 먼저 확인해야 하므로,
   그 판이 자동으로 나가지 않게 한다.
2. 10-02(금) 아침: 수동 3단계로 GQ5 확인 → 통과하면 M3-a 배포 → GM2.
3. 10-03(토) 08:10: 첫 자동 실행(GM3, 일간 + 주간). 이후 GM4.

## 5. 범위 밖

- 저녁 모델(D-8 보류) · compat 단계·v3 `score_history` 채우기(M3 나머지 T3.1 compat·M4) · 텔레그램 로그 알림(사용자 지시로 끔).
