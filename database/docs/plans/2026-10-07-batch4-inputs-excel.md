# 배포 묶음 4(문서상 물결 4 일부) — 점수 입력과 엑셀 바로잡기 (4-0 · 4-1 · 4-2)

> 감사 수정 플랜 [`2026-10-06-audit-fix.md`](2026-10-06-audit-fix.md) '물결 4' 의 'equity 정합'·'scope 엑셀 정확성' 묶음 + 로드맵 단계 0 의 일간 엑셀 자동 발송(Q-3).
> 결정은 [`DECISIONS.md`](../DECISIONS.md) N-25(사용자 12건, 10-07 "ㅇㅇ 권고대로.")·N-26(원칙 위임)에만 있고, 이 문서는 작업과 진행만 적는다.
> 사용자에게 보이는 플랜: https://claude.ai/artifact/WTJxAxpdFkeNymjBpbSYA4 · 진행: https://claude.ai/artifact/TZXaLRHtvofTsSrAsKcoPM

## 진행률

| ID | 무엇 | 계획 | 구현 | 검토 | 배포 | 실전 확인 | 다음 관측 |
|---|---|---|---|---|---|---|---|
| 4-0 | 일간 엑셀 자동 발송(Q0) + 발송 장부·재발송 가드(Q9) + deliver 종료코드(E-13) | 완료 | 완료(wip/b4-0 f5486da1) | 명세·품질 완료 | 10-08 10:20 rev 06df4a3b | 손 실행 1회 통과(10-08 10:22, D=10-07 발송) | 10-09 08:10 첫 자동 발송(D=10-08) |
| 4-1 | equity 정합: C-07 · C-04 · J-41(+disclosure_version, N-26 4.10) · C-11 → G-21 · C-01 (e1.25.0 · stage 2.6.0) | 완료 | 완료(wip/b4-1a d8fd2b27 · wip/b4-1b 8bbf971b) | 명세·품질 완료 · 서버 재연 통과(10-07 23:2x) | 10-08 10:20 rev 06df4a3b | 대기 | 10-08 21:20 첫 e1.25.0 잠정판 → 10-09 08:10 확정판 |
| 4-2 | 엑셀 표시: 4-2a(E-09 · E-02 · 제외 사유 폭 · E-08 메타 · E-06 · E-10 · G-27 원인 없는 비고 · y-y 회색 · 지표 정의 문구 · v4 비교 열 빼기 · 순액 매출 y-y · 팩터 칸 z — N-29) / 4-2b(금융 매출 → 아래 G-28 행과 함께) | 완료 | 4-2a 완료(wip/b4-2a 8b92f1ac) | 4-2a 명세·품질·수정분 검토 완료 | 4-2a 10-08 11:33 rev fa9efbe9 | 4-2a 서버 임시 루트 엑셀 생성 통과(발송 없음) | 10-09 08:10 첫 자동 발송 엑셀 |
| G-28 | 짧은 회계기간(퀄리티 손익 지표만 제외) + fi 금융업 연간 매출(4-2b) | 완료 | 완료(wip/b4-2b d495e581, fi1.2.0 · mb1.4.0) | 명세·품질 완료 | 대기(10-08 밤 첫 e1.25.0 판 확인 뒤 10-09 낮) | 대기 | — |
| 구조 검토 | 단계 끝 전체 검토 1회 | — | — | 대기 | — | — | — |

## 사실 근거(10-07 조사, 서버 읽기 전용)
- 일간 엑셀: 서버 마지막 모델 판은 10-02 분, 마지막 발송 10-05. fi·model·deliver 가 크론·체인에 없다(수동). CLI 는 있다 — `python -m factor_inputs build --date D --basis morning` → `python -m model build --date D --basis morning` → `python -m deliver model-daily --date D --basis morning --send`.
- 4-1: scope 실시간 영향은 126340 의 09-23 r9m 끝점 1건(약 2.8%). 영향은 연구 이력 — C-07 수정주가 가짜 급락·급반등 353건(2016 이후 적용일 수익률 103건 중 |r|>30% 51건), C-11 미래 정보 재무 71행, J-41 stage 11표 약 1.7천행·equity 136행, G-21 정정 그룹 170. equity 패스는 매번 전량을 다시 지으므로 e1.25.0 재빌드 = 배포 뒤 첫 정규 패스(아침 542~561초). C-04 를 '기준일이 캘린더 밖이면 NULL'로 단순 수정하면 권리락 당일 빌드가 깨진다(코드 경로 추론).
- 4-2: 점수가 바뀌는 것은 G-28(scope 4종목, 정규화로 약 120종목 소폭 이동, 상위 30 불변 — 로컬 재실행 '가짜' 수준). 나머지는 표시.

