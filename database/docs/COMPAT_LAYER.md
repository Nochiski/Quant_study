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
`ka10008`(외국인 보유)은 다음 날 07:10 이고(`scripts/daily_build.sh:118`, 결정 7),
`ka20068`(대차)은 06:00 이다.

**QL-D(10-10) 뒤**: compat 은 equity 판의 T 행을 쓰지 않는다. `--basis evening` 의 `daily_prices`·
`investor_detail_flows` T 행은 원장 두 개(① `postclose.db` 15:41~16:00 `price_valid='1'` → ② `kiwoom.db`
21:05 T 행)에서 만든다(`src/compat/t_rows.py`). 아래 '판정' 열은 equity 판 기준(원래 근거), 'QL-D 뒤' 열이
v3 에 들어가는 값이다. 서버 재생(T=10-08, 21:05 경로만 — 장 마감 원장이 없던 날)은 v3 10-08 사본과 종가
2,529/2,529·수급 12열 2,532/2,532 일치, 거래량 19종목 차이, compat 에만 74종목(신규 스팩 T-25 등)이었다.

| v3 열 | 우리 저녁 T 원천 | 판정 | QL-D 뒤 |
|---|---|---|---|
| `daily_prices.open`·`high`·`low` | **없다** — `price_daily.sql` 의 evening 분기가 `NULL AS open_krw, NULL AS high_krw, NULL AS low_krw` 로 명시 고정(원칙 ④). ka10060·ka10014 에 OHL 필드 자체가 없다 | **결측**. 게다가 v3 DDL 이 `open/high/low/close/volume INTEGER NOT NULL`(`backend/db/schema.py:16-27`) 이라 **NULL 을 넣을 수 없다**(§4 DEFECT-C01) | 종가로 채운다(T-32 = D2-9 (c)). 다음 날 아침 KRX 행으로 날짜 단위 교체 |
| `daily_prices.close` | `price_daily.close` ← ka10060 `cur_prc`(`rules_kiwoom.py:55`) | **있다**. 단 정의가 다르다 — KRX 공식 종가(15:30)가 아니라 **장후 마지막 체결가**(결정 11-(c)) | postclose 행 = KRX 공식 종가(정규장, T-33). 16:00 을 넘겨 21:05 원장으로 채운 종목만 장후 마지막 체결가 |
| `daily_prices.volume` | `price_daily.volume_shr` ← ka10060 `acc_trde_prica`. 이름은 "누적거래대금" 이지만 **실측은 거래량(주)** — KRX `ACC_TRDVOL` 과 99.9864% 일치(`rules_kiwoom.py:57-58` 주석) | **있다** (애프터마켓 포함 — KRX 와 같은 정의, 결정 11-(a)) | postclose 행 = 16:00 전까지(애프터마켓 미포함), 21:05 원장 행 = 애프터마켓 포함 |
| `daily_prices.amount` (백만원) | **없다**. 저녁 키움 4 TR 어디에도 거래대금 필드가 없다: ka10060 의 `acc_trde_prica` 는 위처럼 거래량이고, ka10014 의 `shrts_trde_prica_krw` 는 **공매도 거래대금만**(`rules_kiwoom.py:104-105`), ka20068 `remn_amt_krw` 는 대차잔고 금액이다 | **결측 · 원장에 대체 필드 없음** | 종가 × 거래량 ÷ 1e6 근사(T-32). 오차는 KRX 행으로만 쟀다(§4-2) — **21:05 원장 행(장후 체결가 × 애프터마켓 포함 거래량)의 오차는 미실측**(§6-2) |
| `daily_prices.adj_close` | `price_adj_daily.adj_close` — T 행 존재(전방 조정, `price_adj_daily.sql` 이 evening 행을 그대로 싣는다) | **있다** | T 종가 그대로(T-40 — T 가 최신 행). 사슬 끝에 원장 T 단계 `ks_T = D' 종가 ÷ (종가_T − 전일대비_T)` 를 붙여(T-41 보완 — I-1: 키움 기준가 = KRX 기준가) 사건일 종목의 D'−3..D' 덮어쓰기·창 안 adj_close·창 밖 다시 맞춤이 그날 저녁 v3 20:05 결과와 같다. 전일대비·D' 종가가 없으면 단계를 모른다 → T 행 adj_close NULL, 창 행은 D' 기준 — 다음 날 아침 KRX 반영이 맞춘다(§7) |
| `stocks.market_cap` (억원) | `price_daily.mktcap_krw` 는 evening 행에서 **NULL**(`shares_out` NULL). B-24 규칙(T-1 KRX `shares_out` × T 키움 `close`)으로 파생해야 한다 — 플랜 §3-2 가 `src/model/inputs.py` 한 곳에 두기로 한 규칙 | **파생 필요**(규칙은 이미 결정, 구현은 T2.2) | QL-D 범위 밖 — D' 값 그대로 |
| `investor_detail_flows` 13열 | 원장·stage 에는 있다(`stg_flow_daily_kiwoom` T 행). 그런데 equity `flow_daily` 의 격자는 `universe_daily` = `trading_calendar` × `security_span` 이고, `trading_calendar` = `stg_index_daily`(KRX 지수) distinct date 라 **저녁에는 T 가 캘린더에 없다**. 그래서 T 의 ka10060 행은 `flow_daily.sql` 세 번째 분기로 가 `reject_reason='off_grid'`(`_reject/reject_reason=off_grid/`)에 격리된다 | **equity 산출 표에 T 행 없음** | 원장에서 12열(내외국인 `natfor` 는 자리 없음). 가격과 같은 종목 원천 |
| `investor_detail_flows` T-1 이전 | `flow_daily` measured 행 | **있다** | 그대로 |

