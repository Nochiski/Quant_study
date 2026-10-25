# 영역 C — equity 층 전수 조사

- 범위: src/equity/* (rules_s*, sql/*, build·__main__, contract, catalog, inputs(gc_pinned), model.py RULES_VERSION), scripts/equity_rebuild_all.sh·run_equity.sh, docs/EQUITY_DESIGN.md·EQUITY_GATES.md·EQUITY_FIELD_MAP.md 대비 코드, 서버 data/equity/*/MANIFEST.json·logs/equity/, 저녁판(e_)·아침판(m_) 차이
- 시작: 2026-10-06 02:53 KST
- 저장소: ~/orca/workspaces/Quant_study/v3-merge/database (feat/v3-merge)
- 읽기 전용. 결함 형식은 .claude/rules/pr-review.md

## 결함

### C-01 아침 확정 빌드 실패 시 롤백이 '패스 시작 판' = 전날 저녁 잠정판(e_)으로 돌아간다 — REC-13 수정이 실제로는 효과 없음 [중]
- 상황: 평일 운영 순서는 21:20 저녁 잠정 빌드(30표 e_ 커밋, `manifest.commit` 이 basis 와 무관하게 `current_build` 를 새 판으로 바꿈) → 다음 날 08:10 아침 확정 빌드(m_). 서버 MANIFEST 이력 seq 가 표마다 `ememememem` 로 교대.
- 인풋: 아침 `equity_rebuild_all.sh morning_<D> --basis morning` 중간 표 실패(rc≠0) → `rollback --pass ... --before logs/equity/rebuild_morning_<D>/before.json`.
- 에러 위치: `scripts/equity_rebuild_all.sh:43-59`(패스 시작 포인터 기록) · `src/equity/rollback.py:69-99`(before 판으로 되돌림) · `src/stage/manifest.py:76-86`(commit 이 무조건 current 교체). 테스트 `tests/test_equity_rollback.py:105-120` 은 before=`m_1`, builds=`[m_1, e_2, m_3]` 라는 **운영에서 생길 수 없는 전제**(e_2 커밋 뒤에도 current 가 m_1)로 통과한다.
- 위험성: 코드·주석(`rollback.py:75-77`, `equity_rebuild_all.sh:43-44`)은 "직전 판은 전날 저녁 잠정판이라 확정 자리에서 잠정판이 보인다(REC-13) — 시작 시점 판이 정답" 이라 하지만, 아침 패스 시작 시점 포인터가 바로 그 저녁 e_ 판이다. 실패 시 MANIFEST 를 직접 읽는 소비자(상목 SFTP·compat `current` 모드·워크벤치 동기화)는 확정 자리에서 애프터마켓 종가가 든 잠정 T 행 판을 본다. fi 는 저녁 판 가드가 있어 막히지만(엑셀 미발송) 다른 소비자는 막히지 않는다. 카테고리: 문서 불일치 + silent(잠정판이 확정 자리에 노출).
- 근거: 서버 `logs/equity/rebuild_morning_20261002/before.json` — `"e_` 30건, `"m_` 0건. MANIFEST 요약 스크립트 seq=`ememememem`(28표, security·coverage_daily 는 10-05 수동 b_ 로 끝남). 롤백 실발생은 수정 이후 0건(rebuild_morning_* STATUS 전부 TOTAL).

### C-02 저녁판 price_daily T 행 종가 = 애프터마켓 마지막 체결가인데, 소비자용 정본 선언(dataset_profile)은 여전히 '키움 종가·정규장 종가 확정'이다 [하]
- 상황: 09-14 KRX 애프터마켓(16:00~20:00) 시행 뒤 21:05 키움 ka10060 수집 → 21:20 저녁 빌드가 `basis='evening'` T 행을 만든다(`price_daily.sql:81-119`). 저녁 빌드가 커밋하면 MANIFEST current 가 e_ 로 바뀌어 ~22:00~다음 날 09:00 동안 이 판이 '현재 판'이다.
- 인풋: e_ 판 price_daily `basis='evening'` 행 vs 다음 아침 m_ 판 같은 날 KRX 행.
- 에러 위치: `src/equity/rules_s04.py:353-363`(`price.close` disclosure_basis "정규장 종가 확정(세션 마감)", evidence "키움 ka10060 종가로 채우고 … 자연 교체") · `src/equity/rules_s23.py:425-436`(`price.adj_close` evidence "adj_close = 키움 종가 × …") · `src/equity/sql/price_daily.sql:30-41`(주석 "그 종가·거래량으로 T 행" · `corp_action_pending` = 애프터마켓 가격 vs 직전 KRX 공식 종가 30% 비교). 반면 `docs/EQUITY_DESIGN.md` §13-2(10-05 갱신)는 "09-14 이후 close 는 애프터마켓 장후 마지막 체결가"라고 적고, 같은 절 13-4 가 "문서가 아니라 이 선언이 소비자에게 가는 정본"이라 한다 → 정본 쪽이 낡았다.
- 위험성: 저녁판을 읽는 소비자(MANIFEST 직독·`latest_evening.json`·v_adj_price_fwd)는 공식 종가와 다른 값을 '잠정 종가'로 받는다. 모델(fi)은 저녁판 가드로 안 읽어 엑셀 영향은 없다(N-13 이 이미 '모델에 쓰지 않는다'로 정리 — 기존 §3-C). 새 사실은 equity 판 단위 실측과 선언 미갱신. 카테고리: 문서 불일치(+ 저녁판 소비 시 silent corrupt).
- 근거(서버, e_/m_ 쌍 5일): 종가 일치 586/2648(09-28) · 558/2648 · 550/2650 · 577/2650 · 547/2651(10-02) = 20.6~22.1%. |차이| 중앙값 0.34~0.44%, p99 4.4~5.1%, 최대 12.9~26.1%. 거래량 일치 100%. corp_action_pending 0~9건/일.

