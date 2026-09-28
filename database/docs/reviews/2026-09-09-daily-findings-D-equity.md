# findings_D — equity 층 일일 갱신 조사 (2026-09-09)

조사 대상: `stage parquet → equity 28표 → dataset_profile·factor_readiness → 카탈로그 → 소비자`.
표기: **실측** = 서버(`kael-server:~/quant-ledger`)·저장소 파일에서 직접 읽은 값, **추정** = 코드·문서 유추.
서버는 읽기만 했다.

관측 시점 실측값(문서와 어긋나는 것 포함):

| 축 | 실측 | 비고 |
|---|---|---|
| `data/equity/` MANIFEST 보유 표 | **28** | START_HERE 는 29, HANDOFF §1 은 25+3=28 로 문서끼리 어긋남. 러너 `ORDER` 도 28 |
| `dataset_profile` / `factor_readiness` | 79 / 54 | `b_20260908T0435…` / `b_20260908T0436…` |
| 가격 행 | 10,890,251 | `price_daily`·`price_adj_daily`·`universe_daily` 동일 |
| 표별 `rules_version` | **e1.5.0 ~ e1.14.0 혼재** | §2-3 |
| `equity.duckdb` 카탈로그 | **10표 stale** | 09-06 12:38 판. §5-2 |
| 서버 `baseline.json` vs `baseline_locked.json` | **MISMATCH(상수 3건)** | §3-4 |
| quant-ledger 크론 | **`daily_wise.sh` 하나뿐** | `crontab -l` 실측. stage·equity 자동화 없음 |

---

## 1. 입력 고정 방식 — pin 은 "절차" 가 아니라 빌드마다 자동으로 일어난다

### 1-1. 무엇을 읽는가

`build_table()` 이 매 빌드 첫머리에서 입력 전부를 그 자리에서 pin 한다:

```python
# database/src/equity/build.py:151
pinned = {t: inputs.pin(stage_root, equity_root, t) for t in rule.inputs}
```

`inputs.pin()`(`database/src/equity/inputs.py:110-134`)은 `<stage_root>/<t>/MANIFEST.json` 의
**`current_build` 를 그 자리에서 해석해**(`inputs.py:92-96`) 파티션 파일을 `_pinned/<t>/v=<build>/`
로 `os.link` 한다. 같은 build 재고정은 `if out.exists(): continue` 로 no-op.

→ **stage 가 새로 빌드되면 equity 는 다음 빌드에서 자동으로 새 입력을 본다. pin 을 옮기는 별도
절차는 없다.** CLI `python -m equity pin <stg_table>`(`__main__.py:61-65`)은 빌드 없이 미리 고정하는
편의 명령이다.

`_pinned/` 의 목적은 "고정" 이 아니라 **stage keep=3 GC 로부터 재현성을 지키는 것**
(`inputs.py:7-10`). 그래서 `_pinned/<t>/MANIFEST.json` 은 덮어쓰지 않고 BuildRecord 를 **누적**하며
`manifest.commit()`(GC)을 일부러 부르지 않는다(`inputs.py:137-157`). §2-5.

### 1-2. 지금 pin 상태 (실측)

stage 입력 42표 전부 `_pinned` current == `data/stage` current. **stale 0 / 42.**
equity 내부 입력 27표도 같은 규약으로 `_pinned` 에 있다.

### 1-3. pin 이 새 stage 판으로 넘어갈 때 통과해야 하는 게이트 — EG0

`gates.eg0_inputs`(`database/src/equity/gates.py:123-168`):

| 술어 | 코드 | 새 stage 판에서 무엇을 막나 |
|---|---|---|
| `unpinned` | `gates.py:126-130` | `_pinned` 에 그 build 없음 → FAIL |
| `missing_columns` | `gates.py:131-137` + `inputs.py:183-188` | **stage 가 컬럼명을 바꾸면 즉시 FAIL** — 조용한 결측 없음 |
| `input_gate_fail` | `gates.py:138-144` | **stage 파티션 `_meta.gates` 에 `status="fail"` 하나라도 있으면 FAIL** |
| `schema_mismatch` | `gates.py:145-147` | 산출 컬럼 양방향·순서 대조 |
| `axis_ok` | `gates.py:148-149` | 파티션 축(`year=`) 선언 대조 |

EG0 은 폐기형이다. FAIL 하면 tmp 를 버리고 `_failed/<build_id>.json` 을 쓰며 **커밋된 판은 그대로
남는다**(`build.py:225-231`). stage 가 깨진 판을 올려도 equity 는 "어제 판 그대로" 로 남는다 —
일일 갱신 안전망의 핵심.

`check_baseline_lock.py` 는 pin 과 무관하다(서버 baseline 이 저장소 확정본과 바이트 동일한가만 봄).

---

## 2. 재빌드 단위·시간 — 증분 경로는 코드에 없다

### 2-1. 순서

`database/scripts/equity_rebuild_all.sh:17` 의 `ORDER` 28개(HANDOFF §2 의존 순서와 같은 문자열):

```
trading_calendar corp security security_span corp_ticker index_daily price_daily corp_event
adj_factor price_adj_daily universe_daily universe_policy flow_daily short_daily credit_daily
disclosure_version fin_std holder_daily ownership_snapshot audit_opinion shares_outstanding
treasury_stock dividend_event consensus_daily opinion_daily opinion_broker_daily
dataset_profile factor_readiness
```

한 표라도 `rc≠0` 이면 즉시 중단(`equity_rebuild_all.sh:29`).
`run_equity.sh:10-11` 의 `flock -n /tmp/quant_ledger_equity.lock` 으로 직렬화(병렬 시 exit 3).

### 2-2. 표별 소요 (실측)

