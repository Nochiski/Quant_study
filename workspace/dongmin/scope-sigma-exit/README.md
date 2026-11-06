# scope σ 손절·익절 시험과 알파 탐색 (2026-10-30, 연구용)

운영 코드가 아니라 연구 재현용 코드다. 결론은 **둘 다 기각** — 운영(S07)에 반영한 것은 없다.

| 폴더 | 내용 | 결론 |
|---|---|---|
| `sim/` | scope 상위 20 주간 + 지수 대비 −2σ 손절 · +3σ 분할 익절 · 정리 규칙 시뮬레이터(1~3차 사전등록 갈래) | σ 규칙 순효과 없음(손절만 +0.3%p t 0.06, 전체 +2.7%p t 1.00, 보완판 −4.1%p) |
| `search/` | 롱온리 알파 탐색 묶음 F1~F7 · 롤링 · 홀드아웃(J6·H5) · 2011~15 확인 | 누적 107 시험, 세 구간 모두 버틴 롱온리 0 |

사전등록·결과 문서는 저장소 밖 `~/quant-data/research/2026-10-30-scope-sigma-exit/`, `~/quant-data/research/2026-10-30-alpha-search/` 에 있다(입력 신호·시장 판도 그 폴더와 `2026-10-26-trading-rules/` 를 읽기만 한다).

## 실행 (backend 가상환경, 작업 폴더 = 이 폴더)

```bash
python -m sim.run extras                 # 지수·시장·스팩 판(한 번)
python -m sim.run run --signal A --all   # 기준선 + 갈래 + 민감도
python -m search.run F1                  # 묶음 판정(F1~F7)
python -m search.holdout                 # J6 홀드아웃 1회 (H5 는 search.holdout_h5)
```

시험: 저장소 루트에서 `backend/.venv/bin/python -m pytest workspace/dongmin/scope-sigma-exit/tests` (CI 대상 아님, 130개).
