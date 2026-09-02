# Stage 레이어 구현 설계 v2.1 (최종)

> 2026-09-02. v2 를 Opus 5기 **2차 병렬 적대 리뷰**(전건 서버 실측 기반 — 기계검증·PIT재검·
> 회귀검증·사실실측·구현시뮬레이션)로 재개정. 층 계약·실측 사실의 정본은 `STAGE_SPEC.md`.
> v1 의 실패 = 실측을 뒤집음. v2 의 실패 = 실측을 안 함(유령 컬럼 2·유령 클래스 1·미정의 3).
> **v2.1 의 원칙: 규칙 한 줄마다 원장 실측 근거를 병기한다. 근거 없는 규칙은 쓰지 않는다.**
>
> v2 → v2.1 핵심 변경 6:
> ① available_date 재정의 — **사실 날짜만 기록, 랙·보수 버퍼 금지 (판단은 엔진)** [결정 ⑥]
> ② 골격 3컬럼 추가 — `observed_date` · `miss_kind` · 테이블 선언 `write_mode`
> ③ `is_latest`·`observed_seq` 제거 — 판본 선택은 equity 가 (available_date, observed_date) 로
> ④ 중복 접기 = **payload 투영 동일** (바이트 동일 정의는 실측상 공집합)
> ⑤ 카탈로그 실측 정정 — 지수 `IDX_NM`/`IDX_CLSS` · WISE ep 지도 · stg_fin 자연키 8컬럼
> ⑥ 게이트 9→10종 (G9 교차 소스 회귀) + G5 Δ등식 + G7 축 분리

## 0. 범위

**포함**: 정형 원장 5 DB(krx·kiwoom·kis·dart·wisereport) 실물 61테이블의 stage 변환.
**제외 (사유 명기)**:
- 문서층 L1 (ZIP 본문 파싱) — 별도 트랙. 구양식 연대별 표본(2010·2013·2016·2020·2024) 검토 선행
- equity.db(조인·정본 선정·조정계수·유니버스·BCNF 분해)·팩터층 — stage 완료 후.
  ERD 초안은 별도 아티팩트(2026-09-01)에 예습으로 존재
- **공개시점 대장 + `dataset_profile`** (소스별 공표 시점 지식·권장 랙) — **일일 증분·equity
  트랙 산출물.** stage 는 사실 날짜만 실으므로 이 지식을 필요로 하지 않는다
- 일일 증분 상세(더티 파티션 판정·워터마크) — 별도 설계. 단 §2 에 실측 제약을 기록해 둔다
- `ws_run_log` — 운영 요약 로그. 61테이블 중 유일한 stage 미편입 (§4 전수 대조표)
- **정정 체인 복원** — `[기재정정]` 공시의 원본 링크는 원장에 없다(ZIP 본문에만 존재,
  실측: disclosure 컬럼 전수 확인). 문서층 L1 의 몫
- **KRX 2026-08-21~ 공백** — 상장·폐지가 이 구간에서 어떤 원장으로도 재구성 불가(실측:
  KIS 폐지 max 08-18 · DART 상장공시 0건). 백필은 일일 증분 때 소급(09-02 사용자 결정).
  그전까지 `universe_asof` 는 이 구간에서 `coverage_gap` 예외를 던진다 (§8)

## 1. 층 계약과 경계 원리

허용 변환 4종(rename·cast·단위 스케일·categorize), 조인·집계·파생·정책 금지 — SPEC §1.

**경계 판정 원리**: **"출력 행은 원장 한 행의 함수다 (1:1)."** 예외는 다섯뿐, 각각 전용 게이트:

| 예외 | 내용 | 게이트 |
|---|---|---|
| (a) 수직결합 | 동일 스키마 UNION (stk+ksq 등) + `_src` 부여. 실측: stk/ksq bydd 17컬럼 완전 동일 | G1 팬아웃 항 |
| (b) 결정론적 접기 | **payload 투영 동일 행만** 접는다 (§5-중복) | G1 dedup 항 · G6 |
| (c) blob 언네스트 | ws_raw JSON·c1010001 HTML → N행. **zlib 해제 → json.loads ×2 (이중 인코딩)** 포함 | G8 파싱 등식 |
| (d) 불변 참조표 룩업 | `corp_code↔ticker`(dart_corp_map — 시각 컬럼 0, 전량 재생성) · **`stg_rcept_dt_map`(rcept_no→rcept_dt, disclosure 유래 — 키당 값 불변)** | G0 선언 대조 |
| (e) 동일 좌표 다중 blob 결합 | cF5001+cF5002 **outer join** (실측: 겹침 32,867 좌표 전수에서 select_item ≡ avg, 불일치 0). UNION 금지 — 같은 키 2행이 됨 | G8 좌표 합집합 등식 |

`price_matches_krx` 컬럼(교차 정본 딱지)은 equity 몫. `price_basis` 는 stage — 단 **컬럼 단위**(§4-KIS).

## 2. 저장·커밋·빌드

