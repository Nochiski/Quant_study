# EQUITY_HANDOFF — equity 층 인계 (S23, 2026-09-06)

이 문서는 **equity 층을 넘겨받는 사람이 처음 30분에 읽을 것**이다. 설계의 정본은
`EQUITY_DESIGN.md`(테이블 카탈로그·뷰·소비자 계약), 게이트의 정본은 `EQUITY_GATES.md`,
슬라이스 순서·DoD 의 정본은 `EQUITY_WORKFLOW.md`, 필드 대응의 정본은 `EQUITY_FIELD_MAP.md` 다.
여기 적힌 것은 **그 넷을 실제로 돌리는 방법과, 돌리다 막혔을 때 어디를 보는가** 뿐이다.
숫자는 전부 2026-09-06 서버(`kael-server:~/quant-ledger`) 실측이며 근거는 DESIGN §10 P17~P42 다.

---

## 1. 한 문장

`data/stage/` 의 원장 parquet 을 읽어 **28개 equity 표**(팩트·차원 25 + 선언표 3 —
`declaration_table=True` 는 `universe_policy`·`dataset_profile`·`factor_readiness`)를 짓고,
`equity.duckdb` 카탈로그(매크로 8)와 워크벤치 어댑터(`equity_duckdb`, 필드 30)를 통해
백테스트 파이프라인에 point-in-time 관측을 공급한다.

- 코드: `database/src/equity/`
- 산출: 서버 `~/quant-ledger/data/equity/<table>/v=<build_id>/…` + `MANIFEST.json`
- 소비자: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/`(5포트)

---

## 2. 28표와 빌드 의존 순서

아래 순서는 각 `EquityTable.inputs` 의 위상 정렬이며, `database/scripts/equity_rebuild_all.sh` 의
`ORDER` 와 **같은 문자열**이다(둘이 갈리면 스크립트가 아니라 이 표를 고칠 것).
"선행"은 equity 표 입력만 적었다 — stage 입력 수는 괄호 안.

| # | 표 | grain | 파티션 | 선행(equity) | stage 입력 | 서버 행수 |
|---|---|---|---|---|---|---|
| 1 | `trading_calendar` | date | whole | — | 1 | 4,094 |
| 2 | `corp` | corp_code | whole | — | 2 | 3,478 |
| 3 | `security` | ticker | whole | — | 5 | 5,088 |
| 4 | `security_span` | ticker, span_seq | whole | — | 3 | 5,090 |
| 5 | `corp_ticker` | ticker | whole | — | 3 | 5,088 |
| 6 | `index_daily` | index_class, index_name, date | date_axis | — | 1 | 347,821 |
| 7 | `price_daily` | ticker, date | date_axis | 1 | 3 | 10,890,251 |
| 8 | `corp_event` | event_id | receipt_axis | 1·4·5 | 5 | 3,147 |
| 9 | `adj_factor` | ticker, effective_date, event_id | date_axis | 1·3·4·7·8 | 1 | 5,733 |
| 9b | `price_adj_daily` | ticker, date | date_axis | 4·7·9 (+1) | 0 | 10,890,251 |
| 10 | `universe_daily` | date, ticker | date_axis | 1·3·4·7·9 | 3 | 10,890,251 |
| 11 | `universe_policy` | policy, rule_seq | whole | 10 | 0 | 13 |
| 12 | `flow_daily` | date, ticker, src | date_axis | 1·10 | 4 | 9,201,516 |
| 13 | `short_daily` | date, ticker | date_axis | 1·10 | 5 | 9,201,516 |
| 14 | `credit_daily` | date, ticker | date_axis | 1·7·10 | 2 | 9,201,516 |
| 15 | `disclosure_version` | rcept_no | receipt_axis | — | 4 | 198,163 |
| 16 | `fin_std` | corp_code, period_end, report_code, fs_div, vintage_kind | receipt_axis | 2·15 | 3 | 93,986 |
| 17 | `holder_daily` | rcept_no, repror, src | receipt_axis | 2 | 2 | 54,864 |
| 18 | `ownership_snapshot` | corp_code, bsns_year, reprt_code, nm, stock_knd | receipt_axis | 2 | 1 | 175,726 |
| 19 | `audit_opinion` | corp_code, bsns_year, reprt_code, bsns_year_label | receipt_axis | 2 | 1 | 74,275 |
| 20 | `shares_outstanding` | corp_code, bsns_year, reprt_code, se | receipt_axis | 5·7 | 1 | 71,044 |
| 21 | `treasury_stock` | corp_code, bsns_year, reprt_code, acqs_mth1~3, stock_knd | receipt_axis | — | 1 | 260,980 |
| 22 | `dividend_event` | corp_code, bsns_year, reprt_code, stock_knd | receipt_axis | — | 1 | 54,939 |
| 23 | `consensus_daily` | ticker, obs_month, target_period, metric, src | date_axis | 4 | 2 | 145,316 |
| 24 | `opinion_daily` | ticker, obs_date, src | date_axis | 3·4 | 3 | 256,537 |
| 25 | `opinion_broker_daily` | ticker, fetched_date, broker, opinion_date | date_axis | 3·4 | 1 | 9,717 |
| 26 | `dataset_profile` | field_id | whole | **1~25 + 9b 전부(26표)** | 0 | 72 |
| 27 | `factor_readiness` | factor_id | whole | **26 + 그 26표** | 0 | 54 |

**의존 그래프의 실질 형태**

```
stage ─┬─ trading_calendar ─┬─ price_daily ─┬─ corp_event ─ adj_factor ─┬─ price_adj_daily
       │                    │               │  (corp_ticker·span)       ├─ universe_daily ─┬─ universe_policy
       │                    │               │                           │                  ├─ flow_daily
       ├─ corp ─┬─ holder/ownership/audit   │                           │                  ├─ short_daily
       │        └─ fin_std ← disclosure_version                         │                  └─ credit_daily
       ├─ security · security_span · corp_ticker · index_daily          │
       ├─ treasury_stock · dividend_event                               │
       ├─ shares_outstanding ← corp_ticker·price_daily                  │
       └─ consensus_daily · opinion_daily · opinion_broker_daily ← span ┘
                                                                        └─► dataset_profile ─ factor_readiness
