# 호환 계층 (v3 `quant.db`) — 소비자 감사와 만료 규약

작성 2026-09-24 (플랜 `docs/plans/2026-09-24-v3-merge.md` T1.4). 이 문서는 **감사 결과와 만료
조건**의 정본이다. 표별 SELECT 매핑과 단위 변환 자체는 `src/compat/mappings.py`·`units.py` 가
정본이고 여기서 중복하지 않는다.

근거는 v3 코드 스냅샷(읽기 전용)과 우리 `database/` 원문이다. 파일:줄은 v3 = 스냅샷 루트 기준,
우리 = `database/` 기준. 서버 접속 없이 정적으로 확인한 것만 적었고, 확인 못 한 것은 §6.

---

## 1. 목적과 만료 규약

v3 수집(daily_pipeline·adj_prices)을 죽여도 v3 후단(엑셀·텔레그램·국면 리포트·브리핑·
리서치센터·가설·뉴스·위키)과 별도 제품 unitelegram 은 `quant.db` 를 계속 읽는다. 호환 계층은
**그 표들을 우리 equity/model 판에서 채우는 한시 장치**다. 한시임을 코드가 기억하게 표마다
`retire_when` 을 단다(플랜 §3 6항): 소비자가 전부 model/equity 직독으로 옮겨지면 그 표는
exporter 에서 빠지고, 전부 빠지면 `quant.db` 는 플랜 v2 D.2 규약대로 동결된다.

| 표 | `retire_when` (이 조건이 참이 되면 exporter 에서 뺀다) |
|---|---|
| `daily_prices` | 브리핑 `kr_market.py` · 가설 `store.py` · 리서치센터 S1/S2/S11·base_rates·drilldown · unitelegram `watchlist_peak_card.py` 가 전부 equity `price_daily`/`price_adj_daily` 직독으로 옮겨진 뒤 |
| `stocks` | 위 + 뉴스 `preview.py`·`providers/naver_ir.py` · 리서치 브로커 `_store.py` · api `health.py` · `export_and_send.py` · unitelegram `kael_db.py` 가 equity `security`/`universe_daily`/`price_daily` 직독으로 옮겨진 뒤 |
| `investor_detail_flows` | 리서치센터 S1/S11·base_rates·drilldown · 가설 · unitelegram 이 equity `flow_daily` 직독으로 옮겨진 뒤 |
| `consensus_revision_daily` · `consensus_revision_compare` | 리서치센터 S10 이 equity `consensus_revision`(S17b, T2.6) 직독으로 옮겨진 뒤. **모델 입력으로서의 소비는 T2.6 시점에 이미 끝난다** |
| `consensus_annual` · `financial_summary` | 소비자가 v3 스코어링 엔진뿐이다 — 우리 model 층이 `score_history(_v2)` 를 채우는 **D-4 시점(G-M3 통과)** 에 즉시 만료 |
| `score_history` | 브리핑 상위 8 · `export_and_send.py` · api health · unitelegram `get_signal_insights` 가 `data/deliver/latest_scores_<basis>.json` 또는 model 판 직독으로 옮겨진 뒤 |
| `score_history_v2` | 리서치센터 S6 · `export_and_send.py` 가 위와 같이 옮겨진 뒤 |
| `stock_info` | 리서치센터 `drilldown.py:_profile` 1곳이 옮겨지거나, §5 판정대로 NULL 허용이 결정된 뒤 |
| `analyst_opinions` · `major_shareholders` | **소비자 0** (§2 확인). 채우지 않는다 — M4 컷오버일에 갱신 정지, M6 동결(플랜 §1-5) |

`market_*` 6표·`market_regime`·`market_inflection`·`research_reports`·`broker_reports`·
`pipeline_runs`·`column_units` 는 이 계층의 범위 밖이다(v3 가 계속 쓴다 — 플랜 §1-5·M5).

---

## 2. 소비자 컬럼 감사

정적 스캔은 `scripts/compat_consumer_audit.py` 가 낸다:

```
uv run --project backend python database/scripts/compat_consumer_audit.py \
    --v3-root <v3 스냅샷> --out -
```

정규식 근사라 열 목록은 후보다. 아래 표는 **스캔 결과를 원문 확인으로 확정한 것**이고, 시각은
실제 crontab(플랜 §1-6·§8-3)·unitelegram 크론이다. "필요한 행" 은 브리핑·시트가 스스로 해석하는
기준일이다(대부분 `MAX(trade_date)`).

### 2-1. 실제 crontab 에 있는 소비자

