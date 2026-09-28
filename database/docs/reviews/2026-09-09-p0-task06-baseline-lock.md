# P0-06 — equity baseline 락 불일치 판정 (2026-09-09 조사)

읽기 전용 조사. 저장소·서버 어디에도 쓰지 않았다.

---

## 0. 결론 한 줄

**서버 `baseline.json` 이 맞고, 저장소 `baseline_locked.json` 이 09-07 확정 결정 2건을 못 따라온
것이다.** 3건 전부 사람이 승인·커밋한 확정값이며, 서버 값은 이미 그 값으로 지어진 판이 커밋돼
있다. 락을 그대로 서버에 설치하면 `corp_event` 빌드가 **KeyError 로 즉사**한다.

---

## 1. 락 파일은 09-09 조사 이후 바뀌지 않았다

| 항목 | 값 |
|---|---|
| 저장소 HEAD | `db2e507` (2026-09-09 15:05:50 +0900), 브랜치 `feat/daily-p0` (origin/main 추적) |
| 락 파일 이력 | `git log --follow` 결과 **커밋 단 1건** — `71a3a19` (2026-09-06 19:20:45 +0900, S22) |
| 워킹트리 | clean (`git status --porcelain` 무출력) |
| 락 sha256 | `343a51b6ed7825fdd005dcdf7f7e52fa2edad21157bd640e1bdec760ceb069f9` · 76,406B |
| 서버 sha256 | `a6067bc1eeaeb24caaf64f2c5fab8a45f1017154205fbf92442658f38c4a5e07` · 72,896B (mtime 09-07 02:13 UTC) |

`check_baseline_lock.py` 실행 결과(exit 1):

```
MISMATCH — 바이트가 다르다
  게이트가 읽는 상수 차이 3건:
    consensus_daily.v3_wise_match_min: lock=0.65 target=0.93
    consensus_daily.v3_wise_value_tol_rel: lock=0.001 target=0.01
    corp_event.bonus_ratio_window_sessions: lock='<없음>' target=25
```

### 1-1. 상수 3건 말고는 **의미상 완전 동일**하다

전체 JSON 을 재귀 비교했다. 3건 외 비메타 차이 0건, `_measured` 67건 **전건 바이트 동일**,
`_pending` 4항목 동일, `_lock`·`_locked_at`·`measured_at` 동일.

3,510B 크기 차이는 **들여쓰기뿐**이다 — 락은 `indent=2`, 서버 파일은 `indent=1`.
검증: 락에 3건을 패치한 뒤 양쪽을 `json.dumps(sort_keys=True, indent=2)` 로 재직렬화하면
문자열이 완전히 같다.

> 이것이 뒤에 나오는 **주의점 ①** 의 근거다. 락에 상수 3건만 채워 넣어도
> `check_baseline_lock.py` 는 여전히 MISMATCH 를 낸다(공백 차이). 바이트 동일 규약을 만족시키려면
> 패치한 락을 서버로 `scp` 해야 한다 — 다만 그 설치는 **상수를 하나도 바꾸지 않는** 공백 전용
> 덮어쓰기라 재빌드·재판정이 필요 없다.

### 1-2. 서버 파일의 계보 — 락에서 두 번 손댄 것이다

서버 백업 파일이 이력을 그대로 보존하고 있다(서버는 UTC, 아래 괄호는 KST):

| 서버 파일 | 크기 | mtime(UTC) | 정체 |
|---|---:|---|---|
| `baseline.json.bak_s22` | 8,667B | 09-06 10:07 (19:07) | S22 락 설치 **직전**의 옛 baseline |
| `baseline.json.bak_s05_ratio` | 76,406B | 09-07 01:48 (10:48) | **락과 sha256 완전 일치** — S22 락 원본 |
| `baseline.json.bak_s17_tol` | 72,897B | 09-07 02:13 (11:13) | 락 + `bonus_ratio_window_sessions:25` |
| `baseline.json` | 72,896B | 09-07 02:13 (11:13) | 위 + 컨센서스 상수 2건 |

