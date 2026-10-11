# 모델 산출물 전달층 (`src/deliver/`)

> 플랜 [`2026-09-24-v3-merge.md`](plans/2026-09-24-v3-merge.md) M2 W2 T2.5b · 결정 D-12.
> 엑셀 규격 정본은 [`MODEL_EXCEL_SPEC.md`](MODEL_EXCEL_SPEC.md)이고, 이 문서는 **그 규격을 어떻게
> 구현했는지 · 어떻게 돌리는지 · 무엇을 아직 못 했는지**를 적는다.

## 1. 목적과 경계

- model 판(`data/model/`)과 factor_inputs 판(`data/factor_inputs/`)을 **읽기만** 해서 매일 엑셀 ·
  주간 엑셀을 만들고, 텔레그램 모델 채널(`CHAT_ID_AIPLAYGROUND`)로 보낸다.
- 점수를 다시 계산하지 않는다. 엔진이 쓴 값(점수 표 · 지표 긴 표)을 옮기고, 표시용 가공만 한다:
  버킷 점수의 백분위 재순위, 1/99 윈저라이즈, 부호 전환 표식, 단위 배율(비율 → %).
- `src/model/build.py` 를 import 하지 않는다. 파일 규약(§3)만 공유한다. 백분위 공식은 엔진의
  `pct_rank_avg` 를 import 해서 쓴다(정의가 두 곳으로 갈리지 않게).
- 엑셀은 **값만** 쓴다(수식 없음). `=` 로 시작하는 문자열(종목명 등)도 문자열 형으로 고정한다.
- 판단 시트는 없다(사용자 09-26).

## 2. 실행

```bash
# 매일 — 그날 판(_runs/<YYYYMMDD>_<basis>.json)으로
python -m deliver model-daily  --date 20260929 --basis morning [--send [--resend]] [--dry-run]
# 주간 — 그 ISO 주 월~금 아침 확정판으로
python -m deliver model-weekly --week 2026-W40                [--send] [--dry-run]

# 공통 선택
#   --model-root data/model  --fi-root data/factor_inputs  --out-root data/deliver
#   --config-dir config/models   --env-file PATH   --chat-key CHAT_ID_AIPLAYGROUND
```

| 인자 | 동작 |
|---|---|
| (없음) | 엑셀만 만든다. 캡션을 표준출력에 찍는다 |
| `--send` | 엑셀을 만들고 텔레그램 `sendDocument` 로 보낸다. 매일 엑셀은 같은 D·basis 를 이미 보냈으면(발송 장부) 엑셀도 만들지 않고 건너뛴다(rc 0) |
| `--send --resend` | (매일만) 이미 보낸 D·basis 를 정정으로 다시 보낸다 — 캡션 끝에 `정정 n · 판 <model 판 id> · 생성 <model 생성 시각>` (N-25 Q9). 이행 기간(evening 이력 1개월 미만)에 장 마감 판을 손으로 다시 보낼 때는 `--trend-fallback-root data/model` 을 같이 줘야 체인 발송본과 같다(T-50) |
| `--dry-run` | 엑셀을 만들고, 네트워크 없이 env 파일의 `BOT_TOKEN`·채팅 키 **존재만** 확인한다(`--send` 와 함께 줘도 보내지 않는다) |
| `--equity-root PATH` · `--stage-root PATH` | (매일만) 새 시트 여섯(§4-1)의 원천 루트. 없으면 fi 판 JSON(`factor_inputs/_runs/<D>_<basis>.json`)의 `equity_root` · `stage_root`. 로컬 사본 대조 · 리허설용 |
| `--trend-fallback-root PATH` | (매일만) 장 마감 판(`--basis evening`)의 1M 비교 판이 그 루트 evening 이력에 없거나 주 모델 순위를 못 읽으면 PATH(연구 루트 `data/model`)의 아침 확정판 중 같은 규칙 판을 대신 쓴다(T-50, §6). 없으면 대체하지 않는다. 아침 판은 받아도 무시. 장 마감 체인 ⑤ 가 `data/model` 을 넘긴다 |

| rc | 뜻 |
|---|---|
| 0 | 성공(매일 엑셀을 이미 보내 장부로 건너뜀 포함) |
| 1 | 발송 실패(텔레그램 `ok=false`·예외) · dry-run 에서 비밀 키 없음(파일은 있으나 `BOT_TOKEN`·채팅 키 줄이 없음) |
| 2 | 입력·인자 오류 — 그날 판 없음 · 실패 판(status ≠ ok) · 점수/fi 파일 없음 · 날짜·주 형식 · 발송 장부 줄 손상·읽기 실패(권한·인코딩 — 보냈는지 몰라 보내지 않음) · `--send` 없는 `--resend` · `--send`·`--dry-run` 인데 비밀 파일 미설정·없음(`--env-file`·`QL_ENV`, 설정 오류 — RG-C7-4) |
| 3 | 그 밖 예상 밖 예외 — 엑셀 생성 실패 등(E-13). 트레이스백과 인자를 stderr 에 남긴다. 발송 성공 뒤 장부 기록 실패(B-58)면 메시지에 '발송은 됐다 — 장부 기록 실패'(장부에 없어 다음 실행이 다시 보낼 수 있다). 1(발송 실패)과 섞지 않는다 — 재시도 래퍼가 rc 1 을 '다시 보내기'로 다뤄도 이전 실행의 엑셀을 보내지 않게 |

