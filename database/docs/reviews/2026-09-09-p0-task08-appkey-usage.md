# Task 0.8 — 키움·KIS 앱키 실사용량 실측 (kael-system-v3)

조사일 2026-09-09 (서버 TZ = UTC, 본 문서 시각은 별도 표기 없으면 **KST**).
읽기 전용. API 콜 0. 키·토큰 값 미출력.

---

## 0. 결론 요약

| 항목 | 값 | 판정 |
|---|---|---|
| v3 키움 실사용 (5거래일 평균) | **약 12,700콜/일** | 문서상 "카엘이 20,000 전량 소진" 은 **사실이 아님**. 여유 ≈ 7,300 |
| v3 + 우리 증분 10,410 | **약 23,100콜/일** | **20,000 초과** (+15.5%) → **시각 분리로는 불충분** |
| v3 KIS 실사용 | **11콜/일** (uapi 10 + tokenP 1) | + unitelegram(같은 앱키) 추정 20~40 |
| KIS + 우리 증분 2,600 | 약 2,650콜/일 | 실측 무오류 선례 204,235 대비 **1.3%. 문제 없음** |
| 키움 토큰 8005 | **매일 06:00·07:00 에 실제 발생 중** (자동 복구) | 앱키 1개 = 활성 토큰 1개 구조 |

---

## 1. v3 가 키움·KIS 를 호출하는 지점

콜 로그 테이블은 **없다**. `data/quant.db` 의 `pipeline_runs` 는 단계별 start/finish/rows 만 기록하고 콜 카운터는 코드 어디에도 없다(`grep call_count|api_call|request_count` 0건).
httpx 요청 로그는 **briefing·insight 만** 남는다 — `backend/pipeline/__init__.py` 가 `logging.basicConfig` 을 부르지 않아 root 가 WARNING 이고, 그래서 일일 파이프라인 12,000+ 콜은 로그에 한 줄도 안 남는다(`pipeline.log` 의 httpx 2,016줄은 전부 insight 몫).

### 키움

| 호출부 | 파일 | 종목당 콜 | 비고 |
|---|---|---|---|
| `stock_master` | `backend/pipeline/daily_pipeline.py:235-256` | — | ka10099 ×2 (KOSPI/KOSDAQ). `cont-yn=N` 실측(원장 `master_daily.py:41`) → **2콜** |
| `daily_prices` | `backend/pipeline/collectors.py:47-88` | **2** | `get_daily_ohlcv(max_pages=1)` = ka10081 1콜 + `get_stock_price` = ka10001 1콜 |
| `investor_flows` | `backend/pipeline/collectors.py:96-132` | **3** | `get_investor_detail` = ka10059, `for page in range(3)` (`client.py:206`) |
| `adj_prices` | `scripts/_backfill_mode_helpers.py:198-229` | ≤3 (결측분만) | 현재 `daily_prices` 에서 `adj_close` 결측 종목 = **2개** → 약 4콜 |
| `insight_pipeline` | `backend/insight/collector.py:172-188` | — | chart 4 + mrkcond 1 + sect 7 = **12콜** (로그 실측) |
| `briefing_morning` | `backend/briefing/theme.py:134-135` | — | sect **3~4콜** (로그 실측) |

`investor_flows` 3콜/종목은 3중 근거로 확정:
1. 코드 `for page in range(3)` + cont-yn 페이지네이션
2. v3 자체 견적 `scripts/_backfill_modes.py:41-42` — `if mode in {"adj_prices","investor_detail"}: return stock_count * 3`
3. **소요시간 비율** — `investor_flows / daily_prices` 가 5거래일 내내 1.485~1.503 ≈ 3/2 (아래 §2)

### KIS

| 호출부 | 파일 | 실제 콜 |
|---|---|---|
| `holiday_check` | `daily_pipeline.py:117-135` → `clients/kis/holiday.py:170-184` | **0** — `data/.kis_holidays.json` 이 `{"year":2026,...}` 연 단위 캐시라 항상 히트. 20초 소요는 재시도 `sleep(10)` ×2 뿐 |
| `calendar_refresh` | `scripts/refresh_market_calendar.py:33` | **0** — 같은 연 캐시 히트 |
| `stock_master` MST | `clients/kis/mst_parser.py:6` | **0** — `https://new.real.download.dws.co.kr/common/master` 정적 zip, appkey 헤더 없음 |
| `research` 휴장게이트 | `backend/research/pipeline.py:29` | **0** — 캐시 히트 |
| `briefing_morning` 해외 | `backend/briefing/collectors/us_market.py:26-61` | **10** — 지수/환율 5(`inquire-daily-chartprice`) + watchlist 5(`price-detail`) |

