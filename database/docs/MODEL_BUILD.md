# model 판 빌드 — 점수 판 · 게이트 · 인계

> 플랜 [`2026-09-24-v3-merge.md`](plans/2026-09-24-v3-merge.md) M2 W2-a(T2.5). 코드
> `src/model/build.py`(판) · `src/model/gates.py`(MG0~MG5) · `src/model/__main__.py`(CLI), 테스트
> `tests/test_model_build.py`. 입력 층은 [`FACTOR_INPUTS.md`](FACTOR_INPUTS.md), 출력 열 계약은
> `src/model/contracts.py`(`score_columns(spec)`·`INDICATOR_COLUMNS`)가 정본이다.

## 1. 목적

- factor_inputs 판 **하나**를 읽어 레지스트리(`config/models/*.toml`)의 spec 을 전부(또는 고른 것만)
  돌리고, 점수·지표를 spec 별 판으로 남긴다. 엔진은 `model.engines.ENGINES` 그대로다 — 이 층은
  계산하지 않는다.
- equity·factor_inputs 와 같은 판 규약(판 id · `stage.manifest` MANIFEST · 판 manifest · latest 포인터 ·
  게이트 FAIL 이면 포인터 유지)을 따른다. 인계(`src/deliver/`, 엑셀·텔레그램)는 이 층의
  `latest_<basis>.json` 만 보고 읽는다.

## 2. 실행

```bash
python -m model build --date 20260928 --basis morning \
    [--fi-build latest|<factor_inputs build_id>] [--specs all|<spec_id,…>] \
    [--primary scope@1.0] [--root data/model] [--fi-root data/factor_inputs] \
    [--min-prices-on-d 2000] [--min-ranked 100] [--keep 60]
```

| rc | 뜻 |
|---|---|
| 0 | 판 커밋 · `latest_<basis>.json` 갱신(MG5 warn 이 있어도 0) |
| 1 | 게이트 FAIL — 아무 spec 도 올리지 않는다(`_failed/<build_id>.json`, MANIFEST·latest 는 직전 성공 판 유지) |
| 2 | 입력·인자 오류(factor_inputs 판 없음 · 판의 date·basis 가 요청과 다름 · 모르는 spec · `--primary` 가 선택 밖 · 날짜 형식) 또는 예외 |

- 기본 루트는 `QL_HOME`(없으면 저장소 `database/`) 아래 `data/…` — factor_inputs CLI 와 같다.
- `--date` 는 factor_inputs 판의 기준일 D 다. `--fi-build latest`(기본)는
  `data/factor_inputs/latest_<basis>.json` 의 판을 쓰고, 그 판의 date·basis 가 `--date`·`--basis` 와
  다르면 거절한다(어제 판으로 오늘 점수를 내지 않는다). id 를 주면 그 판의 8표 `_meta.json` 으로 같은
  검사를 한다.
- `--specs` 기본 `all` = 레지스트리 전부(지금 `v2_percentrank@1.0` · `v3_zscore@1.0` · `v4_rank@0.1` ·
  `v4_rank@0.2`). `--primary`(기본 `scope@1.0` — 10-05 v4_rank@0.1 에서 바꿈)는 레지스트리와 무관한 인계 설정이고, 선택한 spec 안에
  있어야 한다.
- 일부 spec 만 돌려도 latest 가 그 판으로 바뀐다(판 manifest 의 `specs` 에 그 spec 만 있다). 운영 루트는
  `--specs all` 로만 돌리고, 과거 날짜·실험은 **별도 `--root`** 로 돌린다(factor_inputs 와 같은 규약).
- **언제 돌리나**: 아침 체인에서 factor_inputs 바로 뒤(M3 에서 `build_chain.sh` 에 잇는다 —
  `step "model"`, H_EQUITY ok 일 때만, rc 1 은 warn: equity·factor_inputs 판은 유효하고 점수 인계만
  나가지 않는다). 지금은 체인에 없고 손으로 돌린다.