- 발송 장부(매일만, N-25 Q9 · 로드맵 K0-1): `<out-root>/sent_model_daily.jsonl`(기본 `data/deliver/`), 실제 발송이
  **성공한 뒤에만** 한 줄 — `date`·`basis`·`build_id`(model 판)·`sha256`(보낸 엑셀)·`sent_utc`·`correction`(0 = 첫 발송).
  `--out-root` 를 따르므로 임시 루트 리허설은 운영 장부를 건드리지 않는다.
- `scripts/model_daily.sh` 는 빌드 락(`/tmp/quant_ledger_build.lock`, stage·equity 공용) 안에서 돈다(동시 실행
  직렬화 — 같은 D 이중 발송·모델 단계 중 equity 재빌드를 막는다). `python -m deliver … --send` 를 직접 부르면
  락이 없으니 손 발송은 `model_daily.sh` 로 한다. 손 실행은 08:10 체인~확정판 종료(보통 10:00 전),
  21:20~잠정판 종료(보통 23:10 전)를 피한다 — 그동안 빌드 락을 쥐면 그 시각의 build_chain 이 `flock -n` 으로
  건너뛰어 판이 생기지 않는다.
- **원천 전환 뒤(컷오버 PR-9 · T-7)**: `config/postclose_chain.env` 의 `POSTCLOSE_ENABLED=1` 그리고 `POSTCLOSE_SEND=1`
  이면 전환 뒤다. 판정은 `scripts/postclose_conf.sh` 한 곳이고, 그 밖(그림자 `SEND=0` 등)은 지금처럼 아침이 보낸다.
  - 전환 뒤에는 장 마감 체인 ⑤(15:41~, `--basis evening --out-root data/model_db/deliver`)가 D 의 엑셀을 먼저 보낸다.
  - `model_daily.sh` 는 fi → 모델 → 엑셀을 그대로 짓는다. `--send` 는 장 마감 발송 장부
    `data/model_db/deliver/sent_model_daily.jsonl` 에 그 D 의 basis=evening 줄이 **없을 때만** 붙인다(대체 발송 —
    notify warn '장 마감 판 미발송 → 아침판 대체 발송'). 줄이 있으면 짓기만 한다(info).
  - 장 마감 장부 파일이 없으면 '줄 없음'이다. 줄 판정은 deliver 의 장부 읽기(`_sent`·`LEDGER_NAME`) 그대로다.
  - 줄이 없으면 런 로그 `data/raw/daily_run.db`(읽기는 `daily.window_judge.read_runs`, mode=ro)의 그 D 장 마감 엑셀
    (`postclose_excel`) 런을 **전부** 본다(B-58 — 마지막 런만 보면 앞 런의 발송을 뒤 런이 가린다). 런의 rc·발송 표시는
    장 마감 체인이 detail 에 남긴 `rc=<rc> 발송 on|off …` 다. 런이 없거나 전부 rc 1·2 · rc 0 '발송 off'(그림자 판)면
    보내지 않은 것이라 대체 발송한다 — 발송이 성공하면 장부 줄을 쓰고, 그 쓰기가 실패하면 rc 3 이다(§2 rc 표).
  - 판정 불가면 보내지 않는다(crit '모델 단계 실패: 장 마감 발송 장부 판정 불가(rc=5)'·rc 5 — 중복 발송·무발송 어느
    쪽도 자동으로 고르지 않는다). 판정 불가는 장 마감 장부를 못 읽음 · 런 로그를 못 읽음(파일·`run` 표 없음) · 보냈을
    수 있는 런이 하나라도 있음이다. 보냈을 수 있는 런은 rc 3(발송 뒤 장부 기록 실패일 수 있다) · rc 0 '발송 on'(보냈거나
    이미 보낸 D 를 건너뛴 런이라 장부 줄이 있어야 한다 — 없으면 장부 유실) · rc·발송 표시를 모르는 런(끝 기록 없는
    `running` 등)이다. 사람이 텔레그램에서 그 D 장 마감 판 도착을 확인하고, 안 왔으면
    `scripts/model_daily.sh --date D --resend` 로 손 발송한다.
  - 받아들인 위험 — rc 1 의 모호함: `send_document` 는 업로드 뒤 응답 시간 초과도 전송 예외로 받아 rc 1(발송 실패)로
    낸다(`src/deliver/telegram.py` 전송 예외 처리). 그래서 장 마감 ⑤ 가 rc 1 인 날의 대체 발송은 중복일 수 있다.
    rc 1 을 판정 불가로 두면 진짜 발송 실패일 때마다 무발송이 되므로 대체 발송 쪽을 택했다(리뷰 결정).
  - `--resend`(손 정정 발송)는 스위치와 무관하게 보낸다.
  - ⑤ 엑셀 ok·⑥ v3 반영 실패인 날은 장부 줄이 있으므로 대체 발송하지 않는다. 그 D 의 v3 점수는 다음 날 아침 재반영
    (T-34)이 아침 모델 판으로 채운다.
  - 10:30 워치독(B-57)은 장 마감 장부 basis=evening 줄 또는 아침 장부 basis=morning 줄을 그 D 의 발송 기록으로 본다.

- 기본 루트는 `QL_HOME`(없으면 저장소 `database/`) 아래 `data/…` — factor_inputs CLI 와 같다.
- 산출 경로: 매일 `data/deliver/daily/model_scores_<YYYYMMDD>_<basis>.xlsx`, 주간
  `data/deliver/weekly/<YYYY-Www>/weekly_<YYYY-Www>.xlsx`. 임시 파일에 쓴 뒤 교체한다(원자적).
- 의존성: `openpyxl`(backend `pyproject.toml` 에 추가) · `duckdb`(parquet 읽기, 이미 쓰는 것).
  전송은 표준 라이브러리 `urllib`(curl·requests 안 씀).

