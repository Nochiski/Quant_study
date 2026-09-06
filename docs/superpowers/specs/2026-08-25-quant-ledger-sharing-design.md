# quant-ledger 원장 공유 설계

- **작성일**: 2026-08-25
- **상태**: Phase 1(리빌드 파이프라인) 구현 완료 · Phase 2~3(공개 경로·접근) 대기
- **대상**: 카엘 서버(`kael-mini-server`)의 `quant-ledger` 원장을 협업자(상목)에게 읽기 전용으로 공유

## 배경

카엘 서버에 축적된 국내증시 원장 데이터를 협업자와 공유해야 한다. 협업자의 용도는
**읽기 전용 분석·백테스트**이며, 원장 수집·정정은 카엘 서버가 계속 전담한다.

## 요구사항과 제약

| 항목 | 결정 |
|---|---|
| 협업자 용도 | 읽기 전용 분석·백테스트 (쓰기 없음) |
| 협업자 수 | 1명 |
| 원장 | SQLite 유지. **수집기 코드 변경 0** |
| 제공 형태 | typed 변환본만. 원형(raw) TEXT는 제공하지 않음 |
| 신규 개방 포트 | 없음 |

## 공유 범위

`~/quant-ledger/data/raw/` 의 **13개 테이블**. 실측(2026-08-25) 기준.

| DB | 테이블 | 행수 | Parquet |
|---|---|---:|---:|
| krx.db | `krx_stk_bydd_trd` (KOSPI 일별시세) | 3,771,174 | 84.3MB |
| | `krx_ksq_bydd_trd` (KOSDAQ 일별시세) | 5,430,342 | 121.9MB |
| | `krx_stk_isu_base_info` | 3,771,174 | 4.1MB |
| | `krx_ksq_isu_base_info` | 5,430,342 | 10.4MB |
| | `krx_etf_bydd_trd` | 1,688,735 | 64.8MB |
| | `krx_kospi_dd_trd` (지수) | 195,711 | 5.5MB |
| | `krx_kosdaq_dd_trd` (지수) | 152,110 | 4.4MB |
| | `ingest_log` | 30,373 | 0.1MB |
| kiwoom.db | `ka10008_foreign_holdings` | 7,682,844 | 127.6MB |
| | `ka10060_investor_flows` | 7,621,338 | 122.4MB |
| | `ka20068_lending_balance` | 6,988,296 | 50.6MB |
| | `ka10014_short_selling` | 3,971,630 | 77.5MB |
| | `ingest_shard` | 10,420 | 0.1MB |
| **합계** | **13개** | **46,744,489** | **673.7MB** |

### 제외 대상

- **`dart.db`** — **본 백필 미착수.** `dart_corp_map` 3,478행(종목코드↔corp_code 매핑)만 존재하고
  재무·공시 데이터는 비어 있다. 계획된 백필은 206,859콜 / 약 10.6일 규모이며 아직 시작되지 않았다.
  매핑만 넘겨도 붙일 재무 데이터가 없으므로 이번 범위에서 제외한다. **백필 완료 후 추가 대상**
- **`krx_timing.db`** — KRX 데이터 확정 시각 측정용 운영 프로브(30행). 협업자에게 가치 없음
- **`kael-system-v3` 전체** — `quant.db`(스코어링·컨센서스), `dart.db`(공시), `news.db`, **`trade.db`(매매기록)**

`ingest_log`·`ingest_shard`는 **포함한다.** 협업자가 데이터 적재 범위를 스스로 확인할 수 있어야
백테스트 구간을 잘못 잡지 않는다. 민감정보 점검 결과 `note`는 `skipped by price-probe`(휴장일)뿐이고
`next_cursor`는 전 행이 비어 있어 노출되는 정보가 없다.

## 아키텍처

```
[카엘 서버]
  원장 (정본)                     ← 수집기 변경 0
  ~/quant-ledger/data/raw/{krx,kiwoom}.db        SQLite
          │
          │  일 1회 리빌드 · DuckDB (상주 프로세스 없음) · 실측 136초
          ▼
  공유본 (파생물)
  /srv/quant-share/share/v/<build_id>/*.parquet  673.7MB
          │
          │  Caddy + mTLS (Caddy는 이미 운영 중)
          ▼
[협업자]  로컬 다운로드 → DuckDB로 반복 백테스트
```

**데이터베이스 서버를 신설하지 않는다.** Parquet은 파일이고 DuckDB는 임베디드다.
서버에 새로 상주하는 프로세스가 없다.

