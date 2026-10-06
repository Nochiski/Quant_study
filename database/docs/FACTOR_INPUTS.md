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
- **지금은 아침 확정판(`morning`)만** 굽는다. D-8(모델은 아침 확정판만) 결정으로 저녁 T 오버레이는
  equity `evening_snapshot`(W1-a) 뒤로 미뤘다. `--basis evening` 은 "미구현(W1-a)" 오류(rc 2)다.

## 2. 실행

```bash
python -m factor_inputs build --date 20260929 --basis morning \
    [--root data/factor_inputs] [--stage-root data/stage] [--equity-root data/equity] \
    [--grace-days 5] [--min-eligible 300] [--keep 3]
```

| rc | 뜻 |
|---|---|
| 0 | 8표 판 커밋 · `latest_morning.json` 갱신 |
| 1 | 게이트 FAIL — 판을 올리지 않는다(`_failed/<build_id>.json`, 포인터·latest 는 직전 성공 판 유지) |
| 2 | 입력·인자 오류(원천 판 없음 · D 가 거래일 아님 · 아침판에 저녁 equity 판 · 가격 판 체인 불일치 · evening) 또는 예외 |

- 기본 루트는 `QL_HOME`(없으면 저장소 `database/`) 아래 `data/…` — equity CLI 와 같다.
- **언제 돌리나**: 아침 체인(equity `m_` 판)이 끝난 뒤, 저녁 체인(`e_` 판) 전. 아침판 가드가 `e_`
  equity 판을 거절한다. D 는 확정판의 거래일(예: 09-30 아침 체인 → `--date 20260929`).
- 과거 날짜 검증(예: GC2 의 09-23 재현)은 **별도 `--root`** 로 돌린다 — `latest_morning.json` 은
  날짜와 무관하게 마지막 성공 판을 가리키므로 운영 루트에서 옛 날짜를 돌리면 덮인다.

## 3. 판 규약

```
data/factor_inputs/
  fi_<표>/MANIFEST.json            # stage.manifest — current_build · keep=3 · BuildRecord.inputs
  fi_<표>/v=<build_id>/part0.parquet
  fi_<표>/v=<build_id>/_meta.json  # 표·판·행수·content_hash·inputs·창·게이트
  _runs/<YYYYMMDD>_<basis>.json    # 판 manifest(V2-8) — 성공·실패 모두. 같은 날 재실행은 덮는다. 단 FAIL 재실행은 같은 날 성공 기록을 덮지 않는다(실패는 _failed/ 에만)
  latest_<basis>.json              # 마지막 성공 판(= 그 판의 _runs 내용)
  _failed/<build_id>.json          # 게이트 FAIL 보고서
  _tmp/<build_id>/                 # 빌드 중 임시(끝나면 지운다)
```

- 판 id 하나(`m_<UTC>`)를 8표가 공유한다. 표마다 포인터를 따로 바꾸므로 전환 순간에는 섞여 보일 수
  있다 — **소비자는 `latest_<basis>.json` 의 `build_id` 로 8표를 연다**(keep=3 이라 다음 두 빌드 동안
  남는다).
- **parquet 은 `hive_partitioning=false` 로 읽는다.** 경로의 `v=<build_id>` 가 열(`v`)로 붙으면
  계약 밖 열이 된다(equity 산출과 같은 규약).
- 원천 판은 MANIFEST `current_build` 로 해석한다(맨 glob 금지). equity `_pinned/` 처럼 하드링크로
  고정하지 않는다 — 산출 자체가 창을 자른 사본이고 equity 루트에 쓰지 않기 위해서다. 읽은 판 id 는
  표별 `BuildRecord.inputs` 와 판 manifest `equity_builds`·`stage_builds`·`tables.<표>.inputs` 에 남는다.
- 판 manifest 필드: `layer · status · build_id · date · basis · generated_at · rules_version ·
  equity_root · stage_root · equity_builds · stage_builds · window · universe_rule · min_eligible ·
  coverage · n_lapsed_dropped · tables · gaps · gates · elapsed_s`.

## 4. 표별 원천 · 창 · 단위

