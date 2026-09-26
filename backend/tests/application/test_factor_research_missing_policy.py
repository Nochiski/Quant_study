"""P2-02 리뷰 2차: 팩터 sandbox 의 preview 경로가 문서의 결측 정책으로 값을 계산한다.

`explain` 과 `preview` 는 서로 다른 코드 경로이고, preview 는 표시값만이 아니라 팩터 **값
자체**를 계산해 돌려준다. plan 컴파일과 평가가 각각 따로 정책을 해소하므로, 한쪽만 되돌아가면
plan 은 `zero` 인데 값은 `drop` 으로 계산한 자기모순 응답이 나간다. mock 어댑터의 관측에는
결측 셀이 없어 HTTP 통합 테스트로는 이 갈림을 볼 수 없으므로, 결측 셀을 담은 이중체 포트로
값 수준에서 고정한다.
"""

from __future__ import annotations

from datetime import date

from strategy_workbench.application.factor_research.facade.ports import (
    FactorMetadataSnapshot,
    FactorObservationQuery,
    FactorObservationSet,
)
from strategy_workbench.application.factor_research.facade.research import (
    FactorPreviewRequest,
    FactorResearchService,
)
from strategy_workbench.domain.factor.facade.evaluation import (
    FactorFieldValue,
    FactorObservation,
)
from strategy_workbench.domain.factor.facade.expression import (
    FactorGraph,
    FieldMetadata,
    FieldNode,
    MissingPolicy,
)
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry

SNAPSHOT = "fake-snapshot-v1"
FIELD = "price.close"


class _SparseSource:
    """한 종목의 값이 비어 있는 결정적 관측 한 벌. 결측 정책이 값에 드러나게 한다."""

    def resolve_factor_fields(self, field_ids: tuple[str, ...]) -> FactorMetadataSnapshot:
        return FactorMetadataSnapshot(
            data_snapshot_id=SNAPSHOT,
            fields=tuple(
                FieldMetadata(field_id, "KRW", available_history_sessions=10)
                for field_id in field_ids
            ),
        )

    def load_factor_observations(self, query: FactorObservationQuery) -> FactorObservationSet:
        return FactorObservationSet(
            data_snapshot_id=SNAPSHOT,
            observations=(
                FactorObservation(
                    as_of=date(2024, 1, 8),
                    security_id="005930",
                    fields=(FactorFieldValue(FIELD, 100.0),),
                ),
                FactorObservation(
                    as_of=date(2024, 1, 8),
                    security_id="000660",
                    fields=(FactorFieldValue(FIELD, None),),
                ),
            ),
        )


def _graph(missing_policy: MissingPolicy) -> FactorGraph:
    return FactorGraph(
        nodes=(FieldNode("close", FIELD, "field"),),
        output_node_id="close",
        missing_policy=missing_policy,
    )


def _preview(graph: FactorGraph, missing: MissingPolicy | None) -> tuple[str, list[float | None]]:
    source = _SparseSource()
    service = FactorResearchService(build_default_factor_registry(), source, source)
    request = FactorPreviewRequest(
        graph=graph,
        as_of_start=date(2024, 1, 8),
        as_of_end=date(2024, 1, 8),
        missing=missing,
    )
    preview = service.preview(request)
    return preview.plan.missing_policy, [value.value for value in preview.evaluation.values]


def test_preview_values_follow_the_document_missing_policy() -> None:
    dropped = _preview(_graph(MissingPolicy.DROP), None)
    zeroed = _preview(_graph(MissingPolicy.ZERO), None)

    assert dropped == ("drop", [100.0, None])
    # plan 과 값이 같은 정책을 읽는다: 한쪽만 되돌아가면 이 단언이 깨진다.
    assert zeroed == ("zero", [100.0, 0.0])


def test_preview_prefers_the_explicit_missing_over_the_document() -> None:
    explicit = _preview(_graph(MissingPolicy.ZERO), MissingPolicy.DROP)

    assert explicit == ("drop", [100.0, None])