```

- **9b `price_adj_daily`** = 전방 조정 OHLCV 저장본(S23). `price_daily`·`adj_factor`·`security_span`·`trading_calendar` 를 읽고 워크벤치 `price.adj_close` 가 여기서 나온다 — 카탈로그 매크로가 아니라 **표**라 카탈로그가 낡아도 산다.
- **26·27 은 전 표를 읽는다.** 어떤 표든 다시 지으면 `dataset_profile`·`factor_readiness` 도
  다시 지어야 커버율·준비도가 맞는다.
- `universe_daily` 를 다시 지으면 그 아래 격자 3표(12·13·14)와 `universe_policy` 가 전부 따라온다.
- `dataset_profile` 은 **`universe_daily` 격자를 커버율 분모로 쓴다** — 이 표가 60초 걸리는 이유다.

---

## 3. 서버에서 돌리는 법

전제: `ssh kael-server`, 코드 루트 `~/quant-ledger`, venv `.venv`.
**서버 출력은 반드시 `export LC_ALL=C` 로 읽어라** — 한글 UTF-8 이 섞인 게이트 detail 을
로케일 없이 grep/cut 하면 깨진다.

### 3-1. 코드 배포

```bash
# 로컬 저장소 → 서버 (equity 만. src/stage 는 stage 세션이 main 에서 배포한다 — 건드리지 말 것)
rsync -rq --delete --exclude '__pycache__' database/src/equity/ kael-server:~/quant-ledger/src/equity/
scp database/scripts/run_equity.sh kael-server:~/quant-ledger/scripts/run_equity.sh
```

배포가 맞았는지 확인(경로 목록의 정렬 순서는 로케일 때문에 다를 수 있으니 파일별 해시를 본다):

```bash
ssh kael-server "cd ~/quant-ledger/src/equity && find . \( -name '*.py' -o -name '*.json' -o -name '*.sql' \) \
  | grep -v __pycache__ | sed 's#^\./##' | sort | xargs sha256sum | awk '{print \$2, \$1}' | sort"
```

### 3-2. 표 하나 빌드

```bash
ssh kael-server
cd ~/quant-ledger
scripts/run_equity.sh <table> --threads 3 --memory-limit 8GB
```

`run_equity.sh` 가 `QL_HOME`·`PYTHONPATH` 를 고정하고 `/usr/bin/time -v` 로 RSS·초를 재며
`flock -n /tmp/quant_ledger_equity.lock` 으로 **직렬화**한다(RAM 15GB, 격자 표는 5~7GB 를 쓴다).
락이 잡혀 있으면 exit 3 으로 즉시 빠진다 — 병렬 빌드는 시도하지 마라.

### 3-3. 28표 전량 재빌드(의존 순서)

```bash
# 배포 1회 — 러너는 저장소에 산다
scp database/scripts/equity_rebuild_all.sh database/scripts/equity_manifest_row.py \
    kael-server:~/quant-ledger/scripts/
ssh kael-server "cd ~/quant-ledger && chmod +x scripts/equity_rebuild_all.sh && \
    nohup bash scripts/equity_rebuild_all.sh pass1 > logs/equity/rebuild_pass1_driver.log 2>&1 &"
# 진행: tail -f logs/equity/rebuild_pass1_driver.log
# 결과: logs/equity/rebuild_pass1/summary.tsv  (table, rc, 초, build_id, content_hash, n_rows,
#                                              rules_version, 게이트)
#       logs/equity/rebuild_pass1/<table>.log  (표별 원문)
#       logs/equity/rebuild_pass1/STATUS       (완주하면 TOTAL <초>, 실패하면 FAILED <table>)
# 2회차: 같은 명령에 pass2 — 두 summary.tsv 의 content_hash 열을 비교한다
```

한 표라도 rc≠0 이면 즉시 멈춘다(뒤 표는 어차피 깨진 입력 위에 지어진다).
서버 실측 소요는 **409초**(P42, 27표 기준 2회 모두) + `price_adj_daily` 58초(P43). 두 번 돌려 `summary.tsv` 의 `content_hash` 열을
비교하는 것이 재현성 검사다 — 아래 §7 ⑦ 의 EG5a 한계 때문에 게이트만 믿으면 안 된다.

커밋된 28표를 **빌드 없이 재판정**만 하려면(baseline 상수를 바꾼 뒤 확인용):

```bash
scp database/scripts/equity_gate_all.sh kael-server:~/quant-ledger/scripts/
ssh kael-server "cd ~/quant-ledger && bash scripts/equity_gate_all.sh"
# 결과: logs/equity/gate_all/summary.txt (표별 rc·fail 수·게이트 상태)
```

표별 게이트 `metrics` 를 통째로 뽑아 baseline 근거로 쓰려면
`database/scripts/equity_gate_metrics.py`(서버에서 `data/equity` 를 읽어 JSON 한 덩이).

### 3-4. 카탈로그(뷰 매크로 8) · 소비자 계약(EG-C 6항)

```bash
export QL_HOME=$HOME/quant-ledger PYTHONPATH=$HOME/quant-ledger/src
cd ~/quant-ledger

# 뷰 재생성 + EG11(뷰 결정성)·EG5c(as-of 불변)·EG3-P05 — 빌드·GC 뒤에는 반드시 돌린다
.venv/bin/python -m equity --root data/equity --stage-root data/stage \
  --baseline data/equity/baseline.json catalog

# 소비자 계약 EGC-01·02·03·04·05·10 — 커널 어댑터(pyarrow)로 duckdb 를 교차 검증
.venv/bin/python -m equity --root data/equity --stage-root data/stage \
  --baseline data/equity/baseline.json contract --engine-src $HOME/quant-ledger/_engine