## 갈래 4-0 — 일간 엑셀 자동 발송 (Q0 · Q9 · E-13)
- Files: `scripts/daily_build.sh`(확정판 뒤 단계), 새 스크립트가 필요하면 `scripts/model_daily.sh`, `src/deliver/__main__.py`·`telegram.py`(장부·캡션·가드·rc), 테스트.
- 규칙
  - 08:10 체인의 확정 빌드(`build_morning.sh`)가 판을 쓸 수 있게 끝난 뒤(rc 0, 또는 완료 신호·스냅샷 GC 후처리만 실패한 rc 1 — `build_chain.sh` 판정)에만 이어서 fi → 모델 → 엑셀 → 발송(P9 — 앞 작업 끝을 감지해 잇는다). 확정 빌드가 실패(rc ≥ 2)하거나 건너뛰면(이미 완료 가드·휴장) 아무것도 하지 않는다. (10-07 구현 검산: 처음 'rc 0 뒤에만'으로 적었으나 rc 1 도 판은 ok 라 0·1 로 고침.)
  - 모델 단계(`scripts/model_daily.sh`)는 빌드 락 안에서 돈다 — 락이 쥐여 있으면 notify info 한 줄을 남기고 한도 없이 기다린다(P9). 체인과 손 실행이 같은 D 를 동시에 보내는 것과, 모델 단계 도중 손 재빌드가 섞인 판을 읽는 것을 함께 막는다(10-07 구현 검토에서 추가).
  - 한 단계라도 실패하면 발송 0, 그 단계 이름과 rc 를 notify.log 에 crit(기록만 — 운영 로그 텔레그램 금지, 모델 엑셀 배달은 별개). 확정판 rc 와 섞지 않는다(엑셀 실패가 확정판을 실패로 만들지 않는다 — soft step).
  - 발송 장부(jsonl: D·basis·모델 판 id·sha256·발송 시각·정정 번호)를 두고, 같은 D·basis 를 이미 보냈으면 자동 발송은 건너뛴다(로드맵 K0-1 '거래일마다 정확히 1건'). 같은 날 다시 보낼 때는 `--resend` 처럼 명시해야 하고, 캡션에 '정정 n'·판 id·생성 시각(Q9).
  - deliver CLI 종료코드(E-13): 0 성공 · 1 발송 실패 · 2 입력·인자 오류 · **3 생성 실패(그 밖 예외)**.
- G1: 확정 빌드 rc 0 → 세 단계가 순서대로 불리고 발송 1회 · 확정 빌드 실패 → 0회 · fi 실패 → 모델·발송 0회 + crit · 같은 D 두 번째 실행 → 발송 0회(장부) · `--resend` → 캡션 '정정 1' · deliver 가 ValueError → rc 3.
- 운영 전 확인: 서버에서 지난 D(예 10-06)로 임시 루트(`--root`·`--out-root` 를 `mktemp -d`)에 fi·모델·엑셀을 만들어 소요 시간을 잰다(발송 없음). 10:30 워치독 전에 끝나는지(확정판 종료 09:2x~09:51 + 이 단계).

