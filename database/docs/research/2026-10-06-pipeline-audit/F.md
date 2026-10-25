# 영역 F — 결정·플랜·문서 정합성 + 교차 영역(보안·디스크·백업·v3 공존)

- 범위: docs/DECISIONS.md, docs/plans/2026-10-05-roadmap.md(게이트 34), docs/plans/2026-10-05-gate-register.md, database/README.md(운영·크론표), docs/TECH_DEBT.md, docs/research/2026-10-05-plan-audit.md ↔ 코드(v3-merge worktree)·서버(crontab, DEPLOYED.json, df, 비밀 파일 권한, data 크기, 백업 로그)
- 저장소: ~/orca/workspaces/Quant_study/v3-merge/database (feat/v3-merge)
- 시작: 2026-10-06 02:53 KST
- 규칙: 읽기 전용. 기존 DECISIONS/10-05 감사에 있는 것은 "기존 §x/Uxx" 로 표시, 새 결함만 F-NN.

## 결함

### F-01 입구 문서(README·START_HERE)가 09월 상태로 정본처럼 남아 있고, 새 결정을 옛 장부로 보낸다 [중]
- 상황: 10-05 에 결정 장부를 `docs/DECISIONS.md` 하나로 합치고(DECISIONS:1-11) 낡은 플랜·DECISIONS_PENDING 에는 '10-05 감사' 머리말을 달았다. 저장소 입구 문서 두 개는 손대지 않았다(`git log -- README.md` 마지막 09-20 00ba456c, START_HERE 기준일 09-08).
- 인풋: 새 세션·에이전트가 저장소 규칙대로 README → START_HERE 순으로 읽는다.
- 에러 위치: `database/README.md:7`("상태 (2026-09-09)"), `:52`(DECISIONS_PENDING = "사람이 정해야 할 것"), `:130`("새 결정은 `DECISIONS_PENDING.md`"), `:17-26`(층 구조가 equity 에서 끝남 — factor_inputs·model·deliver·compat 없음, DECISIONS.md·정본 로드맵 링크 0), `:141`("원장 6 DB" — 실제 `backup_raw.sh:21` 은 wiseindex 포함 7 DB), `:125`(배포를 손 rsync 로 안내 — 운영 절 `:216-231` 은 deploy.sh 만, `rebuild_share.py` '회수 예정'도 `:231` '서버 전용'과 모순), `:150`(22:30 Kael-alpha 스코어 '예정(페이즈 C)' — DECISIONS §5 에서 대체됨), `docs/START_HERE.md:3`(e1.14.0 · 09-08, 현재 e1.23.0, 10-05 감사 머리말 없음).
- 위험성(문서 불일치): README 작업 규칙이 DECISIONS 규칙("결정은 이 문서 한 곳에만", DECISIONS:4)과 정면으로 어긋난다 → 새 결정이 옛 장부에 적혀 K1-12(장부 하나·미등재 0)를 다시 깬다. 10-05 감사(§2-C)는 README 의 WICS 3줄 누락만 잡았다.
- 근거: `sed -n 7p;52p;130p README.md`, `head -3 docs/START_HERE.md`, `git log -3 -- README.md`.
- 덧붙임: README 환경변수 목록(`:173-175`, 11개)에 코드가 읽는 13개가 빠졌다 — 특히 위험 스위치 `QL_NOTIFY_TELEGRAM`(N-3 우회)·`QL_FORCE`·`QL_SKIP_KW`, 그리고 `QL_BUILD_LOCK_HELD`·`QL_RAW_LOCK_HELD`·`QL_STAGE_ROOT`·`QL_LEDGER_ROOT`·`QL_EQUITY_ROOT`·`QL_KW_NOT_BEFORE`·`QL_EVENING_NOT_BEFORE`·`QL_ENGINE_SRC`·`QL_COMPAT_UNIVERSE`·`QL_COMPAT_TARGET`(근거: `grep -rhoE '\$\{?QL_[A-Z_]+' scripts/` + `environ` grep).

