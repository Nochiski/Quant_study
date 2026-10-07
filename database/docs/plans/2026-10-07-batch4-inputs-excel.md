# 배포 묶음 4(문서상 물결 4 일부) — 점수 입력과 엑셀 바로잡기 (4-0 · 4-1 · 4-2)

> 감사 수정 플랜 [`2026-10-06-audit-fix.md`](2026-10-06-audit-fix.md) '물결 4' 의 'equity 정합'·'scope 엑셀 정확성' 묶음 + 로드맵 단계 0 의 일간 엑셀 자동 발송(Q-3).
> 결정은 [`DECISIONS.md`](../DECISIONS.md) N-25(사용자 12건, 10-07 "ㅇㅇ 권고대로.")·N-26(원칙 위임)에만 있고, 이 문서는 작업과 진행만 적는다.
> 사용자에게 보이는 플랜: https://claude.ai/artifact/WTJxAxpdFkeNymjBpbSYA4 · 진행: https://claude.ai/artifact/TZXaLRHtvofTsSrAsKcoPM

## 진행률

| ID | 무엇 | 계획 | 구현 | 검토 | 배포 | 실전 확인 | 다음 관측 |
|---|---|---|---|---|---|---|---|
| 4-0 | 일간 엑셀 자동 발송(Q0) + 발송 장부·재발송 가드(Q9) + deliver 종료코드(E-13) | 완료 | 대기 | 대기 | 대기 | 대기 | 배포 뒤 첫 08:10 체인 |
| 4-1 | equity 정합: C-07 · C-04 · J-41 · C-11 → G-21 · C-01 (e1.25.0 · stage 2.6.0) | 완료 | 대기 | 대기 | 대기 | 대기 | 배포 뒤 첫 저녁 잠정판 → 아침 확정판 |
| 4-2 | 엑셀 표시: E-09 · E-02 · 제외 사유 폭 · E-08 메타 · E-06 · E-10 · G-27 표시 · y-y 회색 · 금융 매출 · 지표 정의 문구 | 완료 | 대기(4-0 뒤) | 대기 | 대기 | 대기 | 배포 뒤 첫 엑셀 |
| G-28 | 짧은 첫 사업연도 — 퀄리티 손익 지표에서만 제외 | 완료 | 대기(4-1 배포 뒤) | 대기 | 대기 | 대기 | — |
| 구조 검토 | 단계 끝 전체 검토 1회 | — | — | 대기 | — | — | — |

## 사실 근거(10-07 조사, 서버 읽기 전용)
- 일간 엑셀: 서버 마지막 모델 판은 10-02 분, 마지막 발송 10-05. fi·model·deliver 가 크론·체인에 없다(수동). CLI 는 있다 — `python -m factor_inputs build --date D --basis morning` → `python -m model build --date D --basis morning` → `python -m deliver model-daily --date D --basis morning --send`.
- 4-1: scope 실시간 영향은 126340 의 09-23 r9m 끝점 1건(약 2.8%). 영향은 연구 이력 — C-07 수정주가 가짜 급락·급반등 353건(2016 이후 적용일 수익률 103건 중 |r|>30% 51건), C-11 미래 정보 재무 71행, J-41 stage 11표 약 1.7천행·equity 136행, G-21 정정 그룹 170. equity 패스는 매번 전량을 다시 지으므로 e1.25.0 재빌드 = 배포 뒤 첫 정규 패스(아침 542~561초). C-04 를 '기준일이 캘린더 밖이면 NULL'로 단순 수정하면 권리락 당일 빌드가 깨진다(코드 경로 추론).
- 4-2: 점수가 바뀌는 것은 G-28(scope 4종목, 정규화로 약 120종목 소폭 이동, 상위 30 불변 — 로컬 재실행 '가짜' 수준). 나머지는 표시.

## 갈래 4-0 — 일간 엑셀 자동 발송 (Q0 · Q9 · E-13)
- Files: `scripts/daily_build.sh`(확정판 뒤 단계), 새 스크립트가 필요하면 `scripts/model_daily.sh`, `src/deliver/__main__.py`·`telegram.py`(장부·캡션·가드·rc), 테스트.
- 규칙
  - 08:10 체인의 확정 빌드(`build_morning.sh`)가 성공(rc 0)한 뒤에만 이어서 fi → 모델 → 엑셀 → 발송(P9 — 앞 작업 끝을 감지해 잇는다). 확정 빌드가 실패하거나 건너뛰면(이미 완료 가드·휴장) 아무것도 하지 않는다.
  - 한 단계라도 실패하면 발송 0, 그 단계 이름과 rc 를 notify.log 에 crit(기록만 — 운영 로그 텔레그램 금지, 모델 엑셀 배달은 별개). 확정판 rc 와 섞지 않는다(엑셀 실패가 확정판을 실패로 만들지 않는다 — soft step).
  - 발송 장부(jsonl: D·basis·모델 판 id·sha256·발송 시각·정정 번호)를 두고, 같은 D·basis 를 이미 보냈으면 자동 발송은 건너뛴다(로드맵 K0-1 '거래일마다 정확히 1건'). 같은 날 다시 보낼 때는 `--resend` 처럼 명시해야 하고, 캡션에 '정정 n'·판 id·생성 시각(Q9).
  - deliver CLI 종료코드(E-13): 0 성공 · 1 발송 실패 · 2 입력·인자 오류 · **3 생성 실패(그 밖 예외)**.
