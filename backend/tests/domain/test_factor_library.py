"""사용자 팩터 라이브러리의 버전 신원과 일치 판정(US-CS-08)."""

from __future__ import annotations

from datetime import UTC, datetime

from strategy_workbench.domain.factor_library.facade.library import (
    FactorMatchStatus,
    LibraryFactor,
    LibraryFactorVersion,
    match_factor,
    read_field_ids,
    read_parameter_ids,
    version_identity,
)

_AT = datetime(2026, 10, 19, tzinfo=UTC)
_GRAPH = {
    "nodes": [
        {"node_id": "close", "kind": "field", "field_id": "price.close"},
        {"node_id": "window", "kind": "parameter", "parameter_id": "win"},
        {"node_id": "volume", "kind": "field", "field_id": "price.volume"},
        {"node_id": "again", "kind": "field", "field_id": "price.close"},
    ],
    "output_node_id": "close",
}
_WIN = {"kind": "integer", "parameter_id": "win", "default": 20}
_UNREAD = {"kind": "integer", "parameter_id": "unused", "default": 1}


def _entry(key: str, *identities: str, hidden: bool = False) -> LibraryFactor:
    return LibraryFactor(
        library_id=f"lib-{key}",
        key=key,
        name=key,
        description="",
        hidden=hidden,
        created_at=_AT,
        versions=tuple(
            LibraryFactorVersion(
                version=index + 1,
                identity=identity,
                factor={"factor_id": key},
                parameters=(),
                created_at=_AT,
            )
            for index, identity in enumerate(identities)
        ),
    )


def test_graph_reads_fields_and_parameters_once_in_order() -> None:
    assert read_field_ids(_GRAPH) == ("price.close", "price.volume")
    assert read_parameter_ids(_GRAPH) == ("win",)


def test_identity_ignores_parameter_order_and_unread_parameters() -> None:
    base = version_identity(_GRAPH, [_WIN])
    assert version_identity(_GRAPH, [_UNREAD, _WIN]) == base
    assert version_identity(_GRAPH, [{**_WIN, "default": 30}]) != base
    assert version_identity({**_GRAPH, "output_node_id": "volume"}, [_WIN]) != base


def test_match_prefers_the_same_key_and_reports_three_statuses() -> None:
    a1, a2, b1 = "a" * 64, "b" * 64, "c" * 64
    entries = [_entry("alpha", a1, a2), _entry("beta", b1)]

    current = match_factor("alpha", a2, entries)
    outdated = match_factor("alpha", a1, entries)
    modified = match_factor("alpha", "d" * 64, entries)
    by_graph = match_factor("copied", b1, entries)

    assert current is not None and current.status is FactorMatchStatus.CURRENT
    assert outdated is not None and outdated.status is FactorMatchStatus.OUTDATED
    assert (outdated.matched_version, outdated.latest_version) == (1, 2)
    assert modified is not None and modified.status is FactorMatchStatus.MODIFIED
    assert modified.matched_version is None
    assert by_graph is not None and by_graph.library_id == "lib-beta"
    # 같은 키가 있으면 다른 항목의 같은 그래프로 넘어가지 않는다.
    keyed = match_factor("alpha", b1, entries)
    assert keyed is not None and keyed.status is FactorMatchStatus.MODIFIED
    assert match_factor("other", "e" * 64, entries) is None


def test_hidden_entries_never_match() -> None:
    assert match_factor("alpha", "a" * 64, [_entry("alpha", "a" * 64, hidden=True)]) is None
