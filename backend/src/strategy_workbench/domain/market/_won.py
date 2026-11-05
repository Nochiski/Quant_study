"""문장에 넣는 금액 읽기("1조 4,542억"). 화면 숫자 표기(조·억 소수)는 frontend가 따로 소유한다."""

from __future__ import annotations

EOK = 100_000_000
JO = 10_000 * EOK


def won_words(amount_krw: int) -> str:
    """양수 원 금액을 억 단위로 반올림(반은 올림 — 화면 표기와 같다)해 읽는다. 1억 미만은 '1억 원
    미만'이 아니라 '1억 미만'."""
    eok = (abs(amount_krw) + EOK // 2) // EOK
    if eok == 0:
        return "1억 미만"
    jo, rest = divmod(eok, 10_000)
    if jo == 0:
        return f"{rest:,}억"
    return f"{jo:,}조" if rest == 0 else f"{jo:,}조 {rest:,}억"