---

## 2. 최근 5거래일 일별 콜 수

### 근거 데이터 (원본)

`sqlite3 ~/kael-system-v3/data/quant.db "SELECT job_name, started_at, finished_at, rows_affected FROM pipeline_runs …"` (시각은 UTC, +9 = KST)

| KST일 | 유니버스 N | daily_prices 소요 | investor_flows 소요 | 비율 | 적재행 |
|---|---|---|---|---|---|
| 09-02 (수) | 2,534 | 1,020s | 1,515s | 1.485 | 12,665 / 12,665 |
| 09-03 (목) | 2,533 | 1,070s | 1,599s | 1.494 | 12,580 / 12,640 |
| 09-04 (금) | 2,534 | 1,014s | 1,508s | 1.487 | 12,661 / 12,661 |
| 09-07 (월) | 2,534 | 1,014s | 1,506s | 1.485 | 12,662 / 12,662 |
| 09-08 (화) | 2,533 | 1,014s | 1,524s | 1.503 | 12,663 / 12,658 |

`SyncRateLimiter(max_requests=5, window=1.0)` (`clients/kiwoom/rate_limiter.py`) 기준 5.0req/s.
`daily_prices` 1,014s × 5 = 5,070 ≈ 2,534×2 = **5,068** — 오차 0.04%. 레이트 리밋에 정확히 붙어 돈다는 뜻이고, 종목당 콜 수 추정이 옳음을 뒷받침한다.

### 키움 일별 콜 (콜 수는 코드×유니버스로 **산출**, 로그 실측분은 별도 표기)

| KST일 | stock_master | daily_prices | investor_flows | adj_prices | insight ※ | briefing ※ | **합계** |
|---|---|---|---|---|---|---|---|
| 09-02 | 2 | 5,068 | 7,602 | ~4 | 12 | 27 | **≈12,715** |
| 09-03 | 2 | 5,066 | 7,599 | ~4 | 12 | 27 | **≈12,710** |
| 09-04 | 2 | 5,068 | 7,602 | ~4 | 12 | 16 | **≈12,704** |
| 09-07 | 2 | 5,068 | 7,602 | ~4 | 12 | 3 | **≈12,691** |
| 09-08 | 2 | 5,066 | 7,599 | ~4 | 12 | 4 | **≈12,687** |

※ = httpx 로그 실측 (`~/logs/kael-v3/pipeline.log`·`briefing.log` 의 `INFO httpx: HTTP Request: POST https://api.kiwoom.com/…` 줄을 KST 로 시프트해 집계). 나머지 열은 **추정**(로그 부재 — §1 의 종목당 콜 × 유니버스, 소요시간 비율로 교차검증).
briefing 이 09-04 부터 27 → 3~4 로 준 것은 12:15 KST 슬롯과 rkinfo/slb/chart 수집이 그 무렵 빠졌기 때문. 총량 영향은 무시할 수준.

**5거래일 평균 ≈ 12,701콜/일. 토큰 발급 1~2콜 별도.**

### KIS 일별 콜 (전량 로그 실측)

| KST일 | uapi (overseas) | tokenP | 합계 |
|---|---|---|---|
| 09-02 | 10 | 1 | 11 |
| 09-03 | 10 | 1 | 11 |
| 09-04 | 10 | 1 | 11 |
| 09-07 | 10 | 1 | 11 |
| 09-08 | 10 | 1 | 11 |

**같은 KIS 앱키를 쓰는 제3자**: `~/unitelegram/.env` 의 `KIS_APP_KEY` 가 v3 것과 **동일**(sha256 앞 12자리 일치 확인, 값 미출력). unitelegram 은 httpx 로그를 남기지 않아 실측 불가 — 보유 8종목(`data/portfolio.db::positions`) NAV 조회 + 지수 시세를 크론 4~5개(15:35·15:40·12:15·08:00·16:00 KST)가 부르므로 **일 20~40콜 추정**.
`~/quant-ledger` 는 KIS 토큰 캐시 mtime 이 08-31 로 정지 — 현재 KIS 미사용.

