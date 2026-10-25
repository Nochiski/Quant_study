"""불변 실행 지표와 현재 계열 통계의 관측 시점을 구분하는 읽기 전용 응답."""

from dataclasses import dataclass, field
from datetime import datetime

from strategy_workbench.domain.analytics.facade.metrics import MetricValue
from strategy_workbench.domain.experiment.facade.statistics import DeflatedSharpeEvidence


@dataclass(frozen=True)
class RunValidationReport:
    """PSR은 저장된 FULL 지표(없으면 미기록), DSR은 observed_at에 조회한 원장 기준이다."""

    run_id: str
    run_fingerprint: str
    metric_registry_version: str
    observed_at: datetime = field(
        metadata={
            "description": (
                "application 시계로 기록한 현재 계열 원장 조회 시점. 완료/선택 시각이 아니며 "
                "동시 쓰기와의 DB 스냅숏 격리를 추가하지 않는다."
            )
        }
    )
    psr: MetricValue | None = field(
        metadata={
            "description": (
                "저장된 FULL PSR MetricValue 그대로. null은 미기록이고 "
                "MetricValue.value null은 원래 부재 사유를 따른다. 재계산하지 않는다."
            )
        }
    )
    dsr: DeflatedSharpeEvidence