- G1: 확정 빌드 rc 0 → 세 단계가 순서대로 불리고 발송 1회 · 확정 빌드 실패 → 0회 · fi 실패 → 모델·발송 0회 + crit · 같은 D 두 번째 실행 → 발송 0회(장부) · `--resend` → 캡션 '정정 1' · deliver 가 ValueError → rc 3.
- 운영 전 확인: 서버에서 지난 D(예 10-06)로 임시 루트(`--root`·`--out-root` 를 `mktemp -d`)에 fi·모델·엑셀을 만들어 소요 시간을 잰다(발송 없음). 10:30 워치독 전에 끝나는지(확정판 종료 09:2x~09:51 + 이 단계).

## 갈래 4-1 — equity 정합 (판본 상향은 끝에 한 번: equity e1.25.0 · stage 2.6.0)
순서(같은 파일·사슬끼리 직렬): C-07 → C-04 → J-41 → C-11 → G-21 → C-01.
- **C-07**(N-26 4.1) `equity/sql/corp_event.sql:205-206`·`adj_factor.sql:425-426`·`price_adj_daily.sql:53-54`·`views.py:125` — apply_basis='krx_base_price'(KRX 기준가로 확정)인 계수는 available = apply_date. EG3 재계산(`rules_s06.py:260-261`, fail 검사 :318)과 기존 테스트(`test_equity_s06_adj.py:242`·`:768`)도 함께. G1: 정정 announce > apply, 기준가가 apply 에서 확인 → available == apply_date · 권리락일 수정수익률 |r| < tol. 게이트: `factor_ok AND apply_basis='krx_base_price' AND available>apply` 325 → 0, 002070 07-31 수정수익률 ±수% 이내, 다른 행 불변.
- **C-04**(N-25 Q3) `corp_event.sql:209-211`·`:255-261`, `adj_factor.sql:176-180` — 기준일이 그 판 캘린더 밖이면 D 의 KRX 기준가가 비율을 확인할 때만 effective=D, 아니면 대기(scope_out). G1: 캘린더 끝 D·기준일 D+10 → 효력일 NULL/대기(지금 FAIL) · **회귀 가드**: 기준일 = D 다음 거래일 + D 기준가 점프 → effective == D(지금 PASS, 계속 PASS) · 비율 1.03 → D 에 factor_ok 행 없음(지금 FAIL). 게이트: 현판 6건(052400·303360·023150·290650·456160·009140) → 0.
- **J-41**(N-26 4.2) `stage/build.py:409-417`(lookup available 에 `greatest(원문, 접수번호 날짜)` — 사용처 `rules_dart.py:92-93`·`:142-143`, `rules_dart_events.py:40`, `rules_doc.py:21`), `fin_std.sql:158-164`·`:275`(avail_dt 를 `stg_disclosure.available_date` 기준으로). G1: stage(rcept_dt 2024-03-19 · rcept_no 20250828000446 → available 2025-08-28) · fin_std 같은 조건. 게이트: stage 11표·equity 해당 행 → 0, 다른 행 불변.
- **C-11**(N-25 Q1) `equity/sql/disclosure_version.sql:219-225`(`items='[]'` → NULL), 재작성·재감사·재발행·소급·재무제표수정 사유(reason_raw)면 재무 정정으로(`rules_s11.py:70-71` 키워드 정본). G1(`test_equity_s12_fin.py:646` parametrize): items `[]` → 정정일 · reason '연결재무제표 재작성' + 비재무 항목 → 정정일. 게이트: 00287812 `available_date<rcept_dt` 2 → 0, 71행 → 0, 그 밖 승계 3,686행 불변.
- **G-21**(N-25 Q2) `fin_std.sql:124-133`(grp 가 min rcept_no — 최신 접수로)·`:153-156` — 그룹의 최신 판본 값 + 그 정정일. G1: 같은 그룹 원본(d1, 100)·정정(d2, 90) → 정정·90 · ord 24→26 total_equity → NOT NULL. 게이트: 166행 중 min rcept → 0, 01472930 op_profit = 6,365,380, 01274310 total_equity NOT NULL.
- **C-01**(N-25 Q4) `scripts/equity_rebuild_all.sh:42-59`·`equity/rollback.py:69-99` — 아침 패스 실패 롤백의 before 를 같은 basis 의 마지막 m_ 판으로. G1(`test_equity_rollback.py:107` 옆): builds [m_1, e_2], 시작 current=e_2 → 롤백 대상 m_1.
- 운영 전 확인: 고친 SQL 을 현판 stage·equity parquet 위에 메모리로 재연(읽기 전용) 또는 `equity --root <tmp>`·`stage --stage-root <tmp>`(체인 시간 밖에서만 — 빌드 락 공유·RSS 7.7GB) — 운영 판과 diff 해 대상 행만 달라졌는지, 2패스 해시 비교(C-13).
- 배포: 아침 종료(~09:50) 뒤 낮 창(10:30~15:10)이면 첫 e1.25.0 은 그날 저녁 잠정판, 다음 아침이 첫 확정판. 되돌리기는 직전 rev 재배포(다음 패스가 다시 짓는다) 또는 표별 `equity rollback`.
- Q11: 배포 뒤 다시 돌려야 할 연구·백테스트 목록(C-07·C-11 이력을 쓰는 것)만 만든다.