요약: 저녁 잠정판에서 v3 `daily_prices` 의 **open·high·low·amount 가 결측이고 원장에 대체
필드가 없다**. `close`·`volume`·`adj_close` 는 있다. `stocks.market_cap` 은 파생 가능.
`investor_detail_flows` 의 T 행은 원장에는 있으나 equity 표에는 없다.
**QL-D 뒤**: 두 표의 T 행은 원장에서 만들어 v3 에 들어간다 — OHL·amount 는 T-32 채움, 종가는 KRX 공식
종가(postclose)가 기본이다. 새 원천에 그날 행이 0 이면 날짜 단위 교체를 하지 않고 멈춘다.

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

**구현 상태(10-10, 컷오버 QL-D · T-32 확정)**: v3-merge v2 D2-9 권고대로 (c) 를 넣었다 — 장 마감 판
T 행은 OHL = 종가, amount = 종가 × 거래량이다(`src/compat/t_rows.py`). v3 에서 "같은
상황"은 확인되지 않는다: 로컬 v3 사본(2025-01-02~2026-08-07, 971,487행)에 O/H/L = 0 행이 0건이고(필드가 비면
0 을 넣는 `collectors._int(None)` 경로가 실제로 타지 않았다), 거래 없는 날 행은 open=high=low=close 다(32,277행).
D2-9 (a)(저녁 ka10081)가 들어오면 `t_rows.py` 의 채움만 바꾼다.

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
| `foreign_ownership_pct` (%) | `flow_daily.foreign_wght_pct` ← 키움 ka10008 `wght_pct`(`src/equity/sql/flow_daily.sql` `foreign_own` CTE, `src/stage/rules_kiwoom.py:141`) | **채울 수 있다**. 단 ka10008 은 저녁이 아니라 **07:10** 수집이라(`scripts/daily_build.sh:118`) 아침 확정 compat 에서만 T 값이 선다. drilldown 은 날짜 축이 없는 스냅샷이라 문제 없다 |
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

1. ~~**저녁 `flow_daily` 의 T 행 격리**~~ — QL-D 뒤 compat 은 T 수급 행을 equity `flow_daily` 가
   아니라 원장에서 만든다(§3). 격리 행수 확인은 compat 에 더는 필요 없다(equity 층 자체의 사실로만 남는다).
2. **21:05 원장 행의 거래대금 근사 오차** — §4-2 실측은 KRX 행(15:30 종가)으로 잰 것이다. QL-D 뒤
   v3 에 근사 `amount` 가 들어가는 행은 postclose 행(KRX 공식 종가 × 16:00 전 거래량)과 21:05 원장 행(장후
   마지막 체결가 × 애프터마켓 포함 거래량) 둘이고, **둘 다 오차 미실측**이다(T-32 로 근사는 확정). 그림자
   3일에 v3 ka10081 `trde_prica` 와 대조해 잰다. 다음 날 아침 KRX 실값으로 바뀌므로 영향은 그날 저녁~
   다음 날 아침(07:00 브리핑 포함)이다.
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

---

## 7. v3 대비 의도된 차이(재생 대조 범주)

대조 도구 = QL-G(`scripts/v3_replay.sh`, 대조기 `python -m compat.v3_replay` — 아래 항목과 범주 코드 이름의 대응은 `src/compat/v3_replay.py` `CATEGORIES` 의 근거 열).

v3 날짜별 사본과 compat 반영본을 대조할 때 차이로 나오지만 고치지 않는 것이다. 여기 없는 차이는 결함 후보로 본다.

판정 범위(T-45): 결함 후보(rc)로 세는 것은 **매일 도는 소비자가 읽는 열**(§2-1 crontab · §2-2 unitelegram — 표만 적거나 '등'으로 적은 곳은 그 표 전 열)의 차이뿐이다. §2-3 리서치센터(crontab 밖 수동)만 읽는 열은 `manual_consumer`, 읽는 곳이 없는 표·열(§2-4 의 `consensus_annual`·`financial_summary` 전부, `stocks.listed_date`·`delisted_date` 등)은 `no_consumer` 로 수만 기록한다. 표.열 목록의 정본은 `v3_replay.DAILY_CONSUMER`·`MANUAL_CONSUMER` 다. 옛 날짜를 `--allow-current-builds` 로 재생해 현판으로 대체한 원천 표가 있으면, 그 표가 원천인 v3 표·열(`v3_replay.fallback_columns` — 매핑의 원천 표, 열 대응을 모르면 표 전 열)의 차이는 `builds_fallback` 으로 수만 기록한다(그 뒤 판의 값이 섞인다 — 서버 10-01: security 대체로 078130 등 개명 3건).