- **Parquet** (결정 ①). 원장 SQLite 유지. `.venv` 에 pyarrow·duckdb 설치가 선행 조건(승인 기수령)
- **빌드 입력 동결**: 빌드 직전 `VACUUM INTO` 로 원장 스냅샷 사본을 뜨고 그 위에서만 빌드·게이트.
  근거(실측): 시각 술어(`W_now`)는 스냅샷이 못 된다 — 키움은 런 시작 시각을 전 행에 박아
  늦게 쓴 행이 이른 시각표를 달고, KRX 는 11.3M 행 전부가 3시간 창 안. wisereport.db 는
  WAL 전환(18:00 크론과 읽기 충돌 방지)
- **버전 디렉토리 + manifest 포인터**: `stg_x/v=<빌드ID>/year=YYYY/part.parquet`.
  리더는 `MANIFEST.json`(최상위 인덱스) 경유 필수 — **맨 glob 금지 계약**
- **커밋 입자 = 테이블**: 섀도 버전에 전 파티션 생성 → 게이트 전량 통과 → `MANIFEST.json`
  을 `os.replace` 원자 교체(포인터 1회). 게이트 실패 = 새 버전 폐기, 구 버전 무손.
  tmp 는 `data/stage/_tmp/`(테이블 밖) · 구버전 GC keep=3 · 동시 실행 금지 `flock -n`
- **메타 = 파티션당 1파일**(`_meta.json`): 행수·게이트 결과·rules 버전·`src_bytes`·
  `src_mtime`·`n_src`·스냅샷 ID·`coverage_from`
- **`partition_expr` 테이블별 필수 선언 — 클래스 3종** (실측: 관측일 축이 없는 테이블 실재):
  `date_axis`(내용일 연도 — 가격·수급 등) / `receipt_axis`(rcept 연도 — DART. `bsns_year`
  금지: 접수지연 max 2,875일) / `whole`(단일 파티션 — 참조표·소형·관리)
- **일일 증분은 별도 설계**로 이관하되 실측 제약을 여기 기록한다: ⓐ 워터마크 해상도 —
  kiwoom `collected_at` distinct 1(런 스탬프)·krx 전량 3시간 창 → 시각 워터마크 증분 불성립,
  수집 원장(ingest_log/shard) 기반 더티 판정으로 설계할 것. ⓑ 수집기 2줄 수정
  (kiwoom 행별 stamp·krx tz)은 증분 착수 전 필수 — **풀 빌드에는 스냅샷 동결로 충분**
- **★백필 진행 중 테이블**(doc_index — 문서 수집 ~09-03)은 회귀 고정 제외, 완주 후 재측정.
  `dart_fin_raw` 도 ★ — SPEC 의 "08-27 고정"은 실측 반증(collected_at max 08-30,
  행수 15,375,024). DART 계열 회귀 수치는 빌드 시점에 재측정해 고정한다

## 3. 공통 골격

| 컬럼 | 내용 |
|---|---|
| `ticker` / `date` | 정규 키. date = 내용일(그 행이 어느 날 이야기인가), ISO DATE |
| **`available_date`** | **사실 날짜만** [결정 ⑥]: 원장에 공개일 사실 컬럼이 실재하면 그것(게시일·결제일·수집일), 없으면 내용일. **랙·보수 버퍼를 더하지 않는다 — 판단은 엔진 설정** (`WHERE available_date <= :asof - :lag`). §6 |
| `available_basis` | `measured`(원장 공개일 실재) / `derived`(참조표 유도) / `default`(내용일 대용) |
| **`observed_date`** | 원장 `collected_at` 의 KST 날짜 — **전 테이블 필수.** 재수집 판본의 PIT 선택 축 (equity 가 (available_date, observed_date) 로 판본 선택. 실측: 이것 없이는 재수집 두 판본이 동일 좌표가 되어 선택 불능) |
| `_src` | 원장 출처 (UNION 구분: stk/ksq, v1 등). **시장이전 22종목 실측 — (티커,일자) 단위 속성이지 티커 속성이 아님** (DISTINCT 매핑 조인 시 팬아웃 주의) |
| `_src_flag` | `ok` / `partial` / `parse_failed` — `partial` 은 **캐스팅을 시도했다 실패한 행만** |
| `_cast_fail_cols` | `list<string>` — cast_failed 컬럼명. 전 행 non-null(정상은 빈 리스트 — NULL 혼용 금지) |
| **`miss_kind`** | 컬럼 단위 아님 — 값 없는 셀의 원인 분류를 캐스팅 로직이 기록: `ledger_blank`(원장 `''`) / `ledger_dash`(`'-'`) / `ledger_zero`(테이블별 재판정된 `'0'`) / `ledger_null` / `cast_failed`. 근거(실측): `bfefrmtrm_amount` 는 원장 NULL 988만+빈값 48.6만 = 64.3%가 정상 결측 — 이걸 실패로 세면 G2 임계가 무의미해짐 |