D = 판 기준일. 표 전부 `fi_universe` 종목으로 자른다.

| 표 | 원천 (equity 는 판, stage 는 `stg_`) | 창 | 단위·정수화 |
|---|---|---|---|
| `fi_universe` | `universe_daily`(D 행: 상장·정지 · KOSPI/KOSDAQ · ETF 제외 · `adv20_krw`·`admin_state`·`halt_state`) · `security`(이름·상장일) · `price_daily`(D 의 KRX 종가·상장주식수) · `sector_snapshot`(D 이하 최신 WICS) · `coverage_daily.analyst_count` · `audit_opinion` · `disclosure_version` · 신선도(`stg_consensus_annual`, §5) | D 한 날 | 시총 = **round(주식수 × 종가 / 1e8) 정수 억원**(compat `stocks.market_cap` 과 같은 반올림) · adv20 억원 |
| `fi_prices` | `price_daily`(basis `krx`) | D 포함 550 달력일 | 가격 원 · 거래량 주 · **거래대금 원**(compat 의 백만원이 아니다) |
| `fi_adj_prices` | `price_adj_daily.adj_close`·`cum_share_factor` · `adj_factor`(미해결 사건) | 550 달력일 | `adj_ok` = 미해결 사건 **계단 표식**(아래) |
| `fi_flows` | `flow_daily` 12주체(키움 우선, 전 주체 NULL 칸은 행 없음) | D 까지 60 세션 | **round(원 / 1e6) 정수 백만원** |
| `fi_credit` | `credit_daily.whol_loan_rmnd_stcn_shr`·`whol_loan_rmnd_rate_pct` | D 까지 60 세션, `available_date ≤ D` | 주 · % · `available_date` = 그날 + 3 세션(KIS 실입수, equity FieldProfile) |
| `fi_consensus` | `stg_consensus_matrix`(c1050001 T4) — **한시 예외**(§7) | 신선·유예 종목의 마지막 신선일 판 × 결산기 3 × horizon cur/1w/1m/3m | 억원·원·배·% 원값(eps·bps 는 정수 — compat 과 같다). 판에 있는 (종목, 결산기)마다 네 horizon 행을 값이 NULL 이어도 만든다 |
| `fi_consensus_annual` (v2 전용) | `stg_consensus_annual`(WISE c1050001 T2Y — v2 원천) | 종목별 fetched_date ≤ D **최신 한 판**, 전년·당해·차년 12월기(Y−1/12 · Y/12 · Y+1/12), 추정 E·확정 A 둘 다 | 억원·원·배 원값(eps 정수 — compat `consensus_annual` 과 같다). 원천 행이 있으면 값이 전부 NULL 이어도 싣는다(v2 에게는 행 존재가 뜻이 있다) |
| `fi_fin_summary` | 연간: `stg_fin_wise`(WISE cF3002·cF4002, 계정명+최상위 선택 DQ-6) + `fin_std`(사업보고서, 연결 우선, PIT) + `dividend_event`(보통주 dps) · 분기: `fin_std`(1Q·반기·3Q 3개월 값, 4Q = 사업보고서 − 1~3Q) | 연간 최신 2기 · 분기 최신 5기 | 금액 **정수 억원**(compat 식 그대로) · 비율 % · dps 원 |

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
  법인 축 `'-'` 행, 결산기 = `stlm_dt`, `available_date ≤ D`). 모르면 NULL(무배당 = 0 은 엔진이
  정한다). **연구 R-2 질의와 다른 점**: R-2 는 `'-'` 행을 빼고 쟀다 — 09-28 로컬 판 실측 FY2025 배당
  법인 1,261 중 155 곳이 `'-'` 행에만 dps 를 싣는다. 빼면 그 155 곳이 NULL → 엔진의 '무배당 0' 으로
  조용히 떨어지므로 보통주 값이 없을 때만 `'-'` 를 쓴다.
- `available_date` = 행을 이룬 원천의 max(WISE fetched_date, DART·배당 available_date).
- 분기 행: eps·bps·per·pbr·ev_ebitda·dividend_yield·dps·shares·roe·roa·fcf·capex 는 NULL(아래 §7).

