# factor_inputs — 모델 전용 입력 층

> 플랜 [`2026-09-24-v3-merge.md`](plans/2026-09-24-v3-merge.md) M2 W1-b(T2.2b) · T2.11(DQ-7) ·
> 유예 규칙 [`2026-09-26-layer-fixes.md`](plans/2026-09-26-layer-fixes.md) §3. 계약은
> `src/model/contracts.py` 의 `FI_TABLES`(8표)가 정본이고 이 문서는 그 표를 **어떻게 굽는지**를 적는다.

## 1. 목적

- 엔진(`src/model/engines/`)과 호환 계층(compat, M2 W2 T2.7 부터)은 **이 층만 읽는다**(결정 D-9).
  두 소비자가 같은 재료를 보게 하고, 창 자르기·시총·신선도 판정을 한 곳에만 둔다.
- 새 계산은 셋뿐이다 — ① 창 자르기 ② 시총(주식수 × 종가) ③ 추정치 신선도 유예(T2.11).
  나머지는 equity/stage 값을 계약 단위로 옮긴다.
- **단위·정수화는 compat(v3 미러)와 같다.** 그래야 이식 엔진(이 층) = v3 엔진 원본(compat) 이
  |Δ| ≤ 1e-9 로 맞는다(G-M3 ①). 대응의 참조는 v3 이식 갈래의 어댑터
  `tests/tools/compat_to_fi.py` 다.
- basis 는 둘이다 — 아침 확정판(`morning`)과 장 마감 판(`evening`, 컷오버 T-2 · fi1.5.0). 장 마감
  판은 직전 거래일 아침 확정판을 날짜로 고정해 읽고 오늘 T 의 자리를 만든다(§2-1). T 행(가격·
  수정주가·수급)은 15:41 장 마감 직후 수집에서 얹는다(§2-2, 컷오버 PR-5 · fi1.7.0).

## 2. 실행

```bash
python -m factor_inputs build --date 20260929 --basis morning \
    [--root data/factor_inputs] [--stage-root data/stage] [--equity-root data/equity] \
    [--grace-days 5] [--min-eligible 300] [--keep 60]
```

| rc | 뜻 |
|---|---|
| 0 | 8표 판 커밋 · `latest_morning.json` 갱신 |
| 1 | 게이트 FAIL — 판을 올리지 않는다(`_failed/<build_id>.json`, 포인터·latest 는 직전 성공 판 유지) |
| 2 | 입력·인자 오류(원천 판 없음 · D 가 거래일 아님 · 아침판에 저녁 equity 판 · 가격 판 체인 불일치 · 장 마감 판 조건 §2-1) 또는 예외 |

- 기본 루트는 `QL_HOME`(없으면 저장소 `database/`) 아래 `data/…` — equity CLI 와 같다.
- **언제 돌리나**: 아침 체인(equity `m_` 판)이 끝난 뒤, 저녁 체인(`e_` 판) 전. 아침판 가드가 `e_`
  equity 판을 거절한다. D 는 확정판의 거래일(예: 09-30 아침 체인 → `--date 20260929`).
- 과거 날짜 검증(예: GC2 의 09-23 재현)은 **별도 `--root`** 로 돌린다 — `latest_morning.json` 은
  날짜와 무관하게 마지막 성공 판을 가리키므로 운영 루트에서 옛 날짜를 돌리면 덮인다.