## 갈래 4-1 — equity 정합 (판본 상향은 끝에 한 번: equity e1.25.0 · stage 2.6.0)
순서(같은 파일·사슬끼리 직렬): C-07 → C-04 → J-41 → C-11 → G-21 → C-01.
- **C-07**(N-26 4.1) `equity/sql/corp_event.sql:205-206`·`adj_factor.sql:425-426`·`price_adj_daily.sql:53-54`·`views.py:125` — apply_basis='krx_base_price'(KRX 기준가로 확정)인 정상 계수(사건 교체 ok + 신규 unknown_krx ok — N-26 4.7)는 available = min(announce, apply_date) — 공시가 앞서면 공시일 그대로(10-07 구현 검토: 처음 'apply_date'·'unknown_krx 제외'로 적었으나 그러면 게이트가 325 → 57 에 멈추고 247540 골든이 깨진다). EG3 재계산(`rules_s06.py:260-261`, fail 검사 :318)과 기존 테스트(`test_equity_s06_adj.py:242`·`:768`)도 함께. G1: 정정 announce > apply, 기준가가 apply 에서 확인 → available == apply_date · 권리락일 수정수익률 |r| < tol. 게이트: `factor_ok AND apply_basis='krx_base_price' AND available>apply` 325 → 0, 002070 07-31 수정수익률 ±수% 이내, 다른 행 불변.
- **C-04**(N-25 Q3) `corp_event.sql:209-211`·`:255-261`, `adj_factor.sql:176-180` — 기준일이 그 판 캘린더 밖이면 D 의 KRX 기준가가 비율을 확인할 때만 effective=D, 아니면 대기(scope_out). G1: 캘린더 끝 D·기준일 D+10 → 효력일 NULL/대기(지금 FAIL) · **회귀 가드**: 기준일 = D 다음 거래일 + D 기준가 점프 → effective == D(지금 PASS, 계속 PASS) · 비율 1.03 → D 에 factor_ok 행 없음(지금 FAIL). 게이트: 현판 6건(052400·303360·023150·290650·456160·009140) → 0.
- **J-41**(N-26 4.2) `stage/build.py:409-417`(lookup available 에 `greatest(원문, 접수번호 날짜)` — 사용처 `rules_dart.py:92-93`·`:142-143`, `rules_dart_events.py:40`, `rules_doc.py:21`), `fin_std.sql:158-164`·`:275`(avail_dt 를 `stg_disclosure.available_date` 기준으로). G1: stage(rcept_dt 2024-03-19 · rcept_no 20250828000446 → available 2025-08-28) · fin_std 같은 조건. 게이트: stage 11표·equity 해당 행 → 0, 다른 행 불변.
  - 명세 검토(10-07): 같은 종류의 원천 접수일 사용이 `disclosure_version.sql`(available_date·first_correction_dt)·`holder_daily.sql`(available_date)·`universe_daily.sql`(정지 신호)에 남았다 — 감사 136행 범위 밖이라 명세 위반은 아니고 게이트 장부 RG-C1-07 대상이다. 서버 재연에서 표별 노출(available_date < 접수번호 날짜 행 수)을 재고, 0 이 아니면 이 묶음에 넣을지 정한다.
- **C-11**(N-25 Q1) `equity/sql/disclosure_version.sql:219-225`(`items='[]'` → NULL), 재작성·재감사·재발행·소급·재무제표수정 사유(reason_raw)면 재무 정정으로(`rules_s11.py:70-71` 키워드 정본). G1(`test_equity_s12_fin.py:646` parametrize): items `[]` → 정정일 · reason '연결재무제표 재작성' + 비재무 항목 → 정정일. 게이트: 00287812 `available_date<rcept_dt` 2 → 0, 71행 → 0, 그 밖 승계 3,686행 불변.
- **G-21**(N-25 Q2) `fin_std.sql:124-133`(grp 가 min rcept_no — 최신 접수로)·`:153-156` — 그룹의 최신 판본 값 + 그 정정일. G1: 같은 그룹 원본(d1, 100)·정정(d2, 90) → 정정·90 · ord 24→26 total_equity → NOT NULL. 게이트: 166행 중 min rcept → 0, 01472930 op_profit = 6,365,380, 01274310 total_equity NOT NULL.
- **C-01**(N-25 Q4) `scripts/equity_rebuild_all.sh:42-59`·`equity/rollback.py:69-99` — 아침 패스 실패 롤백의 before 를 같은 basis 의 마지막 m_ 판으로. G1(`test_equity_rollback.py:107` 옆): builds [m_1, e_2], 시작 current=e_2 → 롤백 대상 m_1.
- 운영 전 확인: 고친 SQL 을 현판 stage·equity parquet 위에 메모리로 재연(읽기 전용) 또는 `equity --root <tmp>`·`stage --stage-root <tmp>`(체인 시간 밖에서만 — 빌드 락 공유·RSS 7.7GB) — 운영 판과 diff 해 대상 행만 달라졌는지, 2패스 해시 비교(C-13).
- 배포: 아침 종료(~09:50) 뒤 낮 창(10:30~15:10)이면 첫 e1.25.0 은 그날 저녁 잠정판, 다음 아침이 첫 확정판. 되돌리기는 직전 rev 재배포(다음 패스가 다시 짓는다) 또는 표별 `equity rollback`.
- Q11: 배포 뒤 다시 돌려야 할 연구·백테스트 목록(C-07·C-11 이력을 쓰는 것)만 만든다.

