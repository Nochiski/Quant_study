from __future__ import annotations

from datetime import date
from typing import Protocol

from strategy_workbench.domain.equity.facade.research_data import SecurityRef


class SecurityDirectoryPort(Protocol):
    """그날 유니버스 종목의 이름·티커(lang2 P4-03 기준일 요약의 선정 종목 표시).

    입력은 관측 포트(`RawObservationQuery`)와 같은 시장·유니버스다. 시장·유니버스 → venue 대응은
    관측 포트를 구현한 어댑터가 이미 갖고 있으므로 호출자가 venue 를 따로 적지 않는다. 모르는
    시장·유니버스면 빈 튜플이다.
    """

    def universe_securities(
        self, market: str, universe_id: str, as_of: date
    ) -> tuple[SecurityRef, ...]: ...