- **`write_mode` — 원장 테이블별 필수 선언**: `append_only`(KIS·DART — row_hash PK) /
  `upsert`(KRX·키움 — `INSERT OR REPLACE`, 재수집이 과거를 덮음) / `first_write_wins`
  (v3 미러·master_daily — `INSERT OR IGNORE`). 전 판본 보존 게이트(G6)는 append_only
  소스에만 유효 — 나머지는 `_meta.json` 에 `version_loss_upstream=true` 기록
- **temporality 강제 — 2갈래** (실측 교정): ⓐ 재조회로 덮어써지는 단일 상태 = `_current`
  접미사 강제(`corp_cls_current`, kis_stock_info 의 상태 컬럼 전부, ws_coverage) —
  실측: corp_cls='E' 의 26%가 과거 상장사. ⓑ `snap_date`/`fetched_date` 축으로 누적되는
  스냅샷 = `_current` **금지**, `coverage_from` 필수(ka10099 — 하루만 지나면 이름이 거짓이 됨)
- 시각류는 KST 날짜로 변환해 DATE 로만 (결정 ③′ — 시각 해상도 폐기)

## 4. 테이블 카탈로그 — 실물 61 전수 대조 (2차 실측 정정 반영)

> **원장 61 = stage 편입 60 + 제외 1(ws_run_log).** 절별: KRX 8→5 · 키움 6→6 · KIS 7→7 ·
> DART 32→30(+참조표 1) · WISE 8→12. G0 가 이 표와 실물의 diff 를 매 빌드 검사하며,
> **rules 의 모든 컬럼 참조는 `pragma_table_info` 선검증 + `expected_len` 검증** (큰따옴표
> 폴백·실재-오답 컬럼 함정 방어 — §11 도구 교훈).

### KRX (실물 8 → stage 5)
| stage | 원장 | 키 | 파티션 | 비고 |
|---|---|---|---|---|
| `stg_price_daily` | stk_bydd + ksq_bydd (UNION — 17컬럼 완전 동일 실측) | ISU_CD,BAS_DD | year | 티커=`ISU_CD`(len 6 전수 — 6"문자", `0001A0` 실재: zfill/int 금지) · `market`=MKT_NM 승격(_src 와 동치 — G7 불변식) · **O/H/L=0→NULL 술어: 원문 문자열 `='0'`, 3컬럼 독립, 거래량 무결합**(실측: 패턴 000/111 뿐 — 동시성을 G7 불변식으로) · OHL=0∧거래량>0 = **125행**(stk 96+ksq 29, 회귀 고정. ETF 2 는 별도 — v2 의 127 은 합산 오기) · `SECT_TP_NM`: **stk 100% 빈값 / ksq 만 유효**(관리종목 160,632 실측) → 싣되 `sect_available` 불린 병기, 관리종목 필터 재료로 단독 사용 금지 |
| `stg_etf_price_daily` | etf_bydd | ISU_CD,BAS_DD | year | 불변식 상이 분리. OHL=0∧거래량>0 = 2행 |
| `stg_index_daily` | kospi_dd + kosdaq_dd (UNION) | **IDX_CLSS,IDX_NM**,BAS_DD | year | **컬럼 실명 `IDX_NM`(`index_name` 은 유령 — v2 오기, 미조사 테이블에서 발생)** · `IDX_CLSS` 키 필수 — 업종지수명 20개가 양시장 중복(실측 81,880쌍 충돌) · 거래일 4,094 정확 일치 · 일당 KOSPI 47.8 + KOSDAQ 37.1 |
| `stg_listing_daily` | stk_isu + ksq_isu (UNION) | ISU_SRT_CD,bas_dd_req | year | 티커=`ISU_SRT_CD`(len 6). **`ISU_CD` 는 12자리 ISIN — 실재하는 오답 컬럼, 티커 사용 절대 금지**(expected_len 게이트) · `PARVAL`→`par_value_krw`+`par_value_kind`(비수치 실측 97,996 — v2 의 105,570 정정) · 스냅샷은 **당일 상태**(실측: 삼성 20180504 이 이미 분할 후 — v2 의 "전일 확정치" 반증). 공표 시점 지식(T+1 08:00)은 카탈로그로 · 재상장 실증 2종 → universe_asof 복수 구간 |
| `stg_ingest_krx` | krx.ingest_log | — | whole | 관리 — v2 의 고아 참조("아래") 해소, 정식 편입 |

### 키움 (실물 6 → stage 6)
| stage | 원장 | 키 | 특이 |
|---|---|---|---|
| `stg_flow_daily_kiwoom` | ka10060 | ticker,dt / year | `cur_prc` **abs** · 투자자 13컬럼 keep+×1e6 · `acc_trde_prica`→`volume_shr`(오표기 — 실측 KRX 거래량과 99.9864% 일치) |
| `stg_short_daily_kiwoom` | ka10014 | ticker,dt / year | `close_pric` **abs** · `ovr_shrts_qty`+`ovr_shrts_qty_valid`(77.7% 리셋) · 격자 빈 셀 3,090,809 = 행 생략 실증 — 3분류 재료 |
| `stg_foreign_daily` | ka10008 | ticker,dt / year | `close_pric` **abs** · `poss_stkcnt` keep(음수 3 회귀) · `wght` **strip_plus**(SPEC §2-3 — v2 의 keep 은 인용 오류. 전수 실측: 비영 7,297,410행 — survey 표본치 [0,0] 은 오류, §11-N5) |
| `stg_lending_daily` | ka20068 | ticker,dt / year | |
| `stg_master_daily` | ka10099 | snap_date,code | **snap_date 축 누적 스냅샷 — `_current` 금지, `coverage_from=2026-09-01`** (§3-temporality ⓑ). auditInfo(관리·정지 상태의 유일한 시계열 재료)·state 분해 |
| `stg_shards_kiwoom` | ingest_shard | 원구조 | shard 단위. **커버리지 한계: collected_at 08-23~24 2일뿐** — 3분류 재료로 쓸 때 명시 |