즉 **서버 파일 = 락 + 09-07 오전 두 번의 확정 편집**. 락이 뒤처진 쪽이지 서버가 실험값인 게 아니다.

---

## 2. 상수 3건 — 어느 값이 확정인가 (전건 "서버가 확정")

### 2-1. `consensus_daily.v3_wise_value_tol_rel` 0.001 → **0.01** · `v3_wise_match_min` 0.65 → **0.93**

**판정: 서버 0.01 / 0.93 이 확정. 락 0.001 / 0.65 는 결정 4 이전의 절단본 제안값.**

근거 4중:

1. **커밋** — `0faf4eb` "feat(equity): settle decisions 2, 3 and 4 against server measurements"
   (2026-09-07 11:42:54 +0900, author Dongmin Jeong). 본문:
   > Raised to 1% and the floor to 0.93 after measuring 0.950768 on the server
   > (5,504 of 5,789 matched, 6,192 overlapping). The output hash did not move — this changes
   > what the gate calls a disagreement, not any value.

   서버 편집 시각 09-07 02:13 UTC = **11:13 KST**, 커밋 29분 전. 순서가 맞물린다.

2. **결정 문서** — `database/docs/DECISIONS_PENDING.md:481-491` 결정 4 권고표:

   | 상수 | 지금 | 권고 | 근거 |
   |---|---:|---:|---|
   | `v3_wise_value_tol_rel` | 0.001 | **0.01** | v3 EPS는 정수 저장이라 0.1%는 저장 정밀도보다 촘촘하다 |
   | `v3_wise_match_min` | 0.65 | **0.93** | 1% 허용 시 실측 95.06%. 2%p 여유 |

   같은 줄에 "두 상수 모두 **게이트만 보는 값**이라 재빌드 없이 재판정만으로 반영된다".

3. **HANDOFF §8-2 서버 반영 이력(2026-09-07)** — `EQUITY_HANDOFF.md:462-480`:
   > 같은 날 게이트 상수도 하나 바뀌었다 — 컨센서스 허용 오차 0.1% → **1%**, 하한 0.65 → **0.93**
   > (산출 불변, `consensus_daily` 해시 그대로).
   > 배포 전 … baseline 백업 `baseline.json.bak_s05_ratio`·`.bak_s17_tol`.

   백업 파일 이름까지 이 문단이 직접 지목한다.

4. **서버 실측 재확인(오늘)** — `consensus_daily` MANIFEST 의 `current_build`
   `b_20260907T021359_941671Z`(rules e1.8.0, 145,316행):
   ```
   EG8_consensus_daily pass  n_match=5504  match_rate=0.950768699257212
                             v3_wise_value_tol_rel=0.01  v3_wise_match_min=0.93
   ```
   커밋 메시지 숫자와 소수점까지 일치. `skip(no_baseline)` 잔존 0.

**락이 결정 이전 상태라는 결정적 증거**: 락의 `_measured[v3_wise_match_min]` 은 아직
`pending_human_decision` 필드를 달고 있다 —
> "서버 실측 0.873(5,054/5,789)이 하한 0.65 를 크게 넘지만, 불일치 735건의 원인이 … 아직
> 규명되지 않았다. 원인 규명 전에 하한을 올리면 원천 차이를 회귀로 오인한다 — 값 유지."

결정 4 는 정확히 그 질문에 답한 문서다(불일치 735건 배타 분류: 주식수 기준 23 · v3 정수
반올림 151 · 관측 시점 224 · 미규명 337). 락은 **질문이 아직 열려 있던 시점의 스냅샷**이다.

### 2-2. `corp_event.bonus_ratio_window_sessions` 미등재 → **25**

**판정: 서버 25 가 확정. 락의 "없음" 은 락이 그 커밋보다 앞서서 그렇다.**