서버(수동, 09-28 판):

```bash
cd /home/kael/quant-ledger && QL_HOME=/home/kael/quant-ledger PYTHONPATH=/home/kael/quant-ledger/src \
    .venv/bin/python -m model build --date 20260928 --basis morning
```

## 3. 판 규약

```
data/model/
  <spec_id>/MANIFEST.json                     # stage.manifest — current_build · keep=60 · builds[]
  <spec_id>/v=<build_id>/scores.parquet       # 점수 표(열 = score_columns(spec))
  <spec_id>/v=<build_id>/indicators.parquet   # 지표 긴 표(열 = INDICATOR_COLUMNS, v3·v2 는 0행)
  _runs/<YYYYMMDD>_<basis>.json               # 판 manifest — 성공·실패 모두. 같은 날 재실행은 덮는다. 단 FAIL 재실행은 같은 날 성공 기록을 덮지 않는다(실패는 _failed/ 에만)
  latest_<basis>.json                         # 마지막 성공 판(= 그 판의 _runs 내용)
  _failed/<build_id>.json                     # 게이트 FAIL 보고서(판 manifest 와 같은 모양)
  _tmp/<build_id>/                            # 쓰는 중 임시(끝나면 지운다)
```

- `<spec_id>` 는 `model_id@version` 그대로다(예: `v4_rank@0.1`).
- 판 id 하나(`m_<UTC>` — `stage.model.make_build_id(basis)`)를 선택한 spec 이 공유한다. spec 마다
  MANIFEST 포인터를 따로 바꾸므로 전환 순간에는 섞여 보일 수 있다 — **소비자는 `latest_<basis>.json` 의
  `build_id` 로 `<spec_id>/v=<build_id>/` 를 연다**(keep=60 — 거래일 석 달. 10-06 전엔 3 이라 같은 날
  재빌드 세 번에 전날 판이 지워졌다).
- 쓰기는 원자적이다: 게이트를 전부 통과한 뒤에만 `_tmp/<build_id>/<spec_id>/` 에 쓰고
  `<spec_id>/v=<build_id>/` 로 옮긴 다음 `stage.manifest.commit` → `_runs` → `latest` 순. FAIL 이면
  parquet 을 쓰지 않는다. **판 전체가 실패하는 것은 주 모델(`primary_spec`)이 FAIL 일 때뿐이다.** 비교
  모델만 FAIL 이면 그 spec 만 빼고(`excluded_specs`, MANIFEST·`v=<build_id>` 없음) 나머지를 올린다
  (2026-10-05 사용자 결정 N-11 '격리', mb1.2.0). 비교 모델의 엔진 예외도 그 spec 만 뺀다(주 모델 예외는
  전체 실패, D-01, mb1.3.0).
- MANIFEST `BuildRecord`: `n_rows` = 점수 행 수, `content_hash` = 점수 파일 해시(equity 와 같은 식),
  `partitions[0]` = `{path: "v=<build_id>", n_rows, content_hash, n_indicators, indicators_content_hash}`,
  `inputs` = `{"factor_inputs": <fi build_id>}`, `gates` = MG0~MG5(`name`·`status`·`detail`·`metrics`),
  `rules_version` = `mb1.0.0`.
- parquet 은 `hive_partitioning=false` 로 읽는다(경로의 `v=` 가 열로 붙지 않게).

## 4. 산출 파일

**열은 계약 그대로이고 더한 열이 없다.** dtype 규칙(`gates.score_dtypes(spec)`·`INDICATOR_DTYPES`,
골든 `score_history(_v2).parquet` 와 같다): 식별자·날짜·플래그·사유 = VARCHAR(`stock_code`·`ticker`·
`score_date`('YYYY-MM-DD')·`spec_id`·`exclude_reason`·`sector_l1`·`sector_l2`·`coverage_state`·`key`·
`bucket`·`role`·`flag`·`*_flag`) · `rank`·`n_buckets_used` = BIGINT · `excluded` = BOOLEAN · 나머지 DOUBLE.

