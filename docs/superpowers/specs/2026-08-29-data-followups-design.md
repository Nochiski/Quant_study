# 로드맵 D: 데이터 측 후속 설계 (2026-08-29)

시장 데이터 포트 스펙(`2026-08-29-market-data-ports-design.md`)의 "다음 단계" 세 항목을
구현한다. 4단계(주문 생명주기)와 독립이며 `feat/order-lifecycle` 위에 스택된다.

| 하위 | 내용 | 승격/추가 |
|---|---|---|
| D1 | 자본변동(액면분할·병합) 검출 포트 + KRX 어댑터 + 엔진의 포지션 조정·전략 알림 | Event: CORPORATE_ACTION (UNSUPPORTED → IMPLEMENTED) |
| D2 | 종목마스터 기반 유니버스 포트 + KRX 어댑터 + `ctx.universe()` | 새 포트 |
| D3 | 외부 DB 어댑터(sqlite) + 어댑터 공통 계약 테스트 | 새 어댑터 (엔진 변경 0이어야 함) |

**포함하지 않는 것**: 수정주가(adjusted price) 생성 — 원장 재빌드 책임. 배당·유상증자 등
주식 수가 바뀌지만 가격이 반비례하지 않는 사건의 회계 처리 — 검출만 하고 `SHARE_COUNT_CHANGE`로
알린다. 유니버스 자동 리밸런싱 — 전략 책임.

## 원장 사실 (실측, 2026-08-29)

- 시세 테이블 `krx_{stk,ksq}_bydd_trd`에 `list_shrs`(상장주식수)가 있다. 삼성전자 2018-05-03 →
  05-04: `list_shrs` 128,386,494 → 6,419,324,700 (×50.0000), 종가 2,650,000 → 51,900 (÷51.06).
  주식 수 변화 × 가격 변화 ≈ 0.98 — 액면분할의 결정적 신호다. 에스와이코퍼레이션 2013-08-22는
  `list_shrs` 92,446,775 → 100,000 (÷924)인데 종가가 537 → 1(정지 마커)이라 가격으로 확인이 안 된다.
- 마스터 테이블 `krx_{stk,ksq}_isu_base_info`는 **일별 스냅샷**이다: `bas_dd_req`(기준일)마다
  그날 상장된 종목 한 행씩(`isu_srt_cd` 6자리, `list_dd` 상장일, `secugrp_nm` 증권군). 삼성전자
  4,094행(2010-01-04 → 2026-08-20), 에스와이코퍼레이션 928행(→ 2013-09-24 상장폐지). 따라서
  "세션 d의 구성 = `bas_dd_req == d`인 행"으로 look-ahead 없이 구성을 얻는다.
- 레포 픽스처(`tests/fixtures/krx_parquet/`)에는 마스터 테이블이 없다 → 슬라이스 스크립트를
  확장해 같은 5종목의 마스터 행을 추가한다.

## 확정한 설계 결정

### 1. D1 검출 규칙: 주식 수 변화가 1차, 가격 반비례가 확인

- **선택**: 종목별 세션 정렬 후 연속 행 `(prev, cur)`에서 `r = cur.list_shrs / prev.list_shrs`.
  `r ≥ 1.5` 또는 `r ≤ 1/1.5`이면 사건. `p = cur.close / prev.close`가 정의되고(둘 다 > 0)
  `0.5 ≤ p × r ≤ 2`이면 가격이 반비례로 확인된 것 → `SPLIT`(r > 1) / `REVERSE_SPLIT`(r < 1).
  확인되지 않으면 `SHARE_COUNT_CHANGE`(ratio는 남기되 엔진이 포지션을 건드리지 않음).
- **대안**: 등락률(`fluc_rt`) 절대값이 가격제한폭(±30%)을 넘는 세션을 사건으로 보기.
- **이유**: 등락률만으로는 분할과 정지 해제 급등락을 구분할 수 없고 비율도 못 얻는다.
  `list_shrs`는 원장이 이미 제공하는 사실이고 비율이 그대로 나온다. 1.5 임계는 유상증자·
  자사주 소각 같은 소폭 변화(삼성전자 2017-05 −6%)를 제외하기 위한 값.
- 이벤트 시각은 `cur` 세션(새 주식 수가 처음 나타난 날)의 0시. 거래정지 행(거래량 0)도
  검출 입력에는 포함한다 — 분할 직후 첫 행이 정지 행일 수 있다.