### C-03 09-14 부터 확정판(KRX) volume_shr(확인)·value_krw(추정)가 애프터마켓 거래를 포함한다 — 선언은 '정규장 마감 집계', 기간 표식 없음 [하]
- 상황: KRX 일별 시세의 거래량·거래대금이 09-14 이후 애프터마켓(16:00~20:00) 체결까지 합산된다. 종가는 정규장 15:30 값.
- 인풋: 005930 2026-09-15 — 키움 시간별 프로브(`data/evidence/kw_timing.db`, ka10060) 누적값 16:05 = 11,089,851 → 20:05 = 11,435,506(+3.1%), 확정판 `price_daily`(m_20261003T003809) volume_shr = **11,435,506**(애프터마켓 포함 값과 정확히 일치).
- 에러 위치: `src/equity/rules_s04.py:393-404`(`price.volume`·`price.trading_value` disclosure_basis "정규장 마감 집계") · 파생 `src/equity/sql/universe_daily.sql:52-62·188`(adv20_krw·adv20_rank_pct 가 value_krw 20세션 평균) → `universe_policy` liquid 정책 · fi `adv20`(`factor_inputs/queries.py:185·281`) → v4 비교 모델 적격성 `min_adv20 = 10`(`config/models/v4_rank_0_*.toml`).
- 위험성: 거래량·거래대금 정의가 09-14 를 경계로 조용히 바뀌었다(구조 단절). adv20 이 09-14 전후 창을 섞고, 회전율·거래량 계열 연구 팩터와 v4 적격성 경계 종목이 영향받는다(크기는 삼성전자 1일 +3.1% 표본 1개만 확인 — 전 종목 크기는 미측정). 선언(소비자 정본)은 정규장 집계라고 말한다. 카테고리: 문서 불일치 + silent 정의 변경(연구 DB).
- 근거: 위 kw_timing.db 쿼리(`json_extract(row_json,'$.acc_trde_prica')` 시간별) · price_daily 005930 09-15 조회. DECISIONS N-13 근거 "KRX 일 거래량도 애프터마켓 포함"과 일치하나 equity 선언·adv20 영향은 어디에도 기록 없음.

### C-04 신주배정기준일이 미래인 무상증자의 corp_event.effective_date 가 '그 판의 마지막 거래일'로 찍혀 매일 앞으로 밀린다 → adj_factor no_price_match → fi adj_ok 가 D 에서 뒤집힘 [중]
- 상황: 무상증자 결정공시(stg_event_fric·pifric)가 접수됐고 신주배정기준일(nstk_asstd)이 아직 오지 않았다(캘린더 max 이후).
- 인풋: 서버 m_20261003 판(D=10-02). 052400(접수 09-14, 기준일 **10-30**) · 303360(09-29, **10-14**) · 023150(10-01, **10-20**) · 290650(07-31 유무상, **10-27**) · 456160(capital, ratio NULL).
- 에러 위치: `src/equity/sql/corp_event.sql:209-211` — bonus 의 effective_date = `(SELECT max(c.date) FROM cal c WHERE c.date < d.basis_date)`. basis_date 가 캘린더 밖(미래)이면 이 서브쿼리가 **캘린더 max(= D)** 를 돌려준다. 그 결과 `:258-260` 의 `out_of_calendar`(effective_date > cal_max) 판정도 통과한다(감자 cr_std 는 그대로 비교되어 걸러지는데 bonus 만 샌다). → `src/equity/sql/adj_factor.sql:148-153`(명목 세션 = D) → 가격 점프 없음 → `factor_source='no_price_match'`, `factor_ok=false`, `apply_date = D`. → `src/factor_inputs/queries.py:320-329` `bad` CTE 가 `NOT factor_ok AND apply_date ≤ D AND available_date ≤ D` 로 이 행을 '미해결 사건'으로 세어 adj_ok 를 D 에서 뒤집는다.
- 위험성: (1) corp_event 가 아직 일어나지 않은 사건을 'D 에 효력'으로 싣는다(매 빌드 D 로 이동 — 판마다 값이 바뀌는 행). (2) v4 비교 모델은 adj_ok 전환을 '수정주가미해결'로 보고 하위 지표를 결측 처리한다 — 실제 사건 전 수 주 동안 해당 종목이 v4 에서 빠질 수 있다(10-02 v4@0.1·0.2 에서 052400·290650 `excluded=True, insufficient_data`; 인과는 하위 지표 단위 미확인 = 가설). (3) universe_daily `ca_win`(`universe_daily.sql:79-94`)이 D 주변을 기업행위 창으로 보아 무거래 행을 suspended 로 분류할 수 있다. scope(v3 엔진)는 adj_ok 를 읽지 않아 엑셀 주 모델 영향은 없다. 카테고리: silent corrupt(가짜 효력일) + 비결정(매일 이동).
- 근거: corp_event 10개 판에서 052400 bonus effective_date 가 09-23 → 09-28 → 09-29 → 09-30 → 10-01 → 10-02 로 그 판 캘린더 max 를 따라감(290650·456160 동일, 303360·023150 은 접수 다음 판부터). stage stg_event_fric(m_20261003T001652) nstk_asstd: 052400 10-30 · 303360 10-14 · 023150 10-20, stg_event_pifric 290650 10-27. adj_factor m_20261003: 위 5건 apply_date 10-02 `no_price_match`/`ratio_null`. fi_adj_prices(m_20261005T165747) adj_ok: 023150·052400·303360·456160 이 10-01 True → 10-02 False.