**(a) 현재 커밋된 각 표의 `_meta.elapsed_s`** — 빌드 + 게이트 전량 포함. 표마다 판본·메모리가 달라
한 번 완주 시간의 합은 아니다.

| # | 표 | elapsed_s | rules_version | mem | 최대 RSS(s22_pass1 실측) |
|---|---|---:|---|---|---:|
| 1 | `universe_daily` | **79.3** | e1.9.0 | 6GB | 5.5 GB |
| 2 | `dataset_profile` | **71.2** | e1.14.0 | 6GB | 1.2 GB |
| 3 | `credit_daily` | **67.1** | e1.5.0 | 8GB | 4.9 GB |
| 4 | `price_adj_daily` | **59.4** | e1.9.0 | 6GB | — |
| 5 | `flow_daily` | **54.5** | e1.14.0 | 6GB | 6.8 GB |
| 6 | `short_daily` | **53.6** | e1.14.0 | 6GB | 6.2 GB |
| 7 | `fin_std` | **34.4** | e1.14.0 | 6GB | 5.2 GB |
| 8 | `price_daily` | **25.9** | e1.5.0 | 8GB | 3.6 GB |
| 9 | `adj_factor` | **14.2** | e1.9.0 | 6GB | 2.9 GB |
| 10 | `corp_event` | **9.3** | e1.9.0 | 6GB | 0.8 GB |
| — | 나머지 18표 | 각 0.1~7.3 | — | — | ≤ 0.8 GB |
| | **합계** | **494.7 s** | | | |

**(b) 실제 완주 1회(27표, price_adj_daily 이전, 09-06)**: `logs/equity/s22_pass1/STATUS` =
**`TOTAL 409s`**, `s22_pass2/STATUS` = **`TOTAL 409s`**(2회 동일). + `price_adj_daily` 59s
→ **28표 완주 ≈ 470초(8분)**(추정 — 두 값이 다른 실행에서 나왔다).

RAM 15GB(실측 `free -g`)에 격자 표가 5~7GB 를 쓴다 → 병렬화 여지 없음.

### 2-3. 증분 경로 — **없다**

- `build_table()` 은 `rule.build_by_year` 가 참이면 **`NotImplementedError`**(`build.py:130-133`).
  지금 어떤 표도 이 플래그를 켜지 않는다.
- 산출은 언제나 `COPY (SELECT * FROM out_ok ORDER BY …) TO '<tmp>' (FORMAT PARQUET,
  PARTITION_BY (year), OVERWRITE_OR_IGNORE)`(`build.py:171-186`) — **전 연도 파티션을 매번 새로 쓴다.**
  그 뒤 `shutil.move` 로 `v=<build_id>` 통째 교체(`build.py:252-256`).
- **"오늘치 파티션만 append" 하는 통로가 코드에 존재하지 않는다.** 날짜 파티션은 물리 레이아웃일 뿐
  빌드 단위가 아니다.

표별 판본이 e1.5.0~e1.14.0 으로 갈려 있는 것도 이 때문이다 — 09-07·09-08 에 바뀐 표만 다시 지었다.
일일 갱신을 하려면 이 혼재가 매일 해소되거나(전량 재빌드) 매일 심해진다.

### 2-4. `_asof/` 표본과 `content_hash` 재현성

- **`content_hash`** = `{n_rows}:{bit_xor(hash(row))}`, **tmp 경로에서** 뜬다(`build.py:67-74`;
  주석 10-11행: 최종 경로에서 뜨면 `v=<build_id>` 가 하이브 컬럼으로 섞여 EG5a 가 영원히 깨진다).
- **EG5a 는 전량 재빌드에서 무력하다.** 상위 표를 다시 지으면 하위 표 `inputs` 가 바뀌어
  `skip(inputs_changed)`(`gates.py:335-338`). 순수 stage 입력 표 9개만 pass. 09-06 pass1/pass2
  실측이 정확히 그 모양이다.
  → 전량 재현성 검사는 게이트가 아니라 **두 pass 의 `summary.tsv` `content_hash` 열 비교**다.
- **일일 갱신에서 이 절차는 성립하지 않는다.** 두 번 완주하면 16분이고, 매일 입력이 바뀌므로
  "같은 입력 → 같은 해시" 를 물을 자리가 없다. 재현성 검사는 입력이 안 바뀐 날 또는 규칙 변경
  PR 에서만 의미가 있다(추정).
- **`_asof/`** 는 `catalog` 산출. 표본 = `trading_calendar.asof_sample_dates`(5일) ×
  `asof_sample_tickers`(20종목), 뷰 4개 각 159,655행(실측). EG5c 는 **직전 표본과 행 단위 차이 0**
  요구(`catalog.py:248-300`). 스냅샷 3개 보관(실측).
  → 순수 append 면 과거 as-of 표본이 안 변하므로 EG5c pass 여야 한다. 변했다면 "과거가 다시
    쓰였다" 는 뜻이고 그것이 일일 갱신에서 필요한 경보다(추정, 강함).
  → 단 표본 날짜에 **최신일(2026-08-20)이 박혀 있어** 그 상수를 매일 올리면 표본 자체가 바뀌어
    EG5c 가 FAIL 하고 `--rebase-asof`(사람 승인)를 요구한다(`__main__.py:185-186`). 쟁점 ③.

### 2-5. `_pinned/` 은 GC 가 없다 — 매일 재빌드의 숨은 비용

`inputs._write_build_record`(`inputs.py:137-157`)는 모든 BuildRecord 를 합쳐 보관하고
`manifest.commit()` 을 **일부러** 부르지 않는다(그 함수가 keep 밖 `v=` 를 rmtree 하므로). 실측:

- `_pinned/` 아래 표 69개(stg 42 + equity 27), `v=` 디렉터리 **165개**
- 상위: `dataset_profile` 16판 · `universe_daily` 10판 · `corp_event` 8판 · `flow_daily` 7판
- `_pinned/universe_daily` **2.0 GB** · `_pinned/price_daily` 1.2 GB
- `data/equity` 단독 **9.0 GB**, `data/stage` 단독 8.5 GB, 합쳐 세면 **16 GB**(하드링크 공유 ≈1.5GB)
- 디스크 여유 **271 GB**(`df -h`)

equity 자기 표의 `_pinned` 사본은 하드링크라 만들 때는 0바이트지만, 그 표의 keep=3 GC 가 옛 `v=` 를
지워도 **`_pinned` 하드링크가 inode 를 살려 둔다**. 매일 전량 재빌드하면 `price_daily`(≈300MB)
+ `universe_daily`(≈200MB) + 격자 3표로 **하루 1GB 안팎이 회수 불가로 쌓인다**(추정, 위 판당 크기에서
계산). 271GB 기준 ~9개월. 일일 운영 전 `_pinned` GC 정책 필요.

---

## 3. 게이트 실행 — 빌드에 내장돼 있고, `gate_all` 은 baseline 전용이다

### 3-1. 네 경로

| 경로 | 무엇 | 언제 |
|---|---|---|
| `run_equity.sh <t>` → `build` | 빌드 **직후 게이트 전량**(`build.py:206-215`), 실패면 폐기 | 매 빌드(자동) |
| `equity_gate_all.sh` → `gate <t>` ×28 | **커밋된 판 재판정만**(빌드·폐기 없음) | baseline 상수 변경 후 |
| `equity catalog` | 뷰 8개 재생성 + **EG11·EG5c·EG3_firm_mktcap** | 빌드·GC 뒤 필수 |
| `equity contract` | **EGC-01·02·03·04·05·10**(커널 어댑터 ↔ duckdb 독립 읽기 대조) | 빌드 뒤 |

**일일 갱신에서 "게이트를 따로 돌린다" 는 개념은 필요 없다 — 재빌드가 곧 게이트다.**
따로 돌려야 하는 것은 `catalog` 과 `contract` 둘뿐이다.

### 3-2. 소요 (실측)

- **`equity_gate_all.sh` 전량 재판정 ≈ 175초** (09-06 `s22_gateall` 로그 mtime: 10:07:26 → 10:10:21.8).
  상위: `dataset_profile` 56.7s · `credit_daily` 33.8s · `universe_daily` 19.1s · `flow_daily` 17.4s ·
  `adj_factor` 9.9s · `short_daily` 8.6s · `price_daily` 7.3s · `corp_event` 4.9s. 나머지 <4s.
  (그 실행에는 `price_adj_daily` 가 없다 — S23 이전 판)
- **`catalog` ≤ 94초** (직전 로그 12:38:20 → `_catalog_meta.written_at` 12:39:54.37). 실소요 60~90s(추정).
- **`contract` ≈ 9초** (12:39:54 → `_contract_meta.written_at` 12:40:03).

### 3-3. 신규 행에 민감한가 — 매일 vs 주 1회

| 게이트 | 신규 행 민감도 | 매일 필요? | 근거 |
|---|---|---|---|
| **EG0** 입력 고정·선언 | **높음** — stage 컬럼 변경·stage 게이트 fail 을 그날 잡는다 | 예 | `gates.py:123-168` |
| **EG7** 격리 비율 | **높음** — 새 사유·비율 초과면 그날 폐기 | 예 | `gates.py:176-196` |
| **EG1** 격자 등식 | **높음** — 원장 append-only 판본 중복이 여기서 터진다 | 예 | `gates.py:199-212` |
| **EG2** PIT 불변식 | 중 | 예 | `gates.py:215-244` |
| **EG3** 키 유일·사유 어휘 | **높음** — 신규 티커·신규 어휘가 즉시 걸린다 | 예 | `gates.py:246-265` |
| **EG17** 캘린더 무결 | **높음** — `max(date) == backfill_end` 상수 대조 | 예(상수 동반) | `rules_s02.py:52-93` |
| **EG20** 원주가 불변 | 중 | 예 | `rules_s04.py:192` |
| **EG6/EG8/EG9** 판본·교차원천·커버리지 | 중 — 임계 기반, 하루치로는 잘 안 움직인다 | 예(비용 0) | 각 `rules_s*.py` |
| **EG10** 팩터 준비도 | 낮음 — 선언 기반 | 예(0.5s) | `rules_s20.py:389-469` |
| **EG4** 골든 픽스처 | **낮음** — 선언·선언조인으로만 정해지는 값 | 예(비용 0) | `gates.py:268-303` |
| **EG5a** 재현성 | **일일에서 무의미** — 전량 재빌드는 `skip(inputs_changed)` | 아니오 | `gates.py:335-338` |
| **EG11** 뷰 결정성 | 낮음 | 예(catalog 안) | `catalog.py:200-230` |
| **EG5c** as-of 불변 | **일일 갱신의 핵심 경보** — 과거가 다시 쓰였는지 | **예** | `catalog.py:248-300` |
| **EGC-01~05·10** 소비자 계약 | 중 — 표본(20티커·7일·폐지 20구간) | 예(9초) | `contract.py:493-499` |

**주 1회로 미룰 수 있는 것은 실질적으로 없다** — 게이트는 빌드에 내장돼 있고 `catalog`+`contract`
합쳐 100초 안쪽이다. 다만 **`equity_gate_all.sh`(175초)는 baseline 을 바꾼 날에만** 의미가 있으므로
일일 파이프라인에 넣을 필요가 없다.

