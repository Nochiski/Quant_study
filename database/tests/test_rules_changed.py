"""판본별 '산출이 직접 바뀐 표' 선언(연구 DB S1-a) — stage·equity `RULES_CHANGED` 와 `changed_between`.

건전성 C4·조용한 손실 검사는 판본이 바뀐 날 이 선언(과 그 전파)에 든 표만 '규칙 변경'으로 빼고 나머지는 판본을
넘어 대조한다. 판본을 올리고 선언을 빠뜨리면 여기서 깨진다(규율).
"""
from types import ModuleType

import pytest
from equity import (
    __main__ as _cli,  # noqa: F401  # reason: 등록 부작용 — equity RULES 전수
)
from equity import model as em
from stage import model as sm
from stage import rules as sr


@pytest.mark.parametrize("layer", [sm, em], ids=["stage", "equity"])
def test_현재_판본은_선언의_키에_있다(layer: ModuleType) -> None:
    assert layer.RULES_VERSION in layer.RULES_CHANGED


def test_선언된_이름은_실제_표다() -> None:
    for declared, real in ((sm.RULES_CHANGED, set(sr.RULES)), (em.RULES_CHANGED, set(em.RULES))):
        for version, names in declared.items():
            assert names <= real, (version, sorted(names - real))


def test_changed_between_은_사이_판본_선언의_합이고_순서는_숫자_마디다() -> None:
    declared = {"1.8.0": frozenset({"a"}), "1.9.0": frozenset({"b"}), "1.10.0": frozenset({"c"})}
    assert sm.declared_changes(declared, "1.8.0", "1.10.0") == {"b", "c"}
    assert sm.declared_changes(declared, "1.9.0", "1.10.0") == {"c"}       # 문자열 순서면 1.10.0 < 1.9.0
    assert sm.declared_changes(declared, "1.7.0", "1.7.0") == frozenset()  # 같은 판본 — 선언 밖이어도 빈 집합
    assert sm.changed_between("2.9.0", "2.10.0") == sm.RULES_CHANGED["2.10.0"]


@pytest.mark.parametrize("prev, cur", [
    ("1.7.0", "1.9.0"),     # prev 가 선언 시작(1.8.0) 앞 — 사이 판본의 선언을 모른다
    ("1.9.0", "1.11.0"),    # cur 가 선언에 없다
    ("1.10.0", "1.9.0"),    # 되돌림
])
def test_changed_between_은_선언이_빠지면_None(prev: str, cur: str) -> None:
    declared = {"1.8.0": frozenset({"a"}), "1.9.0": frozenset({"b"}), "1.10.0": frozenset({"c"})}
    assert sm.declared_changes(declared, prev, cur) is None
