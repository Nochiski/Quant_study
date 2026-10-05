# 영역 E — 인계(엑셀·텔레그램)·호환 계층 전수 조사

- 영역: E — deliver(엑셀 일일·주간, trend·스파크라인, qpack, view, reader, common, telegram, __main__) + compat(v3 quant.db 호환 계층)
- 범위: src/deliver/*, src/compat/*, docs/MODEL_EXCEL_SPEC.md·COMPAT_LAYER.md, 서버 data/deliver(daily/·history/·latest_*.json), logs/manual/send_*.log(비밀 줄 제외), 크론(읽기)
- 시작: 2026-10-06 02:53 KST · 마침: 2026-10-06 03:24 KST
- 규칙: 읽기 전용. 기존 DECISIONS.md §x/Uxx·10-05 감사 항목은 새 결함으로 세지 않음.

## 결함

### E-01 주간 엑셀은 지금 구조로 항상 실패한다 — fi 층 keep 3 이 월·화 판과 지난주 판의 fi_universe 를 지운다 [상]
- 상황: N-17 로 모델 판은 keep 60 이지만 factor_inputs 8표는 여전히 keep 3(`stage/manifest.py:18` KEEP_DEFAULT=3, `factor_inputs/build.py:192`, CLI `factor_inputs/__main__.py:35` `--keep` 기본 3, 서버 `fi_universe/MANIFEST.json` keep=3·builds 3). 서버 fi_universe 는 v= 3개(09-30·10-01·10-02 판)만 남았다. 09-01~09-29 model 판 19개가 가리키는 fi 판은 전부 없다.
- 인풋: `python -m deliver model-weekly --week 2026-W40`(또는 어느 주든 토요일 실행). 월~금 아침 판 5개 + 지난주 판 5개.
- 에러 위치: `src/deliver/excel_weekly.py:101-115` `load_week`(:113) → `src/deliver/view.py:116` `load_day` 가 fi_root 가 있으면 **무조건** `read_fi(fi_root, "fi_universe", run.fi_build_id)` → `src/deliver/reader.py:171-173` 디렉터리 없으면 `DeliverError` → rc 2.
- 위험성: 하루 1판(아침)만 지어도 토요일엔 수~금 fi 판 3개만 남으므로 월·화 판과 지난주 판 전부를 열 수 없다 → 주간 엑셀은 매주 결정적으로 실패(운영 중단). 같은 이유로 3판보다 오래된 날의 매일 엑셀도 다시 만들 수 없다(정정판 재발행 불가). 기존 U8 은 '모델 판 GC 때문에 못 열 수 있음(추정, 운영 미확인)'으로 적혀 있고 N-17 이 모델 층만 고쳤다 — 실제 원인은 fi 층이며 확률이 아니라 확정이다(기록과 실제가 다름). 주간 엑셀은 아직 한 번도 생성된 적이 없다(서버 `data/deliver/weekly/` 없음). 덧붙여 `excel_weekly.py:40` `BASIS = "morning"` 고정이라 N-13(장 마감 직후 확정)으로 모델 판 basis 가 바뀌면 판을 하나도 찾지 못한다(미래 위험).
- 근거: `ls -d data/factor_inputs/fi_universe/v=*` → 3개(m_20261005T165724·165736·165747). 로컬 재현(`scratchpad/audit/scripts/repro_weekly.py` — 테스트 픽스처 세계에서 fi 판을 최근 3개만 남김): `build_weekly('2026-W39')` → `DeliverError: factor_inputs fi_universe 판이 없다 …/v=m_20260921T010000Z`, 5판 전 매일 엑셀도 같은 오류, 최신 매일 엑셀만 성공. `_runs/*.json` 의 fi_build_id 대조 → 09-01~09-29 19판 fi_dir False, 09-30~10-02 True. 크론에 model-weekly 없음(`crontab -l`).

### E-02 주 모델 scope 에서 버킷 결측이 빈칸으로 보이고 메타 '축별 결측 수'는 0 으로 나온다 [중]
- 상황: 주 모델 scope@1.0(엔진 v3_zscore)은 indicators.parquet 를 0행으로 쓴다(서버 10-02 판 `count(*)=0`). 그래서 `view.ind` 가 비어 있다.
- 인풋: 10-02 판 scope 점수에서 279570(케이뱅크) `valuation_score IS NULL`. 버킷 하나 이상 NULL 인 종목은 09-28~10-02 매일 1건(279570), 09-23 판 6건(252990·330860·044490·005070·484590·279570).
- 에러 위치: `src/deliver/excel_daily.py:281-290` `scored = t in view.ind` 가 False 라 결측 버킷 칸에 `결측(사유)` 를 쓰지 않고 '결측 축'도 비운다. `src/deliver/excel_daily.py:702-703` 메타는 `scored = [t … if t in view.ind]` 로 세어 항상 0.
- 위험성: 발송본(10-02, 10-05 17:10Z) 279570 행은 밸류 유니버스·업종 칸이 빈칸, '결측 축' 빈칸, 메타 '축별 결측 수: … 밸류 0'. 규격 D("결측은 '결측(사유)' 문자열")와 다르고, 받는 쪽은 결측 종목을 '값 없음'으로 구분할 수 없다. 결측 버킷이 있는 종목도 순위(401위)를 받는데 그 사실이 엑셀 어디에도 안 남는다(silent corrupt — 표시 누락 · 문서 불일치).
- 근거: duckdb `SELECT … FROM read_parquet('data/model/scope@1.0/v=m_20261005T165751_069193Z/scores.parquet') WHERE valuation_score IS NULL` → ('279570', rank 401). 로컬 사본 openpyxl: 점수 시트 408행 '밸류 유니버스' None · '결측 축' None, 메타 23행 '모멘텀 0 · 리비전 0 · 수급 0 · 퀄리티 0 · 밸류 0'.

### E-03 순위 흐름·Δ순위 1M 의 비교 판 선택이 판 파일 삭제·spec 부재에 대체 판 없이 '전 종목 빈칸'으로 끝난다 [하]
- 상황: `_runs/<D>_<basis>.json` 은 GC 뒤에도 남지만 `model/<spec>/v=<bid>/` 는 keep(60 빌드, 같은 날 재빌드·저녁 basis 도 한 칸씩 먹음)을 넘으면 지워진다. 또 주 모델 spec id 가 바뀌면(예: scope@1.0 → scope@1.1, N-1 명명) 옛 판에는 새 spec 이 없다.
- 인풋: `build_daily(D)` — 1M 비교 판(D−1개월 이하 마지막 성공 판)의 점수 파일이 없거나 그 판 specs 에 주 모델이 없음.
- 에러 위치: `src/deliver/trend.py:46-47·80·92-94` — `_on_or_before` 가 판 하나만 고르고, `_ranks` 가 None 이면 그 전·후 판으로 넘어가지 않고 `base=None`·`base_rank={}`. `src/deliver/excel_daily.py:802` 메타는 이 경우에도 '없음(그 전 판이 없다)'라고 적는다. 반면 전일 순위는 `excel_daily.py:783` 에서 주 모델이 없으면 **그 판의 primary_spec(다른 모델) 순위**로 대신 채운다 — 두 열의 대체 규칙이 서로 다르다.
- 위험성: 충돌·예외는 없다(좋음). 그러나 그날 Δ순위 1M 이 전 종목 빈칸·흐름 선이 짧아지고, 메타가 원인을 '판 없음'으로 잘못 적는다. spec 교체 첫날엔 '전일 순위' 열이 다른 모델 순위를 주 모델 순위처럼 보인다(문서 불일치 · 조용한 정의 변경). 지금 서버는 22판 × 5 spec 디렉터리가 모두 있어 미발생. 비교·흐름은 같은 basis 판만 보므로(`reader.py:105-130`) N-13 으로 모델 확정 basis 가 아침 → 장 마감 직후로 바뀌면 전환 뒤 한 달 동안 Δ순위 1M·흐름·전일 순위가 같은 방식으로 빈다(전환 계획에 판 이관·basis 매핑이 필요).
- 근거: 코드 경로. 서버 `_runs` 22개 전부 spec_dirs YYYYY(재생성 판), `model/scope@1.0/v=*` 25개.

### E-04 compat 의 행 누락·adj_close 결측 가드가 COMMIT 뒤에 돈다 — 컷오버(제자리 upsert) 때 부분 반영본이 v3 에 남는다 [중]
- 상황: M4 컷오버 뒤 `QL_COMPAT_TARGET=~/kael-system-v3/data/quant.db` 로 제자리 upsert(D-2). 지금은 크론에 compat 가 없고 대상은 별도 파일이라 미발현(아래 '문제없음' 참조).
- 인풋: `python -m compat export --date D --basis morning --target <v3 quant.db>` 에서 (a) 어떤 표의 v3 필수 열(NOT NULL·PK)이 비어 5% 넘게 건너뜀, 또는 (b) daily_prices 창의 adj_close NULL 이 1% 초과.
- 에러 위치: `src/compat/quant_db.py:376-414` `_upsert` — `COMMIT`(:402) 뒤에야 `n_rows == 0`(:406)·`ratio > MAX_SKIP_RATIO`(:410)를 검사한다. `stocks` 는 같은 트랜잭션의 `post=_mark_delisted`(:462-)가 건너뛴 종목까지 `is_active=0` 으로 바꾼 채 커밋된다. `_check_adj_close`(:496-)도 daily_prices 커밋 뒤에 호출된다(:702). 실패 시 `except`(:704-)는 `_compat_meta` 에 failed 만 남기고 이미 커밋된 표를 되돌리지 않으며 뒤 표는 갱신하지 않는다.
- 위험성: 가드가 '쓰기 전에 멈춘다'가 아니라 '쓴 뒤 알린다'다. 제자리 모드에서 v3 후단(07:00 브리핑·unitelegram)이 오늘 가격 + 어제 점수, 일부 종목 폐지 표시(is_active=0 → naver_ir·브로커 리서치가 그 종목을 뺀다) 같은 표 간 불일치 상태를 읽는다(silent corrupt · 부분 반영). 모듈 머리 주석(:13 "전부 쓰기 전/직후에 예외로 멈춘다")과 COMPAT_LAYER §1 의 '부분 기록이 읽히면 안 된다' 취지와도 어긋난다(문서 불일치).
- 근거: 코드 순서 `con.execute("COMMIT")`(:402) → `if n_rows == 0`(:406) → `if ratio > MAX_SKIP_RATIO`(:410). 로컬 재현(`scratchpad/audit/scripts/repro_compat.py`, 테스트 픽스처): R9 예외 뒤 대상 `daily_prices` 6행(그중 adj_close NULL 1행) 커밋된 채 남음 · `_compat_meta` = ('failed','daily_prices'); R7 예외 뒤 5행 남음. 기존 테스트(`tests/test_compat_export.py:691-705`)는 예외·meta 만 보고 대상 표 내용은 보지 않는다.

### E-05 엑셀 열 사전·운영 문서가 v4 주 모델·옛 색 방향 기준으로 남아 있다(주 모델 scope 전환·N-15 미반영) [하]
- 상황: 10-05 주 모델이 scope@1.0(v3_zscore 엔진, 종합 점수 = z 가중합)으로 바뀌었고(N-7), 10-06 색 방향이 '초록 = 좋음'으로 뒤집혔다(N-15). 엑셀 메타 시트의 열 사전은 코드의 `definition` 문자열을 그대로 싣는다.
- 인풋: 10-02 발송본 메타 '열 사전' · `docs/MODEL_DELIVER.md` · `docs/MODEL_EXCEL_SPEC.md`.
- 에러 위치:
  1. `src/deliver/excel_daily.py:232` 종합 점수 = "가중평균(0~100, 엔진 값)" — scope 실제 종합 점수 범위 −1.80~1.48(10-02 판 `min/max(composite_score)`).
  2. `excel_daily.py:241` 제외 사유 = "D-13 적격성(관리·정지·감사·지연·거래대금) · 버킷 게이트(고점근접+반전 하위 30%)", `:154` 거래대금 = "(D-13 적격성 재료)" — v4 규칙. scope 는 모집단 = 순위(510 = 510, 제외 0)라 이 열은 늘 빈칸. 각주 ①은 §6-1 로 고쳤지만 열 사전은 그대로.
  3. `excel_daily.py:244`·`common.py:81` 커버리지 "유예 D+n" — N-14(유예 0) 뒤로 나올 수 없는 값.
  4. `docs/MODEL_DELIVER.md:108-110` "높음 = 빨강 · 순위 열 1위 = 빨강"(N-15 와 반대), `:107` "숫자 열 너비 13"(N-9 최소 폭), `:61·130·134·180` primary_spec·캡션 예시·D-12 문구가 v4_rank@0.1, `:73` 점수 시트 열에 Δ순위 1M·1M 흐름·비고 없음.
  5. `src/deliver/excel_weekly.py:289` 주간 팩터 카드 열 사전 "축 = 엔진 버킷 점수(0~100)" — scope 버킷 점수는 −3~3 z.
  6. `src/deliver/excel_daily.py:54-55` 각주 ① "탈락 종목은 점수 없이 제외 사유만 적는다"·`:296-297` 점수 시트 머리 "제외 종목은 점수만 남고 순위가 없다" — scope 는 유니버스 밖 종목의 행이 아예 없다(모집단 510 = 순위 510).
  7. `docs/MODEL_EXCEL_SPEC.md:8` "v4 순위순"·"다른 모델 순위(v3 원본·v3.1·…)", `:13` "v3.1·v4 리서치"(실제 비교 = v2 원본·v3 원본·v4 기본·v4 동일가중), `:14` "각주 4줄"(실제 5줄, ⑤ 흐름).
  8. `docs/COMPAT_LAYER.md:27` score_history 만료 조건이 `data/deliver/latest_scores_<basis>.json` 직독을 말하지만 이 파일을 만드는 코드가 없다(서버 data/deliver 에도 없음).
- 위험성: 받는 쪽이 메타 열 사전으로 종합 점수를 0~100 척도로 읽고(실제는 ±3 z), 운영 문서를 보고 색을 거꾸로 해석할 수 있다(문서 불일치). 데이터 자체는 맞다.
- 근거: 위 grep 결과, 발송본 메타 41~59행, 서버 duckdb `min(composite_score)=-1.7959, max=1.4815`.

### E-06 유니버스 크기가 바뀌면 Δ순위 1M 의 칸 색·선 색·선 모양이 서로 어긋난다(가운데 = 중앙값 −21.5, 원순위 Δ vs 백분위 선) [하]
- 상황: Δ순위 1M 은 '변화' 열(kind=chg)이라 3색 백분위 10/50/90 스케일을 받는다. 유니버스가 09-02 486 → 10-02 510 종목으로 커져 기존 종목 순위가 전반적으로 밀렸다.
- 인풋: 10-02 판 Δ순위 1M 468값 — p10 −192.3 · p50 −21.5 · p90 174.9(서버 parquet 로 재계산, 발송본과 0건 차이).
- 에러 위치: `src/deliver/qpack.py:91-95` `scale_high_good` 가 가운데를 `percentile 50` 으로 둔다 → `src/deliver/excel_daily.py:235-237` Δ순위 1M 열(kind chg)에 그대로 적용(`qpack.py:309-316`). 같은 행의 흐름 선 색은 `trend.py:105-108` 부호 기준.
- 위험성: (a) Δ −1 ~ −21 인 35종목(7.5%)은 칸이 노랑~연두(=N-15 '순위 상승 = 초록' 쪽)인데 선은 빨강(하락)이다. 소폭 상승 종목은 거의 노랑이다. (b) 선은 판마다 `100·(N−순위)/(N−1)` 백분위(`trend.py:96-101`)인데 선 색은 원순위 Δ 부호라, N 이 486 → 510 으로 늘자 **선 끝이 시작보다 높은데(백분위 상승) 빨강**인 종목이 19개(4%) 생겼다 — 예 009540 Δ −1, 백분위 33.61 → 36.54 · 035250 Δ −4, 43.71 → 45.58. 같은 행의 숫자·칸 색·선 색·선 모양이 서로 다른 말을 한다(표시 오해 · 문서 불일치 — 각주 ③ '초록 = 좋음', ⑤ '위로 갈수록 순위 상승'). 유니버스 크기가 변하는 날마다 재발한다.
- 근거: `d1m` 분포 계산(위 인풋), 발송본 스파크라인 색 검증(선 색은 Δ 부호와 1,020건 전부 일치 — 규칙대로 그려졌다), 숨김 시트 '순위 흐름' 09-02·10-02 열과 Δ 대조(Δ<0 인데 백분위 상승 19건, Δ>0 인데 하락 0건).

### E-07 서버의 마지막 발송본(10-05 17:10Z)은 1W 를 지우기 전 코드로 만든 것이다 — 현재 결정(N-16 1W 삭제)과 다른 파일이 채널에 마지막으로 남아 있다 [하]
- 상황: 1W 삭제 커밋 e6af3117 은 10-05 17:52Z 배포(DEPLOYED.json). 발송 로그 마지막은 `logs/manual/send_20261002_20261005T171034Z.log`(17:10Z), `data/deliver/daily/model_scores_20261002_morning.xlsx` mtime 17:10Z.
- 인풋: 그 파일의 점수 시트 7행 헤더.
- 에러 위치: 산출물 상태(코드 결함 아님) — 점수 시트에 'Δ순위 1W'(M열)·'1W 흐름'(O열) 존재, 스파크라인 1,020개(1W 510 + 1M 510), 메타 각주 ⑤ "1W·1M 동안 … 1일 Δ순위는 노이즈라 싣지 않는다", 메타 '1W 비교 판 2026-09-23'.
- 위험성: 사용자 원문 "1w 델타와 1w흐름은 없애도 되겠다. 삭제해"(N-16)가 반영된 파일은 아직 만들어지지도 보내지지도 않았다. 다음 발송 전까지 채널의 최신본이 결정과 다르다(운영 공백 · 의도된 대기일 수 있음 — 재생성 시각 미확인).
- 근거: openpyxl 로 로컬 사본 헤더 확인, `unzip` sheet1.xml 의 `<x14:sparkline>` 1,020개.

### E-08 같은 날짜 재발송(정정판)이 원본과 구별되지 않고 무엇을 보냈는지 남지 않는다 [하]
- 상황: 10-05 하루에 10-02 엑셀을 5번 보냈다(15:07·15:22·16:28·16:58·17:10Z, 정정 발송 N-10 포함). 매번 같은 파일을 덮어쓴 뒤 보냈다.
- 인풋: `python -m deliver model-daily --date 20261002 --basis morning --send` 반복.
- 에러 위치: `src/deliver/excel_daily.py:764-765` `daily_path` 가 날짜·basis 만으로 파일명을 정하고 `save_atomic` 이 덮어쓴다. `src/deliver/telegram.py:139-145` 캡션에 판 id·생성 시각·정정 표시가 없다. `src/deliver/__main__.py:56-63` 발송 기록은 표준출력뿐(판 id 없음), 보낸 파일 사본·발송 장부·중복 발송 가드 없음. 엑셀 메타(`excel_daily.py` `meta_pairs`)에도 엑셀 생성 시각·deliver 코드 판(rev)이 없어, 같은 model 판으로 코드만 바뀐 정정판(예: 17:10Z 1W 포함본 vs 17:52Z 배포 뒤 1W 삭제본)을 파일만 보고 구별할 수 없다.
- 위험성: 채널에는 같은 캡션("[모델 점수] 2026-10-02 morning · scope@1.0 / 상위5 … / 순위 510 · 제외 0 · 모집단 510")·같은 파일명 문서가 5개 쌓였다. 받는 쪽은 어느 것이 최신(정정)본인지 메시지 시각으로만 알 수 있고, 앞서 받은 파일을 열면 정정 전 내용을 본다. 서버에는 마지막 판만 남아 '그때 무엇을 보냈는지' 재현할 수 없다(운영 공백 · 감사 추적 불가). 자동 발송(Q-3)이 붙으면 재실행 = 중복 발송이다.
- 근거: `logs/manual/send_20261002_*.log` 5개의 caption 줄이 글자까지 같음, `ls data/deliver/daily` 에 20261002 파일 1개(mtime 17:10Z).

### E-09 메타 줄 삽입 위치가 고정 숫자라 1W 삭제 뒤 '순위 흐름 판'이 판 id 묶음 사이에 끼었다 [하]
- 상황: N-16 으로 WINDOWS 가 ("1W","1M") → ("1M",) 로 줄었다. 메타 줄은 `meta_pairs` 결과에 고정 위치로 끼워 넣는다.
- 인풋: 현재 코드로 만든 매일 엑셀(로컬 테스트 픽스처로 생성해 확인).
- 에러 위치: `src/deliver/excel_daily.py:801-803` — `insert(6)`·`[7:7]`·`insert(9)` 가 창 2개를 전제로 한 숫자다.
- 위험성: 메타 순서가 '… 1M 비교 판 · factor_inputs 판 id · **순위 흐름 판** · equity 판 id …'로 판 id 묶음이 갈라진다. 창 수가 또 바뀌면 다른 줄 사이로 옮겨 간다(표시 오류, 데이터 영향 없음).
- 근거: 로컬 생성본 메타 16~18행 = 'factor_inputs 판 id' · '순위 흐름 판' · 'equity 판 id'.

### E-10 주 모델 scope 엑셀은 점수의 원값을 하나도 싣지 않는다 — deliver 가 v4 전용 indicators.parquet 만 읽는다 [중] (기존 Q-11 '원자료 보완'과 겹칠 수 있음 — 원인·범위 구체화)
- 상황: scope@1.0(v3_zscore)은 원값을 scores.parquet 열로 싣는다 — r1m·r3m·r6m·r9m·r12m · op/ni_change_1w/1m/3m · flow_inst/for/pe_5d/20d · qual_gpa/roa/fcf_assets/debt_ratio/gpa_change/std_20d · val_per/pbr/ev_ebitda/dividend_yield 27열 + 표식 6열. indicators.parquet 는 0행.
- 인풋: 10-02 발송본(주 모델 scope).
- 에러 위치: `src/deliver/excel_daily.py:194-202` `_indicators` 가 `view.spec.indicators`(scope TOML 에 [[indicators]] 없음) 또는 `view.ind`(indicators.parquet)만 본다. `sheet_raw`(:319-)·`sheet_display`(:375-)·업종 시트 E/P·리비전·1M 수익률(:595-605, `has` = indicators 키)이 전부 이 경로라 scope 에서는 빈다.
- 위험성: 발송본 '점수 원자료' 시트 = 코드·이름·대분류 + 기준일 6열뿐(지표 0열), 업종 시트 'E/P 중앙값'·'리비전 상향 비율'·'1M 수익률' 37행 전부 빈칸. 규격(MODEL_EXCEL_SPEC A '점수 원자료 = 하위 지표 원값·백분위·표식')과 다르고, 받는 쪽은 모멘텀 99백분위·수급 99백분위의 근거(수익률·순매수)를 엑셀에서 확인할 수 없다(문서 불일치 · 정보 누락). 데이터는 판에 이미 있다.
- 근거: duckdb `DESCRIBE` scope scores.parquet(위 열 목록), `count(*) FROM indicators.parquet = 0`, 발송본 openpyxl 열 머리('점수 원자료' 10열 · 업종 시트 해당 3열 값 0개).

### E-11 (가설 — 외부 근거 있음, 실제 엑셀 미확인) 점수 시트를 정렬하면 '1M 흐름' 꺾은선이 다른 종목의 선을 보인다 [중]
- 상황: 스파크라인 원자료가 다른 시트(숨김 '순위 흐름')에 있고, 점수 시트에는 7행 자동필터(`A7:AP517`)와 1행 정렬 마커('sort'·'▲')가 있어 정렬해 쓰라는 모양이다.
- 인풋: 사용자가 점수 시트를 필터 단추로 정렬(예: Δ순위 1M 내림차순, 대분류 오름차순).
- 에러 위치: `src/deliver/trend.py:128-148` `spark_groups` 가 각 스파크라인을 `'순위 흐름'!<첫 날>{r}:<끝 날>{r}` → '1M 흐름' 열 `{r}` 고정 참조로 만들고(발송본 예: `'순위 흐름'!B515:V515` → `P515`), `src/deliver/qpack.py:391-406` 이 그대로 x14 확장에 싣는다.
- 위험성: Microsoft Q&A(“How to get Sparklines to move on sort when data for Sparkline is in other sheet”)에 따르면 원자료가 다른 시트에 있으면 정렬해도 스파크라인이 행을 따라 움직이지 않는다. 그러면 정렬 뒤 각 행의 선 모양·선 색(Δ 부호)이 다른 종목 것이 되고, 숫자 칸(Δ순위 1M)은 맞는데 선만 틀린 상태가 경고 없이 생긴다(silent corrupt — 표시). 정렬하지 않으면 정상(발송본 1,020개 정렬 전 대응 0건 오류 확인).
- 근거: 외부 문서(learn.microsoft.com/en-us/answers/questions/5223329), 발송본 sheet1.xml 의 `<xm:f>'순위 흐름'!…</xm:f><xm:sqref>P{r}</xm:sqref>` 구조(행마다 고정), 자동필터 정의(`_xlnm._FilterDatabase` '점수'!$A$7:$AP$517). 실제 엑셀에서 정렬 1회로 확인 필요.

### E-12 '재무 접수일'·'Q0 공시일'이 510종목 전부 기준일(D = WISE 수집일)이라 정보가 없고, '공시일' 이름과 다르다 [하]
- 상황: fi_fin_summary 의 `available_date` = 행을 이룬 원천의 max(WISE fetched_date, DART·배당 available_date)(`docs/FACTOR_INPUTS.md:99`). WISE 가 매일 수집되므로 WISE 가 있는 종목은 늘 D 가 된다. scope 유니버스는 전부 WISE 커버 종목이다.
- 인풋: 10-02 발송본 '실적'·'점수 원자료' 시트, 메타 '데이터 기준일'.
- 에러 위치: `src/deliver/excel_daily.py:482-483` 'Q0 공시일' 정의 = "최근 분기 행의 available_date(공시로 알게 된 날)", `:344-345` '재무 접수일' = "available_date(공시·수집으로 알게 된 날)", `:723` 메타 '재무 최신 접수', `excel_weekly.py:267` 팩터 카드 '재무 …'.
- 위험성: 발송본 '재무 접수일'·'Q0 공시일'이 510/510 = 2026-10-02(연간 2025/12·분기 2026/06 모두). 서버 fi 에서 005930 은 2024/12 연간까지 available_date 2026-10-02 다(DART 원천 행은 2026-08-14 등 실제 접수일). 받는 쪽은 '삼성전자 2분기 실적이 10-02 공시됐다'로 읽을 수 있고, 실적 신선도 판단에 쓸 수 없다(표시 오해 · 문서 불일치). 데이터 PIT 는 보수적이라 계산 오류는 아니다.
- 근거: openpyxl 집계 `Counter('Q0 공시일') = {'2026-10-02': 510}`, `Counter('재무 접수일') = {'2026-10-02': 510}`; duckdb fi_fin_summary(10-02 판) `available_date` 분포 — quarter 2026-10-02 3,140행 · 2026-08-14 1,545행 …, 005930 전 행 2026-10-02.

### E-13 deliver CLI 는 DeliverError 밖의 예외를 잡지 않아 rc 1(= 문서상 '발송 실패')로 끝난다 [하]
- 상황: 자동 발송(Q-3)·체인 연결(T3.1)이 rc 로 성패를 가를 때.
- 인풋: 엑셀 생성 중 duckdb 읽기 오류·parquet 손상·`add_sparklines` 의 `ValueError`(시트 못 찾음) 등.
- 에러 위치: `src/deliver/__main__.py:71-91` 은 `DeliverError` 만 rc 2 로 바꾸고 나머지는 트레이스백 → 파이썬 기본 rc 1. `docs/MODEL_DELIVER.md:37-41` 은 rc 1 = '발송 실패(텔레그램 ok=false·예외) · dry-run 비밀 키 없음'.
- 위험성: '엑셀을 못 만들었다'와 '만들었지만 못 보냈다'가 같은 rc 다. 감시·재시도 래퍼가 rc 1 을 발송 재시도로 다루면 같은 경로에 남아 있는 **이전 실행의 엑셀**(`save_atomic` 은 성공해야만 교체)을 보낼 수 있다(운영 · 문서 불일치). 지금은 수동 실행이라 미발현. compat CLI(`compat/__main__.py:58-60`)는 예상 밖 예외도 rc 2 로 바꾼다 — 두 CLI 규약이 다르다.
- 근거: 코드 경로, MODEL_DELIVER rc 표.

### E-14 compat `--model-universe estimates`(그림자에서 쓴 모드)는 스코어링 밖 소비자의 시총을 지운다 — 제자리 대상에 이 모드를 막는 장치가 없다 [하]
- 상황: M1 그림자는 v3 엔진이 추정치 보유 종목만 채점하도록 `estimates` 모드로 돌았다(`_compat_meta.model_universe='estimates'`, 09-22~09-28). 컷오버(제자리 upsert) 때 같은 설정(`QL_COMPAT_UNIVERSE=estimates`)으로 돌리면 v3 `stocks.market_cap` 이 추정치 없는 종목에서 NULL 이 된다.
- 인풋: `compat_export.sh --date D --basis morning` + `QL_COMPAT_UNIVERSE=estimates`(또는 `--model-universe estimates`) + `QL_COMPAT_TARGET=<v3 quant.db>`.
- 에러 위치: `src/compat/quant_db.py:440-459` `_apply_model_universe`(:445-446) — 독스트링은 "이름·시장 열은 남아 브리핑·뉴스·unitelegram 의 조인이 깨지지 않는다"고만 적고, COMPAT_LAYER §2-1 의 시총 소비자(뉴스 preview `market_cap IS NOT NULL ORDER BY … LIMIT 100`, naver_ir 상위 600, unitelegram `kael_db` 시총 표시)는 고려하지 않는다. `scripts/compat_export.sh:25` 는 환경변수만으로 모드를 바꾸고 대상이 v3 파일인지 보지 않는다.
- 위험성: 09-28 그림자 DB 실측 — 2,610종목 중 1,991종목 `market_cap` NULL. v3 자체 시총 상위 100 중 2종목(086520·180640), 상위 600 중 176종목이 NULL 이 된다 → 뉴스 이벤트 preview·naver_ir 수집 대상에서 조용히 빠지고 unitelegram 시총이 빈다(silent corrupt — 컷오버 시 잠재). 컷오버 뒤엔 v3 엔진을 은퇴시키므로(D-4) 이 모드가 필요 없는데도 막혀 있지 않다.
- 근거: sqlite `?mode=ro` — 그림자 `SELECT count(*), sum(market_cap IS NULL) FROM stocks` = (2610, 1991); v3 `ORDER BY market_cap DESC LIMIT 100/600` 대조 2 · 176. 플랜 v1:131 "M1 그림자에서는 compat --model-universe estimates".

## 기존 항목과의 관계
- E-01 = 기존 U8(판 보관 keep 3)의 **실측 확정**. 새 사실: 원인은 모델 층이 아니라 fi 층(N-17 은 모델 층만 60), '못 열 수 있음(추정)'이 아니라 매주 결정적 실패.
- E-10 은 Q-11 '원자료 보완'과 겹칠 수 있다 — 원인(deliver 가 indicators.parquet 만 읽음)과 범위(원자료·업종 지표 3열)를 구체화.
- E-05 의 각주 ① 문제는 §6-1 로 고쳐졌다. 남은 것은 열 사전·머리 각주·운영 문서.
- compat score_history·score_history_v2 매핑 없음(원천 None)은 기존 v2 플랜 T5-1(미구현) — 새 결함으로 세지 않음.

## 문제없음 확인
- Δ순위 1M·Δ순위 1W(발송본)·전일 순위: 서버 parquet 로 독립 재계산 → 510종목 전부 일치(불일치 0). 1M 비교 판 = 09-02(10-02 의 D−1개월 이하 마지막 판), 규칙 N-16 과 같다.
- 스파크라인: 발송본 1,020개 전부 행 정렬(원자료 행 = 그릴 행)·선 색(Δ 부호: 초록 상승·빨강 하락·회색 0/모름) 규칙대로. 1M 원자료 범위 시작 = 비교 판 열(B=09-02).
- add_sparklines XML 주입: 시트 이름은 workbook.xml → rels 로 찾아 시트 위치와 무관, 이름은 상수('점수'·'순위 흐름', 따옴표 없음)라 특수문자 문제 없음. openpyxl 출력엔 시트 extLst 가 없어 `</worksheet>` 앞 삽입 경로만 탄다(발송본 `</extLst>` 1개 = 우리 것). x14 자식 순서(colorSeries…colorLow → sparklines)가 스키마 순서와 같다. 현재 사용 범위에서 xlsx 를 깨뜨리는 경로는 확인되지 않았다(이론적 위험: 장래 중첩 extLst 가 생기면 첫 `</extLst>` 에 들어감, 치환 실패 시에도 개수를 반환).
- 색 방향(N-15): 발송본 조건부 서식 — 점수 시트 백분위·변화 열 낮음 F8696B → 높음 63BE7B, 모델 비교 순위 열 낮음(1위) 63BE7B → 높음 F8696B. 각주 ③ 문구와 일치.
- 유니버스 규칙 줄: '추정치 보유 · common/spac · KOSPI/KOSDAQ · 추정치 유예 없음 · 추정기관수 ≥ 1 · 시총 ≥ 1,000억' — scope TOML·N-6·N-14·U7 과 일치. 커버리지 510 전부 '신선', 추정기관수 최소 1, 1~3명 265종목 '애널리스트 3명 이하' 비고.
- 다른 모델 순위 열: v2 원본·v3 원본·v4 기본·v4 동일가중·최대 차이 존재, 비교 모델 실패 격리(N-11)는 테스트로 확인(메타 '제외된 비교 모델' 줄).
- 종목명·업종 조인: 510종목 이름 빈칸 0·코드=이름 0·'보통주' 꼬리 0·중복 0(KRX 약명), 대분류·중분류 빈칸 0, 시장 KS 303·KQ 207, 시총 최소 1,053억, 거래대금 20일(억)은 equity price_daily value_krw 20세션 평균과 일치(001820 1,506.3억·092870 150.3억).
- 표준 글꼴: 발송본 styles.xml 글꼴 0번 = Arial 10.
- 표시 지표 척도: ROE·ROA·부채비율은 % 단위(중앙값 6.82·3.27·79.28, 005930 ROE 10.36%), 선행 PER 중앙값 11.95·'적자' 48, 선행 PBR 중앙값 1.24 — 배율 오류 없음.
- 업종 시트 합계: 대분류 10행·중분류 27행 모두 종목 수 510 · 시총 비중 100% · 상위 30/100 비중 100% · 후보 30 — 집계 일관.
- 실적 시트 산술: 삼화콘덴서 순이익 FY0 E y-y 120.6% = 278/126 − 1, 삼성전자 Q0 y-y 1,813.8% = 894,924/46,761 − 1 — 계산 규칙(E) 대로.
- 텔레그램 비밀: 발송·파이프라인·백필 로그 7개에서 토큰형·채팅 ID형·`/bot` 경로 패턴 0건, 로그엔 키 이름(CHAT_ID_AIPLAYGROUND)만. 예외·API 오류 문자열은 두 값을 `***` 로 가린다(`telegram.py:124-129`). 발송 5회 모두 ok.
- compat 현재 상태(그림자, 제자리 쓰기 꺼짐): 크론에 compat_export.sh·`-m compat` 없음, `QL_COMPAT_TARGET` 은 스크립트·크론 어디에도 설정 없음(기본 data/compat/quant.db), v3 `~/kael-system-v3/data/quant.db` 에 `_compat_meta` 표 없음(제자리 쓰기 이력 0). 마지막 실행 09-29 00:25Z(`--full`, quant_20260928.db, rc 0). quant-ledger 의 다른 코드(model/compare.py 는 ?mode=ro)도 v3 DB 에 쓰지 않는다.
- compat 스키마 드리프트 없음: 7개 매핑 SQL 을 서버 현재 판(equity m_20261003…, security b_20261005T103717)에 DESCRIBE(바인딩만) → 7/7 바인딩 성공·열 순서 일치, R5 basis·R9 가격 체인 가드 통과.
- compat 단위(GAP-3 미확인 항목): 09-23 investor_detail_flows 5주체 값이 v3 와 2,530종목 전부 정확히 일치(백만원 확인). daily_prices amount·volume 2,426종목 일치, close 일치 412/2,426 은 기존 §6-4(v3 종가 = 애프터마켓 마지막 체결가). 09-28 그림자 실행에서 건너뛴 56,296행(3.07%, 한도 5%)은 전부 거래정지일(OHL NULL·거래량 0) 행.
- 로컬 테스트: `uv run --project backend pytest database/tests/test_deliver_excel.py test_deliver_telegram.py test_compat_export.py test_compat_schema.py test_compat_units.py -q` → 117 passed. 서버·로컬 openpyxl 3.1.5·duckdb 1.5.5 동일.
- COMPAT_LAYER §6-6(리서치센터 야간이 크론에 없는지) 재확인 — crontab 36행 `run_research_center.sh` 는 주석("정지 2026-07-24"). §6 의 GAP-3(흐름 단위)도 위 대조로 닫힌다(백만원).
- data/deliver/history·latest_*.json: 10-02 아침·저녁까지 health stage/equity ok. 10-05 는 개천절 대체공휴일이라 저녁 체인이 '휴장 — 건너뜀'으로 끝난 것이 맞다.
- scope 의 엔진(v3_zscore)은 업종을 쓰지 않는다(`grep -i sector` 0건). 그래서 재생성 판 09-01~09-17(WICS 첫 스냅샷 09-18 이전 — fi 는 `snapshot_date <= D` 로 PIT)의 순위도 업종 공백의 영향을 받지 않고, 1M 비교 기준선으로 써도 된다.

## 미조사
- 실제 엑셀에서 정렬 시 스파크라인 동작(E-11) — 이 환경에 엑셀이 없다.
- 재생성 판(09-01~10-01)의 PIT 성질 전반(추정기관수·커버리지·재무 접수일이 그날 기준인지) — D 영역. 1M 흐름·Δ순위 1M 의 기준선이 여기에 달려 있다.
- compat 매핑의 값 수준 재검증(consensus_*·financial_summary 의 v3 대조) — 컷오버(K3) 전 G-M2 재실행 몫. 이번엔 바인딩·flows·prices 단위만 봤다.
- 주간 엑셀의 나머지 시트 논리(팩터 카드·지난주 성적의 폐지 종목 처리 등)는 E-01 때문에 실데이터로 돌려 보지 못했다(테스트 픽스처로만 통과 확인).
- data/compat 그림자 DB 13개 2.6GB 는 gc 대상 밖(디스크 56% 사용 — F 영역에 넘김).

## 끝