| spec | scores.parquet | indicators.parquet |
|---|---|---|
| `v3_zscore@1.0` | v3 `score_history` 48열(`stock_code` 키, 종합 `composite_score`). 전 행 순위(제외 없음), `(−종합, 코드)` 순. `growth_score`·`sentiment_score`·`volatility_score`·`size_score`·`foreign_score`·`shareholder_score` 는 항상 NULL | 0행(스키마만) |
| `v2_percentrank@1.0` | v2 `score_history_v2` 21열(`stock_code` 키, 종합 `total_score`). 전 행 순위, `(−종합, 코드)` 순 | 0행 |
| `v4_rank@*` | 기본 11열 + 버킷마다 `<bucket>_score`(0~100). 순위 행이 먼저(rank 순), 제외 행(`excluded`=true · `exclude_reason` · `rank` NULL)이 뒤에 종목코드 순. D-13 적격성 탈락은 버킷·종합이 NULL, 게이트 제외(`pull_gate`)는 점수가 남는다 | 종목 × 지표(spec 순서) 행 — `role` score/display, `raw`·`pct`(0~100, display 는 NULL)·`flag` |

- 점수 대상은 `fi_universe.eligible` 종목뿐이다(v3 은 여기에 시총 ≥ 1,000억, v2 는 시총 > 0, v4 는
  보통주·유예 재판정). **종목명·시장·업종 이름·시총은 점수 표에 없다** — 인계가 필요하면 판 manifest 의
  `fi_build_id` 로 `data/factor_inputs/fi_universe/v=<fi_build_id>/part0.parquet` 를 조인한다.

## 5. 판 manifest (`_runs/<YYYYMMDD>_<basis>.json` = `latest_<basis>.json`)

| 키 | 뜻 |
|---|---|
| `layer` | `"model"` |
| `status` | `"ok"` · `"gate_failed"`(latest 에는 ok 만 온다) |
| `build_id` | 모델 판 id(`m_…`) |
| `date` · `basis` | 판 기준일 D(ISO) · `morning` |
| `fi_build_id` | 읽은 factor_inputs 판 id |
| `generated_at` | UTC(`…Z`) |
| `specs` | `{spec_id: {n_scores, n_ranked, n_excluded, gates: {MG0…MG5: {status, detail, metrics}}}}` — spec_id 순. ok 판에는 올린 spec 만, gate_failed 판에는 고른 spec 전부(엔진 예외 낸 비교 모델은 `{error}` 하나) |
| `excluded_specs` | 게이트 FAIL·엔진 예외로 이번 판에서 뺀 비교 모델 — 게이트로 뺀 것은 `specs` 와 같은 모양, 엔진 예외로 뺀 것은 `{error: "<예외 클래스>: <메시지>"}`(500자에서 자름, D-01). 없으면 `{}`(gate_failed 판도 `{}`) |
| `primary_spec` | 인계 대표 모델(`--primary`) |
| `elapsed_s` | 적재·엔진·게이트 소요(초) |

게이트 `status` 는 `pass` · `fail` · `skip` · `warn`(MG5 만)이다. 예(합성 보드 40종목, 두 번째 판 —
테스트 픽스처로 실제 생성, 게이트는 v2 하나만 펼쳤다):