## 3. 입력 계약(고정)

```
data/model/latest_<basis>.json                        마지막 성공 판
data/model/_runs/<YYYYMMDD>_<basis>.json              날짜별 판(같은 JSON) — 정본
data/model/<spec_id>/v=<build_id>/scores.parquet      점수 표(contracts.score_columns)
data/model/<spec_id>/v=<build_id>/indicators.parquet  지표 긴 표(contracts.INDICATOR_COLUMNS, v4 만)
data/factor_inputs/<fi 표>/v=<fi_build_id>/*.parquet   입력 8표 — hive_partitioning=false
data/factor_inputs/_runs/<YYYYMMDD>_<basis>.json      fi 판 manifest(equity 판 id 표기용)
<코드 루트>/DEPLOYED.json                             배포 기록(deploy.sh) — 메타 '코드 rev'(읽기만).
                                                      코드 루트 = src/ 의 부모(서버 ~/quant-ledger) — QL_HOME 아님
```

- scope·v3 주 모델은 지표 긴 표가 0행이고 원값을 점수 표 열(`contracts.V3_SCORE_COLUMNS` 원값 27 · 표식 6)로
  싣는다 — Raw · Sector 업종 지표는 그 열을 읽는다(E-10).

- 판 JSON: `{"layer":"model","status":"ok","build_id","date","basis","fi_build_id","generated_at",
  "specs":{spec_id:{"n_scores","n_ranked","n_excluded","gates":{…}}},"primary_spec":"v4_rank@0.1"}`.
- 판 선택: `_runs/<D>_<basis>.json` 이 있으면 그것, 없으면 날짜가 같은 `latest_<basis>.json`.
  `status ≠ ok` 면 rc 2. 전일 비교는 D 보다 앞선 가장 최근 성공 판(같은 basis).
- 주 모델 = `primary_spec`. 나머지 spec 은 모델 비교 열이 된다(레지스트리 등록 수만큼 는다).
- `gates` 는 dict(`{"MG0": {"status": "pass"}}` 또는 `{"MG0": "pass"}`)·list 모두 요약한다.

## 4. 매일 엑셀 — 시트별 열

확정 양식(샘플 22 · 28 · 30 — DECISIONS §7 #6·#7·#10·#11, 구현 `excel_daily.py` · `qpack.write_confirmed` ·
`sheets_extra.py`). 시트 순서 **Score · Sector · Earnings · Analyst · Events · Market · ETF · Flow · Short ·
Compare · Raw · Meta · trend(숨김)**. 지표(표시용) 시트는 10-11 사용자 결정으로 뺐다. 행은 모두 **주 모델 모집단**(점수 표 전 행): 순위 종목(순위순) 뒤에 제외 종목(코드순). 그룹(6행)에 있는
말은 열 머리(7행)에서 되풀이하지 않는다(Meta 열 사전 이름은 '그룹 · 열').