| 소비자 | 파일:줄 | 시각(KST) | 표.열 | 필요한 행 |
|---|---|---|---|---|
| 브리핑 morning — 기준일 해석 | `backend/briefing/collectors/kr_market.py:167-174` | 07:00 월~금 | `daily_prices.trade_date` | `MAX(trade_date) <= prev_business_day(오늘)` = **T 저녁 행**. 그 다음 `MAX(trade_date) < d` = T-1 |
| 브리핑 — 등락 상하위 15×2 | `kr_market.py:26-35`(`_MOVER_JOIN`)·`:79-94` | 07:00 | `daily_prices.close`·**`amount`**·`stock_code`·`trade_date`, `stocks.stock_name`·`market`·`sector` | T 행(`d`)과 T-1 행(`p`). 필터 `d.amount > 30000`(백만원 = **300억원**), `p.close > 0` |
| 브리핑 — 거래대금 상위 12 | `kr_market.py:98-120` | 07:00 | 같음 (`ORDER BY d.amount DESC LIMIT 12`) | T 행 + T-1 행 |
| 브리핑 — 스코어 상위 8 | `kr_market.py:127-142` | 07:00 | `score_history.composite_score`·`momentum_score`·`flow_score`·`valuation_score`·`stock_code`·`score_date`, `stocks.stock_name` | `score_date = T`(위에서 해석한 T) |
| 브리핑 — 지수·수급·국면 | `kr_market.py:38-57`·`:60-75`·`:149-162` | 07:00 | `market_indices`·`market_investor_flows`·`market_regime` (**범위 밖**, v3 insight 가 채운다) | T 행·T-1 행 |
| 엑셀·텔레그램 export | `scripts/export_and_send.py:15-33`·`:52-62`·`:124` | 저녁 (daily_all 안, M4 뒤 `daily_post`) | `score_history` **41열**(rank·composite·팩터 5·r1m~r12m·op/ni_change_1w/1m/3m·flow_*_5d/20d·qual_* 6·val_* 4·*_flag 6) · `score_history_v2` **20열** · `stocks.stock_name`·`market`·`sector`·`market_cap` | `score_date = --date` 또는 `date.today()` = **T** |
| api health | `backend/api/routers/health.py:69-73` | 상시 | `stocks` COUNT(*) · `score_history.score_date` MAX | 최신 1행 |
| 뉴스 이벤트 preview — 시총 상위 100 | `backend/news/events/preview.py:40-55` | 이벤트 잡 (04:30·07:40·일 09:00·09:30 등) | `stocks.stock_name`·`market_cap` (`market_cap IS NOT NULL ORDER BY DESC LIMIT 100`) | 날짜 축 없음(스냅샷) |
| 뉴스 이벤트 preview — 국내 종목명 | `preview.py:228-241` | 같음 | `stocks.stock_name` 전량 | 스냅샷 |
| 뉴스 naver_ir — 상위 600 | `backend/news/events/providers/naver_ir.py:23-33` | 같음 | `stocks.stock_code`·`is_active`·`market_cap` | 스냅샷 |
| 리서치 브로커 | `backend/research/brokers/_store.py:21` | 21:00 | `stocks.stock_name`·`stock_code`·`is_active` | 스냅샷 |
| 가설 shadow | `backend/hypothesis/store.py:32-73` | 00:30 | `daily_prices.stock_code`·`trade_date`·`adj_close`·`close`·`volume`·`amount` / `investor_detail_flows.foreign_investor`·`institution_total`·`individual` / `market_regime` 등 | `trade_date >= since` **전 기간**. NULL 허용(`None` 그대로 적재) |

### 2-2. unitelegram (별도 제품 — `quant.db` 를 rsync 로 통째 복사)

서버에서 확인한 사실(스냅샷 밖). 파일은 `~/unitelegram` 기준.

| 소비자 | 파일 | 시각(KST) | 표.열 | 필요한 행 |
|---|---|---|---|---|
| 종목 메타 | `sources/kael_db.py` | 아래 크론 전부 | `stocks.stock_code`·`stock_name`·`market`·`sector`·`market_cap` | 스냅샷 |
| 수급 | `sources/kael_db.py` | 같음 | `investor_detail_flows.foreign_investor`·`institution_total` 등 **최근 5일** | T-4 ~ T |
| 리포트 | `sources/kael_db.py` | 같음 | `research_reports.report_date`·`broker`·`title`·`target_price`·`opinion`·`summary` (**범위 밖** — v3 유지) | 최신 |
| 시그널 인사이트 | `sources/kael_db.py` | 같음 | `score_history.r1m`·`r3m`·`r6m` 등 | 최신 `score_date` |
| 벤치마크 | `portfolio/benchmark.py` | `weekly review` 07:00 등 | `market_indices` (**범위 밖**) | 기간 |
| 워치리스트 피크 카드 | `data/watchlist_peak_card.py` | `weekly watchlist` 일 09:00 | `daily_prices` | 기간 |
| 휴장일 | `sources/holiday.py` | 전부 | v3 `data/.kis_holidays.json` (표 아님 — `calendar_refresh` 산출) | — |
| KIS 토큰 캐시 | `market/rest.py` | 전부 | v3 KIS 토큰 캐시 파일 (표 아님) | — |