---

## 3. 호출 시간대 분포 (KST) — 우리 06:00 체인과의 겹침

서버 크론은 UTC. `5 11 * * 1-5` = 20:05 KST.

| 시각(KST) | 주체 | 채널 | 콜 |
|---|---|---|---|
| **06:00:01~06:00:03** | quant-ledger `master_daily.py` | 키움 | 2 (+토큰 1회 강제재발급) |
| 06:00~06:05 | quant-ledger `backfill_wise.py` | WISE (앱키 무관) | 15,580 |
| **07:00:0x** | v3 `briefing_morning` | 키움 3~4 / KIS 10 | (+토큰 각 1) |
| 20:05:01~20:05:22 | v3 `holiday_check` | — | 0 (캐시) |
| 20:05:22~20:05:25 | v3 `stock_master` | 키움 | 2 |
| **20:05:25~20:22:19** | v3 `daily_prices` | 키움 | **≈5,068** |
| **20:22:19~20:47:30** | v3 `investor_flows` | 키움 | **≈7,602** |
| 20:47~21:51 | v3 `consensus` | WISE (앱키 무관) | 0 |
| 21:51~22:15 | v3 `adj_prices` | 키움 | ~4 |
| 22:15 | v3 `insight_pipeline` | 키움 | 12 |

**시각 자체는 안 겹친다.** v3 키움 대량 구간은 20:05~20:47 이고, 06:00 창에는 원장 2콜뿐이다.
다만 우리 증분 10,410콜을 원장 페이싱(`api.py:118` `time.sleep(0.25)` = 4req/s)으로 돌리면 **06:00~06:43**, v3 5req/s 기준이라도 **06:00~06:35** — 07:00 브리핑까지 여유 17~25분이다. 좁다.

---

## 4. 토큰 캐시 · 키움 8005

### 앱키 공유 실태 (해시 대조, 값 미출력)

| .env | KIS_APP_KEY | KIWOOM_APP_KEY |
|---|---|---|
| `~/kael-system-v3/.env` | `3c0f4076d48b…` | `e793c91c0259…` |
| `~/unitelegram/.env` | `3c0f4076d48b…` (동일) | 없음 |
| `~/quant-ledger` | 자체 `.env` 없음 — `src/api.py:8` 이 `~/kael-system-v3/.env` 를 직접 읽음 (동일 키) |

**토큰 캐시는 3벌로 갈라져 있다.**

| 캐시 파일 | 소유 | mtime (KST) | 만료 계산 |
|---|---|---|---|
| `~/kael-system-v3/data/.kiwoom_token.json` | v3 | 2026-09-09 07:00:06 | `expires_in` 기본 79,200s(22h) — 키움이 `expires_in` 을 안 주므로 v3 는 항상 22h 로 가정 |
| `~/kael-system-v3/data/.kis_token.json` | v3 | 2026-09-09 07:00:02 | 86,400s |
| `~/quant-ledger/src/.kw_token.json` | 원장 | 2026-09-09 06:00:01 | 서버가 준 `expires_dt` = 2026-09-10 15:00 (33h) |

### DEFECT-A: 키움 앱키 1개에 활성 토큰이 1개뿐 — 두 저장소가 서로의 토큰을 매일 무효화한다

- **상황**: 키움 앱키 1개를 v3(`data/.kiwoom_token.json`)와 quant-ledger(`src/.kw_token.json`)가 **독립 캐시**로 들고 있다. 매일 원장이 06:00, v3 가 07:00 에 각각 첫 키움 콜을 쏜다.
- **인풋**:
  1. 06:00:01 — 원장 `master_daily.py` 가 캐시 토큰(전일 06:00 발급, `exp` 는 아직 9시간 남음)으로 ka10099 호출
  2. → `return_code=3 / 8005 Token이 유효하지 않습니다`
  3. `src/api.py:118-124` 가 강제 재발급 후 1회 재시도 → 성공. 캐시 mtime 이 매일 06:00:01 로 갱신되는 것이 그 흔적
  4. 07:00:0x — v3 `briefing` 이 sect 호출(HTTP 200) → `return_code=3` → `_post_with_retry` 의 `AuthError` 경로(`clients/kiwoom/client.py:104-110`)로 `oauth2/token` 재발급 후 재시도