### 2. D1 스키마: `CorporateActionEvent` 확장

- `action_type: str` → `CorporateActionType` enum(`SPLIT`, `REVERSE_SPLIT`, `SHARE_COUNT_CHANGE`).
- `ratio: Decimal` (구주 1주당 신주 수, SPLIT이면 > 1), `detail: str` (검출 근거: 주식 수·종가 전후).
- 포트 `ports/corporate_actions.py`: `CorporateActionQuery(instruments, start, end)`,
  `CorporateActionSource.load_actions(query) -> CorporateActionResult(actions, status, detail)`
  — `LoadStatus` 재사용, 부분 성공 금지 규칙 승계.

### 3. D1 엔진: 확인된 분할만 포지션을 조정하고, 기록·알림은 모두 남긴다

- `BacktestEngine.run(strategy, feed, corporate_actions=())`. 세션 ts의 MARKET 처리 **맨 앞**
  (체결 시도 전)에 그 ts의 사건을 적용한다.
- `SPLIT`/`REVERSE_SPLIT` + 보유 포지션: 수량 `floor(qty × r)`, 평균단가 `avg / r`. 단주(소수
  부분)는 그 세션 시가에 현금 지급 — `FillEvent`가 아니라 `CorporateActionApplied(instrument,
  ts, old_qty, new_qty, cash_paid)` 레코드로 EventStore에 남긴다. 회계 불변조건은 "Snapshot은
  Bar + Fill + CorporateActionApplied로 재계산 가능"으로 확장한다.
- 해당 종목의 대기 주문은 전부 `CANCELLED(detail="corporate action …")` — 지정가·스톱 수준이
  무의미해진다.
- `SHARE_COUNT_CHANGE`: 기록·알림만.
- 전략이 `EventKind.CORPORATE_ACTION`을 선언했으면 NOTIFY 우선순위로 전달(4c와 같은 경로).
- 사건 종목이 `feed`에 없거나 bar가 없는 세션이면 `CorporateActionWithoutBar` 예외로 중단 —
  시가 없이 단주를 정산할 수 없고 조용히 건너뛰면 포지션이 틀어진다.

### 4. D2 유니버스 포트: 구간(interval) 결과

- `ports/universe.py`: `UniverseQuery(venue, start, end)`,
  `UniverseSource.load_universe(query) -> UniverseResult(memberships: tuple[Membership, ...], status, detail)`,
  `Membership(instrument, first_session: date, last_session: date)`.
  `UniverseResult.members(session: date) -> frozenset[InstrumentId]`,
  `UniverseResult.instruments_active_between(start, end) -> tuple[InstrumentId, ...]`(BarQuery용).
- **대안**: `members(session)`를 포트 메서드로 두고 매 세션 소스를 조회.
- **이유**: 마스터가 일별 스냅샷이라 종목별 `min/max(bas_dd_req)`로 구간이 바로 나오고,
  엔진은 파일을 다시 읽지 않는다. 상폐 후 재상장은 원장에 없어 구간 하나로 충분하다
  (재상장이 나타나면 `Membership` 여러 개로 확장 — 결과 타입은 이미 튜플).
- KRX 어댑터 `KrxParquetUniverseSource(root, security_groups=None)`: 두 마스터 파일을 읽어
  `isu_srt_cd`별 `bas_dd_req` min/max. `security_groups`로 `secugrp_nm` 필터(기본 전체).
- 엔진: `BacktestEngine.run(..., universe: UniverseResult | None = None)`,
  `ctx.universe() -> frozenset[InstrumentId]` = `members(now.date())`. universe를 주지 않고
  호출하면 `UniverseNotProvided` 예외(조용히 빈 집합을 주면 전략이 "종목 없음"으로 오판).

### 5. D3 sqlite 어댑터: 표준 라이브러리로 포트 가설 검증

- `adapters/sqlite_bars.py`의 `SqliteBarSource(path, table="bars")`. 스키마
  `(symbol TEXT, session TEXT ISO-8601, open REAL, high REAL, low REAL, close REAL, volume INTEGER)`.
  `sqlite3`는 표준 라이브러리라 새 의존성이 없다. 정렬은 SQL `ORDER BY session`, 정제는
  `clean_raw_bars` 공용.
