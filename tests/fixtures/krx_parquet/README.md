# KRX 원장 테스트 슬라이스

`quant-data-20260826` 빌드(카엘 서버가 KRX API 원장을 typed parquet으로 변환한 것,
전체 674MB)에서 종목 5개의 전 기간 일별시세만 잘라낸 픽스처다.
스키마·파일명은 원본과 같아서 `KrxParquetBarSource(root)`에 이 디렉토리를 그대로 넘기면 된다.

| 파일 | 내용 |
|---|---|
| `krx_stk_bydd_trd.parquet` | KOSPI 일별시세 (005930 삼성전자, 000660 SK하이닉스, 008080 에스와이코퍼레이션) |
| `krx_ksq_bydd_trd.parquet` | KOSDAQ 일별시세 (247540 에코프로비엠, 066970 엘앤에프) |
| `krx_stk_isu_base_info.parquet` / `krx_ksq_isu_base_info.parquet` | 같은 5종목의 종목마스터 일별 스냅샷 (`bas_dd_req`마다 한 행, 6자리 코드는 `isu_srt_cd`) — 유니버스 포트용 |
| `manifest.json` | 슬라이스 시각, 종목 선정 이유, 원본 `manifest.json` 사본 |

종목 선정 이유는 `manifest.json`의 `tickers`에 있다. 요약:
삼성전자는 2018-05 액면분할 정지 구간(o/h/l=0, 거래량 0), 에스와이코퍼레이션은
2013 감자·정지·등락률 +6,699,900%·상장폐지, 에코프로비엠은 기간 중간 상장 케이스.

## 원장 특성 (어댑터가 처리하는 것 / 안 하는 것)

- **원주가(미수정)** — 액면분할·감자 구간의 가격 불연속은 어댑터가 보정하지 않는다.
  백테스트 기간을 자본변동 이후로 잡는 것은 호출자 책임이다.
- 행이 날짜순이 아니다 → 어댑터가 정렬한다.
- 거래정지 중에도 행이 존재하고 종가 표기가 종목마다 다르다(직전값 유지 / 1원) →
  어댑터는 `acc_trdvol = 0`을 유일한 정지 신호로 보고 해당 행을 제거하고 `dropped_rows`로 보고한다.
- 상장주식수(`list_shrs`)가 있어 액면분할·병합은 `KrxParquetCorporateActionSource`가 검출한다
  (삼성전자 2018-05-04 ×50). 가격 반비례가 확인되지 않는 변화는 `SHARE_COUNT_CHANGE`로만 알린다.
- 종목마스터는 일별 스냅샷이라 "세션 d의 상장 종목 = `bas_dd_req == d`인 행"으로
  `KrxParquetUniverseSource`가 look-ahead 없이 구간을 만든다 (에스와이코퍼레이션 2013-09-24 까지).
- 배당·총수익·유니버스 플래그 없음. 상세는 원본 빌드의 `README.md` / `KNOWN_GAPS.md`.

## 재생성

```bash
uv sync --extra parquet
uv run python scripts/slice_krx_fixture.py <원장 디렉토리> --out tests/fixtures/krx_parquet
```

같은 원본이면 결과 parquet은 바이트 단위로 동일하다 (zstd, `bas_dd` 정렬).

## 테스트에서의 사용 규칙

`.claude/rules/testing.md`에 따라 이 파일의 행 수·특정 값을 단언하는 테스트는 쓰지 않는다.
어댑터 동작 테스트는 테스트가 직접 만든 소형 parquet으로 하고, 이 슬라이스는
"포트 → DataFeed → 엔진이 예외 없이 도는지" 스모크 용도로만 쓴다.
