"""ETF 유형 자동 분류 — 이름 · 기초지수 이름 낱말 규칙(시험, DECISIONS §7 #10 · 샘플 28).

`classify` = (유형, 세부) — 요약 · 목록 표. `classify2` = 시계열용 큰 유형 — 레버리지 · 인버스를
레버리지(롱) · 인버스로 나눈다. 규칙 순서가 뜻이다(앞 규칙이 이긴다): 레버리지 · 인버스 → 단기자금 ·
금리 → 혼합 → 채권 → 원자재 · 통화 → 리츠 → 해외주식(이름에 코리아 · 한국 · K- 등이 있으면 국내) →
국내주식 세부(전략 · 반도체·IT · 시장대표 · 업종 · 테마).
"""
from __future__ import annotations

import re

FOREIGN = re.compile(
    r"미국|S&P|NASDAQ|나스닥|다우존스|Dow Jones|차이나|중국|China|CSI ?\d|항셍|홍콩|일본|Japan|닛케이|"
    r"Nikkei|TOPIX|인도|India|Nifty|베트남|VN30|유럽|Europe|STOXX|글로벌|Global|선진국|MSCI World|ACWI|"
    r"신흥국|Emerging|대만|Taiwan|PHLX|Indxx|Philadelphia|필라델피아|독일|DAX|멕시코|브라질|라틴|영국|FTSE|"
    r"\bICE\b|NYSE|Morningstar|빅테크|Magnificent|테슬라|엔비디아|애플|마이크로소프트|구글|아마존|팔란티어|"
    r"브로드컴|\bUS\b|U\.S")
_SECTOR_WORDS = r"반도체|2차전지|바이오|조선|방산|원자력|원전|전력|은행|금융|증권|자동차|게임|인터넷|미디어|엔터"
_MARKET_BRANDS = (r"^(KODEX|TIGER|RISE|ACE|SOL|HANARO|KIWOOM|PLUS|1Q|TIME|WON|BNK|UNICORN|마이티|FOCUS|"
                  r"파워|KCGI|DAISHIN343|에셋플러스|VITA|TRUSTON|더제이|히어로즈|ITF|KOSEF|ARIRANG|KBSTAR)"
                  r"\s*(200|200TR|코스닥150|KRX300|MSCI Korea|코스피|200 TR)")
_MARKET_INDEXES = (r"^(코스피 200|코스피 200 TR|코스피|코스닥 150|KRX 300|MSCI Korea TR Index|MSCI Korea|"
                   r"코스피 100|코스피 50|코스닥 150 TR|KRX 100)$")
_DOMESTIC_SUBS = (
    ("2차전지", r"2차전지|배터리|리튬|양극재|이차전지"),
    ("바이오·헬스", r"바이오|헬스|제약|의료|헬스케어"),
    ("조선·방산·원전·전력", r"조선|방산|원자력|원전|전력|에너지인프라|중공업|K-방산|우주"),
    ("금융", r"은행|금융|증권|보험|지주"), ("자동차", r"자동차|모빌리티|현대차"),
    ("인터넷·게임·콘텐츠", r"인터넷|게임|미디어|엔터|K-콘텐츠|콘텐츠|플랫폼|K팝|웹툰"), ("그룹주", r"그룹"))
STRATEGY = "전략(배당·커버드콜·밸류업)"


def classify(name: str, idx: str) -> tuple[str, str]:
    """(유형, 세부). 세부는 국내주식 · 해외주식만."""
    t = f"{name} {idx}"
    if re.search(r"레버리지|인버스|2X|곱버스|-1X|\(-1\)", t):
        return ("레버리지·인버스", "")
    if re.search(r"MMF|머니마켓|CD금리|CD Index|CD\)|CD 1Y|KOFR|SOFR|Cash|단기채|초단기|단기통안|콜금리|"
                 r"금리액티브|금리투자|1년은행|전단채|MSB", t):
        return ("단기자금·금리", "")
    if re.search(r"혼합|TDF|TRF|자산배분|Target Date|타겟데이트", t):
        return ("혼합·자산배분", "")
    if re.search(r"채권|국고채|국채|회사채|Treasury|국공채|통안|크레딧|TIPS|물가채|Bond|금융채|여전채|"
                 r"은행채|하이일드|AA-|A-이상", t):
        return ("채권", "")
    if re.search(r"금현물|골드|Gold|금선물|은선물|국제금|Silver|원유|WTI|Crude|구리|Copper|농산물|달러선물|"
                 r"미국달러|엔선물|원자재|Commodity|천연가스", t) and not re.search(r"금채굴|Miners", t):
        return ("원자재·통화", "")
    if re.search(r"리츠|REIT|부동산", t):
        return ("리츠·부동산", "")
    domestic = (re.search(r"코리아|한국|K-|K방산|K반도체|K리츠|200|코스피|코스닥", name)
                and not re.search(r"미국|중국|차이나|일본|인도|베트남|대만|글로벌|선진국|유럽|러셀", name))
    if FOREIGN.search(t) and not domestic:
        if re.search(r"미국|S&P|NASDAQ|나스닥|다우|Dow|PHLX|Indxx|Philadelphia|필라델피아|NYSE|테슬라|"
                     r"엔비디아|애플|마이크로소프트|구글|아마존|팔란티어|브로드컴|Magnificent|빅테크", t, re.I):
            return ("해외주식", "미국")
        if re.search(r"차이나|중국|China|CSI|항셍|홍콩", t, re.I):
            return ("해외주식", "중국·홍콩")
        return ("해외주식", "기타 해외")
    if (re.search(r"커버드콜|배당|밸류업|고배당|로우볼|퀄리티|동일가중", t)
            and not re.search(_SECTOR_WORDS, t)):
        return ("국내주식", STRATEGY)
    if re.search(r"정보기술|200 IT|코리아테크|반도체|HBM|하이닉스|삼성전자", t):
        return ("국내주식", "반도체·IT")
    if re.search(_MARKET_INDEXES, idx.strip()) or re.search(_MARKET_BRANDS, name):
        return ("국내주식", "시장대표")
    if (re.search(r"커버드콜|배당|밸류업|고배당|로우볼|퀄리티|가치|모멘텀|동일가중|액티브$", t)
            and not re.search(_SECTOR_WORDS, t)):
        return ("국내주식", STRATEGY)
    for k, pat in _DOMESTIC_SUBS:
        if re.search(pat, t):
            return ("국내주식", k)
    return ("국내주식", "기타 섹터·테마")


def classify2(name: str, idx: str) -> tuple[str, str]:
    """시계열용 큰 유형 — 레버리지·인버스를 레버리지(롱) · 인버스로 나눈다."""
    big, sub = classify(name, idx)
    if big == "레버리지·인버스":
        return ("인버스" if re.search(r"인버스|곱버스|-1X|\(-1\)", f"{name} {idx}")
                else "레버리지(롱)"), ""
    return big, sub


__all__ = ["STRATEGY", "classify", "classify2"]