### C-05 adj_factor 의 factor_ok=false 에는 '정상 처리된 사건의 중복본'(near_dup_suppressed·same_day_suppressed)도 들어가는데, fi adj_ok 는 이를 미해결 사건으로 센다 [하]
- 상황: 같은 사건이 두 원천(DART 결정공시·자본변동·KRX)에 실려 한쪽이 ok(계수 적용), 다른 쪽이 `near_dup_suppressed`(ok=false, 계수 1)로 눌린 경우.
- 인풋: 043220 capred — `near_dup_suppressed` apply 2025-11-19 + 형제 `mktcap_neutral`(ok) apply 2025-12-09. 최근 13개월 중복 억제 15건 중 형제가 ok 인 것 1건(나머지는 형제도 미해결).
- 에러 위치: 의미 정의 `src/equity/sql/adj_factor.sql:52-68`(factor_ok=false 사유 6종 — 억제 2종은 '중복이라 계수를 안 낸다') ↔ 소비 `src/factor_inputs/queries.py:320-325`(`WHERE NOT factor_ok` 전부를 미해결로 계단 표식).
- 위험성: 가격은 이미 올바르게 조정됐는데 adj_ok 가 억제 행 적용일에 뒤집혀 v4 가 그 날을 넘는 창의 하위 지표를 '수정주가미해결'로 비운다(거짓 결측). 043220 은 fi_adj_prices 에서 2025-11-19~2026-10-02 adj_ok=False(213행) — 지금은 v4 유니버스 밖이라 실영향 없음(잠재). 계약 문서에 'factor_ok=false ≠ 미해결' 구분이 없다. 카테고리: 문서 불일치(층 간 계약 의미) — fi 쪽 수정 대상이지만 equity 계약이 원인.
- 근거: adj_factor m_20261003 조회(억제 행·형제 행), fi_adj_prices m_20261005T165747 043220 adj_ok 집계.

### C-06 EG21(최신 구간 행수 완결성)이 격자 3표에서는 구조적으로 항상 통과하고, opinion_daily 에서는 등록(09-19) 뒤 줄곧 SKIP 이다 — DEFECT-C06 감시 공백이 실제로는 안 메워졌다 [하]
- 상황: 일일 운영에서 EG5a 는 매번 skip(서버 현판 30표 = inputs_changed 29 + security rules_changed)이라, 최신 구간 이상을 잡는 폐기형은 EG14(price_daily)와 EG21 뿐이다.
- 인풋: flow_daily·short_daily·credit_daily·opinion_daily 의 EG21 (현판 m_20261003, 직전 e_20261002).
- 에러 위치: `src/equity/gates.py:308-370` — 날짜별 `count(*)`(행수)만 센다. 그런데 격자 표는 `universe_daily` 격자 셀마다 측정 여부와 무관하게 1행을 만든다(`sql/flow_daily.sql:17-24` ② 미측정 셀). 그래서 날짜별 행수 = 유니버스 크기(서버 baseline_median_rows 2,764.5)이고, 그날 키움·KIS 값이 통째로 없어도 행수는 그대로다. `rules_s08.py:555-556` 주석("날짜 파티션이 통째로/반쯤 빈 경우를 잡는다")의 경우는 격자 정의상 생기지 않는다. opinion_daily 는 `base_date` 축 세션이 22개(09-01~10-02)뿐이라 `lag+window+base = 23` 미만 → `skip(no_coverage)` (`gates.py:348-350`).
- 위험성: equity 층에는 '최신 세션 측정 비율' 폐기형이 없다. 행수 축으로는 '측정 0' 과 '측정 100%' 가 같은 값이다(아래 보강의 예시). 원장 건전성(U9·U10·U12)이 위에서 막고 있어 단독 사고로 번지지는 않지만, equity 게이트가 막는다는 09-19 감사 정리(model.py e1.16.0 ③)는 사실과 다르다. opinion_daily 는 다음 거래일(세션 23개)부터 자연 활성. SKIP=통과 패턴의 새 위치(기존 §6-6 목록에 없음). 카테고리: 게이트 공백 + 문서 불일치.
- 근거: 서버 MANIFEST 게이트 metrics — flow_daily·short_daily EG21 pass `baseline_median_rows=2764.5`(= 유니버스 크기); opinion_daily EG21 `skip no_coverage n_sessions_seen=22`.

### C-07 기업행위 공시의 announce_date 가 '마지막 정정 접수일'이라 무상증자 계수가 권리락일보다 늦게 접힌다 → price_adj_daily 에 하루짜리 가짜 급락·급반등 [중]
- 상황: 무상증자결정 공시가 권리락 뒤에 [기재정정]된 경우. DART API 는 정정본만 돌려주므로 stage `stg_event_fric` 의 rcept_no·available_date 가 마지막 정정본이다(e1.22.0 이 fin_std 에서 고친 것과 같은 원인 — corp_event 에는 적용 안 됨).
- 인풋: 서버 현판(m_20261003). 2025-01 이후 factor_ok 인데 `available_date > apply_date` 인 사건 29건 — DART 무상증자 8건(246960·419120·209640·472850·480370·368970·004270·002070) + KRX 기준가로만 잡힌 unknown_krx 다수(900xxx 외국기업 등) + 기타. 예: 002070 원공시 2026-07-15 '주요사항보고서(무상증자결정)', 권리락 07-31, 정정 08-12 → corp_event announce_date **08-12**.
- 에러 위치: `src/equity/sql/corp_event.sql:203-207`(`announce_date = coalesce(d.available_date, rcept_no 앞 8자리)` — 정정본 기준, 주석 `:46-47`) → `src/equity/sql/adj_factor.sql:425-426`(available = least(announce, 다음 세션) → 다음 세션) → `src/equity/sql/price_adj_daily.sql:53-57`(fold_date = greatest(apply_date, available_date)). KRX 기준가 신규 행((b)(d))도 `adj_factor.sql:89`·`:425-426` 에서 available = 다음 세션인데, 같은 KRX 일별 파일에서 온 price_daily 행은 available = date 라 규약이 엇갈린다.
- 위험성: 권리락일 하루 동안 조정가가 조정되지 않아 수정주가 일간 수익률에 가짜 점프가 생긴다 — 무상증자 −34%~−68% 뒤 다음 세션 +41%~+247%, KRX 전용 감자·병합 +180%~+3,702% 뒤 −74%~−98%. fi `adj_ok`(factor_ok 기준)는 이를 못 거른다. scope(v3 엔진)는 모멘텀 r1m~r12m(30%)을 adj_close 끝점 수익률로, 퀄리티 `std_20d`(퀄리티 안 20%)를 20세션 변동성으로 쓰므로, 권리락일이 D 인 날 그 종목 모멘텀이 반토막 나고 이후 20세션 변동성이 부푼다(유니버스 포함 여부는 미확인 — 영향 크기는 가설). 연구 백테스트의 수정주가 수익률도 같다. EQUITY_DESIGN S21 한계 (i) 가 '서버 건수 실측 필요'로 남긴 것의 실측이며, 원인 중 정정일은 새 발견. 카테고리: silent corrupt(조정가) + PIT 규약 불일치.
- 근거: adj_factor 조회(연도별 available>apply 인 ok 사건: 2025 9건 · 2026 20건, unknown_krx 13건), price_adj_daily 해당 날짜 adj_close 수익률(예 900300 06-24 +37.02배·06-25 −0.984, 246960 06-30 −0.675·07-01 +2.47), stage stg_disclosure 에서 8개 법인 모두 원공시가 권리락 전, 정정이 권리락 뒤임을 확인.