- 어댑터 공통 계약 테스트 `tests/test_bar_source_contract.py`: CSV·KRX parquet·sqlite 세
  어댑터를 같은 시나리오(정상 로드·기간 필터·누락 종목 NO_DATA·중복 세션 FORMAT_ERROR·
  거래정지 행 처리)로 파라미터화. 새 어댑터는 픽스처 빌더 하나만 추가하면 계약 전체를 통과해야 한다.
- 성공 기준: `src/backtest_engine/engine/`·`types/`·`data/` diff 0.

## 테스트·검증 계획

### 공통
- 회귀: 4단계까지의 테스트 전부 통과, KRX 데모 출력 불변(사건 조정은 옵션 인자라 기본 경로 불변).
- `ruff check`·`pyright` 0 오류. 픽스처 재생성 후 `scripts/slice_krx_fixture.py` 바이트 동일성 유지.

### D1
- 검출 단위(`tests/test_corporate_actions.py`, 테스트가 만든 parquet): (i) ×50 + 가격 ÷50 →
  SPLIT ratio 50, (ii) ÷10 + 가격 ×10 → REVERSE_SPLIT, (iii) ×50인데 종가 0/1 → SHARE_COUNT_CHANGE,
  (iv) −6% 소폭 변화 → 사건 없음, (v) 상한가 연속(가격만 ×1.3², 주식 수 불변) → 사건 없음(오탐 방지),
  (vi) 기간 필터 경계, (vii) 요청 종목 누락 → NO_DATA 전체 실패.
- 엔진 골든(`tests/test_engine_golden.py`): 보유 7주, 1:5 분할 세션 시가 20 → 35주, 평균단가 ÷5,
  단주 0; 보유 7주 1:1.5 → 10주 + 단주 0.5×시가 현금, equity 연속성(분할 전후 equity 손계산 동일);
  대기 GTC 주문 CANCELLED; CORPORATE_ACTION 선언 전략이 이벤트 수신; 미선언 전략은 미수신;
  SHARE_COUNT_CHANGE는 포지션 불변; bar 없는 세션의 사건 → 예외.
- 스모크: 픽스처 슬라이스에서 삼성전자 2018-05 분할이 SPLIT으로 검출되고 분할 구간을 포함한
  골든크로스 실행이 예외 없이 돈다 (값 단언 없음).

### D2
- 어댑터 단위(테스트가 만든 마스터 parquet): 구간 min/max, 두 파일 병합, `security_groups` 필터,
  빈 결과 NO_DATA.
- `UniverseResult` 단위: `members(d)` 경계 포함, 상장 전/상폐 후 제외(look-ahead), `instruments_active_between`.
- 엔진: `ctx.universe()`가 세션별로 바뀌는지(상장 세션부터 포함, 상폐 다음 세션부터 제외),
  universe 미제공 시 예외.
- 스모크: 픽스처 마스터로 5종목 구간이 나오고 에스와이코퍼레이션이 2013-09-24 이후 빠진다(값 단언은 존재 여부만).

### D3
- 계약 테스트 3어댑터 × 5시나리오 전부 통과. sqlite 고유: 테이블 없음 → NO_DATA, 컬럼 타입 오류 → FORMAT_ERROR.
- 엔진·타입·data 패키지 diff 0을 리뷰에서 확인.

## 구현 결과와 스펙 차이 (2026-08-29 구현 완료)

- D1 포지션 조정 시 해당 종목의 평가 가격(mark)을 정산가(사건 세션 시가)로 교체한다 —
  분할 전 종가로 평가하면 SESSION_CLOSE 전에 읽히는 equity가 왜곡되기 때문. 사건 ts가
  feed 세션에 없으면 run 시작 전에 `CorporateActionWithoutBar`로 거절한다.
- D2 `UniverseQuery.venue`는 어댑터가 `InstrumentId`를 만들 때 쓴다 (마스터에 거래소 코드가 없음).
- D3 sqlite 어댑터는 `mode=ro` URI로 열어 없는 파일을 만들지 않는다. 중복 세션은 별도 검사
  없이 `clean_raw_bars`의 역행 검사로 FORMAT_ERROR가 된다.
- 픽스처 스모크는 `.claude/rules/testing.md`에 따라 값 단언 없이 "로드·실행이 되는지"만 본다.

## 다음 단계

5단계 Basket·공매도·MARGIN (`2026-08-29-roadmap-overview.md`).
