"""체결 후보와 확정 체결이 공유하는 자본·총노출 장부 투영."""

from decimal import Decimal


def project_valuation(
    equity: float,
    gross: float,
    old: Decimal,
    new: Decimal,
    old_mark: float,
    price: float,
    cost: float,
) -> tuple[float, float]:
    return (
        equity + (float(old) * (price - old_mark) - cost),
        gross + (float(abs(new)) * price - float(abs(old)) * old_mark),
    )