1. **커밋** — `87ef481` "feat(equity): recover bonus-issue ratios from KRX listed shares
   (rules e1.8.0)" (2026-09-07 10:47:27 +0900). `baseline_seed_s05.json` 에
   `"bonus_ratio_window_sessions": 25` 를 추가한 diff 가 그 커밋에 있다.
   서버 편집 시각 09-07 01:48 UTC = **10:48 KST**, 커밋 1분 뒤. 맞물린다.

2. **근거 문서** — `database/docs/RATIO_RECOVERY.md:84-85,133`:
   > 창 25 세션은 무상증자 신주가 권리락 후 3~4주에 상장되는 관행에서 나왔고, 재현율이 정점인
   > 지점이다(20: 75.9% · **25: 76.3%** · 30: 75.7% · 35: 75.3%).
   > **확정: 창 25 · 357건 회수.**

3. **코드가 이미 그 값을 요구한다** — `database/src/equity/rules_s05.py:448-449` 의
   `consts=("effective_before_announce_max_days", "krx_share_change_tol",
   "bonus_ratio_window_sessions")`, 소비 지점은 `database/src/equity/sql/corp_event.sql:362`
   (`a.n = bb.n0 + CAST(k.bonus_ratio_window_sessions AS INTEGER)`).
   판본 이력도 `model.py:53-58` 에 e1.8.0 으로 박혀 있다.

4. **서버 실측 재확인(오늘)** — `corp_event` `current_build` `b_20260907T045719_410257Z`
   (rules e1.9.0, 9,749행): `EG3_corp_event pass`,
   `bonus_ratio_window_sessions=25`, `n_by_ratio_basis={'disclosed':1507,'krx_shares':1072,'none':7170}`.
   **KRX 로 유도한 비율 1,072건이 이미 실려 있다.**

### 2-3. ★ 위험 — 락을 서버에 설치하면 빌드가 죽는다

`bonus_ratio_window_sessions` 는 게이트 상수가 아니라 **빌드 상수**(`EquityTable.consts`)다.
`database/src/equity/build.py:102-105`:

```python
missing = [k for k, t, m in keyed if baseline.get(t, m) is None]
if missing:
    raise KeyError(f"baseline constant not found — table={rule.name} keys={missing} ...")
```

미등재는 `SkipGate` 가 아니라 **KeyError** 다. 전 상수 커버리지를 기계적으로 확인했다:

- 서버 `baseline.json`: 현재 코드가 선언한 빌드 상수 **전건 등재 — 누락 0**
- 저장소 `baseline_locked.json`: **`corp_event.bonus_ratio_window_sessions` 1건 누락**

따라서 "서버를 락으로 되돌린다" 는 선택지는 **존재하지 않는다**. 되돌리면 `corp_event` 빌드가
즉사하고, 의존 사슬(`adj_factor` → `price_adj_daily` → `universe_daily` → …)이 전부 멈춘다.

---

## 3. 서버 현재 상태 재확인 (09-09 기준)

### 3-1. `rules_version` 분포 — 28표, 4개 판본 혼재 (코드는 e1.14.0)

| 판본 | 표 수 | 표 |
|---|---:|---|
| `e1.5.0` | **18** | audit_opinion, corp, corp_ticker, credit_daily, disclosure_version, dividend_event, holder_daily, index_daily, opinion_broker_daily, opinion_daily, ownership_snapshot, price_daily, security, security_span, shares_outstanding, trading_calendar, treasury_stock, universe_policy |
| `e1.8.0` | 1 | consensus_daily |
| `e1.9.0` | 4 | adj_factor, corp_event, price_adj_daily, universe_daily |
| `e1.14.0` | 5 | dataset_profile, factor_readiness, fin_std, flow_daily, short_daily |