- **에러 위치**: `~/quant-ledger/src/api.py:112-124` 와 `~/kael-system-v3/backend/clients/kiwoom/client.py:83-118` — 양쪽 다 **재시도로 조용히 복구**하므로 아무 데도 에러로 기록되지 않는다.
- **재현 흔적 (로그 실측)**:
  - `~/quant-ledger/logs/daily_wise_0902.log:2-4` — 재시도가 없던 시절의 미복구 사례:
    `✖ kospi 실패 — return_code=3 msg=인증에 실패했습니다[8005:Token이 유효하지 않습니다] rows=0` (kospi·kosdaq 둘 다, 그날 마스터 스냅샷 0행)
  - `~/logs/kael-v3/briefing.log` — 09-07·09-08·09-09 07:00 세 날 모두 동일 패턴:
    `POST /api/dostk/sect "200"` → `POST /oauth2/token "200"` → `POST /api/dostk/sect ×3`
    (첫 sect 가 8005 를 받아 강제 재발급된 것. 재발급이 먼저 오지 않고 sect 뒤에 오는 순서가 증거)
- **위험성**: 지금은 양쪽 다 1회 재시도로 자동 복구되어 데이터 손실이 없다. 그러나 **우리 증분(10,410콜, 06:00~06:43)을 얹으면 창이 길어져** 07:00 v3 브리핑 재발급이 우리 증분 **한가운데** 떨어진다. 그 시점 이후의 콜은 전부 8005 를 맞고, 원장 `api.py` 의 재시도는 **콜마다 재발급**을 시도하므로 토큰 발급 API 자체가 레이트 리밋에 걸릴 수 있다. 2026-09-02 처럼 **`rc=0` + 0행으로 조용히 스킵**되면 그날 증분이 통째로 유실된다(플랜 §4 도입부가 이미 지목한 `master_daily.py:39-42` 의 조용한 스킵).

### 그 밖의 429

`~/logs/kael-v3/pipeline.log` 의 `429 rate limited, waiting …` 를 KST 일자별로 집계:
09-01 1건 · 09-02 2건 · 09-03 2건 · 09-04 2건 · 09-08 1건 (직전 30일 최대 4건/일).
12,700콜 중 1~4건 = **0.03% 미만**. 현재 키움 레이트 리밋 여유는 충분하고, 일일 한도 근처라는 신호는 없다.
`DailyLimitExceeded`(연속 429 10회) 발생 이력 0건.

---

## 5. 결론

### (a) 키움 — `v3 + 10,410` 이 20,000 을 넘는가 → **넘는다**

```
v3 실측       12,701 (5거래일 평균)
원장 master        2
우리 증분     10,410
────────────────────
합계          23,113   > 20,000  (+15.6%)
```

`COLLECT_PLAN.md:11` 의 "카엘이 이미 검증 한도 20,000콜을 전량 소진한다" 는 **과대 추정**이다(실측 12,701, 여유 7,299). 하지만 그 여유로도 10,410 은 못 담는다. `COLLECT_PLAN.md:237` 의 "키움 단독 11,060 + 카엘 20,000 = 31,060" 도 실측 기준으로는 **23,761** 로 정정된다 — 결론(앱키 분리 필요)은 그대로다.

### (b) KIS — `v3 + 2,600` 이 문제인가 → **아니다**

```
v3               11
unitelegram   20~40 (추정)
우리 증분     2,600
────────────────────
합계         ~2,650   vs 실측 무오류 선례 204,235  → 1.3%
```
KIS 는 **앱키 분리 불필요**. 단 토큰은 v3·unitelegram·우리가 각자 캐시를 들 것이므로 발급 빈도만 유의(KIS 는 tokenP 재발급을 분당 1회로 제한).

### (c) 권고

**1순위 — 키움 앱키 분리를 R5 의 하드 선행조건으로 확정.** (a) 가 명확히 초과이고, 시각 분리로는 일일 **총량** 문제를 못 푼다. 발급 요청을 Task 0.8 Step 2 대로 진행.

**2순위 — 분리가 늦어질 때의 대안 (플랜 R5 옆에 병기 권장).**
v3 `investor_flows` 의 3페이지 중 **2·3페이지는 버려진다**: `collectors.py:104-107` 가 `records[:5]` 만 쓰는데 `client.py:206` 은 3페이지를 다 긁는다. 1페이지로 줄이면 **일 5,068콜 절감** →
```
v3  12,701 − 5,068 = 7,633
+ 우리 증분 10,410  = 18,043  < 20,000  ✅
```

