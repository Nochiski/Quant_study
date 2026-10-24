from __future__ import annotations

from collections.abc import Collection
from datetime import date
from typing import Protocol

from strategy_workbench.domain.equity.facade.research_data import SecurityRef


class SecurityDirectoryPort(Protocol):
    """종목의 이름·티커.

    `universe_securities`(lang2 P4-03 기준일 요약의 선정 종목 표시)의 입력은 관측 포트
    (`RawObservationQuery`)와 같은 시장·유니버스다. 시장·유니버스 → venue 대응은 관측 포트를
    구현한 어댑터가 이미 갖고 있으므로 호출자가 venue 를 따로 적지 않는다. 모르는 시장·유니버스면
    빈 튜플이다.
    """

    def universe_securities(
        self, market: str, universe_id: str, as_of: date
    ) -> tuple[SecurityRef, ...]: ...

    def describe_securities(self, security_ids: Collection[str]) -> tuple[SecurityRef, ...]:
        """종목 id 로 이름·티커를 찾는다(백테스트 결과의 보유·매매 표시).

        유니버스·날짜와 무관하다 — 결과에는 유니버스에서 빠진 뒤에도 들고 있던 종목이 남는다.
        모르거나 형식이 틀린 id 는 결과에서 빠지고, 결과는 id 오름차순이다.
        """
        ...