```json
{
 "layer": "model", "status": "ok", "build_id": "m_20260929T031515_775209Z",
 "date": "2026-09-28", "basis": "morning", "fi_build_id": "m_20260929T000500_000000Z",
 "generated_at": "2026-09-29T03:15:16Z",
 "specs": {
  "v2_percentrank@1.0": {"n_scores": 40, "n_ranked": 40, "n_excluded": 0, "gates": {
   "MG0": {"status": "pass", "detail": "점수·지표 열이 계약과 같고 열마다 타입이 하나다",
           "metrics": {"scores.columns_mismatch": 0, "indicators.columns_mismatch": 0,
                       "n_type_violations": 0, "type_violations": {}}},
   "MG1": {"status": "pass", "detail": "점수 40/40 = 1.0000 ≥ 0.95",
           "metrics": {"coverage_below_min": 0, "n_scores": 40, "n_eligible": 40, "n_covered": 40,
                       "n_outside_universe": 0, "coverage": 1.0, "coverage_min": 0.95}},
   "MG2": {"status": "pass", "detail": "두 번 실행의 점수·지표 해시가 같다",
           "metrics": {"scores_differ": 0, "indicators_differ": 0, "scores_sha256": "31f4…",
                       "rerun_scores_sha256": "31f4…", "indicators_sha256": "4f53…",
                       "rerun_indicators_sha256": "4f53…"}},
   "MG3": {"status": "pass", "detail": "NaN·inf 없음 · 순위 1…n · 열 규약",
           "metrics": {"n_nonfinite": 0, "duplicate_ticker": 0, "rank_not_1_to_n": 0,
                       "unranked_rows": 0, "composite_null": 0, "n_ranked": 40}},
   "MG4": {"status": "pass", "detail": "D 종가 종목 40 ≥ 10",
           "metrics": {"prices_on_d_below_min": 0, "n_prices_on_d": 40, "min_prices_on_d": 10}},
   "MG5": {"status": "pass",
           "detail": "Spearman 1.0000 · 상위 30 겹침 30 (전판 m_20260929T031515_421900Z)",
           "metrics": {"prev_build_id": "m_20260929T031515_421900Z",
                       "prev_score_date": "2026-09-28", "n_common": 40, "spearman": 1.0,
                       "spearman_warn": 0.8, "top30_overlap": 30, "top30_n": 30,
                       "top30_prev_n": 30, "warn": false}}}},
  "v3_zscore@1.0": {"n_scores": 40, "n_ranked": 40, "n_excluded": 0, "gates": {"…": "…"}},
  "v4_rank@0.1": {"n_scores": 40, "n_ranked": 28, "n_excluded": 12, "gates": {"…": "…"}},
  "v4_rank@0.2": {"…": "…"}
 },
 "primary_spec": "v4_rank@0.1",
 "elapsed_s": 0.3
}
```

## 6. 게이트 (spec 마다. 주 모델 FAIL 이면 판을 안 올리고, 비교 모델 FAIL 이면 그 spec 만 뺀다)

엔진을 같은 FactorInputs 로 **두 번** 돌린 결과(메모리)에서 판정한다. MG0 이 FAIL 이면 MG1·MG2·MG3·MG5 는
`skip(upstream_failed)`, MG4 는 입력 판정이라 그대로 돈다.

| 게이트 | 판정 | FAIL 조건(임계) |
|---|---|---|
| MG0 스키마 | 점수·지표 행의 열(순서까지) = 계약, 값 타입 = 열 dtype | 열이 다른 행이 하나라도 · 타입 위반(예: rank 에 문자열, DOUBLE 에 bool) |
| MG1 커버리지 | v3·v2: 자기 유니버스 규칙으로 센 종목(v3 = eligible ∧ D 가격 행 ∧ 시총 ≥ `min_market_cap` · v2 = eligible ∧ 시총 > 0) 중 점수가 나온 비율. v4 계열: 순위 종목 수(제외 사유별 수 기록) | v3·v2 비율 < **0.95**(유니버스 0 포함) · v4 순위 < **100**(`--min-ranked`, 09-28 서버 첫 판 317) |
| MG2 결정성 | 두 실행의 점수·지표 직렬화(JSON, 부동소수 repr) sha256 | 해시가 다르면 |
| MG3 온전성 | NaN·inf · 종목 중복 · 순위 = 순위 행의 1…n · v3·v2 전 행 순위·종합 있음 · v3 항상 NULL 6열 · v4: 종합·버킷 점수·지표 백분위 ∈ [0, 100], `excluded` ⇔ `exclude_reason` ⇔ rank NULL, 순위 행은 종합 있음, `spec_id` 일치 | 위반이 하나라도 |
| MG4 신선도 | 전체 `fi_prices`(eligible 로 자르기 전)에서 D 종가가 있는 종목 수 — v3 원본 stale guard(`backend/scoring/engine.py:40-51`, `V3_MIN_DAILY_PRICES_THRESHOLD`)와 같은 하한 | < **2,000**(`--min-prices-on-d`) |
| MG5 전판 대비 | 같은 spec 의 직전 성공 판(MANIFEST `current_build`)과 종합 Spearman(공통 종목, 동률 평균순위)·상위 30 겹침·공통 종목 수 기록 | **FAIL 없음** — Spearman < **0.8**(또는 셀 수 없음)이면 `warn`. 첫 판·전판 파일 없음은 `skip(no_previous)` |