`fi_adj_prices.adj_ok`(오케스트레이터 09-29): 창 안 미해결 사건(`adj_factor.factor_ok = false`,
적용일 ∈ (창 시작, D], available ≤ D)의 **적용일마다 값이 뒤집힌다** — 창 첫 구간 True, 첫 사건
적용일부터 False, 둘째 사건부터 다시 True …(같은 날 사건 여럿은 한 번). **사건 쪽(적용일 이후)을
뒤집는다** — 사건이 하나면 '사건 전 True · 사건부터 False'. 창 안에서 값이 바뀌면 그 창이 사건을
넘는다(v4 엔진 `crosses_event`), 한결같으면 척도가 이어진다. 창 밖 옛 사건은 세지 않는다. 09-28 로컬
판 실측: 2,763 종목 중 224 종목이 창 안에 전환을 가진다.

D-13 적격성 재료 5열(계약 09-29 — **eligible 에는 쓰지 않는다**, v4 가 `UniverseRule.exclude`·
`min_adv20` 으로 건다):

| 열 | 원천 · 규칙 | 09-28 로컬 판(2,763) |
|---|---|---|
| `adv20` | `universe_daily.adv20_krw / 1e8` — [D−19, D] 20 세션 거래대금 평균(억원). 값 있는 세션이 20 미만이면 NULL | 값 2,757 |
| `is_admin` | `universe_daily.admin_state` — 관리종목(KOSDAQ 투자주의환기 소속부 포함), 판정 재료가 없으면 NULL | true 179 |
| `is_halted` | `universe_daily.halt_state` — D 에 매매거래정지 | true 113 |
| `audit_adverse` | 의견이 실린 가장 최근 사업보고서(11011, available ≤ D)의 **당기 행** 분류 ∈ {한정, 부적정, 의견거절}. 한 접수에 당기·전기·전전기 3행 → 라벨에 '전' 없는 행, '당' 우선, 기수 큰 순. 의견 원문 없는 행(FY2025 '-' 라벨 414건)은 건너뛰어 직전 알려진 의견. 적정 → false, other → NULL | 알려짐 2,560 · true 43 |
| `filing_late` | 가장 최근 정기보고서 **원본**(정정 제외, available ≤ D)의 접수일 > 실효 기한. `legal_deadline`(기말 + 90/45 역일)은 휴장 보정이 없어 기한이 휴장일이면 **다음 거래일**로 민다. 미제출(기한 지났는데 아직 안 낸 보고서)은 보지 못한다 | 알려짐 2,638 · true 35 |

`fi_universe.eligible` = 기본 `UniverseRule()` — sec_type ∈ {common, spac} · 시장 ∈ {KOSPI, KOSDAQ}
· D 에 KRX 종가 있음 · 추정치 보유(`has_estimates`). **시총 하한은 걸지 않는다**(엔진이
`spec.universe.min_market_cap` 으로 건다). `exclude_reason` 은 먼저 걸린 하나:
`sec_type → market → no_price → estimates_lapsed → estimates_none`.

## 5. 신선도 유예 (T2.11 · DQ-7)