### KIS (실물 7 → stage 7)
| stage | 원장 | 자연키 (실측) | 특이 |
|---|---|---|---|
| `stg_flow_split_daily` | kis_investor_flow | req_ticker,stck_bsop_date (중복 52,347 — 전건 2행) | 수정주가 포함 — 재수집 시 값 변동, observed_date 가 판본 축 |
| `stg_short_daily_kis` | kis_short_sale | req_ticker,stck_bsop_date (중복 0) | `acml_*` 6컬럼 `acml_valid`(창 첫 행 리셋) |
| `stg_loan_daily_kis` | kis_loan_trans | req_ticker,bsop_date (중복 0) | `rmnd_stcn` 음수 2,691 keep(회귀) |
| `stg_credit_daily` | kis_credit_balance | req_ticker,deal_date (중복 566,795) | date=deal_date · `*_amt` 8컬럼 unit=unknown — `_krw` 금지 · **가격축 컬럼 단위 실측**: `stck_prpr`=수정종가(÷50 검증) / `stck_oprc·hgpr·lwpr`=**원주가(KRX 와 완전 일치, 5일 전수)** → `price_basis_close='adjusted_asof_collect'` / `price_basis_ohl='raw'` — **행 단위 라벨 금지**(v2 정정). OHL 은 교차검증·조정계수 재료로 사용 가능. 크로스 63.7% 는 유일한 수정주가 컬럼(prpr)만 대조한 결과였음 · 중복 566,795 = **req_d2 만 상이한 순수 재수집**(payload 접기 실증, §5) · `stck_prpr='0'` 561행 |
| `stg_delisted_master` | kis_stock_info | req_ticker | pdno 12→6자리 · **상태 컬럼 전부 `_current` 강제**(admn_item_yn·tr_stop_yn·kospi200_item_yn 등 — 652행 전건 08-26 단일 조회 실측: 폐지 종목은 동결값·생존 종목은 현재값 혼재. kospi200 현재값의 과거 필터 사용 = look-ahead+생존편향) |
| `stg_calls_kis` / `stg_units_kis` | kis_call_log / kis_ingest_log | — | 3분류 재료. 시계 컬럼 실명 `ts` |

### DART (실물 32 → stage 30 + 참조표 1)
| stage | 원장 | 비고 |
|---|---|---|
| **`stg_rcept_dt_map`** | dart_disclosure 유래 (예외 d) | **(rcept_no → rcept_dt) 참조표.** 근거: `rcept_dt` 는 32테이블 중 **5개에만 실재**(disclosure·elestock±v1·majorstock±v1) — 나머지 21개 내용 테이블은 rcept_no 뿐. 접수번호 앞 8자리 대용은 gap 실측 −716~+359일이라 불가. 표본 9/9 전건 disclosure 에서 조인 성공. 미스 시 rcept_no[:8], basis=default. 키당 값 불변(append 만 됨) |
| `stg_fin` | dart_fin_raw | **자연키 8컬럼 실측 확정: (req_corp_code, req_bsns_year, req_reprt_code, req_fs_div, sj_div, account_id, account_detail, ord)** — 15,375,024행 위반 0. account_detail 제외 시 878,156 위반·ord 제외 시 418,902(자본변동표 465만 행 오접힘 위험) · **키는 요청축**(응답축 bsns_year 불일치 120행 — `bsns_year_mismatch` 불린) · `account_detail` 원문 보존(키 구성원 — 변형 금지)+`account_detail_path` list 병기 · 금액 6컬럼 **wide 유지**(1:1 원리 — long 은 1→6 팬아웃) · 기간 라벨 4컬럼(`thstrm_nm` 등 841종)은 §5 문자열 정규화만, 날짜 파싱 금지(비12월 105사) · `account_id='-표준계정코드 미사용-'` 2,741,192행(17.8%) → `account_std=false` 불린, 표준계정 조인 금지 · Decimal(38,4)(실측: 총자릿수 max 18·소수 ≤2 — thstrm 기준, 나머지 5컬럼은 survey v2) · 파티션=rcept 연도(12파티션 합=행수 검증) · 정기 6종 `stlm_dt`=결산기준일 — **가용일 유도 금지**(KIS `stlm_date`=결제일과 이름 충돌 경고) |
| `stg_dividend`~`stg_audit` 6종 | 정기 보조원장 6종 | se long 유지 |
| `stg_holder_elestock` / `_majorstock` | elestock(+v1 UNION) / majorstock(+v1) | v1 전용 194행 구제 |
| `stg_disclosure` | dart_disclosure | rm 분해 · `corp_cls_current`·`corp_name_current` · rcept_no 중복 **491**(v2 의 106 은 구수치 — ★원장 성장분) = 페이지 경계, payload 접기 대상 · `is_correction` 카테고라이즈(`[...정정]` 접두 — 정정 577,215건=16.8%) · rcept_dt 포맷 8자리(elestock/majorstock 은 10자리 — 파서 테이블별 선언) |
| `stg_company` | dart_company | `acc_mt`(결산월) · `_current` 계열 · **rcept_no·rcept_dt 없음 — available_date 비부여** |
| `stg_corp_map` | dart_corp_map | 예외 (d). 시각 컬럼 0 — 증분 제외·전량 재생성 |
| `stg_event_*` 15종 | DS005 15종 | 한글 날짜 단일 포맷(`YYYY년 MM월 DD일` — 점표기·기간표기 0건 실측) · `'-'` 결측 마커 실재(cvbd pymd 229행) · **만기일 2053년 실재 — G7 내용일 축** · 키 rcept_no(유일성 전수 ✓) |
| `stg_doc_index` | doc_store | 메타 인덱스(원장에 blob 없음 — ZIP 은 파일시스템). rcept_no 유일 · ★수집 중 |
| `stg_calls_dart` / `stg_units_dart` | dart_call_log / ingest_log | 시계 컬럼 실명 `ts` — 3분류 재료 |

