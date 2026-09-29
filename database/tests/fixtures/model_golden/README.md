# 모델 골든 픽스처

M2 W1 엔진 이식(v3 `v3_zscore` · v2 `v2_percentrank`)의 기준 데이터. 이식 엔진이 v3 엔진 원본과
같은 점수를 내는지(G-M3 ①, |Δ| ≤ 1e-9 · rank 동일) 로컬·CI 에서 확인하는 데 쓴다.

## 2026-09-28/

- 출처: 서버 `data/compat/quant_20260928.db` — 09-29 아침 5일째 그림자(`logs/shadow_run.sh 20260928`)가
  09-28 확정판으로 새로 만든 compat DB 에 **v3 엔진 원본**(`backend.scoring.engine`·`v2_engine`)이
  점수를 쓴 것. 추출 스크립트 서버 `logs/export_golden.py`.
- 유니버스: `stocks.market_cap IS NOT NULL`(compat `--model-universe estimates`) 619종목.
  v3 점수 579행(시총 1,000억 하한 등 v3 자체 규칙), v2 점수 619행.
- 창: `daily_prices` D−550일 · `investor_detail_flows` D−60일 · 재무·컨센서스는 그 종목 전 행.
- `score_history.parquet`·`score_history_v2.parquet` = 기대 출력(v3 원본이 쓴 그대로).
- `meta.json` = 표별 행수·크기 + `v3_scoring_config`(v3 `load_config().scoring`, 가중치·하위가중·시총 하한).

v3 원본은 이 입력을 compat(v3 스키마) 표로 읽는다. 이식 엔진은 `model.contracts` 의 `fi_*` 표를 읽으므로
테스트는 compat 표 → `fi_*` 어댑터를 거친다(이 변환이 v3 가 본 값과 1:1 이어야 한다).
