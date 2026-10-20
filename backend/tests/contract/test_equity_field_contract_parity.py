"""mock·duckdb 두 Equity 어댑터의 필드 계약 대조 (#207).

테스트·e2e 는 `equity_mock`, 실사용은 `equity_duckdb` 를 쓴다. compile 은 연결된 어댑터의 필드
계약(`resolve_factor_fields`·`list_fields`)으로 단위 경고·field_missing·타입 검사를 하므로, 같은
field_id 의 단위나 값 타입이 두 어댑터에서 다르면 mock 으로 green 인 문서가 실데이터에서만 다르게
동작한다. 정본은 원장이다. mock 은 원장 스키마를 옮긴 duckdb 선언표(`FIELD_SPECS`)에 단위·값
타입을 맞추고, 선언표의 단위·빈도·값 타입과 mock 의 랙은 원장 `dataset_profile` 선언에 맞춘다.
단위·빈도의 어휘 차이는 아래 계약 대응표가 소유한다(#375 DR-A-13). 원천이 행을 고르는 필터도
원장 필드 선언과 원장이 싣는 사건 유형에 대조한다(#583).

대조는 선언표를 직접 읽는다 — 손 픽스처 루트의 `list_fields()` 는 그 루트에 원천 테이블이 있는
필드만 내므로, 새 `FieldSpec` 이 픽스처보다 먼저 들어오면 대조를 빠져나갈 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from strategy_workbench.adapters.outbound.equity_duckdb._specs import (
    FIELD_BY_ID,
    SOURCE_BY_NAME,
    UNSUPPORTED_FIELDS,
    FieldSpec,
    SourceSpec,
)
from strategy_workbench.adapters.outbound.equity_mock._fixture import (
    Membership,
    MockSplit,
    Observation,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.domain.equity._models import CONTRACT_PROSE_FIELDS
from strategy_workbench.domain.equity.facade.research_data import (
    CellKind,
    DatasetFieldProfile,
    FieldCoverageCapability,
    SecurityRef,
)

# 두 어댑터가 함께 내는 필드 — 대조가 빈 교집합으로 공허하게 통과하지 않게 최소 집합을 못박는다.
EXPECTED_SHARED = frozenset(
    {
        "price.close",
        "price.adj_close",
        "price.market_cap",
        "financial.book_equity",
        "financial.net_income",
        "consensus.forward_eps",
        "flow.foreign_net_buy",
        "credit.margin_balance",
    }
)


# 필드 계약 판에 닿는 dataclass 와 그 판에서 빠지는 문장 칸.
_PROSE_BY_TYPE: dict[type, set[str]] = {
    FieldSpec: {"label", "description", "disclosure_basis", "evidence", "lag_basis"},
    SourceSpec: {"lag_basis"},
    DatasetFieldProfile: {
        "label",
        "available_date_basis",
        "description",
        "disclosure_basis",
        "evidence",
    },
    **{
        data: set()
        for data in (FieldCoverageCapability, SecurityRef, Membership, Observation, MockSplit)
    },
}


@pytest.mark.parametrize(
    ("declaration", "prose"),
    list(_PROSE_BY_TYPE.items()),
    ids=[declaration.__name__ for declaration in _PROSE_BY_TYPE],
)
def test_only_prose_is_left_out_of_the_field_contract_revision(
    declaration: type, prose: set[str]
) -> None:
    """#291 리뷰 r2 P3-1: 판에서 빠지는 칸은 타입마다 이 문장 칸뿐이다.

    `CONTRACT_PROSE_FIELDS` 는 이름으로 거르므로 판에 닿는 dataclass(선언표·프로필·mock fixture
    데이터) 모두에 적용된다. 뜻 칸 이름이 목록에 들거나, 데이터 타입에 목록과 같은 이름의 뜻 칸이
    생기면 그 변화가 스냅샷 id·재현 지문·캐시 키에서 조용히 빠진다(#235 재발).
    """
    assert {item.name for item in fields(declaration)} & CONTRACT_PROSE_FIELDS == prose


def _mock_profiles() -> dict[str, DatasetFieldProfile]:
    return {profile.field_id: profile for profile in MockEquityDataAdapter.demo().list_fields()}


def _shared_field_ids() -> list[str]:
    return sorted(set(_mock_profiles()) & set(FIELD_BY_ID))


def test_공통_필드_집합이_비어_있지_않다() -> None:
    shared = set(_shared_field_ids())
    assert shared >= EXPECTED_SHARED, (
        f"mock·duckdb 공통 필드가 기대 집합보다 작다 — missing={sorted(EXPECTED_SHARED - shared)} "
        f"shared={sorted(shared)}"
    )


def test_원장_field_map_필드는_선언표나_미지원표_정확히_한쪽에_있다() -> None:
    """원장이 field_map 으로 선언한 필드마다 어댑터가 내주거나, 내주지 않는 사유를 적는다(#373).

    선언표 → 원장 방향만 보면 원장에 있는데 배선도 사유도 없는 필드가 조용히 남는다(도메인 리뷰 A
    DR-A-07 의 세 필드).
    """
    ledger = {
        profile.field_id for profile in _ledger_profiles().values() if profile.scope == "field_map"
    }
    declared, unsupported = set(FIELD_BY_ID), set(UNSUPPORTED_FIELDS)

    assert sorted(ledger - declared - unsupported) == []
    assert sorted(declared & unsupported) == []
    assert all(reason.strip() for reason in UNSUPPORTED_FIELDS.values())


# 사용자 설명에 새면 안 되는 개발 참조 — 이슈·문서 절·단계·결함 id, 백틱, 클래스 이름, 필드 id 가
# 아닌 밑줄 식별자(표·열·상수 이름). 다른 필드 id(예: price.adj_close)는 사용자가 고르는 이름이다.
_DEVELOPER_REFERENCE = re.compile(
    r"#\d|§|\bS\d{2}\b|GAP-|DEFECT-|DESIGN|내부 스코프|`|\b[A-Z][a-z]+[A-Z]|(?<![.\w])[A-Za-z]+_\w"
)
# 설명은 뜻과 한계 몇 문장이다. 개발 근거는 `evidence` 나 선언 옆 주석에 둔다.
_DESCRIPTION_MAX_CHARS = 240


def test_필드_설명은_개발_참조_없는_짧은_사용자_문장이다() -> None:
    """#373 DR-A-07: 계약 인스펙터·편집기 완성·AI `list_equity_fields` 가 설명을 그대로 보인다."""
    descriptions = {
        **{f"duckdb {spec.field_id}": spec.description for spec in FIELD_BY_ID.values()},
        **{f"mock {field_id}": p.description for field_id, p in _mock_profiles().items()},
    }
    leaked = {
        key: found
        for key, text in descriptions.items()
        if (found := _DEVELOPER_REFERENCE.findall(text))
    }
    too_long = {
        key: len(text) for key, text in descriptions.items() if len(text) > _DESCRIPTION_MAX_CHARS
    }
    assert (leaked, too_long) == ({}, {})


def test_mock_필드는_원장이_아는_id_만_쓴다() -> None:
    """mock 전용 필드는 duckdb 가 미지원 사유를 적어 둔 id 여야 한다.

    mock 이 원장에 없는 id 를 지어내면 그 필드를 쓰는 문서는 실데이터에서 field_missing 이 된다.
    """
    unknown = sorted(
        field_id
        for field_id in _mock_profiles()
        if field_id not in FIELD_BY_ID and field_id not in UNSUPPORTED_FIELDS
    )
    assert not unknown, f"원장 선언표에 없는 mock field_id — unknown={unknown}"


@pytest.mark.parametrize("field_id", _shared_field_ids())
def test_공통_필드의_단위와_값_타입이_같다(field_id: str) -> None:
    mock = _mock_profiles()[field_id]
    spec = FIELD_BY_ID[field_id]
    got = (mock.unit, mock.value_type)
    expected = (spec.unit, spec.value_type)
    assert got == expected, (
        f"mock 필드 계약이 원장 정본과 다르다 — field_id={field_id} "
        f"expected(unit, value_type)={expected} got={got}"
    )


def test_resolve_factor_fields_가_두_어댑터에서_같은_계약을_낸다(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """compile 이 실제로 읽는 포트 응답(`FieldMetadata`)까지 같아야 한다.

    원주가 표시(`adjusted_field_id`)도 포함한다 — compile 의 원주가 warning(BACKLOG-018)이 이 값을
    읽으므로 어댑터를 바꾸면 warning 이 켜지거나 꺼지면 안 된다.
    """
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root

    root: Path = tmp_path_factory.mktemp("field-parity") / "equity"
    duckdb_adapter = EquityDuckdbAdapter(build_workbench_root(root))
    shared = tuple(_shared_field_ids())
    mock_fields = {
        item.field_id: (item.unit, item.value_type, item.adjusted_field_id)
        for item in MockEquityDataAdapter.demo().resolve_factor_fields(shared).fields
    }
    duckdb_fields = {
        item.field_id: (item.unit, item.value_type, item.adjusted_field_id)
        for item in duckdb_adapter.resolve_factor_fields(shared).fields
    }
    missing = sorted(set(shared) - set(duckdb_fields))
    assert not missing, f"손 픽스처가 공통 필드를 다 내지 않는다 — missing={missing}"
    assert mock_fields == duckdb_fields


def test_duckdb_선언표의_원주가_표시는_같은_단위의_실재하는_조정_짝을_가리킨다() -> None:
    """compile 의 원주가 warning(BACKLOG-018)이 읽는 표시의 정본은 이 선언표다.

    duckdb extra 가 없는 CI 에서도 표시 자체가 지워지는 회귀를 잡는다(리뷰 #232 DEFECT-232-06).
    표시가 어댑터 응답까지 실리는지는 위 `resolve_factor_fields` 대조가 본다.
    """
    marked = {
        field_id: spec.adjusted_field_id
        for field_id, spec in FIELD_BY_ID.items()
        if spec.adjusted_field_id is not None
    }

    assert marked == {"price.close": "price.adj_close"}
    for raw, adjusted in marked.items():
        assert FIELD_BY_ID[adjusted].unit == FIELD_BY_ID[raw].unit
        assert FIELD_BY_ID[adjusted].adjusted_field_id is None, "조정 짝은 원주가가 아니다"


# ── 랙·빈도·값 타입 (#230) ─────────────────────────────────────────────────────
# 랙과 값 타입의 정본은 원장 `dataset_profile`(S19)이다. 원장 빌드가 그 표를 만드는 선언
# (`database/src/equity` 의 `rules_s*.FIELDS`)을 그대로 읽어 대조한다. 실원장 파일 없이 CI
# 에서 돈다.
# 빈도는 원장 어휘(session·report)가 아니라 duckdb 어댑터가 `list_fields()` 로 내는 어휘
# (`SourceSpec.frequency`)로 맞춘다. mock 이 대신 서는 것은 그 어댑터이기 때문이다.


def _ledger_fields() -> dict[str, tuple[str, Any]]:
    """원장 빌드 선언의 원천과 프로필. report 주기 해석에는 원천 맥락도 필요하다."""
    pytest.importorskip("duckdb", reason="원장 선언 모듈이 duckdb 를 import 한다(extra `equity`)")
    from tests.equity_fixture import import_ledger_module

    rules_s19: Any = import_ledger_module("equity.rules_s19")
    return {profile.field_id: (table, profile) for table, profile in rules_s19.owned_fields()}


def _ledger_profiles() -> dict[str, Any]:
    return {field_id: profile for field_id, (_, profile) in _ledger_fields().items()}


# 원장 → 워크벤치 계약의 어휘 대응이다. 값의 배율을 바꾸지 않는다. 새 단위·주기는 자동 통과하지
# 않는다. 특히 빈 단위가 항상 score인 것도, report가 항상 분기 보고인 것도 아니다.
_LEDGER_UNITS = {
    "pct": "pct",
    "KRW": "KRW",
    "억원": "억원",
    "원": "KRW",
    "주": "shares",
    "명": "count",
}
_CONTEXT_UNITS = {("consensus.recommendation", ""): "score"}
_LEDGER_FREQUENCIES = {"session": "daily", "monthly": "monthly", "event": "event"}
_CONTEXT_FREQUENCIES = {("fin_std", "report"): "quarterly", ("dividend_event", "report"): "annual"}


@pytest.mark.parametrize("field_id", sorted(FIELD_BY_ID))
def test_지원_필드의_단위가_원장_척도와_같다(field_id: str) -> None:
    _, ledger = _ledger_fields()[field_id]
    expected = _LEDGER_UNITS.get(ledger.unit, _CONTEXT_UNITS.get((field_id, ledger.unit)))
    actual = FIELD_BY_ID[field_id].unit
    assert expected is not None and actual == expected, (
        f"원장 단위와 어댑터 계약이 다르다 — field_id={field_id} "
        f"ledger_unit={ledger.unit!r} expected={expected!r} actual={actual!r}"
    )


@pytest.mark.parametrize("field_id", sorted(FIELD_BY_ID))
def test_지원_필드의_빈도가_원장_원천의_주기와_같다(field_id: str) -> None:
    table, ledger = _ledger_fields()[field_id]
    expected = _LEDGER_FREQUENCIES.get(
        ledger.frequency, _CONTEXT_FREQUENCIES.get((table, ledger.frequency))
    )
    actual = SOURCE_BY_NAME[FIELD_BY_ID[field_id].source].frequency.value
    assert expected is not None and actual == expected, (
        f"원장 주기와 어댑터 계약이 다르다 — field_id={field_id} table={table} "
        f"ledger_frequency={ledger.frequency!r} expected={expected!r} actual={actual!r}"
    )


@pytest.mark.parametrize("field_id", _shared_field_ids())
def test_공통_필드의_랙이_원장_선언과_같다(field_id: str) -> None:
    """mock 은 PIT 를 이 랙으로 흉내 낸다. 다르면 mock 에서 본 공개 시점이 실데이터와 어긋난다."""
    mock = _mock_profiles()[field_id]
    ledger = _ledger_profiles()[field_id]
    assert mock.recommended_lag_sessions == ledger.recommended_lag_sessions, (
        f"mock 랙이 원장 dataset_profile 과 다르다 — field_id={field_id} "
        f"expected={ledger.recommended_lag_sessions} got={mock.recommended_lag_sessions}"
    )


@pytest.mark.parametrize("field_id", _shared_field_ids())
def test_공통_필드의_빈도가_duckdb_어댑터와_같다(field_id: str) -> None:
    mock = _mock_profiles()[field_id]
    expected = SOURCE_BY_NAME[FIELD_BY_ID[field_id].source].frequency
    assert mock.frequency == expected, (
        f"mock 빈도가 duckdb 어댑터와 다르다 — field_id={field_id} "
        f"expected={expected} got={mock.frequency}"
    )


@pytest.mark.parametrize("field_id", _shared_field_ids())
def test_공통_필드는_가린_셀과_원천_생략_0_을_같게_선언한다(field_id: str) -> None:
    """원장 뷰가 가리는 원천(`SourceSpec.masked_expr`)의 필드는 mock 도 MASKED 를 선언한다(#298).
    원천이 행을 뺀 칸이 그날 0 인 원천(`SourceSpec.omitted_is_zero`)의 SOURCE_OMITTED_ZERO 도 같다
    (#371).

    MASKED 셀은 실행 결측 정책이 채우지 않는다. 한쪽만 선언하면 같은 문서·같은 결측 정책이 mock
    과 실데이터에서 다른 셀을 채운다. 원천 생략 0 을 한쪽만 내면 같은 창 연산의 커버가 갈린다.
    """
    kinds = _mock_profiles()[field_id].coverage.supported_cell_kinds
    source = SOURCE_BY_NAME[FIELD_BY_ID[field_id].source]
    mock = (CellKind.MASKED in kinds, CellKind.SOURCE_OMITTED_ZERO in kinds)
    duckdb = (source.masked_expr is not None, source.omitted_is_zero)
    assert mock == duckdb, (
        f"(MASKED, SOURCE_OMITTED_ZERO) 선언이 두 어댑터에서 다르다 — field_id={field_id} "
        f"duckdb={duckdb} mock={mock}"
    )


def test_원장_뷰의_가림_표시_열을_그_뷰를_읽는_원천이_읽는다() -> None:
    """원장이 선언한 가림 표시 열(`views.MASK_COLUMNS`)과 duckdb 원천의 `masked_expr` 가 같다
    (#311 리뷰 P2-1).

    한쪽만 있으면 가린 셀이 MISSING 으로 나가 실행 결측 정책이 다시 채운다 — 수정주가 가림을
    배선하지 않으면 `zero` 에서 가린 행이 끝점인 12-1 모멘텀이 −100% 가 된다(#301 실측 324셀).
    위 테스트가 mock 선언을 이 배선에 맞추므로 원장 뷰 → duckdb → mock 이 한 줄로 묶인다.
    """
    from tests.equity_fixture import import_ledger_module

    views: Any = import_ledger_module("equity.views")
    wired = {
        spec.relation: spec.masked_expr
        for spec in SOURCE_BY_NAME.values()
        if spec.masked_expr is not None
    }
    assert wired == views.MASK_COLUMNS, (
        f"원장 뷰의 가림 표시 열과 어댑터 배선이 다르다 — ledger={views.MASK_COLUMNS} "
        f"duckdb={wired}"
    )


# ── 행 필터 (#583) ────────────────────────────────────────────────────────────
# 원천의 `row_filter` 가 원장에 없는 값으로 거르면 질의는 오류 없이 행 0 을 읽어 필드가 조용히
# 비고, 단위·값 타입 대조로는 드러나지 않는다. 원장 선언을 직접 읽어 두 방향에서 본다.
_EVENT_TYPE_PREDICATE = re.compile(r"\bevent_type\s*(?:=\s*'[^']*'|IN\s*\([^)]*\))", re.IGNORECASE)


def _conjuncts(predicate: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s+AND\s+", predicate, flags=re.IGNORECASE)]


def test_원장_필드의_행_필터를_어댑터_원천이_그대로_건다() -> None:
    """원장 필드 선언의 행 필터는 그 필드 원천 `row_filter` 의 AND 항 하나와 글자까지 같다.

    어댑터는 그 뒤에 조건을 더 이어 좁힐 수 있다(컨센서스 FY1). 원장 필터가 없는 필드는 보지
    않는다 — 재무의 `period_frontier` 는 원장 뷰가 만든 열이다.
    """
    ledger = _ledger_profiles()
    expected = {
        field_id: profile.row_filter
        for field_id in FIELD_BY_ID
        if (profile := ledger.get(field_id)) is not None and profile.row_filter
    }
    actual = {
        field_id: SOURCE_BY_NAME[FIELD_BY_ID[field_id].source].row_filter or ""
        for field_id in expected
    }
    drift = {
        field_id: (want, actual[field_id])
        for field_id, want in expected.items()
        if want not in _conjuncts(actual[field_id])
    }

    assert expected, "원장 행 필터가 있는 지원 필드가 없다 — 대조가 공허하다"
    assert drift == {}, f"원장 행 필터와 어댑터 원천 필터가 다르다 — (ledger, adapter)={drift}"


def test_corp_event_를_거르는_유형은_원장이_싣는_사건_유형이다() -> None:
    """어댑터 원천과 손 픽스처가 쓰는 `event_type` 은 원장 `corp_event` 가 실제로 싣는 유형이다.

    선언만 되고 행을 만들지 않는 유형(`EVENT_TYPE_VOCAB` 의 나머지)으로 걸러도 필드가 비므로
    `LOADED_EVENT_TYPES` 와 대조한다. 손 픽스처가 어댑터의 틀린 값을 따라 적으면 어댑터 테스트가
    그 값으로 통과하므로 픽스처 행도 함께 본다(#583).
    """
    pytest.importorskip("duckdb", reason="원장 선언 모듈이 duckdb 를 import 한다(extra `equity`)")
    from tests.equity_fixture import WB_EVENT_ROWS, import_ledger_module

    rules_s05: Any = import_ledger_module("equity.rules_s05")
    loaded = set(rules_s05.LOADED_EVENT_TYPES)
    filtered = {
        literal
        for spec in SOURCE_BY_NAME.values()
        if spec.relation == rules_s05.CORP_EVENT.name and spec.row_filter
        for predicate in _EVENT_TYPE_PREDICATE.findall(spec.row_filter)
        for literal in re.findall(r"'([^']*)'", predicate)
    }
    written = {row[2] for row in WB_EVENT_ROWS}

    assert filtered, "corp_event 를 event_type 으로 거르는 원천이 없다 — 대조가 공허하다"
    assert (sorted(filtered - loaded), sorted(written - loaded)) == ([], []), (
        f"원장이 싣지 않는 event_type — (adapter, fixture) 밖 값, loaded={sorted(loaded)}"
    )


@pytest.mark.parametrize("field_id", sorted(FIELD_BY_ID))
def test_duckdb_선언표의_값_타입이_원장_선언과_같다(field_id: str) -> None:
    """`FIELD_SPECS` 는 원장 스키마를 옮긴 표다. 값 타입이 원장 `dataset_profile` 과 같아야 한다."""
    ledger = _ledger_profiles().get(field_id)
    assert ledger is not None, f"원장 dataset_profile 에 없는 field_id — field_id={field_id}"
    got = FIELD_BY_ID[field_id].value_type.value
    assert got == ledger.value_type, (
        f"FIELD_SPECS 값 타입이 원장 dataset_profile 과 다르다 — field_id={field_id} "
        f"expected={ledger.value_type} got={got}"
    )


def test_필드_프로파일과_선언표는_같은_팩터_값타입_변환을_쓴다() -> None:
    from dataclasses import replace

    from strategy_workbench.domain.equity.facade.research_data import FieldValueType
    from strategy_workbench.domain.factor.facade.expression import NodeValueType, field_metadata

    profile = _mock_profiles()["price.close"]
    spec = FIELD_BY_ID["price.close"]
    for value_type in FieldValueType:
        expected = (
            NodeValueType.GROUP_SERIES
            if value_type is FieldValueType.CATEGORY
            else NodeValueType.NUMERIC_SERIES
        )
        for declaration in (
            replace(profile, value_type=value_type),
            replace(spec, value_type=value_type),
        ):
            metadata = field_metadata(declaration, "price.adj_close")
            assert metadata.field_id == "price.close"
            assert metadata.unit == spec.unit
            assert metadata.value_type is expected
            assert metadata.adjusted_field_id == "price.adj_close"