### C-08 워크벤치 어댑터·FIELD_MAP 은 자사주 취득을 `event_type='tsstk_aq'` 로 찾는데 equity 어휘는 `treasury_buy` 다 → `event.buyback_amount` 가 소비자에게 항상 빈 값 [중]
- 상황: e1.9.0(09-07, cbc04e7a)이 corp_event 에 자사주 취득을 `treasury_buy`(1,944행)로 싣고 equity 선언(dataset_profile)도 `row_filter="event_type = 'treasury_buy'"` 로 바꿨다. 어댑터와 문서는 그 전(09-06) 이름에 머물렀다.
- 인풋: 워크벤치 `equity_duckdb` 어댑터로 `event.buyback_amount`(팩터 E05·I03·I04 재료)를 요청.
- 에러 위치: `backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_specs.py:309`(`row_filter="event_type = 'tsstk_aq'"`, `:904-909` evidence 도 'tsstk_aq') ↔ `database/src/equity/rules_s05.py:56`(`FACT_ONLY_TYPES = ("treasury_buy", "cb_issue")`)·`:384-387`(선언 row_filter 'treasury_buy'). 문서 `docs/EQUITY_FIELD_MAP.md:155`(어댑터 표 `corp_event(event_type='tsstk_aq')`)·`:64`(§2 "커버율 0 — treasury_buy 행이 하나도 없다", 실제 dataset_profile 커버 19.6%).
- 위험성: 어휘 밖 값으로 거르므로 0행 → 셀 없음/결측. 오류 없이 자사주 계열 팩터가 연구 백테스트에서 조용히 빠진다. 어댑터 계약 테스트가 이 불일치를 못 잡았다(실데이터 0행이어도 통과하는 구조로 보임 — 가설). 모델 파이프라인(fi/scope)은 이 필드를 안 써 엑셀 영향 없음. 카테고리: silent(데이터 소실) + 문서 불일치(층 간 계약).
- 근거: 서버 corp_event m_20261003 event_type 집계 `treasury_buy 1944, cb_issue 4734` (tsstk_aq 0) · dataset_profile `event.buyback_amount` estimated_coverage_pct 19.65 · `git log -S tsstk_aq` → 어댑터 마지막 수정 5b6553ba(09-06) 이후 이 줄 변경 없음.

### C-09 10-05 수동 부분 재빌드로 equity 현재 판이 섞였고, 완료 신호·인계·카탈로그 메타는 옛 판을 가리킨다 [하]
- 상황: 10-05 19:05·19:37 KST 에 `coverage_daily`(b_20261005T100502, e1.22.0, stage stg_analyst_summary b_ 판 입력)·`security`(b_20261005T103717, **e1.23.0**)만 손으로 재빌드. 나머지 28표는 m_20261003(e1.22.0). 10-05·10-06 아침은 휴장 가드로 체인이 안 돌아 다음 전량 빌드는 10-06 21:20 저녁판.
- 인풋: 서버 MANIFEST·`data/equity/_READY.json`·`data/deliver/latest_morning.json`·`data/equity/_catalog_meta.json`.
- 에러 위치: 절차 공백 — `src/equity/__main__.py:8`("catalog — 빌드·GC 뒤에는 반드시")·`scripts/build_chain.sh:177-210`(완료 신호는 체인 안에서만 기록). 수동 `equity build` 경로(`scripts/run_equity.sh`)는 카탈로그·인계·완료 신호를 갱신하지 않는다.
- 위험성: 같은 순간에 '현재 판'이 셋이다 — MANIFEST(b_ 2표) / `_READY.json`·`latest_morning.json`·`_catalog_meta.builds`(m_20261003). `_READY.json` 을 따르는 공유 소비자(상목 SFTP)는 약명 열이 없는 옛 security 를 읽고, MANIFEST 를 따르는 fi·compat 은 새 판을 읽는다. fi `_check_basis` 는 b_ 를 허용하고(`factor_inputs/build.py:137-143`) 가격 3표 외에는 체인 일치를 보지 않는다. 카탈로그 매크로 9개는 이 두 표를 참조하지 않아 실해는 확인 안 됨. 한 판 안에 RULES_VERSION 이 두 개(e1.22.0·e1.23.0) 공존. 카테고리: 운영 공백(판 섞임) + 문서 불일치.
- 근거: MANIFEST 요약(security `cur=b_20261005T103717 rv=e1.23.0`, coverage_daily `cur=b_20261005T100502`), `_READY.json` security=`m_20261003T003800_180795Z`·coverage_daily=`m_20261003T004559_515414Z`, `_catalog_meta.json` written 10-03 00:49Z.

### C-10 소비자 계약(EG-C)은 기록형이라 실패해도 판·인계·완료 신호·일일 리포트 어디에도 안 남고, 워크벤치 어댑터는 계약 대상이 아니다 [하]
- 상황: 체인은 `equity contract` 를 `step_soft` 로 돌린다. 실패 시 `FAILED_SOFT` → `notify.sh warn` 1건인데, 10-01(N-3) 이후 notify 는 로그만 남긴다.
- 인풋: build_chain 8단계(계약) rc≠0 인 날.
- 에러 위치: `scripts/build_chain.sh:77-86·128·287`(soft 처리) · `:131-175`(인계 JSON `health` 에 계약 축 없음) · `scripts/daily_report.py`(계약·FAILED_SOFT 를 읽는 줄 없음, grep 0건) · 계약 대상은 커널 어댑터(`backend/src/backtest_engine/adapters/equity_duckdb.py`)뿐이고 워크벤치 어댑터(`strategy_workbench/adapters/outbound/equity_duckdb`)는 검사 밖(`src/equity/contract.py:1-40`).
- 위험성: 계약이 깨진 판도 `latest_*.json`·`_READY.json` 에 health ok 로 실린다 — 알 수 있는 곳은 `logs/<basis>/build_<D>.log` 와 `logs/notify.log` 뿐(Q-6 '매일 아침 점검'이 정해지기 전까지 사람이 안 본다). 워크벤치 쪽 계약 공백은 C-08(자사주 어휘 불일치)이 실제로 빠져나간 경로다. 지금까지 계약은 16회 연속 rc 0(09-20~10-03) — 실해 없음, 잠재 위험. 카테고리: 운영 공백(실패 은닉).
- 근거: 서버 `grep "equity contract 종료"` 16건 전부 rc=0, `_contract_meta.json` status pass(10-03 00:49Z), 커널 어댑터 서버 사본 md5 = 저장소(1141af8f…).

