"""후보 묶음. PROTOCOL.md 의 표가 정본이고 결과를 보기 전에 고정한다."""

from __future__ import annotations

from dataclasses import dataclass

LOW_VOL = (("VOL60", -1),)
VQ = (("VALU", 1), ("QUAL", 1))
LVQ = (("VOL60", -1), ("VALU", 1), ("QUAL", 1))
PULL = (("H52", 1), ("REV1M", 1))


@dataclass(frozen=True)
class Candidate:
    cid: str
    label: str
    kind: str  # stock · stock_ew · sector · trend · cap · tilt · exclude · core_sat · avoid
    universe: str = "U200"  # U200 · U500
    spec: tuple[tuple[str, int], ...] = ()


F1: tuple[Candidate, ...] = (
    Candidate("C01", "저변동", "stock", "U200", LOW_VOL),
    Candidate("C02", "밸류", "stock", "U200", (("VALU", 1),)),
    Candidate("C03", "퀄리티", "stock", "U200", (("QUAL", 1),)),
    Candidate("C04", "밸류+퀄리티", "stock", "U200", VQ),
    Candidate("C05", "52주 고점 근접", "stock", "U200", (("H52", 1),)),
    Candidate("C06", "고점 근접+1M 반전", "stock", "U200", PULL),
    Candidate("C07", "12-1 모멘텀", "stock", "U200", (("MOM12_1", 1),)),
    Candidate("C08", "저변동+밸류+퀄리티", "stock", "U200", LVQ),
    Candidate("C09", "코스피 상위 200 동일 비중", "stock_ew", "U200"),
    Candidate("C10", "저변동+밸류+퀄리티 (상위 500)", "stock", "U500", LVQ),
    Candidate("C11", "고점 근접+1M 반전 (상위 500)", "stock", "U500", PULL),
    Candidate("C12", "업종 로테이션 6개월 상위 3", "sector"),
    Candidate("C13", "코스피200 200일선 추세", "trend"),
)

# F2.md: 시총 가중 + 팩터 기울이기. E0 는 판정하지 않는 참고 줄
E0 = Candidate("E0", "시총 가중 (참고)", "cap")
F2: tuple[Candidate, ...] = (
    Candidate("E1", "저변동 기울이기", "tilt", "U200", LOW_VOL),
    Candidate("E2", "밸류 기울이기", "tilt", "U200", (("VALU", 1),)),
    Candidate("E3", "퀄리티 기울이기", "tilt", "U200", (("QUAL", 1),)),
    Candidate("E4", "밸류+퀄리티 기울이기", "tilt", "U200", VQ),
    Candidate("E5", "12-1 모멘텀 기울이기", "tilt", "U200", (("MOM12_1", 1),)),
    Candidate("E6", "52주 고점 기울이기", "tilt", "U200", (("H52", 1),)),
    Candidate("E7", "저변동+밸류+퀄리티 기울이기", "tilt", "U200", LVQ),
    Candidate("E8", "12-1 모멘텀 하위 20% 제외", "exclude", "U200", (("MOM12_1", 1),)),
    Candidate("E9", "밸류+퀄리티 하위 20% 제외", "exclude", "U200", VQ),
)

# F3.md: 가격·재무 밖의 정보로 기울이기
F3: tuple[Candidate, ...] = (
    Candidate("G1", "신용잔고율 낮은 쪽", "tilt", "U200", (("CRDT_LVL", -1),)),
    Candidate("G2", "신용잔고율 증가 작은 쪽", "tilt", "U200", (("CRDT_CHG", -1),)),
    Candidate("G3", "대차잔고 증가 작은 쪽", "tilt", "U200", (("LEND_CHG", -1),)),
    Candidate("G4", "외국인 60일 순매수", "tilt", "U200", (("FRGN60", 1),)),
    Candidate("G5", "1개월 반전", "tilt", "U200", (("REV1M", 1),)),
    Candidate("G6", "영업이익 전년 대비 증가", "tilt", "U200", (("OP_YOY", 1),)),
    Candidate("G7", "신용·대차·외국인·반전·실적 합성", "tilt", "U200",
              (("CRDT_CHG", -1), ("LEND_CHG", -1), ("FRGN60", 1), ("REV1M", 1), ("OP_YOY", 1))),
)

# F4.md: 초대형 10 핵심(시총 몫) + 팩터 위성 30(균등)
F4: tuple[Candidate, ...] = (
    Candidate("H1", "핵심+저변동 위성", "core_sat", "U200", LOW_VOL),
    Candidate("H2", "핵심+밸류 위성", "core_sat", "U200", (("VALU", 1),)),
    Candidate("H3", "핵심+퀄리티 위성", "core_sat", "U200", (("QUAL", 1),)),
    Candidate("H4", "핵심+저변동·밸류·퀄리티 위성", "core_sat", "U200", LVQ),
    Candidate("H5", "핵심+12-1 모멘텀 위성", "core_sat", "U200", (("MOM12_1", 1),)),
    Candidate("H6", "핵심+고점 근접·반전 위성", "core_sat", "U200", PULL),
    Candidate("H7", "핵심+영업이익 증가 위성", "core_sat", "U200", (("OP_YOY", 1),)),
)

# F5.md: 코스닥 상위 150, 기준 지수 코스닥 150
F5: tuple[Candidate, ...] = (
    Candidate("J1", "코스닥 저변동", "stock", "UQ150", LOW_VOL),
    Candidate("J2", "코스닥 밸류", "stock", "UQ150", (("VALU", 1),)),
    Candidate("J3", "코스닥 퀄리티", "stock", "UQ150", (("QUAL", 1),)),
    Candidate("J4", "코스닥 저변동+밸류+퀄리티", "stock", "UQ150", LVQ),
    Candidate("J5", "코스닥 12-1 모멘텀", "stock", "UQ150", (("MOM12_1", 1),)),
    Candidate("J6", "코스닥 영업이익 증가", "stock", "UQ150", (("OP_YOY", 1),)),
    Candidate("J7", "코스닥 상위 150 동일 비중", "stock_ew", "UQ150"),
)

# F7_EVENTS.md: 기업 이벤트
F7: tuple[Candidate, ...] = (
    Candidate("K1", "CB 발행 종목 뺀 시총 가중", "avoid", "U200", (("CB12", 1),)),
    Candidate("K2", "핵심+자사주 매입 위성", "core_sat", "U200", (("BUYAMT", 1),)),
)

FAMILIES = {"F1": F1, "F2": F2, "F3": F3, "F4": F4, "F5": F5}
NEEDS_ALT = {"F3", "F4", "F5"}
BENCHMARKS = {"F5": "코스닥 150"}  # 없으면 코스피 200
REFERENCES = {"F2": E0, "F3": E0, "F4": E0}