### WISE (실물 8 → stage 12)
> **ep 실물 어휘 6종 전부**: `c1010001`·`c1050001_data`·`cF3002`·`cF4002`·`cF5001`·`cF5002`.
> **T2Y/T2Q/T4 는 ep 가 아니라 `c1050001_data` 의 `pkey` 값**이다 (v2 는 유령 ep 로 오기 —
> 그대로 구현하면 컨센서스 3테이블의 원장이 존재하지 않아 G0 즉사).

| stage | 원장 (ws_raw) | 키 · 파싱 규칙 |
|---|---|---|
| `stg_consensus_monthly` | ep=cF5001 + cF5002 (예외 e — outer join) | (ticker,fetched_date,target_period,**metric**,obs_month) — metric∈{eps,revenue} 없으면 chart1/chart2 가 같은 키 충돌(실측) · **날짜 라벨로 join — 인덱스 zip 금지**(축 길이 불일치 1,740/2,430=72% 실측: EPS·매출이 한 달 어긋남) · 말미 관측점 중복 71% → 값 동일 검증 후 dedup+카운터 · `obs_label` 원문 보존(비월말 라벨 실재) · 무커버 프로브(cF5001 52.4%)는 **행 생성**(avg=NULL, close_price 실값 보존) — cF5002 빈 배열은 0행. G8 계상 규칙 명문 · zlib(`789C`)+이중 JSON — **양쪽 loads 에 parse_float=Decimal** · 내부 키명 ep 별 상이(select_item vs avg) — 파서 분기 |
| `stg_consensus_annual` / `_quarterly` | ep=c1050001_data, pkey='T2Y' / 'T2Q' | ticker,fetched_date,period |
| `stg_consensus_matrix` | ep=c1050001_data, pkey GLOB 'T4:*' | ticker,fetched_date,target_period,acc_cd,lookback · `target_period` YYYYMM 문자열 보존(비12월 202605·202903 실재 — 연도 절삭 금지) · cmp_cd 비숫자 19종(`0004Y0` 등) — TEXT 유지 |
| (pkey='') | c1050001_data 목록 호출 | 파싱 입력으로 소비 — 별도 테이블 없음 |
| `stg_analyst_summary` | c1010001 HTML | ticker,fetched_date — 추정기관수. G8 필수 |
| `stg_fin_wise` | cF3002/4002 | ACCODE 기준 · Decimal(38,6) |
| `stg_v3_revision_daily` 등 v3 4종 | v3_* | 정식 stage(결정 ⑤). 규칙 분해는 §6 |
| `stg_wise_coverage` | ws_coverage | **이력 아님 — 종목당 1행 현재 상태**(실측 2,566행=2,566종목) → `status_current`·`checked_date_current`. 3분류 재료로 쓰려면 수집기를 append 이력으로 바꿔야 — stage 밖 이슈로 등록 |
| `stg_calls_wise` | ws_call_log | 시계 컬럼 실명 `ts` |

**v3 편입 계약**(결정 ⑤ 유지): 병합 시점 동결 사본 + 이후 sync 는 `INSERT OR IGNORE`
(write_mode=first_write_wins — 상류 정정은 원장 단계에서 유실됨을 명기). `base_date` 는
내용 기준일 라벨(실측: 비NULL 61,160행 전수에서 collected=base+1영업일 100%).
**annual·compare 는 sync_date 단일값(2026-09-01)으로 시작 — 이후 일일 sync 가 이력을
쌓는다. 09-02 이전 백테스트 기여 0** (WISE 자체 수집 6종도 동일 — fetched_date 첫날 하나).