## 4-0·4-1 구현·검토·재연 결과 (10-07)
- 통합 브랜치 `wip/b4-int`(b43df921 + wip/b4-0·b4-1a·b4-1b 병합, 판본 문자열 충돌 1건을 하나의 e1.25.0 항목으로 합침): `database/tests` 1,966 passed · 2 skipped(맥 flock 없음 — 실물 flock 테스트는 서버·CI 몫). `feat/v3-merge` 병합은 묶음 3 배포(10-08 낮) 뒤 — 지금 병합하면 묶음 3 과 함께 배포된다.
- 검토가 고친 것(구현 중 결정은 DECISIONS N-26 4.7~4.10): unknown_krx 정상 행도 C-07 대상(325 → 0 이 되려면 필요) · model_daily 빌드 락 · C-01 '직전 확정판' = latest_morning.json 의 전 표(성공 표만 옮기면 m_/e_ 혼합 재발) · 롤백 뒤 catalog 재생성 · G-21 의 문서 없는 최신 정정이 그룹을 통째로 격리하던 결함 → 같은 정정 사슬 문서로 기간 보충 · EG3 기간 증인 '자기 문서 우선' · 그 증인 질의가 서버 규모에서 제곱으로 커지던 성능 결함(합성 1.0배 OOM → 0.08초·0.3GB) · disclosure_version 공개일 17행(J-41 범위 추가).
- 서버 재연(읽기 전용 — 코드만 `mktemp -d` 임시 폴더, 현판 10-07 저녁 판을 메모리에서 다시 계산):
  - 4-1a(12초·RSS 1.7GB): C-07 `factor_ok ∧ krx_base_price ∧ available > apply` 325(사건 교체 268 + unknown_krx 57) → 0 · 그 행들의 적용일 |수정수익률| > 30% 175 → 5(최대 30.9% — 가격제한폭 부근 실제 등락), 다음 세션 193 → 0 · 002070 07-31 −35.0%/08-03 +159.6% → +29.8%/+30.0% · C-04 D(10-06) 확정 0, 대기 8 leg(현판 D 행 7건 소멸) · adj_factor 바뀐 열은 available_date 325행뿐, C-07 대상 밖 변경 0 · price_adj_daily 243종목 339행.
  - 4-1b(55초·RSS 6.1GB): stage J-41 11표 1,743행 → 0, 그 밖 변경 0 · fin_std J-41 8 → 0 · C-11 정정일로 돌아간 승계 71(사유 21 + 빈 항목 50) · G-21 접수 둘 이상 그룹 183, min rcept 행 179 → 0, 01472930 op_profit 6,365,380 · 01274310 total_equity NOT NULL · 사슬 문서 보충 1,358그룹(G-21 8 · 백필 정정 1,350, 옛 판 inferred 1,112 · 행 없음 238 · period_end 다름 6), period_unresolved 162 → 0 · disclosure_version 공개일 17행(최악 −654일)·그 밖 변경 0 · 고친 EG3 pass(5.75초) · fin_std 바뀐 1,730행 중 설명 안 되는 변경 0.
  - 운영 전 리허설(새 코드로 stage·equity 전량을 서버 임시 루트에 미리 짓기 — 배포 뒤 첫 저녁·아침 확인을 앞당기는 안, 10-08 00:46 제안)은 10-08 사용자 "리허설 일단 보류하자." 로 보류. 배포 뒤 확인은 원래대로 첫 저녁 잠정판 → 아침 확정판.
  - 기대값 정정 1건: '00287812 available_date < rcept_dt 2 → 0' 은 법인 전체를 센 기준이라 어긋났다(현판 8 → 새 판 6). 재작성 정정 20180907000426·435·440 의 3행(FY2015~2017)은 정정일로 갔고, 남은 5행은 비재무 정정(기재 오류·합병 사후정보·직원 단위 오기 — stg_doc_correction 사유·항목 확인)이라 원본 공시일 승계가 맞다.
  - G-21 정책(N-25 Q2) 비용: 값이 같은데 공개일이 늦어진 그룹 37(최대 925일) — 결정대로.
  - 서버 노출 실측(현판, `available_date < 접수번호 날짜`): disclosure_version 17 · 보조 5표 128 · holder_daily 0 · corp_event 0 — 모두 이 묶음이 고친다. J-41 은 DS005 이벤트 표 0행이라 corp_event(4-1a)로 번지지 않는다.

