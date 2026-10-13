"""연구 구간 잠금 — 측정은 봉인 구간 뒤에서만 한다(spec D1, V1-01).

봉인 구간 2016-01-01 ~ 2019-12-31 은 저장소에서 아직 아무도 결과를 보지 않은 유일한 창이라
홀드아웃 개봉(D11)으로만 연다. 연구용 측정은 봉인 뒤 첫 거래일부터 하고, 봉인 앞(2010~2015)도
측정하지 않는다. 그래서 판정은 "측정 구간 시작일 ≥ 연구 하한" 하나다. 지표 계산에 필요한 과거
관측(워밍업, `RawObservationQuery.history_sessions_before_start`)은 측정이 아니므로 이 판정을 받지
않는다.

`RunEnvironment.__post_init__` 에 넣지 않는다. 엔진 직접 테스트·벤치 스크립트·은퇴 문서 업그레이드
응답이 2020 이전 환경을 값으로 만들 수 있어야 한다. 판정은 실행 관문 `require_environment` 가 한다.
"""

from __future__ import annotations

from datetime import date

SEALED_START = date(2016, 1, 1)
SEALED_END = date(2019, 12, 31)
RESEARCH_START = date(2020, 1, 2)


class ResearchWindowViolationError(ValueError):
    """측정 구간 시작일이 연구 하한보다 앞이다.

    봉인 구간·연구 하한을 속성으로 실어, 거절을 다른 계약(HTTP detail)으로 옮기는 쪽이 날짜를 다시
    적지 않게 한다.
    """

    code = "run_environment.research_window"
    sealed_start = SEALED_START
    sealed_end = SEALED_END
    research_start = RESEARCH_START

    def __init__(self, *, start: date, requested_by: str) -> None:
        super().__init__(
            "측정 시작일이 연구 구간 밖이라 실행할 수 없다 — "
            f"code={self.code} requested_by={requested_by} "
            f"expected=start>={RESEARCH_START} got=start={start} "
            f"— {SEALED_START}~{SEALED_END}은 홀드아웃 봉인 구간이고 그 앞 구간도 측정하지 않는다. "
            f"시작일을 {RESEARCH_START} 이후로 옮겨라"
        )


def require_research_window(start: date, *, requested_by: str) -> None:
    """측정 구간 시작일이 연구 하한 이후인지 판정한다.

    Raises:
        ResearchWindowViolationError: `start` 가 연구 하한보다 앞일 때.
    """
    if start < RESEARCH_START:
        raise ResearchWindowViolationError(start=start, requested_by=requested_by)