미구현 확인(실측 grep, `src/equity/` 전체): **EG12·EG13·EG14·EG15·EG18·EG19 가 없다.**
특히 **EG13(available_date 미래값)·EG14(파티션 경계 누락)** 은 일일 갱신에서 정확히 필요한
술어인데 없다. EG19(as-of 단조성)도 없다.

### 3-4. baseline — 갱신 규칙과 lock, 그리고 **매일 움직여야 하는 날짜 상수 5개**

정본 = 서버 `data/equity/baseline.json`, 확정본 = 저장소 `database/src/equity/baseline_locked.json`,
검사 = **바이트 동일성**(`check_baseline_lock.py:58-66`). 설치 경로는 `scp` 하나뿐.

**지금 상태(실측)** — 두 파일이 다르다:

```
lock   343a51b6…  76,406B   baseline_locked.json   (_locked_at 2026-09-06)
target a6067bc1…  72,896B   서버 baseline.json     (mtime 2026-09-07 02:13)
게이트가 읽는 상수 차이 3건:
  consensus_daily.v3_wise_match_min      lock=0.65  → 서버 0.93
  consensus_daily.v3_wise_value_tol_rel  lock=0.001 → 서버 0.01
  corp_event.bonus_ratio_window_sessions lock=<없음> → 서버 25
```

09-07 서버 변경이 저장소 확정본에 역류하지 않았다. 자동화 전에 **락을 먼저 맞춰야** 한다.

**날짜 상수 — 하루 갱신마다 움직여야 하는 것(실측 `baseline_locked.json`)**:

| 상수 | 현재 값 | 성격 | 안 올리면 |
|---|---|---|---|
| `trading_calendar.backfill_end` | 2026-08-20 | **게이트 전용**(EG17) | `max(date)` 가 새 날짜가 되는 순간 **EG17 FAIL → trading_calendar 폐기 → 전 체인 중단** |
| `security.backfill_end` | 2026-08-20 | **산출식**(`sql/security.sql:69`) | 새 날 폐지 종목이 "아직 살아 있음(NULL)" 으로 남는다 = **조용한 생존편향** |
| `adj_factor.asof_for_jump_check` | 2026-08-20 | 게이트 전용(EG8) | 새 구간 점프를 안 본다(조용) |
| `trading_calendar.asof_sample_dates[4]` | 2026-08-20 | 게이트 전용(EG5c·EG11) | 표본이 옛 마지막날에 고정 — 최신 구간을 EG5c 가 안 본다 |
| `universe_daily.contract_probe_dates[6]` | 2026-08-20 | 게이트 전용(EGC-02·05) | 계약 검사점이 최신일을 안 본다 |
| `trading_calendar.calendar_start` | 2010-01-04 | 게이트 전용 | 고정 |

**첫째가 hard blocker.** `eg17_calendar_integrity`(`rules_s02.py:88-92`)가 `max(date) != backfill_end`
를 무조건 FAIL 로 낸다. 캘린더는 `ORDER` 1번이라 그 자리에서 파이프라인이 멈춘다. 그리고 상수를
바꾸는 유일한 승인 경로가 "저장소 락 파일 수정 + scp" 이므로, 현재 규약대로면 **일일 갱신이 매일
저장소 커밋을 요구한다**. 쟁점 ①.

baseline 승인 루프(GATES §7-3): 미등재 상수는 `skip(no_baseline)` + 측정치 기록 → 사람이 등재 →
2회차부터 정식 판정. `growing=true` 축(컨센서스·문서 인덱스)은 값 변화 자체가 실패가 아니다.

---

## 4. 카탈로그(dataset_profile·factor_readiness) — 무엇이 자동이고 무엇이 아닌가

### 4-1. 자동 재계산 / 선언 고정

`rules_s19.py:9-13` 이 두 갈래를 명시한다.

| 컬럼 | 출처 | 매 빌드 자동 갱신? |
|---|---|---|
| `coverage_from`·`coverage_to`·`n_observed`·`n_denominator`·`estimated_coverage_pct`·`coverage_by_mktcap_quintile` | **실측** — 고정한 equity 파티션을 직접 잰다(`rules_s19.py:300-379`) | **예** |
| `recommended_lag_sessions`·`recommended_lag_days` | **선언** — 각 표의 `FieldProfile`(`rules_s19.py:188-189`) | **아니오(코드 상수)** |
| `point_in_time`·`requires_confirmation`·`unit`·`disclosure_basis` | 선언 | 아니오 |
| `available_date_basis`·`source_stage_tables`·`supported_cell_kinds` | 소유 테이블에서 기계 유도 | 예(구조 변경 시) |
| `factor_readiness.first_usable_date` | **계산** = 요구 필드 `coverage_from` 의 최댓값(`sql/factor_readiness.sql:74`, `rules_s20.py:19`) | **예** |
| `factor_readiness.status`·`blocked_reason`·`required_columns` | 계산(`dataset_profile` 조인) | 예 |

→ **커버리지·`first_usable_date` 는 자동으로 따라온다.** 단 두 표를 매번 다시 지어야 갱신된다
(HANDOFF §2: "어떤 표든 다시 지으면 이 둘도 다시 지어야 한다"). 비용 **71.2s + 0.5s**(전체의 15%).
→ **`recommended_lag_sessions` 는 자동으로 안 바뀐다.** 랙이 틀리면 매일 그 값이 소비자에게 나간다.

### 4-2. TECH_DEBT 1 (스냅샷 지문이 이름표만 해싱) — 일일 갱신에서의 실제 경로

`catalog.snapshot_id`(`catalog.py:66-70`)와 어댑터 복제본(`_source.py:79-83`)이
`"{table}={build_id}"` 정렬 문자열만 해싱한다. **서버 build_id 는 마이크로초 시각이라 충돌하지
않는다** — 일일 갱신 자체가 이 부채로 데이터를 오염시키지는 않는다. 대신 **지문이 매일 바뀐다**:

1. `_asof/<view>/<snapshot_id>/` 가 매일 새로 생기고(`catalog.py:376-393`) 보관은 `ASOF_KEEP`=3
   → **as-of 이력이 3일치만 남는다.** EG5c 는 직전 1판만 보므로 판정 자체는 성립.
2. 워크벤치가 내보내는 모든 결과의 `snapshot_id`·`tape_hash`·`run_fingerprint` 가 매일 바뀐다
   (`_adapter.py:327·417·686·825·999`, `domain/portfolio/_compiler.py:246`). → 어제 백테스트와
   지문 대조 불가, 그리고 `_adapter.py:129` 가 "dataset 지문 ≠ target_tape 지문" 을 거부하므로
   **어제 만든 target_tape 가 오늘 거부된다**(추정 — 코드 경로 확인, 실행 미확인).
3. `_catalog_meta.snapshot_id ≠ 현재 MANIFEST 지문` 이면 어댑터가 **카탈로그를 stale 로 막는다**
   (`_source.py:155-190`). 매일 재빌드 뒤 `catalog` 을 안 돌리면 `financial.*`·`consensus.*` 9필드가
   통째로 unavailable. → §5-2 가 지금 그 상태다.

### 4-3. TECH_DEBT 4 (어댑터 상수 available_date) — **문서가 낡았다. 코드는 고쳐져 있다**

`TECH_DEBT.md` §4 는 "어댑터가 `lag_sessions=0` 을 우긴다, 30필드 중 25가 한 세션 이르다, 심각도 상"
으로 적혀 있으나 **현재 코드는 `dataset_profile` 을 읽는다**:

```
_adapter.py:380-390  """`dataset_profile` 의 field_id → (랙 세션, 근거). 표가 없으면 빈 dict(폴백)."""
                     SELECT field_id, recommended_lag_sessions, available_date_basis …
_adapter.py:411      return source.lag_sessions, f"{source.lag_basis} (fallback: no dataset_profile row)"
_adapter.py:478-488  list_fields() 가 그 값을 recommended_lag_sessions 로 내보낸다
```

HANDOFF §9·§12 ②가 커밋 `8014655`(09-07)로 기록. **TECH_DEBT.md §4 본문만 갱신되지 않았다** —
문서를 근거로 계획하면 끝난 일을 다시 하게 된다.

**남은 위험은 폴백이다.** 프로파일 표·행이 없으면 **조용히 `_specs.py` 상수(전부 0세션)로 돌아간다**.
`available_date_basis` 에 `fallback` 이라 적히지만 실패하지 않는다. 일일 갱신에서의 사고 경로:

- **상황**: 매일 서버 → 소비자 로컬로 표를 rsync 한다.
- **인풋**: `fetch_equity_local.sh <dest> minimal` 대신 손으로 표 몇 개만 복사(또는 목록에서 누락).
- **에러 위치**: `_adapter.py:405-411` — 프로파일 행이 없으면 `source.lag_sessions`(0)로 폴백.
- **위험성**: 격자 3표(`flow.*`·`short.*`·`credit.margin_balance`)가 D일 값을 D일에 연다.
  `credit.margin_balance` 는 T+1 공표가 확정 사실(`_specs.py:58-61`) → **확정 look-ahead**.
  조용하고 성과를 낙관 방향으로 틀리게 만든다.
- **막는 법**: 루트에 `dataset_profile` 없으면 **기동 실패**로 바꾸거나, 전달 스크립트가 표 목록을 검증.

또 하나 남은 것: 어댑터가 `financial.revenue_basis`·`_prev` 를 아직 안 낸다(HANDOFF §8-3).

---

## 5. 소비자 전달 — 채널이 정해져 있지 않다

### 5-1. 읽는 방식 (실측)

| 소비자 | 읽는 것 | 카탈로그 필요? |
|---|---|---|
| **엔진 커널** `backtest_engine/adapters/equity_duckdb.py` | parquet 5표: `price_daily`·`security_span`·`trading_calendar`·`security`·`adj_factor`(`:59-63`), MANIFEST `current_build` 경유 | **아니오** |
| **워크벤치** `strategy_workbench/adapters/outbound/equity_duckdb/` | 21표 + `equity.duckdb` 매크로 8 | `financial.*`·`consensus.*`·`v_firm_mktcap` 만 |
| `price.adj_close` | **표 `price_adj_daily` 직접**(S23) | 아니오 — 카탈로그가 낡아도 산다 |

전달 경로:

- **서버 duckdb 직접 읽기는 하지 않는다.** 서버에 워크벤치·uvicorn 프로세스 없음(실측 `ps`).
- **정규 경로 = `database/scripts/fetch_equity_local.sh <로컬경로> [minimal|full]`** — rsync 로 표를
  받고 **카탈로그를 로컬에서 다시 만든다**(매크로가 절대경로를 굽기 때문, `:47-49`).
  `minimal` 12표 ≈2.1GB / `full` 21표 ≈3.7GB. `_pinned/`·`_asof/`·`_tmp/`·`_failed/` 는 안 받는다.
- **`share/`** 는 equity 와 무관 — 2026-08-27 키움 원장 parquet 공유본(실측 `ka10008` 등).
- **`~/quant-ledger/_export/equity/`** — 09-06 12:57 에 만든 **표당 단일 parquet 평면 사본 951MB**
  (실측). 저장소 어느 스크립트에도 생산자가 없다(grep 0건) → **손으로 만든 산출물(추정)**.
  MANIFEST 규약 밖이라 어댑터가 읽지 못한다. 채널 후보로 삼으려면 규약부터 정해야 한다.