## 배포 기록(10-08) — 4-0·4-1
- 10-08 10:20 KST rev 06df4a3b(`wip/b4-int` 병합, deploy.sh 테스트 1,966 passed · 2 skipped). 결정 N-28(같은 날 배포 — 묶음 3 은 10:11 rev 2b2f5636). 되돌릴 rev 2b2f5636.
- 바뀐 파일 26개(src 23 · scripts 3, model_daily.sh 신규 실행 권한) 로컬↔서버 md5 26/26 일치, 서버 `bash -n` daily_build·model_daily·equity_rebuild_all, import 통과, 판본 stage 2.6.0 · equity e1.25.0 · fi fi1.1.0(불변).
- 배포 전 서버에 `data/deliver/sent_model_daily.jsonl` 없음(손 발송 장부 없음 — 첫 자동 D 와 겹침 없음).
- model_daily 빌드 락 실물 대기: 배포본 락 절(40~66행)을 떼어 서버 임시 폴더에서(notify 호출만 echo 로 바꿈) — 쥐인 동안 대기 시작 → 풀린 뒤 3초에 이어서, 빈 락은 바로, 열 수 없는 경로는 rc 3 + crit 문구.
- **손 실행 1회(N-28 ③)**: `scripts/model_daily.sh --date 20261007` 10:21:53 → 10:22:13(20초: fi 4초 · 모델 8초 · 엑셀·발송 8초), rc 0. fi m_20261008T012153 · 모델 m_20261008T012157(scope@1.0 n=513), 텔레그램 AIPLAYGROUND 발송 ok, 발송 장부 첫 줄(date 2026-10-07 · basis morning · build_id · sha256 · correction 0). 10-05 이후 첫 일간 엑셀. 08:10 체인(확정판 09:2x~09:5x)에 20초 남짓 더해진다.
- 남은 운영 확인: 10-08 21:20 첫 e1.25.0·stage 2.6.0 저녁 잠정판(게이트·소요), 10-09 08:10 확정판 + 첫 자동 발송(D=10-08, 장부 둘째 줄).

