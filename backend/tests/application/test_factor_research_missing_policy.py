"""팩터 sandbox 의 preview 경로가 plan 과 값에 같은 결측 정책을 쓴다.

`explain` 과 `preview` 는 서로 다른 코드 경로이고, preview 는 표시값만이 아니라 팩터 **값
자체**를 계산해 돌려준다. plan 컴파일과 평가가 각각 따로 정책을 해소하므로, 한쪽만 다른 값을
읽으면 plan 은 `zero` 인데 값은 `drop` 으로 계산한 자기모순 응답이 나간다. mock 어댑터의
관측에는 결측 셀이 없어 HTTP 통합 테스트로는 이 갈림을 볼 수 없으므로, 결측 셀을 담은 이중체
포트로 값 수준에서 고정한다.

schema 1.2 부터 그래프에는 결측 정책이 없다(P2-03). 요청이 생략하면 실행 설정과 같은 기본값
(`DEFAULT_MISSING_POLICY`)으로 떨어진다 — 편집 화면의 실행 플랜 패널과 실제 실행이 같은
`plan_hash` 를 보이려면 두 기본값이 한 상수여야 한다.
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
from strategy_workbench.domain.backtest.facade.environment import DEFAULT_MISSING_POLICY
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


def _graph() -> FactorGraph:
    return FactorGraph(nodes=(FieldNode("close", FIELD, "field"),), output_node_id="close")


def _preview(missing: MissingPolicy | None) -> tuple[str, list[float | None]]:
    source = _SparseSource()
    service = FactorResearchService(build_default_factor_registry(), source, source)
    request = FactorPreviewRequest(
        graph=_graph(),
        as_of_start=date(2024, 1, 8),
        as_of_end=date(2024, 1, 8),
        missing=missing,
    )
    preview = service.preview(request)
    return preview.plan.missing_policy, [value.value for value in preview.evaluation.values]


def test_preview_without_an_explicit_missing_uses_the_run_default() -> None:
    assert _preview(None) == (DEFAULT_MISSING_POLICY.value, [100.0, None])


def test_preview_plan_and_values_read_the_same_explicit_missing() -> None:
    dropped = _preview(MissingPolicy.DROP)
    zeroed = _preview(MissingPolicy.ZERO)

    assert dropped == ("drop", [100.0, None])
    # plan 과 값이 같은 정책을 읽는다: 한쪽만 다른 값을 읽으면 이 단언이 깨진다.
    assert zeroed == ("zero", [100.0, 0.0])