| 시트 | 열(그룹 · 열) |
|---|---|
| Score | 종목(코드 · 이름 · 시장 KS/KQ · 대분류 · 중분류 · 시총(억원) · 거래대금 20일(억원, 10억 미만만 소수 1자리) · 주가(원) — 틀고정) / **종합**(순위 · 점수 · 전일 순위 · Δ순위 1M(색 가운데 0) · 1M 흐름(원순위 꺾은선)) / 상태(제외 사유 · 커버리지 — '신선'은 옅은 회색 · 비고 짧은 표지 `애널 ≤3` · `의견 0(3M)` · `애널 미상` · `N월 결산` · `매출 순액` · `퀄리티 변동성만`, 폭 17, 뜻은 머리 칸 메모 범례) / 버킷마다 유니버스 · 업종(z 엔진 = '(Z)', 형식 0.0 · v4 = 백분위) / 결측(축) / 비교 순위(v3 원본 · v2 원본 — `v4_rank@*` 는 결함 수정 전까지 뺀다(N-27) · 최대 차이) / **후보(대분류당 최대 9)**: 후보(순위순으로 대분류마다 최대 9 까지 30종목) · 변화(전일 순위에 같은 규칙을 건 전일 후보 대비 — 진입 · 이탈 · 이탈(9종목 초과) · 9종목 초과, 전일 대분류 = 오늘 값. 전일 순위가 없으면(첫 날 · 직전 데이터 없음 · 읽기 실패) 변화 열을 모두 비우고 변화 머리 메모 · Meta '전일 비교 데이터'에 '전일 데이터 없음' + 경고 로그). 전일 순위 칸 노랑 = 진입(조건부 서식) |
| Sector | 위 표 대분류(WICS 10) · 빈 줄 · 아래 표 업종(WICS 중분류, 머리 두 줄을 다시 쓴다 · 자동 필터): 업종(구분 · 코드 · 업종명 · 대분류) / 규모(종목 수 · 순위 종목 수 · 시총 합 · 시총 비중) / 쏠림(상위 30 비중 · 상위 100 비중 · 후보 수 — 위 표는 `n/9`) / 종합(점수 중앙값) / 축별 평균(Z)(유니버스 z 업종 평균 · v4 = 백분위 평균) / 업종 지표(PER 중앙값 · 리비전 상향 비율 · 1W · 1M · 3M 수익률 시총가중 — 1W = 종목 5세션 수익률(엔진 `_price_histories` · `_period_return` 를 그대로 부른다 — 주 모델이 `adj_jump_missing` 을 켰으면(scope) 엔진 r1m 처럼 창 6행 안에서 `adj_jump_ok` 계단이 바뀌는 종목은 뺀다, 수정종가 표가 없으면 종가 + 경고 로그), 1M = r1m, 3M = r3m, v4 는 EP · REV_OP_1M · R1M · R3M) / 상위 3(순위). 색: 축 평균 대분류 ±0.5 · 업종 ±1, 업종 표 5종목 미만은 회색, 수익률은 표마다 백분위 |
| Earnings | 종목 / 매출 · 영업이익 · 순이익(억원 · YoY %) × (Y−1 · YE · (Y+1)E) × (값 · YoY) / 분기 영업이익(최근 달력 분기 5칸 `YY.nQ` · YoY(최근 분기)). YoY 머리 = 비교하는 두 해(`YoY (2024-2025)` …, Y = 데이터 날짜의 연도라 해가 바뀌면 넘어간다). YoY 색 = 가운데 0, 양 끝 = 음수 · 양수 \|값\| 90% 분위(유효숫자 2자리, 날마다). 흑자전환 · 흑전 파랑 · 적자 쪽 빨강 글자. 세로선 3단(블록 경계 medium 595959 · 연도 묶음 사이 · 분기 YoY 앞 A6A6A6 · 묶음 안 D9D9D9). Y−1 확정치가 없으면 추정치를 회색 'E'(옆 YoY 도 회색, N-25 Q10). 순액 매출 대 총액 비교 YoY 는 비우고 Score 비고 `매출 순액`(4-2b #1). 비12월 결산은 결산기가 끝나는 연도 칸(N-19) |
| Compare | 종목 / 순위(주 모델 먼저 — 핵심 색, 순위 색 반전, `v4_rank@*` 는 뺀다) / 상위 30 포함(●) / 차이(최대 차이). A4 세로 인쇄 |
| Raw | 종목(코드 · 이름 · 대분류) / 버킷마다 role=score 지표 × (원값 · 엔진 백분위 · 표식) — 주 모델 엔진이 v3_zscore(scope·v3)면 대신 점수 표 원값 27열 + 표식 6열(모멘텀 · 수익률(비율) · 리비전 영업이익 · 순이익 추정 변화(비율) — 두 블록 · 수급 · 순매수/시총 · 퀄리티 · 밸류, 엔진 값 그대로 · 비율은 0.0000, 점수 표에 없는 열은 그 칸만 빈칸 + 경고 로그 한 줄 — E-10) / 데이터 기준(연간 기준기 · 재무 접수일 · 분기 기준 · 추정 관측일 · 애널리스트 수 · 수급 최신일). 열 정의 = 7행 머리 칸 메모. 표식 흑자 파랑 · 적자 빨강. 인쇄 2쪽 너비 · A:C 반복 |
| Meta | 위: 기준일 · basis · 데이터 성격(장 마감 데이터 evening 만 — `common.BASIS_NOTES`, PR-6) · 주/비교 모델 · 비교에서 뺀 모델(N-27) · model 데이터 id · model 생성 시각 · 전일 비교 데이터 · 1M 비교 데이터(아침 확정 데이터로 메운 장 마감 데이터는 값 뒤에 `(아침 확정 데이터 — 장 마감 데이터 비교 데이터 없음, T-50)`) · 순위 흐름 날짜 · fi · equity 데이터 id · 엑셀 생성 시각 · 코드 rev(E-08) · 새 시트 데이터 id(§4-1) · 유니버스 규칙 · 종목 수 · 제외 사유별 · 축별 결측 수 · Raw 단위(scope·v3) · 데이터 기준일 · 모델 버전 · 가중치 · 제외 게이트 · 후보 규칙(커트라인 · 9종목이 찬 대분류) · spec 별 데이터 검사 · 각주 5. 아래: **열 사전**(Score · Raw · Earnings · Sector · Compare 순, 끝에 Score 후보 · 변화 두 줄, 그 뒤 새 시트 — ETF · Analyst · Events · Market · Flow · Short). 끼워 넣는 줄은 키 이름 뒤에 둔다(E-09) |

### 4-1. 새 시트 여섯(Analyst · Events · Market · ETF · Flow · Short)

원천은 equity · stage 층(`sources.py` — fi 판 JSON 의 `equity_builds` 에 있는 표는 그 판, 없으면 그날
current_build, stage 는 current_build. 읽은 판 id 는 Meta '새 시트 데이터 id'). 시트마다 기준일 = 그
시트 원천의 최신 날짜(≤ D — 사용자 결정 Q-E2, 장 마감 엑셀은 대개 D−1)이고 4행에 적는다(D 와 다르면
`기준일: <원천 날짜>(원천 최신 · 엑셀 MM-DD)`). 창 = KRX 지수 거래일로 1 · 5 · 20 · 60거래일 전 · 전년 마지막
거래일. 원천이 없거나 비면 그 시트만 `데이터 없음: 사유` 한 줄(경고 로그) — 엑셀은 그대로 나간다. 종목 표의
유니버스 = Score 행(종합 순위 · 대분류 · 시총 · Raw 모멘텀 1M).

| 시트 | 원천 · 내용 |
|---|---|
| Analyst | equity `opinion_broker_daily`(같은 날 같은 증권사 1건, 나중 수집이 이긴다) · `opinion_daily` · `price_daily`. 대분류별 1W · 1M 상향 · 하향 · 신규 · 상향 비율, 그래프 4개(일별 건수 · 5일 상향 비율 — 기준일 전날까지 · 대분류 1M 상향 비율 · 여력 중앙값), 1W 상향 · 하향 상위 20, 여력 상위 20(증권사 3곳 이상), 의견 변경(1M) · 신규 커버(1W) |
| Events | stage `stg_disclosure`(제목 NFKC 'ᆞ' → 원문 'ㆍ') · `stg_corp_map` · `stg_event_*`, equity `holder_daily`. 유형별 공시 건수(제목 낱말 규칙, 같은 날 같은 공시의 정정 · 첨부는 1건), 1W 주요 공시, 임원 보유 순변동(10% 주요주주 보고 뺌), 5% 대량보유(지분 변화 있는 것 큰 순 30), 자사주 · 증자 · 사채 결정(금액 · 날짜는 DART 원문 모양) |
| Market | equity `index_daily` · `price_daily` · `universe_daily` · `flow_daily`(키움, 보통주) · 수정종가(`equity.views` 의 `v_adj_price` 템플릿을 판 고정 원천에 올린다). 대표 · 업종 지수 수익률, 그래프 4개(코스피 · 코스닥 · 외국인 누적 순매수 · 거래대금 20일), 투자자별 순매수(국가 행 뺌), 시장 폭(상승 · 하락 · 보합 · 52주 신고 · 신저 — 250거래일 · 이력 200거래일 이상) |
| ETF | stage `stg_etf_price_daily`(KRX ETF 일별 2010-01~) · equity `index_daily`(거래대금 비중 분모). 유형 = `etf_classify.py` 이름 · 기초지수 규칙(시험). 자금 유입 = 좌수 변화 × NAV(분할 · 병합 보정, 시계열은 신규 상장 유입 · 폐지 유출), 요약(12주 셀 안 막대) · 그래프 4개 · 주간 26주 · 월별 12개월 · 연도별 · 운용사 · 1M 유입 · 유출 상위 15 · 전체 목록. 계산 `etf_data.py`(DuckDB, 합은 코드 · 날짜 순 왼쪽부터 — 그래프 값 사본이 비트 단위로 같게) |
| Flow | equity `flow_daily`(키움). 대분류별 투자자 순매수 1W · 1M, 외국인 · 기관계 · 연기금 · 개인 1M 상위 · 하위 15, 외국인 지분율 1M 변화 |
| Short | equity `short_daily`(키움 공매도 · 대차) · `price_daily` · `credit_daily`(KIS, 원천이 2거래일쯤 늦다 — 5행에 그 날짜). 시장 추이 26주 · 그래프 4개, 공매도 비중 · 대차 ÷ 시총 · 신용잔고율 상위 20 |

표시: 1~5행 띠 · 6·7행 머리 · 본문 서식은 Sector 시트 칸에서 옮긴다(`qpack.XSheet`), 머리 색은 표마다 마지막
데이터 열까지, 코드 없는 표는 A · B 합침, 색은 백분위 5 · 0 · 백분위 95 그라데이션, 틀 고정 C6 · 인쇄 반복 2~5행.
그래프는 Q.Pack 문법(`qpack.date_frame` · `cat_frame`) — 원자료는 숨긴 열이라 '숨긴 데이터 표시'를 켜고, 저장 뒤
`qpack.finish_charts` 가 값 사본(cache) · 날짜 축 `auto` · 제목 자동 생성 끔을 넣는다. ETF 12주 막대는
`qpack.add_column_sparklines`(displayHidden).

Score 시트 축 칸은 **엔진 단위를 따른다**(10-08 사용자 결정 '둘 다 z'). 판정은 `DayView.z_engine`
(주 모델 엔진 = `v3_zscore`) 한 곳이다.
- z 엔진(scope@1.0 · v3_zscore@1.0): 유니버스 z = 엔진 버킷 점수(`<버킷>_score` — 종합 점수에 들어간
  값, ±3 상한) 그대로, 다시 순위 매기지 않는다. 업종 z = 같은 z 를 WICS 대분류 안에서 다시 z(엔진
  `z_score_winsorized` — 대분류 평균·표본 표준편차, ±3σ 로 자른 뒤 표준화, ±3 안). 값 있는 종목 < 5
  이거나 표준편차 0 이면 유니버스 z 로 되돌린다. 업종 z 는 순위·점수에 쓰이지 않는 참고값. 형식
  `#,##0.00`, 색 = 고정 3색 −3 · 0 · +3(초록 = 높음 = 좋음).
- 백분위 엔진(v4_rank — 버킷 점수 자체가 0~100 백분위): 지금처럼 버킷 점수를 한 번 더 순위 매긴
  **표시값**. 유니버스 = 그 버킷 점수가 있는 전 종목 안, 업종 = WICS 대분류 안(값 있는 종목 < 5 면
  유니버스로 되돌린다 — 엔진 `min_sector_size` 와 같다). 공식은 엔진 `pct_rank_avg`(동률 평균순위).
- 결측 표기('결측(사유)')·결측 축·메타 결측 수 규칙은 두 경우 같다.

## 5. 주간 엑셀 — 시트별 열

재료 = 그 ISO 주 월~금의 **아침 확정판**. 기준일 = 판이 있는 마지막 거래일(보통 금요일).
**판이 없는 날(휴장·미빌드)은 ✕, 평균 순위는 순위가 있는 날만**(사용자 09-25).
제목 3행 '비교' 목록은 일간과 같은 규칙이라 `v4_rank@*` 가 빠진다(N-27 — 주간 시트는 원래 v4 비교 열이 없다).
주간 엑셀의 축 칸(주간 후보 6축 · 팩터 카드)은 z 엔진 주 모델이어도 아직 백분위다(일간만 z — 후속).
후보 = 기준일 주 모델 순위순으로 대분류당 최대 9, 30 종목(`OutputRule`). 지난주 = 직전 ISO 주의
기준일 판(같은 주 모델).

| 시트 | 열 |
|---|---|
| 주간 후보 | 후보(순번 · 코드 · 이름 · 시장 · 대분류 · 중분류 · 시총) / **순위**(금요일 순위(원본·상한 전) · 주간 평균 순위 · 월~금 ●○✕ 5칸 · 등장(●) · 지난주 순위 · 신규/유지(신규 = 형광) · v3 순위(참고)) / 점수(종합 점수 · 6축 유니버스 백분위) |
| 팩터 카드 | 후보마다 세로 블록: 제목 띠 → 헤더(항목 · 구분 · 값 · 유니버스 백분위 · 업종 백분위 · 업종 중앙값 · 대비 · 표식·결측) → 축 6행(엔진 버킷 점수) → 점수 지표 12행(원값 · 엔진 백분위 — 대분류 기준 버킷은 업종 칸) → 최근 5일 순위 → 기준일(재무 · 추정 · 수급 · 커버리지) |
| 주간 추이 | 이번 주 후보 + 지난주 후보(이탈 포함): 종목 · 구분(유지/신규/이탈) / 월~금 순위(✕ = 판 없음, 제외 = 그날 순위 없음) / 월~금 종합 점수 |
| 이탈·진입 | 방향(진입 형광 · 이탈) · 종목 / 지난주 순위 · 이번주 순위 · Δ순위 · 이번주 상태(제외 사유 · 순위 밖 · 모집단 밖) / 가장 크게 변한 축 · 변화량(버킷 점수 차) |
| 지난주 성적 | 지난주 후보 30: 순번 · 종목 · 지난주 순위 / 1주 수익률(지난주 기준일 → 이번 주 기준일 수정종가) · 유니버스 평균(지난주 순위 종목 전체) · 초과 · 승(●). 5행 = 평균·중앙값·유니버스·초과·승률 요약. 두 날 `adj_ok` 가 다르면 '결측(수정주가미해결)' |
| 메타 | 주 · 기준일 · 요일별 판 id(없으면 '판 없음 — ✕') · 지난주 기준일 · 유니버스 · 후보 규칙 · 가중치 · 결측 요약 · 지난주 성적 · 표식 규칙 · 열 사전 |

## 6. 표시 규칙(규격 C·D·E 구현)

- 매일 엑셀 = 확정 양식(`qpack.write_confirmed`): 1행 A1 '정렬: …' · 정렬 열 위에만 ▲ · 2행 'Sheet: 설명'
  (4F81BD) · 3·4행 연한 파랑 DCE6F1 + 진남색 글자(주 모델 · 비교 / 기준일 + `아침 확정 데이터` · `장 마감
  데이터` · 출처 — 틀 고정 폭을 넘으면 ' · ' 뒷부분을 첫 스크롤 열로) · 5행 읽는 법 + 블록 첫 열 위 설명 ·
  6행 그룹 595959 · 7행 열 7F7F7F · 핵심 블록 8064A2(Score 종합 · Compare 주 모델 순위 · Sector 점수 중앙값) ·
  칠이 같은 여러 칸 그룹 이름은 가운데 맞춤 · 블록 첫 열 왼쪽 · 표 오른쪽 끝 medium 595959, 블록 안 thin
  D9D9D9, 표 끝 선 = 마지막 행 다음 행 위쪽(정렬해도 남는다) · 머리 칸 메모(줄 300px · 기호는 앞뒤 낱말과
  붙임 · 상자 크기 = 줄 수 · 글 폭) · 인쇄 A4 가로 1쪽 너비 · 2~7행 반복 · 바닥글 '&A' · '&P / &N'. 글에
  '판'을 쓰지 않는다(읽는 사람이 모른다 — 데이터 · 날짜 · 신규 편입), '상한'은 '대분류당 최대 9종목'.
  주간 엑셀은 아래 Q.Pack 서식 그대로.
- Q.Pack 서식(주간 엑셀): 격자선 끔 · 맑은 고딕 8 · 기본 행 높이 11.25 · 1행 정렬 마커(BDD7EE) · 2~4행 제목 띠
  (4F81BD, 흰 글씨: 시트명 / 모델·버전 / 기준일·Source·판 id) · 5행 각주 ·
  6~7행 2단 헤더(그룹 파랑 · 식별 그룹 회색 · 종합/순위 그룹 금색 FFC000 · 핵심 열 보라 7030A0 · 열
  이름 회색 7F7F7F · 얇은 테두리 상자 · 7행 높이 33.75) · 8행부터 데이터 · 그룹 사이 빈 구분 열(너비 5 ·
  숨김 · 아웃라인 1) · 숫자 열 너비 13 · 식별 열 끝에서 틀고정 · 7행 자동필터 · 병합 없음.
- 색 스케일: 3색 백분위 10/50/90, 빨강 F8696B(낮음) → 노랑 FFEB84 → 초록 63BE7B(높음 = 좋음, N-15).
  백분위 · 변화(y-y · 수익률 · 리비전) 열에만. 순위 열은 반전(1위 = 초록) — 단 Score 시트의 순위 열은
  규격대로 무색. Δ순위 1M 은 가운데 = 0(아래 줄). z 엔진 주 모델의 축 z 칸(Score 시트 · Sector 시트
  축별 평균 z)은 고정 3색 −3 · 0 · +3. 레벨 값 무색. 형광 노랑은 '신규 진입' 한 종류.
- 결측 = `결측(사유)` 문자열(사유 = 엔진 flag 첫 항목 · 버킷은 `버킷결측(NN%)` 우선). 플래그 ≠ 결측.
  예외: scope·v3 Raw 의 원값 빈칸 = 엔진 값 없음(v3 엔진은 사유를 남기지 않는다).
  점수 대상(빈 버킷을 결측으로 적고 결측 축 · 메타 결측 수에 세는 종목) = 지표 행이 있거나 점수 행에 종합
  점수가 있는 종목 — scope·v3 는 지표 긴 표가 0행이라 빈 버킷이 `결측(원천없음)`(E-02, N-26 4.5).
- Δ순위 1M 칸 색은 가운데 = 0(하락 빨강 · 상승 초록). 끝점 ±|Δ| 90 백분위는 구현 선택이다(Q8 은
  가운데 0 만 정했다). 1M 흐름 꺾은선은 원순위
  (숨김 'trend' 시트에 −순위로 싣는다 — 위 = 상승)라 선의 처음 → 끝 방향 · 선 색(Δ 부호) · Δ 숫자가
  늘 같은 쪽이다. 10-07 까지는 백분위 선 · 백분위 50 가운데라 유니버스가 커진 날 셋이 어긋났다(E-06, N-25 Q8).
- 장 마감 판(evening)의 전일 순위 · 1M 비교 판 · 흐름은 evening 판끼리 읽는다(PR-6). 단 1M 비교 판(D−1개월 이하
  마지막 성공 판)이 evening 이력에 없거나 그 판에서 주 모델 순위를 못 읽으면, `--trend-fallback-root`(장 마감 체인 =
  연구 루트 `data/model`)의 아침 확정판 중 같은 규칙 판으로 Δ순위 1M 을 채우고 흐름 첫 점도 그 판으로 둔다(T-50 —
  선 처음 → 끝 = Δ 유지). 메타 '1M 비교 판'에 표시가 붙는다. evening 이력이 D−1개월까지 닿는 날(약 11-14)부터는
  저절로 evening 판만 쓴다. 전일 순위와 아침 판 엑셀은 바뀌지 않는다. 대체 루트를 읽지 못하면(manifest ·
  점수 parquet 깨짐 등) 대체 없이 빈칸 + 경고 로그 한 줄이고 엑셀은 멈추지 않는다(보조 입력 — P1). 대체를
  시도했는데 못 쓴 모든 경우(판 없음 · 주 모델 없음 · 점수 표 없음 · 읽기 실패)에 경고 한 줄(대체 루트 · 기준일
  D−1개월 · 사유)을 남긴다 — evening 1M 판이 없는 이행 기간에만 나온다. manifest 가 객체가 아닌 JSON 같은
  프로그래밍 오류는 삼키지 않는다.
- 실적 y-y: 부호가 바뀌면 증가율 대신 같은 y-y 칸에 글자 — 흑자전환(−→+ · 0→+) · 적자전환(+→− · 0→−) · 적자지속(−→−). 흑자지속은 증가율 숫자. 표식 열은 없다(N-19).
- 리비전 변화(엔진 REV_*): 흑전 · 적확 · 적축(이전값 < 0)은 값 대신 표식만, 적전은 값 + 표식.
  선행 EPS 1M 변화도 같은 규약.
- E/P 음수는 값 그대로 + '적자' 표식. 선행 PER 은 당해 컨센서스 순이익 < 0 이면 '적자'. 배당수익률
  무배당은 0(엔진 '무배당' 표식 병기).
- 윈저라이즈 1/99(선형 보간 분위수)는 표시 전용 도우미(`common.winsorize`) — 순위·점수에는 쓰지 않는다
  (지표(표시용) 시트를 뺀 뒤 매일 엑셀은 쓰지 않는다).
- 비율 지표는 ×100 해 % 로 보인다(라벨에 `(%)`). 고점근접+반전(M_PULL_C)은 0~100.

## 7. 텔레그램

- `send_document(path, caption, *, env_file=None, chat_key="CHAT_ID_AIPLAYGROUND", transport=None,
  dry_run=False) → {"ok", "description"}`.
- 비밀: env 파일 = 인자 → `QL_ENV`(둘 다 없거나 가리킨 파일이 없으면 `env_path` 가 FileNotFoundError, CLI 는 rc 2 — 다른 파일로 대신하지 않는다, RG-C7-4. 운영은 서버 `$HOME/quant-ledger/.env` — README "운영 (P6) → 비밀 파일"). 그 안에서
  `BOT_TOKEN` 과 채팅 키 두 줄만 읽는다(`export `·따옴표 허용). **토큰·채팅 ID 는 반환값·로그·예외
  문자열에 싣지 않는다** — 전송 예외·API 오류 문자열은 두 값을 `***` 로 가린다. 로그에는 키 이름만.
- 캡션(1,024자 절단):

```
[모델 점수] 2026-09-29 morning · v4_rank@0.1
상위5: 1.종목A · 2.종목B · 3.종목C · 4.종목D · 5.종목E
순위 317 · 제외 286 · 모집단 603

[모델 점수] 2026-10-14 evening · scope@1.0          ← 장 마감 직후 판(PR-6)만 둘째 줄이 붙는다
장 마감 직후 판(가격 15:35·수급 15:40 정규장 기준) · 컨센서스 기준일 = 직전 거래일
상위5: 1.종목A · …

[주간 후보] 2026-W40 (기준 2026-10-02) · v4_rank@0.1
상위5: 1.종목A · …
후보 30 · 신규 5 · 이탈 5 · 거래일 판 5/5
```

## 8. 규격 대비 미구현(원천 없음 또는 계약 밖)

| 규격 항목 | 상태 · 사유 |
|---|---|
| 지표(표시용 — 10-11 시트를 뺐다): 주가 변화 1D·1W·12M·YTD · 12-7 · 6-1 · FIP · 120일 변동성 · 60일 회전율 · 수급 5/20/60일(기관·연기금·외국인) | 레지스트리 display 지표가 아니다. 옛 시트는 레지스트리 `role=display` 가 열을 정하므로 엔진에 계산식 + `config/models/*.toml` 에 display 지표로 더하면 자동으로 열이 생긴다(전달층이 따로 계산하지 않는다 — 정의 중복 방지) |
| 지표(표시용): 공매도 거래비중 · 대차잔고 · 투자의견 평균 · 외국인 지분율 · SUE · 서프라이즈 % · 발표일 · 선행 EV/EBITDA | factor_inputs 8표에 원천 열이 없다 |
| 실적: 분기 8개(미발표는 컨센) · 어닝시즌(예정일 · 잠정치 · 컨센 · 서프라이즈) | fi_fin_summary 분기는 5기만(계약 창), 분기 컨센서스 · 잠정실적 원천 없음 → 최근 5기 · Q0 y-y · 공시일만 |
| 메타: WICS 스냅샷 날짜 | fi_universe 에 스냅샷일 열이 없다(equity 판 id 로 대신) |
| 주간 후보: 확신 표식(SUE × ΔP/E) | SUE 원천 없음(메타에 '미구현' 명시) |
| Sector 시트 '업종 리비전' | v4 = REV_OP_1M > 0 비율, scope·v3 = 점수 표 op_change_1m > 0 비율(E-10). 둘 다 없는 spec 이면 빈칸 |
| Score 시트 비고 — 외화 재무(G-27, N-25 Q6) | 원인 없는 표기만 구현: scope 점수 행의 퀄리티 손익 지표(qual_gpa · qual_roa · qual_fcf_assets · qual_debt_ratio · qual_gpa_change)가 다 비고 qual_std_20d 만 있으면 비고 '퀄리티 변동성만'. '외화 재무' 같은 원인은 적지 않는다 — fi 8표에 통화 열이 없고 equity fin_std 는 원화가 아닌 재무를 격리(`_reject/reject_reason=non_krw`)해 본 표 `currency` 가 늘 KRW 이며, 477850·0011T0 처럼 다른 이유로 비는 종목도 있다. 원인 표기·원통화 비율은 fi·equity 계약 변경 뒤 |

## 9. 테스트

- `tests/test_deliver_excel.py` — 합성 model 판 · fi 판을 고정 경로에 parquet 로 써서(월~금 중 수 휴장,
  지난주 목·금) 매일·주간 엑셀을 만들고 openpyxl 로 다시 연다: 시트·헤더·행 수, 값만(`<f>` 없음,
  `=` 문자열 보존), 업종 상한 9, ●○✕(휴장 포함) · 평균 순위, 부호 전환 표식, 윈저라이즈, 서식(글꼴 ·
  채움 · 틀고정 · 자동필터 · 숨긴 구분 열 · 색 스케일 규칙), CLI dry-run·발송(가짜 전송)·rc 2.
  마지막 테스트는 **실제 `model.build`** 가 40종목 합성 입력으로 쓴 판(네 spec)을 그대로 읽어 파일 계약을
  대조한다.
- `tests/test_deliver_telegram.py` — 가짜 전송만: 성공 · 예외/API 오류 가림 · 비밀 없음 · dry-run ·
  env 해석 · 캡션 형식 · multipart 모양. 토큰·채팅 ID 가 반환값·로그에 없음을 매번 확인한다.

```bash
uv run --project backend pytest database/tests/test_deliver_excel.py database/tests/test_deliver_telegram.py -q
```

## 10. 서버 운영

서버 venv 는 맨 pip 이고 배포(`scripts/deploy.sh`)는 패키지를 설치하지 않는다 — **처음 한 번**
`~/quant-ledger/.venv/bin/pip install 'openpyxl>=3.1.5'` 가 필요하다.

```bash
cd ~/quant-ledger
export QL_ENV="$HOME/quant-ledger/.env"   # 비밀 파일(RG-C7-4) — 없으면 rc 2
# dry-run(엑셀 생성 + 비밀 키 존재 확인, 발송 없음)
QL_HOME=$PWD PYTHONPATH=src .venv/bin/python -m deliver model-daily --date 20260929 --basis morning --dry-run
QL_HOME=$PWD PYTHONPATH=src .venv/bin/python -m deliver model-weekly --week 2026-W40 --dry-run
# 실제 발송(CHAT_ID_AIPLAYGROUND)
QL_HOME=$PWD PYTHONPATH=src .venv/bin/python -m deliver model-daily --date 20260929 --basis morning --send
QL_HOME=$PWD PYTHONPATH=src .venv/bin/python -m deliver model-weekly --week 2026-W40 --send
```

D-12: 매일 v4 점수는 파일 저장이 기본이고 발송은 스위치(`--send`)다. 주간은 토요일 아침 금요일
확정판 기준. 매일 엑셀은 08:10 `daily_build.sh` 가 확정판(rc 0·1) 뒤 `scripts/model_daily.sh --date D` 로
fi → 모델 → `--send` 를 잇는다(N-25 Q0, 임시 — N-13 전까지). 손으로 다시 돌릴 때도 같은 명령이고, 정정
발송은 `--resend` 를 붙인다. 원천 전환 뒤(PR-9)에는 아침판은 짓기만 하고 장 마감 판이 그 D 를 못 보낸 날만 대체
발송한다(§2 발송 장부 아래). 주간 발송은 체인에 없다.