**ka10059 페이지 크기 — 직접 실측은 못 했으나(읽기 전용, 콜 금지) 정황 근거는 강하다:**
- `scripts/_backfill_modes.py:37-42` 의 `estimate_calls` 가 `investor_detail` 을 **`days` 와 무관하게 `stock_count * 3`** 으로 잡는다. 바로 위 `flows` 는 `stock_count * days` 다. 즉 v3 는 **ka10059 3페이지가 최대 1년치(`backfill.py:172` 기본 `--days 365`)를 덮는다고 전제**하고 설계했다 — 페이지당 100행 이상이라는 뜻.
- `run_investor_detail_backfill`(`_backfill_modes.py:48-78`)이 종목당 `get_investor_detail(code, end_key)` **한 번**만 부르고 반환 행을 `start_key..end_key` 로 필터링하는 구조도 같은 전제 위에 있다.
- 공식 스펙(`docs/reference/kiwoom_rest_api/sheets/ka10059.tsv`)에는 페이지 크기 명시가 없다. 응답 예시는 2행이지만 문서용 축약이다.

→ 페이지당 행수가 5 이상이면 **일일 경로의 2·3페이지는 100% 낭비**다. 확정에는 검증 콜 1건이면 충분하다. 이건 v3 저장소 수정이므로 카엘 쪽 합의가 필요하다.

**3순위 — 앱키 분리 여부와 무관하게 반드시 해야 할 것: 토큰 캐시 단일화 또는 8005 취급 강화.**
분리하면 DEFECT-A 는 자연 소멸한다. 분리 전까지 우리 증분을 돌린다면
- 증분 러너는 8005 를 **조용히 스킵하지 말 것** (플랜 §4 도입부 규약대로 rc 2 + `crit`)
- 증분 창을 **06:00~06:40 안에 끝내고** 07:00 v3 브리핑과 최소 20분 버퍼 확보
- 토큰 재발급은 러너 시작 시 **1회 선제 발급**으로 몰고, 콜마다 재발급하지 않도록 상한을 둘 것

**시각 분리만으로는 불충분하다** — 시간대는 이미 안 겹치는데(§3) 문제가 총량이기 때문이다.

---

## 부록 — 재현 명령

```bash
# 파이프라인 단계별 시각·행수 (콜 수 산출의 기반)
ssh kael-server 'cd ~/kael-system-v3 && sqlite3 -header -column data/quant.db \
  "SELECT job_name, started_at, finished_at, status, rows_affected
   FROM pipeline_runs WHERE started_at >= \"2026-09-01\" ORDER BY run_id;"'

# 로그에 남는 키움/KIS 실콜을 KST 일자로 집계 (briefing·insight 분)
ssh kael-server 'python3' <<'PY'
import re, datetime, collections
pat = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")
agg = collections.defaultdict(collections.Counter)
for path in ("/home/kael/logs/kael-v3/briefing.log", "/home/kael/logs/kael-v3/pipeline.log"):
    for line in open(path, errors="ignore"):
        m = pat.match(line)
        if not m or ("api.kiwoom.com" not in line and "openapi.koreainvestment.com" not in line):
            continue
        t = datetime.datetime.strptime(" ".join(m.groups()), "%Y-%m-%d %H:%M:%S") + datetime.timedelta(hours=9)
        k = ("kw_token" if "kiwoom.com/oauth2" in line else "kw_api" if "kiwoom.com" in line
             else "kis_token" if ":9443/oauth2" in line else "kis_api")
        agg[t.strftime("%Y-%m-%d")][k] += 1
for d in sorted(agg): print(d, dict(agg[d]))
PY

# 429 일자별
ssh kael-server 'awk "/^[0-9]{4}-[0-9]{2}-[0-9]{2}T/{d=substr(\$1,1,10)} /429 rate limited/{c[d]++} END{for(k in c) print k,c[k]}" \
  ~/logs/kael-v3/pipeline.log | sort'

# 8005 흔적
ssh kael-server 'grep -rh "8005" ~/quant-ledger/logs/*.log'
ssh kael-server 'grep -n "oauth2/token" ~/logs/kael-v3/briefing.log | tail -5'
```
