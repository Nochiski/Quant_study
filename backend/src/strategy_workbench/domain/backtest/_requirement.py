"""실행 설정 필수 규칙 — 1.2 문서는 실행 설정을 요청에서만 받는다(spec D6, P2-03).

1.1 까지는 `data`·`execution`·`graph.missing_policy` 로 실행 설정을 만드는 브리지(`_bridge.py`)가
있었다. 1.2 가 그 세 자리를 전략 문서에서 지우면서 브리지의 입력이 사라졌고, 은퇴 버전 문서는
업그레이더(P2-09)를 거쳐서만 들어온다. 그래서 남는 규칙은 하나다: **실행 설정은 요청이 싣는다.**

판정을 서비스마다 다시 쓰지 않도록 이 한 곳에 둔다. 세 실행 경로(preview·trace·run)가 같은
코드·같은 문장으로 거절해야 프론트가 하나의 코드만 번역한다.
"""

from __future__ import annotations

from ._models import RunEnvironment


class MissingRunEnvironmentError(ValueError):
    """실행 요청에 실행 설정이 없다(spec D3·D6).

    1.1 문서는 시장·기간·유니버스·체결을 문서 안에 갖고 있어서 요청이 비어도 실행이 됐다.
    1.2 문서에는 그 값이 없으므로 여기서 기본값을 지어내면 사용자가 지정한 적 없는 기간·유니버스로
    백테스트가 돌고 매니페스트에는 그 값이 사실로 기록된다. 조용한 기본값 대신 거절한다.
    """

    code = "run_environment.required"

    def __init__(self, *, requested_by: str) -> None:
        super().__init__(
            "실행 설정이 없어 실행할 수 없다 — "
            f"code={self.code} requested_by={requested_by} "
            "expected=요청 본문의 environment(시장·기간·유니버스·체결·비용·결측) got=None "
            "— schema 1.2 문서는 실행 설정을 담지 않는다. 실행 설정을 지정하라"
        )
        self.requested_by = requested_by


def require_environment(environment: RunEnvironment | None, *, requested_by: str) -> RunEnvironment:
    """요청이 실은 실행 설정을 확정한다. 없으면 코드화된 진단으로 거절한다.

    Args:
        environment: 요청 본문의 실행 설정.
        requested_by: 진단 메시지에 실을 호출 경로 이름(`portfolio.preview` 등).

    Raises:
        MissingRunEnvironmentError: `environment` 가 `None` 일 때.
    """
    if environment is None:
        raise MissingRunEnvironmentError(requested_by=requested_by)
    return environment
