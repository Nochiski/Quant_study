---
paths:
  - "**/*.py"
  - "pyproject.toml"
---

# Python 설계 원칙

> `.claude/rules/code-style.md`의 "협업 가독성 룰 (Clean Code)" / "Pyright 사용 룰"을 보완한다. Python 코드 작성·리뷰 시 함께 따른다.

## 결과·실패는 위치형 튜플이 아니라 Result 값 타입으로 (errors-as-values)

함수가 **"값 + 상태(성공/실패/사유)"** 를 함께 반환할 때 `(value, bool)` / `(bool, value)` / `value | None` 같은 **위치형 튜플·센티넬**로 나르지 않는다. 이름 있는 단일 결과 타입(`@dataclass`/Pydantic)으로 반환한다.

**이 anti-pattern이 만드는 부채:**
- **arity churn**: 사유 하나 추가하려면 모든 호출부 언팩이 깨진다 (OCP 위반 — 호출부 수십 곳 결합)
- **순서 혼동**: 레이어마다 `(DataFrame, bool)` vs `(bool, list)`처럼 순서가 달라 silent 오언팩 (bug class)
- **lossy**: 실패를 `None`/`False`로 뭉개 사유가 사라진다 (진단 불가)
- **불가능 상태**: "성공인데 값 없음" 같은 조합이 타입상 표현 가능

**원칙:**
1. **errors-as-values (Result/Either)** — 예상된 도메인 실패(데이터 없음·기간 부족·검증 실패 등)는 예외가 아니라 **상태 enum을 가진 결과 값**으로. 종목/기간 루프에서 per-item 수집이 필요하면 특히 필수.
2. **불가능 상태 제거 (make illegal states unrepresentable)** — `ok` 여부와 값의 유무를 타입으로 묶는다.
3. **도메인 실패 ≠ 예외적 실패 (expected vs exceptional)** — 데이터 없음·검증 실패(예상)=결과 값 / NaN·shape 불일치·버그(예상 못함)=예외 또는 전용 `ERROR` 상태로 **경계 한 곳에서** 흡수한다. 예외 정책을 호출부마다 흩뿌리지 않는다. 실패를 silent `None`/기본값(0, 직전 값)으로 대체하는 것 금지 — 백테스트에서 조용한 값 대체는 성과 왜곡으로 직결된다.
4. **확장 안전 (OCP)** — 상태를 추가해도 `.ok`/`.value`/`.status`를 읽는 호출부는 안 깨진다.

```python
# Bad — 위치형 튜플: lossy + 순서혼동 + 사유 없음 + 호출부 수십 곳 결합
def load_prices(...) -> tuple[pd.DataFrame, bool]:
    return df, False          # 왜 실패? 모름

# Good — 이름 있는 Result 값 타입
class LoadStatus(Enum):
    OK = "ok"; NO_DATA = "no_data"; INSUFFICIENT_HISTORY = "insufficient_history"; SOURCE_ERROR = "source_error"

@dataclass(frozen=True, eq=False)   # DataFrame/ndarray 필드 → eq=False (원소별 __eq__/__hash__ 모호성 회피)
class LoadResult:
    frame: pd.DataFrame | None
    status: LoadStatus
    detail: str | None = None       # 예외 흡수 시 repr
    @property
    def ok(self) -> bool: return self.status is LoadStatus.OK

# 호출부: r = load_prices(...); if r.ok: use(r.frame)   — 사유는 r.status, 상태 추가에도 안 깨짐
```

**경계 주의:** Result 값 타입은 **도메인/공개 진입 함수**(데이터 로더, 시그널 계산 엔트리, 백테스트 러너 등)에만 둔다. 내부 벡터화 primitive(`compute_*` 등 배열/시리즈 일괄 연산)는 배열·Series 그대로 유지한다 — 벡터화 hot-loop를 보존하고 경계에서 1회만 wrap. 외부로 내보내는 리포트/직렬화 시점에는 `status`를 문자열/Literal로 변환한다.

> 명칭 레퍼런스: errors-as-values(Rust `Result`/FP `Either`) · make illegal states unrepresentable · primitive obsession 회피(value object) · expected vs exceptional errors.

## 관련 규칙

- 예외/로그 메시지 진단 디테일: `.claude/rules/error-messages.md`
- 코드 스타일·타입 체크: `.claude/rules/code-style.md`