코드 현재값은 `database/src/equity/model.py:25` `RULES_VERSION = "e1.14.0"`.
09-09 조사 이후 바뀐 것 없음(빌드 시각 최신이 09-08 04:36 UTC).

### 3-2. catalog / contract 지문 — stale, 10표와 어긋남

| | snapshot_id | written_at_utc | status |
|---|---|---|---|
| `_catalog_meta.json` | `24b6925ef3387bf5` | 2026-09-06T12:39:54Z | — |
| `_contract_meta.json` | `24b6925ef3387bf5` | 2026-09-06T12:40:03Z | pass |

두 파일의 `builds` 맵(28표)을 각 표 MANIFEST 의 `current_build` 와 대조한 결과
**카탈로그·계약 모두 동일하게 10표가 어긋난다**:

| 표 | catalog 가 가리키는 build | 실제 current_build |
|---|---|---|
| adj_factor | `b_20260906T095928_698833Z` | `b_20260907T050114_144919Z` |
| consensus_daily | `b_20260906T100431_515962Z` | `b_20260907T021359_941671Z` |
| corp_event | `b_20260906T095922_209768Z` | `b_20260907T045719_410257Z` |
| dataset_profile | `b_20260906T123433_143901Z` | `b_20260908T043513_442266Z` |
| factor_readiness | `b_20260906T123534_459852Z` | `b_20260908T043624_886197Z` |
| fin_std | `b_20260906T100348_848898Z` | `b_20260908T043249_897977Z` |
| flow_daily | `b_20260906T100103_572209Z` | `b_20260908T043418_521268Z` |
| price_adj_daily | `b_20260906T123721_652451Z` | `b_20260907T050128_632779Z` |
| short_daily | `b_20260906T100152_408123Z` | `b_20260908T043324_530172Z` |
| universe_daily | `b_20260906T095943_049042Z` | `b_20260907T050228_489679Z` |

`_asof/<view>/24b6925ef3387bf5/` 표본 4뷰(각 159,655행)도 09-06 지문 위에 고정돼 있다.
`equity.duckdb`(274KB) mtime 09-06 12:38 — 카탈로그 뷰도 09-06 판이다.

> 즉 **소비자가 보는 카탈로그는 09-06 판**이고, 그 뒤 09-07·09-08 에 지어진 10표는 카탈로그에
> 반영된 적이 없다. `contract status: pass` 도 09-06 판에 대한 pass 이지 지금 데이터에 대한
> 보증이 아니다.

### 3-3. 참고 — baseline.json 을 자동으로 고쳐 쓰는 코드는 없다

`database/src/equity/baseline.py` 는 읽기 전용(`FILENAME` 상수 + 로더뿐, write 경로 없음),
서버 `~/quant-ledger/scripts/` 도 `--baseline …/baseline.json` 을 인자로 넘길 뿐이다.
09-07 의 편집은 사람이 손으로 한 것이고 `.bak` 두 개가 그 흔적이다. 락을 설치한 뒤 다시
드리프트가 생길 자동 경로는 없다.

---

## 4. 권고

### 4-1. 락 파일에 넣을 정확한 JSON 변경 (`database/src/equity/baseline_locked.json`)

**필수 3건 — 상수 (서버 값으로 맞춘다)**

| 키 경로 | 현재 | → 바꿀 값 |
|---|---|---|
| `consensus_daily.v3_wise_match_min` | `0.65` | **`0.93`** |
| `consensus_daily.v3_wise_value_tol_rel` | `0.001` | **`0.01`** |
| `corp_event.bonus_ratio_window_sessions` | (키 없음) | **`25`** (신규 키) |

바뀐 뒤 두 블록의 최종 모습:

```json
"consensus_daily": {
  "cover_ratio_drop_max": 0.7,
  "cover_ratio_rise_max": 16.0,
  "v3_wise_match_min": 0.93,
  "v3_wise_value_tol_rel": 0.01
},
"corp_event": {
  "bonus_ratio_window_sessions": 25,
  "effective_before_announce_max_days": 2555,
  "krx_share_change_tol": 0.001,
  "near_dup_window_days": 5,
  "thresholds": { "EG7": 0.06 }
}
```

