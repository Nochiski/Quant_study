"""공시 원문(document.xml ZIP) 수집 대상 — `dart_disclosure` 위의 술어 한 곳.

수집기(`backfill_docs.py`)가 우선순위마다 이 술어로 받을 공시를 고르고, 일일 DART 게이트(`daily.dart_daily`
documents)와 08:10 원장 건전성(`daily.ledger_health` dart.docs)이 같은 술어로 그날 받았어야 할 원문을 센다.
대상이 바뀌면 여기만 고친다 — 사본이 셋이던 때 잠정실적(N-64 P4)을 더하며 게이트 사본만 고쳐(괄호 결함 #655 도
그 사본에만 났다) 건전성 사본은 옛 대상 그대로였다(11-05).

import 해도 부작용이 없다(`.env` 를 요구하는 `api` 를 끌어오지 않는다) — 일일 모듈이 수집기를 import 하지 않는
이유가 그것이다(`daily.dart_daily` docstring).
"""
from __future__ import annotations

# 사업보고서 LIKE 매칭이 "사업보고서제출기한연장신고서" 를 물지 않게 막는다.
NOT_REPORT = "report_nm NOT LIKE '%연장신고%'"
# 연결·별도 잠정실적과 그 정정·첨부추가. 자회사 실적 공시는 상장사 본인 숫자가 아니라 뺀다
PRELIM = "(report_nm LIKE '%영업(잠정)실적(공정공시)%' AND report_nm NOT LIKE '%자회사%')"
# 우선순위 → (이름, 술어). 번호 순으로 받는다(중단돼도 가치 높은 것부터 — `backfill_docs` docstring)
PRIORITIES: dict[int, tuple[str, str]] = {
    1: ("분할·병합", ("(report_nm LIKE '%주식분할결정%' OR report_nm LIKE '%주식병합결정%') "
                    "AND stock_code <> ''")),
    2: ("사업보고서", f"report_nm LIKE '%사업보고서%' AND {NOT_REPORT} AND stock_code <> ''"),
    3: ("반기·분기", ("(report_nm LIKE '%반기보고서%' OR report_nm LIKE '%분기보고서%') "
                    f"AND {NOT_REPORT} AND stock_code <> ''")),
    4: ("잠정실적", f"{PRELIM} AND stock_code <> '' AND rcept_dt >= '20260701'"),
}
# 접수일 D(자리표시자 하나)에 받았어야 할 원문 — 우선순위 술어의 합
TARGETS_ON_DATE_SQL = ("SELECT DISTINCT rcept_no FROM dart_disclosure WHERE rcept_dt = ? AND ("
                       + " OR ".join(f"({cond})" for _, cond in PRIORITIES.values()) + ")")
