"""Declared dependencies for application.stock_chart.

개별종목차트: 종목·지수 검색, 캔들, 국면 판정·채널·되돌림·수평 구간·중첩 후보, 선 저장.
일봉은 자기 outgoing `ChartPricePort`로, 선은 `ChartDrawingStore`로만 읽고 쓴다. 묶기·배율·
분석 규칙과 사용자 대면 문장은 `domain.chart_analysis`가 소유하고 여기서는 조립·좌표 변환
(날짜 ↔ 봉 index, 화면 ↔ 저장 가격)만 한다. 다른 application 노드를 import하지 않는다.
"""

DEPENDS_ON: tuple[str, ...] = ("domain.chart_analysis",)