- **신규 스팩 — compat 에만 있다(v3 누락 교정)**: v3 `stocks` 에는 옛 숫자코드 스팩 117개뿐이고, 그 뒤 상장한 스팩(2024-02-01~2026-09-22 상장, 숫자·영숫자 코드 모두)이 없다. compat 은 `sec_type='spac'` 이면 싣는다(10-01·06·07·08 재생에서 71종목, 10-10 판정).
- **점수 종목 수 — compat 이 적다(유니버스 결정, T-17)**: 하루 행이 `score_history` 1,329 → 593(scope@1.0), `score_history_v2` 2,526 → 625(v2_percentrank@1.0)로 준다. 10-05 '추정치 보유 종목만' 결정을 받아들인 것이다(QL-C). 점수는 유니버스 안 표준화라 공통 종목도 값 크기가 다르다. 그래서 공통 종목끼리 순위(Spearman)로 대조한다. `score_history.val_ev_ebitda` 는 늘 NULL 이다. scope 가 EV/EBITDA 를 밸류에 쓰지 않기 때문이고, v3 도 거의 비어 있었다.
- **단위 환산 반올림 ±1(`ROUNDED`)**: compat 이 원 → 백만원·억원으로 바꾸며 반올림한 정수 열(수급 12열 · `stocks.market_cap` · 컨센서스 eps·bps · `financial_summary` 억원 열·주식수)은 ±1 을 같다고 본다. 그 밖 정수·문자 열은 정확히 같아야 하고, 실수 열은 부동소수 잡음(상대 1e-9)만 허용한다.
- **`stocks.updated_at` 은 대조하지 않는다**: 값이 아니라 쓰기 시각이다(v3 `datetime('now')` 자리에 compat 은 export 시각).
- **`stocks.market_cap` 종가 정의(T-45 ① · T-33)**: 함의 주식수(시총 ÷ 그날 종가)가 같고 억원 반올림 ±1 이다 — compat 시총은 KRX 공식 종가, v3 시총은 애프터마켓 종가로 센 값이다. compat 쪽 종가는 시총을 고른 equity 행(D 이하 마지막 KRX 행), v3 쪽은 v3 사본의 D 종가다(서버 10-08 사본 증분 재생: 시총 차이 1,823 중 1,809 + 반올림 1건 335870).
- **상장폐지 반영 시점(T-45 ②)**: compat 은 KRX 폐지일(`delisted_date ≤ D`)로 `is_active=0`·시총·폐지일을 두고, v3 는 아직 `is_active=1`·시총 NULL 이다 — v3 지연(서버 10-08: 196490, 한쪽 NULL 시총의 폐지 종목). v3 에만 있는 가격·수급 행 중 compat 폐지일 ≤ 행 날짜 ≤ D 인 것(v3 가 폐지 당일·뒤에 쓰는 거래량 0 행 — 서버 196490 10-07 · 084180 10-01)도 여기다.
- **v3 그날 수집 누락(T-45 ③)**: v3 가 아는 종목(v3 `stocks` 에 있거나 그날 앞 v3 `daily_prices` 행이 있음)의 그날 행이 v3 에 없고 KRX equity `price_daily` 에 있다 — compat 에만 있는 가격·수급 행과 그 종목의 v3 시총 NULL(서버 10-08: 035290·066430·088280). v3 이력 시작 앞 날짜는 여기 들지 않는다. v3 에 그날 행은 있는데 `stocks.market_cap` 만 v3 NULL·compat 값(KRX 시총 행 근거)인 것은 `v3_missing_value` — v3 값 결측을 compat 이 채운 것이다(서버 009770·011760 10-07, 057030·060150 10-08).
- **장 마감 판 T 행 — 원천·채움이 다르다(QL-D, N-42 Q3, T-32·T-33)**: `--basis evening` 의 `daily_prices`·`investor_detail_flows` T 행은 원장에서 만든다(규칙 정본 `src/compat/t_rows.py`). 종목마다 원천이 하나이고(두 표가 같은 선택) `_compat_meta.tables` 의 표별 `t_rows` 에 남는다(`postclose`·`kiwoom_2105` 수, 21:05 원장으로 대체한 종목, 행 없는 종목과 그 비율 `missing_ratio` — 기록형).
  - `postclose` 행(15:41~16:00, `price_valid='1'` · 종가 > 0 · 거래량 있음 — `daily.kw_daily.ka10060_postclose_price_usable_sql`, fi 장 마감 판 PR-5 와 같은 술어): 종가 = KRX 공식 종가(정규장, T-33), 거래량·수급 = 16:00 전까지. v3(20:05 ka10081·ka10059)는 애프터마켓까지 포함한 값이라 다르다.
  - `kiwoom_2105` 행(21:05 키움 원장 — 16:00 을 넘긴 종목, `price_valid` 가 '0'·NULL 이거나 종가가 비었거나 0 이하·거래량이 빈 종목): 지금 v3 와 같은 뜻(애프터마켓 포함)이다. 과거 T 재생(장 마감 원장이 없던 날)은 전 종목이 이 경로다.
  - `open`·`high`·`low` = 종가, `amount` = 종가 × 거래량 ÷ 1e6(근사 — 오차는 §6-2 미실측), `adj_close` = T 종가(T 가 최신 행 — 아래 daily_prices 항목). 사건일(기준가 ≠ D' 종가)이어도 원장 기준가(`daily.kw_daily.ka10060_base_price_sql`)로 T 단계를 알면 값이 있다. 전일대비·D' 종가가 없어 단계를 모를 때만 NULL — 그날 저녁~다음 날 아침 동안만이다.
  - 대상 = D' 유니버스 이월이라 T 당일 신규 상장 종목은 없다(v3 는 그날 ka10099 에 있으면 싣는다).
  - 다음 날 아침 `--basis morning --date T` 가 T 행을 날짜 단위로 KRX 행으로 바꾼다 — 아침 KRX 에 없는 종목의 저녁 행도 지워진다. 아침 판에 그날 행이 0 이면 지우지 않고 멈춘다. 반대로 대상에 이번(T, 장 마감)보다 나중 반영 기록(T-35 순서 — 날짜가 뒤이거나 같은 날 아침, 판정은 `v3_post._newer` 한 곳)이 있으면 장 마감 판이 멈춘다(재생은 `--allow-older`).

- **`daily_prices` — v3 가 쌓던 모양 그대로(QL-E · T-18 · T-40 · T-41)**: 식 정본은 `src/compat/mappings.py` daily_prices 주석이다.
  - **adj_close = KRX 기준가 사슬 K(T-40)**: `ks(e) = 전일 종가 ÷ 기준가`(다를 때, 아니면 1), `adj_close(d) = 종가(d) × K(d) ÷ K(L)`, L = 그 종목의 마지막 행. v3(키움 ka10081 수정주가)가 곧 K 사슬이다 — 로컬 v3 08-07 사본 932,265행 중 1원·0.15% 밖 0행(리뷰 재현, 원천은 equity `price_daily` 종가·기준가). equity `price_adj_daily` 계수는 쓰지 않는다(기준가 무변화 날 접기·정지 해제 재평가 없음·호가 반올림이 키움과 다르다). equity·fi·모델의 전방 조정은 그대로다(T-3).
  - **시·고·저·종가·거래량 = v3 '최근 5행 덮어쓰기'(T-41)**: v3 는 매일 ka10081 최근 5행을 그날 기준 수정값으로 덮는다. 그래서 행 d 는 `c = min(d 뒤 4번째 행, L)` 기준값이다 — 가격 = 원값 × K(d)/K(c), 거래량 = 원값 × K(c)/K(d), 거래대금은 원값(로컬 사본: 덮인 행의 거래대금은 원값 82% · 조정값 45% 일치). 사건이 없으면 원값이다. 07:00 브리핑이 원종가로 등락률을 세면 사건일에 가짜 급등락(011930 병합 +900% 등)이 뜨므로 지키는 기존 동작이다. 장 마감 판 T 행은 T-32 채움 그대로(T 가 최신 행 — c = T).
  - **장 마감 판 T 단계(T-41 보완, QL-E 재리뷰 MAJOR-A)**: equity 판의 사슬은 D' 에서 끝나므로 원장 T 행의 단계 `ks_T = D' 종가 ÷ 기준가_T`(기준가_T = 종가_T − 전일대비_T, `daily.kw_daily.ka10060_base_price_sql` — I-1: 키움 기준가 = KRX 기준가 사건일 4,219/4,219)를 사슬 끝에 붙인다(`compat.t_rows.STEP_TABLE`). 사건일 종목의 D'−3..D' 행(d 뒤 4번째 행이 T)이 그날 저녁 덮어써져 07:00 브리핑 등락률이 KRX 수익률이 된다. 다음 날 아침 KRX 반영은 같은 단계를 KRX 기준가로 다시 세어 같은 값을 쓴다(멱등). 전일대비나 D' 종가가 없을 때만 단계를 모른다 — T 행 adj_close NULL, 창 행은 D' 기준.
  - **창 밖**: 창 안에 기준가 단계가 든 v3 종목(`V3_STOCK_FILTER`)은 대상의 창 밖 옛 행도 다시 쓴다 — adj_close = equity 원종가 × K(d)/K(L)(대상 close 는 덮어쓰기·옛 백필 값일 수 있어 곱하지 않는다), 시·고·저·종가·거래량 = T-41 값(자가 복구, MINOR-1). 거래대금·행 수는 그대로이고, equity 행이 없는 날은 시·고·저·종가·거래량을 두고 adj_close 만 NULL 이다. 기록은 `_compat_meta.tables.daily_prices.rebase`(종목·다시 쓴 행 수·NULL 수) — NULL 이 0 이 아니면 `v3_post.sh` 가 warn 한 줄을 남긴다(정지 아님). 대조 범주는 `rebase_adj_null` 이다(기록의 종목·창 시작 앞 행이고, adj_close 만 compat NULL 이며 나머지 열은 같다).
  - **창 하한과 한계**: 제자리 반영(`--in-place`)은 창이 5거래일(`MIN_WINDOW_SESSIONS` = 덮어쓰기 4행 + 1, `daily.calendar`) 이상이어야 한다 — 아니면 쓰기 전에 멈춘다(rc 2). 매일 증분 창(K1-9d — as_of 이하 마지막 거래일과 그 앞 `INCREMENTAL_PRIOR_SESSIONS` = 10거래일, 11세션. 08:10 KRX 재수집 창 `daily_build.sh` krx_step `prev_trading_day(D, n=10)` ~ D 와 같아 재수집으로 고친 날이 다음 증분에 모두 들어간다. 정본은 `ledger_health.KRX_RECHECK_SESSIONS`. 판정 달력으로 세고, 달력을 못 읽으면 멈춘다)은 늘 넘는다 — 이 가드가 실제로 막는 것은 `--window-days` 가 짧은 `--full` 이다. 반영이 며칠 끊겨 사건 직전 행이 창 밖으로 밀려도, 사건 단계가 창 안에 있는 동안의 다음 반영이 위 자가 복구로 그 행들을 바로잡는다. 못 잡는 경우는 둘이다: ① 반영이 끊긴 사이 사건 단계까지 창 밖으로 나간 경우(공백 > 창 길이) ② equity `price_daily` 원값(종가·기준가)이 판 상향으로 바뀐 경우 — K 는 그 원값이 바뀌지 않는 한 equity 판본과 무관하다. 둘 다 `--full` 로 맞춘다. 배포 직후의 그림자 대상(`data/compat/quant.db` — 그때까지 전방 조정 값)도 `--full` 1회가 필요하다. v3 본 파일은 첫 반영(V3-C)이 `--full` 이다.
  - **대조 기준**: 가격·거래량·adj_close 는 ±1 또는 0.15%(키움이 조정값을 원·주 단위로 반올림한다), 거래대금은 ±1. 대조 도구는 QL-G(`compat.v3_replay` — 행 분류 `_price_verdict`)다. 남는 차이(수치는 서버 10-08 사본 `--full` 재실행 뒤 채운다):
    1. **T-33 종가 정의** — 09-14 애프터마켓 연장 뒤 전 거래일. v3 종가는 애프터마켓 마지막 체결가, compat 은 KRX 공식 종가라 수준이 아니라 adj/close 비(= K(d)/K(L))와 거래량으로 대조한다. (서버 10-08 사본 `--full` 10-10: 31,203행 — 사건 5,435·무사건 25,768, 비율·거래량 기준 결함 후보는 아래 6 의 17행뿐)
    2. **v3_backfill** — v3 옛 일괄 백필이 행 d 를 d+4 보다 뒤 날 기준 수정값으로 덮은 행(로컬 08-07 사본 34,339행·217종목). adj_close 는 같다. V3-C `--full` 뒤에는 v3 과거 OHLCV 가 T-41 규칙값으로 바뀐다(의도 — 소비자가 보는 과거 시·고·저·종가·거래량이 달라진다). (서버 수치: 재실행 뒤)
    3. **v3 adj_close NULL · GAP-4 후보** — v3 adj_prices 가 못 채운 행·옛 기준에 남은 종목. `v3_defect`. (서버 수치: 재실행 뒤)
    4. **v3 오류일 2026-03-27** — 그날 v3 종가가 전 종목에서 틀리다. `v3_defect`. 행 수는 가격·거래량·adj 가 다른 행에 거래대금만 다른 행을 더해 센다(로컬 08-07 사본 2,442 + 89 = 2,531행. 서버 수치: 재생 뒤). 오류일은 등록된 날(`V3_BAD_DAYS`)만 범주로 인정한다 — 대조기가 탐지한 다른 날은 보고서 `bad_days_unregistered` 에 싣고 결함 후보로 둔다.
    5. **장 마감 판 T 단계 미상 NULL** — MAJOR-A 로 사건일 NULL 은 사라지고, 원장 전일대비(또는 D' 종가)가 없는 종목의 그날 저녁~다음 날 아침 T 행 adj_close 만 남는다.
    6. **v3 사본 기준일 행 = 20:05 수집 시점 값** — v3 는 다음 날 최근 5행을 다시 받아 기준일 행을 덮는다. 애프터마켓 거래가 많은 대형주의 거래량·거래대금이 최종값과 다를 수 있다(서버 10-08 사본: 기준일 10-08 대형주 17행 — 005930 v3 20,157,893 대 KRX 최종 21,259,056). 기준일 행은 다음 거래일 사본으로 대조한다(10-09 는 휴장이라 v3 가 덮지 않았음 — 10-12 사본으로 확정 예정). 저가 차이(000660)는 T-33 정의 차이일 수 있어 여기 원인으로 적지 않는다.
    7. **v3 과거 수정주가 다음 날 갱신(`v3_adj_next_day`)** — 사건 뒤 v3 adj_prices 가 과거 행 adj_close 를 다음 날 고친다. D 사본과는 다르고(09-14 전 `pre_mismatch`·뒤 `T33_ratio_or_volume` 으로 떨어진 행) 다음 거래일 사본의 같은 행과는 맞으면 이 범주다(서버 378800 2025-09-11: 10-01 사본 adj 2,094 · compat 10,468 · 10-02 사본 10,468). 다음 거래일 사본이 없으면 adj_close 만 다른 행(나머지 열은 맞음)을 `v3_adj_next_day_unconfirmed` 로 두고 rc 를 막지 않는다 — 기준일 행(6)과 같은 방식.

---

## 8. v3 quant.db 제자리 반영 — `scripts/v3_post.sh` (QL-F·QL-F2)

compat 을 v3 파일에 직접 돌리지 않는다. compat 은 표마다 따로 커밋하고(반영 도중 소비자가 일부 표만 바뀐 상태를 읽는다, G9) 필수 열이 빈 행을 5% 까지 건너뛴다(G11). 제자리 반영은 아래 순서로만 한다. 범위·게이트의 정본은 `src/compat/v3_post.py` 머리 주석이다.

1. **스테이징**: v3 quant.db 를 sqlite 온라인 백업으로 임시 파일에 뜬다(`python -m compat stage`). 본 파일은 읽기 전용으로 연다. 스테이징 경로가 본 파일(링크·-wal/-shm 포함)과 같은 파일이면 거부한다(뜨기 전에 그 경로를 지우므로). 디스크 여유가 본 파일 크기 × 2 보다 작으면 뜨지 않고, 백업이 실패하면 부분 사본을 지운다.
2. **반영 표**(T-34 · T-38, `python -m compat v3-tables`): 기본 9표.
   - `--basis morning` 은 본 파일 `_compat_meta` 에 같은 D 의 **점수 두 표를 반영한** 장 마감(evening) ok 기록이 있으면 점수 두 표를 뺀 7표다. 저녁에 보낸 엑셀과 v3 DB 점수가 같고, 가격 재반영이 아침 모델 판 실패에 묶이지 않는다. 그런 기록이 없으면 아침 모델 판 점수를 쓴다(T-7 대체 발송과 같은 뜻). 점수 표를 반영했는지는 기록의 `tables` 열(compat 이 쓴 표 → 결과 json) 키로 본다.
   - `--no-scores`(`--basis evening` 전용, T-38)는 늘 7표다. 21:05 원장 뒤 재반영(refill)은 늘 이 모드다 — 점수는 장 마감 반영(⑥)만 쓴다. compat 이 점수 표를 고르지 않으므로 장 마감 판이 없는 날(판 실패일·세션 예외일 T-26)에도 돈다. 이 기록엔 점수 표가 없어 위 판정이 세지 않으므로, 그날 ⑥ 의 점수 포함 기록이 있으면 다음 날 아침은 7표, 없으면 아침 모델 판으로 점수를 채운다. 순서(T-35)는 (D, evening) 그대로다. 아침이나 daily_post 명령과 함께 주면 인자 오류(rc 5, 파이썬·CLI 층도 거부)다 — export_scores 를 그날 점수 없이 부르지 않는다.
3. **compat export `--in-place --tables <반영 표>`** 를 스테이징에 돌린다(QL-B 가드 — `stocks.market_cap` 은 `all`). 장 마감 판(`--basis evening`)이면 T 행 원장 두 개(`QL_POSTCLOSE_DB` 기본 `data/raw/postclose.db` · `QL_KIWOOM_DB` 기본 `data/raw/kiwoom.db`)를 넘기고, `--allow-older` 는 apply 와 함께 export 에도 넘긴다(compat 장 마감 판이 같은 T-35 순서로 먼저 멈춘다 — QL-D).
4. **게이트**(COMMIT 전):
   - 이번 compat 기록 1행·status ok·날짜·basis 일치·창 끝 = D
   - 순서(T-35): 본 파일에 이번보다 (날짜, basis — 같은 날은 아침 > 장 마감) 가 큰 ok 반영 기록이 없다. 재생은 `--allow-older`. **새 정지 조건이라 사용자 확인 대기**
   - 복원 뒤 첫 반영(QL-I · T-42): 본 파일 `_compat_meta` 의 마지막 복원 기록(basis `restore` — `scripts/v3_restore.sh` 가 남긴다. 기본 복원은 점수 두 표를 뺀 7표) 뒤에 ok 반영 기록이 아직 없으면 compat 기록이 `--full` 이어야 한다. 그 앞 반영 기록은 순서(T-35)·아침 7표(2, T-34) 판정에서 빠진다. 그림자(`--shadow`)는 보지 않는다. **새 정지 조건이라 사용자 확인 대기**. 되돌리기 절차는 [`CUTOVER_ROLLBACK.md`](CUTOVER_ROLLBACK.md)
   - 반영 표 전부 · 필수 열 빈 행을 건너뛴 수 0(그림자 compat 의 5% 허용을 제자리에서는 0 으로, P1)
   - 기록에 반영 표 밖의 표가 없다 — 기록은 본 파일에 그대로 옮겨지고 2 의 아침 판정이 그 표 목록을 본다. 옮기지 않은 점수 표가 기록에 남으면 다음 날 아침이 점수를 건너뛴다(QL-F2)
   - 신선도(T-31 ③): compat 이 이번에 `daily_prices` 에 **쓴** trade_date = D 행 ≥ 1(`tables.daily_prices.metrics.n_on_date`). 스테이징은 본 파일 사본이라 'D 행 있음' 만으로는 옛 행에도 참이 된다. 비율 하한은 두지 않는다
   - 표마다 반영 범위 행 > 0(점수 두 표는 `score_date = D`)
5. **한 트랜잭션 반영**: `ATTACH` → `BEGIN IMMEDIATE` → 표별 범위 `DELETE` → 스테이징 범위 `INSERT` → `_compat_meta` 기록 1행 → `COMMIT`.

| 표 | 반영 범위(= compat 이 쓴 범위) |
|---|---|
| `daily_prices` · `investor_detail_flows` | `trade_date` ∈ 이번 기록의 창 `[from_date, to_date]`. `daily_prices` 는 여기에 **창 밖 다시 맞춘 종목**(기록의 `tables.daily_prices.rebase.tickers`)의 `trade_date < from_date` 행을 더한다 — 창 안에 KRX 기준가 단계가 든 v3 종목의 창 밖 행(OHLCV·adj_close, QL-E — §7 daily_prices). 그중 adj_close 를 NULL 로 둔 행(`rebase_null`)이 있으면 warn 한 줄 |
| `score_history` · `score_history_v2` | `score_date = D` |
| `stocks` · `consensus_revision_daily` · `consensus_revision_compare` · `consensus_annual` · `financial_summary` | 표 전체(날짜 창 없는 as-of 스냅샷 — 스테이징이 같은 락 안의 사본이라 compat 이 안 건드린 행은 같은 값으로 다시 들어간다) |

compat 이 쓰지 않는 표(`market_*`·`pipeline_runs`·`research_reports` 등, T-27)는 건드리지 않는다. 락은 v3 체인과 같은 `/tmp/kael_v3_daily_all.lock` 이고 `--shadow`(1~4단계만, 본 파일 무변경, 스테이징 기본 경로 `staging_<basis>[_noscores]_shadow.db`)는 락을 잡지 않는다. 기본 스테이징 경로는 점수 없는 반영에 `_noscores` 를 붙인다 — refill 이 그날 ⑥ 이 실패해 남긴 스테이징을 지우지 않게. 실물 v3 `stocks` 는 `delisted_date` 가 마이그레이션으로 `updated_at` 뒤에 붙어 열 순서가 `v3_schema.sql` 과 다르다 — compat 스키마 검사는 열 이름·타입으로 본다(순서 무관).

`daily_prices` 의 O/H/L 이 비고 종가가 있는 krx 행은 비어 있는 칸을 종가로 채운다(정지 참고가 행 · 정규장 체결 없이 시간외만 있던 날 — 서버 `--full` 게이트 실측 145210 2025-03-21, v3 사본 같은 행 O=H=L=C). 근거는 `mappings.py` `_REF_FILL` 주석.

**호출 형태(PR-8 장 마감 체인 — QL-F2 연결 지점).** T = 그날 거래일, T' = T 의 직전 거래일, D = 아침 재반영 대상 거래일(전날). 판은 인계 이력으로 고정한다(`--builds-from` — PR-8 리뷰 MAJOR 와 같다).

| 시점 | 명령 |
|---|---|
| 장 마감 체인 ⑥ | `scripts/v3_post.sh --date T --basis evening --v3-db <v3 quant.db> --model-root data/model_db/model --builds-from data/deliver/history/<T'>_morning.json [--shadow] [--v3-post-cmd <daily_post>]` (점수 포함 9표 — 점수는 여기서만 쓴다) |
| refill(21:05 키움 원장 커밋 뒤) | `scripts/v3_post.sh --date T --basis evening --v3-db <v3 quant.db> --no-scores --builds-from data/deliver/history/<T'>_morning.json [--shadow]` (늘 — ⑥ 결과와 무관, compat 만 7표, `--model-root` 불필요) |
| 다음 날 아침 | `scripts/v3_post.sh --date D --basis morning --v3-db <v3 quant.db> --builds-from data/deliver/history/<D>_morning.json [--shadow]` (compat 만 — 7표·9표는 T-34 가 본 파일 기록으로 정한다) |

다음 날 아침 반영 표: 그날 ⑥ 이 점수 포함 반영을 COMMIT 했으면(뒤에 daily_post 만 실패한 rc 6 포함) 7표, ⑥ 이 실패했거나 없었으면(판 실패·세션 예외일) 9표다 — refill 기록은 판정에 들어가지 않는다. PR-8 `postclose_chain.sh` 의 세 호출(`close_main` ⑥ · `refill_main` · `morning_main`)이 위 표 그대로다 — refill 은 ⑥ 결과와 상관없이 늘 `--no-scores`(셸 테스트가 실물 v3_post.sh 로 인자 계약을 본다).

### 8-1. v3 쪽 변경 목록(V3-A~E — 컷오버 날, 백업 뒤, N-42 Q4)

줄 번호는 v3 로컬 사본(25dd56b, 08-30) 기준이다. 서버 파일과 다를 수 있으니 적용 직전에 grep 으로 다시 찾는다.

| ID | 파일·위치 | 변경 |
|---|---|---|
| V3-A | `scripts/job_runner.py:54`(`CHAINS["daily_all"]` 의 닫는 `],` 뒤) | 체인 둘을 더한다(T-31). `"daily_post": [("holiday_gate", 1, 30, True), ("export_scores", 1, 120, False)]` · `"daily_insight": [("holiday_gate", 1, 30, True), ("insight_pipeline", 1, 300, False), ("wiki_ingest", 1, 3600, False), ("wiki_lint", 1, 3600, False)]`. `calendar_refresh` 는 넣지 않는다(KIS 를 불러 `.kis_holidays.json` 을 덮는다 — 휴장 파일 정본은 `daily.calendar_export`, T-24). `--chain` 선택지는 CHAINS 키에서 자동으로 생긴다. 백업 `job_runner.py.bak.<ts>` |
| V3-A | 서버 crontab | `daily_insight` 를 지금 daily_all 자리(`5 11 * * 1-5` UTC = 20:05 KST)에 둔다. 락은 **별도 파일** `/tmp/kael_v3_daily_insight.lock` 이다 — 공유 락 `/tmp/kael_v3_daily_all.lock` 에 `flock -n` 을 쓰면 v3_post 가 락을 쥔 순간 insight 가 조용히 건너뛰어져 T-27 장애(그날 국면·다음 날 07:00 브리핑 지수 없음)가 재발한다. `daily_post` 는 크론에 두지 않는다 — v3_post.sh 가 `--v3-post-cmd` 로 부른다(v3 crontab 줄에서 flock 만 뺀 형태: `cd "$HOME/kael-system-v3" && source "$HOME/.local/bin/env" && export $(grep -v "^#" .env | xargs) && PYTHONPATH=. .venv/bin/python scripts/job_runner.py --chain daily_post`) |
| V3-B | 서버 crontab daily_all 줄(`5 11 * * 1-5 … flock -n /tmp/kael_v3_daily_all.lock … --chain daily_all` — 로컬 사본 `scripts/cron_schedule.sh:3` 에 같은 줄) | 줄을 지운다(`crontab -l` 백업). 퀀트 단계(`job_runner.py:46-49` daily_pipeline·adj_prices·scoring·scoring_v2)만 빼고 남기면 export(텔레그램)가 daily_post 와 두 번 돈다. `CHAINS["daily_all"]` 코드는 되돌리기용으로 둔다(되돌리기 = 줄 복원) |
| V3-C | quant-ledger `scripts/v3_post.sh` | 첫 반영(K3-2): QL-I 백업 2벌(`scripts/v3_backup.sh` — 이 표의 대상 파일 사본도 함께 뜬다, `CUTOVER_ROLLBACK.md` §1) 뒤 `scripts/v3_post.sh --date <D> --basis morning --v3-db "$HOME/kael-system-v3/data/quant.db" --full` 1회(`QL_V3_LOCK_FILE` 은 비운다). 아래 운영 메모 |
| V3-D | 서버 crontab v3 휴장 쓰기 2줄 | `refresh_year_holidays`(`0 20 29 12 *`)·`monthly_holiday_review`(`0 0 1 * *`) — v3 `docs/system-guide/survey/08-ops.md:136·157`. QL-Q 연결과 같은 날 끈다(T-24) |
| V3-E | unitelegram `sources/kael_db.py` `get_signal_insights`(서버 전용 파일 — 줄은 서버에서 확인) | 점수 조회에 `score_date = (SELECT max(score_date) FROM score_history)` 조건(T-17 — 593 유니버스 밖 종목의 옛 v3 행이 종목별 최신 행으로 잡히지 않게) |

**V3-C 운영 메모.** `--full`(730일 창)은 서버에서 v3 쓰기 락을 30초 남짓 쥔다(서버 10-08 사본 `--full` 은 게이트 실패까지 전체 27.6초라 이동 자체는 아직 서버 미실측 — 로컬 465MB 사본에서 730일 이동 트랜잭션 21.8초). v3 연결의 busy_timeout 은 5초다(`backend/db/connection.py`). 그래서 v3 가 quant.db 에 쓰는 시각 — 07:00·12:15·15:35 브리핑(job_runner 가 `pipeline_runs` 를 쓴다)과 20:30 리서치·21:00 브로커 리서치 수집 — 을 피해 돌린다. 매일 증분 반영은 서버 실측 7.5초다(K1-9d 전 14달력일 창 — 평시 11세션으로 지금 창과 같다. 연휴가 낀 주만 지금 창이 더 길다). 서버 v3 프로세스는 UTC 다(v3 로그 시각이 크론 11:05 UTC 와 같아 `.env` 에 TZ 지정이 없다고 판단) — v3 의 `date.today()` 는 KST 09:00 전까지 전날이다.

### 8-2. 그림자 날 대조(QL-G)

그림자(`POSTCLOSE_V3` ≠ `in-place`)에서는 `v3_post.sh --shadow` 가 본 파일에 쓰지 않고 스테이징만 남긴다(기본 경로 `data/_v3_post/`, 같은 단계가 다음에 돌 때 지우고 새로 뜬다).

| 파일 | 담는 것 |
|---|---|
| `staging_evening_shadow.db` | ⑥(장 마감 체인 끝, 16시대) 때의 본 파일 사본 + compat 9표(점수 포함) |
| `staging_evening_noscores_shadow.db` | refill(21:05 원장 커밋 뒤) 때의 본 파일 사본(v3 20:05 결과가 이미 들어 있다) + compat 7표. 점수 두 표의 T 행은 v3 자기 점수다 |
| `staging_morning_shadow.db` | 다음 날 아침 KRX 재반영(T 행을 KRX 확정값으로 바꾼 상태 — 그날 v3 사본과 대조할 상태가 아니다) |

컷오버 뒤 다음 날 03:00 v3 사본이 담을 compat 상태는 **⑥ 점수 + refill 7표** 다. 그래서 refill 스테이징의 점수 두 표 T 행을 ⑥ 스테이징의 T 행으로 바꾼 파일을 그날 v3 사본(`quant_<T>.db`)과 대조한다(`python -m compat.v3_replay shadow-merge` — 범위는 반영과 같은 `v3_post._scope`, 두 스테이징은 읽기만). ⑥ 이 실패해 점수 포함 기록이 없는 날은 합치지 않고 멈춘다(rc 2 — 그날 v3 점수는 다음 날 아침 T-34 가 채운다). 순서: 다음 날 03:00 v3 사본 보존 뒤, 다음 ⑥ 이 스테이징을 덮기 전, 운영 체인 밖에서. 원본·스테이징은 sqlite 로 직접 열지 않고 운영 루트 밖 임시 폴더로 복사해 쓴다(스테이징 옆에 `-wal` 이 있으면 아직 쓰는 중이다).

```
W=$(mktemp -d) && cd "$W"
cp ~/quant-ledger/data/_v3_post/staging_evening_shadow.db ev.db
cp ~/quant-ledger/data/_v3_post/staging_evening_noscores_shadow.db rf.db
cp <v3 사본 폴더>/quant_<T>.db v3.db
export PYTHONPATH=~/quant-ledger/src PYTHONDONTWRITEBYTECODE=1 PY=~/quant-ledger/.venv/bin/python
"$PY" -m compat.v3_replay shadow-merge --evening ev.db --refill rf.db --date <T> --out compat.db
"$PY" -m compat.v3_replay compare --compat-db compat.db --v3-db v3.db --date <T> \
  --equity-root ~/quant-ledger/data/equity [--next-v3-db <다음 거래일 사본의 복사본>] --json v3_replay.json
```

옛 날짜 재생(`scripts/v3_replay.sh --allow-current-builds`)은 대체 판 조합에 따라 export 가 실패할 수 있다(판 스키마 차이 — 서버 10-02: 현판과 옛 판이 섞여 `security` 의 `name_abbrv_current` 열이 없다). 그날은 대조 불가다.

대조기는 compat 기록의 basis(evening)로 T 행을 장 마감 판 범주(§7 — `evening_t_*`)로 나누고, 21:05 원장 종목의 T 행은 다음 거래일 사본이 있으면 기준일 행(§7 daily_prices 6)으로 판정한다. equity 판은 compat 기록의 `equity_builds`(장 마감 판은 D' 아침 판)를 읽으므로 그 판이 보관 판 안에 있을 때 돌린다.
