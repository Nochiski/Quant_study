# 수동 검증 도구 (tests/manual)

pytest가 수집하지 않는 수동 실행·시각 확인용 산출물을 둔다.

## 엔진 vs Zipline 대조 리포트

커스텀 `backtest_engine`과 Zipline을 같은 데이터(005930 일봉)·같은 전략·같은 비용
가정으로 실행해 equity curve를 세션 단위로 비교한다. Zipline이 설치된
implementation 프로젝트에서 실행한다:

```bash
# 1) 원본 CSV — 레포의 KRX 원장 슬라이스에서 생성한다 (원주가, 2018-06-01 → 2024-12-30, 1,619세션)
uv run python - <<'EOF'
from datetime import date
from pathlib import Path
import csv
from backtest_engine.adapters.krx_parquet import KrxParquetBarSource
from backtest_engine.ports.market_data import BarQuery, OhlcPolicy
from backtest_engine.types.instruments import AssetClass, InstrumentId
inst = InstrumentId(venue="XKRX", symbol="005930", asset_class=AssetClass.EQUITY, currency="KRW")
res = KrxParquetBarSource(Path("tests/fixtures/krx_parquet")).load_bars(
    BarQuery(instruments=(inst,), start=date(2018, 6, 1), end=date(2024, 12, 31), ohlc_policy=OhlcPolicy.CLAMP)
)
out = Path("reference/sangmok/implementation/data/raw/005930.csv")
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["date", "open", "high", "low", "close", "volume"])
    for b in res.bars:
        w.writerow([b.ts.date().isoformat(), b.open, b.high, b.low, b.close, b.volume])
EOF

# 2) 대조 실행 (Zipline 환경)
cd reference/sangmok/implementation
uv sync
uv run python scripts/compare_engines.py --csv data/raw/005930.csv
```

- 콘솔에 시나리오별(OK/MISMATCH) diff 요약이 출력되고, tolerance 초과 시 exit 1.
- `compare_results.json` — 파라미터, diff 통계, 양쪽 equity curve 원본.
- `compare_report.html` — 브라우저로 열면 equity 겹침 차트와 상대 오차
  차트, 통계 표를 볼 수 있는 자체완결 페이지 (외부 리소스 없음).
- `data/raw/*.csv`는 gitignore 대상이라 위 1)로 다시 만든다.

### 시나리오와 결과 (2026-08-29, 6단계까지 반영된 엔진)

| 시나리오 | 대조하는 것 | 결과 |
|---|---|---|
| `buy-hold` | 기본 회계·수수료·T+1 시가 체결 | 최대 상대 오차 0 |
| `golden-cross` | 히스토리 윈도·비중 사이징·재리밸런싱 | 최대 상대 오차 0 |
| `buy-hold-slippage` | 4d `VolumeShareSlippage` + 참여율 캡(GTC 잔량 이월) | 최대 상대 오차 0 |
| `short-hold` | 5a 공매도 회계 (비중 −0.7 단일 진입, 차입비 0) | 최대 상대 오차 0 |

`buy-hold-slippage`는 기본 파라미터(volume_limit 0.025, price_impact 0.1)에서 005930의
거래량 대비 주문(136주)이 작아 충격이 주당 1e-6원 수준이다. 식과 잔량 이월의 동일성은
`--price-impact 10000000000 --volume-limit 0.000005`(세션당 캡 48주라 136주가 3세션에 걸쳐
체결되고 충격이 시가의 약 24%)로도 확인했다 — 역시 최대 상대 오차 0. 이 스트레스 실행이
라우터가 목표형 주문에 `ExecutionPolicy.time_in_force`를 전달하지 않던 결함(GTC 정책인데
DAY 주문이 나가 캡 잔량이 이월되지 않음)을 잡아냈고, 같이 수정했다.

### 정합 규칙 (요약)

- 판단: 세션 T 종가 기준 → 체결: 다음 실제 세션 시가 (Zipline은 하네스 전용
  `NextBarOpenSlippage` / `NextBarOpenVolumeShareSlippage`로 정렬 — Zipline 기본 모델은
  종가 기준이라 그대로 쓰면 규칙이 다르다).
- 수량: `backtest_engine.sizing.floor_delta_shares`를 양쪽이 공유.
- Zipline 캘린더는 XKRX 명시 (기본 XNYS는 한국 휴일을 세션으로 오처리).
- 거래정지 행(가격 0)은 양쪽 모두 세션이 아닌 것으로 처리
  (커스텀: 로더 CLAMP drop / Zipline: 저장소가 0을 NaN으로 읽는 성질 이용).
- 슬리피지 시나리오: 양쪽 다 `share = min(체결량/거래량, volume_limit)`, 충격 `share² × price_impact × open`,
  세션당 체결 상한 `volume_limit × 거래량`, 잔량은 다음 세션 이월(커스텀은 GTC + `max_participation`).
- 지정가·스톱은 Zipline이 종가 기준으로 발동을 판정해 규칙이 다르므로 대조 대상이
  아니다. 이 경로는 `tests/test_broker.py`의 규칙표와 `tests/test_order_lifecycle.py`의
  손계산 골든, 그리고 `backend/tests/test_core_parity.py`(Python↔Rust 코어)로 검증한다.
