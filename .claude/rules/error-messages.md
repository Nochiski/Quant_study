---
paths:
  - "**/*.py"
---

# 에러 메시지 / 진단 디테일 룰 (코드 측)

코드에서 예외를 raise 하거나 `logger.warning/error/exception` 을 호출할 때, 메시지에 **재현·진단에 필요한 컨텍스트를 무조건 포함**한다. 한 줄짜리 추상 메시지(`"operation failed"`, `"X returned empty"`, `"invalid input"`) 금지.

> issue/findings/PR body 에 결함을 **기술**하는 형식(4요소)은 `.claude/rules/pr-review.md` "결함 / 이슈 보고 형식" 참고. 이 파일은 코드 안 메시지 디테일 룰이다.

## 포함해야 할 컨텍스트

호출 부에서 다음 중 해당하는 것을 모두 표시:

1. **입력값**: 함수에 들어온 인자 (ticker, 기간, 파라미터, path, index 등). 사용자 입력이면 반드시.
2. **식별자**: 다루던 객체의 id/key/name (종목코드, 전략명, 데이터 소스명, 실행 id 등).
3. **shape / count / size**: DataFrame/ndarray/list 면 shape 또는 행 수.
4. **기대 vs 실제**: validation 실패면 "expected X, got Y" 형태.
5. **위치 단서**: 어떤 step / 리밸런싱 시점 / sub-operation 에서 났는지 (특히 multi-step 파이프라인).

## 코드 예시

```python
# Bad — 무엇이 왜 실패했는지 모름
raise RuntimeError("price data is empty")

# Good — 입력값 + 식별자 + 기간 포함
raise EmptyPriceDataError(
    f"no rows returned — ticker={ticker} start={start} end={end} source={source}"
)

# Bad
raise ValueError("invalid window")

# Good
raise ValueError(
    f"lookback window exceeds available history: window={window} rows={len(df)} ticker={ticker}"
)

# Bad
logger.warning("skip")

# Good
logger.warning(
    "rebalance skipped — insufficient universe (date=%s, n_valid=%d, required=%d)",
    date, n_valid, required,
)
```

## 보안 예외

다음 경우는 detail 을 축약/마스킹한다:

- API key·토큰·계좌번호 등 비밀값은 절대 메시지/로그에 안 적음 — 브로커·데이터 벤더 클라이언트에서 특히 주의
- 외부로 내보내는 메시지(리포트, 알림 봇 등)의 절대 경로는 **상대 경로**로 변환
- 로컬 로그(`logger.*`) 는 절대 경로/상세 stack 노출해도 OK — 단 PII/secret 은 제외

## 점진적 적용

신규/수정 코드는 위 룰 무조건. 기존 코드의 추상 메시지는 PR 범위에 들어오면 같이 갱신, 별도 cleanup PR 도 환영. "Y가 추상 메시지로 raise" 만 짚는 한 줄 결함 보고는 cleanup 영역으로 분리.

## 로깅 레벨 가이드

- DEBUG: 상태 변화 상세 (개발 전용) · INFO: 주요 동작 시작/완료 (운영 기본) · WARNING: 복구된 에러/재시도 · ERROR: 실패 + stack trace · CRITICAL: 시스템 정지 필요
- 외부 호출(데이터 API, 주문 전송)은 항상 로그 (요청 파라미터, 응답 요약, 결과, 소요 시간)
- 명백히 잘못된 레벨(예: `logger.error("시작합니다")`)만 리뷰 지적 — 미묘한 레벨 선택은 주관적이므로 지적하지 않는다