#### C-03 보강(03:18) — 수급(flow_daily)도 09-14 부터 애프터마켓 포함
- 005930 2026-09-15 키움 ka10060 프로브: 16:05~20:05 개인 341,831 · 외국인 −291,051 · 기관 −559,506(백만원, 정규장 확정값) → 21:05 342,347 · −271,644 · −578,325. equity `flow_daily`(m_20261003, kiwoom) = **21:05 값**과 일치(외국인 +6.7%p 차). 원본 v3 `investor_detail_flows` 도 같은 값(덮어쓰기로 최종값 보유) → v3 대조 차이는 없음.
- 함의: N-13(장 마감 직후 15:30~16:05 수집)으로 가면 D 의 수급은 정규장분만, 09-14~전환 전 이력은 애프터마켓 포함이라 같은 열에 두 정의가 섞인다(scope 수급 20%: inst/for/pe 5d·20d). 거래량·거래대금(KRX)도 같은 문제. 설계 시 정의를 하나로 고정해야 한다. 카테고리: 정의 단절(문서 없음).

#### C-06 보강 — 문서 근거
- `docs/EQUITY_GATES.md:2433-2462`(§11-1)이 EG21 의 임계 0.8 근거로 "격자 3표 행수 2,762~2,766(±0.15%)"를 들고 "커버가 −67% 나면 걸린다"고 쓰지만, 그 진폭은 유니버스 크기(구조)이고 '커버 −67%'는 비격자 표(opinion_daily)에서만 행수로 드러난다. 격자 3표에 필요한 축은 날짜별 `fill_kind.kind='measured'` 비율이다(EG3_flow_daily 등에도 최신 세션 측정 비율 폐기형 없음).

### C-11 e1.22.0 '정정본 원본 공시일 승계'가 재무 수치를 바꾼 정정도 '재무 무관'으로 판정해, 재작성된 값에 원본 공시일을 붙인다 (look-ahead) [중]
- 상황: fin_std 는 정정 체인의 모든 정정이 `corr_has_fin_item = false` 이면 available_date 를 원본 접수일로 당긴다(서버 현판 3,757행). `corr_has_fin_item` 은 정정 '항목 이름'에 키워드 7개(재무제표·재무상태표·손익계산서·현금흐름표·자본변동표·요약재무·재무에 관한)가 있는지로만 정한다.
- 인풋: (1) 00287812 FY2015 사업보고서, 2018-09-07 정정(20180907000426). 항목 이름은 "Ⅰ회사의 개요 6. 배당에 관한 사항 등 -주요배당지표"뿐이지만 내용은 "(연결)당기순이익(백만원) 11,202 → 9,213"(사유: 2015~2017 연결재무제표 재작성·감사보고서 재발행). (2) 정정 첫 장에서 항목을 하나도 못 뽑은 정정(`items='[]'`, n_items 0) — 예 20181129000580(개발비 판단오류로 재무제표 재작성), 20170915000145(재감사로 재무제표 수정).
- 에러 위치: `src/equity/sql/disclosure_version.sql:219-225` — 항목 이름 LIKE 만 보고, `items='[]'`(빈 목록)은 NULL 이 아니라 FALSE 가 된다(주석 `fin_std.sql:168` "첫 장 미해석 NULL 은 건드린 것으로 본다"의 의도와 어긋남) · 키워드 정본 `src/equity/rules_s11.py:70-71` · 승계 `src/equity/sql/fin_std.sql:165-183`.
- 위험성: 재작성된 재무값(2018-09 공개)이 원공시일(2016-03-30)부터 보인다 → 2016~ 백테스트·연구 패널의 퀄리티·밸류 팩터가 미래 정보를 쓴다. 실측 00287812 FY2015 CFS: available_date 2016-03-30 · rcept_dt 2018-09-07 · 순이익 열에 재작성 값 9,213,190,883. 규모: 승계 3,757행 중 체인에 빈 항목 정정이 낀 것 56행(rcept 2016-05~2025-03), 항목 내용(정정전/후)에 순이익·매출·자산총·영업이익·자본총이 나오는 것 287행(전부가 재무 변경은 아님 — 상한). 실시간 모델(그날 최신값 사용)에는 영향 없음. 카테고리: look-ahead.
- 근거: equity fin_std·disclosure_version(m_20261003) + stage stg_doc_correction(items·n_items) 조인 쿼리, 위 예시 행 직접 조회.

#### C-05 보강(03:22)
- `unknown_price_only`(기준가/직전 종가가 1±`base_price_tol_rel`=**0.2%** 밖, 주식수 불변, 전일 거래 — 계수를 만들지 않는 사실 행)도 factor_ok=false 라 fi `bad` 에 들어간다. 예: 207940(삼성바이오로직스) 10-02 기준가 1,418,000 / 직전 종가 1,429,000 = −0.77% → adj_ok 가 D 에서 뒤집힘. fi 적격 626 종목 중 최근 365일 안에 adj_ok 가 바뀐 종목 24개(009830·010170·011790·207940·068270·006800·052400·290650 …). scope 는 adj_ok 를 안 읽어 영향 없음, v4 는 해당 창 하위 지표 결측.