- **`~/quant-ledger/_engine/backtest_engine`** = `equity contract --engine-src` 용 커널 소스 rsync 본.

기동(HANDOFF §13):
```
export STRATEGY_WORKBENCH_EQUITY_ADAPTER=duckdb
export STRATEGY_WORKBENCH_EQUITY_ROOT=/path/to/equity
cd backend && uv run --extra parquet --extra equity server
```
환경변수 없으면 `mock`, `duckdb` 인데 루트 없으면 `ValueError` 로 죽는다(조용한 폴백 없음 —
`_container.py:85-92`, `_http.py:21-31`).

### 5-2. 갱신 뒤 소비자가 새 데이터를 보게 되는 절차 — **지금 끊겨 있다**

필요한 순서(추정, 코드 근거):
1. 서버 재빌드(28표) → 2. **서버 `catalog`** → 3. 서버 `contract` → 4. 소비자 rsync
(`fetch_equity_local.sh`, 내부에서 로컬 catalog 재생성) → 5. 워크벤치 재기동(기동 시 MANIFEST·
프로파일을 읽는다).

**지금 2번이 안 돼 있다(실측).** `_catalog_meta.json` 은 09-06 12:38 판이고 현재 표와 **10개 어긋난다**:

```
adj_factor · consensus_daily · corp_event · dataset_profile · factor_readiness
fin_std · flow_daily · price_adj_daily · short_daily · universe_daily
```

→ `snapshot_id` 불일치 → `read_catalog()` 가 `usable=False, reason="catalog is stale — rebuild it"`
(`_source.py:180-188`) → 지금 서버 루트를 그대로 워크벤치에 물리면 **`financial.*`·`consensus.*` 가
전부 unavailable**. `_contract_meta.json`(09-06 12:40)도 같은 이유로 낡았다.

**교훈**: `catalog` 을 빠뜨리는 일이 실제로 일어난다. 일일 파이프라인은 재빌드와 `catalog` 을
**분리 불가능한 한 단위**로 묶어야 한다.

### 5-3. `equity_adapter="duckdb"` 컨테이너 상태 — **둘 다 완료**

WORKFLOW §0-1 이 정의한 완료 조건 두 개를 실측 확인:

| 조건 | 상태 | 근거 |
|---|---|---|
| 계약 테스트 `ADAPTERS` 에 `equity_duckdb` 진입 | **완료** | `backend/tests/contract/test_raw_observation_port.py:96` `ADAPTERS = [param("mock"), param("equity_duckdb")]`, 13개 테스트가 `indirect=True` 매개변수화 |
| `build_container(equity_adapter="duckdb")` 기동 | **완료** | `bootstrap/_container.py:64` `EQUITY_ADAPTERS = ("mock", "duckdb")`, `:85-92` |

계약 테스트는 서버 데이터가 아니라 `backend/tests/equity_fixture.py::build_workbench_root` 가 합성한
루트 위에서 돈다(`test_raw_observation_port.py:105-118`) — 일일 갱신과 무관하게 CI 로 돈다.
서버 데이터 대조는 별도로 `equity contract`(EGC-01~05·10, 9초)가 한다.

### 5-4. `backend/.local/` 의 역할

**equity 데이터와 무관하다.** 워크벤치 런타임 로컬 산출물 저장소:
- `backend/.local/backtest-runs` — 백테스트 run 아티팩트(`_container.py:118-120`, `backend/README.md:23`)
- `backend/.local/strategy-revisions.sqlite3` — 전략 리비전 저장소(`backend/README.md:140`)
- `.gitignore:258` 로 제외. 로컬 워크트리에는 아직 없다(실측).

---

## 6. 정정·재계산 파급 — "증분" 이 원리적으로 불가능한 표

전제: 지금 코드에 증분 경로가 없다(§2-3). 아래는 **가령 증분을 만든다면 어디까지 다시 계산해야
하는가**를 SQL 의미로 가른 것. 근거는 각 `sql/*.sql` 헤더 주석과 `_const` 창 값.

### 6-1. 원리적으로 전량 재계산이 필요한 표 (과거 파티션이 바뀐다)