## 5. 값 규칙

- **식별자**: 정규 `ticker` 6문자(숫자 캐스팅·zfill 금지). bydd=`ISU_CD`(len 6),
  isu_base=`ISU_SRT_CD`(len 6, **`ISU_CD` 는 12자 ISIN 함정**), KIS pdno 뒤 6자.
  rules 에 `expected_len` 필수 — G0 이 길이 분포 검증
- **중복 (결정 ④′ — 리뷰 5기 수렴으로 투영 재정의)**: 접기 판정 = **payload 투영 동일**.
  payload = 원장 컬럼 − {`req_*`, `row_hash`, `dup_seq`, `collected_at`} (테이블별 rules 선언.
  단 응답 의미를 바꾸는 요청 파라미터 — 수정주가 플래그류 — 는 payload 에 포함).
  근거(실측): kis_credit 중복 566,795 전형 표본이 전 데이터 컬럼 동일·req_d2 만 상이 —
  "바이트 동일" 기준으로는 접힘 0건이라 ④ 의 목적 자체가 무효였음. payload 상이 =
  전 행 보존, 판본 좌표는 (available_date, observed_date) — **`is_latest`·`observed_seq`
  는 stage 에 두지 않는다**(윈도우 연산 = §1 원리 위반 + 정렬 재료 부재로 비결정 실측)
- **문자열 정규화 — 텍스트 컬럼만**: NFKC → 개행·탭 제거 → 연속공백 축약 → strip + 태그 제거.
  **식별자·조인 키(account_id·corp_code·ticker·ISU_*)와 숫자 컬럼에는 적용 금지** —
  실측: 숫자 컬럼 1,607만 행에서 전각·유니코드 마이너스 0건(방어는 게이트로 —
  비ASCII 검출 시 실패), 반면 account_id 는 17.8%가 한글 센티널이라 무차별 NFKC 는 키 변형 위험
- **숫자**: 콤마 제거 → 부호 정책 → **Decimal**. 컬럼별 (p,s) 필수 — **survey v2 가 유도
  필드(max_int_digits·max_scale·has_comma·has_sign)를 제공하는 것이 선행 조건**(현 조사
  JSON 엔 해당 필드 없음 + num_range 는 float64 유래라 2^53 초과 구간 자체 손실 — 실측).
  오버플로 = 게이트 즉사. JSON 은 `parse_float=Decimal, parse_int=Decimal` — **이중 인코딩
  블록은 안쪽 loads 에도** (§4-WISE)
- **부호 정책 3값** {abs, strip_plus, keep} — (table,column) 키. abs 실측 3컬럼(§4 키움).
  abs 컬럼은 원부호 `*_dir` 보존. 음수 회귀 고정: poss_stkcnt 3 · rmnd_stcn 2,691 ·
  whol_loan_gvrt 539 · **whol_stln_gvrt 1 (신규 고정)**
- **비율**: `_pct` / `_ratio` 접미사. 실측 범위 주의: whol_loan_gvrt −292~+333 (0~100 가정 금지)
- **결측**: 값 NULL + `miss_kind` (§3). 마커 실측: `''`(DART 5.8%) · `'-'`(tesstk 80.8%,
  DS005 날짜 컬럼에도 실재) · `'0'`(KIS 만 결측 표현 — 테이블별 재판정) · 집계행
  (`'합계'`·`'계'`·`'총계'`)은 `row_kind='aggregate'` 카테고라이즈(실패 아님)
- **신뢰 불가 값**: "싣는다 + 기계 판독 플래그" 단일 처방 (각주·미적재 금지)

## 6. available_date — 사실 날짜만 [결정 ⑥·⑦, 2026-09-02 사용자 확정]

> v2 의 클래스표("+1거래일"·"T+2" 등)는 **판단을 사실인 척 저장**하는 설계였고, 입자
> 오류(동일 종가가 테이블 따라 D/D+1/D+2 — 실측)까지 낳았다. v2.1: **stage 는 사실만.**
> 공개 시점 지식(수급 익일 공표 등)은 공개시점 대장→`dataset_profile`(equity)로,
> 랙 판단은 엔진 설정으로. 이로써 "+N거래일" 계산이 소멸 — 캘린더 순환 의존도 함께 해소.

