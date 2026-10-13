"""카탈로그 페이지 요청 검증·창 계산의 단일 정본(#375 DR-A-14)."""

import pytest

from strategy_workbench.application.equity_workspace.facade.workspace import FieldCatalogQuery
from strategy_workbench.application.factor_research.facade.research import FactorCatalogQuery
from strategy_workbench.domain.paging.facade.page import MAX_PAGE_SIZE, paginate


def test_window_counts_pages_and_keeps_total_past_the_last_page() -> None:
    items = tuple(range(45))
    assert paginate(items, 3, 20).items == tuple(range(40, 45))
    assert paginate(items, 3, 20).page_count == 3
    beyond = paginate(items, 4, 20)
    assert (beyond.items, beyond.total, beyond.page_count) == ((), 45, 3)
    assert paginate((), 1, 20).page_count == 0


@pytest.mark.parametrize("page,page_size", [(0, 20), (1, 0), (1, MAX_PAGE_SIZE + 1)])
def test_both_catalog_queries_reject_the_same_invalid_page(page: int, page_size: int) -> None:
    # 팩터 카탈로그 쿼리는 검증이 없어 page=0 이 음수 offset 으로 엉뚱한 창을, page_size=0 이
    # 0 나눗셈을 냈다. 두 카탈로그가 같은 정본으로 거절한다.
    with pytest.raises(ValueError, match="catalog page"):
        FactorCatalogQuery(page=page, page_size=page_size)
    with pytest.raises(ValueError, match="catalog page"):
        FieldCatalogQuery(page=page, page_size=page_size)
