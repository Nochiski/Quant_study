# 키움 ka10060 거래량 필드 이동 — 아침 확정판 · 장 마감 체인 중단(10-23 ~ 10-24)

> 브랜치 `fix/kiwoom-ka10060-volume`(기준 `fix/excel-fin-basis` 55ca58d7 — 10-22 서버 배포본). 사용자 승인 10-23
> 브리핑('ㅇㅇ진행해' — 수정 → 배포 → 확정판 재빌드).

## 결함

### DEFECT-K60: 키움 ka10060 응답의 거래량 자리 이동을 stage 가 거래대금으로 읽음

- **상황**: 키움이 10-22 15:41(장 마감 수집 — 옛 모양)과 21:05(저녁 수집 — 새 모양) 사이에 ka10060 응답을 바꿨다.
  - 옛 모양: `acc_trde_prica` = 거래량(주). 이름은 '누적거래대금'인데 실측은 거래량이었다 — 명세 오표기, KRX
    ACC_TRDVOL 99.9864% 일치.
  - 새 모양: `acc_trde_prica` = 거래대금(백만원, 이름대로), 새 필드 `acc_trde_qty` = 거래량.
  - 키움 원장은 첫 관측을 지킨다(`kw_daily.merge_tr` INSERT OR IGNORE). 그래서 10-21 이전 행은 `acc_trde_qty` 가
    NULL(열이 10-22 21:05 에 덧붙음)이고 `acc_trde_prica` 가 거래량이다. 10-22 · 10-23 행은 새 모양이다.
  - 실측(스냅샷 snap_20261023T232456Z): 10-19 ~ 10-21 은 옛 식 전 종목 일치, 10-22 · 10-23 은 `acc_trde_qty` 가
    KRX 거래량과 2,648 · 2,649 종목 전부 일치. 삼성전자 10-22 `acc_trde_qty` 11,469,322 · `acc_trde_prica`
    2,492,992(백만원).
- **인풋**:
  1. 10-23 08:10 `daily_build.sh`(D=10-22) → `build_morning` → stage `stg_price_daily` G9.
  2. 10-23 15:41 `postclose_chain.sh close`(T=10-23, D'=10-22).
  3. 10-24 08:10 `daily_build.sh`(D=10-23) → 같은 G9 → 끝의 주말 보강판.
- **에러 위치**:
  - `src/stage/rules_kiwoom.py:58` — `ColumnRule("acc_trde_prica", "volume_shr", …)`. 10-22 부터 거래대금을
    거래량으로 싣는다.
  - `src/stage/rules_krx.py:88` — G9 `volume_match_sql` 이 `o.acc_trde_prica` 를 KRX 거래량과 대조했다.
  - `src/stage/baseline.py:74` — G9 기준값 측정도 같은 식이었다.
  - `src/compat/t_rows.py:74` — v3 T 행(거래량 · 거래대금)이 같은 열 규칙으로 원장을 직접 읽는다.
- **위험성**:
  - 중단: G9 거래량 일치율이 0.99953(10-22 판)으로 기준 0.99986518 − 허용폭 5e-5 아래라 `stg_price_daily` 가
    폐기됐다. equity · 아침 확정판 · 모델이 멈췄다(10-23 · 10-24 crit).
    - 10-23 15:41 장 마감 체인은 D'=10-22 확정판이 없어 수집 0건으로 끝났다(no_targets, 엑셀 미발송).
    - 21:19 장 마감 재반영도 실패해 v3 의 10-23 T 행이 비었다.
    - 10-24 첫 자동 주말 보강판도 D 확정판이 없어 짓지 않았다.
  - 값 오염(고치지 않았다면 — silent corrupt): G9 가 막지 않는 경로는 거래대금을 거래량으로 썼을 것이다.
    - 장 마감 원장 → `stg_flow_postclose_kiwoom.volume_shr` → fi T 가격 행 거래량(유동성 조건), v3 T 행
      `volume` · `amount`.
    - 10-23 15:41 은 수집 0건이라 실제로 들어간 값은 없다. 10-22 15:41 은 옛 모양이라 정상(삼성전자
      10,981,416주).
  - 감시 소음: 원장 머지의 원천 정정 수 `changed` 가 매일 약 25만 행으로 뛰었다(옛 행의 `acc_trde_prica`
    거래량 → 거래대금). 원장 값은 첫 관측대로 남았다.

## 변경

| 파일 | 무엇 |
|---|---|
| `src/stage/model.py` | `ColumnRule.fallback_src` — src 가 NULL 인 행(그 열이 생기기 전에 처음 관측된 행)은 이 열 원문을 쓴다. `src_sql(alias, prefix)`. `''` 는 NULL 이 아니라 결측(`ledger_blank`)으로 남긴다 — 새 필드가 빈 행이 거래대금으로 떨어지지 않는다. `RULES_VERSION` 2.9.0 |
| `src/stage/build.py` | 원문 식 · miss_kind 가 `fallback_src` 를 따른다. 새 자리 열이 어느 원장에도 없으면(옛 판) NULL 로 세운다 |
| `src/stage/gates.py` | G0 실물 계약 = `fallback_src`(옛 자리) — 새 자리는 옛 판에 없을 수 있다 |
| `src/stage/rules_kiwoom.py` | `volume_shr` = `acc_trde_qty`, fallback `acc_trde_prica`(저녁 원장 표 · 장 마감 원장 표 공유 규칙) |
| `src/stage/rules_krx.py` · `baseline.py` | G9 · 기준값 측정 = 같은 열 규칙 식(`CROSS_VOLUME_SQL`) |
| `src/compat/t_rows.py` | v3 T 행 원문 식 = 같은 열 규칙(`src_sql`) |
| `src/daily/postclose.py` | 장 마감 원장 스키마에 `acc_trde_qty` 를 응답 전부터(종전엔 새 필드가 오면 덧붙이기만) |
| 테스트 | 새 모양 · 옛 행 · 빈 새 필드(stage), G9 픽스처 21:10 행을 새 모양으로(옛 식이면 거래량 일치 2/4), v3 T 행 두 원장, 장 마감 원장 스키마 · stage 응답 모양, 판본 2.9.0 |

## 검증

- 재현: 새 stage 테스트가 고치기 전 10-22 행 거래량 2,492,992(거래대금)로 실패, 고친 뒤 11,469,322.
- 관련 테스트(stage · compat · v3_post · postclose · fi · 두 판 대조 · 조용한 손실) 1,253 통과.
- 서버 스냅샷(snap_20261023T232456Z, 읽기 전용) G9 식 대조: 옛 식 0.999203 → 새 식 0.999866(기준 0.99986518,
  통과). 10-22 · 10-23 전 종목 일치.

## 후속(대기 목록)

- 원장 머지 `changed` 비교에서 `acc_trde_prica` 의 뜻이 바뀐 행(옛 행 `acc_trde_qty` NULL)을 빼기 — 지금은 매일
  약 25만 건이라 ka10060 원천 정정 감시가 사실상 눈이 멀었다. 값은 틀리지 않는다.
- 종목 거래대금(새 `acc_trde_prica`, 백만원)을 stage 열로 싣는 일은 19:00 엑셀 신선도 플랜(Market 시트)에서 정한다.
- 10-22 아침 확정판(D=10-22)은 다시 짓지 않는다. v3 10-22 행은 D=10-23 아침 KRX 재반영 창이 덮고, 남는 것은 그날 두 판
  대조 기록뿐이다.