```

- `catalog` 가 게이트에 실패하면 **카탈로그를 교체하지 않는다**(옛 `equity.duckdb` 가 남는다) —
  `data/equity/_failed/catalog_<snapshot>.json` 을 읽어라.
- `_asof/<view>/<snapshot_id>/` 는 EG5c 의 표본이다. 표본을 새 기준으로 갈아야 할 때만
  `catalog --rebase-asof` 를 쓰고, 그 사실을 DESIGN §10 에 기록하라(승인 축).
- `contract` 의 `--engine-src` 는 서버에 rsync 해 둔 엔진 소스(`~/quant-ledger/_engine`)다.
  서버 venv 에 `numpy`·`pyarrow` 가 있어야 한다.

### 3-5. 로컬 테스트

```bash
# equity (절단본 stage_slice 위 TDD)
uv run --with duckdb --with pyarrow --with numpy --with pytest pytest database/tests -q -p no:cacheprovider
ruff check --line-length 100 --select E,F,I,UP,B database/src/equity

# 워크벤치 어댑터(ruamel.yaml 등 backend 의존성이 필요해 --project backend 로 돈다)
cd backend && uv run --extra parquet --extra equity pytest \
  tests/contract tests/test_adapters_equity_duckdb.py tests/test_adapters_equity.py -q
```

`pytest ... | tail` 은 실패를 가린다 — **FAILED 도 grep 하라**.

### 3-6. MVP-B 백테스트(절단본)

절단본 9표 체인 + `catalog` 위에서 워크벤치 파이프라인을 한 번 완주시킨다.

```bash
uv run --project backend python database/scripts/run_mvp_backtest.py \
  --root <equity_root> --start 2011-01-03 --end 2026-08-20 \
  --universe krx.common-stock --price-field price.adj_close --top 20
```

HEAD 기준 확정 수치(DESIGN §10 P25″): 세션 3,843 · 종목 11 · 리밸런싱 187 ·
top20 `price.adj_close` **+124.51%**(CAGR 5.448% · MDD −12.62% · tape `5cf26486…`) ·
top3 adj **+173.84%** vs top3 raw **+143.40%**. Rust `backtest_core` 가 없으면 Python core 로 떨어진다.

**수치가 흔들리면 backend 를 먼저 의심하지 마라** — 09-06 조사에서 backend 6커밋은 전부
`tape_hash` 까지 동일했고, 갈린 곳은 equity 규칙(`fc6e889` S06-2 · `de75e9f` S03C)이었다.
데이터 축을 고정(`--root` 를 같은 루트로)하고 `--engine-src` 만 바꿔 가르는 것이 절차다.

---

## 4. 게이트가 실패하면

빌드는 `build_table()` → 게이트 → 통과해야 `MANIFEST.json` 을 교체한다. 실패하면 tmp 를 버리고
`data/equity/_failed/<build_id>.json` 에 `first_failed_gate` 와 전 게이트 결과를 쓴다.
**커밋된 판은 그대로 남는다** — 실패해도 서비스가 깨지지 않는다.

실행 순서는 `EG0 → EG7 → EG1 → EG2 → EG3 → (테이블 특화) → EG4 → EG5`이고
앞 게이트가 FAIL 하면 뒤는 `skip(upstream_failed)` 이다. **첫 실패 게이트만 보면 된다.**

| 실패 게이트 | 뜻 | 먼저 볼 곳 | 흔한 원인·대응 |
|---|---|---|---|
| **EG0** | 입력 고정·선언 대조 | `_meta.gates[EG0].metrics` 의 `unpinned`·`missing_columns`·`input_gate_fail` | stage 새 판을 `python -m equity pin <stg_table>` 로 다시 고정 / stage 컬럼명이 바뀌었으면 `rules_s*.py` 의 `input_columns` 정정 |
| **EG7** | 격리 비율 상한 초과 | `_meta.n_reject_by_reason` | 사유 어휘가 새로 늘었는지 먼저 본다. **임계를 올리기 전에 왜 늘었는지 답할 것** — 임계는 `baseline.json` 의 `<table>.thresholds.EG7` |
| **EG1** | 행수 등식(lhs = rhs) | `rules_s*.py` 의 `eg1_lhs_sql`·`eg1_rhs_sql` | 격자 3표는 `EG1_ledger`/`EG1_<table>` 이 원장 축을 따로 잰다. 원장 append-only 판본 중복(§7 ①)이 자주 범인 |
| **EG2** | PIT — `available_date` | `available_rule`·`available_basis` | 파생 컬럼의 `<col>_available_date` 가 구성 행 max 인지(EG2-P05), `lag_known=false` 원천이 `dataset_profile` 에 랙 ≥ 1 로 있는지(EG2-P04) |
| **EG3** | 무결성 술어 | `extra_gates` 의 `eg3_<table>` | 어휘 폐쇄(EG3-P13) 위반이면 stage 원문에 새 값이 생긴 것 — 대응표를 늘려야지 값을 지우면 안 된다 |
| **EG4** | 골든 픽스처 불일치 | `src/equity/fixtures/<table>.json` | **모집단 의존 값을 픽스처에 굳혔는지 먼저 의심하라**(§7 ②). 절단본 실측을 픽스처에 넣으면 서버에서 반드시 깨진다 |
| **EG5a** | 재현성(같은 inputs → 같은 해시) | `metrics.previous_partition_hashes` 대 `partition_hashes` | 비결정 SQL(총순서 없는 `ntile`·`row_number`, HASH 순서 의존 집계)이 원인. §7 ④ |
| **EG6/EG8/EG9/EG10** | 테이블 특화(판본 선택·교차 원천·커버리지·준비도) | `rules_s*.py` 의 해당 훅 + `baseline.json` 임계 | 임계가 미등재면 `skip(no_baseline)` 로 남고 측정치가 `metrics` 에 실린다 — 그 값을 보고 사람이 등재한다(GATES §7-3) |
| **EG11/EG5c** | 뷰(카탈로그) | `_failed/catalog_<snapshot>.json` | EG11 = 같은 as-of 표본 2회 실행 해시 동일 / EG5c = 직전 `_asof/` 표본과 차이 0 |
| **EGC-01~10** | 소비자 계약 | `_contract_meta.json`·`_failed/contract_<snapshot>.json` | 어댑터(backend)와 duckdb 독립 읽기의 대조다 — 실패는 대개 어댑터 쪽 |

**커밋된 판을 다시 판정만** 하고 싶으면(빌드 없이, 폐기 없이):

```bash
.venv/bin/python -m equity --root data/equity --stage-root data/stage \
  --baseline data/equity/baseline.json gate <table>