### 파일 구조

```
/srv/quant-share/                 ← root:root 755 (chroot/공개 루트 요구사항)
├── build/<build_id>/             ← 작업 중. 검증 통과 전 비공개
└── share/                        ← kael:kael 755
    ├── latest -> v/<build_id>    ← 상대경로 심볼릭. mv -T 로 원자 교체
    └── v/<build_id>/
        ├── *.parquet (13개)
        ├── manifest.json
        └── KNOWN_GAPS.md
```

`build/` 에서 만들고 검증한 뒤 `v/<build_id>/` 로 옮긴다. 실패한 산출물이 공개 경로에 나타나지 않는다.
심볼릭 링크는 **상대경로**여야 한다 — 절대경로는 chroot 안에서 깨진다.
`ln -sfn` 은 unlink+symlink 라 원자적이지 않으므로 `mv -T`(rename(2))를 쓴다.

### 파이프라인

```
[0] 게이트   ingest_log / ingest_shard status 확인 → 수집 미완료면 중단
[1] 읽기     SQLite READ_ONLY ATTACH, 단 1회      ← 두 번 읽으면 스냅샷이 갈린다
[2] 변환     build_id 부여 → typed Parquet 생성 (build/)   ★ canonical
[3] 게이트   행수 · PK중복 · 날짜범위 · 변환실패 검증
[4] 공개     build/ → v/<build_id>/ 이동
[5] 교체     latest 심볼릭을 mv -T 로 원자 교체
[6] 기록     manifest.json (build_id, 원본 해시·mtime, 테이블별 행수·날짜범위, schema fingerprint)
[7] 보관     v/ 최근 7개 유지, 이전 삭제
```

실패 시 `latest`는 이전 성공 버전을 그대로 가리킨다.

## 타입 변환 규칙

원장은 **전 컬럼 TEXT**다(`typeof()` 실측). 그대로 쓰면 `'9800' > '38650'` 같은 문자열 정렬이 발생한다.

### KRX

| 대상 | 규칙 | 근거 |
|---|---|---|
| `ISU_CD`, `ISU_SRT_CD`, `ticker` | **TEXT 유지** | `005930`을 정수화하면 `5930`이 된다 |
| `BAS_DD`, `bas_dd_req`, `LIST_DD` | `'YYYYMMDD'` → DATE | |
| 가격·거래량·시총·`NAV` | BIGINT / DECIMAL(18,2) | 빈값 0건·비숫자 0건 실측 |
| `FLUC_RT`, `CMPPREVDD_*` | DECIMAL(18,2) (음수 정상) | 소수 최대 2자리 실측 |
| **`PARVAL`** | **TEXT 유지** | `무액면` **31,487건**, `.25` 존재 → 숫자 변환 불가 |
| ETF `OBJ_STKPRC_IDX`/`FLUC_RT_IDX` | 빈문자열 → **NULL** | 107,069 / 107,035건. **기초지수 없는 ETF로 정상 데이터** |
| 지수 `CLSPRC_IDX`/`ACC_TRDVAL`/`MKTCAP` | 빈문자열 → **NULL** | 4,094~20,836건 |

빈 문자열을 캐스팅하면 **0**이 된다. 지수 시가총액 0원이 20,836건 생기므로 NULL로 보내야 한다.
다만 이는 수집 실패가 아니라 정상 데이터이므로 **assertion으로 빌드를 중단시켜서는 안 된다.**

### 키움 — 부호 규칙이 셋으로 갈린다

| 패턴 | 컬럼 | 규칙 |
|---|---|---|
| 가격 (부호 = 등락 방향) | `close_pric`(10008·10014), `cur_prc`(10060) | **부호 전부 제거** |
| 등락·증감 (부호 = 진짜 값) | `pred_pre`, `flu_rt`, `chg_qty`, `dbrt_trde_irds`, `frgnr_limit_irds` | `+`만 제거, `-` **유지** |
| 순매수 (음수 정상) | `ind_invsr`, `frgnr_invsr`, `orgn`, `fnnc_invt`, `insrnc`, `invtrt`, `etc_fnnc`, `bank`, `penfnd_etc`, `samo_fund`, `natn`, `etc_corp`, `natfor` | `+` 제거, `-` **유지** |
| 비율 (`+`는 장식) | `wght`, `trde_wght`, `limit_exh_rt` | `+` 제거 → DECIMAL |
| 순수 양수 | `trde_qty`, `shrts_qty`, `ovr_shrts_qty`, `poss_stkcnt`, `rmnd`, `remn_amt` 등 | 그대로 |
| 부호 **코드** | `pred_pre_sig` (1~5) | **TEXT 유지.** 값이 아니라 상한/상승/보합/하한/하락 코드 |