unitelegram 크론: `uni position eod` 06:35 · `weekly review` 07:00 · `weekly
watchlist`/`holding-review` 일 09:00 · `brief morning`/`lunch`/`close` 07:10·12:15·15:40.

### 2-3. 리서치센터 (`backend/research_center/`) — **실제 crontab 에 없다**

`scripts/run_research_center.sh` → `python -m backend.research_center.night` 는 job_runner
체인 밖 독립 라인이고 서버 crontab 에 등록돼 있지 않다(플랜 §1-5 의 `agenda.db`·`board.db`
처분 판단 근거). 아래는 **수동·베타 가동 시** 읽는 열이다 — D-8 우선순위 판단에서 이 점이
중요하다(오늘 매일 깨지고 있는 소비자가 아니다).

| 시트 | 파일:줄 | 표.열 | 필요한 행 |
|---|---|---|---|
| S1 수급 | `sheets_market_data.py:16-41` | `investor_detail_flows.foreign_investor`·`institution_total`·`stock_code`·`trade_date` / **`daily_prices.amount`**(유동성 가드 `>= 1000` 백만 = 10억) / `stocks.stock_name` | `asof = MAX(investor_detail_flows.trade_date)` = **T** + 최근 5 거래일 |
| S2 시세 | `sheets_market_data.py:44-83` | `daily_prices.adj_close`·`close`·**`amount`**·`volume`·`stock_code`·`trade_date` / `stocks.stock_name` | `asof = MAX(daily_prices.trade_date)` = **T**, 비교 행 T-1, 거래량 급증은 최근 21 세션 |
| S3 국면 | `sheets_market_data.py:85-120` | `market_regime`·`market_inflection`·`market_breadth`·`market_indices`·`macro_data` (**범위 밖**) | 최신 |
| S6 스코어 급변 | `sheets_meta.py:63-87` | `score_history_v2.total_score`·`stock_code`·`score_date` / `stocks.stock_name` | 최근 2 `score_date` (T, T-1) |
| S10 컨센서스 | `sheets_meta.py:148-178` | `consensus_revision_daily.collected_date`·`op`·`stock_code`·`target_period` / `consensus_revision_compare.op_1w` / `stocks.stock_name` | `MAX(collected_date)` — 스스로 "구조적 1일 지연" 이라고 적고 있다 |
| S11 시계열 특이점 | `sheets_stats.py:23-35`·`:83-118`·`:138-150` | `daily_prices.adj_close`·`close`·**`amount`**·`stock_code`·`trade_date` / `investor_detail_flows.foreign_investor`·`institution_total` / `stocks.stock_code`·`stock_name` | 최근 66 세션(`WIN`), 52주 신고·신저는 T 이전 252 세션 |
| base_rates | `base_rates.py:50-108` | `daily_prices.adj_close`·`close`·`amount` / `investor_detail_flows` / `stocks.stock_code`·`market` / `market_indices` | **전 기간**. 루프가 `range(41, len(rows)-1)` 이라 **마지막 행(T)은 쓰지 않는다** |
| drilldown `price_history` | `drilldown.py:71-78` | `daily_prices.trade_date`·`adj_close`·`close`·**`amount`** | 최근 `days` 행 |
| drilldown `flow_history` | `drilldown.py:80-89` | `investor_detail_flows.trade_date`·`foreign_investor`·`institution_total`·`individual` | 최근 `days` 행 |
| drilldown `stock_profile` | `drilldown.py:91-101` | `stocks.stock_name`·`sector` / `stock_info.foreign_ownership_pct`·`beta`·`high_52w`·`low_52w` | 스냅샷 (§5) |

### 2-4. 소비자가 없는 표

| 표 | 확인 |
|---|---|
| `analyst_opinions` | 참조 2건 전부 `backend/db/repositories/analyst_opinion_repo.py`(v3 수집 저장소). 읽는 후단 0 |
| `major_shareholders` | 참조 4건 전부 `major_shareholder_repo.py`. 읽는 후단 0 |
| `consensus_annual` | 후단 0. 읽는 곳은 `backend/scoring/v2_data_loader.py:34`(v3 엔진) + repo |
| `financial_summary` | 후단 0. 읽는 곳은 `backend/scoring/factors/quality.py:15`·`valuation.py:10`(v3 엔진) + repo |
| `stock_info` | 후단 1곳 — `research_center/drilldown.py:91-101`(§5) |

---

## 3. 저녁 T 행 결측 판정

우리 저녁 잠정판(`--basis evening`, 21:20 빌드)에서 T 세션 행이 어디까지 있는지. 근거는
`docs/EQUITY_DESIGN.md` §13-2, `src/equity/sql/price_daily.sql`·`price_adj_daily.sql`·
`flow_daily.sql`, `src/stage/rules_kiwoom.py`, `scripts/daily_evening.sh:69`, 결정 11.