| 표 | 왜 과거가 바뀌는가 | 근거 |
|---|---|---|
| **`disclosure_version`** | 원본 행의 `first_correction_dt`·`n_corrections` 는 **나중에 들어온 정정들의 집계**다. 오늘 들어온 [기재정정] 하나가 몇 년 전 접수 행을 다시 쓴다. 링크 후보 탐색도 `o.rcept_no < c.rcept_no` 전 이력 | `sql/disclosure_version.sql:21-23` |
| **`fin_std`** | `vintage_kind='api_restated'` 하나뿐이라 DART 재작성이 과거 `period_end` 행의 **값 자체**를 바꾼다. `revenue_basis_prev` 는 **직전 회계연도 같은 보고서** 행을 본다 | `sql/fin_std.sql:31-36`, HANDOFF §9 |
| **`adj_factor`** | 오늘 공시된 사건의 효력일이 과거일 수 있고(`effective_before_announce_max_days`=2555일=7년), `near_dup_window_days`=5 로 이웃 사건 판정이 바뀐다. `price_match_window_sessions`=40 전방 탐색 | HANDOFF §5-1, `rules_s05`·`rules_s06` |
| **`price_adj_daily`** | 누적곱 Π{fold_date ≤ d}. **과거 apply_date 계수가 새로 들어오면 그 날짜 이후 전 행**의 `adj_*`·`cum_*`·`n_factors_applied`·`n_unadjusted_events` 가 바뀐다 | `sql/price_adj_daily.sql:8-13` |
| **`universe_daily`** | ① `adv20_krw` 20거래일 창 ② `no_trade_run` 연속 run ③ **`no_trade_reason='corp_action_window'` = 사건 apply_date 가 `[D−5, D+45]` 세션 창** → 오늘 적재된 사건이 **과거 45세션**의 status 를 바꾼다 ④ `admin_window_td`=365일 | `sql/universe_daily.sql:29-46`, HANDOFF §5-1 |
| **`security`** | `delist_date_krx` = "마지막 존재일의 다음 거래일", 판정 기준이 `_const.backfill_end`. 상수가 움직이면 **전 종목 생사 판정이 다시 계산**된다 | `sql/security.sql:69` |
| **`security_span`** | 구간 끝·`end_reason='coverage_gap'` 이 캘린더 max 에 매여 있다 | `sql/security_span.sql:13` |
| **`dataset_profile`** | 26표 전부를 읽어 커버율 분자·분모를 **전 구간에서** 다시 잰다. 정의상 증분 불가 | `rules_s19.py:19-23` |
| **`factor_readiness`** | `dataset_profile` 의 함수. `first_usable_date` = coverage_from 최댓값 | `sql/factor_readiness.sql:74` |
| **`consensus_daily`** | `available_date` = **최초 관측**(전 이력 최소값). 원천 우선순위(v3 vs wise)도 전 이력 축 | HANDOFF §5-2, `rules_s17` |
| **`opinion_daily`** | `eg6_first_observation` — 같은 이유 | `rules_s18.py:215` |
| **`corp_event`** | 판본 선택(first_write_wins)·근접 중복 억제가 전 이력 축 | `sql/corp_event.sql` |
| **`trading_calendar`** | `prev_td`/`next_td` 체인 — 새 날짜 추가가 직전 마지막 행의 `next_td` 를 NULL→값으로 바꾼다. 1행뿐이지만 append 로는 못 쓴다 | `sql/trading_calendar.sql:9-13` |

### 6-2. 원리적으로 append 가 가능한 표 (그래도 지금은 전량 재빌드)

| 표 | 조건 |
|---|---|
| `price_daily` | 원장이 append-only 이고 과거 정정이 없다면 그날 파티션만. **KRX 정정이 들어오면 무너진다** |
| `index_daily` | 같음 |
| `flow_daily`·`short_daily`·`credit_daily` | 격자 (date, ticker) 순수 조인. 다만 **`universe_daily` 격자를 분모로 쓰므로 그 표가 과거를 다시 쓰면 같이 따라온다** |
| `holder_daily`·`ownership_snapshot`·`audit_opinion`·`shares_outstanding`·`treasury_stock`·`dividend_event`·`opinion_broker_daily` | 접수 축 append. 단 원장 append-only 판본 중복 접기가 전 이력 축(HANDOFF §7 ①) |
| `corp`·`corp_ticker`·`universe_policy` | 소형(3,478 / 5,088 / 13행) — 증분 논의가 무의미 |

**결론**: 28표 중 **13표가 원리적으로 증분 불가**이고 그중 `universe_daily`·`price_adj_daily`·
`dataset_profile` 이 소요 상위 4개 안에 있다. **증분으로 아낄 수 있는 시간은 많아야 전체의 20% 안팎**
(추정 — 격자 3표 175s + price_daily 26s ≈ 200s / 470s). **증분 설계는 값이 없다.**

---

## 7. "equity 하루 갱신 완료" 판정 체크 (제안)

전제: 매일 전량 재빌드. 기대치 근거는 §2-2·§3-2.

| # | 체크 | 명령·산출 | 기대치 (실측 근거) |
|---|---|---|---|
| 0 | stage 새 판 커밋 + 게이트 fail 0 | `data/stage/*/MANIFEST.json` | equity EG0-P03 이 자동 확인 |
| 1 | 날짜 상수 5개가 새 거래일로 갱신 | `check_baseline_lock.py` | **OK(바이트 동일)**. 지금은 MISMATCH(§3-4) |
| 2 | 28표 전량 재빌드 완주 | `logs/equity/rebuild_<pass>/STATUS` | **`TOTAL` ≈ 470±60 s**, rc 전부 0 |
| 3 | 게이트 fail 0 | `rebuild_<pass>/summary.tsv` 8열 | `EG*=fail` 0건. `EG5a=skip(inputs_changed)` 는 정상 |
| 4 | 행수가 하루치만 늘었다 | `summary.tsv` 6열 | `price_daily`·`price_adj_daily`·`universe_daily` = 전일 + 그날 유니버스(≈ **3,924**, 2026-08-20 실측). **감소는 즉시 중단 사유** |
| 5 | 판본 혼재 없음 | `summary.tsv` 7열 | 28표 전부 같은 `rules_version` |
| 6 | 카탈로그 갱신 + 뷰 게이트 | `python -m equity … catalog` | `ok … macros=8 skipped=[] tables=28`, **EG11 pass · EG5c `n_diff_total=0` · EG3_firm_mktcap pass**. ≤ 94 s |
| 6b | **EG5c ≠ 0 이면 = 과거가 다시 쓰였다** | `_failed/catalog_<snap>.json` | 정정 유입(§6-1)이면 정상, 아니면 사고. **자동 `--rebase-asof` 금지** |
| 7 | 소비자 계약 | `python -m equity … contract` | EGC-01·02·03·04·05·10 **전부 pass**, ≈ 9 s |
| 8 | 카탈로그 stale 아님 | `_catalog_meta.snapshot_id` == 현재 MANIFEST 지문 | 일치. 불일치면 `financial.*`·`consensus.*` 9필드 사망 |
| 9 | 대장이 자랐다 | `dataset_profile` 79행 / `factor_readiness` ready 54 | 행수·`ready` 감소는 회귀. `first_usable_date` 가 뒤로 밀리면 커버가 끊긴 것 |
| 10 | 격리 비율 | `EG7` metrics | 실측 여유: `flow_daily` 0.00998/0.05 · `short_daily` 0.00629/0.05 · `fin_std` 0.01275/0.03 |
| 11 | 소비자 전달 | `fetch_equity_local.sh` 완료 + 로컬 catalog 재생성 | **`dataset_profile` 포함 확인**(누락 시 §4-3 look-ahead) |
| 12 | 디스크 | `du -sh data/equity` | 전일 대비 ≈ **+1 GB/일**(추정). `_pinned/` GC 없으면 단조 증가 |