- **재생 `--replay`**(컷오버 PR-8b — `scripts/replay.sh --basis evening` 만 쓴다): 과거 D 의 판을
  현판(최근 세션까지 온 equity)에서 짓는다. 정보 입력은 SQL 이 이미 asof 로 자르지만 세션 축은 자르지
  않아, 현판으로는 장 마감 판 MD-SEAM(§2-1)이 서지 않고 아침판은 달력 뒤 세션이 filing_late 실효 기한을
  정한다. `--replay` 는 equity 세션 축 7표(`build.REPLAY_SESSION_TABLES` — trading_calendar · universe_daily ·
  price_daily · price_adj_daily · flow_daily · credit_daily · coverage_daily)의 뷰만 `date <= asof`(아침판 D ·
  장 마감 판 D')로 자르고 판 manifest 에 `replay`(자른 날·표)를 남긴다. `queries` SQL·게이트·진입 조건은
  그대로이고, 재생이 아니면 뷰 SQL 도 글자 그대로다(`tests/test_factor_inputs_replay.py`). 마스터
  (security·corp)와 equity 재계산은 현판 그대로라 그날 판과 다를 수 있다.
  **재생은 다음 차이를 볼 수 없다.** 재생의 두 판(장 마감 판·연구 판)이 같은 equity 현판과 같은 21:05 원장
  수급을 읽어, 실운영에서 두 판을 가르는 아래 차이가 재생 대조에서는 구조적으로 0 이다 — 그림자 3거래일
  (+ 컷오버 뒤 기록형) 몫이다.
  - equity 소급 재판정이 낳는 T 전 행 차이 — adj_factor 기준가 창이 asof 뒤 세션을 본다 · universe_daily
    corp_action_window 가 45세션 앞을 본다 · equity 규칙 변경. 실운영 장 마감 판은 D' 확정판 equity 를, 연구
    판 T 는 다음 날 확정판 equity 를 읽어 갈릴 수 있다.
  - 정규장 수급 대 21:05 원장 수급 차이 — 재생 T 행 수급과 연구 판 수급이 같은 21:05 키움 원장에서 온다.
    실운영 장 마감 판 T 행 수급은 15:41 정규장 수집이다.

### 2-1. 장 마감 판(`--basis evening`, 컷오버 PR-4)

```bash
python -m factor_inputs build --date <T> --basis evening --root data/model_db/factor_inputs \
    --builds-from data/deliver/history/<D'>_morning.json [--calendar-dir data/calendar] \
    [--postclose-stage-root data/model_db/stage] [--candidates-root data/factor_inputs]
```

- `--date` 는 오늘 T. `--builds-from`(직전 거래일 D' 아침 확정판 인계 이력)이 없으면 rc 2.
- **`--root data/model_db/factor_inputs` 필수(T-3).** 연구 루트(`<QL_HOME>/data/factor_inputs`, CLI
  기본값)를 같이 쓰면 keep 이 하루 2판씩 소모돼 모델 판이 가리키는 fi 판이 GC 된다 — 장 마감 판이
  연구 루트를 가리키면(심볼릭 링크 등 표기가 달라도 실제 경로로 본다) rc 2 로 거절한다.
- 진입 조건 — 어긋나면 rc 2, 사유는 stderr: (a) T 가 `daily.calendar` 거래일이다(판정 달력을 못
  읽어도 rc 2) (b) MD-SEAM — `daily.calendar` 의 T 직전 거래일 D' = 고정 판 `trading_calendar` 의
  마지막 날(연구 판이 D' 까지 왔고 T 를 아직 담지 않았다).
- 세션 = 연구 판 `trading_calendar` ∪ {T}(`_calx`). 가격 550 달력일·수급·신용 60 세션 창은 T 에서
  끝난다.
- 유니버스 = `universe_daily` **D' 행 이월**(date 열만 T, T 신규 상장은 빠진다). 시총 =
  round(D' KRX 상장주식수 × T 종가 / 1e8), `mktcap_basis = 't1_shares_x_t_close'`. T 종가는 T 행
  `_t_prices`(§2-2)에서 읽고 없으면 시총 NULL · `no_price`.
- 정보 시점 asof = D': 날짜 축이 있는 정보 입력 — WISE(컨센서스·연간·분기 재무)·DART(재무·4Q
  파생값·배당·감사·공시)·`universe_daily`(D' 행)·WICS(`sector_snapshot`)·추정기관 수·기업행위
  (`adj_factor.available_date`) — 를 fetched/available ≤ D' 로 자른다(compat `--consensus-asof` 와
  같은 개념). 재생이 D' 뒤 자료를 담은 판을 고정해도 실운영과 같다. 날짜 축이 없는 마스터
  (`security` 이름·상장일, `corp` 업종 코드·지주사 판정)는 고정 판 그대로다.
- 세션 축은 T 다: 창의 끝 · 신용 `available_date ≤ T`(T 아침 실입수분 포함) · 당해 12월기 `fy` 와
  연간 컨센서스 연도 창 Y = **T 의 연도**(연초 첫 거래일에 D' 와 해가 갈려도 다음 날 연구 판 T 와 같다).
- `filing_late`: 법정기한이 (D', T] 에 드는 보고서는 세션 축에 T 가 있어 실효 기한이 T 가 되고,
  접수일 ≤ D' < T 라 false 다. 아침판 D' 에서는 기한 뒤 세션이 달력에 없어 NULL 이다(다음 날 연구 판
  T 와 같은 판정 — 두 판 대조의 등록 범주).
- WISE 수집 지연(N-12)은 (D\*, D'] 의 거래일 수다 — 예상 수집일이 D' 다(T 로 재면 정상 상태가
  lag 1).
- 산출은 `_runs/<T>_evening.json` · `latest_evening.json`, 판 id 접두 `e_`.

### 2-2. 장 마감 판 T 행 · 당일 기업행위 · 후보 커버리지(컷오버 PR-5 · fi1.7.0)

- 원천 = 장 마감 stage 루트(`--postclose-stage-root`, 기본 `<QL_HOME>/data/model_db/stage` — T-29)의
  `stg_flow_postclose_kiwoom` current 판, date = T 행(`_t_src`). 15:41 수집(`daily.postclose` →
  `data/raw/postclose.db`)과 stage 단독 빌드가 먼저 끝나 있어야 한다(체인은 PR-8). 판이 없으면 rc 2.
  이 판은 고정하지 않는다(T 의 판) — 읽은 판 id 는 판 manifest `postclose_stage_root`·`postclose_builds`
  와 `tables.<표>.inputs` 에 남는다(연구 stage 판 목록 `stage_builds` 와 섞지 않는다). K1-7a 허용표에
  이 표 한정 stage SKIP 3종(G4 `golden_inherited` · G6 `write_mode=first_write_wins` · G8 `not_blob`)이
  올라 있다.
- **가격(`fi_prices` T 행)** = 가격을 쓸 수 있는 행만 — price_valid 참 · 종가 > 0 · 거래량 있음
  (`daily.kw_daily.ka10060_postclose_price_usable_sql`, compat T 행 ① 선택과 같은 술어). `price_source =
  'postclose'`, close = |cur_prc|(키움 KRX 코드 정규장 종가, N-35 ①), volume = 수집 시점 누적 거래량,
  open·high·low·amount = NULL(ka10060 에 없다 — 엔진은 종가만 읽는다). 술어가 거짓(16:00 뒤 응답 ·
  price_valid NULL · 종가 0 이하 · 거래량 없음)이거나 원장 행이 없으면 그 종목은 T 가격이 없다(`no_price`).
- **연구 부분은 D'(asof)에서 자른다**: 수급(`flow_daily`)과 수정주가 원천(`price_adj_daily` — `_t_adj_src`
  첫 갈래)은 date ≤ D' 만 읽는다 — 연구 판에 T 날짜 행이 섞여도 T 행은 장 마감 원천 하나다(아침판은
  asof = D 라 SQL 이 그대로다).
- **수정주가(`fi_adj_prices` T 행)** = T 행을 연구 판 SQL 이 읽는 원천에 넣는다(`_t_adj_src` =
  equity `price_adj_daily` + T 행: adj_close = T 종가 × D' cum_share_factor ÷ D' cum_price_only_factor,
  누적계수 = D' 값). 그래서 adj_factor · adj_ok · adj_jump_ok 가 연구 판 행과 같은 SQL 에서 나온다 —
  점프 판정 수익률은 세션 축이라 T 행까지 보고(asof 로 자르지 않는다), 사건은 available ≤ D' 만 센다.
  T 에 적용일이 있는 사건이 없고 T 수익률이 점프가 아니면 표식은 D' 값과 같다. D' 수정주가 행이 없는
  종목은 T 행도 없다.
- **수급(`fi_flows` T 행)** = 장 마감 수급 12주체(KRX 정규장, N-35 ②), price_valid 와 무관하다. 같은
  단위(round(원 / 1e6))·같은 거르기(전 주체 NULL 은 행 없음). 신용은 T 행이 없다(실입수 랙 3).
- **T-6 당일 기업행위(`_t_pending`)**: T 가격이 있어도 아래 하나면 그날 eligible=false, 사유
  `corp_action_pending`(no_price 다음 순서). 첫 조건은 compat T 행(QL-D)과 같은 술어
  `daily.kw_daily.ka10060_base_price_differs_sql`(판정 불가도 참)이고, 갈래 이름은 FG5 metrics
  `corp_action_pending` 에 기록만 한다.
  - `base_price` — 키움 기준가(stage `close_krw − pred_pre_krw` = |cur_prc| − pred_pre, I-1: KRX 기준가와
    사건일 4,219/4,219 일치) ≠ D' KRX 종가
  - `price_limit` — |T 종가 / D' KRX 종가 − 1| > T 날짜의 가격제한폭(`queries.PRICE_LIMIT_*`, H1-4 와
    같은 정의) + 1e-9. 정확히 ±30%(상·하한가)는 보류가 아니다
  - `no_value` — pred_pre 가 없거나 D' KRX 종가가 없거나 **0 이하**다
- **후보 커버리지(FG5, N-42 Q4)**: 대상 = 직전 판 모델 후보 — 연구 fi 루트(`--candidates-root`, 기본
  연구 루트)의 `_runs/<D'>_morning.json`(status ok) `fi_universe.eligible`. 수집기 순서 ① 과 같은 함수
  (`daily.postclose.fi_candidates`)로 읽는다. 대상 중 T 가격이 없는 종목(원장 행 없음 · price_valid
  아님 · 그 밖) 비율이 `T_CANDIDATE_MISSING_MAX`(= 1 − `kw_daily.COMMIT_MIN_RATIO` = 0.02)를 **넘으면** 판
  실패(rc 1). T-6 보류는 T 행이 있으므로 세지 않는다(수집 결손이 아니라 사건 — 기록만). 후보 중 보류
  비율이 `T_CANDIDATE_PENDING_WARN`(0.02)을 넘으면 metrics `warn`·detail 'warn:' 만 남기고 판정은 그대로다
  (기록형 — 새 정지 조건이 아니다, P3). T 가격은 있는데 pred_pre 가 없는 층 종목 수(`n_pred_pre_missing`)와
  장 마감 stage 판 id·그 판의 max(date)(`t_source_build`·`t_source_max_date` — T 가 아니면 판이 T 수집
  전에 섰다)도 남긴다. 후보를 못 읽으면 FG5 FAIL.

### 2-3. 두 판 대조(컷오버 PR-7 · 판정 기준 T-36)

```bash
python -m daily.board_compare --date <T> --evening-root data/model_db --research-root data \
    [--calendar-dir data/calendar] [--replay] [--out-root DIR] [--spearman-min 0.975] [--list-n 20]
```

- 장 마감 판 T(`data/model_db/{factor_inputs,model}/_runs/<T>_evening.json`)를 다음 날 08:10 연구 판
  T(`data/{factor_inputs,model}/_runs/<T>_morning.json`)와 맞대고, 연구 판 D'(`data/factor_inputs/_runs/
  <D'>_morning.json`)를 3자 대조의 기준으로 읽는다. 증거 원천: 장 마감 stage T 행(장 마감 판 기록
  `postclose_stage_root`·`postclose_builds` 그대로, 가격 술어는 §2-2 와 같은
  `kw_daily.ka10060_postclose_price_usable_sql`) · 수집 대상(① `daily.postclose.fi_candidates` ·
  ② `V3_STOCK_FILTER`) · 연구 판 T 가 읽은 equity `adj_factor`((D', T] 에 공개된 기업행위) ·
  `daily.calendar`(D' = T 직전 거래일 = 장 마감 판 asof = `builds_from_date`).
- 범주는 증거가 있을 때만 인정한다. 정의·정본 근거의 단일 정본은 `src/daily/board_compare.py` 의
  `CATEGORIES` 다. 이월·정보 시점·filing_late 는 '장 마감 T = 연구 D' 이고 연구 T ≠ 연구 D'' 이고, 연초 첫
  거래일의 연도 창만 예외다. 실운영 T 종가 차이는 미설명이고 `--replay`(T 행 = 21:05 원장)이면서 T 가
  KRX 애프터마켓 시행일(2026-09-14, `AFTER_MARKET_START`) 이후일 때만 종가 정의다(그 전 날은 21:05 키움
  종가 = 정규장 종가라 미설명). 거래량·수급(주체별 판 통계 상한)·16:00 컷오프(stage)·수집 대상 밖·
  T-6(연구 판 흔적)·신용 T 실입수는 각자 증거를 본다. 맞는 범주가 없으면 미설명이다.
- 모델은 spec 마다 종합점수 Spearman · 엑셀 후보 겹침 · 점수 열 |Δ| 상위 종목과 그 종목의 fi 범주.
- rc 0 = 미설명 0 · 모든 spec Spearman ≥ 하한(0.975 임시 — `--spearman-min 0` 은 기록형) / 1 = 미설명
  있음 또는 하한 미달 / 2 = 입력 오류. 결과는 `<out-root>/compare/<T>.json`(기본 out-root =
  `--evening-root`, rc 2 도 `verdict: error` 로 남긴다).

## 3. 판 규약

```
data/factor_inputs/
  fi_<표>/MANIFEST.json            # stage.manifest — current_build · keep=60 · BuildRecord.inputs
  fi_<표>/v=<build_id>/part0.parquet
  fi_<표>/v=<build_id>/_meta.json  # 표·판·행수·content_hash·inputs·창·게이트
  _runs/<YYYYMMDD>_<basis>.json    # 판 manifest(V2-8) — 성공·실패 모두. 같은 날 재실행은 덮는다. 단 FAIL 재실행은 같은 날 성공 기록을 덮지 않는다(실패는 _failed/ 에만)
  latest_<basis>.json              # 마지막 성공 판(= 그 판의 _runs 내용)
  _failed/<build_id>.json          # 게이트 FAIL 보고서
  _tmp/<build_id>/                 # 빌드 중 임시(끝나면 지운다)
```

- 판 id 하나(`m_<UTC>`)를 8표가 공유한다. 표마다 포인터를 따로 바꾸므로 전환 순간에는 섞여 보일 수
  있다 — **소비자는 `latest_<basis>.json` 의 `build_id` 로 8표를 연다**(keep=60 이라 다음 59번의
  빌드 동안 남는다).
- **parquet 은 `hive_partitioning=false` 로 읽는다.** 경로의 `v=<build_id>` 가 열(`v`)로 붙으면
  계약 밖 열이 된다(equity 산출과 같은 규약).
- 원천 판은 MANIFEST `current_build` 로 해석한다(맨 glob 금지). equity `_pinned/` 처럼 하드링크로
  고정하지 않는다 — 산출 자체가 창을 자른 사본이고 equity 루트에 쓰지 않기 위해서다. 읽은 판 id 는
  표별 `BuildRecord.inputs` 와 판 manifest `equity_builds`·`stage_builds`·`tables.<표>.inputs` 에 남는다.
- 판 manifest 필드: `layer · status · build_id · date · basis · asof · generated_at · rules_version ·
  equity_root · stage_root · equity_builds · stage_builds · postclose_stage_root · postclose_builds ·
  window · universe_rule · min_eligible · coverage · n_lapsed_dropped · tables · gaps · gates ·
  elapsed_s`(`postclose_*` 는 장 마감 판만, 아침판 null).

## 4. 표별 원천 · 창 · 단위

D = 판 기준일. 표 전부 `fi_universe` 종목으로 자른다. 아래 정보 상한 '≤ D' 는 장 마감 판에서
asof = D' 다(§2-1). 창의 끝(D 포함 550 달력일 · D 까지 60 세션)과 신용 `available_date ≤ D`, 연도
기준 Y 는 세션 축이라 장 마감 판에서 T 다.

| 표 | 원천 (equity 는 판, stage 는 `stg_`) | 창 | 단위·정수화 |
|---|---|---|---|
| `fi_universe` | `universe_daily`(D 행 — 장 마감 판은 D' 행 이월: 상장·정지 · KOSPI/KOSDAQ · ETF 제외 · `adv20_krw`·`admin_state`·`halt_state`) · `security`(이름·상장일) · `price_daily`(D 의 KRX 종가·상장주식수 — 장 마감 판은 D' 주식수 × T 종가) · `sector_snapshot`(D 이하 최신 WICS(장 마감 판은 asof=D', §2-1)) · `coverage_daily.analyst_count` · `audit_opinion` · `disclosure_version` · 신선도(`stg_consensus_annual`, §5) | D 한 날 | 시총 = **round(주식수 × 종가 / 1e8) 정수 억원**(compat `stocks.market_cap` 과 같은 반올림) · adv20 억원 |
| `fi_prices` | `price_daily`(basis `krx`) — 장 마감 판 T 행은 `stg_flow_postclose_kiwoom` 의 가격을 쓸 수 있는 행(§2-2) | D 포함 550 달력일 | 가격 원 · 거래량 주 · **거래대금 원**(compat 의 백만원이 아니다) |
| `fi_adj_prices` | `price_adj_daily.adj_close`·`cum_share_factor`·`cum_price_only_factor` · `adj_factor.price_resolution`(가격 축 미해결 사건) · `trading_calendar`(인접 세션) — 장 마감 판은 T 행을 넣은 원천 `_t_adj_src`(§2-2) | 550 달력일 | `adj_factor` = cum_share ÷ cum_price_only(원가 × 계수 = 수정가, fi1.3.0) · `adj_ok` = 가격 축 미해결 사건 **계단 표식**(아래) · `adj_jump_ok` = 그중 제한폭 초과 **점프 행**마다 뒤집히는 계단 표식(아래, fi1.6.0) |
| `fi_flows` | `flow_daily` 12주체(키움 우선, 전 주체 NULL 칸은 행 없음) — 장 마감 판 T 행은 `stg_flow_postclose_kiwoom`(price_valid 무관, §2-2) | D 까지 60 세션 | **round(원 / 1e6) 정수 백만원** |
| `fi_credit` | `credit_daily.whol_loan_rmnd_stcn_shr`·`whol_loan_rmnd_rate_pct` | D 까지 60 세션, `available_date ≤ D` | 주 · % · `available_date` = 그날 + 3 세션(KIS 실입수, equity FieldProfile) |
| `fi_consensus` | `stg_consensus_matrix`(c1050001 T4) — **한시 예외**(§7) | 신선·유예 종목의 마지막 신선일 판 × 결산기 3 × horizon cur/1w/1m/3m | 억원·원·배·% 원값(eps·bps 는 정수 — compat 과 같다). 판에 있는 (종목, 결산기)마다 네 horizon 행을 값이 NULL 이어도 만든다 |
| `fi_consensus_annual` (v2 전용) | `stg_consensus_annual`(WISE c1050001 T2Y — v2 원천) | 종목별 fetched_date ≤ D(장 마감 판은 asof=D', §2-1) **최신 한 판**, 전년·당해·차년 12월기(Y−1/12 · Y/12 · Y+1/12, Y = D 의 연도 — 장 마감 판은 T 의 연도), 추정 E·확정 A 둘 다 | 억원·원·배 원값(eps 정수 — compat `consensus_annual` 과 같다). 원천 행이 있으면 값이 전부 NULL 이어도 싣는다(v2 에게는 행 존재가 뜻이 있다) |
| `fi_fin_summary` | 연간: `stg_fin_wise`(WISE cF3002·cF4002, 계정명+최상위 선택 DQ-6 — **(종목, ep) 단위** fetched_date ≤ D(장 마감 판은 asof=D', §2-1) 최신 판, fi1.4.0) + `fin_std`(사업보고서, 연결 우선, PIT) + `dividend_event`(보통주 dps) · 분기: `fin_std`(1Q·반기·3Q 3개월 값, 4Q = 사업보고서 − 1~3Q) | 연간 최신 2기 · 분기 최신 5기 | 금액 **정수 억원**(compat 식 그대로) · 비율 % · dps 원 |

`fi_consensus` 와 `fi_consensus_annual` 을 나눈 이유(계약 09-29 W1-d): 같은 기·같은 항목이어도
v3 원천(매트릭스 T4 · cF3002)과 v2 원천(c1050001 T2Y)의 값이 다르다(09-28 골든: 당해 추정 op 318 ·
per 618종목, 2025/12 확정 ni 595종목 — 어댑터 `compat_to_fi` 주석). 두 원본을 동시에 맞추려고
원천마다 표를 둔다. `fi_consensus_annual`
에는 신선도 유예를 걸지 않는다(계약이 '최신 ≤ D 한 판' — compat 과 같고 eligible 이 이미 거른다).
compat 은 한 기에 E 하나(E 우선)만 남기지만 이 표는 E·A 를 둘 다 싣고 v2 의 E 우선은 엔진이 한다.

`fi_fin_summary` 의 compat 대비 차이(전부 의도):

- 연간 = 결산월 12 인 기만(compat DQ-11 v3 미러 — v3_zscore@1.0 동등). **비12월 결산 연간 확정치는
  싣지 않는다.** compat 은 그것을 `quarter` 로 두지만(v3 는 읽지 않는다) 이 표의 quarter 는 3개월
  분기라 12개월 값이 섞이면 v4 TTM 이 틀어지고 같은 결산월의 DART 4Q 행과 키가 겹친다.
- WISE 값은 신선·유예 종목만(T2.11). 그 밖 종목은 WISE 열이 NULL 이고 DART 열만 남는다.
- (E) 추정 슬롯은 싣지 않는다 — 추정치는 `fi_consensus` 몫(어댑터와 같은 규약).
- `fs_basis` = WISE 라벨, WISE 가 없는 행은 `DART:<fs_div>`. `capex_basis` 는 DART 그대로(DQ-8).
- `dps` = `dividend_event.dps_krw`(사업보고서 11011, `stock_knd ∈ {보통주, 보통주식}`, 값이 없으면
  법인 축 `'-'` 행, 결산기 = `stlm_dt`, `available_date ≤ D`(장 마감 판은 asof=D', §2-1)). 모르면 NULL(무배당 = 0 은 엔진이
  정한다). **연구 R-2 질의와 다른 점**: R-2 는 `'-'` 행을 빼고 쟀다 — 09-28 로컬 판 실측 FY2025 배당
  법인 1,261 중 155 곳이 `'-'` 행에만 dps 를 싣는다. 빼면 그 155 곳이 NULL → 엔진의 '무배당 0' 으로
  조용히 떨어지므로 보통주 값이 없을 때만 `'-'` 를 쓴다.
- WISE 연간 판 = **(종목, ep) 단위** fetched_date ≤ D(장 마감 판은 asof=D', §2-1) 최신(fi1.4.0, 배포 묶음 7 D7-4). stage 2.7.0 이
  같은 (종목, ep, pkey) 의 직전 판과 같은 원문을 접어 cF3002(손익)·cF4002(지표) 판 날짜가 종목 안에서
  다를 수 있다 — 종목 한 날짜로 고르면 cF4002 만 새 판인 날 손익 열(매출·영업이익·순이익·매출총이익·
  fs_basis)이 조용히 빈다(fi1.2.0 까지의 쿼리). 한쪽 ep 가 그날 수집되지 않았으면 그 ep 의 직전 판을
  잇는다(결손 자체는 WISE 부분 실패 알림 — N-30 ③ — 이 따로 알린다). compat `financial_summary` 도 같은 규칙.
- `available_date` = 행을 이룬 원천의 max(WISE fetched_date, DART·배당 available_date). 연간 행의
  WISE 날짜는 두 ep 판 중 늦은 날, 분기 WISE 행은 Q:IS 판 날짜다. DART 4Q 행(사업보고서 − 1~3Q)은
  파생값(`q4_derived_available_date` ≤ asof)을 실었으면 max(사업보고서 available_date,
  `q4_derived_available_date`), 못 실었으면(값 NULL) 사업보고서 날짜다(fi1.8.0, 컷오버 F-1 · T-43 —
  1~3Q 정정이 뒤에 들어오면 파생값이 그날 처음 선다). stage 2.7.0 부터 WISE fetched_date 는
  '그 원문을 처음 본 날'(그 전엔 '마지막으로 확인한 날')이라 원문이 그대로인 종목은 D 보다 이르다 —
  '알게 된 날' 뜻은 그대로이고 ≤ D(장 마감 판은 asof=D', §2-1) 다(FG1). 엔진은 읽지 않는다.
- 연간 매출은 분기와 같은 계정(최상위 '매출액(수익)' → 보험 '영업수익' → 은행·증권·금융지주
  '순영업이익')으로 고르고 `revenue_basis`(gross | net)를 적는다(fi1.2.0, 배포 묶음 4-2b). compat 은
  '매출액(수익)' 만 봐서 금융업 연간 매출이 비었다(10-02 판 scope 금융 28종목 전부). v3 엔진은 매출을
  읽지 않으므로 G-M3 동등성과 무관하다.
- `period_months`(연간 행만) = DART `fin_std` `period_start`~`period_end` 의 **달력 달 수**(양끝 달
  포함 — 1월 중 설립이면 12, 06-15 시작이면 7. fin_std 의 1분기·3분기 판정과 같은 식). 12 미만 =
  짧은 첫 사업연도·결산월 변경·리츠 단기 결산 등 — scope 가 퀄리티 손익 지표에서 뺀다(G-28,
  N-25 Q5, fi1.2.0). 엑셀 비고는 후속(4-2a 병합 뒤) — 문구는 '회계기간 N개월'(결산월 변경도 걸려
  '첫 사업연도' 는 틀릴 수 있다), 문턱은 12 를 박지 말고 spec `quality.min_period_months` 를 읽는다.
- 연간 `revenue_basis` 는 WISE 가 없는 연간 행(DART 만)에서 NULL 이다.
- 분기 행: eps·bps·per·pbr·ev_ebitda·dividend_yield·dps·shares·roe·roa·fcf·capex 는 NULL(아래 §7).

`fi_adj_prices.adj_ok`(오케스트레이터 09-29): 창 안 가격 축 미해결 사건
(`adj_factor.price_resolution = 'unresolved'`, 적용일 ∈ (창 시작, D], available ≤ D(장 마감 판은 asof=D', §2-1))의 **적용일마다
값이 뒤집힌다** — 창 첫 구간 True, 첫 사건 적용일부터 False, 둘째 사건부터 다시 True …(같은 날
사건 여럿은 한 번). **사건 쪽(적용일 이후)을
뒤집는다** — 사건이 하나면 '사건 전 True · 사건부터 False'. 창 안에서 값이 바뀌면 그 창이 사건을
넘는다(v4 엔진 `crosses_event`), 한결같으면 척도가 이어진다. 창 밖 옛 사건은 세지 않는다. 09-28 로컬
판 실측: 2,763 종목 중 224 종목이 창 안에 전환을 가진다(fi1.2.0 기준 — `factor_ok = false` 전부를
셌다).
fi1.3.0(배포 묶음 6-3, N-33): not-ok 행 중 가격 축에서 해소된 것(⑤ 계수 행 `price_only` · 같은 단위
다른 행 `price_only_dup` · `factor_near`(C-05 원안 '정상 사건의 중복본': 형제가 ok 인 억제 중복본 ·
창 안에 ok 계수 적용일이 있는 행((c) 후보 없음) · ok 계수가 접히는 날의 행 — 뒤 둘은 ② 사유 행·유상감자
제외) · `price_only_near`(근처 ⑤ 단위, (c) 후보 없음))은 수정종가가 이어지므로 세지 않는다. equity e1.26.0 이전 판(새 열 없음)을 읽으면 판을 만들지 않고 멈춘다.

`fi_adj_prices.adj_jump_ok`(fi1.6.0, 컷오버 H1-4 · T-9): `adj_ok` 와 같은 모양의 계단이되 **점프 행**마다
뒤집힌다. 점프 행 = 위 미해결 사건(available ≤ asof)의 적용일(첫 거래일 ≥ 적용일) 앞뒤
`ADJ_JUMP_NEIGHBOR_SESSIONS`(6) 세션 안의 그 종목 행 중 |수정종가 ÷ 그 종목 직전 행 수정종가 − 1| > 그 행
날짜의 제한폭인 행. **±6 은 거래일(세션) 기준**이다(달력일 아님 — `_calx` 번호 차). 제한폭 = 행 날짜 기준
2015-06-15 전 15%·뒤 30%(`queries.PRICE_LIMIT_*` — 날짜별 제한폭의 유일한 정의, 상한가 그대로의 부동소수
잡음은 `PRICE_LIMIT_EPS` 로 넘지 않은 것으로 본다). 적용일이 아니라 점프 행에서 뒤집으므로, 명목 적용일과
실제 점프가 며칠 어긋나도 그 사이에서 시작하는 창이 점프를 품고 빠져나가지 않는다. D 이하 행만 본다
(D 뒤 점프는 그날 판이 모른다). scope 는 모멘텀(r1m~r12m)·20일 변동성 창 안에서 이 값이 바뀌면 그
지표를 결측으로 둔다(`params.adj_jump_missing`, mb1.6.0). 제한폭 안 미해결 사건(10-09 분해: 주식 계열
334)은 수정종가가 끊겼다고 볼 근거가 없어 세지 않는다 — 지금 scope 유니버스의 창 안 미해결 종목은
전부 제한폭 안이라 이 규칙은 앞으로 생길 점프만 막는다. 미해결 사건이 없는 끊김은 이 표식 밖이다 —
B-65(정지 뒤 재개 기준가 리셋, 행도 표식도 없음)의 불연속은 scope 모멘텀·20일 변동성에 그대로 남는다.

D-13 적격성 재료 5열(계약 09-29 — **eligible 에는 쓰지 않는다**, v4 가 `UniverseRule.exclude`·
`min_adv20` 으로 건다):

| 열 | 원천 · 규칙 | 09-28 로컬 판(2,763) |
|---|---|---|
| `adv20` | `universe_daily.adv20_krw / 1e8` — [D−19, D] 20 세션 거래대금 평균(억원). 값 있는 세션이 20 미만이면 NULL | 값 2,757 |
| `is_admin` | `universe_daily.admin_state` — 관리종목(KOSDAQ 투자주의환기 소속부 포함), 판정 재료가 없으면 NULL | true 179 |
| `is_halted` | `universe_daily.halt_state` — D 에 매매거래정지 | true 113 |
| `audit_adverse` | 의견이 실린 가장 최근 사업보고서(11011, available ≤ D(장 마감 판은 asof=D', §2-1))의 **당기 행** 분류 ∈ {한정, 부적정, 의견거절}. 한 접수에 당기·전기·전전기 3행 → 라벨에 '전' 없는 행, '당' 우선, 기수 큰 순. 의견 원문 없는 행(FY2025 '-' 라벨 414건)은 건너뛰어 직전 알려진 의견. 적정 → false, other → NULL | 알려짐 2,560 · true 43 |
| `filing_late` | 가장 최근 정기보고서 **원본**(정정 제외, available ≤ D(장 마감 판은 asof=D', §2-1))의 접수일 > 실효 기한. `legal_deadline`(기말 + 90/45 역일)은 휴장 보정이 없어 기한이 휴장일이면 **다음 거래일**로 민다(세션 축 — 장 마감 판은 T 포함이라 기한 ∈ (D', T] 는 T 로, 접수일 ≤ D' 라 false · 아침판 D' 는 NULL). 미제출(기한 지났는데 아직 안 낸 보고서)은 보지 못한다 | 알려짐 2,638 · true 35 |

`fi_universe.eligible` = 기본 `UniverseRule()` — sec_type ∈ {common, spac} · 시장 ∈ {KOSPI, KOSDAQ}
· D 에 KRX 종가 있음 · 추정치 보유(`has_estimates`). **시총 하한은 걸지 않는다**(엔진이
`spec.universe.min_market_cap` 으로 건다). `exclude_reason` 은 먼저 걸린 하나:
`sec_type → market → no_price → corp_action_pending → estimates_lapsed → estimates_none`
(`corp_action_pending` 은 장 마감 판 T-6 만, §2-2).

## 5. 신선도 유예 (T2.11 · DQ-7)

- D\* = `stg_consensus_annual` 의 fetched_date ≤ D(장 마감 판은 asof=D', §2-1) 중 **마지막 수집일**(전 종목 공통).
- 종목의 마지막 신선일 = 당해 12월기(`period = D.year||'12'` — 장 마감 판은 T 의 연도) 추정(`period_kind='E'`)의 op·ni 가
  **둘 다** 있는 fetched_date 의 최댓값(compat 추정치 유니버스와 같은 판정).
- 나이 = (마지막 신선일, D\*] 안의 거래일 수(equity `trading_calendar`).

| `coverage_state` | 조건 | `has_estimates` | 쓰는 판 |
|---|---|---|---|
| `fresh` | 마지막 신선일 = D\* (나이 0) | true | D\* 판 |
| `grace` | 나이 ≤ G | true | 마지막 신선일 판(추정·WISE 재무·추정기관 수) |
| `lapsed` | 나이 > G | false → 유니버스 제외, `n_lapsed_dropped` | 없음(추정·WISE 재무 NULL) |
| `none` | 신선일이 한 번도 없다 | false | 없음 |

- G = `--grace-days`(기본 `UniverseRule().coverage_grace_days` = **0** — 2026-10-05 사용자 결정 N-14.
  WISE 는 증권사 추정치를 약 3개월(91~92일)만 쓰므로 이탈은 만료이고, 유예하면 만료 추정치로 채점한다.
  근거 `DECISIONS.md` U1 원인). 복귀(fresh 재진입)는
  마지막 신선일이 D\* 로 바뀌는 것이라 나이가 저절로 0 이 된다.
- `n_lapsed_dropped` = 다른 조건은 다 통과했는데 lapsed 라 빠진 종목 수(판 manifest 최상위와
  `coverage`).
- compat 그림자에는 적용하지 않는다(v3 미러). 그래서 compat 추정치 유니버스와 이 층의 eligible 은
  유예·소멸 종목에서 갈린다 — compat 은 수집이 끊긴 종목의 옛 판을 계속 쓰고(DQ-7), 이 층은 G 뒤 뺀다.

## 6. 게이트 (판 끝에서, FAIL 이면 커밋 안 함)

| 게이트 | 판정 | FAIL 조건 |
|---|---|---|
| FG0 스키마 | 8표 열 이름·순서·타입 = 계약 | 하나라도 다르면(뒤 게이트는 `skip(upstream_failed)`) |
| FG1 행수 | 유니버스 × 창 | 종목 ⊄ fi_universe · fi_universe 종목 중복·date ≠ D · eligible 인데 D 가격 없음 · eligible 인데 cur 컨센서스 없음 · eligible < `--min-eligible`(기본 300) · 날짜가 창 밖 · 수급·신용 60 세션 초과 · 신용 `available_date > D` · 재무 `available_date > asof` · 연간 > 2기 · 분기 > 5기 · horizon 어휘 밖 · `fi_consensus_annual` 기가 Y−1~Y+1/12 밖·data_type ∉ {E, A}·fetched_date > asof · eligible 중 WISE 연간 재무(per 또는 eps) 비율 < 0.9. 기록형: eligible 중 WISE 연간 손익(op 또는 ni — cF3002) 비율 `eligible_wise_is_ratio`(fi1.4.0, D7-8 — 서버 재연 6개 D 에서 ≥ 0.95 면 `FIN_IS_COVERAGE_ENFORCED` 로 같은 하한 0.9 의 FAIL 조건으로 올린다) |
| FG2 T 행 출처 | 아침판: 전 행 KRX. 장 마감 판: T 전 행 KRX · T 행 `postclose` · 시총 기준 `t1_shares_x_t_close`, T 가격 행 수 `n_t_price_rows` 기록 | 아침판 `price_source`·`mktcap_basis` ≠ 'krx' · 장 마감 판 T 전 행 ≠ krx · T 행 ≠ postclose · `mktcap_basis` ≠ t1 |
| FG3 시총 | market_cap = round(shares × close(D) / 1e8) (상대 1e-6) | 규칙 위반 · 주식수·종가가 있는데 NULL · basis ≠ krx(장 마감 판은 ≠ t1). KRX `mktcap_krw` 반올림값과 다른 수는 기록형(`n_krx_mktcap_diff` — 장 마감 판은 KRX T 시총이 없어 NULL). 장 마감 판의 shares 는 D' 주식수, close 는 T 행 종가 |
| FG4 골든 | `src/factor_inputs/fixtures/golden.json`(3종목 005930·000660·161890, 22항목, stage 원장에서 손으로 옮긴 값) | 창·유니버스 안 항목이 값이 다르거나 행이 없다. 셀 수 있는 항목이 0 이면 `skip(no_fixtures)` — 허용표 밖이라 판은 **FAIL**(K1-7a). 창이 지나가면 골든을 갱신한다(수급·신용 항목은 2026-08 날짜라 11월 중순에 창 밖, 가격 항목은 550일 창이라 2028-02 까지 남는다) |
| FG-fresh | 상태 수·eligible 상태 수·`n_lapsed_dropped`·D\*·수집 지연 기록 | 어휘 밖 · has_estimates ≠ 상태 · fresh 나이 ≠ 0 · grace 나이 > G · lapsed 나이 ≤ G · lapsed/none 인데 eligible · **D\* 가 예상 수집일(asof — 아침판 D, 장 마감 판 D')보다 1거래일 넘게 뒤처짐**(`COLLECTION_LAG_MAX`, N-12 — 유예 G 와 따로 둔다. 수집 정지 — 전 종목이 '신선' 으로 보이는 조용한 낡음). D 이전 수집 기록이 없으면 `skip(no_collection)` — 허용표 밖이라 판은 **FAIL**(K1-7a) |
| FG5 후보 커버리지(장 마감 판만, 컷오버 PR-5) | 직전 판 모델 후보(수집기 순서 ① 과 같은 집합) 중 T 가격이 없는 종목 수·비율과 갈래(`n_no_row`·`n_price_invalid`·`n_missing_other`·`missing_tickers`) · T-6 보류 수(후보 중 · 층 전체 갈래별)와 후보 중 비율 > 0.02 이면 `warn`(기록형) · `n_pred_pre_missing` · 장 마감 stage 판 id·max(date) | **비율 > `T_CANDIDATE_MISSING_MAX`(= 1 − `kw_daily.COMMIT_MIN_RATIO` = 0.02)** — N-42 Q4 '당일 행 없는 종목이 상한 넘으면 판 실패'. 근거: N-35 ③ 프로브 후보 100/100(3일, 오류 0)·후보 먼저 수집이라 16:00 전 완료, 크기는 저녁 키움 직행 ka10060 커버 하한(T-28)에서 끌어온다 · 후보를 못 읽음(`candidates_unavailable`). 아침판 판 기록에는 없다 |

아침판 가드(게이트 전, rc 2): equity 판 접두어가 `e_` 이면 거절 · `price_daily`·`price_adj_daily`·
`adj_factor` 판의 빌드 시각 차 > 3시간이면 거절(compat R5·R9 와 같은 값).

## 7. 비운 열 · 보류 (판 manifest `gaps` 와 같다)

| 표.열 | 사유 |
|---|---|
| `fi_prices.price_source` · `fi_universe.mktcap_basis` | 아침판 전 행 `krx`. 장 마감 판은 T 전 행 `krx` · T 행 `postclose`(15:41 장 마감 직후 수집, 키움 KRX 코드 정규장 종가 — `stg_flow_postclose_kiwoom` 의 가격을 쓸 수 있는 행(price_valid 참 · 종가 > 0 · 거래량 있음)만. 그 밖·원장 행 없음은 T 가격 없음 → `no_price`, 시총 NULL) · 시총 기준 `t1_shares_x_t_close`(B-24) |
| `fi_prices` 장 마감 판 T 행 open·high·low·amount | 원천 ka10060 에 없어 NULL — volume 은 수집 시점 누적 거래량. 엔진은 종가만 읽는다(다음 날 아침 확정판 T 행은 KRX 값) |
| `fi_adj_prices` 장 마감 판 T 행 | adj_close = T 종가 × D' 누적계수(T 의 새 계수는 아직 없다), 표식은 연구 판 행과 같은 SQL(§2-2) — T 에 기준가를 바꾼 사건은 T-6 이 그날 eligible 에서 빼고 반영은 다음 날 아침 확정판 |
| `fi_universe` 장 마감 판 | `universe_daily` D' 행 이월(상태 변화는 다음 날 아침 확정판, T 신규 상장 누락) · WISE·DART·`universe_daily`·WICS 는 D' 까지, `security`·`corp` 은 고정 판 마스터 그대로 |
| `fi_consensus` 전체 원천 | **한시 예외**: equity 에 op·ni 컨센서스 표가 없어(B-23) stage `stg_consensus_matrix` 를 직독한다(compat GAP-6 과 같은 예외). 만료 = W1-a S17b `consensus_revision` |
| `fi_consensus.obs_date`(1w/1m/3m) | NULL — WISE 매트릭스는 lookback 관측일을 주지 않는다(cur 만 base_date) |
| `fi_fin_summary.op_margin·ni_margin·yoy` | compat 과 같이 NULL — 계산은 엔진 몫 |
| `fi_fin_summary` 분기 행 eps…capex | WISE 분기 슬롯(`val_q*`)은 기간 라벨이 없다 · 분기 ROA 연율화 정의 전 · 분기 capex·FCF 는 `fin_std._q` 가 부호가 섞인 누계의 차라(절단본 003540: 1Q −7.2억 → 반기 +13.7억) 싣지 않는다 |
| `fi_fin_summary` 비12월 결산 연간 | 싣지 않는다(§4) — 전 시장 12종목, 09-23 유니버스 0 |
| `fi_fin_summary.period_months` | 연간 행만 — DART 연간 행이 없거나(WISE 만) `fin_std.period_start`(문서 메타)가 없으면 NULL(모름 — scope 는 빼지 않는다). 분기 행 NULL |
| `fi_universe.filing_late` | 미제출 보고서는 판정 밖(가장 최근 제출분만) |
| `fi_universe.is_admin` | equity `admin_state` 그대로 — 판정 재료가 없으면 NULL |

## 8. 테스트

- `tests/test_factor_inputs.py` — 합성 트리 왕복(판·계약·신선도 4상태 + 복귀·유예 경계·유니버스·
  컨센서스·재무·창·수급·신용·실패 경로·CLI · 장 마감 판 세션·이월·D' 자르기·수집 지연·진입 조건 ·
  T 행 얹기(수집기 실물 함수로 만든 `postclose.db` → stage 빌더 실물 → 장 마감 판): price_valid 별
  가격·수급, 수정주가 같은 SQL, 연구 부분 D' 자르기, T-6 세 갈래·±30% 경계·D' 종가 ≤ 0, FG5 경계·
  후보 없음·대량 보류 경고, 장 마감 stage 판 없음 rc 2). 공유 술어 두 개의 진리표와 compat 쪽 쓰임은
  `tests/test_compat_evening_t.py`.
- `tests/test_factor_inputs_gates.py` — 게이트마다 한 가지씩 망가뜨려 FAIL 을 확인.
- `tests/test_factor_inputs_slice.py` — 커밋된 stage 절단본 위 equity 16표 체인 → 판 빌드(FG4 골든
  통과) · `fi_fin_summary` 연간 = compat `financial_summary` SQL(겹치는 열 전부 — 금융업 연간 매출
  003540 2기만 의도한 차이) · `fi_consensus_annual`
  = compat `consensus_annual` SQL(같은 종류 행) · 005930 DART 값 손계산.