(`cover_ratio_drop_max` 0.7 / `rise_max` 16.0 은 **건드리지 않는다**. 시드 `baseline_seed_s17.json`
의 0.5 / 1.0 은 절단본 제안값이고 서버 확정본이 0.7 / 16.0 이다 — HANDOFF §5-2 와 일치.)

**같이 해야 하는 3건 — 근거 블록 (안 하면 락이 자기 값을 부정한다)**

서버 파일도 이 부분은 **09-06 판 그대로**라 지금 서버조차 내부 모순이다. 락을 고칠 때 같이 고친다.

1. `_measured[]` 중 `metric == "v3_wise_match_min"` 항목:
   - `value`: `0.65` → `0.93`
   - **`pending_human_decision` 키를 삭제** (결정 4 가 답했다)
   - `measured_at`: `"2026-09-07"` 추가
   - `seed_note` 를 `baseline_seed_s17.json` 의 갱신된 `note` 로 교체
     (`**확정(2026-09-07 서버 실측).** tol 0.01 에서 서버 일치율 **0.950768** …`)
   - `server_evidence.measured` 를 오늘 확인한 값으로:
     `{"match_rate": 0.950768699257212, "n_match": 5504, "n_overlap_measured": 5789}`,
     `build_id` `b_20260907T021359_941671Z`, `content_hash` `145316:d0ae713bdd5a6e58`
2. `_measured[]` 중 `metric == "v3_wise_value_tol_rel"` 항목: `value` `0.001` → `0.01`,
   `measured_at: "2026-09-07"`, `seed_note` 를 시드의 갱신 note 로 교체,
   `server_evidence.build_id`/`content_hash` 를 위와 같이 갱신
3. `_measured[]` 에 **`corp_event.bonus_ratio_window_sessions` 항목 신설** —
   지금 락에도 시드(`baseline_seed_s05.json`)에도 없다. 락 규약(`_lock` 문구:
   "`_measured[]` 는 등재된 상수 전건의 근거") 위반이므로 반드시 채운다. 근거는
   `RATIO_RECOVERY.md`(창 25 재현율 76.3% 정점 · 창별 회수 357건 86.4%)와 서버 게이트
   (`EG3_corp_event`, build `b_20260907T045719_410257Z`, `content_hash 9749:3befee5c625e778e`,
   `n_by_ratio_basis.krx_shares = 1072`).

**메타 2건**

- `_locked_at`: `"2026-09-06"` → 갱신일
- `_lock` 문구에 09-07 결정 반영 사실 한 줄 추가(선택)

**같은 PR 에서 같이 고칠 문서 (지금 stale)**

- `EQUITY_HANDOFF.md:346-347` §5-2 표 — `v3_wise_match_min` 0.65, `v3_wise_value_tol_rel` 0.001
  이 아직 옛 값. → 0.93 / 0.01
- `EQUITY_HANDOFF.md:639` — "`v3_wise_match_min` 상향 판단 — 서버 실측 0.873(하한 0.65)" 이
  §9 미해결 후속에 남아 있다. → 결정 4 로 종결됐으므로 취소선 처리
- `EQUITY_HANDOFF.md` §5-1 표 — `corp_event.bonus_ratio_window_sessions` 행이 없다.
  빌드 상수이므로 §5-1(재빌드 열 `corp_event`)에 추가
- `database/tests/conftest.py:40` 의 `SLICE_BASELINE_OVERRIDE` 는 **그대로 둔다**
  (절단본 겹침 45행이라 0.80 으로 낮춘 것이고 `0faf4eb` 가 이유를 적어 뒀다)

### 4-2. 서버 쪽에 할 일 — "되돌리기" 는 없다