저녁 21:05 키움 수집은 **`ka10060`·`ka10014` 두 TR 뿐**이다(`scripts/daily_evening.sh:69`).
`ka10008`(외국인 보유)은 다음 날 07:10 이고(`scripts/daily_build.sh:104`, 결정 7),
`ka20068`(대차)은 06:00 이다.

| v3 열 | 우리 저녁 T 원천 | 판정 |
|---|---|---|
| `daily_prices.open`·`high`·`low` | **없다** — `price_daily.sql` 의 evening 분기가 `NULL AS open_krw, NULL AS high_krw, NULL AS low_krw` 로 명시 고정(원칙 ④). ka10060·ka10014 에 OHL 필드 자체가 없다 | **결측**. 게다가 v3 DDL 이 `open/high/low/close/volume INTEGER NOT NULL`(`backend/db/schema.py:16-27`) 이라 **NULL 을 넣을 수 없다**(§4 DEFECT-C01) |
| `daily_prices.close` | `price_daily.close` ← ka10060 `cur_prc`(`rules_kiwoom.py:55`) | **있다**. 단 정의가 다르다 — KRX 공식 종가(15:30)가 아니라 **장후 마지막 체결가**(결정 11-(c)) |
| `daily_prices.volume` | `price_daily.volume_shr` ← ka10060 `acc_trde_prica`. 이름은 "누적거래대금" 이지만 **실측은 거래량(주)** — KRX `ACC_TRDVOL` 과 99.9864% 일치(`rules_kiwoom.py:57-58` 주석) | **있다** (애프터마켓 포함 — KRX 와 같은 정의, 결정 11-(a)) |
| `daily_prices.amount` (백만원) | **없다**. 저녁 키움 4 TR 어디에도 거래대금 필드가 없다: ka10060 의 `acc_trde_prica` 는 위처럼 거래량이고, ka10014 의 `shrts_trde_prica_krw` 는 **공매도 거래대금만**(`rules_kiwoom.py:104-105`), ka20068 `remn_amt_krw` 는 대차잔고 금액이다 | **결측 · 원장에 대체 필드 없음** |
| `daily_prices.adj_close` | `price_adj_daily.adj_close` — T 행 존재(전방 조정, `price_adj_daily.sql` 이 evening 행을 그대로 싣는다) | **있다** |
| `stocks.market_cap` (억원) | `price_daily.mktcap_krw` 는 evening 행에서 **NULL**(`shares_out` NULL). B-24 규칙(T-1 KRX `shares_out` × T 키움 `close`)으로 파생해야 한다 — 플랜 §3-2 가 `src/model/inputs.py` 한 곳에 두기로 한 규칙 | **파생 필요**(규칙은 이미 결정, 구현은 T2.2) |
| `investor_detail_flows` 13열 | 원장·stage 에는 있다(`stg_flow_daily_kiwoom` T 행). 그런데 equity `flow_daily` 의 격자는 `universe_daily` = `trading_calendar` × `security_span` 이고, `trading_calendar` = `stg_index_daily`(KRX 지수) distinct date 라 **저녁에는 T 가 캘린더에 없다**. 그래서 T 의 ka10060 행은 `flow_daily.sql` 세 번째 분기로 가 `reject_reason='off_grid'`(`_reject/reject_reason=off_grid/`)에 격리된다 | **equity 산출 표에 T 행 없음**. compat 는 stage 직독 또는 `_reject` 읽기가 필요하다 (§6 — 서버 실측 미확인) |
| `investor_detail_flows` T-1 이전 | `flow_daily` measured 행 | **있다** |

요약: 저녁 잠정판에서 v3 `daily_prices` 의 **open·high·low·amount 가 결측이고 원장에 대체
필드가 없다**. `close`·`volume`·`adj_close` 는 있다. `stocks.market_cap` 은 파생 가능.
`investor_detail_flows` 의 T 행은 원장에는 있으나 equity 표에는 없다.

### 3-1. 07:00 브리핑이 T 행을 쓰는가 — 쓴다

`backend/briefing/pipeline.py:122-139` 가 `date = kst_today()`(= D+1) →
`prev = prev_business_day(date)`(= D) → `kr_market.collect_kr(db_path, prev, prev2)`(`context.py:72`).
`kr_market._resolve`(`:165-174`)가 `MAX(trade_date) <= D` 로 다시 해석하므로 **D 저녁 행을
읽는다**. 우리 시간표에서 D+1 07:00 시점의 최신 판은 D 저녁 잠정판(21:20 빌드)이고, 확정판은
08:10 이라 아직 없다 — 즉 07:00 브리핑은 **구조적으로 저녁 잠정 T 행의 소비자**다.

---

## 4. GAP-1 판정과 D-8 선택지

### 4-1. 판정

무엇이 깨지는지는 두 갈래다.

#### DEFECT-C01 — 저녁 T 행은 v3 `daily_prices` 에 **넣을 수조차 없다**

- **상황**: M4 컷오버 뒤 저녁 체인이 compat exporter 로 v3 `quant.db` 에 T 행을 upsert 하려는
  시점. 우리 판은 `--basis evening`, `price_daily` T 행의 `open`·`high`·`low` 는 NULL.