- D\* = `stg_consensus_annual` 의 fetched_date ≤ D 중 **마지막 수집일**(전 종목 공통).
- 종목의 마지막 신선일 = 당해 12월기(`period = D.year||'12'`) 추정(`period_kind='E'`)의 op·ni 가
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
| FG1 행수 | 유니버스 × 창 | 종목 ⊄ fi_universe · fi_universe 종목 중복·date ≠ D · eligible 인데 D 가격 없음 · eligible 인데 cur 컨센서스 없음 · eligible < `--min-eligible`(기본 300) · 날짜가 창 밖 · 수급·신용 60 세션 초과 · 신용·재무 `available_date > D` · 연간 > 2기 · 분기 > 5기 · horizon 어휘 밖 · `fi_consensus_annual` 기가 Y−1~Y+1/12 밖·data_type ∉ {E, A}·fetched_date > D · eligible 중 WISE 연간 재무(per 또는 eps) 비율 < 0.9 |
| FG2 T 행 출처 | 아침판: 전 행 KRX | `price_source`·`mktcap_basis` ≠ 'krx' |
| FG3 시총 | market_cap = round(shares × close(D) / 1e8) (상대 1e-6) | 규칙 위반 · 주식수·종가가 있는데 NULL · basis ≠ krx. KRX `mktcap_krw` 반올림값과 다른 수는 기록형(`n_krx_mktcap_diff`) |
| FG4 골든 | `src/factor_inputs/fixtures/golden.json`(3종목 005930·000660·161890, 22항목, stage 원장에서 손으로 옮긴 값) | 창·유니버스 안 항목이 값이 다르거나 행이 없다. 셀 수 있는 항목이 0 이면 `skip(no_fixtures)` — 창이 지나가면 골든을 갱신한다(수급·신용 항목은 2026-08 날짜라 11월 중순에 창 밖) |
| FG-fresh | 상태 수·eligible 상태 수·`n_lapsed_dropped`·D\*·수집 지연 기록 | 어휘 밖 · has_estimates ≠ 상태 · fresh 나이 ≠ 0 · grace 나이 > G · lapsed 나이 ≤ G · lapsed/none 인데 eligible · **D\* 가 D 보다 1거래일 넘게 뒤처짐**(`COLLECTION_LAG_MAX`, N-12 — 유예 G 와 따로 둔다. 수집 정지 — 전 종목이 '신선' 으로 보이는 조용한 낡음). D 이전 수집 기록이 없으면 `skip(no_collection)` |

아침판 가드(게이트 전, rc 2): equity 판 접두어가 `e_` 이면 거절 · `price_daily`·`price_adj_daily`·
`adj_factor` 판의 빌드 시각 차 > 3시간이면 거절(compat R5·R9 와 같은 값).

## 7. 비운 열 · 보류 (판 manifest `gaps` 와 같다)

| 표.열 | 사유 |
|---|---|
| `fi_prices.price_source` · `fi_universe.mktcap_basis` | 아침판만 — 전 행 `krx`. 저녁 T 오버레이·`t1_shares_x_t_close`(B-24)는 W1-a 뒤 |
| `fi_consensus` 전체 원천 | **한시 예외**: equity 에 op·ni 컨센서스 표가 없어(B-23) stage `stg_consensus_matrix` 를 직독한다(compat GAP-6 과 같은 예외). 만료 = W1-a S17b `consensus_revision` |
| `fi_consensus.obs_date`(1w/1m/3m) | NULL — WISE 매트릭스는 lookback 관측일을 주지 않는다(cur 만 base_date) |
| `fi_fin_summary.op_margin·ni_margin·yoy` | compat 과 같이 NULL — 계산은 엔진 몫 |
| `fi_fin_summary` 분기 행 eps…capex | WISE 분기 슬롯(`val_q*`)은 기간 라벨이 없다 · 분기 ROA 연율화 정의 전 · 분기 capex·FCF 는 `fin_std._q` 가 부호가 섞인 누계의 차라(절단본 003540: 1Q −7.2억 → 반기 +13.7억) 싣지 않는다 |
| `fi_fin_summary` 비12월 결산 연간 | 싣지 않는다(§4) — 전 시장 12종목, 09-23 유니버스 0 |
| `fi_universe.filing_late` | 미제출 보고서는 판정 밖(가장 최근 제출분만) |
| `fi_universe.is_admin` | equity `admin_state` 그대로 — 판정 재료가 없으면 NULL |

## 8. 테스트

- `tests/test_factor_inputs.py` — 합성 트리 왕복(판·계약·신선도 4상태 + 복귀·유예 경계·유니버스·
  컨센서스·재무·창·수급·신용·실패 경로·CLI).
- `tests/test_factor_inputs_gates.py` — 게이트마다 한 가지씩 망가뜨려 FAIL 을 확인.
- `tests/test_factor_inputs_slice.py` — 커밋된 stage 절단본 위 equity 16표 체인 → 판 빌드(FG4 골든
  통과) · `fi_fin_summary` 연간 = compat `financial_summary` SQL(겹치는 열 전부) · `fi_consensus_annual`
  = compat `consensus_annual` SQL(같은 종류 행) · 005930 DART 값 손계산.
