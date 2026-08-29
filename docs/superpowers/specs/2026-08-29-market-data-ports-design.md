# 시장 데이터 포트/어댑터 설계 (2026-08-29)

v1 설계(`2026-08-24-backtest-engine-v1-design.md`)의 데이터 계층을 헥사고날
아키텍처로 분리한 결정 기록. 주가 데이터는 여러 채널(CSV, KRX 원장 parquet,
추후 외부 DB)에서 들어오므로, "데이터를 읽는 쪽"과 "엔진/전략이 입력을 받는 쪽"의
의존 방향을 고정한다.

## 범위

- `ports/market_data.py`: 도메인이 소유하는 계약 — `BarSource` 프로토콜,
  `BarQuery`, `LoadResult`/`LoadStatus`/`OhlcPolicy`.
- `data/cleaning.py`: 소스 무관 정제(OHLC 정책, 거래정지 제거, 시간 역행 검사).
- `adapters/csv_bars.py`: 기존 CSV 로더를 어댑터로 이동 + `CsvBarSource`.
- `adapters/krx_parquet.py`: `quant-data` 빌드의 `krx_*_bydd_trd.parquet` 어댑터.
- `tests/fixtures/krx_parquet/`: 원장 슬라이스(종목 5개, 605KB)와 재생성 스크립트.

의도적으로 뺀 것: 수정주가(자본변동 보정), 종목마스터·지수·수급 테이블 어댑터,
외부 DB 어댑터, 다종목 유니버스 선택. 전부 포트 뒤에 붙을 후속 작업이다.

## 확정한 설계 결정

### 1. 의존 방향: adapters → ports ← engine

- **선택**: `ports/`는 `types/`만 import 한다. 어댑터는 포트를 import 하고,
  엔진·전략·예제는 포트와 `DataFeed`만 본다. 벤더 의존성(pyarrow)은 어댑터
  함수 안에서 지연 import 한다.
- **대안**: `data/` 패키지 하나에 로더를 나열하고 호출자가 골라 쓰기.
- **이유**: 외부 DB 채널이 추가될 때 엔진 코드 변경이 0이어야 한다. 또 코어
  패키지가 pyarrow 없이도 import 되어야 CSV 사용자와 테스트가 가벼워진다.

### 2. 포트는 `load_bars(BarQuery) -> LoadResult` 하나

- **선택**: 종목 집합 + 기간 + OHLC 정책을 `BarQuery` 값 타입으로 받고,
  결과는 기존 `LoadResult`(errors-as-values) 그대로.
- **대안**: `available_instruments()`·`sessions()` 같은 조회 메서드를 함께 요구.
- **이유**: 현재 소비자는 `DataFeed` 하나이며 Bar 튜플만 필요하다. 메서드가
  늘수록 새 채널을 붙이는 비용이 커진다. 필요해지면 별도 포트로 추가한다.

### 3. 부분 성공 금지

- 요청 종목 중 하나라도 데이터가 없으면 전체 `NO_DATA`. `merge_results`가
  종목별 결과를 합칠 때 첫 실패를 그대로 반환한다.
- 한 종목이 조용히 빠진 채 백테스트가 돌면 결과가 왜곡되는데, 호출자가
  `len(bars)`를 세지 않는 한 알 수 없다. silent corrupt 방지.

### 4. 정제 로직은 한 곳(`data/cleaning.py`)

- CSV와 parquet 어댑터가 각자 OHLC/정지/역행 검사를 갖지 않는다. 어댑터는
  `RawBar`(값 + 진단용 `origin`)만 만들고 `clean_raw_bars`에 넘긴다.
- 원래 CSV 로더에 있던 규칙을 그대로 옮겼으며 기존 `test_data.py`가 동작
  불변을 확인한다.

### 5. KRX 원장의 거래정지는 거래량 0으로만 판별

- **선택**: `KrxParquetBarSource`는 `drop_zero_volume=True`로 정제한다.
  가격 0 행(CLAMP 시 제거)과 별개로, 종가가 유지된 정지 행도 제거된다.
- **대안**: 종가 1원 / 등락률 필터.
- **이유**: 데이터 README 실측 — 같은 "감자 후 정지"인데 한 종목은 1원, 다른
  종목은 직전 종가 유지. 신뢰할 수 있는 신호는 `acc_trdvol = 0` 하나뿐이다.
  `OhlcPolicy`와 무관하게 항상 적용되며 `dropped_rows`로 보고된다.

### 6. 수정주가는 어댑터 책임이 아니다 (명시적 제외)

- 원장은 원주가다. 액면분할 428건의 가격 불연속을 어댑터가 "보정"하면 그것이
  silent 보정이 된다. 어댑터는 원장을 그대로 전달하고, 데모/호출자는 백테스트
  기간을 자본변동 이후로 잡는다(`examples/run_krx_demo.py`는 2018-06 이후).
- 후속: 자본변동 이벤트(등락률 > 가격제한폭)를 별도 포트로 노출해 전략이
  해당 종목을 제외하거나 엔진이 포지션을 재평가하도록 한다.

### 7. pyarrow는 optional extra

- `[project.optional-dependencies] parquet = ["pyarrow>=15"]`, dev 그룹에도 포함.
  parquet은 표준 라이브러리로 읽을 수 없어 대안이 없다. 코어 의존성에는 넣지 않는다.

## 검증

- 포트/정제 단위 테스트(`tests/test_ports.py`): BarQuery 검증, CLAMP 보정·제거
  집계, 거래량 0 옵션, 역행 거절, 부분 실패 전파.
- KRX 어댑터 테스트(`tests/test_adapters_krx.py`): 테스트가 만든 parquet으로
  정렬·정지 제거·기간 필터·KOSPI/KOSDAQ 병합·누락 종목·중복 세션·OHLC 위반.
- 스모크: 레포 슬라이스로 골든크로스 엔진 실행 (행 수·값 단언 없음).
- 수동 확인: `examples/run_krx_demo.py`를 슬라이스와 674MB 원장 디렉토리 양쪽에서
  실행해 동일 결과(1,619세션, 2018-06-01 → 2024-12-30) 확인.

## 다음 단계

- 외부 DB 어댑터 (`BarSource` 구현 하나 추가로 끝나야 한다 — 그렇지 않으면 포트 설계 재검토).
- 자본변동 이벤트 포트, 종목마스터 기반 유니버스 포트.
- v1 로드맵 4단계(주문 생명주기 확장)는 이 작업과 독립.
