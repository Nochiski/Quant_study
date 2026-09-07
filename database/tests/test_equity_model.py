"""T0.1 — `equity/model.py` 의 선언 타입. RULES 없이 dataclass 단위로 돈다."""
from __future__ import annotations

from pathlib import Path

import pytest
from equity import model
from equity.model import BASIS_VOCAB, PARTITION_CLASSES, EquityTable

SQL = Path("/tmp/does-not-matter.sql")


def _table(**over: object) -> EquityTable:
    kw: dict[str, object] = {
        "name": "t", "grain": ("k",),
        "columns": {"k": "BIGINT", "available_date": "DATE", "available_basis": "VARCHAR"},
        "inputs": ("stg_x",), "partition_class": "whole", "partition_key_expr": None,
        "available_rule": "column:available_date",
        "eg1_lhs_sql": "SELECT count(*) FROM out_pq",
        "eg1_rhs_sql": "SELECT count(*) FROM stg_x", "sql_path": SQL}
    kw.update(over)
    return EquityTable(**kw)   # pyright: ignore[reportArgumentType]  # reason: 테스트 팩토리


def test_grain_컬럼이_columns에_있다() -> None:
    assert _table().grain == ("k",)
    with pytest.raises(ValueError, match="grain column not declared"):
        _table(grain=("k", "없는컬럼"))


def test_partition_class_어휘_폐쇄() -> None:
    assert PARTITION_CLASSES == ("date_axis", "receipt_axis", "whole")
    for pc in ("date_axis", "receipt_axis"):
        assert _table(partition_class=pc, partition_key_expr="year(k)").partition_class == pc
    with pytest.raises(ValueError, match="unknown partition_class"):
        _table(partition_class="month_axis", partition_key_expr="month(k)")


def test_available_basis_어휘_폐쇄() -> None:
    assert BASIS_VOCAB == ("measured", "derived", "convention", "default", "unknown")
    assert _table(available_basis=("measured", "derived")).basis_vocab == ("measured", "derived")
    assert _table().basis_vocab == BASIS_VOCAB          # 미선언이면 전체 어휘
    with pytest.raises(ValueError, match="available_basis outside vocabulary"):
        _table(available_basis=("guessed",))


def test_partition_column은_whole일때만_None() -> None:
    assert _table(partition_class="whole", partition_key_expr=None).partition_key_expr is None
    with pytest.raises(ValueError, match="partition_key_expr is required"):
        _table(partition_class="date_axis", partition_key_expr=None)
    with pytest.raises(ValueError, match="whole 파티션은"):
        _table(partition_class="whole", partition_key_expr="year(k)")


def test_차원테이블은_available_rule이_none() -> None:
    assert _table().is_fact is True
    assert _table(available_rule=model.AVAILABLE_NONE).is_fact is False


def test_content_date_column은_선언_컬럼이어야_한다() -> None:
    assert _table(content_date_column="k").content_date_column == "k"
    with pytest.raises(ValueError, match="content_date_column not declared"):
        _table(content_date_column="date")


def test_input_columns는_선언한_입력만_가리킨다() -> None:
    assert _table(input_columns={"stg_x": ("k",)}).declared_columns("stg_x") == ("k",)
    assert _table().declared_columns("stg_x") == ()
    with pytest.raises(ValueError, match="input_columns declares a table that is not an input"):
        _table(input_columns={"stg_y": ("k",)})


def test_fill_kind_어휘_폐쇄() -> None:
    assert model.FILL_KINDS == ("measured", "src_omitted", "empty_response", "not_collected")
    assert model.FILL_EVIDENCE == ("shard_done", "unit_ok", "shard_empty", "unit_empty", "none")


def test_레지스트리는_rules_모듈이_채운다() -> None:
    from equity import rules_sample

    assert model.RULES[rules_sample.SAMPLE_TABLE.name] is rules_sample.SAMPLE_TABLE
    with pytest.raises(ValueError, match="duplicate equity table registration"):
        model.register(_table(name=rules_sample.SAMPLE_TABLE.name))