| 원장의 공개일 사실 | available_date | basis | 해당 |
|---|---|---|---|
| 없음 → 내용일 대용 | = `date` (내용일 그대로. 가격은 당일 실시간 관측 실증 — 교차 100%) | default (가격류는 measured 급 실증이나 라벨 통일) | 가격·지수·ETF·마스터·수급·외인·대차·공매도(거래 데이터 — **v2 의 "잔고 T+2" 클래스는 삭제: 양 테이블 전 컬럼 실측 결과 잔고 컬럼 0개**, 진짜 잔고는 SPEC §4 취득 불가) |
| 결제일 실재 | = `stlm_date` (실측 매매일+2~12일, 행별 상이 — 위반 0) | measured | stg_credit |
| 게시일 — 참조표 유도 | = `stg_rcept_dt_map[rcept_no]` (그대로 — max() 보정 등 판단 금지). 미스 시 rcept_no[:8] | derived / default | DART 내용 21테이블 + rcept_dt 보유 5테이블(직접) |
| 수집일 실재 | = `fetched_date` (WISE — 18:00 수집이라는 지식은 카탈로그로) / `collected_date`(v3 revision — NULL 1,390행은 base_date, basis=default + `coverage_degraded` 불린: **실측 시리즈 최초 4거래일 100% 집중, 04-06·07 은 커버 15%·11% 붕괴**) / `snapshot_date`(v3 opinions) / `sync_date`(v3 annual·compare) | measured | WISE 6종 · v3 4종(4행 분해 — v2 의 "4종 일괄" 은 3종에 collected_date 부재로 불성립 실측) |
| 비부여 | — | — | calls·units·shards·doc_index·coverage·corp_map·company |

## 7. 정정·판본

- stage 는 **전 행 보존**, 판본 좌표 = (available_date, observed_date). 선택(pit/latest)은
  equity — `is_latest` 없음 (§5)
- `vintage_kind`: `original` / `restated_unknown`(fnlttSinglAcntAll 전량 — **재무의 원본
  판본은 원리적으로 부재**: 다중 rcept_no 유닛 0 실측 + 표본 3/3 이 [기재정정]. §7 은
  재무에서 무동작임을 명기) / `v3_mirror`
- **소급 재작성 소스(WISE 13개월 창)의 PIT 판본 = 같은 (ticker,obs) 점의 `min(fetched_date)`
  행** — equity 규칙으로 지식 이관. 최신 행 선택은 재작성 컨센서스의 과거 주입 = 리비전
  팩터 무효화(방향이 곧 알파라서)
- 정정 존재의 기계 판독: `stg_disclosure.is_correction` (§4). 체인 복원은 범위 밖 (§0)

## 8. 마스터 사용 규칙

과거=KRX 스냅샷(pit) · 현재=키움 ka10099 · 폐지일=KIS(정본). `universe_asof(date)` 는
재적 "복수 구간"(재상장 실증 2종) + **2026-08-21 ≤ date < 백필일 구간은 `coverage_gap`
예외**(조용한 08-20 연장 금지 — §0). **확정 한계 2건**(SPEC §4 등록): ⓐ 관리종목·거래정지
과거 시계열 전 카탈로그 부재(SECT_TP_NM stk 전건 빈값 실측 — §11 미결 해소. 09-01부터
ka10099 로 적립) ⓑ 08-21~31 상장·폐지 재구성 불가.

## 9. 게이트 — 10종, 하나라도 걸리면 그 테이블 버전 폐기

| # | 게이트 | 내용 |
|---|---|---|
| G0 | 선언 대조 | 원장 실물 vs rules diff=0 + **pragma 컬럼 실재 + expected_len 길이 분포** |
| G1 | 행수 등식 | `stage = Σ(원장ᵢ×fanoutᵢ) − dedup(payload동일) − reject`. 각 항 독립 산출, 실패 시 reject parquet+키 샘플 |
| G2 | 손실 임계 | **`miss_kind='cast_failed'` 만** 카운트 (원장 정상 결측 제외 — 64% 오염 방지). 임계 = survey v2 실측 |
| G3 | 불변식 | stage 산출물에 duckdb DECIMAL 재작성 실행 (원장 문법·REAL 금지) |
| G4 | 골든 픽스처 | 불변형/시변형 분리. 정정 반영: 회귀 **125**(price)+**2**(etf) · 정지행 125(OHL=0∧거래량>0 — DEFECT-001 계열) · 원주가 2,650,000 · abs 금지 3행 · **unit≠1 선언 전 컬럼에 픽스처 강제**(×1e6 오적용 = SPEC 최대 결함 클래스의 유일 방어) |
| G5 | 회귀 Δ등식 | `Δstage = Δn_src×fanout − Δdedup − Δreject` — 설명 안 되는 증감 실패 (v2 의 "증가 허용"은 payload 접기 하에서 탐지력 상실 — 정정) · ★테이블 제외 |
| G6 | 판본 보존 | (자연키, observed_date) 유일성 위반 0 + **자연키 중복 중 payload 동일 비율 기록**(임계 초과 = 재수집 잡음 과다) — **append_only 소스에만** (§3 write_mode) |
| G7 | 범위 | **관측일 축**(BAS_DD·dt·deal_date·rcept) 연도 [2000, 현재+1] / **내용일 축**(만기·상환일 — pymd·*_edd) 연도 [1990, 현재+40] (실측: CB 만기 2053 정상값 — v2 단일 축은 테이블 통째 오폐기) · 부호 교차 · 비율 범위 |
| G8 | 파싱 등식 | blob=parse_log ∧ Σn_rows=stage — **ep 별 분리 산출** + 조인 테이블은 좌표 합집합 등식 (예외 e) · coverage 급락 감지(v3 일별 행수 < 직전 중앙값 50% 플래그) |
| G9 | **교차 소스 회귀** (신설) | 실측 확정 일치쌍의 재검: 종가 KRX=키움 100%(2,864,871행) · 거래량 99.9864% · 시총 항등식 — 빌드마다 재실행, 저하 = 실패 |