```bash
# ✗ 절대 금지 — 지금 락을 그대로 설치하면 corp_event 빌드가 KeyError 로 죽는다
# scp database/src/equity/baseline_locked.json kael-server:~/quant-ledger/data/equity/baseline.json

# ✓ 4-1 을 적용해 락을 고친 **뒤**, 문서가 정한 유일한 설치 경로로
scp database/src/equity/baseline_locked.json \
    kael-server:~/quant-ledger/data/equity/baseline.json

# 확인 (exit 0 = 바이트 동일)
scp kael-server:~/quant-ledger/data/equity/baseline.json /tmp/server_baseline.json
uv run python database/scripts/check_baseline_lock.py /tmp/server_baseline.json
```

이 설치는 **상수를 하나도 바꾸지 않는다**(공백 + `_measured` 근거 문구만 바뀐다).
서버 백업은 이미 `.bak_s05_ratio`·`.bak_s17_tol` 두 개가 있지만, 관례대로 설치 직전
`cp baseline.json baseline.json.bak_<이번작업>` 을 하나 더 남기는 편이 낫다.

### 4-3. 정렬 순서(락 갱신 → 전량 재빌드 → catalog → contract) 주의점

**① 락 갱신 자체는 재빌드를 요구하지 않는다 — 재빌드의 이유는 따로다.**
3건 모두 이미 서버에 있고 이미 그 값으로 지어졌다. 락을 맞추는 것은 **저장소를 서버에
맞추는 기록 작업**이다. 전량 재빌드가 필요한 진짜 이유는 §3-1 의 `rules_version` 혼재
(18표가 e1.5.0 에 머물러 있고 코드는 e1.14.0)와 §3-2 의 catalog stale 이다. 두 문제를 한 PR 에
섞되 **근거를 분리해 적어야** 한다 — 안 그러면 "락을 고쳤더니 전 표가 다시 지어졌다" 로 읽힌다.

**② 순서를 지키되, 락 설치는 재빌드 앞이어야 한다.**
재빌드 도중에 baseline 이 바뀌면 앞 표와 뒤 표가 서로 다른 상수로 지어진다. 락 설치 → 재빌드
개시 사이에 다른 작업이 끼지 않게 한다.

**③ 판본을 올린 첫 빌드는 EG5a 가 `skip(rules_changed)` 다 — 반드시 2회 돌린다.**
HANDOFF §6 이 명시한다. `equity_rebuild_all.sh pass1` 후 `pass2` 를 돌리고
`logs/equity/rebuild_pass1/summary.tsv` 와 `pass2` 의 `content_hash` 열을 비교하는 것이
재현성 검사다. 게이트만 믿으면 안 된다(§7 ⑦ EG5a 한계).
1회차만 돌리고 "PASS 니까 끝" 으로 판단하면 안 된다.

**④ 18표가 e1.5.0 → e1.14.0 으로 점프하면 EG5a 비교 대상이 사라진다.**
그 18표는 09-06 이후 산출이 안 바뀌었을 가능성이 높지만, 판본이 올라가는 순간 직전 빌드와
해시를 못 비교한다. **재빌드 전에 현재 `content_hash` 를 전부 받아 적어 두고**(§3-1 표에 이미
있다) pass1 결과와 손으로 대조해야 "판본만 올랐고 값은 그대로" 를 증명할 수 있다.

**⑤ `catalog` 은 재빌드가 전부 rc=0 으로 끝난 뒤에만 돌린다.**
`catalog` 은 그 시점의 `current_build` 28건을 `snapshot_id` 로 접는다. 중간에 돌리면 지금과
똑같은 stale 상태가 새 지문으로 재생산된다. 한 표라도 rc≠0 이면 즉시 멈추는 것이 러너 규약이다.