## 배포 기록(10-08) — 4-2a
- 10-08 11:33 KST rev fa9efbe9(`wip/b4-2a` 병합, deploy.sh 테스트 1,986 passed · 2 skipped). 바뀐 파일 deliver 8개(stats.py 신규), md5 11/11 일치.
- 서버 확인: 10-07 판으로 `deliver model-daily` 를 임시 out-root 에 실행(발송 없음, 5.9초) — 시트 8개, 점수 시트 5팩터 × '유니버스 z'·'업종 z'(071840 리비전 유니버스 z = 3 = 엔진 값), 메타 코드 rev fa9efbe9·엑셀 생성 시각·'비교에서 뺀 모델' 줄. 발송 장부 1줄 그대로.
- 검토가 지시와 다르게 둔 1건을 받아들임: 순액 매출 y-y 는 '두 해 중 한쪽만 순액'일 때 비운다(둘 다 순액이면 같은 기준이라 남긴다).
- 4-2b 와 함께 정리할 사소: 회색 y-y·업종 z 표본 문턱·G-27 음성 케이스 테스트 공백, 순액 비고가 추정치 없는 종목에도 붙는 표기, min_sector_size ≤ 1 spec 의 stdev 가드, MODEL_EXCEL_SPEC 각주 수.
- 구현(10-08, 갈래 4-2c — 사용자 결정 10-08): scope 유니버스의 의견 0 제외(`min_analysts = 1`, 10-05)를 뺐다 — 의견 0 은 점수 대상, 엑셀 비고 '최근 3개월 의견 없음'(deliver 기존)으로만 표시. 이름 scope@1.0 유지, 엔진·MG1 의 min_analysts 지원은 남김(쓰는 등록 spec 없음), 모델 판본은 4-2b 의 mb1.4.0 에 함께. 재연(가짜 — 10-02 fi 사본): 모집단 510 → 593(+83, 전부 의견 0) · 새로 든 종목 중 상위 30 = 340450 지씨지놈 1위 · 219130 타이거일렉 4위 · 263860 지니언스 21위 · 003350 한국화장품제조 30위(밀려난 것 131290·098460·353200·093520) · 기존 510 중 509 순위 변동(중앙 +46, −3~+106).

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
- 구현(10-08, 갈래 4-2b — G-28 + 4-2 의 금융 매출 줄): fi1.2.0. `fi_fin_summary` 에 `period_months`(INTEGER, 연간만, DART 연간 행·`period_start` 가 없으면 NULL) · 연간 매출 계정 = 분기와 같은 `WISE_Q_REVENUE_*` + 연간 `revenue_basis`. scope TOML `[params.quality] min_period_months = 12`(없으면 끔 — v3_zscore@1.0). 레지스트리에 spec 해시가 없어 파라미터 추가로 바뀌는 기록 값은 없다(같은 `scope@1.0` 이 점수만 달라진다). 엑셀 비고는 후속(4-2a 병합 뒤) — 결산월 변경·리츠 단기 결산도 12개월 미만이라 문구는 '회계기간 N개월'(10-08 검토), 문턱은 spec `quality.min_period_months` 를 읽는다(12 하드코딩 금지). 개월 = 달력 달 수(1월 중 설립이면 12). 모델 판본 mb1.4.0.
  - 영향 재연(가짜 — 서버 10-02 fi 판 사본 + 로컬 equity fin_std 같은 판으로 `period_months` 를 붙여 옛/새 엔진 비교): 12개월 미만 행 4종목(489790 전기 4개월 · 499790 전기 1개월 · 475150 전기 10개월 · 0126Z0 최신 2개월), 순위 변동 138종목(최대 66계단, 499790), 상위 30 집합·순서 불변, v3_zscore@1.0 동일. 금융(WICS G40) scope 28종목은 10-02 판 2025 연간 매출이 전부 빈칸 — 28종목 모두 WISE 분기에 금융 계정이 있다(gross 10 · net 18). 연간 채움 수 자체는 로컬에 WISE cF3002 원천이 없어 미검증(서버 확인 몫).

## 범위 밖 · 알려진 한계
- C-05(가짜 '미해결' 사건 집계)는 v4 묶음(§8-17 먼저, N-26 4.3). 068270 06-04 무상증자 미조정(krx_base_inconsistent, 그날 수정수익률 −6.07%)·295310 매출총이익 증가율 83.8(전기 음수 분모)은 기록만.
- G-27 정확한 수정(원통화 비율)은 cF1001 수집(Q-9) 또는 equity 계약 변경 뒤.
