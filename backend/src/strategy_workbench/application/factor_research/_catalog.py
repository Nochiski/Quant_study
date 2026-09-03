from __future__ import annotations

from dataclasses import dataclass

from strategy_workbench.domain.factor.facade.registry import (
    FactorAvailability,
    FactorCategory,
    FactorDefinition,
    FactorRegistry,
)


@dataclass(frozen=True)
class FactorCatalogQuery:
    search: str | None = None
    categories: tuple[FactorCategory, ...] = ()
    availability: tuple[FactorAvailability, ...] = ()
    page: int = 1
    page_size: int = 20


@dataclass(frozen=True)
class FactorCatalogFacets:
    categories: tuple[FactorCategory, ...]
    availability: tuple[FactorAvailability, ...]
    output_units: tuple[str, ...]


@dataclass(frozen=True)
class FactorCatalog:
    registry_version: str
    factors: tuple[FactorDefinition, ...]
    total: int
    page: int
    page_size: int
    page_count: int
    facets: FactorCatalogFacets


def build_factor_catalog(registry: FactorRegistry, query: FactorCatalogQuery) -> FactorCatalog:
    definitions = registry.all()
    needle = query.search.strip().casefold() if query.search else None
    filtered = tuple(
        definition
        for definition in definitions
        if (
            needle is None
            or needle in definition.factor_id.casefold()
            or needle in definition.label.casefold()
            or needle in definition.description.casefold()
            or any(needle in tag.casefold() for tag in definition.tags)
        )
        and (not query.categories or definition.category in query.categories)
        and (not query.availability or definition.availability in query.availability)
    )
    page_count = (len(filtered) + query.page_size - 1) // query.page_size
    offset = (query.page - 1) * query.page_size
    return FactorCatalog(
        registry_version=registry.version,
        factors=filtered[offset : offset + query.page_size],
        total=len(filtered),
        page=query.page,
        page_size=query.page_size,
        page_count=page_count,
        facets=FactorCatalogFacets(
            categories=tuple(FactorCategory),
            availability=tuple(FactorAvailability),
            output_units=tuple(sorted({item.output_unit for item in definitions})),
        ),
    )