### F-02 DECISIONS Q-1 이 설명하는 프로브와 서버에 깔린 프로브가 다르고, 10-06 사용자 결정 4건이 장부에 없다 [하]
- 상황: 10-06 02:55 KST 임시 크론(# postclose-probe) 설치, 배포 rev b220060a(DEPLOYED.json).
- 인풋: `probe_postclose.py minute|sweep|grade` 크론 5줄(15:20~16:30 5분 · 15:45/16:00/16:20 sweep · 매일 09:20 grade), `UNTIL_DEFAULT="20261008"`.
- 에러 위치: `docs/DECISIONS.md:145`(Q-1: "10-06 15:25~16:15 … 10종목 분 단위 + 15:40·15:50 후보 전량(약 650) 1회씩 + 16:30·20:30 재조회, 10-07 아침 … 1회 대조") ↔ `src/probe_postclose.py:6-21·43-49`(3종목 × 거래소 3(KRX·NXT·SOR), 후보 100종목 3회, 1분봉 시험 제외, 10-06~10-08 3거래일, 매 거래일 다음 날 grade). 코드 주석에만 "10-06 사용자 '3종목으로 줄여'·'100종목만해'·1분봉 제외·거래소 3종 요청"이 있다.
- 위험성(문서 불일치): 장부 규칙(DECISIONS:11 "새 결정은 날짜와 사용자 원문을 함께")을 어긴 미등재 결정. 프로브 판정(Q-1 '확인할 것': 후보 전량 소요 시간 등)을 장부 기준으로 읽으면 100종목 → 630종목 환산치가 실측처럼 보일 수 있다. 로드맵 §6 단계 0 ②·K0-4("10-06 21시, KIS·키움 각 5종목")는 §3 머리말에서 재작성 대상으로 표시돼 있어 기존 항목.
- 근거: `crontab -l`(# postclose-probe 블록), `sed -n 1,50p src/probe_postclose.py`.
- 덧붙임(확인): `data/evidence/postclose.db` 에 시험 실행분(T=20261002, 관측 10-06 02:28~02:52 KST, obs 2,729행)이 남아 있고 `postclose_20261002.md/json` 도 시험 채점본이다. 10-06 09:20 KST grade 크론은 직전 거래일 = 10-02 를 대상으로 잡아(`probe_postclose.py:460-465`, DB 존재) 이 시험분을 다시 채점해 같은 파일을 덮는다. 다음 날 결과와 섞이지는 않지만(target_dt 로 거른다) 'T 당일 관측'이 아닌 보고서가 증거 폴더에 실측처럼 남는다.

### F-03 서버 crontab 주석이 실제 줄과 다르다(유령 주석·옛 시각) [하]
- 상황: README 크론표(README:139-155)와 crontab 의 quant-ledger 12줄은 시각·인자가 줄 단위로 일치한다(확인). 주석만 낡았다.
- 인풋: `crontab -l`.
- 에러 위치: 서버 crontab ① "# [임시 · 플랜 P0 Task 0.7 · 3거래일 뒤 제거] 키움·KIS T-1 확정 시각 프로브" — 해당 크론 줄은 이미 없다(README:155 '09-16 제거'). ② "# quant-ledger 워치독(결정 V2-7 → 결정 11): 21:50 저녁 원장 보고 · 23:00 잠정판(보류) · 09:15 확정 빌드 보고" — 실제 줄은 23:30(`30 14`)·10:00(`0 1`).
- 위험성(문서 불일치): 크론을 직접 읽는 사람·에이전트가 워치독 시각을 23:00/09:15 로 오판. 로드맵 공통 5(되돌림 시 크론 원복)·게이트 대장 RG-C0-07 ②(crontab = 기대표 대조)를 만들 때 주석을 근거로 쓰면 틀린다.
- 근거: `ssh kael-server crontab -l`.

### F-04 장부·로드맵 어디에도 없는 systemd 타이머 3개가 매일 실패 상태로 돈다(옛 kael-system/quant-pipeline) [하]
- 상황: 서버 systemd 에 `quant-fetch.timer`(매일 12:00 UTC = 21:00 KST, `main.py --step fetch --fetch-target rbase`), `quant-enrich.timer`(13:00 UTC = 22:00 KST, enrich·adjust), `quant-consensus.timer`(월~토 17:00 UTC = 02:00 KST, consensus fetch·process·factor·score) 가 `Persistent=true` 로 걸려 있다. WorkingDirectory `~/kael-system/quant-pipeline` 은 없다.
- 인풋: 매일 타이머 발화.
- 에러 위치: `systemctl show quant-*.service` → 10-05 세 유닛 모두 `Result=exit-code`·`ActiveState=failed`(시작·종료 같은 초). 문서 grep(`quant-pipeline|quant-fetch|systemd`) 결과 게이트 대장 T5-3·G-M5'⑤ 의 일반 문구 외 0건.
- 위험성(운영 위생·부하): 지금은 즉시 실패라 부하 0. 다만 ① 컷오버 게이트 K3-4('v3 수집 0·이중 수집 0')·로드맵 §5 부하표가 크론만 세고 systemd 를 빠뜨림 ② 그 디렉토리가 복원되면 21:00 KST 가격 수집·02:00 컨센서스 수집이 quant-ledger 저녁 체인(21:05 키움·21:20 빌드)과 겹쳐 다시 돈다(가설 — 사용하는 API 키는 .env 금지로 미확인).
- 근거: `systemctl list-timers --all`, `systemctl cat quant-*.service`, `ls ~/kael-system/quant-pipeline` → 없음.

### F-05 원장 백업이 원본과 같은 물리 디스크 한 곳에만 있다 — 디스크 고장 한 번에 원장 46G 와 그 백업이 함께 사라진다 [중]
- 상황: 서버 디스크는 sda 하나(LVM `/` 474 GB). `data/raw`(46G)·`~/backups/quant-ledger/20261003`(19G, 7 DB) 모두 같은 `/dev/mapper/ubuntu--vg-ubuntu--lv`. 결정 9(09-14)는 보관 개수(주 1회·1세트)만 정했고 보관 위치·외부 사본은 어느 문서에도 없다(docs grep `오프사이트|offsite|같은 디스크|원격 백업` 0건).
- 인풋: 디스크·LVM 장애 또는 실수로 `~/` 삭제.
- 에러 위치: `scripts/backup_raw.sh:17`(`BACKUP_ROOT=$HOME/backups/quant-ledger`), `:19`(KEEP_SETS=1). 백업 대상 밖: `data/raw/documents/`(27G), stage·equity·factor_inputs·model(N-17 로 재생성한 09-01~10-01 이력 포함)·deliver(발송 엑셀·인계 이력)·`logs/health`.
- 위험성(데이터 손실): 원본과 백업이 같은 장치라 디스크 장애에는 백업이 아무 일도 하지 않는다. 다시 받는 비용은 원천별로 다르다 — krx·dart·kiwoom·documents 는 백필 콜(수일~수주, DART 일 한도), WISE 일별 스냅샷(18:05 그날 전량, STAGE_SPEC:455·459)과 KIS 신용(콜당 30영업일, `kis_daily.py:47`)은 과거 일자를 같은 해상도로 다시 받는 경로가 코드에 없다(원천이 과거 일자 스냅샷을 주는지는 미확인 — 가설: 영구 손실).
- 근거: `df -P ~/backups/quant-ledger ~/quant-ledger/data/raw`, `lsblk`, `ls -la ~/backups/quant-ledger/20261003`, `tail logs/backup_raw.log`(10-03 성공).

### F-06 DECISIONS 의 코드 위치(파일:줄)가 10-05 23:11 커밋 뒤로 어긋났다 [하]
- 상황: DECISIONS 표는 '코드' 칸에 파일:줄을 적어 상태 확인의 입구로 쓴다(DECISIONS:10). b2c3fa83(10-05 23:11, N-11·N-12·N-14)·9eccf754(10-06, N-17)가 같은 파일을 고쳐 줄이 밀렸다.
- 인풋: HEAD b220060a 에서 인용 줄을 그대로 연다.
- 에러 위치: N-7 `model/build.py:48` → 실제 `LAYER = "model"`(PRIMARY_DEFAULT 는 :50) · U5 `model/contracts.py:242-244` → `OutputRule` 머리(top_n·max_per_sector 는 :245·:247) · U6 `factor_inputs/build.py:45` → `BASES_KNOWN`(MIN_ELIGIBLE_DEFAULT 는 :47), `gates.py:37` → 주석(FIN_COVERAGE_MIN 은 :38) · U1 `factor_inputs/build.py:203` → 오류 문구(유예 규칙은 :205) · U2 `gates.py:315` → `bad = …`(수집 지연은 :320-321) · U3 `model/build.py:256` → `rerun=engine.run`(격리 판정은 :265·:275) · §5 D-8 `factor_inputs/build.py:42` → `RULES_VERSION`(아침판 제한은 :44 `BASES_IMPLEMENTED = ("morning",)`·:200). 그 밖에 §6 에 '4.' 가 두 번 나온다(DECISIONS:181·186). 같은 결정이 두 ID 로도 있다 — '스냅샷 keep 3' 이 R9(:66)와 결정 9(:72)에 각각(K1-12 '중복 ID 0').
- 위험성(문서 불일치): K1-12 '코드의 미확정 기본값은 결정 장부 §3 에 빠짐없이 등재'를 판정할 때 줄 번호로 대조하면 엉뚱한 줄을 본다. 값 자체는 맞다(U5 30·9, U6 300·0.9, N-12 1, N-14 0, N-17 60 확인).
- 근거: `sed -n` 로 인용 줄 출력, `grep -n "PRIMARY_DEFAULT =\|top_n\|MIN_ELIGIBLE_DEFAULT\|FIN_COVERAGE_MIN"`.

### F-07 결함 장부가 둘로 갈렸다 — 로드맵은 '결함 = TECH_DEBT', 10-01 뒤 결함은 DECISIONS §6 에만 있고 TECH_DEBT 는 09-30 에 멈췄다 [하]
- 상황: 로드맵 K1-12 "결정은 `DECISIONS.md`, 결함은 `TECH_DEBT.md`. 중복 ID 0 · 미등재 0"(roadmap:144). DECISIONS §6 은 '정정할 결함(결정이 아니라 고칠 것)' 7항목(번호 4 중복, 1·2 는 정정 완료 취소선 — 열린 것 5, v3 애프터마켓 종가 결함은 4요소 양식)을 따로 들고 있다.
- 인풋: 10-01 이후 확인된 결함 — v3 종가 = 애프터마켓 체결가(§6-4), README WICS 3줄(§6-4'), E03·D-5 낡은 주석(§6-5), SKIP=통과 4곳(§6-6), 병합 판본 재지정(§6-3).
- 에러 위치: `docs/TECH_DEBT.md:1`(제목 "equity 층 기술 부채 (2026-09-06 실측 · 09-07 갱신)", 마지막 커밋 602f6c64 09-30 B-41) · `docs/DECISIONS.md:177-188`.
- 위험성(문서 불일치): K1-12 판정 기준(결함 = TECH_DEBT)대로 세면 §6 의 결함이 '미등재'로 잡히고, DECISIONS 기준으로 세면 TECH_DEBT 와 이중 관리가 된다. 어느 쪽이 정본인지 규칙이 두 문서에서 다르다.
- 근거: `git log -1 -- docs/TECH_DEBT.md`, `grep -n "애프터마켓\|SKIP" docs/TECH_DEBT.md`(10-01 뒤 항목 0).

### F-08 crit 이 사람에게 닿는 경로가 없다 — 10-01~10-03 crit 4건이 notify.log 에만 있고 처리 기록이 없다 [중]
- 상황: N-3(10-01)으로 notify.sh 는 기본 '로그만'(`scripts/notify.sh:14-22`). 대체 경로인 '매일 아침 점검'은 Q-6(결정 대기)이고 코드·크론·문서 절차가 없다. 로드맵 공통 6 은 "매일 아침 점검해서 보고한다"라고만 적는다.
- 인풋: 서버 `logs/notify.log` 38줄 = crit 4 · info 34. crit: ① 10-01 21:20 KST daily_evening DART rc=2 ② 10-02 07:21 daily_ledger dart rc=2('⚠ 기간 라벨 미해석 1건 … 분기보고서 (2026.08)') ③ 10-02 09:51 일일 리포트 D=10-01 crit ④ 10-03 09:49 일일 리포트 D=10-02 crit(kis_credit partial — 게이트 대장 RG-C0-08 이 지적한 partial→crit 분류).
- 에러 위치: `scripts/notify.sh:16-23`(로그 기록 후 exit 0) · 운영 절차 부재(README 운영 절·로드맵 공통 6 에 점검 주체·시각·기록 위치 없음). '2026.08' 라벨 미해석은 저장소 문서 grep 0건.
- 위험성(운영 중단 감지 공백): 수집 실패·게이트 폐기·디스크 < 50 GB·백업 실패가 crit 으로 찍혀도 누가 언제 보는지 정해져 있지 않다 → 무음 실패. K0-3·K1-11 '무사고' 창은 사람이 crit 을 봐야 셀 수 있다. (기존 N-3·Q-6 의 실제 영향 — 새 사실은 crit 4건의 무처리.)
- 근거: `awk '{print $2}' logs/notify.log | sort | uniq -c`, `grep " crit " logs/notify.log`.

## 표 — 로드맵 §8 게이트 34개 구현 실태(HEAD b220060a = 서버 DEPLOYED.rev, 10-06 03:10 KST)
| ID | 실태 | 근거(코드·서버) |
|---|---|---|
| K0-1 발송 장부 1건 | 미구현 | `src/deliver/` 에 장부(sent_ledger) 코드 0, 서버 `data/deliver/` 에 장부 파일 없음(daily/·history/·latest_*·ledger_evening 만). 모델·발송 크론 0(아래 K0-2) |
| K0-2 실패 시 발송 0·rc≠0 | 부분 | 발송기 가드만: `deliver/reader.py:87` status≠ok 판은 None → rc 2. fi→model→deliver 체인 스크립트(model_daily.sh) 없음(`grep factor_inputs\|-m model\|-m deliver scripts/` 0), 실패 코드 전수 테스트 없음 |
| K0-3 5거래일 무사고 | 미측정 | 자동 발송 자체가 없음(Q-3 대기). 서버 엑셀 `data/deliver/daily/` 09-28~10-02 5개 모두 수동 |
| K0-4 저녁 원천 프로브 | 대체 진행 | N-13 으로 재작성 대상(roadmap:35). 장 마감 직후 프로브 크론 10-06~10-08 가동(F-02 — 장부 기술과 다름) |
| K1-1 main 병합 | 미착수 | Q-4 대기, 서버 branch=feat/v3-merge(DEPLOYED.json) |
| K1-2 배포 회귀 | 부분 | deploy.sh 테스트(DEPLOYED tests=ok)·기존 게이트 묶음은 있음. '배포 뒤 3거래일 crit 0' 판정 장치 없음, 10-01~10-03 crit 4(F-08) |
| K1-3 미래 데이터 0(EG13) | 미구현 | `src/equity/gates.py` 에 eg13 없음(EG13 문자열 src·tests 0건). EG2 는 하한만(`gates.py:216-247`). 기존 TECH_DEBT:12·HD:75 |
| K1-4 조용한 손실 0 | 도구만 | `scripts/equity_diff.py:698 --gate` 있음, 어느 체인 스크립트도 호출 안 함 |
| K1-5 결정성 3/3 | 도구만 | `equity_diff.py:700 --determinism` 있음, 실행 기록·체인 연결 없음 |
| K1-6 수정주가 3,562 | 미착수 | Q-5 대기 |
| K1-7 SKIP≠통과 | 미구현 | DECISIONS §6-6 의 4곳 그대로(기존) |
| K1-8 한계 표기 | 미구현 | `src/model·factor_inputs·deliver` 에 limitations·'한계' 필드 0 |
| K1-9 휴장 달력 ①~⑦ | 미구현(기존 1개만) | 있는 것: `ledger_health.py:234` krx.holiday_misfire(정방향 HALT)·`sync_calendar.sh` 건수·연도·주말 검증. 없는 것: 직접 갱신(①)·역방향 HALT(②)·trading_calendar 대조(③)·12-15 이듬해(④)·단일 모듈(⑤ — fi 는 equity `trading_calendar`, daily 는 KIS 캐시)·축소 경고(⑥)·폴백 금지(⑦, R7 그대로 `calendar.py:97-99`) |
| K1-10 아침 ≤ 60분 | 미달 | 10-03 일일 리포트: morning 5054초(84분)·evening 6092초(101분) — notify.log 21번째 줄 |
| K1-11 10거래일 무사고 | 미측정 | crit 4건(10-01~10-03) |
| K1-12 장부 하나 | 미달 | F-06·F-07 |
| K1-13 판본 강제 CI | 미구현 | `.github/workflows/ci.yml:27` 은 pytest 만, RULES_VERSION 검사 없음 |
| K1-14 원천 대조 등급 | 미구현 | 판 메타에 대조 등급 필드 없음 |
| K2-1~K2-7·K2-9 모델 DB | 미구현 | N-4 미구현(DECISIONS:32). 모델 DB 코드·디렉터리 없음 |
| K2-8 모델 유동성 | 부분 | 비교 모델 실패 격리 구현(`model/build.py:265·275`, mb1.2.0, N-11). status·main 1개·lock·골든 불변·승격 절차 없음(`model/registry.py` 에 status/main/lock 0) |
| K3-1~K3-7 컷오버 | 미구현 | compat 코드·`data/compat`(2.6G)는 있으나 게이트 없음. K3-5(키 이관) — 비밀은 여전히 `~/kael-system-v3/.env`(`src/api.py:14-16`, `deliver/telegram.py:35-37`, `notify.sh:24`) |
- 요약: 구현 0 · 부분 3(K0-2·K1-2·K2-8) · 도구만 2(K1-4·K1-5) · 대체 진행 1(K0-4) · 나머지 28 미구현·미착수·미측정. 로드맵 머리말 "전부 미구현"(roadmap:107)은 K2-8 일부(N-11)가 구현돼 이제 사실과 조금 다르다.

### F-09 scope 엑셀을 0건으로 만들 수 있는 코드 기본값 3개가 결정 장부 §3 에 없다 [하]
- 상황: DECISIONS 규칙 "코드의 기본값·임계를 바꾸거나 인용하기 전에 여기서 상태를 확인한다. 미확정 값은 사용자에게 묻는다"(DECISIONS:10), 로드맵 K1-12 "코드의 미확정 기본값은 결정 장부 §3 에 빠짐없이 등재". §3-A U6 은 fi 하한(eligible 300·재무 0.9)만 '미달이면 엑셀 0건'으로 올렸다.
- 인풋: fi → model 빌드(주 모델 scope@1.0 = v3_zscore 엔진).
- 에러 위치: ① `src/model/gates.py:45` `COVERAGE_MIN = 0.95`(MG1 — v3·v2 엔진 = scope 에 적용, 자기 유니버스 대비 점수 비율 하한) ② `:47` `MIN_PRICES_ON_D = 2000`(MG4 — v3 상수 상속) ③ `src/factor_inputs/build.py:50·153-157` `BUILD_CHAIN_MAX_GAP_H = 3`(가격·수정주가·계수 equity 판 빌드 시각 차 > 3시간이면 fi 예외 → 모델·엑셀 0건). 참고로 MG5 `SPEARMAN_WARN 0.8`·`TOP_N 30`(경고만)도 미등재. DECISIONS 전문 grep(`0.95|2000|MAX_GAP|MG1|MG4|MG5`) → U10 의 KIS '첫 수집 0.95' 1건뿐(무관).
- 위험성(문서 불일치 → 운영 중단 원인 추적 곤란): 이 셋 중 하나로 주 모델 판이 FAIL 하면 N-11 격리와 무관하게 그날 엑셀이 나가지 않는데, 장부에는 근거·상태가 없어 '미확정 기본값'으로 묻지도 못한다. ③은 표 하나만 손으로 다시 지은 날(부분 재빌드) 바로 걸린다.
- 근거: `grep -nE "^[A-Z_]+ *= *[0-9]" src/model/gates.py src/factor_inputs/build.py`, `grep -nE "0\.95|2000|MAX_GAP|MG1|MG4|MG5" docs/DECISIONS.md` → :115(U10) 1건.

### F-10 정본 로드맵이 10-05 밤·10-06 결정을 따라가지 않아 일부 서술이 지금 코드와 반대다 [하]
- 상황: 로드맵 마지막 커밋 0dace1bf(10-05 23:11). 같은 시각 b2c3fa83 이 N-11·N-12·N-14 를 구현했고, 10-06 에 N-15~N-17 이 들어왔다.
- 인풋: 로드맵 §7·§8 을 정본으로 읽는다(roadmap:3 "목적·완성 조건·구조·순서·게이트는 이 문서가 정한다").
- 에러 위치: `docs/plans/2026-10-05-roadmap.md:158`(K2-8 "지금 코드는 반대 — 한 모델이 실패하면 전부 미공개, `model/build.py:256`, U3" — 실제는 격리 구현 `model/build.py:265·275`) · `:99`(단계 0 결정 대기에 U1·U2·U3 — 이미 N-14·N-12·N-11 확정) · `:104`('결정됨'이 N-5~N-10 까지만) · `:107`("전부 미구현") · `:74`(스냅샷 약 16GB — 서버 실측 판당 18G).
- 위험성(문서 불일치): 결정은 DECISIONS 가 이긴다는 규칙(roadmap:3)이 있어 피해는 제한적이나, 게이트 판정자가 K2-8 을 읽으면 이미 고친 것을 결함으로 센다.
- 근거: `git log -1 -- docs/plans/2026-10-05-roadmap.md`, `sed -n 99p;104p;107p;158p`, `du -sh data/snapshots/*`.

### F-11 확정·잠정 빌드가 거래일마다 약 2분씩 길어져 워치독 여유가 아침 9~11분·저녁 28분 남았다 — 원인은 매일 전량 재파싱되는 WISE 누적 표, README 실측 시각은 낡았다 [중]
- 상황: 워치독 시각은 '실측 종료 + 여유'로 정했다 — 아침 10:00 = "실측 종료 09:23~09:30 + krx_step 재시도 1회 +10분 흡수"(README:145, DEFECT-D03), 저녁 23:30 = "한도 21:45 시작 + stage 43~66분 + equity 9~11분 = 23:06"(watchdog.sh:6, D02).
- 인풋: 서버 `data/deliver/history/<D>_{morning,evening}.json` 의 generated_at(KST 환산).
  - 아침(D → 완료): 09-16→09-17 09:22 · 09-17→09:30 · 09-21→09:21 · 09-22→09:33 · 09-28→09:25 · 09-29→09:41 · 09-30→09:41 · 10-01→10-02 09:51 · 10-02→10-03 09:49(5,054초).
  - 저녁(완료): 09-18 22:41 · 09-21 22:25 · 09-22 22:41 · 09-28 22:28 · 09-29 22:48 · 09-30 22:57 · 10-01 22:57 · 10-02 23:02(6,092초).
- 에러 위치: `scripts/watchdog.sh` morning_build(`latest_morning.json` date ≠ 직전 거래일이면 crit, 10:00 크론) · `scripts/daily_build.sh:44-70`(KRX 미공표 시 10분 × 최대 6회 재시도 → 빌드 시작이 그만큼 밀림) · `README.md:144-145`(실측 종료 09:23~09:30)·`:147`(저녁 실측 종료 22:38~22:41)·`:202`(daily_report ≈08:55 KST) — 지금 실측과 다르다.
- 위험성(운영 중단 감지 오탐·지연): 지금 종료(09:49~09:51)에 KRX 재시도 1회(+10분)만 겹쳐도 정상 진행 중인 판을 10:00 에 crit 으로 찍는다(설계가 흡수하기로 한 바로 그 경우). 추세가 이어지면 저녁도 23:30 을 넘는다. sar 실측(10-01·10-02 KST 21:00~23:00): CPU 유휴 3~15%, iowait 최대 72% — 저녁 빌드(21:21~23:02)가 v3 daily_all(20:05~23:31, adj_prices 22:09~22:51)과 한 디스크에서 겹친다. 아침(08:30~09:50, v3 미가동)도 iowait 30~75% 라 빌드 자체가 I/O 묶임. 원인 분해는 아래 덧붙임(stage 의 WISE 누적 표). 덧붙여 로드맵 §5 의 'v3 체인 약 160분'(roadmap:73)은 실측 180분(10-01 20:05→23:05)·206분(10-02 20:05→23:31, adj_prices 41.5분 포함)이다(로드맵 스스로 '재실측 필요'라 적음 — 기존).
- 근거: history JSON generated_at 목록, `LC_ALL=C sar -u -f /var/log/sysstat/sa01|sa02|sa03`, `notify.log` 10-03 일일 리포트(morning 5054초·evening 6092초), v3 `pipeline.log` job 시각(읽기만).
- 덧붙임(원인 확인, 03:25): 늘어난 것은 stage 다 — build_morning 단계 시각(`logs/morning/build_<D>.log`) 09-18: stage 3,294초 + equity 642초(08:24→09:30) → 10-03: stage 4,386초 + equity 668초(08:24→09:49). 표별(`logs/stage_all/summary.tsv`): `stg_fin_wise` 159초(summary.tsv 첫 10판 중앙값) → 804초(최근 7거래일 행 4.02M → 5.57M, 거래일마다 약 +22만 행 — WISE 매일 전체 저장 D-Q4 를 R2 가 매일 전량 다시 파싱), `stg_fin_wise_q` 44 → 89초(10-01 신설, 하루 +64만 행), `stg_consensus_monthly` 32 → 95초(+20만 행/일), `stg_fin` 1,183 → 1,720초(행은 거의 그대로 — 원인 미상). 행이 날마다 쌓이는 표를 날마다 통째로 다시 지으므로 소요가 거래일 수에 비례해 는다.
- 추정(선형 외삽, 가설): 아침은 D=09-17→10-02 9거래일에 +18.8분(≈ +2.1분/거래일) → 10:00 여유 10.7분이 약 5거래일 뒤(D=10-13 판, 10-14 아침 전후) 소진, 저녁은 D=09-18→10-02 8거래일에 +21분(≈ +2.6분/거래일) → 23:30 여유 28분이 약 11거래일 뒤(10-21 전후) 소진. `stg_fin_wise_q` 는 10-01 에 생겨 아직 이틀치라 더 빨라질 수 있다. K1-10(아침 ≤ 60분)과는 반대 방향으로 멀어진다. D-Q4 ↔ RM:74 충돌(§3-C)은 기존이지만 '소요가 매일 는다'는 결과와 워치독 여유 소진은 장부·로드맵에 없다.

### F-12 장 마감 프로브의 sweep 3회가 전부 minute 실행과 같은 분에 시작해 '유량 초과·소요 시간' 측정이 자기 자신과 섞인다 [하 · 오늘 15:20 전 조치 가능]
- 상황: 임시 크론(10-06~10-08). sweep 은 "운영 수집기와 같은 속도(4.4콜/초)로 소요 시간·실패·유량 초과를 잰다"(`probe_postclose.py:11-13`). 키움 실측 상한은 TR 당 5.0콜/초(`daily/kw_daily.py:44` 주석).
- 인풋: crontab `20-55/5 6 * * 1-5`·`0-30/5 7 * * 1-5`(minute = 15:20…15:55·16:00…16:30 KST) 와 `45 6`·`0,20 7`(sweep = 15:45·16:00·16:20 KST). 세 sweep 시각이 모두 minute 시각 집합에 들어 있다(같은 분 :00초 동시 시작). minute 1회 = 3종목 × 거래소 3 × (ka10060·ka10086) + ka10095 3콜 ≈ 21콜, sweep = ka10060 100콜 + ka10095 묶음.
- 에러 위치: 서버 crontab # postclose-probe 블록 · `src/probe_postclose.py:76-84`(CountingClient 가 프로세스 안에서만 429 를 센다 — 다른 프로세스 콜과 구분 못 함)·`:164-195`(간격 1/4.4초 고정, 프로세스 간 락 없음).
- 위험성(측정 오염): 같은 TR(ka10060)을 두 프로세스가 같은 초에 쏘면 TR 당 5콜/초를 넘을 수 있고, 그 429 와 지연이 sweep 의 n_rate·소요 시간(→ 100종목 → 630종목 환산, Q-1 '후보 전량 소요 시간')에 그대로 들어간다. 실제로 429 가 나는지는 미확인(가설) — 시각 겹침은 사실.
- 근거: `crontab -l`, `grep -n "RATE_PER_SEC\|gap\|rate" src/probe_postclose.py src/daily/kw_daily.py`.

### F-13 보안 정리(#99·#165, 09-20)가 빠진 브랜치가 이미 원격에 올라가 있고, 사설 IP 1건은 main 에도 남아 있다 [하]
- 상황: PR #99 "public 레포에서 서버 계정명·홈 경로 제거"(5f97f8ce)·#165(서버 주소 기본값 제거)는 main 에만 있다. feat/v3-merge 는 09-20 d0372a4d 에서 갈라졌다(`git merge-base`). HD:144 는 이것을 '병합 때 보안 역행 위험(39파일)'으로 적었다(기존).
- 인풋: `git grep -I '<서버 홈 경로>'` — origin/main 0줄, HEAD 72줄(39파일), **origin/feat/v3-merge(원격 끝 3af78837 — 10-02 커밋까지 푸시됨) 72줄**. `git grep -E '192\.168\.'` — main·브랜치 모두 1파일.
- 에러 위치: `database/README.md`(9줄 — 크론 복구 원문 포함)·`scripts/*.sh`(watchdog 6 등)·`src/daily/ledger_health.py`(1) · `docs/DOC_DESIGN.md:221`(로컬 LLM 호스트의 사설 IP — #99 범위 밖).
- 위험성(보안 — 정보 노출): 새 사실은 '병합 때 위험'이 아니라 이미 원격 브랜치에 계정명·홈 경로가 올라가 있다는 것(저장소 공개 여부는 #99 제목 근거, GitHub 설정은 미확인). sshd 는 비밀번호 차단(`00-hardening.conf`)·공유 계정 chroot+읽기전용 sftp 라 직접 위험은 낮다. HEAD 는 원격보다 22커밋 앞서 있어 다음 push 때도 같은 문자열이 나간다.
- 근거: `git merge-base --is-ancestor 5f97f8ce HEAD` → 아님, `git grep -I '<서버 홈 경로>' origin/feat/v3-merge -- database | wc -l` → 72, `git rev-list --count origin/feat/v3-merge..HEAD` → 22. (주: 이 확인을 위해 `git fetch -q origin` 을 1회 실행 — 원격 추적 ref 만 갱신, 작업 트리 무변경.)

### F-14 v4 비교 모델의 '유예 5거래일'(TOML·DECISIONS N-14)은 fi1.1.0 뒤 실제로 작동하지 않는다 — 기록과 실행이 다르다 [하]
- 상황: N-14(10-05)로 fi 공용 유예가 0 이 됐다(`model/contracts.py:234`, fi1.1.0). DECISIONS N-14 는 "v4 TOML 은 5 를 명시(비교 모델, U26)"라고 적어 v4 는 5 로 도는 것처럼 읽힌다.
- 인풋: `python -m factor_inputs build`(기본 `UniverseRule()` — `factor_inputs/build.py:205`) → `python -m model build` 의 v4_rank@0.1·0.2(`config/models/v4_rank_0_1.toml:30`·`v4_rank_0_2.toml:27` `coverage_grace_days = 5`).
- 에러 위치: `src/factor_inputs/queries.py:168-170`(require_estimates 면 lapsed → exclude_reason 'estimates_lapsed' → eligible false; G=0 이면 상태는 fresh 아니면 lapsed 뿐)·`:407·543·587`(추정·WISE 재무 행을 fresh·grace 에만 싣는다) → `src/model/engines/v4_rank.py:562-563`(`if not r["eligible"] … continue` 가 v4 자체 유예 검사 `:569-573` 보다 먼저). spec 유예 > fi 유예일 때 경고·검사 없음(`model/build.py`·`contracts.py` grep 'grace' 일치 검사 0).
- 위험성(silent corrupt — 비교 모델 정의의 무음 변경): v4 는 10-05 부터 사실상 유예 0 으로 돈다. TOML·결정 장부·`docs/FACTOR_INPUTS.md` 어디에도 이 효과가 적혀 있지 않아, v4 와 scope 비교(K2-8 승격 판단·U26 결정)에 쓰는 v4 의 정의가 기록과 다르다. scope 엑셀에는 영향 없음.
- 근거: 위 파일:줄 `sed -n`, `grep -n coverage_grace_days config/models/*.toml`.

### F-15 모델·엑셀 설계 문서가 N-7·N-14·N-15·N-16 을 일부만 따라갔다 [하]
- 상황: N-7(메인 = scope@1.0)·N-14(유예 0)·N-15(초록 = 좋음, 1위 = 초록)·N-16(1일·1W Δ순위 삭제)은 코드에 반영됐다(`model/build.py:50`, `deliver/excel_daily.py:61-63·235-239`).
- 인풋: 설계 문서를 엑셀 해석 기준으로 읽는다.
- 에러 위치: `docs/MODEL_DELIVER.md`(마지막 커밋 09-29 f0325b76) `:61` primary_spec 예시 v4_rank@0.1 · `:73` 점수 시트 '전일 순위 · Δ순위'(1일)·커버리지 '유예 D+n' · `:109` "순위 열은 반전(1위 = 빨강)" — N-15 와 반대 · `docs/MODEL_EXCEL_SPEC.md:8` "유니버스 종목 1행, v4 순위순" · `:13` 모델 비교에 scope 없음 · `docs/MODEL_BUILD.md:37-38` `--specs all` 목록에 scope@1.0 없음(레지스트리 5개 — `config/models/`), `:149` 예시 primary v4.
- 위험성(문서 불일치): 엑셀 각주·색을 문서 기준으로 검수하면 정상 산출물을 결함으로(또는 그 반대로) 판정한다. 결정 장부와 문서가 엇갈리면 장부가 이기지만(DECISIONS:4), 설계 문서에는 '10-05 감사' 머리말이 없다.
- 근거: `grep -n "Δ순위\|전일 순위\|1위 = 빨강\|v4 순위순\|primary_spec" docs/MODEL_*.md`, `git log -1 -- docs/MODEL_DELIVER.md`.

### F-16 서버 logs/ 에 저장소 밖 수동 텔레그램 발송 스크립트가 남아 있다 — deliver 가드를 거치지 않는 두 번째 발송 경로 [하]
- 상황: 모델 엑셀 발송은 `src/deliver`(status ok 판만, 기본 채널 AIPLAYGROUND — `deliver/reader.py:87`, `telegram.py:23`)로 한다. 게이트 대장 '발송 장부' ①은 "모든 엑셀 발송은 래퍼 하나를 거친다"를 요구한다(채택 전).
- 인풋: 서버 `~/quant-ledger/logs/tg_send_doc.py`(8줄, 09-26 06:59 UTC, 644) + 같은 폴더 `sample_model_scores_20260923_morning.xlsx`.
- 에러 위치: `logs/tg_send_doc.py:4`(`~/kael-system-v3/.env` 직접 파싱)·`:7`(curl `-F chat_id=CHAT_ID_AIPLAYGROUND -F document=@<인자>` — 판 상태·중복 검사 없음). 저장소에 없음(src 대조 결과 서버 전용은 rebuild_share.py·sync_v3_wise.py 둘뿐), deploy.sh 범위 밖이라 배포로 지워지지도 않는다. (토큰·채팅 ID 하드코딩은 없음 — 패턴 grep 0건.)
- 위험성(운영 — 무장부 발송): 이 경로로 보낸 엑셀은 FAIL 판·중복 여부를 보지 않고 나가며 기록도 남지 않는다. 토큰이 curl 인자(URL)로 넘어가 같은 호스트의 프로세스 목록에 잠시 보인다(로컬 사용자는 운영 계정·공유 계정(chroot sftp) 둘뿐이라 실위험 낮음).
- 근거: `ls -la ~/quant-ledger/logs/tg_send_doc.py`, `grep -nE "\.env|CHAT_ID" tg_send_doc.py`(값 마스킹), 토큰 패턴 count 0.

## 참고 — 결함은 아니지만 종합에 쓸 사실
- 현재 판 세트가 규칙 판본 혼합이다(10-05 수동 재빌드): stage 2.4.0 × 67 + 2.5.0 × 1(`stg_analyst_summary`, 10-05 10:05Z) · equity e1.22.0 × 29(그중 `coverage_daily` 는 10-05 10:05Z 재빌드) + e1.23.0 × 1(`security`, 10:37Z). fi `_runs/20261002_morning.json` 이 `b_…` 판 id 로 기록하므로 추적은 된다. 10-06 08:10 전량 빌드로 해소 예정. 이 판 위에서 09-01~10-02 모델 이력 25판(N-17)을 지었고, 그 fi·equity·stage 입력 판은 keep(3·10) 로 며칠 안에 지워진다 → 재생성 이력의 입력 재현은 불가(모델 산출물은 keep 60 으로 남음).
- 모델·발송 크론 0 — fi → model → deliver 는 전부 수동(서버 `data/deliver/daily/` 09-28~10-02 5개). 기존 Q-3(아침 자동 발송 결정 대기).
- 일일 리포트의 '알림 실패(24h) 0건'은 로그 모드에서 늘 0 이다(`notify.sh` 가 로그만 쓰고 exit 0) — 게이트 대장 RG-C0-03 이 이미 지적.
- 10-01~10-03 crit 의 원인은 DART 기간 라벨 미해석('분기보고서 (2026.08)')과 kis_credit partial → crit 분류(RG-C0-08) — 영역 A 몫.
- 저녁 키움(21:05~21:20)은 10-01·10-02 모두 rc 0, 2,651~2,652/2,655 종목 — v3 daily_all(20:05~)과 앱키 동시 사용 흔적 없음(로그 기준).

## 문제없음 확인
1. 서버 코드 = 저장소 HEAD b220060a — DEPLOYED.json(rev·branch feat/v3-merge·tests ok), scripts 41개·src 219개 md5 일치, 서버 전용은 `rebuild_share.py`·`sync_v3_wise.py` 둘(README:231·deploy.sh:40-49 와 일치), `config/models` 5개 동일.
2. README 크론표 12줄 = crontab quant-ledger 12줄(UTC→KST·인자·로그 경로 일치). 복구 원문 9줄은 서버 줄과 글자 그대로 같고 WICS 3줄만 빠짐(기존 §6).
3. keep 값 = 결정: stage 3·fi 3·equity 10·model 60·스냅샷 3판·백업 1세트(R9·U8·U20·N-17·결정 9 ↔ 서버 MANIFEST·디렉터리).
4. 결정 값 일치: N-12 `COLLECTION_LAG_MAX = 1`, N-14 유예 0, N-6 `min_analysts = 1`, U7 시총 1,000억, U5 30·9, U6 300·0.9, D-12 기본 채널 AIPLAYGROUND, N-3 notify 기본 로그만(10-03 백업 알림 'logged only').
5. 비밀 파일 권한: `src/.kis_token.json`·`.kw_token.json` 600, `~/kael-system-v3/.env`·백업 4개 600, `data/calendar/*` 600. `~/quant-ledger/.env` 는 없음(키는 v3 .env 에서 읽는 설계 — README:126·`api.py:14-16`).
6. 저장소 database/ 에 비밀값 없음 — 키 패턴 5건은 테스트 더미(값 길이·문자 판정), 공인 IP 0건(사설 IP 1건은 F-13).
7. sshd: 비밀번호·root 로그인 차단(`00-hardening.conf`), 공유 계정 = chroot + 읽기전용 internal-sftp, 바인드는 raw·stage·equity 3개.
8. 디스크: `/` 466G 중 200G 여유(56%). data ≈ 162G(equity 55·snapshots 54·raw 46·stage 4.4·compat 2.6), 백업 19G(+v3 14G). 하한 50·60G 와 거리 충분. kern.log·syslog 에 OOM 없음.
9. 백업 10-03 03:30~03:37 KST 7 DB 성공·integrity ok·이전 세트 삭제 — 결정 9 대로. v3 백업(03:00, 14초)과 시각 안 겹침.
10. v3 와 같은 DB 파일 쓰기 없음 — v3 트리에서 읽는 것은 `.kis_holidays.json`(06:00·18:05 복사, v3 쓰기 20:05·매월 1일 09:00·12-30 05:00 과 분리)과 `.env` 뿐. compat 은 `data/compat/quant.db` 사본에만 쓰고, model.compare 는 `mode=ro`·수동.
11. 휴장 달력 2026 파일 121건 — 10-03·10-05·10-09·12-25·12-31 휴장, 10-06~10-08 거래일. 이번 주 체인 판정 정상.
12. 장 마감 프로브: 원장 무기록(raw 는 `mode=ro`), `--until 20261008` 로 10-09 이후 minute·sweep 무동작(grade 는 10-12 까지 10-08 재채점 덮어쓰기만).
13. DECISIONS 인용 중 운영 쪽 줄 번호는 맞음 — `daily_evening.sh:64·69·83-95`, `daily_build.sh:44-70·104`, `kw_daily.py:49·54-58`, `kis_daily.py:47-51`, `calendar.py:97-99`, `gc.sh:80-84`, `backup_raw.sh:19`, `watchdog.sh:5-8`, `dart_daily.py:100·105`, `price_daily.sql:116`(표류는 model·fi 쪽 — F-06).
14. 10-05 감사 §4 의 로드맵 모순 중 #1·#2·#3·#4·#6·#7·#8·#11 은 로드맵에서 해소 확인.
15. 플랜 15개 전부 git 추적(게이트 대장 RG-C0-05 ⑤ '미추적' 서술은 이제 사실 아님).
16. 크론 시각 UTC→KST 전수 환산 확인(probe 5줄 포함 — 15:20~16:30·15:45/16:00/16:20·09:20).

## 미조사
- 게이트 대장 본문(355줄 중 1~68줄만) — 비정본이라 후순위. C1~C7·§3~§6 의 코드 위치 대조 안 함.
- v3 daily_all 내부 단계별 키움 사용 시각(20:05~22:09) — 저녁 키움 21:05~21:20 과 앱키 동시 사용 여부는 로그 rc·오류 없음까지만 확인.
- `stg_fin` 소요가 행 수 변화 없이 1,183 → 1,720초(저녁 최대 1,988초)로 는 원인(동시 실행 표와의 I/O 경합·캐시 압박 가설) — 영역 B.
- KIS·DART 키 공유 여부(.env 금지), unitelegram(15:35·15:40, KIS 토큰을 v3 data 폴더에서 읽음)과의 토큰 경합.
- WISE·KIS 과거 일자 재수집 가능 여부(F-05 의 가설 부분) — 원천 확인은 외부 호출이라 안 함.
- HD·MDB·v2 등 비정본 플랜의 세부 결정 ↔ 코드 전수 대조, docs/reviews·handoff 의 낡은 서술 전수.
- notify.log crit 4건 각각의 원인·처리 경과(영역 A).


- 종료: 10-06 03:22 KST (결함 16건: 중 4 · 하 12, 상 0)

## 끝