- **인풋**:
  1. `python -m compat export --date D --basis evening --target ~/kael-system-v3/data/quant.db --tables daily_prices`
  2. 매핑이 `price_daily`(basis='evening') 행을 `INSERT OR REPLACE INTO daily_prices
     (stock_code, trade_date, open, high, low, close, volume, amount, adj_close)` 로 보냄
- **에러 위치**: v3 `backend/db/schema.py:16-27` — `open INTEGER NOT NULL, high INTEGER NOT
  NULL, low INTEGER NOT NULL, close INTEGER NOT NULL, volume INTEGER NOT NULL` (`amount`·
  `adj_close` 만 nullable). `migration_sql.py` 에 이 제약을 푸는 ALTER 없음(`adj_close` 추가만).
- **위험성**: 저녁 T 행 전량이 `IntegrityError: NOT NULL constraint failed` 로 실패한다.
  표 단위 트랜잭션이므로 `daily_prices` 표 전체가 롤백되고, 0행 실패 규약(플랜 §2-1)대로
  compat 가 rc≠0 로 죽는다. **OHL 을 채우거나 v3 DDL 을 고치지 않으면 저녁 T 행은 물리적으로
  못 들어간다** — 이건 품질 저하가 아니라 하드 블로커다.

#### DEFECT-C02 — T 행이 없으면 07:00 브리핑이 **하루 늦은 장을 오늘 장처럼** 보고한다

- **상황**: DEFECT-C01 때문에(또는 저녁 compat 를 아예 안 돌려서) `daily_prices` 에 D 행이
  없는 채로 D+1 07:00 브리핑이 돈다. 아침 확정 compat 는 08:10 빌드 뒤라 아직 없다.
- **인풋**: `job_runner.py --chain briefing_morning` (crontab `0 22 * * 0-4`)
- **에러 위치**: v3 `backend/briefing/collectors/kr_market.py:165-174` `_resolve()` —
  `SELECT MAX(trade_date) FROM daily_prices WHERE trade_date <= ?` 가 상한을 **힌트**로만 쓰고
  실재 최대 거래일로 내려앉는다(주석: "휴장일/갭 안전"). D 행이 없으면 조용히 D-1 을 고른다.
- **위험성**: 리포트 머리글은 `_date_header(date, "모닝 브리핑")`(`pipeline.py:152`)이라
  **D+1 날짜**로 찍히는데 본문 등락·거래대금·스코어는 **D-1 장**이다. 예외도 경고도 없다
  (`_resolve` 의 fallback 은 정상 경로다). 하루 묵은 장을 어제 장으로 읽고 매매 판단을 하는
  실사고 경로이고, `_collect_scores(d)` 도 `score_date = D-1` 을 집어 **스코어까지 하루 묵는다**.

#### 그 외 — amount 만 NULL 인 경우(가령 OHL 을 close 로 채워 넣었을 때)

| 소비자 | 결과 |
|---|---|
| 브리핑 등락 상하위 | `WHERE d.amount > 30000` 이 NULL 을 거른다 → 0행 → `prompts.py:79` 의 `if ctx.movers_up:` 이 **섹션을 조용히 통째로 뺀다** |
| 브리핑 거래대금 상위 12 | `ORDER BY d.amount DESC` 가 전부 NULL → 임의 12종목이 `amount_eok=None` 으로 실리고, `prompts.py:96` 의 `_fmt_int(m.amount_eok)`(`prompts.py:32-34`, `f"{v:,}"`)가 **TypeError** 로 터진다. `run_morning` 에 try/except 가 없어(`pipeline.py:122-160`) **모닝 브리핑 잡 전체가 실패**한다 — silent 는 아니고 job_runner 가 ⚠️ 를 낸다 |
| 리서치센터 S2 | `a.amount >= 1000` 이 NULL 을 걸러 **시세 시트 전체가 0행** |
| 리서치센터 S1 | 유동성 가드 조인이 NULL 을 걸러 **수급 시트 0행** |
| 리서치센터 S11 | `len(amts) == len(rows)` 검사(`sheets_stats.py:59-60`)가 깨져 **거래대금 축 특이점이 전 종목에서 사라진다**. 다른 축은 산다 |
| 리서치센터 base_rates | 영향 없음 — 루프가 마지막 행을 제외한다(`base_rates.py:87`) |
| 리서치센터 drilldown `price_history` | `f"대금 {r['amount']:,}백만"` 이 `TypeError` → `except Exception` 이 "조회 실패" 사실로 격리 |
| 가설 shadow | 영향 없음 — `amount[c][i] = float(v) if v is not None else None` |
| unitelegram | `watchlist_peak_card.py` 가 `daily_prices` 의 어느 열을 쓰는지 **미확인**(§6) |

### 4-2. D-8 선택지