요약하면 **`+`는 전 컬럼에서 제거하고, `-`는 `close_pric`·`cur_prc`에서만 제거한다.**

#### 부호 제거가 무손실인 근거

`close_pric = '-52800'` 은 **종가 52,800원 + 전일 대비 하락**을 뜻한다. 그대로 캐스팅하면 종가가 음수가 된다.

- KRX 종가와 대조: 2026년 이후 음수 접두사 **61,118건 전부**가 부호 제거 시 일치(100%), 원본 그대로는 0% 일치
- 부호 중복성 전수 검증: `close_pric`/`cur_prc`의 부호가 `pred_pre` 부호와 **불일치 0건** (ka10008 768만 + ka10014 397만 + ka10060 762만 = **1,927만 행**)

즉 가격 컬럼의 부호는 `pred_pre`와 완전한 중복이므로, 제거해도 정보가 사라지지 않는다.

## 무손실 검증 결과

검증 방식은 **변환 → 역변환 → 원본 바이트 비교**다. 되돌렸을 때 원본과 다르면 손실이다.

| 검증 항목 | 결과 |
|---|---|
| 행수 | 31,057 → 31,057 일치 |
| 날짜·종목코드·종가·전일대비·거래량·시총·상장주식수 왕복 | 불일치 **0** |
| 키움 부호 제거 | 1,927만 행 전수, 불일치 **0** |
| **변환 실패로 인한 추가 NULL** | **0건.** 원본 빈값 수와 Parquet NULL 수가 정확히 일치 |
| 소수 정밀도 | 전 컬럼 최대 2자리 → `DECIMAL(18,2)` 무손실 |
| 정수 범위 | 최대 2.1×10¹⁵ vs BIGINT 9.2×10¹⁸ (4,352배 여유) |

### 의도적으로 버리는 것

| 항목 | 실질 영향 |
|---|---|
| 원본 TEXT 표현 (`+5100` → `5100`) | typed만 제공하기로 한 결정의 결과. 등락 방향은 `pred_pre`에 보존 |
| 빈 문자열 → NULL | 원본에 진짜 NULL이 0건이라 구분할 대상이 없다. 빈 문자열이 0으로 캐스팅되는 사고를 막는 정정 |

## 접근 방식

**HTTPS + mTLS (Caddy).** 서버에 SSH 계정을 만들지 않는다.

```
data.kaelinvestment.com {
    tls {
        client_auth {
            mode require_and_verify
            trusted_ca_cert_file /etc/caddy/quant-ca.crt
        }
    }
    root * /srv/quant-share/share
    file_server
}
```

| 항목 | 내용 |
|---|---|
| 인증 | 클라이언트 인증서. 없으면 **TLS 핸드셰이크 단계에서 차단** — HTTP 요청이 서버에 닿지 않음 |
| CA | 카엘이 자체 발급. **`ca.key`는 서버에 올리지 않는다** (`ca.crt`만 필요) |
| 인증서 수명 | 365일. 유출 시 위험 기간을 1년으로 제한 |
| 신규 개방 포트 | **없음** (443 재사용, Caddy 이미 운영 중) |
| 차단 | Caddyfile 블록 제거 또는 CA 재발급 |

### 협업자 사용 흐름

```bash
curl --cert sangmok.crt --key sangmok.key \
     -O https://data.kaelinvestment.com/v/<build_id>/krx_stk_bydd_trd.parquet
```
```sql
SELECT ticker, bas_dd, tdd_clsprc FROM '~/quant-data/krx_stk_bydd_trd.parquet'
WHERE bas_dd >= '2020-01-01';
```

**협업자는 특정 `build_id`를 고정해서 쓴다.** `latest`를 매일 따라가면 원장 정정이 반영되어
과거 값이 바뀌고, 어제 백테스트와 오늘 백테스트 결과가 달라진다. 사용한 버전은 `manifest.json`에 기록된다.

## KNOWN_GAPS — 제공하지 않는 것

원장은 깨끗하지만 **백테스트에 필요한 파생물이 없거나 오염되어 있다.** 이들은 이번 범위에서
**만들지도, 제공하지도 않는다.** 협업자가 원주가를 수정주가로 착각하는 순간 모든 결과가 무너지므로
아래를 데이터와 함께 배포한다.