- 플랜 §3-4 원안(MG0 스키마 · MG1 행수 · MG2 NaN·rank · MG3 골든 · MG4 순위 상관 · MG5 manifest 완비)과
  번호가 다르다 — 09-29 W2-a 지시가 정본이다. 골든 대조(G-M3 ②)는 테스트(`test_model_v3_port`·
  `test_model_v2_port`·이 층 `test_golden_tree_reproduces_the_ported_engines`)가 맡는다.
- MG5 는 전판이 **다른 날짜**여도(과거 날짜를 운영 루트에서 돌린 경우 등) 그대로 비교한다 —
  `prev_score_date` 로 확인한다.

## 7. 인계(deliver)가 읽는 법

1. `data/model/latest_morning.json` → `status == "ok"` · `date` · `build_id` · `primary_spec` · `specs`.
2. spec 마다 `data/model/<spec_id>/v=<build_id>/scores.parquet`(·`indicators.parquet`),
   `read_parquet(…, hive_partitioning=false)`.
3. 이름·시장·업종 이름·시총·거래대금은 `fi_build_id` 의 `fi_universe` 에서 조인한다.
4. 경고는 `specs.<spec>.gates.MG5.status == "warn"`(순위가 전판과 크게 달라짐)이다. latest 에는 FAIL
   판이 오지 않는다 — 그날 FAIL 이면 latest 의 `date` 가 D 보다 앞선다(인계가 날짜를 확인할 것).
   `excluded_specs` 에 있는 비교 모델은 그날 판에 없다 — 엑셀은 그 모델 열을 빼고 메타에 사유를 적는다.
5. 전일 순위·Δ순위 1W·1M·순위 흐름은 그날들의 `_runs/<YYYYMMDD>_morning.json` 의 `build_id` 로 같은
   경로를 연다(keep=60. 판 파일이 없으면 그날은 비우거나 흐름에서 뺀다 — `deliver/trend.py`).

## 8. 테스트

`tests/test_model_build.py` — 합성 보드 트리(`test_model_v4_rank.Board`, 40종목 · 네 spec) 왕복(판 규약·
열·dtype·엔진 직접 실행과 비트 일치·MANIFEST·판 manifest·latest), 골든 트리(`tests/tools/compat_to_fi`
→ v3 579·v2 619 행 = 이식 엔진 = v3 원본 순위), 실제 factor_inputs 판(`test_factor_inputs.make_roots`)
→ v2, 게이트마다 엔진을 감싸 한 가지씩 망가뜨린 FAIL 경로(MG0 열·타입 · MG1 v3 비율·시총 하한·v4 100 ·
MG2 비결정 · MG3 6가지 · MG4 골든 618 < 2,000) · FAIL 이면 latest·MANIFEST 유지 · 비교 모델 FAIL 격리 ·
주 모델 FAIL 이면 전부 미공개 · MG5 기록·warn ·
keep GC · spec 선택·primary · 입력 판 거절 · CLI rc.
