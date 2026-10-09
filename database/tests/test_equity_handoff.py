"""인계 이력 읽기(`equity.handoff.load`) — 오류 분기와 읽은 값."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from equity import handoff

GOOD = {"date": "20260928", "basis": "morning",
        "equity_builds": {"price_daily": "m_20260929T000500_000000Z"},
        "stage_builds": {"stg_fin_wise": "m_20260928T231000_000000Z"},
        "health": {"stage": "ok", "equity": "ok"}}


@pytest.mark.parametrize(("text", "want"), [
    ('{"equity_builds": ', "읽지 못했다"),                                  # 깨진 JSON
    ('["m_20260929T000500_000000Z"]', "객체가 아니다"),                     # 객체가 아닌 JSON
    (json.dumps({k: v for k, v in GOOD.items() if k != "equity_builds"}), "equity_builds 가 없다"),
    (json.dumps({**GOOD, "equity_builds": {}}), "equity_builds 가 없다"),   # 비었다
], ids=["broken_json", "not_object", "equity_builds_absent", "equity_builds_empty"])
def test_load_refuses_unusable_history(tmp_path: Path, text: str, want: str) -> None:
    path = tmp_path / "20260928_morning.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(handoff.HandoffError, match=want) as e:
        handoff.load(path)
    assert str(path) in str(e.value)


def test_load_reads_builds_date_basis_and_health(tmp_path: Path) -> None:
    path = tmp_path / "20260928_morning.json"
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    h = handoff.load(path)
    assert (h.date, h.basis) == ("20260928", "morning")
    assert h.equity_builds == GOOD["equity_builds"] and h.stage_builds == GOOD["stage_builds"]
    assert h.health_ok
    path.write_text(json.dumps({**GOOD, "health": {"stage": "ok"}}), encoding="utf-8")
    assert not handoff.load(path).health_ok