#### C-01 보강
- `docs/TECH_DEBT.md:1055` 는 REC-13(롤백 시작 판)을 '배포 전에 닫은 것'으로 적는다 — 실제로는 정상 운영 순서(저녁 e_ 커밋 → 아침 패스)에서 '시작 판 = 저녁 잠정판'이라 닫히지 않았다. 확정 자리를 지키려면 before 를 '같은 basis(m_) 의 마지막 판'으로 잡아야 한다(수정안, 미검증).

#### C-07 보강 — 게이트가 못 잡는 이유
- adj_factor EG8(적용 세션 수정수익률 |r| ≤ `adj_return_jump_max`=1.0)은 현판 pass(`max_abs_adj_return_jump` 0.30, `n_return_jump_over` 0). EG8 은 계수를 **apply_date 에** 접어 재는데, 소비 표 price_adj_daily 는 `greatest(apply_date, available_date)` 에 접는다 — 같은 사건이 소비 표에서는 +3,702%/−98% 점프를 내도 게이트 축에서는 0 이다. price_adj_daily 의 EG3 도 '재계산 일치'만 보고 일간 수익률 점프는 안 본다.

#### C-06 보강 — 측정 비율 예시
- (처음 적었던 credit_daily 10-01·10-02 측정 0 예시는 EG21 lag 3 세션 판정 밖이라 근거에서 뺐다.) 격자 표는 측정 비율과 무관하게 날짜별 행수가 유니버스 크기다 — short_daily 2024-05-30 행 2,685 · 측정(키움) 541(20.1%), flow_daily 최저 2024-01-03 행 2,656 · 측정 2,415. 행수 축으로는 '측정 0' 과 '측정 100%' 가 같은 값이 된다는 것이 결함의 요지(논리 확인, 실제 사고 사례는 미발견).

### C-12 문서 묶음: 'EG14' 이름 충돌 · 미구현 제안 게이트 · '29표' 표기 [하]
- 상황/인풋: 문서와 코드 대조.
- 에러 위치·내용:
  1. `docs/EQUITY_GATES.md:1541-1560`(§6 제안) 의 **EG14 = 파티션 경계 누락**(연도 파티션 통째 결측 탐지)과 코드의 **EG14 = 최신 KRX 세션 행수 완결성**(`rules_s04.py`, GATES §10, e1.15.0)이 같은 이름이다. 'EG14 pass' 를 보고 파티션 경계가 검사됐다고 읽을 수 있다. 제안 표(`:1668-1683`)의 EG12(단위 접미사)·EG13(available_date 미래값)·EG15(폐지 직전 가격)·EG18(조인 팬아웃)·EG19(as-of 단조성)는 코드에 없다(grep 0) — 표에 '미구현/보류' 표시가 없다. 현재 미래 available_date 행은 0(전 표 스캔)이라 실해는 없음.
  2. equity 표는 **30개**(`scripts/equity_order.txt` 비주석 30줄, 서버 MANIFEST 30, before.json 30)인데 `scripts/equity_rebuild_all.sh:2·40`·`scripts/equity_gate_all.sh:2·13`·`scripts/build_chain.sh:42`·`src/equity/gates.py:314` 가 '29표'라 적는다(S25 sector_snapshot 09-20 추가 뒤 미갱신). DEFECT-C09 가 바로 이 '표 수 오독'(28 vs 29)이었다.
- 위험성: 게이트 결과·표 수 해석 오류. 카테고리: 문서 불일치.
- 근거: grep 결과(EG12·13·15·18·19 구현 파일 0), `grep -vcE '^\s*(#|$)' scripts/equity_order.txt` = 30, 미래 available_date 스캔 0건.

#### C-04 보강(03:31) — 작은 무상증자는 '조정 완료'로 잘못 접혀 scope 모멘텀까지 건드린다 (코드 경로로 확인, 현판에는 대기 사례 없음)
- 같은 원인(미래 배정기준일 → effective_date = D)에서 비율이 작으면(ratio ≤ 1/(1−0.05) ≈ 1.0526, `jump_mag ≤ price_match_tol_abs`) `adj_factor.sql:176-182` step_a 가 **가격 확인 없이** 'nominal' 로 통과 → `factor_ok = TRUE`, apply_date = D, available = announce(< D) → `price_adj_daily.sql:53-57` 이 D 행에 share_factor(예 1.03)를 곱한다. 결과: 공시~실제 권리락 사이 매일의 판에서 그 종목 D 수정종가가 +r% 부풀고(fi adj_ok 는 True 라 못 거름), scope 모멘텀 r1m~r12m 끝점이 같은 비율만큼 올라간다. 실제 권리락일이 캘린더에 들어오면 KRX 기준가로 교체되어 이력은 바로잡힌다(그래서 사후 점검으로는 안 보인다).
- 빈도: 비율 ≤ 1.0526 인 무상증자 2023 20건 · 2024 3건 · 2025 4건 · 2026 2건, 공시~효력 13~18일(예 002720 1.05 · 028300 1.05 · 067290 1.05). 현판(m_20261003)에 대기 중인 작은 무상증자는 없음 — 실제 판에서의 재현은 미확인(가설 등급의 영향, 경로는 확정).

- 대표 사례: **068270(셀트리온)** 이 해마다 4~5% 무상증자를 한다(2025-05-28 공시 → 06-09 효력 1.04, 2026-05-21 → 06-04 1.05). 같은 일정이 반복되면 공시~권리락 약 2주 동안 매일의 판에서 셀트리온 D 수정종가가 +5% 부푼다 — scope 유니버스의 대형주. 이번 모델 판(09-01~10-02 재생성 포함)과는 겹치지 않아 실발생 0, 다음 위험 구간은 2027 상반기(추정).

#### C-03 정정(03:35) — 확인 범위
- 직접 확인한 것은 **거래량**(KRX volume_shr = 키움 20:05 이후 누적값)이다. `value_krw`(거래대금)도 같은 KRX 일별 파일이라 애프터마켓 포함으로 **추정**하나 분리 검증 수단이 없어 미확인. adv20 영향은 거래대금 기준이므로 이 추정에 기대고 있다.

