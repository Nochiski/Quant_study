# 영역 D — 모델 입력·모델 (factor_inputs · model) 전수 조사

- 영역: D (factor_inputs → model)
- 범위: src/factor_inputs/*(queries·build·gates·__main__), src/model/*(contracts·registry·build·gates·engines/*·__main__), config/models/*.toml, docs/FACTOR_INPUTS.md·MODEL_BUILD.md·FACTORS.md, 서버 data/factor_inputs·data/model
- 시작: 2026-10-06 02:53 KST
- 정본 코드: ~/orca/workspaces/Quant_study/v3-merge/database (feat/v3-merge)
- 서버: kael-server ~/quant-ledger (읽기 전용)

## 결함

### D-01 비교 모델 '격리'(N-11)가 게이트 FAIL 만 덮고 엔진 예외·설정 오류는 못 막는다 — v4·v2 가 예외를 내면 scope 판·엑셀도 0건 [중]
- 상황: 운영 루트 `python -m model build --specs all`(기본). 레지스트리에 scope@1.0(주)·v3_zscore@1.0·v2_percentrank@1.0·v4_rank@0.1·@0.2.
- 인풋: 비교 모델 엔진이 예외를 던지는 입력(예: v4 `_check` 의 계약·spec 검증 `ValueError`, 지표 계산 중 ZeroDivision 등) 또는 비교 모델 TOML 한 개의 오타·검증 실패.
- 에러 위치: `src/model/build.py:252-260` — spec 루프에서 `engine.run(spec, inputs)` 를 두 번 부르는데 try/except 가 없다. 격리는 `:264-267` 의 `gates.failed(rs)`(게이트 FAIL)만 본다. `build.py:138` `registry.all_specs()` 도 TOML 하나만 틀려도 `ValueError` 로 전체 중단. `__main__.py:54-56` 은 모든 예외를 rc 2 로 바꾼다.
- 위험성: N-11 "비교 모델(v4·v2) 실패는 그 모델만 빼고 scope 판·엑셀은 발송"과 다르다. 비교 모델 한 개의 런타임 예외가 주 모델 판·latest·엑셀을 모두 막는다(운영 중단). 테스트 `tests/test_model_build.py:264` `test_comparison_model_failure_is_isolated` 는 MG3 FAIL 만 흉내 내고 예외 경로는 없다. 카테고리: 운영 중단 · 문서(결정)-코드 불일치.
- 근거: build.py 252-267 직접 읽음, `grep -n "raise\|Exception" tests/test_model_build.py` → 예외 격리 테스트 없음.

### D-02 factor_inputs keep=3 · model keep=60 불일치 — 모델 판이 가리키는 fi 판이 사흘 뒤 지워진다(주간 엑셀은 과거 요일 판을 열다 실패) [중]
- 상황: N-17 로 모델 판만 keep 60. fi 는 `__main__.py:35` `--keep` 기본 3(표마다 `stage.manifest`). 서버 10-06 01:53 KST 백필(09-01~10-02, 22판)을 운영 루트에서 돌렸다.
- 인풋: (1) 서버 `data/factor_inputs/fi_universe/` 에 v= 판 3개(09-30·10-01·10-02 판)뿐. 모델 `_runs/20260901~20260929_morning.json` 19개의 `fi_build_id` 는 이미 없는 판. (2) 주간 엑셀 `deliver/excel_weekly.py:113` `load_week` 가 그 주 월~금(그리고 지난주 `:531-532`)의 판마다 `load_day(..., fi_root=fi_root, with_fi=False)` 를 부른다.
- 에러 위치: `src/deliver/view.py:116` — `with_fi` 와 무관하게 `fi_root` 가 있으면 `read_fi(fi_root, "fi_universe", run.fi_build_id)` 를 읽고, `reader.py:170-171` 은 판 디렉터리가 없으면 `DeliverError`. scope(v3_zscore 엔진) 점수에는 업종 열이 없어 이 읽기가 필수다(`view.py:117-121`). 원인은 `factor_inputs/__main__.py:35`(keep 3) ↔ `model/build.py:55`(keep 60).
- 위험성: 주간 엑셀(MODEL_EXCEL_SPEC B, 아직 미가동 — 서버 `data/deliver/weekly` 없음)을 켜는 첫 주에 월·화(·지난주) fi 판이 GC 돼 DeliverError 로 실패한다(운영 중단, 잠재). 또 22판 중 19판은 입력 판(fi)이 사라져 "그날 왜 이 순위였나"를 저장된 데이터로 추적할 수 없다(재현성·감사 추적 상실). 카테고리: 운영 중단(잠재) · 데이터 손실(입력 추적).
- 근거: `ssh kael-server ls data/factor_inputs/fi_universe` → v=m_20261005T165724·165736·165747 3개, MANIFEST builds 3개. `logs/manual/backfill_model_20261005T165348Z.log` 22판 fi id 대조. 실행 재현은 안 함(코드 경로 확인).

### D-03 v4 TOML `coverage_grace_days = 5` 는 죽은 설정 — fi eligible(G=0)이 먼저 잘라 v4 는 사실상 유예 0 [하]
- 상황: N-14 로 fi 기본 유예 0(`model/contracts.py:234`). v4 TOML(`config/models/v4_rank_0_1.toml`·`_0_2.toml` [universe])은 5 를 명시하고 DECISIONS N-14 도 "v4 TOML 은 5 를 명시"라고 적는다.
- 인풋: 마지막 신선일 뒤 1~5거래일 지난 종목(fi `coverage_state='lapsed'`, eligible=false).
- 에러 위치: `src/model/build.py:188-196` `load_inputs` 가 `WHERE eligible` 종목만 FactorInputs 에 싣고, `engines/v4_rank.py:562` `_universe` 도 `r["eligible"]` 부터 요구 → `:569-573` 의 `age <= u.coverage_grace_days` 분기는 나이 1~5 종목에 닿지 않는다. fi_consensus·WISE 재무도 fresh/grace 종목만 싣는다(`factor_inputs/queries.py:404-408`·`:543`). `v4_rank.py:492-500` `_check` 는 "기본보다 넓힐 수 없다"를 sec_types·markets·require_estimates 만 검사하고 유예 확대는 거절하지 않는다.
- 위험성: 비교 모델의 선언 규칙과 실제 규칙이 조용히 다르다(설정이 무시됨). v4 를 다시 주 모델로 올리거나 유예 효과를 비교할 때 오판. 카테고리: 문서 불일치 · silent config ignore.
- 근거: 코드 대조(위 줄). 서버 백필 로그 전 22판 states 에 grace 0건.

### D-04 유예 0(N-14)의 모서리 — D 수집이 빠지고 비거래일 스냅샷이 D* 가 되면 사라진 추정치 종목이 'grace(나이 0)'로 남아 만료 추정치로 채점된다 [하]
- 상황: 저녁 체인은 휴장일 WISE 를 건너뛰지만(`scripts/daily_evening.sh:110-113`) stage 에는 비거래일 스냅샷이 실제로 있다 — `stg_consensus_annual` fetched_date 09-05(토)·09-06(일)·09-26(토). G = 0.
- 인풋: D = 09-28(월) 아침판, 09-28 WISE 수집이 통째로 실패(행 없음)한 경우를 서버 stage 로 재현(09-28 행만 빼고 `queries.coverage_sqls` 그대로 실행).
- 에러 위치: `src/factor_inputs/queries.py:144-153` — 나이 = (last_fresh, D*] 거래일 수라 D* 가 비거래일(09-26)이고 last_fresh 가 직전 거래일(09-23)이면 나이 0 → `WHEN age <= 0 THEN 'grace'`. `gates.py:301-303` 은 grace 나이 > G 만 막고, 수집 지연 lag = (09-26, 09-28] = 1 이라 N-12(`COLLECTION_LAG_MAX = 1`, `gates.py:321`)도 통과.
- 위험성: 재현 결과 D*=2026-09-26, lag 1, `grace` 6종목(457600·0015N0·192650·354320·323350·059210, 전부 last_fresh 09-23)이 has_estimates=true 로 남아 09-23 매트릭스 추정치로 채점된다. N-14 "최신 WISE 스냅샷에 올해 영업이익·순이익 추정치가 없으면 바로 유니버스에서 뺀다"와 다르고 FG-fresh 가 잡지 못한다. 조건(그날 수집 실패 + 비거래일 스냅샷)이 겹쳐야 한다. 카테고리: 결정-코드 불일치 · 조용한 낡음.
- 근거: q5.py(서버 읽기 전용 duckdb) 출력 `[('fresh',0,607),('grace',0,6),…]`. fetched_date 분포 질의(일요일·토요일 행 확인).

### D-05 MG1 이 v3·scope 에서 동어반복 — 엔진과 같은 규칙으로 센 유니버스 대비 비율이라 유니버스 축소를 못 잡는다(절대 하한·전판 대비 검사 없음) [하]
- 상황: scope@1.0(v3_zscore 엔진)은 fi eligible 위에 시총 ≥ 1,000억·추정기관수 ≥ 1 을 더 건다. fi FG1 의 하한(eligible ≥ 300)은 그 앞 단계에만 걸린다.
- 인풋: 추정기관수(n_analysts)·시총 원천이 부분적으로 깨져(예: 절반 종목이 0 또는 단위 오류) scope 유니버스가 510 → 250 으로 줄어드는 경우.
- 에러 위치: `src/model/gates.py:220-242`(`_spec_universe`)가 `engines/v3_zscore.py:67-94`(`_universe`)와 같은 조건을 다시 세므로 coverage 는 엔진이 크래시하지 않는 한 1.0 이다. FAIL 은 유니버스 0(비율 0)일 때뿐. MG5(`:331-353`)는 warn 기록만.
- 위험성: 유니버스가 반토막 나도 판·엑셀이 정상으로 나간다(조용한 축소). v4 계열은 `MIN_RANKED = 100` 절대 하한이 있는데 주 모델엔 없다. 실측 scope 순위 수 485~510(09-01~10-02 백필 로그)로 안정적이라 하한을 정할 근거는 있다. 카테고리: 게이트 공백(silent corrupt 위험).
- 근거: gates.py·v3_zscore.py 대조, `logs/manual/backfill_model_20261005T165348Z.log`.

### D-06 모델·fi 문서/머리 주석이 10-05~06 변경을 못 따라갔다(판본·keep·격리·주 모델·신용 랙) [하]
- 상황: N-11(격리)·N-17(keep 60)·mb1.2.0·scope 주 모델·신용 랙 3세션이 코드에 들어갔다.
- 인풋: 문서·주석을 읽고 운영·검토하는 사람.
- 에러 위치:
  1. `docs/MODEL_BUILD.md:79` — BuildRecord `rules_version` = `mb1.0.0`(코드 `model/build.py:46` mb1.2.0).
  2. `docs/MODEL_BUILD.md:37` — `--specs all` 목록에 scope@1.0 이 없다(레지스트리 5개).
  3. `src/model/build.py:8` — "`stage.manifest.commit`(keep=3)"(코드 `:55` KEEP_DEFAULT 60).
  4. `src/model/gates.py:4-5` — "FAIL 이 하나라도 있으면 판 전체(모든 spec)를 올리지 않는다"(N-11 뒤 주 모델만).
  5. `src/model/__main__.py:5` — 사용 예 `[--primary v4_rank@0.1]`(기본 scope@1.0).
  6. `src/model/contracts.py:186` — fi_credit 주석 "KIS 신용은 T+1 이후 확정"(실제 `equity/rules_s10.py:543` recommended_lag_sessions=3, fi `_runs` credit_lag_sessions 3).
  7. `docs/FACTOR_INPUTS.md:135` 표 — grace 의 "쓰는 판 = 마지막 신선일 판(추정·WISE 재무·추정기관 수)" 인데 WISE 재무는 `queries.py:539-546` 이 마지막 신선일이 아니라 D 이하 최신 스냅샷을 쓴다.
- 위험성: 판 manifest·리뷰에서 판본·보관·실패 규칙을 문서대로 믿으면 오판(예: 비교 모델 FAIL 이면 판 전체가 없다고 보고 수동 재빌드). 카테고리: 문서 불일치.
- 근거: grep 으로 각 줄 확인.

### D-07 연도 경계 계단 — 1월 첫 거래일에 추정치 기준 결산기가 Y+1 로 바뀌어 유니버스가 한 번에 줄 수 있다 [하·가설 포함]
- 상황: 신선 판정·리비전 결산기가 D 의 연도에 묶여 있다(`queries.py:139` `period = '{p.fy}'`, `build.py:238` `fy=f"{d.year}12"`; v3 `_consensus_pair` 는 D 의 연월 이상 최소 결산기 `engines/v3_zscore.py:175-179`).
- 인풋: D = 2027-01-04(첫 거래일). 그날 WISE 스냅샷에서 2026/12 E 는 있고 2027/12 E 는 없는 종목.
- 에러 위치: 위 줄 — 12월 30일 판은 "2026/12 E op·ni 보유", 1월 4일 판은 "2027/12 E op·ni 보유"로 판정이 하루 만에 바뀐다. 게이트(FG-fresh·MG1·MG5)는 계단을 막지 않는다(MG5 warn 만).
- 위험성: 10-02 스냅샷 실측 — 2026/12 E op·ni 보유 628 중 2027/12 E 도 가진 것 600(95.5%), 28 종목(4.5%)이 연초에 lapsed 로 한꺼번에 빠질 후보다(1월에 비율이 다를 수 있어 가설). Δ순위 1M·주간 엑셀이 연초 첫 주에 유니버스 변화로 흔들린다. 원본 v3 에는 추정치 유니버스가 없어(D-10 은 우리 규칙) '의도된 v3 동작'이 아니다. 카테고리: 설계 위험(계단·비연속).
- 근거: q3.py — fetched_date 2026-10-02 `[(628, 600, 1)]`.

#### 재현 보강(03:2x KST)
- D-01 재현 통과: scratchpad `auditD/t/test_d01_isolation.py` — v2 엔진 run 을 ZeroDivisionError 로 바꾸고 `build(specs=[V2,V4], primary=V4)` → 예외 전파, latest·V4 판 없음(1 passed).
- D-02 재현 통과: `auditD/t/test_d02_weekly_fi_gc.py` — scope 판을 만든 뒤 fi 판 디렉터리만 지우면 `load_day(..., with_fi=False)` 가 `DeliverError … 판이 없다`(1 passed).
- 로컬 회귀: `uv run --project backend pytest database/tests/test_factor_inputs*.py test_model_build.py test_model_scope.py test_model_v3_port.py test_model_contracts.py` → 146 passed.

### D-08 스팩 합병 상장사 117곳이 영구히 sec_type='spac' — v4(보통주만)는 클래시스·RFHIC 등 추정치 보유 12종목을 조용히 뺀다 [중, 원인은 영역 C]
- 상황: equity `security.sec_type` 은 "전 이력에 SPAC 소속부·'기업인수목적' 이름이 한 번이라도 있으면 spac"이고 spac 이 common 보다 우선이다. 스팩 합병은 스팩 법인이 존속해 종목코드가 그대로라 합병 뒤 사업회사도 spac 으로 남는다.
- 인풋: 서버 fi 10-02 판(m_20261005T165747) `fi_universe` — sec_type='spac' 188 중 이름에 '스팩' 있는 것 71, 없는 것 117(클래시스 214150 시총 19,995억·추정기관 15, 현대무벡스·RFHIC·와이씨·넥슨게임즈·콜마비앤에이치·바디텍메드 …). eligible(추정치 보유) 12종목 전부 사업회사.
- 에러 위치: `src/equity/sql/security.sql:37-41`(`spac` CTE — EXISTS 전 이력) · `:70-72`(spac 우선). 모델 쪽 영향은 `config/models/v4_rank_0_1.toml`·`_0_2.toml` `sec_types = ["common"]` → `engines/v4_rank.py:564` 에서 탈락. scope·v3·v2 는 common+spac 이라 유니버스 영향은 없다(10-02 scope 순위 24 와이씨·184 RFHIC 등은 그대로 채점).
- 위험성: 비교 모델 v4 가 우량 사업회사를 이유 표시도 없이 모집단에서 뺀다(점수 표에 행 자체가 없다 — v4 `_universe` 는 모집단 밖을 출력하지 않음). equity `universe_daily.adv20_rank_pct` 모집단(sec_type='common')·compat U21 등 sec_type 소비자 전부가 같은 오분류를 물려받는다. 나중에 scope 를 '보통주만'으로 바꾸면 117곳이 한꺼번에 빠진다. 카테고리: silent corrupt(분류) · 데이터 정의 오류.
- 근거: q11.py `[(188, 71, 12, 12)]` + 목록. q10.py — scope 10-02 순위 표에 sec_type='spac' 5종목(와이씨 24위 등).

#### D-05 보강
- FG1 도 eligible 종목의 D 수급 행·창 안 수급 존재를 요구하지 않는다(`factor_inputs/gates.py:96-166`). fi_flows 가 비면 `engines/v3_zscore.py:250-271` 에서 수급 팩터가 통째로 빠지고 종합은 나머지 4 팩터로 재정규화된다 — MG1(비율)·MG3(종합 NULL 아님)이 모두 통과하는 '다른 모델'. 실측 09-30~10-02 eligible 전원 D 수급 있음(596/596·598/598·626/626)이라 지금 발생한 것은 아니다.

### D-09 같은 날 재실행이 주 모델 FAIL 이면 `_runs/<D>` 가 gate_failed 로 덮여, 이미 올린 성공 판을 인계·추이가 못 찾는다 [중]
- 상황: D 의 모델 판이 이미 성공해 latest·`_runs/<D>_morning.json`(ok)·`<spec>/v=<id>` 가 있다(엑셀 발송까지 끝남). 운영은 수동이라 같은 D 를 다시 돌리는 일이 실제로 잦다(10-02 판 하루에 4번 — scope MANIFEST 10:05·10:37·15:07·16:57 UTC).
- 인풋: 같은 D 로 `python -m model build` 재실행, 이번엔 주 모델 게이트가 FAIL(입력 판 이상·코드 변경 등).
- 에러 위치: `src/model/build.py:283-287` — FAIL 이면 `_runs/<D>_<basis>.json` 을 gate_failed payload 로 **덮어쓴다**(latest·MANIFEST 는 그대로). 인계 `src/deliver/reader.py:75-88` `find_run` 은 `_runs` 파일이 있으면 latest 로 돌아가지 않고 status ≠ ok 면 None. factor_inputs 도 같은 모양(`factor_inputs/build.py:309-312`).
- 위험성: 판 파일·latest 는 멀쩡한데 그날 엑셀(정정판 재생성 `load_run`)이 "성공 판이 아니다"로 실패하고, 이후 날들의 전일 순위(`previous_run`)·Δ순위 1M·순위 흐름(`runs_between`)에서 그날이 빠진다. 성공 기록이 실패 기록으로 지워지는 것이라 원인 추적도 어렵다. 카테고리: 데이터 손실(색인) · 운영 중단(정정판).
- 근거: scratchpad `auditD/t/test_d09_runs_overwrite.py` — 성공 빌드 → 같은 D 로 MG3 FAIL 빌드 → latest·판 파일은 성공 판인데 `find_run(D)` None(1 passed). MODEL_BUILD.md:60 "같은 날 재실행은 덮는다"는 적혀 있지만 이 결과는 적혀 있지 않다.

### D-10 모델이 fi 판의 유예 설정을 확인하지 않는다 — fi 를 `--grace-days 5` 로 돌리면 scope 유니버스가 조용히 N-14 를 벗어난다(문서 예시가 바로 5) [하]
- 상황: fi·모델 모두 수동 실행. scope·v3 엔진은 유니버스를 `fi_universe.eligible` 에 맡긴다(`engines/v3_zscore.py:390-394` — 기본 규칙과 같다고 '믿는다').
- 인풋: `python -m factor_inputs build --date D --basis morning --grace-days 5`(사용 예 `src/factor_inputs/__main__.py:5`·`docs/FACTOR_INPUTS.md:24` 가 그대로 `[--grace-days 5]`) → 이어서 `python -m model build --date D`.
- 에러 위치: `src/model/build.py:151-176`(`_resolve_fi`)는 date·basis 만 대조하고 fi 판 manifest 의 `universe_rule.coverage_grace_days`·`rules_version` 은 보지 않는다. spec 의 유예(기본 0)와 fi 의 유예가 달라도 통과.
- 위험성: N-14(유예 0) 위반 판이 게이트(MG0~MG5) 통과로 발송된다 — 만료 추정치 종목이 최대 5거래일 채점. 판 manifest 에는 fi id 만 남아 사후에야 fi `_runs` 로 알 수 있는데 fi 는 keep 3 이라 그것도 사라진다(D-02). 카테고리: 결정-코드 불일치(운영 실수 경로) · 문서 불일치.
- 근거: grep "universe_rule\|grace" src/model → 대조 코드 없음.

### (기존 D2-10·Q-5 실측 갱신 — 새 결함으로 세지 않음) 수정주가 미해결 사건이 scope 모멘텀에 그대로 들어간다
- `engines/v3_zscore.py:21` 머리 주석은 adj_ok 무시를 "의도적으로 재현한 v3 동작"이라 적지만, 원본 v3 는 키움 수정주가(`upd_stkpc_tp=1`)라 같은 사건에서 연속이다. 즉 v3 동작 재현이 아니라 입력 차이다(HD:32·v2:76 D2-10 이 이미 '입력 결함'으로 분류 — 주석만 낡음).
- 10-02 scope 판(510): 최근 241행 안 adj_ok 전환 22건/20종목. 서버 v3 quant.db(-readonly) 대조 — 한화 000880 08-25: 우리 83,800→118,100(+40.9%) vs v3 수정 100,602→118,100(+17.4%), r3m~r12m 오염(scope 217위). 삼성바이오로직스 207940 2025-11-24: 우리 +46.5% vs v3 −0.4%(r12m 0.335, 386위). 대한제강 084010 2026-01-05: 우리 −41.4% vs v3 −12.2%(r12m −0.435, 454위). 셋 다 adj_factor factor_ok=false(`krx_base_inconsistent`·`unknown_price_only`).

## 문제없음 확인(03:2x KST 까지)
- 엔진 결정성(프로세스 간): 서버 scope·v3·v2·v4×2 의 10-02 판 content_hash 가 10:05 vs 10:37 UTC, 15:07 vs 16:57 UTC 빌드에서 각각 일치(입력 같을 때 비트 동일). MG2 는 프로세스 안만 보지만 실데이터로 교차 확인됨.
- 재생성 22판(09-01~10-02): fi·model 전부 status ok, `excluded_specs` 전부 {}, scope MG1 1.0·MG4 2,760~2,766·MG5 Spearman 0.92~0.97(09-01 만 전판이 10-02 라 warn 0.41 — 기록형).
- fi 10-02 판 게이트 FG0~FG4·FG-fresh 전부 pass, FG4 22/22 검사, FG3 n_krx_mktcap_diff 0.
- WISE 매트릭스 base_date = 직전 거래일(DT=T−1) 25개 스냅샷 전부 일관, 신선 종목 중 같은 날 매트릭스 없는 종목 0(→ FG1 eligible_without_cur_consensus 위험 없음).
- 신선→소멸 전환 중 '하루 빠졌다 돌아오기'(수집 누락형) 0건 — 사라진 종목은 다음 스냅샷에도 없음(유예 0 과 데이터 일치).
- 추정기관수(coverage_daily b_20261005T100502): 신선 종목 중 NULL 0~2/일, 0명 76~107/일 — 백필 기간 내 정의 흔들림 없음. scope 유니버스 계산(eligible∧시총≥1,000∧기관≥1) = 510 이 로그와 일치.
- fi_prices·fi_adj_prices NULL 종가 0, eligible 전원 D 수급 있음(09-30~10-02), 당해 결산기 cur·1m op/ni NULL 0.
- scope 510 의 연간 재무: 509 WISE+DART, 1 WISE 만, 연간 행 없음 0. 밸류 결측 1.
- PIT(재생성 판): WISE 3표·coverage_daily 는 fetched_date ≤ D, fin_std·배당·감사·공시는 available_date ≤ D, price_adj_daily 는 전방 조정(fold = max(적용일, 공개일)) — 미래 사건이 과거 값을 바꾸지 않음. 비-PIT 은 표시용 현재 이름·현재 sec_type(D-08)·현재 지주사 목록·WICS available_date=dt(토 03:00 수집분을 금요일로 기록) 정도.
- 판본: fi1.1.0(b2c3fa83) 뒤 fi 코드 변경 없음, mb1.2.0 뒤 모델 변경은 keep 60 뿐(규칙 아님).
- 로컬 테스트 146 passed.

### D-11 모델 판 manifest 에 spec 정의(TOML 내용·해시)가 없다 — 같은 spec_id 를 제자리 수정하면 과거 판과 구분할 수 없고, 엑셀은 '지금' TOML 로 과거 판을 설명한다 [하]
- 상황: N-1 은 정의·가중 변경 때 버전을 올리게 하지만 실제로 scope@1.0 은 10-05 에 min_analysts 를 제자리 추가했고(5b8e21df, "첫 발송 전이라 v1.0 에 포함"), 유예 기본값 변경(5→0)도 spec_id 를 바꾸지 않았다(v4_rank@0.1 은 D-03 처럼 실효 규칙이 바뀌었는데 0.1 그대로).
- 인풋: 같은 spec_id 로 TOML·기본 UniverseRule 이 바뀐 뒤 과거 판을 읽거나(Δ순위 1M·주간·정정판) 그 판을 감사.
- 에러 위치: `src/model/build.py:270-276` 판 manifest 키(layer·status·build_id·date·basis·fi_build_id·generated_at·specs(수·게이트)·excluded_specs·primary_spec·elapsed_s)와 `:320-327` BuildRecord 에 spec 내용·해시가 없다. 인계 `src/deliver/view.py:114` 는 `spec_of(spec_id)` 로 **현재** 레지스트리에서 spec 을 읽어 유니버스 문구·버킷을 그린다(`excel_daily.py:674-690`).
- 위험성: 판 재현·감사 때 어떤 규칙으로 지은 판인지 manifest 만으로 알 수 없다(rules_version mb·fi id 로 간접 추정만 가능, fi 는 keep 3 으로 사라짐 — D-02). TOML 을 고친 뒤 과거 판 엑셀을 다시 만들면 메타 '유니버스 규칙'이 그 판과 다르게 적힌다. 카테고리: 재현성·문서 불일치.
- 근거: build.py payload·BuildRecord 키 직접 확인, `git log` 5b8e21df·b2c3fa83.

#### D-04 보강(발생 경로)
- 정상 아침 체인에서는 D 의 WISE 런이 없으면 `daily/ledger_health.py:403-409` `wise.run`(REQUIRED)이 FAIL 해 확정 빌드가 안 생기므로 fi(D) 자체가 못 돈다. 그래서 이 모서리는 ① `--skip wise`(HSKIP) ② ledger_health 의 D+1 아침 스냅샷 폴백(`:397-399`, 그 스냅샷 fetched_date 는 D+1 이라 D* 에 안 잡힘) ③ 수동 equity 재빌드(b_) 경로에서만 열린다. 심각도 하 유지.
- 같은 이유로 부분 수집 실패(종목 일부 누락)는 `wise.run n_bad=0·n_ok=n_req`·`wise.req_identity`·`wise.raw` REQUIRED 가 막는다 → 유예 0 이 '수집 실패로 유니버스가 조용히 줄어드는' 경로는 정상 체인에서는 닫혀 있음(문제없음 쪽 근거).

### D-12 v3 이식의 '수정종가 없으면 원종가' 폴백이 전방 조정과 만나면 척도가 섞인다 — fi 에 fi_prices↔fi_adj_prices 키 동일성 게이트가 없다 [하·잠재]
- 상황: v3 원본은 키움 수정주가(최근 = 원주가, 과거를 낮춤)라 `adj_close or close` 폴백이 무해했다. 우리 price_adj_daily 는 **전방 조정**(첫 관측 고정, 사건 뒤를 올림)이라 수정종가가 원주가의 0.02~50배다(005930 = ×50, FG4 골든 `adj_close 13,550,000 = 271,000 × 50`).
- 인풋: fi_adj_prices 에 일부 (종목, 날짜) 행이 빠진 판(예: price_adj_daily 만 다른 시각에 재빌드돼 D 행이 없는데 3시간 체인 검사 안쪽).
- 에러 위치: `src/model/engines/v3_zscore.py:105-116`(`_price_histories` — adj 없으면 `r["close"]`) · v2 도 같은 COALESCE(`v2_percentrank.py` 머리 주석 `:17`). fi 게이트 `factor_inputs/gates.py:96-166` FG1 은 두 표의 키 동일성을 보지 않는다(FG4 는 08-20 한 날 3종목만). 보호막은 equity EG1(같은 판 안 행 항등)과 `factor_inputs/build.py:146-157` `_check_chain`(빌드 시각 차 ≤ 3h)뿐.
- 위험성: 10-02 scope 510 중 191 종목(37%)이 D 누적계수 ≠ 1 — 한 행이라도 원종가로 섞이면 r1m 등이 −98%·+4,900% 같은 값이 되고 게이트(MG0~MG5)는 통과한다(MG5 warn 만). 지금은 키 995,036 = 995,036, 누락 0 이라 발생하지 않았다. 카테고리: silent corrupt(잠재) · 게이트 공백.
- 근거: q15.py `[(510, 191, 50.0, 0.0199…)]`, `key identity [(995036, 995036, 0)]`.

#### D-02 분류 정정
- DECISIONS U8 이 이미 "주간 엑셀이 GC 로 지워진 월·화 판을 못 열 수 있음(추정, 운영 미확인)"을 적고 있다 → D-02 는 **기존 U8 의 확인·구체화**로 센다. 새로 밝힌 점: ① N-17(모델만 keep 60) 뒤에도 남는 원인은 fi 층 keep 3 + `deliver/view.py:116` 의 무조건 fi_universe 읽기(`with_fi=False` 로도 못 피함), ② 지난주 판은 항상 GC 상태라 첫 주간 실행부터 실패, ③ 재생성 22판 중 19판의 입력 fi 가 이미 없음(추적 상실). 재현 테스트로 '추정'을 '확인'으로 바꿈.

#### 기존 기록과의 교차(03:2x)
- D-09: `plans/2026-10-05-gate-register.md:51`(RG-C0-01, 채택 보류)이 "_runs 는 재실행이 덮으므로 쓰지 않음 — model/build.py:272·315"라 적어 덮어쓰기 자체는 알려져 있다. 그 결과 인계 `find_run` 이 **이미 올린 성공 판을 못 찾는다**는 점·fi 도 같다는 점은 기록 없음 → 새 발견으로 유지(부분 기지).
- D-07: 같은 문서 GH3 ⑦(채택 보류)이 연도 전환 재생(`factor_inputs/build.py:236`)을 계획 — 계단 크기(10-02 기준 628 중 28 = 4.5%) 실측은 새것.
- D-02: GM1(채택 보류)이 "keep 3 → 주간 DeliverError"를 음성 대조로 두고 단계 0 에 `--keep 15` 를 계획 — 모델 keep 만 보며 fi keep 3 은 언급 없음.

- D-01: gate-register GM-R3(채택 보류, `:160`)이 "지금은 … 예외 미처리", "현 코드(3af78837)에서 compare 예외 → rc 2(현행 음성)"라 적었다. 그 뒤 N-11 구현(mb1.2.0)은 게이트 FAIL 만 격리했고 DECISIONS N-11 은 '구현'으로 닫았다 → 기록(DECISIONS)과 실제가 다름 = 새 발견으로 유지.
- D-10: GM-R1 ③(채택 보류, `:157`)이 운영 루트에서 "fi --grace-days … 거절"을 계획 — 미구현. 부분 기지.
- D-11: GM-R1 ⑥⑦·RG-C5-03(채택 보류)이 판 manifest 의 TOML 원문·spec 해시·deliver 의 판 스냅샷 복원을 계획 — 미구현. 부분 기지.

## 문제없음 확인(추가, 03:3x)
- scope 10-02 판 r1m·r3m·r12m 을 fi 에서 독립 재계산(005930·000660·232140) — 엔진 값과 소수 끝자리까지 일치.
- WISE 매트릭스·연간(10-02)에서 (종목, 기, 항목, 시점) 중복 0 → `max(CASE…)` 로 조용히 큰 값이 뽑히는 경로 없음. cF4002 EPS·BPS 중복은 PER·PBR 아래 자식 행(p_accode 있음)이라 top_only 가 거른다.
- 티커 재사용(036220 2016→2024, 101970 2015→2025)은 550일 창 밖 — 가격 창이 두 회사를 잇지 않음(둘 다 eligible 아님).
- WISE 부분 수집 실패는 아침 ledger_health `wise.run`(n_bad 0·n_ok=n_req)·`wise.req_identity`·`wise.raw` REQUIRED 가 막아 유예 0 이 조용히 유니버스를 줄이는 경로는 정상 체인에서 닫혀 있음.
- 시총 상위 대형주(현대차·KB금융·삼성생명·셀트리온·NAVER 등) 전부 fresh·eligible. 추정치 없는 대형주(에코프로·HLB 등)는 'estimates_none' 으로 규칙대로 빠짐.
- 수급·신용 창: 신용 available_date = 날짜 + 3세션(`_calx`), 10-02 판 FG1 `fi_credit.available_after_d` 0.

## 참고(결함 아님·다른 영역)
- 10-02 스냅샷에서 추정치 보유(fresh)가 600 → 628(+28, 그중 15 는 10-01 엔 WISE 커버 없음, 24 는 3개월 의견 0명). WISE 쪽 변화로 보인다(수집 2,604 전수 ok, 커버 799 → 808). scope 는 N-6 덕에 영향 작고 v3_zscore@1.0·v2·v4 유니버스가 하루에 +19~28 → 비교 모델 Δ순위가 흔들린다. 영역 A·B 확인 권장.
- WICS sector_snapshot 의 available_date = dt(금)인데 실제 수집은 토 03:00 KST(크론 `0 18 * * 5` UTC). 아침판에는 문제없고 N-13(장 마감 직후 모델)에서는 하루 앞당긴 표기가 된다 — 영역 C.
- `n_lapsed_dropped` 는 09-01 이후 한 번이라도 신선했던 종목을 영구히 lapsed 로 센다(0 → 57 단조 증가) — 일일 '오늘 빠진 수'로 읽으면 오해. 표시 문구 확인(영역 E).
- fi 와 모델이 둘 다 수동 실행(크론·체인 없음) — MODEL_BUILD.md:41-44·Q-3 으로 기존 기지.

## 미조사
- `src/model/compare.py`(v3 대조 수동 도구) 정독 — 머리 주석·인자만 확인.
- v4_rank 엔진 지표식 하나하나의 연구 SQL 대조(OPM_TTM 연속 4분기·CRDT_CHG·M_PULL_C 경계) — 흐름·결정성만 확인.
- `tests/tools/compat_to_fi.py` 어댑터와 골든(G-M3) 재현 — 로컬 테스트 통과로 갈음.
- 1월 실제 WISE 스냅샷에서의 연도 전환 계단(D-07) — 과거 1월 스냅샷이 없어 10-02 로 대리 측정.
- v3 원본(quant.db)과 scope 의 종목별 순위 차이 전수(G-M3 재측정) — 09-28 뒤 model_compare 기록 없음.
- deliver 쪽(엑셀·주간·trend)의 판 해석 — 영역 E(D-02·D-09 재현에 필요한 부분만 봄).

## 추가 확인(03:3x~)
- scope 10-02 vs 원본 v3 `score_history` 10-02(quant.db -readonly, 공통 510): 종합 Spearman 0.987 · 모멘텀 0.998 · 리비전 0.968 · 수급 0.951 · 퀄리티 0.983 · 밸류 0.997, 기관 5일 수급 원값 1.000, r1m |Δ|>2%p 13종목(원본 v3 종가가 09-14 부터 애프터마켓 체결가 — DECISIONS §6.4 기지). v3_zscore@1.0(공통 593) 종합 0.987. → 이식 자체의 큰 결함 신호 없음(차이는 유니버스 차이로 z 척도가 달라지는 것·종가 원천·컨센서스 수집 시각).

## 끝