**⑥ `catalog` 의 EG5c 가 `_asof` 표본에서 실패할 수 있다 — `--rebase-asof` 를 반사적으로 쓰지 마라.**
`_asof/<view>/24b6925ef3387bf5/` 는 09-06 `adj_factor`(5,733계수 판) 위에서 뜬 표본인데,
그 뒤 e1.8.0 무상증자 비율 복구로 `adj_factor` 5,507행 / `price_adj_daily` 512종목 조정가가
바뀌었다(HANDOFF §8-2). 조정가가 바뀐 이상 EG5c 가 걸리는 것이 **정상**이다.
`--rebase-asof` 는 승인 축이고 쓴 사실을 `EQUITY_DESIGN.md §10` 에 남기게 돼 있다 —
"실패하니까 rebase" 는 GATES §7-3 의 "baseline 을 느슨하게 고쳐 실패를 해소하지 않는다" 와
같은 종류의 위반이다. 왜 바뀌었는지 먼저 설명하고 나서 rebase 한다.

**⑦ `catalog` 실패 시 카탈로그는 교체되지 않는다 — 조용한 성공으로 오해하기 쉽다.**
실패하면 옛 `equity.duckdb`(지금 09-06 판)가 그대로 남고 `_failed/catalog_<snapshot>.json` 만
생긴다. 즉 **아무 일도 안 일어난 것처럼 보인다.** 반드시 `_catalog_meta.json` 의
`snapshot_id`·`written_at_utc` 가 실제로 바뀌었는지 확인하고 넘어간다.

**⑧ `contract` 는 맨 마지막. 서버 venv 에 `numpy`·`pyarrow`, `--engine-src $HOME/quant-ledger/_engine`
가 필요하다.** `contract` 도 `snapshot_id` 를 접으므로 catalog 성공 후에 돌려야 두 지문이 맞는다.
끝나면 `_catalog_meta.snapshot_id == _contract_meta.snapshot_id` 인지 확인한다.

**⑨ 마무리 확인 3종**
```bash
# (a) 락 ↔ 서버 바이트 동일
uv run python database/scripts/check_baseline_lock.py /tmp/server_baseline.json   # exit 0
# (b) skip(no_baseline) 잔존 0 · fail 0  (HANDOFF §7-4)
ssh kael-server "cd ~/quant-ledger && bash scripts/equity_gate_all.sh"
# (c) catalog·contract 지문이 28표 current_build 와 일치 (§3-2 대조를 다시)
```

---

## 5. 부수 발견 (이번 조사 범위 밖, 기록만)

- **`_measured` 근거 블록이 서버에서도 stale.** §4-1 의 "같이 해야 하는 3건" 은 락만의 문제가
  아니다. 지금 서버 `baseline.json` 도 상수는 0.01/0.93 인데 `_measured` 근거는 0.001/0.65 와
  `pending_human_decision` 을 들고 있다. 락 규약(`_lock` 문구)이 요구하는 "등재 상수 전건의 근거"
  가 깨진 상태이므로, 이번 설치가 그것까지 함께 고치는 것이 맞다.
- **`bonus_ratio_window_sessions` 는 시드에도 `_measured` 항목이 없다.**
  `baseline_seed_s05.json` 은 값 `25` 만 있고 근거 항목이 없다. 커밋 `87ef481` 이 상수는
  넣었지만 `_measured` 를 안 넣었다. 근거는 `RATIO_RECOVERY.md` 에 다 있으므로 옮겨 적으면 된다.
- **회수 건수 표기 불일치.** `RATIO_RECOVERY.md:133` "357건 회수" vs `EQUITY_HANDOFF.md` §8-2
  "354건 복구" vs 오늘 서버 실측 `n_by_ratio_basis.krx_shares = 1072`. 뒤 숫자는 e1.9.0 에서
  자사주·CB 6,602건이 추가된 뒤의 값이라 축이 다르다. 앞의 357 대 354 는 어느 쪽이 맞는지
  확인이 필요하다(이번 조사 범위 밖).
