"""매도 거래세 세율표와 실행 설정 → 엔진 세율 일정 변환(spec D7, 검증 랩 V2-01).

법정 세율의 owner 는 이 파일 하나다. 엔진은 받은 일정(`(시작일, bp)` 행)대로 매도 체결에만 부과하고
세율을 따로 알지 않는다.

세율은 매도 금액 대비 증권거래세와 농어촌특별세를 더한 값이다. 코스피는 증권거래세에 농어촌특별세
0.15%(농어촌특별세법 제5조 제1항 제5호)가 붙고, 코스닥은 증권거래세만 낸다. 아래 모든 구간에서 두
시장의 합계가 같아서 행을 시장 구분 없이 `Market.KRX` 하나로 둔다 — 원장 유니버스는 코스피·코스닥뿐
(`database/src/equity/rules_s03.py` 의 `MARKET_VOCAB`)이고, 합계가 다른 코넥스(0.10%)는 없다. 합계가
갈리는 시장이 유니버스에 들어오면 종목별 시장 구분을 엔진까지 배선해야 한다.

세법은 양도 시점(결제일, T+2)에 세율을 정하지만 엔진은 체결 세션 날짜로 찾는다. 그래서 시행일 직전
2거래일에 판 체결은 실제로는 새 세율을 냈는데 여기서는 옛 세율이 붙는다.
"""

from __future__ import annotations

from datetime import date

from ._models import Market, RunEnvironment, SellTax

# 시장별 `(시행일, 증권거래세 + 농어촌특별세 bp)`. 시행일 오름차순이고 첫 행은 원장 시작 이전부터다.
STATUTORY_SELL_TAX_BPS: dict[Market, tuple[tuple[date, float], ...]] = {
    Market.KRX: (
        # 2019-06-02 까지: 코스피 0.15% + 농특세 0.15%, 코스닥 0.30%.
        (date.min, 30.0),
        # 증권거래세법 시행령 제5조(대통령령 제29788호, 2019-05-28 공포):
        # 코스피 0.10%, 코스닥 0.25%.
        (date(2019, 6, 3), 25.0),
        # 같은 조(대통령령 제31290호, 2020-12-29 공포): 코스피 0.08%, 코스닥 0.23%.
        (date(2021, 1, 1), 23.0),
        # 같은 조(대통령령 제33209호, 2022-12-31 공포)의 단계 인하:
        # 2023년 코스피 0.05%·코스닥 0.20%, 2024년 0.03%·0.18%, 2025년 0%·0.15%.
        (date(2023, 1, 1), 20.0),
        (date(2024, 1, 1), 18.0),
        (date(2025, 1, 1), 15.0),
        # 같은 조(대통령령 제36001호, 2025-12-31 공포, 2026-01-01 시행): 코스피 0.05%, 코스닥 0.20%.
        (date(2026, 1, 1), 20.0),
    ),
}


def sell_tax_schedule(environment: RunEnvironment) -> tuple[tuple[date, float], ...]:
    """실행 설정의 거래세 방식을 엔진 세율 일정(`(시작일, bp)` 행, 시작일 오름차순)으로 바꾼다.

    `none` 은 빈 일정이다. 엔진은 일정이 비었거나 체결일보다 앞선 행이 없으면 세금을 매기지 않는다.
    """
    match environment.sell_tax:
        case SellTax.KRX_STATUTORY:
            return STATUTORY_SELL_TAX_BPS[environment.market]
        case SellTax.CUSTOM:
            if environment.sell_tax_bps is None:
                raise ValueError(
                    "custom sell tax without a rate — "
                    f"sell_tax={environment.sell_tax.value} universe_id={environment.universe_id!r}"
                )
            return ((date.min, environment.sell_tax_bps),)
        case SellTax.NONE:
            return ()