| 없는 것 | 실측된 영향 |
|---|---|
| **DART 재무·공시 전체** | 본 백필 미착수. **밸류·퀄리티 팩터를 만들 수 없다** — PBR·PER·ROE·부채비율 등 재무 기반 지표 전부 불가. 협업자가 지금 만들 수 있는 것은 **가격·수급 기반 팩터**(모멘텀·변동성·거래량·수급·공매도·대차)로 한정된다 |
| **수정주가** | 원장은 **원주가**다. 무상증자·액면분할 **428건**에서 수천% 점프가 진짜 수익률로 읽힌다. 조정계수는 **20.3% 오염**(457건 중 93건이 정지 재개일 시가기준가 오인, `052670`에 **+29,948%** 잔류) |
| **배당 / TR 수익률** | 16.6년 CAGR이 총수익 대비 **23~25% 과소**. 팩터마다 부호가 달라(저변동성 −4.35%p/년, 밸류 +3.01%p) 조직적 편향 발생 |
| **유니버스 플래그** | 상장폐지일 없음 → 생존편향. `stock_status` 0행 → 정리매매 68건/년(최대 +182.61%) 잔류 |

### 생존편향에 대한 정확한 진술

`krx_stk_bydd_trd`는 **그날 거래된 전 종목**을 담으므로 **원장 자체에는 생존편향이 없다.**
편향은 협업자가 *최신 종목마스터로 과거를 조회할 때* 생긴다. 폐지일 마스터가 없으므로
**원장의 마지막 등장일로 근사**해야 한다.

### 오염 방지 3중 장치

1. **경로·파일명** — `raw_unadjusted` 를 경로에 명시
2. **`manifest.json`** — `known_gaps` 필드에 위 목록 포함
3. **`KNOWN_GAPS.md`** — 데이터와 같은 디렉터리에 동봉

## 설계 결정

### 1. PostgreSQL을 쓰지 않는다

- **선택**: Parquet + DuckDB
- **대안**: PostgreSQL 서버 구축 (협업자 요청)
- **이유**: 세 축 모두에서 밀린다.
  - **성능** — 백테스트는 전체 스캔이다. 컬럼 스토어가 12~221배 우세(실측). PG는 행 지향 OLTP 엔진
  - **보안·운영** — 계정·GRANT·비밀번호·pgdata 권한·컨테이너 관리가 모두 신규 부담. 특히 협업자 쿼리 하나가 같은 서버의 `kael-system-v3` 파이프라인을 마비시킬 수 있다
  - **되돌리기 비대칭** — Parquet→PG는 DuckDB 한 줄이지만, PG→Parquet은 파이프라인 폐기 + 협업자 코드 재작성
- **미해결**: 협업자 백테스트 엔진이 psycopg/SQLAlchemy 커넥션을 **요구하는지 미확인**. 요구한다면
  Parquet에서 PG로 적재하는 단계를 추가한다(파이프라인 재사용 가능)

### 2. SSH가 아니라 HTTPS + mTLS

- **선택**: Caddy + 클라이언트 인증서
- **대안**: SFTP chroot (SSH 계정 생성)
- **이유**: SFTP chroot는 커널 격리가 강하지만 **호스트에 로그인 가능한 주체가 하나 늘어난다.**
  HTTPS는 SSH 계정을 만들지 않고, Caddy는 이미 비특권으로 정적 파일만 서빙한다.
  mTLS는 basic auth와 달리 **TLS 핸드셰이크 단계에서 차단**하므로 인증 강도가 공개키급이다
- **부가**: 카엘이 인증서를 발급해 전달하는 방식이라, 키를 직접 만들어 주고 싶다는 요구와도 맞는다

### 3. Parquet이 canonical

- **선택**: 원장 → Parquet(정본 파생물) → (필요 시) 다른 형식
- **대안**: 원장에서 각 산출물을 개별 생성
- **이유**: 산출물을 각각 원장에서 뽑으면 **SQLite를 여러 번 읽게 되어 스냅샷이 갈린다.**
  수집기가 그사이 커밋하면 산출물끼리 불일치한다. 단일 경로로 만들면 일관성이 보장되고,
  나중에 PG가 필요해져도 Parquet에서 적재하면 된다

### 4. `isu_base_info` 압축 최적화는 불필요