| | (a) 저녁 ka10081 추가 수집 | (b) v3 쿼리를 T-1 KRX 기준으로 수정 | (c) 거래대금만 근사 보강 (`close × volume`) |
|---|---|---|---|
| 무엇을 한다 | 21:05 키움 슬롯에 `ka10081`(일봉, `upd_stkpc_tp=0`) 1페이지를 종목당 1콜 추가 → 원장 `ka10081_*` 신설 → stage 표 → `price_daily` evening 분기가 OHL·`value_krw` 를 여기서 채움 | v3 `kr_market._resolve`·리서치센터 `_latest()` 가 `basis='krx'` 에 해당하는 **T-1** 을 기준일로 잡도록 고침. 저녁 T 행은 `daily_prices` 에 아예 넣지 않음 | OHL 을 `close` 로, `amount` 를 `close × volume / 1e6`(백만원)로 채워 NOT NULL 을 만족시킴. 값 출처를 `_compat_meta` 에 표시 |
| 살아나는 소비자 | 브리핑 등락 상하위·거래대금 상위·스코어 상위 8 전부 T 기준으로 정상. 리서치센터 S1·S2·S11 정상. drilldown 정상 | 전부 정상 동작하되 **하루 늦은 장**을 본다 (오늘 저녁 장은 다음 날 아침 확정판이 들어간 뒤에야 보인다) | 브리핑 등락 상하위(300억 컷): 실측 오분류 0건. 거래대금 상위 12: 11/12 일치. 리서치 S1·S2 유동성 컷(10억): 1,235종목 중 7건 이탈. S11 거래대금 축: 살아나되 z 값이 근사 |
| 남는 것 | **비용**: ≈2,700콜. 우리 수집기 `RATE = 4.4`콜/초(`src/backfill_kw.py:28`) → **≈10분**. 현재 저녁 슬롯은 ka10060·ka10014 2 TR(≈5,400콜 ≈20분)이라 21:05 시작 → 잠정 빌드 21:20·한도 21:45 와 정면 충돌. **결정 11-(d) 시간표와 D-6(20:15 이동) 을 같이 다시 잡아야 한다**. 앱키는 v3 daily_all(20:05) 이 죽은 뒤라 공유 충돌은 없다. `close` 가 KRX 공식 종가로 바뀌어 ka10060 `cur_prc`(장후 마지막 체결가)와 **어느 쪽을 T 종가 정본으로 할지 재결정**이 필요하다(결정 11-(c) 수정) | **v3 코드 수정 2곳 이상 + 승인**. 그리고 "저녁에 그날 스코어를 낸다" 는 이 프로젝트의 목표와 어긋난다 — 스코어는 T 로 내는데 브리핑만 T-1 이면 07:00 리포트 안에서 등락과 스코어의 기준일이 갈린다 | **OHL 은 여전히 가짜다** — `open=high=low=close` 인 행이 v3 `daily_prices` 에 들어간다. 리서치센터·가설이 OHL 을 읽지는 않지만(감사 결과 §2 에 open/high/low 소비자 0), 표를 읽는 사람이 진짜 OHLC 로 오인한다. `amount` 오차: 중앙값 0.4~0.6% · p90 1.5~2.0% · **최대 44%**(아래 실측) |

**거래대금 근사 오차 실측** (로컬 `price_daily` 판 `m_20260923T002256_209832Z`, `basis='krx'`
행으로 `close × volume_shr` vs 원장 `value_krw` 비교):

| 축 | 값 |
|---|---|
| 일별 상대오차 중앙값 (2026-09-11~09-22, 일 3,806~3,817종목) | 0.40% ~ 0.59% |
| p90 | 1.5% ~ 2.0% |
| 최대 | 18% ~ 44% |
| 거래대금 상위 12 겹침 (09-22) | 11 / 12 |
| 상위 100 겹침 (09-22) | 99 / 100 |
| 브리핑 movers 컷 300억 (09-22, 참 191종목) | 이탈 0 · 추가 0 |
| 리서치 S1/S2 컷 10억 (참 1,235종목) | 이탈 7 · 추가 0 |
| 리서치 S11 평상 유동성 컷 30억 (참 756종목) | 이탈 5 · 추가 1 |

주의: 이 실측은 **KRX 행**(종가 = 15:30 정규장 종가, 거래량 = 애프터마켓 포함)으로 잰 것이다.
저녁 키움 행은 종가가 장후 마지막 체결가라 오차 분포가 다를 수 있다 — 미실측(§6).

### 4-3. 권장안

**(c) 를 M1~M3 의 임시 조치로 쓰고, (a) 를 D-6(시간표 재조정)과 묶어 M4 뒤에 올린다.**

이유:

1. **(b) 는 목표와 충돌한다.** 이 플랜의 목적은 T 저녁에 T 스코어를 내는 것이다. 브리핑만
   T-1 로 되돌리면 같은 리포트 안에서 등락(T-1)과 스코어(T)의 기준일이 갈린다. v3 수정
   범위도 가장 크다(`kr_market._resolve` + 리서치센터 `_latest()` 4곳).
