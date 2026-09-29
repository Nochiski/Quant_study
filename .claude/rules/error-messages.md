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

위 예시는 내부 예외·로그 메시지다. 사용자에게 그대로 보이는 문장은 아래 절을 따른다.

## 사용자 대면 문장과 내부 메시지의 언어

- 사용자에게 그대로 보이는 문장은 backend가 **한글로 완성**해 보낸다. compile 진단의 `message`
  (`ValidationIssue`)와 결과·데이터 경고의 `message`(`DataWarning`·`PortfolioWarning`·
  `ResearchDataWarning`)가 여기 든다. 영문 안정 키 `code`를 함께 싣고, 재현용 컨텍스트(종목·세션·
  개수 등)는 문장 안에 `key=value` 원문으로 남긴다. 소비자(frontend·AI 결과 설명)는 문장을 다시
  조립·번역하지 않는다. 정본은 `.claude/rules/strategy-workbench-sot.md`의 "authoring 진단 코드"·
  "결과·데이터 경고 문장" 행이다.
- 진단 코드의 네임스페이스(`strategy.*`·`structure.*`·codec 코드, 팩터 그래프 `factor.graph.*`를
  `strategy.expression.*`로 옮기는 규칙)도 같은 SoT의 "authoring 진단 코드" 행이 소유한다. 레지스트리에
  없는 코드를 호출 지점에서 새로 만들지 않는다.
- 내부 예외·로그 메시지의 언어는 정한 규칙이 없다. 지금은 영문과 한글이 섞여 있다(예외는 영문이
  많고, 최근 로그 경고에는 한글이 있다). 어느 언어로 쓰든 위 컨텍스트 규칙은 같다.

## 보안 예외

다음 경우는 detail 을 축약/마스킹한다:

- API key·토큰·계좌번호 등 비밀값은 절대 메시지/로그에 안 적음 — 브로커·데이터 벤더 클라이언트에서 특히 주의
- 외부로 내보내는 메시지(리포트, 알림 봇 등)의 절대 경로는 **상대 경로**로 변환
- API 응답으로 나가는 문장도 외부 메시지다. 우리가 쓰는 문장(포트 결과 `detail`, 원천을 뺀 사유, 스냅샷
  `source`, 질의 중 예외)은 처음부터 서버 경로 없이 쓰고, 경로는 부팅 예외와 로그에만 싣는다. 가리기
  (`_mask_paths`)는 서드파티 예외 원문이 응답으로 나가는 한 곳에만 둔다. 그곳이 run `error`다
  (`application/backtest_run/_service.py`의 `_describe_failure`). 쓰는 쪽이 경로를 싣고 받는 쪽이
  지우는 두 단계를 만들지 않는다(#163)
- 로컬 로그(`logger.*`) 는 절대 경로/상세 stack 노출해도 OK — 단 PII/secret 은 제외

## 점진적 적용

신규/수정 코드는 위 룰 무조건. 기존 코드의 추상 메시지는 PR 범위에 들어오면 같이 갱신, 별도 cleanup PR 도 환영. "Y가 추상 메시지로 raise" 만 짚는 한 줄 결함 보고는 cleanup 영역으로 분리.

## 로깅 레벨 가이드

- DEBUG: 상태 변화 상세 (개발 전용) · INFO: 주요 동작 시작/완료 (운영 기본) · WARNING: 복구된 에러/재시도 · ERROR: 실패 + stack trace · CRITICAL: 시스템 정지 필요
- 외부 호출(데이터 API, 주문 전송)은 항상 로그 (요청 파라미터, 응답 요약, 결과, 소요 시간)
- 명백히 잘못된 레벨(예: `logger.error("시작합니다")`)만 리뷰 지적 — 미묘한 레벨 선택은 주관적이므로 지적하지 않는다