#### C-07 보강 — 규모
- 2016 이후 DART 결정공시 원천 사건 중 announce_date(= 마지막 정정 접수일) > 효력일: 감자(event_cr) 154/587 · 무상(event_fric) 33/720 · 유무상(event_pifric) 6/98. 전 기간 factor_ok 인데 fold 가 적용일보다 늦는 계수 353건(그중 bonus·capred·split·reverse_split 296). 감자는 적용일이 변경상장일(기준일 수 주 뒤)이라 대개 영향이 없고, 무상증자(권리락 = 효력일)가 직접 맞는다.

#### C-02 보강 — 소비자 쪽 거름망
- 워크벤치 어댑터는 백테스트 bar 경로에서만 `basis='krx'` 로 거르고(`backend/.../equity_duckdb/_adapter.py:44-50·930`), 팩터 관측 경로의 가격 원천 명세(`_specs.py` PRICE_TABLE·ADJ_TABLE `row_filter=None`)는 저녁 행을 거르지 않는다. 저녁판이 current 인 밤사이 동기화한 루트에서 as_of = T 로 팩터를 뽑으면 애프터마켓 종가가 '종가'로 들어간다(가설 — 실제 사용 패턴 미확인).

### C-13 equity 재현성(같은 입력 → 같은 해시) 실측이 09-09(e1.14.0, 28표)가 마지막이다 — 이후 9개 판본·신설 2표는 재현성 확인 없음 [하]
- 상황: 일일 운영의 EG5a 는 매번 `skip(inputs_changed)`(현판 29표 + security `rules_changed`). 재현성 증거는 두 번 연속 전량 재빌드의 summary 해시 대조뿐이다.
- 인풋: `logs/equity/rebuild_p0pass1`·`rebuild_p0pass2`(09-09, e1.14.0) — 28표 content_hash 차이 0. 그 뒤 쌍(pass1/pass2) 실행 기록 없음(rebuild_manual_20260919 단독).
- 에러 위치: 절차 공백 — `scripts/equity_rebuild_all.sh:9-10`("재현성 검사 = 두 pass 의 summary.tsv content_hash 열 비교")가 정한 검사를 e1.15.0~e1.23.0(저녁 행·EG21·S25 sector_snapshot·S24 coverage_daily·fin_std capex/정정일 승계·약명) 뒤에 다시 하지 않았다.
- 위험성: 비결정 산출(동률 정렬·집계 순서)이 생겨도 일일 운영에서는 '입력이 바뀌었다'로 가려져 모른다. 모델 재생성(N-17)·감사 재현이 같은 판을 다시 못 만들 수 있다. 실제 비결정 사례는 이번에 찾지 못함(가설). 카테고리: 비결정(검증 공백).
- 근거: 위 두 summary 의 5열(해시) join 결과 28표 0 diff, 이후 rebuild_* 목록.

#### C-03 보강 — 기존 기록과의 관계
- `docs/EQUITY_DESIGN.md` §13-2 표(10-05 갱신)는 저녁 행 설명에서 "volume_shr 는 애프터마켓 포함(KRX 와 동일 정의)"이라 적어 KRX 거래량의 애프터마켓 포함을 **이미 전제**한다. 그런데 소비자 정본 선언(`rules_s04.py:393-404` "정규장 마감 집계")과 adv20·유동성 정책·v4 적격성에 대한 영향, 09-14 정의 단절 표식은 어디에도 없다 — 새 발견은 이 부분이다.

#### C-08 보강 — 준비도 표도 낡았다
- `factor_readiness`(현판) E05 '자사주 취득 발표' = `status='ready'` 인데 caveat 는 "corp_event 가 MVP 4유형만 적재해 treasury_buy 행이 0" 이라 적는다(`src/equity/rules_s20.py:307-308`, e1.9.0 이후 미갱신). 준비도 표는 ready·caveat 0행으로 서로 모순이고, 실제 소비 경로(워크벤치)는 'tsstk_aq' 필터로 0행이다 — 준비도가 소비 가능성을 증명하지 못한다.

#### C-11 보강(03:37) — 규모 재추정
- 승계 3,757행 중 정정 체인의 사유(reason_raw)에 '재작성·재감사·재발행·소급·재무제표 수정'이 명시된 판 **21행** = 확실한 look-ahead. '항목 내용에 순이익·자산총계 등' 287행은 표본 6건 중 다수가 경영진단·주주·이사회 서술 정정이라 대부분 무해(상한일 뿐). 빈 항목 목록 56행은 사유 확인이 필요하다.

## 요약(심각도순)
| ID | 심각도 | 한 줄 | 카테고리 |
|---|---|---|---|
| C-11 | 중(look-ahead — 상향 검토) | fin_std 원본 공시일 승계가 재무를 바꾼 정정도 '무관'으로 판정(항목 이름 키워드·빈 항목 목록) → 재작성 값에 원공시일(확실 21행, 상한 ~340행 / 승계 3,757) | look-ahead |
| C-07 | 중 | 기업행위 announce_date = 마지막 정정일 → 무상증자 계수가 권리락 다음 세션에 접혀 수정주가 하루 가짜 점프, EG8 축이 달라 못 잡음 | silent corrupt |
| C-04 | 중 | 미래 배정기준일 무상증자의 effective_date 가 매일 그 판의 D 로 이동 → 큰 비율은 adj_ok 가짜 전환(v4), 작은 비율(≤5.26%, 셀트리온 매년)은 D 수정종가 +r% (scope 모멘텀) | silent corrupt·비결정 |
| C-01 | 중 | 아침 확정 실패 롤백의 '시작 판'이 전날 저녁 잠정판(e_) — REC-13 수정 무효 | 운영·문서 불일치 |
| C-08 | 중 | 워크벤치 어댑터·FIELD_MAP 의 자사주 어휘 'tsstk_aq' ≠ equity 'treasury_buy' → event.buyback_amount 항상 빈 값, 준비도 caveat 도 낡음 | silent 소실 |
| C-03 | 하 | 09-14 부터 확정판 거래량(거래대금 추정)·수급이 애프터마켓 포함 — 선언 '정규장 마감 집계', adv20 정의 단절, N-13 전환 시 재단절 | 정의 단절·문서 |
| C-02 | 하 | 저녁판 T 행 종가 = 애프터마켓 마지막 체결가(공식 종가 일치 21%), 소비자 정본 선언은 '키움 종가·정규장 종가' | 문서 불일치 |
| C-05 | 하 | factor_ok=false 에 '정상 사건의 중복본'·0.2% 기준가 변화도 포함 → fi adj_ok 가 미해결로 셈 | 층 간 계약 |
| C-06 | 하 | EG21 은 격자 3표에서 행수(=유니버스 크기)만 봐 항상 통과, opinion_daily 는 등록 뒤 줄곧 SKIP | 게이트 공백 |
| C-09 | 하 | 10-05 수동 부분 재빌드로 current 판 섞임, _READY·latest·카탈로그 메타는 옛 판 | 운영 공백 |
| C-10 | 하 | 계약(EG-C) 실패는 로그에만, 워크벤치 어댑터는 계약 밖 | 운영 공백 |
| C-12 | 하 | GATES 'EG14' 이름 충돌·제안 게이트 5종 미구현 표시 없음·'29표'(실제 30) | 문서 불일치 |
| C-13 | 하 | 재현성 쌍 실측이 09-09(e1.14.0)가 마지막 | 비결정 검증 공백 |