**돌리지 않아도 되는 것**: `equity_gate_all.sh`(175s) — baseline 을 바꾼 날에만.
**하루 총 소요 추정**: 재빌드 470s + catalog 90s + contract 9s ≈ **10분**(전달 rsync 별도).

---

## 플랜 작성자에게 — 결정이 필요한 쟁점

### ① 날짜 상수를 매일 어떻게 올릴 것인가 (**최우선 — 이걸 안 정하면 자동화 자체가 불가능**)
`trading_calendar.backfill_end` 가 새 거래일과 다르면 **EG17 이 FAIL 하고 첫 표에서 멈춘다**
(`rules_s02.py:88-92`). 그런데 baseline 의 유일한 승인 경로는 "저장소 `baseline_locked.json` 수정 →
scp"(바이트 동일성, `check_baseline_lock.py`)다. 선택지:
- (a) 매일 사람이 락 파일을 고치고 커밋·배포 — 지금 규약. 자동화와 양립 불가
- (b) `backfill_end` 를 상수에서 **파생값**(캘린더 max 또는 stage 원장 max)으로 바꾸고 EG17 은 다른
  술어(예: "stage 최신일과 일치")로 대체 — 규칙 판본 상향 + 재빌드
- (c) baseline 에 "날짜 상수 = 자동 갱신 대상" 구획을 두고 락 검사에서 제외
셋 다 `security.backfill_end`(산출식)까지 같이 움직여야 한다. **(b) 권고**(추정) — 상수가 아니라
사실이기 때문. 다만 EG17 이 "원천이 잘리면 조용히 짧아진 캘린더" 를 막으려 상수를 쓴 것이므로
대체 술어를 먼저 설계해야 한다.

### ② 매일 전량 재빌드로 갈 것인가
실측 근거는 "간다" 쪽으로 강하다: 완주 **470초**, 증분 불가 표 13개(§6-1)이고 그중 셋이 소요 상위,
증분으로 아낄 최대치 20% 안팎. **증분 설계는 비용 대비 값이 없다.**
남는 결정은 **부수효과 셋을 받아들일지**:
- `_pinned/` 무한 증가(≈1GB/일, GC 금지 — `inputs.py:144-145`). **GC 정책 필요.**
- `_asof/` 스냅샷 3판 = 3일치만 남는다(`ASOF_KEEP`). 늘릴지.
- `snapshot_id` 가 매일 바뀌어 **어제 만든 target_tape 가 오늘 거부된다**(`_adapter.py:129`, 추정).

### ③ EG5c 실패를 어떻게 다룰 것인가
`_asof` as-of 불변 게이트는 **일일 갱신에서 유일하게 "과거가 다시 쓰였다" 를 잡는 장치**다.
그런데 §6-1 대로 정정 유입은 **정상적으로도** 과거를 바꾼다 — 즉 매일 울릴 수 있다.
- 자동 `--rebase-asof` 는 **금지**(그 순간 감시가 사라진다)
- 필요한 것은 "얼마나 바뀌었나" 임계 + 어느 사건이 바꿨는지 귀속. 지금은 `diff_by_kind`·
  `diff_keys`(10건)만 남는다. **정정 귀속 리포트가 없다.**
- 표본 날짜에 최신일이 박혀 있어 ①과 얽힌다(상수를 올리면 표본이 바뀌어 diff 가 뜬다).

### ④ 소비자 전달 채널을 무엇으로 확정할 것인가
후보 셋 중 규약이 있는 것은 하나뿐이다.
- `fetch_equity_local.sh` rsync(정규, 2.1~3.7GB) — **매일 3.7GB 를 미는 게 맞는가**
- `_export/equity/` 평면 parquet 951MB(생산자 스크립트 없음, MANIFEST 규약 밖 → 어댑터가 못 읽음)
- 서버에 워크벤치 상주(지금 프로세스 없음, RAM 15GB 에 격자가 5~7GB 라 빌드와 충돌)
확정 시 **`dataset_profile` 필수 포함을 스크립트가 아니라 어댑터 기동 검사로 강제할지**도 같이
정해야 한다(§4-3 조용한 look-ahead 경로).

### ⑤ 자동화 전에 손으로 한 번 정렬해야 하는 것 (지금 밀려 있음)
1. **`catalog` 미실행 10표 stale** → 지금 서버 루트로 워크벤치를 띄우면 재무·컨센서스가 죽는다
2. **baseline 락 불일치 3건**(09-07 서버 변경이 저장소에 역류 안 됨)
3. **`rules_version` 혼재 e1.5.0~e1.14.0** — 전량 1회 재빌드로만 해소
4. **`TECH_DEBT.md` §4 본문이 낡았다**(코드는 `8014655`로 고쳐졌는데 문서는 "심각도 상")
5. **EG13(available 미래값)·EG14(파티션 경계 누락)·EG19(as-of 단조성) 미구현** — 셋 다 일일 갱신에
   필요한 술어. 특히 EG14 는 "그날 파티션이 통째로 비었는데 통과" 를 막는 자리다.
