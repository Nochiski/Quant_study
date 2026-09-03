from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from strategy_workbench.domain.equity.facade.research_data import (
    DatasetFieldProfile,
    DataSnapshot,
)


@dataclass(frozen=True)
class FieldCatalogQuery:
    search: str | None = None
    dataset_ids: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    frequencies: tuple[str, ...] = ()
    page: int = 1
    page_size: int = 20

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError(f"catalog page must be >= 1 — page={self.page}")
        if not 1 <= self.page_size <= 100:
            raise ValueError(
                f"catalog page_size must be within [1, 100] — page_size={self.page_size}"
            )


@dataclass(frozen=True)
class FieldCatalogFacets:
    dataset_ids: tuple[str, ...]
    units: tuple[str, ...]
    frequencies: tuple[str, ...]


@dataclass(frozen=True)
class ResearchCatalog:
    snapshot: DataSnapshot
    fields: tuple[DatasetFieldProfile, ...]
    total: int
    page: int
    page_size: int
    page_count: int
    facets: FieldCatalogFacets


def build_research_catalog(
    *,
    snapshot: DataSnapshot,
    all_fields: tuple[DatasetFieldProfile, ...],
    query: FieldCatalogQuery,
) -> ResearchCatalog:
    fields = tuple(profile for profile in all_fields if _matches_query(profile, query))
    start = (query.page - 1) * query.page_size
    return ResearchCatalog(
        snapshot=snapshot,
        fields=fields[start : start + query.page_size],
        total=len(fields),
        page=query.page,
        page_size=query.page_size,
        page_count=ceil(len(fields) / query.page_size),
        facets=FieldCatalogFacets(
            dataset_ids=tuple(sorted({field.dataset_id for field in all_fields})),
            units=tuple(sorted({field.unit for field in all_fields})),
            frequencies=tuple(sorted({field.frequency for field in all_fields})),
        ),
    )


def _matches_query(profile: DatasetFieldProfile, query: FieldCatalogQuery) -> bool:
    searchable = " ".join(
        (
            profile.field_id,
            profile.dataset_id,
            profile.label,
            profile.description,
            profile.disclosure_basis,
            profile.evidence,
        )
    ).casefold()
    return (
        (query.search is None or query.search.strip().casefold() in searchable)
        and (not query.dataset_ids or profile.dataset_id in query.dataset_ids)
        and (not query.units or profile.unit in query.units)
        and (not query.frequencies or profile.frequency in query.frequencies)
    )