2. **(a) 는 옳지만 지금은 못 넣는다.** ≈10분이 21:05~21:20 창에 안 들어간다. 결정 11-(d)
   시간표와 D-6 프로브(20:15 이동)가 같이 움직여야 하고, ka10081 종가가 들어오면 "저녁 T
   종가 정본" 을 ka10060 장후가에서 KRX 공식 종가로 바꿀지까지 재결정해야 한다. M4 전에
   건드리면 G-M2·G-M3 비교의 축이 흔들린다(D-5 와 같은 논리 — 입력을 바꾸면서 이식하지 않는다).
3. **(c) 의 실측 피해가 작다.** 브리핑이 실제로 쓰는 300억 컷에서 오분류 0건, 거래대금 상위
   12 중 11건 일치. 가장 치명적인 DEFECT-C02(하루 묵은 장 보고)를 막는 최소 조치이기도 하다.
4. (c) 를 쓸 때의 필수 조건 세 가지:
   - `open`·`high`·`low` 에 `close` 를 넣는 것은 **`basis='evening'` 행에만**, 그리고
     `_compat_meta` 에 `daily_prices.ohl_source='evening_close_fill'`·
     `amount_source='close_x_volume'` 를 기록한다. 아침 확정 compat 가 같은 (stock_code,
     trade_date) 를 KRX 실값으로 덮어쓴다(자연 교체 — `INSERT OR REPLACE`).
   - 이 채움이 **우리 equity 표·model 입력으로는 절대 역류하지 않는다**. 원칙 ④(결측은 결측)는
     equity 층의 규칙이고, 여기서 깨는 것은 "v3 스키마가 NOT NULL 을 요구한다" 는 외부 계약
     때문이다. `src/compat/mappings.py` 주석에 이 사유와 만료 조건(= (a) 채택 시 제거)을 적는다.
   - `retire_when` 에 "D-8-(a) ka10081 저녁 수집이 들어오면 제거" 를 단다.

대안으로 **(d) v3 `daily_prices` DDL 의 NOT NULL 을 푸는 마이그레이션**도 있으나, v3 스키마
수정은 승인 대상이고 `price_repo` 가 쓰는 경로와 충돌 위험이 있으며, 풀어도 §4-1 의 "amount
NULL → 섹션 조용히 사라짐" 은 그대로 남는다 — 권장하지 않는다.

**사용자 결정이 필요한 것**: (a)/(b)/(c)/(d) 중 무엇을, 언제.

---

## 5. GAP-2 — `stock_info`(외인지분·베타·52주)

### 5-1. 소비자가 정확히 무엇을 어떻게 읽는가

`backend/research_center/drilldown.py:91-101` 한 곳뿐이다.

```sql
SELECT s.stock_name, s.sector, i.foreign_ownership_pct, i.beta, i.high_52w, i.low_52w
FROM stocks s
LEFT JOIN stock_info i ON i.stock_code = s.stock_code
WHERE s.stock_code = ?
```

출력은 문장 한 줄: `"{이름}({코드}) 섹터 {sector} · 외인지분 {foreign_ownership_pct}% ·
베타 {beta} · 52주 {low_52w}~{high_52w} (스냅샷)"`. 포맷 지정자가 없는 `{}` 라 **NULL 이면
`None` 이라고 찍히고 예외는 나지 않는다**.

부수 결함 하나: `stock_info` 의 PK 는 `(stock_code, snapshot_date)`
(`backend/db/schema.py:134-144`)인데 조인 조건에 `snapshot_date` 가 없다. 종목당 스냅샷이
여러 날 쌓이면 `fetchone()` 이 **어느 날 행을 집을지 비결정**이다. 우리가 이 표를 채운다면
`snapshot_date` 를 한 날짜만 유지하거나(멱등 upsert + 이전 행 삭제) 이 결함을 감수해야 한다.
`market_cap`·`floating_ratio` 는 이 표에서 **아무도 읽지 않는다**(§2-4).

### 5-2. 우리 데이터로 채울 수 있는가

