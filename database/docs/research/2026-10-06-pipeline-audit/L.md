# 영역 L — 장 마감 직후 프로브(probe_postclose) 실행 전 독립 검토

- 시작: 2026-10-06 04:47 KST
- 범위: `database/src/probe_postclose.py`(로컬 v3-merge HEAD b220060a = 서버 배포본), 테스트 `database/tests/test_probe_postclose.py`,
  `src/daily/kw_daily.py`(KW.TRS)·`src/api.py`, 서버 crontab `# postclose-probe` 블록, 시험 흔적 `data/evidence/postclose.db`·`postclose_20261002.md/.json`,
  원장 `data/raw/krx.db`·`kiwoom.db`(읽기 전용)
- 방법: 읽기 전용(외부 API 호출 없음, .env 미열람, sqlite -readonly, 서버 쿼리 05:40 KST 이전)

## 결함

### L-01 NXT 채점 분모에 NXT 미상장 종목(001820)이 들어가 'NXT 첫 전 종목 일치'는 영원히 None, '16:00 뒤 첫 불일치'는 첫 16시대 분으로 고정된다 [중]
- 상황: 고정 3종목 중 001820(삼화콘덴서)은 일부러 고른 'NXT 없음' 종목이다(`src/probe_postclose.py:44-46`). 시험 실측(postclose.db, 02:52 실행): `ka10060`·`ka10086` 의 `001820_NX` 는 빈 응답(`[]`, obs ok=1, err "T 행 없음 — 최신 (빈 응답)"), `ka10095` 의 `001820_NX` 는 `stk_cd` 만 있고 `cur_prc`·`close_pric` 가 빈 문자열인 행.
- 인풋: 10-07 09:20 `probe_postclose.py grade`(target=20261006). NXT 의 005930·086520 가격이 공식 종가와 매 분 같아도 결과가 같다.
- 에러 위치: `src/probe_postclose.py:327-332`(T 행 없음 → 값 None 을 시계열에 넣어 종목 수에 포함) · `:336-341`(ka10095 빈 행 → `price("")` = None) · `:346-352`(`n = len(per)` = 3, `all_ok` 는 `match == n`, `first_diff` 는 `match < n`) · `:428-429`(md 표는 이 두 값만 보여준다).
- 위험성(채점 왜곡 — 측정 자체는 정상): md 표의 NXT 6행(ka10060 cur_prc·ka10086 close_pric·ka10095 cur_prc/close_pric)이 매일 '첫 전 종목 일치 None · 16:00 뒤 첫 불일치 16:00' 으로 나온다. 'NXT 가격은 공식 종가와 끝내 안 맞고 16:00 부터 어긋난다'로 읽히는 구조적 거짓 신호다(판독 ④ 거래소별 차이). **오늘 15:20 첫 실행의 수집은 틀어지지 않는다** — 원자료가 obs 에 남아 재채점할 수 있지만, 10-07·10-08·10-09(~10-12) 아침 보고서의 NXT 행은 그대로 틀린다.
- 근거: 재현 `scratchpad/auditL/repro_l01.py`(NXT 두 종목이 매 분 공식 종가와 같게 넣어도 `NXT.ka10060.cur_prc n=3 first_all_match=None first_mismatch_after_1600=16:00 by_minute={'15:35': 2, …}`, KRX 는 15:35·None 정상). 서버 obs 02:52:37 `ka10060 001820_NX []`, 02:52:39 `ka10095 001820_NX cur_prc ""`. 테스트(`tests/test_probe_postclose.py`)에 빈 T 행 종목 사례 없음.
- 기존 여부: 신규.

### L-02 collect() 가 콜 루프 전체를 sqlite 쓰기 트랜잭션으로 감싸, 15:45·16:00·16:20 에 minute 이 sweep 의 ka10060 100콜(약 32초) 동안 INSERT 에서 멈추고, 60초를 넘으면 'database is locked' 로 죽는다 [중]
- 상황: minute 과 sweep 이 같은 분 :00 에 두 프로세스로 뜨고(F-12 의 시각 겹침) 같은 `data/evidence/postclose.db`(journal_mode=delete, 서버 Python 3.12.3 — 기본 레거시 트랜잭션: 첫 INSERT 앞에 암묵 BEGIN)에 쓴다. 시험 실측 sweep ka10060 은 628콜 200초 = 0.318초/콜 → 100콜 ≈ 32초.
- 인풋: crontab `45 6`·`0,20 7`(sweep) 와 `20-55/5 6`·`0-30/5 7`(minute) — 15:45·16:00·16:20 KST 동시 시작.
- 에러 위치: `src/probe_postclose.py:169-192` — 종목마다 콜 → `INSERT`(:177·:182·:187) 를 반복하고 `con.commit()` 은 루프 끝(:192)에서 한 번. 첫 INSERT 부터 커밋까지 RESERVED 잠금을 쥔 채 API 콜을 계속한다. 다른 프로세스의 INSERT 는 `sqlite3.connect(path, timeout=60)`(:143) 만큼 기다린 뒤 `OperationalError` — 잡는 곳이 없다(:242-251·:451-472).
- 위험성(측정 시각 왜곡 + 결측 가능): ① 확실 — 세 슬롯에서 minute 은 KRX ka10060 몇 콜 뒤 약 30초 멈췄다가 나머지(NXT·SOR)를 쏜다. 같은 '16:00' 칸 안에서 KRX 관측은 16:00:0x, NXT·SOR 관측은 16:00:3x~4x 가 되어, 애프터마켓이 열리는 바로 그 경계에서 거래소 간 비교가 같은 시각 비교가 아니게 된다. ② 조건부 — sweep ka10060 이 60초를 넘으면(평균 0.6초/콜 이상, 응답 지연·유량 재시도 0.5초 백오프 누적) minute 은 예외로 죽고 그 슬롯의 나머지 거래소 관측과 `runs` 행이 통째로 빠진다(로그 traceback 에만 남는다). 55초 근처면 관측이 다음 분 칸으로 넘어가 L-03 의 거짓 불일치가 된다. 덧붙여 F-12 가 걱정한 'ka10060 동시 콜로 5콜/초 초과'는 이 잠금 때문에 실제로는 minute 1콜 정도만 겹친다(유량 측정 오염은 작고, 대신 minute 이 밀린다). **오늘 15:20 첫 실행은 영향 없음(sweep 없음), 같은 날 15:45 부터 영향**.
- 근거: 재현 `scratchpad/auditL/repro_l02.py` — A(timeout 60s, sweep 20콜×0.3s): minute 의 첫 3콜 collect 가 평소 0.7초 → 6.1초(= sweep 커밋 6.2초까지 대기). B(busy timeout 을 2s 로 줄여 '60초 초과' 모사): `[minute] 예외 OperationalError: database is locked`. 서버 `PRAGMA journal_mode` = delete, `.venv/bin/python -V` = 3.12.3. 시험 runs `sweep@0229 ka10060 628콜 02:29:04→02:32:24`.
- 기존 여부: 신규(F-12 는 시각 겹침·유량 오염 가설 — 이 잠금 경로는 다루지 않았다).