- **선택**: 일별 스냅샷 구조를 그대로 둔다
- **대안**: SCD-2 전환(변경일만 보관, −1.84GB 절감안)
- **이유**: 실측 결과 `krx_stk_isu_base_info`는 시세 테이블과 행수가 같은데(377만) **84.3MB vs 4.1MB로 20배 작다.**
  매일 같은 값이 반복되어 Parquet 딕셔너리 인코딩 + zstd가 거의 공짜로 처리한다.
  SQLite에서 존재하던 용량 문제가 Parquet에서는 존재하지 않는다

## 구현 현황

| Phase | 내용 | 상태 |
|---|---|---|
| **1** | 리빌드 파이프라인 (`ops/rebuild_share.py` → 서버 `~/quant-ledger/src/`) | **완료** |
| | 산출물 `~/quant-ledger/share/{build,v,latest}` | **완료** |
| | 크론 `30 0 * * *` UTC(=KST 09:30), flock 적용 | **완료** |
| **2** | `/srv/quant-share` 생성·권한 (sudo 필요) | 대기 |
| **3** | Cloudflare DNS·CA·인증서·Caddyfile (sudo + 카엘 개입) | 대기 |

### Phase 1 검증 결과 (2026-08-25 실행)

- 행수·PK중복·행수감소·변환실패 NULL 게이트 **전부 통과**
- **실패 주입 테스트 PASS** — 리빌드 중단 시 `latest`가 이전 버전 유지, 공개 디렉터리 미오염
- 크론 명령을 최소 PATH 환경(`env -i`)에서 실행 검증

### Phase 1에서 발견·수정한 결함

**DEFECT-S01: 실패한 build 잔여물 누적**
- **상황**: 리빌드가 검증 실패 또는 예외로 중단된 뒤 재실행할 때
- **인풋**: `rebuild_share.py` 실행 → 검증 게이트에서 중단
- **에러 위치**: `ops/rebuild_share.py` — `build_dir.mkdir()` 이후 실패 시 `build/<build_id>/`가 잔존.
  성공 경로에서만 `shutil.move`로 비워지므로 실패분은 영구 축적
- **위험성**: 실패 1회당 최대 674MB 축적. 크론이 매일 실패하면 주당 4.7GB가 조용히 디스크를 잠식.
  불완전한 산출물이라 보관 가치도 없다
- **수정**: 시작 시 `build/` 하위 미완성 산출물을 정리

## 실측 근거 (2026-08-25, kael-server)

| 항목 | 값 |
|---|---|
| 전체 변환 시간 | **136.0초** (4,674만 행, 13개 테이블) |
| 산출물 크기 | **673.7MB** (SQLite 7.4GB 대비 **11.0배** 압축) |
| 최대 테이블 | `ka10060_investor_flows` 762만 행 / 24.7초 / 122.4MB |
| DuckDB | 1.5.5 (`~/quant-ledger/.venv`) |

일 1회 크론에 136초는 부담이 없다.

## 미검증 항목

| 항목 | 확인 방법 |
|---|---|
| **DuckDB `httpfs`의 mTLS 클라이언트 인증서 지원** | 실제 연결 시도. 미지원이면 `curl` 다운로드 방식만 사용(실사용 지장 없음) |
| Caddy `client_auth` 실제 차단 동작 | 인증서 없이 요청해 거부되는지 확인 |
| ~~`manifest.json`용 원본 SHA256 계산 시간~~ | **해결.** 7.4GB 해싱 **12.9~16.8초** — mtime+size 대체 불필요 |

## Test plan

- [ ] 변환 무손실 검증 — 왕복 대조로 불일치 0 확인 (KRX·키움 각 1개 이상)
- [ ] 변환 실패 NULL 검증 — 원본 빈값 수와 Parquet NULL 수 일치
- [ ] 원자 교체 검증 — 리빌드 실패 주입 시 `latest`가 이전 버전 유지
- [ ] mTLS 차단 검증 — 인증서 없이 접근 시 거부
- [ ] 권한 검증 — `/srv/quant-share` 소유·권한이 요구사항 충족
- [ ] 보관 정책 — `v/` 8개째 생성 시 가장 오래된 것 삭제
- [ ] 문서 갱신 — `KNOWN_GAPS.md`가 데이터와 함께 배포되는지

## 관련 컨텍스트

- `database/docs/DATA_CATALOG.md` — PR-02/PR-03/MS-02/MS-04/MS-05 결함 상세
- `database/docs/archive/RESEARCH_VERDICT.md` — 원장 SQLite + 분석층 Parquet 아키텍처 확정
