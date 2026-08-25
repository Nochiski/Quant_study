# Quant_study

퀀트 스터디 저장소. 데이터 수집·가공부터 백테스팅까지 각자 실습하고, 쓸 만한 코드는 공용으로 올려 함께 쓴다.

## 디렉토리 구조

```
workspace/
  dongmin/     # 개인 작업 공간 (docs/ 만 추적, data/·logs/ 는 git 제외)
  sangmok/     # 개인 작업 공간
src/
  backtest_engine/   # 이벤트 드리븐 백테스트 엔진 (공용)
ops/           # 서버 운영 스크립트 (공용)
docs/          # 설계 스펙
examples/      # 예제 전략
tests/         # 테스트
2026-08-17/    # 설계 아티팩트, Zipline 관찰용 앱
.claude/rules/ # 코딩 규칙 (공용)
```

`shared/` 는 아직 없다. 두 사람이 같은 코드를 각자 짜고 있다는 게 확인되면 그때 만들어서 옮긴다. 미리 만들어 두지 않는다.

## 백테스트 엔진

`2026-08-17/`의 학습 노트에서 고정한 전략 I/O 계약을 `src/backtest_engine` 패키지로 구현한다.

```
src/backtest_engine/
├─ types/          # 프로토콜 계약: Requirements, Event, Context, Decision, Action, serde
├─ capability.py   # DEFINED ≠ IMPLEMENTED — 실행 전 요구사항 협상과 거절
├─ engine/         # reference engine: EventQueue, Router, BrokerSim, Portfolio, Metrics
└─ data/           # CSV 로더(OHLC 정제 정책 포함), DataFeed
```

- 전략은 `requirements()`와 `on_event(ctx, event) -> StrategyDecision` 두 메서드만 구현한다.
  판단만 반환하고, 주문 수량·체결·회계는 엔진 책임이다.
- 엔진 내부는 `(ts, priority, seq)`로 정렬되는 이벤트 큐 하나로 흐른다:
  `MARKET(대기 주문 체결) → FILL(포트폴리오 반영) → SESSION_CLOSE(평가·전략 호출) → ORDER(주문 등록)`.
  T 종가 판단은 T+1 시가에 체결된다 (look-ahead 차단).
- 현금과 보유 수량은 FillEvent 적용 시점에만 변한다. 모든 이벤트는 append-only
  EventStore에 남아 Run → Decision → Action → Order → Fill → Snapshot으로 역추적된다.
- 미구현 Action/Feature/Schedule은 데이터 루프 전에 `CapabilityNotImplemented`로
  전체 위반 목록과 함께 거절된다.

v1 구현 범위(로드맵 3단계): `NoAction` · `SetPortfolioTarget(WeightTarget)` ·
`LiquidatePosition`, MARKET 이벤트, `EverySession` 일정. 나머지 스키마는 정의만
되어 있고 `reference_engine_capabilities()`에 NOT_IMPLEMENTED로 명시된다.

### 실행

```bash
uv sync                             # 의존성 설치 (Python 3.11+)
uv run pytest                       # 테스트
uv run ruff check src tests examples
uv run pyright src tests examples
uv run python examples/run_demo.py  # 005930 일봉으로 골든크로스 백테스트
```

### 검증: Zipline 대조

엔진 회계는 Zipline과의 세션 단위 equity 대조로 검증됐다 (buy-hold 오차 0,
골든크로스 최대 3e-16). 실행 방법과 리포트는 `tests/manual/README.md` 참고.

## 데이터

원장(KRX·키움 수집분)은 카엘 서버가 정본이다. 저장소에는 데이터를 넣지 않는다.
공유 방식은 `docs/superpowers/specs/2026-08-25-quant-ledger-sharing-design.md` 참고.

## 작업 규칙

1. **남의 `workspace/` 폴더는 건드리지 않는다.** 개인 공간 안에서는 구조도 스타일도 자유. 이것만 지키면 충돌이 날 일이 없다.
2. **공용 영역 변경은 상의하거나 PR 로.** `.gitignore`, `.claude/rules/`, `README.md`, `src/`, `ops/`, `pyproject.toml` 이 해당된다.
3. **데이터 파일은 커밋하지 않는다.** 시세 CSV·parquet 등은 `.gitignore` 에서 막아 두었다. 저장소에는 **데이터를 만들어 내는 스크립트**를 넣고, 데이터는 각자 로컬에서 재현한다.
4. **API 토큰·키는 절대 커밋하지 않는다.** `*_token.json`, `*.token` 은 `.gitignore` 에서 막아 두었다.

데이터를 둘 곳이 필요하면 `workspace/<이름>/data/` 를 쓰면 된다 — `**/data/` 규칙으로 이미 git 에서 제외된다. 손으로 계산할 수 있는 소형 테스트 픽스처만 `tests/fixtures/` 아래 CSV 로 예외 허용.

## 코딩 규칙

`.claude/rules/` 에 정리되어 있다.

| 파일 | 범위 | 내용 |
|---|---|---|
| `code-style.md` | `**/*.py` | 기존 헬퍼 재사용, 기능/정리 커밋 분리, ruff·pyright 게이트, 네이밍 |
| `python.md` | `**/*.py` | 성공/실패는 튜플 대신 Result 값 타입으로 |
| `error-messages.md` | `**/*.py` | 예외·로그에 재현 가능한 컨텍스트 포함 |
| `testing.md` | `tests/`, `scripts/` | 산출물 파일 존재/내용을 단언하는 테스트 금지 |
| `pr-review.md` | 전체 | PR 본문 양식, 결함 보고 4요소 |