```

임계만 바꿨을 때는 이걸로 충분하다. **산출식 상수를 바꿨으면 재빌드해야 한다**(§5 표).

---

## 5. baseline 상수 — 뜻과 바꿀 때 다시 지어야 하는 표

정본은 서버 `data/equity/baseline.json`. 저장소의 **`database/src/equity/baseline_locked.json`
이 그 파일과 바이트 동일한 확정본**이고, 검사는 `database/scripts/check_baseline_lock.py` 다.

```bash
# 로컬에서 서버 사본을 받아 대조
scp kael-server:~/quant-ledger/data/equity/baseline.json /tmp/server_baseline.json
uv run python database/scripts/check_baseline_lock.py /tmp/server_baseline.json
# 확정본을 서버에 설치(= 바이트 동일을 보장하는 유일한 경로)
scp database/src/equity/baseline_locked.json kael-server:~/quant-ledger/data/equity/baseline.json
```

`baseline_seed_s*.json` 은 **슬라이스별 제안 시드**(절단본 실측 + 근거 note)이고 정본이 아니다.
새 슬라이스는 시드를 내고, 서버 실측 뒤 사람이 `baseline_locked.json` 에 확정해 배포한다.

### 5-1. 산출식으로 들어가는 상수 (바꾸면 **재빌드** — 게이트 재판정으로는 못 반영)

`EquityTable.consts` 로 선언돼 `build.make_consts()` 가 `_const` 뷰로 주입한다.
"재빌드" 열은 직접 소비하는 표이고, **그 아래 §2 의존 그래프 전체가 따라온다.**

| 상수 | 값 | 뜻 | 재빌드 |
|---|---|---|---|
| `corp.financial_ksic_prefix` | `"64,65,66"` | KSIC 중분류 = 금융업 판정(`induty_class`) | `corp` |
| `security.backfill_end` | `2026-08-20` | 백필 상한 거래일. 이 날까지 행이 있으면 "아직 살아 있다" | `security` |
| `corp_ticker.isin8_len` | 8 | ISIN 앞 8자 = 법인 그룹 축 | `corp_ticker` |
| `corp_event.effective_before_announce_max_days` | 2555 | 효력일이 공시일보다 이만큼 이상 앞서면 격리(EG7-P08) | `corp_event` |
| `corp_event.krx_share_change_tol` | 0.001 | 같은 날 주식수 변화 허용 상대오차 | `corp_event`·`adj_factor` |
| `corp_event.near_dup_window_days` | 5 | 근접 중복 사건 창(일) | `adj_factor` |
| `adj_factor.price_match_tol_rel` / `_tol_abs` | 0.15 / 0.05 | 사건↔가격 매칭 허용 상대·절대 오차 | `adj_factor` |
| `adj_factor.price_match_window_sessions` / `_lookback_sessions` | 40 / 5 | 매칭 탐색 창(전방/후방 세션) | `adj_factor` |
| `adj_factor.base_price_tol_rel` | 0.002 | KRX 기준가 대조 허용 상대오차(S06-2) | `adj_factor` |
| `adj_factor.base_match_window_sessions` | 5 | 기준가 매칭 창(세션) | `adj_factor` |
| `adj_factor.factor_product_tol_base` | 0.01 | 기준가 원천 계수의 곱 불변 허용오차 | `adj_factor` |
| `universe_daily.adv_window_td` | 20 | ADV 창(거래일) | `universe_daily` |
| `universe_daily.no_trade_run_k` | 5 | 연속 무거래 k세션이면 `suspended` 로 본다(신호 없는 무거래에만) | `universe_daily` |
| `universe_daily.admin_window_td` | 365 | 관리종목 신호 유효창(일) | `universe_daily` |
| `universe_daily.corp_action_lookback_sessions` / `_lookahead_sessions` | 5 / 45 | 기업행위 전후 창(−5/+45 세션) | `universe_daily` |
| `universe_daily.admin_signal_window_sessions` | 5 | 관리종목 신호 창(세션) | `universe_daily` |
| `universe_policy.version` / `liquid_top_pct` | `s03c-v4` / 0.5 | 정책표 판본 / `liquid` = 날짜별 상위 50% | `universe_policy` |
| `disclosure_version.deadline_days_annual` / `_interim` | 90 / 45 | 법정 제출기한(일) | `disclosure_version` |
| `disclosure_version.date_check_near_days` | 7 | `off_2_7d` 판정 창 | `disclosure_version` |
| `fin_std.quarter_months` / `half_months` / `three_quarter_months` | 3 / 6 / 9 | `period_from→period_to` 개월 수로 1Q/반기/3Q 판정 | `fin_std` |
| `fin_std.period_end_lag_max_days` | 200 | `period_end` 대 접수일 최대 지연 | `fin_std` |
| `fin_std.rcept_lag_p99_days` | 1826 | 접수 지연 허용 범위 상한(EG7-P04 격리 경계) | `fin_std` |
| `ownership_snapshot.pct_min` / `pct_max` | 0 / 100 | 지분율 범위(EG7-P07) | `ownership_snapshot` |
| `dividend_event.cash_total_unit_krw` | 1,000,000 | 현금배당 총액 단위(백만원) | `dividend_event` |

### 5-2. 게이트만 보는 상수 (바꾸면 `gate <table>` 재판정으로 충분)

| 상수 | 값 | 뜻 · 소비 게이트 |
|---|---|---|
| `trading_calendar.calendar_start` | 2010-01-04 | 캘린더 하한(KRX API 하한). EG17 |
| `trading_calendar.backfill_end` | 2026-08-20 | 캘린더 상한. EG17 · `security_span.end_reason='coverage_gap'` 경계 |
| `trading_calendar.asof_sample_dates` (5) · `asof_sample_tickers` (20) | — | `_asof/` 표본. EG5c·EG11·EG19 |
| `security_span.respan_count` | 2 | 재상장 종목 수(036220·101970). EG16a |
| `security.delist_sample_n` / `delist_sample_seed` | 20 / 20260905 | 폐지 표본. EGC-10 |
| `universe_daily.contract_probe_dates` (7) | — | 계약 검사점. EGC-02·05 |
| `adj_factor.adj_return_jump_max` | 1.0 | 분할·무상증자일 수정수익률 점프 상한. EG8-P02 |
| `adj_factor.adj_volume_ratio_band` | 3.0 | 조정 거래량 중앙값 비 허용 배수. EG8-P03 |
| `adj_factor.asof_for_jump_check` | 2026-08-20 | EG8 점프 검사 as-of |
| `flow_daily.investor_sum_tol_krw` | 6,000,000 | 키움 12주체 순매수 합 = 0 허용오차. EG3-P06 |
| `holder_daily.pct_max` | 100 | 지분율 상한. EG7-P07 |
| `ownership_snapshot.agg_rate_tol_pct` | 0.5 | 합계행 대 개별합 허용차(%p) |
| `disclosure_version.link_rate_min` | 0.99 | 정정 링크 성립률 하한. EG6-P05(E-G6a) |
| `disclosure_version.reach_rate_min` | 0.99 | `rm` 플래그 도달률 하한 |
| `disclosure_version.date_exact_rate` | 0.987 | `date_check ∈ {exact, off_1d}` 비율(기록형 기준). EG6-P06 |
| `fin_std.nonmatch_rate_gap_max` | 0.05 | 비12월 대 12월 무매칭률 차 상한. EG6-P08 |
| `consensus_daily.v3_wise_match_min` | 0.65 | v3⋈WISE 겹침 일치율 하한. EG8-P07 |
| `consensus_daily.v3_wise_value_tol_rel` | 0.001 | 위 일치 판정의 값 허용 상대오차 |
| `consensus_daily.cover_ratio_drop_max` / `_rise_max` | 0.7 / 16.0 | 월별 커버율 계단 하락·상승 상한. EG9 |
| `opinion_daily.src_overlap_agree_min` | 0.99 | v3⋈WISE 겹침 5축 일치율 하한. EG8 |
| `<table>.thresholds.EG7` | 표별 | 격리 비율 상한. 미등재면 코드 기본값 0.001 |

**등재 상태의 `thresholds.EG7`**: `corp_event` 0.06 · `credit_daily` 0.005 · `flow_daily` 0.05 ·
`short_daily` 0.05 · `fin_std` 0.03 · `ownership_snapshot` 0.001 ·
`disclosure_version`/`holder_daily`/`audit_opinion`/`shares_outstanding`/`treasury_stock`/`dividend_event` 0.0.
나머지 표는 미등재 = 코드 기본값 0.001 로 판정된다.

### 5-3. 미등재로 남긴 것 (사람 승인 대기)

- **`factor_readiness.ready_min`** — EG10 의 ready 하한. 서버 실측은 **ready 35 / blocked 19**(P41′).
  DESIGN §4-8 의 초기값 36 은 격자 3표를 전제로 센 수이고 실측이 35 라 지금 등재하면 첫 빌드가
  폐기된다. 35 를 그대로 등재하면 하한이 실측과 같아져 **회귀 감시로는 유효하지만
  개선 여지를 0 으로 못 박는다**. `blocked` 19 중 `field_unavailable` 이 남아 있는 한
  이 수는 올라갈 여지가 있으므로 **미등재 유지**를 권고한다(§8 후속).
- `disclosure_version.misjudge_rate_max` — `rules_s11.py:297` 이 읽지만 미등재.
  정정 그룹 표본 오판율(EG6-P07)의 임계이며 표본 판정 절차 자체가 아직 없다.
- `price_daily.krx_kis_ratio_match_min`(EG8-P01) · `corp_event.detect_recall_min`(EG8-P04) —
  독립 KIS 가격 stage 표가 없어 대조축이 없다.

---

## 6. `RULES_VERSION` 상향 규칙

`database/src/equity/model.py:25` 의 `RULES_VERSION` 은 `BuildRecord.rules_version` 에 실린다.

- **산출을 바꾸는 규칙 변경이면 반드시 올린다.** `sql/*.sql`·`rules_*.py` 의 산출식·
  선언 컬럼·`field_profiles`·게이트 술어가 대상이다.
- EG5a 는 **같은 판본의 직전 빌드하고만** 해시를 비교하고, 판본이 다르면 `skip(rules_changed)`.
  판본을 안 올리고 산출을 바꾸면 EG5a 가 **FAIL** 하고 새 판이 폐기된다(09-05 corp_event 4차 실측).
- 반대로 산출이 안 바뀌어도 **선언면이 늘면 올린다** — e1.4.0(`FieldProfile` 훅 신설),
  e1.5.0(격자 3표 `field_profiles` 선언, `dataset_profile` 66→72행)이 그 사례다.
- 판본 이력은 `model.py` 의 주석 블록에 한 줄씩 남긴다. 지우지 마라 — EG5a skip 사유를
  나중에 읽는 유일한 근거다.
- 판본을 올린 뒤 **첫 빌드는 EG5a 가 skip** 이므로 재현성이 확인되지 않는다.
  올린 판본으로 **두 번** 지어야 비교가 성립한다.

---

## 7. 알려진 함정

1. **원장 append-only 판본 중복** — stage 원장은 같은 키를 여러 판본으로 쌓는다.
   equity 가 그대로 조인하면 행이 부풀어 EG1 이 깨진다(S11 서버 1차 198,189 → 198,163, 26건).
   판본 선택 규칙(첫 관측·최소 `observed_date`)을 SQL 안에서 명시하라. EG6-P01~P04 가 감시한다.
2. **모집단 의존 픽스처 금지** — 골든 픽스처(`src/equity/fixtures/<table>.json`)에
   절단본(15티커) 실측값을 굳히면 서버(3,478법인·10.9M 격자)에서 반드시 EG4 FAIL 한다.
   실측 2회 사고: `adv20_rank_pct` 순위(P26′), `financial.borrowings.coverage_from`(P38′).
   **픽스처에는 선언과 선언 조인만으로 정해지는 값만** 넣고, 실측은 절단본 pytest 로 옮겨라.
3. **좌변 전용 조인 술어** — `JOIN … ON a.k = b.k AND NOT b.flag` 처럼 한쪽 테이블에만
   걸리는 술어를 조인 조건에 두면 DuckDB 가 해시 조인을 `BLOCKWISE_NL_JOIN` 으로 떨어뜨린다.
   `credit_daily` 가 격자 9.2M × 원장 8.4M 에서 **70분+ → 6초**로 바뀐 사고다(P33′, fe0c5b7).
   양변을 쓰는 등호로 바꾸거나 서브쿼리에서 미리 걸러라.
4. **`ntile`·`row_number` 의 총순서** — ORDER BY 가 동률을 남기면 같은 입력에서 다른 산출이
   나온다(비결정). `dataset_profile` 의 시총 분위가 `ORDER BY mktcap_krw` 만 걸어 EG5a 를
   깨뜨렸다(P38′) → `ORDER BY mktcap_krw, ticker` 로 총순서를 만들었다.
   커버율도 정수 분자·분모 + 고정 반올림으로 바꿨다(EG2-P08).
5. **`LC_ALL=C`** — 서버 게이트 detail 에 한글이 섞여 있다. 로케일 없이 `grep`/`cut` 하면
   깨진 바이트가 나온다. ssh 명령 앞에 `export LC_ALL=C;` 를 항상 붙여라.
6. **`pkill -f` 가 자기 셸을 잡는다** — 패턴이 자기 ssh 명령줄과도 매치해 세션이 끊긴다.
   PID 를 `ps -eo pid,args` 로 확인하고 죽여라.
7. **EG5a 는 전량 재빌드에서 무력하다** — 상위 표를 다시 지으면 하위 표의
   `BuildRecord.inputs`(equity build_id) 가 바뀌어 EG5a 가 `skip(inputs_changed)` 로 빠진다.
   순수 stage 입력 표 9개(`trading_calendar`·`corp`·`security`·`security_span`·`corp_ticker`·
   `index_daily`·`disclosure_version`·`treasury_stock`·`dividend_event`)만 `pass` 가 뜬다.
   전량 재현성은 **게이트가 아니라 `summary.tsv` 의 `content_hash` 를 2회 비교**해서 본다(§3-3).
8. **`flock` 직렬** — 서버 RAM 15GB 에 격자 표가 5~7GB 를 쓴다. 병렬로 돌리면 OOM 이다.
9. **`baseline.json` 은 원자 교체** — `stage.baseline.write` 를 그대로 쓴다.
   직접 편집하지 말고 파일을 통째로 덮어써라(§5).
10. **서버 `src/stage` 는 stage 세션 소유** — equity 는 `src/equity`·`scripts/run_equity.sh`·
    `_engine/`·`data/equity/` 만 쓴다. stage 코드를 배포하면 stage 크론이 깨진다.
11. **`catalog.snapshot_id` 는 내용 해시가 아니다** — `{table}={build_id}` 문자열의 sha256
    앞 16자다(`catalog.py:67-70`). `build_id` 를 손으로 고정해 지으면 **내용이 달라도 같은
    snapshot** 이 나온다. "snapshot 이 같으니 데이터도 같다" 는 추론은 성립하지 않는다 —
    S21 본판이 이 오해로 MVP-B 드리프트를 backend 탓으로 오진했다(DESIGN §10 P25″).
    데이터 동일성은 표별 `content_hash` 로 봐라.

---

## 8. 절단본(`stage_slice`) 재생성

로컬 TDD 는 서버 원장이 아니라 `database/tests/fixtures/stage_slice/` 의 소형 parquet 트리 위에서
돈다. 구조는 실물과 같다: `<stg_table>/MANIFEST.json` + `v=<build_id>/year=YYYY/*.parquet`
(또는 `v=<build_id>/part0.parquet`).

**현재 상태**: 61테이블. 티커 15 / 법인 11 축은
`database/tests/fixtures/stage_slice/README.md` 가 정본이다(티커 목록·표별 행수·추가 이력).

**재생성 절차**(생성 스크립트는 저장소에 없다 — 아래를 따라 다시 만든다):

1. 서버에서 대상 stage 표의 `current_build` 를 확인한다:
   `ssh kael-server "cd ~/quant-ledger && python3 -c \"import json;print(json.load(open('data/stage/<t>/MANIFEST.json'))['current_build'])\""`
2. 그 판본의 parquet 을 티커/법인 축으로 걸러 로컬에 쓴다. 축은 표마다 다르다:
   - 티커 축(`stg_listing_daily`·`stg_price_daily`·격자 원천 등): README 의 티커 15
   - 법인 축(`stg_event_*`·`stg_capital`·`stg_shares`·`stg_fin`·`stg_doc_*` 등): 그 15티커의 `corp_code` 11
   - 접수번호 축(`stg_doc_*`): 위 법인 11 의 `rcept_no` 33,971건
3. `MANIFEST.json` 은 서버 것을 복사하되 `builds[].partitions[].path`·`n_rows` 를 실제 산출로
   맞춘다. **0행 표도 스키마 보존용 빈 파티션 1개를 반드시 넣어라** —
   파티션 0개면 `read_parquet` 이 IOException 을 던져 입력으로 열리지도 않는다
   (`stg_flow_split_daily` 가 이 결함을 갖고 있다, README 알려진 결함 ①).
4. stage 가 정규화(공백·개행·NFKC)를 바꾸면 **그 표를 다시 잘라야 한다** — 픽스처 키가
   정규화 전 텍스트로 굳으면 서버에서 EG4 FAIL 한다(P35′ `shares_outstanding`, bd114bf).
5. 다 자른 뒤 `README.md` 의 표 목록·행수·생성일을 갱신하라.

**결함(미해결)**: 절단 스크립트(`slice_stage.py`)가 세션 스크래치에만 있었고 저장소에 없다.
위 절차는 README + 커밋 이력에서 복원한 것이며, 재현 가능한 스크립트로 만드는 것이 후속이다(§9).

---

## 9. 미해결 후속

정본은 DESIGN §11 이다. 여기엔 **다음 사람이 곧바로 집을 수 있는 것**만 골라 적는다.

### 데이터 축
- **S08-2 외국인 보유 3컬럼** — `flow_daily.foreign_wght_pct`·`limit_exh_rt_pct`·`foreign_poss_shr`
  의 원천 `stg_foreign_daily`(키움 ka10008)가 stage 에는 있고 **절단본에 없다**.
  `flow_daily` 에 컬럼 자체가 없어 팩터 F02·F05 가 `blocked(field_unavailable)`.
- **키움 대차(`stg_lending_daily`) 미절단** — `short.short_balance_ratio` 미지원의 원인.
- **`classification.sector` PIT 없음** — 현재값 라벨이라 `point_in_time=false`. 업종 PIT 원천 필요.
- **기준가 불일치 151건** — S06-2 가 남긴 정밀 조정 대상(P27′).
- **KRX 매매거래정지 현황 수집** — stage 몫. `halt_state` 의 공시 기반 추정을 대체한다.
- **재무 원본 판본(4C = S14)** — 문서층 P2 `stg_fin_asreported` 대기.
  그때까지 `fin_std.vintage_kind` 는 `api_restated` 하나뿐이고 PIT 결측은 비랜덤이다.

### 계약·어댑터 축
- **`src_omitted` → `CellKind` 라벨 손실**(DESIGN §11 ⑪) — 워크벤치 도메인이 값 없는
  `SOURCE_OMITTED_ZERO` 를 거부해 S21-3 이 MISSING 으로 접었다. 소비층이 "0 으로 읽어도 되는 결측"과
  "그냥 결측"을 구분하지 못한다. 해소는 (a) `dataset_profile` 이 값 축·지식 축을 분리하거나
  (b) 워크벤치 도메인 계약 변경.
- **어댑터 랙 상수 하드코딩**(DESIGN §11 ⑭) — `_specs.py` 의 격자 4원천이 `lag_sessions=0` 인데
  `dataset_profile` 은 1 세션으로 확정했다. `price.market_cap`·`price.shares_outstanding` 도 같다.
  **어댑터가 `dataset_profile` 을 읽어 필드별 랙을 적용하는 형태로 한 번에** 교체해야 한다
  (한 필드군만 고치면 어댑터 안에서 규약이 갈린다).
- **`consensus.*` 의 `target_period` 선택**이 어댑터 규칙(FY1)이다 — 레지스트리 라벨
  "12개월 선행 EPS" 와 값의 뜻이 다르다.

### 운영·검증 축
- **`factor_readiness.ready_min` 등재 판단**(§5-3).
- **절단본 생성 스크립트 커밋**(§8 결함).
- **`catalog.snapshot_id` 에 내용 축 추가 판단**(§7 ⑪) — 지금은 `build_id` 지문뿐이라
  "같은 snapshot = 같은 데이터" 가 성립하지 않는다.
- **EG7 임계 조이기 판단** — 서버 실측 격리비율이 임계보다 크게 낮다:
  `flow_daily` 0.00998/0.05 · `short_daily` 0.00629/0.05 · `fin_std` 0.01275/0.03.
  조이면 회귀 감시가 촘촘해지지만 원천 커버리지가 흔들릴 때 폐기된다 — 사람 판단.
- **`consensus_daily.v3_wise_match_min` 상향 판단** — 서버 실측 0.873(하한 0.65).
  불일치 735건이 WISE 월간 엔드포인트와 v3 리비전의 추정 모집단 차이인지 먼저 규명할 것.
- **EG3 재계산 31s 최적화** · **EG8-P01/P04 대조축 확보**(독립 KIS 가격 원천).
- **서버 전 종목 백테스트 실측** — MVP-B 는 절단본 15티커에서만 완주했다.
  파이프라인이 격자 행을 파이썬 객체로 올리므로 10.9M 격자 × 15년 run 은 메모리·시간 실측 전이다.
- **격리 비율 분모 문서 통일** · **커널 정지 세션 정책**(`TargetTapeStrategy` 임시 규칙 회수).

---

## 10. 참고 — 파일 지도

| 무엇 | 어디 |
|---|---|
| 표 선언(grain·입력·EG1 등식·상수·픽스처 훅) | `database/src/equity/rules_s<NN>.py` |
| 산출 SQL | `database/src/equity/sql/<table>.sql` |
| 프레임(빌드·게이트·입력 고정·baseline·CLI) | `build.py`·`gates.py`·`inputs.py`·`baseline.py`·`__main__.py` |
| 전방 조정가 표 | `rules_s23.py` · `sql/price_adj_daily.sql` — 소비 규약은 아래 「조정가 읽는 법」 |
| 뷰 매크로 8 | `views.py` (`v_cum_adj`·`v_adj_price`·`v_adj_volume`·`v_adj_price_fwd`·`v_adj_volume_fwd`·`v_firm_mktcap`·`v_consensus`·`v_fin_latest`) |
| 카탈로그 publish + EG11·EG5c | `catalog.py` |
| 소비자 계약 EG-C | `contract.py` |
| 골든 픽스처 | `database/src/equity/fixtures/<table>.json` |
| baseline 확정본·시드 | `baseline_locked.json` · `baseline_seed_s<NN>.json` |
| 워크벤치 어댑터(5포트·필드 30) | `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/` |
| MVP-B 백테스트 | `database/scripts/run_mvp_backtest.py` |
| 서버 빌드 러너 | `database/scripts/run_equity.sh`(표 1개) · `equity_rebuild_all.sh`(28표 전량, `ORDER` 에 `price_adj_daily` 포함) · `equity_gate_all.sh`(전량 재판정) · `equity_manifest_row.py`·`equity_gate_metrics.py`(요약·근거 추출) |

## 조정가 읽는 법 (`price_adj_daily`, S23)

**무엇인가** — 전방 조정(forward-adjusted) OHLCV 의 저장본이다. `adj_close(d) = close(d) ×
Π{share_factor : factor_ok ∧ 같은 `security_span` 구간 ∧ greatest(apply_date, available_date) ≤ d}`
이고, **종목의 첫 관측 수준을 고정**하고 사건마다 이후 가격을 올린다(005930 2018-05-03 =
2,650,000 원주가 그대로 · 05-04 = 51,900 × 50 = 2,595,000). 값은 (ticker, date) 의 **순수 함수**라
질의 창·as_of 에 무관하고 `available_date` 는 언제나 `date` 다.

**누가 읽는가**
- 워크벤치 `price.adj_close` — 어댑터가 이 표를 직접 읽는다(매크로가 아니다). 카탈로그가 낡거나
  없어도 산다.
- parquet 을 직접 읽는 분석 — `data/equity/price_adj_daily/v=<build>/year=*/…`.
- 카탈로그 매크로 `v_adj_price_fwd`·`v_adj_volume_fwd` — 같은 값을 내는 읽기 경로다. 표에 없는
  것(원주가 컬럼 동반·`lag_override` 로 계수 컷오프를 미는 축)이 필요할 때만 쓴다.

**읽지 않는 곳 — 엔진 커널**. 커널(`backtest_engine`)은 **원주가 bar + `CorporateActionEvent`** 로
포지션 수량을 스스로 조정한다. 조정가를 bar 로 주면 같은 사건이 두 번 반영된다(가격은 이미
조정됐는데 수량까지 다시 조정된다). `backtest_engine/adapters/equity_duckdb.py` 가 이 표를 읽지
않는다는 것을 `test_equity_s23_price_adj.py::test_커널_어댑터는_조정가_표를_읽지_않는다` 가 지킨다.

**`n_unadjusted_events` 를 반드시 보라** — 같은 구간에서 `factor_ok=false` 이고
`apply_date ≤ d` 인 사건 수다. **0 이 아니면 그 구간의 조정 시계열은 불완전하다**: 기업행위가
실재하는데 계수를 못 냈다는 뜻이고(사유는 `adj_factor.factor_source` — `no_price_match` ·
`ratio_null` · `capred_paid` · `unknown_price_only` 등), 그 뒤 구간의 수익률에는 조정되지 않은
점프가 남아 있다. equity 는 값을 만들어 채우지 않는다. 소비자는 이 열로 종목·구간을 거른다
(서버 실측: 행의 27.4% · 1,440 종목이 걸린다 — 대부분 `unknown_price_only`(유상증자 권리락·
주식배당락 등 MVP 밖 사건)라 "조정이 틀렸다" 가 아니라 "이 축은 MVP 가 안 덮는다" 는 뜻이다).

**`cum_price_factor` × `cum_share_factor` = 1** 이고(시총 불변), 구간 첫 행에서는 둘 다 정확히
1 이다. 재상장 종목(036220·101970)은 구간마다 누적이 초기화된다 — 폐지 전 구간의 계수는 새 구간에
넘어오지 않는다.

## 소비자 기동 (워크벤치 · 로컬 데이터)

**로컬 데이터 내려받기** — `database/scripts/fetch_equity_local.sh <로컬 경로> [minimal|full]`
- `minimal`(기본) 10표 ≈ 2.0GB: 가격·**조정가**·유니버스·조정계수·기업행위·식별 4표. 가격/모멘텀/변동성 전략용.
- `full` 19표 ≈ 3.3GB: 재무·컨센서스·의견·수급·공매도·신용·배당·지분 추가.
- **`_pinned/` 은 받지 않는다** — 재빌드 시 stage 입력을 고정한 하드링크 사본이라 읽기에 불필요하고,
  rsync 하면 하드링크가 풀려 실제 크기(수 GB)로 복사된다. `_asof/`·`_tmp/`·`_failed/` 도 같다.
- 스크립트가 `baseline.json` 을 함께 받고 **카탈로그를 다시 만든다**. 매크로 본문이 절대경로를
  굽기 때문에(§10 P1c) 경로가 바뀌면 `financial.*`·`consensus.*` 가
  `unavailable` 이 된다(`price.adj_close` 는 S23 부터 표를 읽으므로 무관하다) — 손으로 복사했다면 반드시 `python -m equity --root <경로> … catalog`.

**워크벤치를 duckdb 어댑터로 기동**
```
export STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb
export STRATEGY_WORKBENCH_EQUITY_ROOT=/path/to/equity
cd backend && uv run --extra parquet --extra equity server
```
환경변수를 안 주면 `mock` 으로 뜬다(기존 동작). `duckdb` 인데 루트가 없으면 기동 시점에
`ValueError` 로 죽는다 — 조용한 mock 폴백은 없다. 구현은
`backend/src/strategy_workbench/bootstrap/_http.py` 의 `runtime_equity_selection()`,
회귀 테스트는 `backend/tests/test_http_equity_env.py`.