## 문제없음 확인
- 서버 `src/equity`(py·sql·json 113파일) md5 = 저장소 HEAD b220060a, equity 스크립트 4종 동일, `data/equity/baseline.json` = `baseline_locked.json` 바이트 동일(sha256 5e94f48d…).
- RULES_VERSION 이력(e1.0.0→e1.23.0) vs `src/equity` 산출 변경 커밋: 산출을 바꾼 커밋은 모두 같은 날 상향 동반(09-08 키움 대차 병합 1cbaa0f4 는 e1.14.0 상향 8분 뒤 같은 판본에 묶임 — 그 사이 서버 판은 미확인, 영향 없음으로 판단).
- 현판 30표 게이트 FAIL 0. SKIP 은 EG5a inputs_changed(security 는 rules_changed)·EG2 dimension_table·EG1 declaration_table·EG21 opinion no_coverage 뿐. EG14·EG20·EG3_price_daily(`n_evening_rows_in_morning_build` 0) 통과.
- 계약 EG-C 09-20~10-03 16회 rc 0, `_contract_meta` pass, 커널 어댑터 서버 사본 = 저장소.
- 카탈로그 매크로 9개가 가리키는 판 = MANIFEST current(낡은 참조 0).
- 저녁판 vs 아침판: KRX 축 표(trading_calendar·security·security_span·universe_daily·flow·short·credit·index·adj_factor·fin_std)는 저녁판 해시 = 직전 아침판(그날 새 데이터 없음), 저녁에 바뀌는 것은 DART·WISE 당일분 표와 price_daily·price_adj_daily(T 행 2,648~2,651)뿐 — 설계대로. 수급 T 행은 off_grid 격리(+2,651, COMPAT_LAYER §6 '서버 실측 미확인'을 실측 확인). universe_daily 저녁 T 이월은 미구현(RM 단계 2 계획) = 현 코드와 일치.
- 아침판 KRX 종가로 저녁 T 행 자연 교체(겹침 0). 거래량은 저녁·아침 100% 일치.
- security `name_abbrv_current` NULL 0(전 sec_type), ETF 는 가격표 이름, 재상장 2종(036220·101970) 구간 정상.
- 09-19 수정 뒤 롤백 실발생 0, 아침·저녁 패스 전부 TOTAL(실패 없음, 09-23 저녁 제외 — 원장 미완료로 빌드 자체 미시작).
- 빌드 피크 RSS 7.7GB < 한도 8GB, `_tmp` 잔여 8.6MB, `_pinned` GC 매 체인 1.47GB 회수·보호 id 1,383~1,451.
- 미래 available_date 행 0(available_date 가진 전 표 스캔).
- universe_daily halt_state=true 인데 그날 거래한 행 0(09-28~10-02).
- fi `_check_basis` 가 아침 fi 에서 e_ equity 판을 거부 → C-01 상황에서도 엑셀 오발송은 막힌다.
- 원본 v3 `investor_detail_flows` = ql flow_daily(둘 다 애프터마켓 포함 최종값) → G-M3 수급 대조 차이 없음.
- 어댑터 행 필터 어휘(consensus metric eps·revenue, holder src elestock) = equity 어휘(자사주 하나만 불일치 → C-08).
- 워크벤치 어댑터 FieldSpec 30개가 읽는 값 열(close·adj_close·fin 8계정·est_mean/min/max·target_price_krw·opinion_score·analyst_count·flow 3·short/lending 키움·whol_loan_rmnd_stcn_shr·dps_krw·amount_krw·qty_change_shr)은 서버 현판 스키마에 전부 존재.

## 미조사
- fin_std 24계정 tier·pick 규칙 전반(EG8_fin_std 외), C-11 의 287행 중 실제 재무 변경 비율(정정 전후 값 대조).
- consensus_daily·opinion_daily·opinion_broker_daily·holder_daily·ownership_snapshot·treasury_stock·shares_outstanding·audit_opinion SQL 상세.
- credit_daily·short_daily 원천 선택·판본 접기 규칙 상세.
- EQUITY_GATES §3 EG1 등식 전수 vs 코드 1:1 대조.
- 워크벤치 어댑터 `_specs.py` 필드별 랙(lag_sessions 폴백) vs dataset_profile `recommended_lag_sessions` 대조(열 존재는 확인함).
- 애프터마켓이 KRX value_krw 에 주는 전 종목 크기(분리 원천 없음).
- `scripts/equity_diff.py`(GATES §12).
- C-04 작은 무상증자 경로의 실제 판 재현(현판에 대기 사례 없음).
- 로컬 워크벤치 동기화: 이 브랜치의 `scripts/fetch_equity_local.sh` 는 표마다 rsync 하고 잠금·`_READY`·basis 확인이 없어 서버 빌드 중이나 저녁판 current 시간에 받으면 섞인 판·잠정판을 받는다(코드 확인). 실제 사용 도구 `ledger_sync.sh`(verify 포함)는 main 에만 있어(이 브랜치 밖) 미조사.

## 끝