## 갈래 4-2 — 엑셀 표시 (4-0 머지 뒤 — 같은 deliver 파일)
- E-09 `excel_daily.py:847-850` 메타 줄을 키 이름 기준으로 · E-02 `excel_daily.py:309`·`:315-317`·`:744-745` 점수 행이 있으면 scored → '결측(원천없음)'·결측 축·메타 수(N-26 4.5) · 제외 사유 열 폭 26(`excel_daily.py:263`, `qpack.py:179-191`) · E-08 메타에 엑셀 생성 시각·rev(`excel_daily.py:746-758`).
- E-06(Q8) `deliver/trend.py:95-108`·`qpack.py:91-95` — 선을 원순위로, 칸 색 가운데 0. G1: 합성 Trend(N 100→110, 순위 50→51) → 선 색 = 선 방향.
- E-10(Q7) `excel_daily.py:217-225`·`:347-399`·업종 `:574`·`:637-646` — scope `scores.parquet` 27열 + 표식 6열을 '점수 원자료' 시트로(열 사전·단위표 — `r1m` 비율, `val_dividend_yield` %, `flow_*` 순매수/시총), 업종 3열(PER 중앙값 · `op_change_1m`>0 비율 · `r1m` 시총가중). G1: scope 실물 build → r1m 열 존재.
- G-27(Q6) 표시만 — 비고 '외화 재무 — 퀄리티 = 변동성만'(241560).
- y-y 회색(Q10) — 추정치로 채운 Y−1 옆 y-y 칸.
- 금융 매출 빈칸 — `factor_inputs/queries.py:565` 연간 매출 계정명에 '영업수익'·'순영업이익'도(분기 `:495-496` 과 같게) · 지표 시트 ROE·ROA·부채비율 정의 문구를 DART 로(`excel_daily.py:418-420`).
- 운영 전 확인: 서버 10-02 판을 로컬에 복사해 옛/새 코드로 엑셀을 만들어 셀 diff(발송 없음).

## G-28 — 4-1 배포 뒤 (Q5)
- `factor_inputs/queries.py:653-668`·`:685` — fi 연간 행에 기간(개월) 열(fin_std `period_start`), `model/engines/v3_zscore.py:306-338` 은 scope(TOML 파라미터)에서만 12개월 미만 행을 퀄리티 손익 지표(gpa·roa·fcf_assets·gpa_change)에서 뺀다 — v3_zscore@1.0(원본 대조용)은 그대로, 이름 scope_v1.0 유지. 비고 '첫 사업연도 N개월'. fi 판본 상향. G1: 짧은 전기 → gpa_change 없음 · fi 기간 열.

## 범위 밖 · 알려진 한계
- C-05(가짜 '미해결' 사건 집계)는 v4 묶음(§8-17 먼저, N-26 4.3). 068270 06-04 무상증자 미조정(krx_base_inconsistent, 그날 수정수익률 −6.07%)·295310 매출총이익 증가율 83.8(전기 음수 분모)은 기록만.
- G-27 정확한 수정(원통화 비율)은 cF1001 수집(Q-9) 또는 equity 계약 변경 뒤.