| v3 열 | 우리 원천 | 판정 |
|---|---|---|
| `foreign_ownership_pct` (%) | `flow_daily.foreign_wght_pct` ← 키움 ka10008 `wght_pct`(`src/equity/sql/flow_daily.sql` `foreign_own` CTE, `src/stage/rules_kiwoom.py:141`) | **채울 수 있다**. 단 ka10008 은 저녁이 아니라 **07:10** 수집이라(`scripts/daily_build.sh:104`) 아침 확정 compat 에서만 T 값이 선다. drilldown 은 날짜 축이 없는 스냅샷이라 문제 없다 |
| `high_52w` · `low_52w` (원) | `price_daily.close` 에서 최근 252 세션 max/min 으로 **계산 가능**. 기성 표는 없다 | **파생하면 채울 수 있다**. 단 v3 값은 네이버 원본(미조정 원)이고 우리 계산은 `close`(미조정 원)이라 축은 같다. `adj_close` 를 쓰면 값이 달라진다 — `close` 로 맞춘다 |
| `beta` | **없다.** equity 30표에 베타 표·열이 없다. `price_adj_daily` × `index_daily` 회귀로 만들 수 있으나 그건 새 파생(윈도·지수·수익률 정의를 결정해야 한다) | **못 채운다**(새 파생 결정 필요) |
| `market_cap` (억원) | `price_daily.mktcap_krw / 1e8` | 채울 수 있으나 **소비자 없음** — 채우지 않는다 |
| `floating_ratio` (%) | 정의가 네이버 유동주식비율이다. 우리 `treasury_stock`·`ownership_snapshot`(최대주주·특수관계인 지분)으로 근사할 수는 있으나 **정의가 다르다** | **못 채운다**(정의 불일치) |

참고: 과제 지시에 있던 `ownership_snapshot`·`holder_daily` 는 둘 다 **DART 지분 공시**다 —
`ownership_snapshot`(S15)은 최대주주·특수관계인 지분 현황, `holder_daily`(S15)는 5% 대량보유·
임원 소유상황 스트림이다(`src/equity/sql/*.sql` 머리 주석). **외국인 지분율의 원천이 아니다.**
외국인 지분율의 정본은 키움 ka10008 이고 equity 에서는 `flow_daily.foreign_wght_pct` 다.

### 5-3. 권장

`stock_info` 를 **3열만 채운다**: `foreign_ownership_pct`(`flow_daily.foreign_wght_pct`) ·
`high_52w`·`low_52w`(`price_daily.close` 252 세션 파생). `beta`·`floating_ratio`·`market_cap`
은 NULL 로 둔다 — drilldown 은 `None` 을 그냥 찍고 죽지 않는다. `snapshot_date` 는 한 날짜만
유지해 §5-1 의 비결정을 없앤다. 이것도 사용자 확인 대상이다(플랜 GAP-2 가 "아니면 drilldown
필드 NULL 허용 여부를 결정" 이라고 둔 자리).

---

## 6. 미확인 목록

정적으로 확인하지 못했고 서버·실행 없이는 못 닫는 것들. 추측으로 채우지 않는다.

1. **저녁 `flow_daily` 의 T 행 격리** — §3 의 판정은 `flow_daily.sql` + `trading_calendar.sql`
   + EG17 상한 규칙에서 유도한 것이다. 로컬 equity 사본에는 `e_` 판 파티션이 없어
   (`~/quant-ledger/data/equity/flow_daily/` 에 `v=m_...` 하나뿐) 실제 `_reject/
   reject_reason=off_grid/` 행수를 세지 못했다. **서버에서 저녁 판 1개로 확인해야 한다**
   (`n_reject`·`reject_by_reason` 은 MANIFEST 에 있다).
2. **저녁 키움 행의 거래대금 근사 오차** — §4-2 실측은 KRX 행(15:30 종가)으로 잰 것이다.
   저녁 ka10060 종가(장후 마지막 체결가)와 애프터마켓 포함 거래량 조합에서의 오차는 미실측.
   D-8 을 (c) 로 정한다면 컷오버 전 3일 프로브로 재야 한다.
3. **unitelegram `data/watchlist_peak_card.py` 가 `daily_prices` 의 어느 열을 읽는지** —
   서버 파일이라 스냅샷에 없다. `amount`·`open/high/low` 를 읽으면 §4 의 피해 표가 늘어난다.
4. **unitelegram `kael_sync.py` 의 rsync 시각** — `quant.db` 사본을 언제 가져가는지 모른다.
   compat 의 제자리 upsert(M4)와 rsync 가 겹치면 부분 반영본을 복사해 갈 수 있다.
   (WAL 이라 sqlite 자체는 일관되지만 `quant.db` 파일만 복사하면 `-wal` 이 빠진다.)
5. **v3 `stock_info` 의 실제 행 상태** — `snapshot_date` 가 종목당 몇 날짜 쌓여 있는지
   (§5-1 의 비결정이 실제로 발현 중인지) 서버 DB 조회가 필요하다.
6. **리서치센터 야간이 정말로 크론에 없는지** — 플랜 §1-5 의 조사 결과를 인용했다. 이 문서는
   그것을 재확인하지 않았다. D-8 우선순위가 이 사실에 걸려 있으므로 컷오버 전에 한 번 더
   crontab 을 확인할 것.
7. **`stocks.market` CHECK 제약** — v3 DDL 이 `CHECK(market IN ('KOSPI','KOSDAQ'))`
   (`backend/db/schema.py:7`)다. 우리 유니버스에 KONEX·ETF 등 다른 시장 값이 있으면 그 행은
   삽입이 거부된다. 우리 `security.market` 의 어휘 분포를 T1.2 에서 확인해야 한다.