## 10. 빌더

```
stage/rules.py    선언 — 테이블별 {소스, 자연키, fanout, partition_class, dedup=payload_cols,
                  write_mode, 컬럼맵[원명→정규명, expected_len, (p,s), sign_policy, unit,
                  temporality, miss 마커]}
stage/build.py    duckdb 주엔진: ATTACH(READ_ONLY, 스냅샷 사본) → CAST DECIMAL → COPY TO parquet
                  memory_limit 6GB · threads 3
                  파이썬 보조 단계(듀얼 불가 변환): 한글날짜 · 파이프분해 · zlib+이중 JSON ·
                  **NFKC = pyarrow pc.utf8_normalize 벡터화** (duckdb 에 NFKC 부재 실측 —
                  v2 "인엔진 고정" 문구 폐기. 벤치: 벡터화 48초 vs 파이썬 루프 ~10분)
stage/gates.py    G0~G9 · stg_parse_log · 원장 인덱스 부재(PK 오토인덱스뿐 실측) → 전량
                  스캔 전제로 벤치 산정
```
착수 전 1개 연도 파티션 벤치(경과·최대 RSS) — 추정과 2배 이상 벌어지면 재검토.
슬라이스: S1 `stg_price_daily` → S2 `stg_fin` → S3 `stg_consensus_monthly` → 잔여 확장.

**착수 선행 조건 체크리스트**:
1. `.venv` pyarrow·duckdb 설치 (승인 기수령)
2. survey v2 — (p,s) 유도 필드 추가 + **미조사 16테이블 커버**(지수 2종 포함 — `index_name`
   사고의 진원. "조사 안 된 테이블·컬럼 0개"가 완료 기준)
3. `VACUUM INTO` 스냅샷 절차 + wisereport.db WAL 전환
4. `dart_fin_raw` 등 ★테이블 회귀 수치 빌드 시점 재측정
5. (일일 증분 전까지만) 수집기 2줄 수정 유예 가능 — 풀 빌드는 스냅샷으로 충분

## 11. 결정 기록

| # | 결정 | 일자 |
|---|---|---|
| ① | Parquet + pyarrow·duckdb | 09-01 승인 |
| ②′ | 결측 = NULL + `_src_flag` + `_cast_fail_cols` + **`miss_kind`** (v2.1 확장) | 09-01 / 09-02 |
| ③′ | available_at → available_date (날짜 단위) | 09-01 사용자 확정 |
| ④′ | 중복 = **payload 투영 동일만 접기** · 값 상이 전 행 보존 · **is_latest 는 stage 서 제거** (④ 의 목적 — 소급 주입 방지·전행 보존 — 을 지키는 투영 재정의. 리뷰 5기 수렴) | 09-01 승인 / 09-02 개정 |
| ⑤ | v3 미러 = 정식 stage (팩터·백테스트 입력) — 4종 규칙 분해로 이행 | 09-01 사용자 확정 |
| **⑥** | **available_date = 사실 날짜만. 랙·보수 버퍼 금지 — 판단은 엔진 설정, 공개시점 지식은 카탈로그(dataset_profile — equity·증분 트랙)** | **09-02 사용자 확정** |
| **⑦** | **가격 = 내용일 당일** ("당일치 바로 나오니까" — 실시간 관측 + 교차 100% 실증) | **09-02 사용자 확정** |
| **⑧** | KRX 08-21~ 백필 보류 — 일일 증분 때 소급, 그전엔 coverage_gap 예외 | **09-02 사용자 결정** |
| — | stage 1회 풀 빌드 → 증분은 별도 설계 · equity 는 stage 후 | 09-01 |

**미결**: 컬럼별 (p,s) 최종값(survey v2 대기) · 수급·공매도의 실제 공표 시점(관행 D+1 —
프로브 실측은 일일 증분 트랙, 공개시점 대장에서) · cF5003 어닝서프라이즈 엔드포인트.

**크로스 대조 최종 실측 (09-01)**: 종가 KRX=키움 2,864,871행 100.0% · 거래량 99.9864% ·
공매도 키움∩KIS 겹침 0(상보 유니버스) · 상장주식수 97.45%(차이 24건 전부 8/20~9/1 기업행위).

**도구 교훈 (3건 — rules·gates 는 실행 전 컬럼 존재+길이 검증 필수)**:
① SQLite 큰따옴표 폴백 — 없는 컬럼명이 문자열 상수가 됨 (ISU_SRT_CD·close_pric 2회)
② **실재하는 오답 컬럼** — isu_base 의 `ISU_CD` 는 12자 ISIN: 존재 검증을 통과하는 더 나쁜 변종
③ **미조사 지대의 유령** — `index_name` 은 전수조사가 안 덮은 테이블에서 났다. 조사 커버리지 = 설계 신뢰의 상한
