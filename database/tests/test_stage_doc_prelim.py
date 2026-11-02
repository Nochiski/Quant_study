"""parsers_doc 잠정실적 — DART 공정공시 '영업(잠정)실적' 서식(xforms HTML) → stg_doc_prelim 1행 (N-64).

서식은 11-01 표본 20건(원장 밖)에서 본 꼴을 줄여 만든다 — 공개 저장소라 실제 원문(담당자 이름 · 전화번호)은
넣지 않는다. 표 둘: 실적기간 표(당기 · 전년동기 … 시작~끝) · 실적내용 표(항목 × 당해/누계 × 당기 · 전기 · 전년동기).
"""
import io
import zipfile

from stage import doc_vocab
from stage import parsers_doc as pd_

V = doc_vocab.load_vocab()
RNO = "20261029800635"

# 금호타이어 3Q26 연결(백만원) — 당해 (당기, 전기, 전년동기)
KUMHO = {"매출액": ("1,237,395", "1,332,767", "1,113,746"),
         "영업이익": ("130,521", "182,238", "108,507"),
         "법인세비용차감전계속사업이익": ("12,791", "183,457", "101,984"),
         "당기순이익": ("13,064", "137,774", "91,548"),
         "지배기업 소유주지분 순이익": ("6,096", "129,651", "82,817")}


def _td(text: str, colspan: int = 1, rowspan: int = 1) -> str:
    span = (f' colspan="{colspan}"' if colspan > 1 else "") + (f' rowspan="{rowspan}"' if rowspan > 1 else "")
    return f'<td{span}> <span style="font-size:10pt;">{text}</span> </td>'


def _tr(*cells: str) -> str:
    return "<tr> " + " ".join(cells) + " </tr>"


def _period_table(cur: tuple[str, str], py: tuple[str, str], tid: str = "XFormD1_Form0_Table0") -> str:
    rows = [_tr(_td("실적기간", 4))]
    for label, (a, b) in (("당기실적", cur), ("전기실적", ("2026-04-01", "2026-06-30")),
                          ("전년동기실적", py), ("당기누계실적", ("2026-01-01", cur[1])),
                          ("전년동기누적실적", ("2025-01-01", py[1]))):
        rows.append(_tr(_td(label), _td(a), _td("~"), _td(b)))
    return f'<table border="1" id="{tid}"> <tbody> ' + " ".join(rows) + " </tbody> </table>"


def _result_table(items: dict[str, tuple[str, str, str]], unit: str = "백만원",
                  unit_in_header: bool = False, consolidated: bool = True,
                  tid: str = "XFormD1_Form0_RepeatTable0") -> str:
    kind = "1. 연결실적내용" if consolidated else "1. 실적내용"
    rows = [_tr(_td("※ 동 정보는 잠정치로서 향후 확정치와는 다를 수 있음.", 9))]
    if unit_in_header:                                  # 코스닥 서식 — 단위가 '구분' 칸 안에
        rows.append(_tr(_td(kind, 9)))
        gubun = f"구분(단위 : {unit}, %)"
    else:                                               # 유가 서식 — 머리 오른쪽 칸
        rows.append(_tr(_td(kind, 4), _td(f"단위 : {unit}, %", 5)))
        gubun = "구분"
    rows.append(_tr(_td(gubun, 2, 2), _td("당기실적"), _td("전기실적"), _td("전기대비", 2),
                    _td("전년동기실적"), _td("전년동기대비", 2)))
    rows.append(_tr(_td("('26.3Q)"), _td("('26.2Q)"), _td("증감율(%)"), _td("흑자적자전환여부"),
                    _td("('25.3Q)"), _td("증감율(%)"), _td("흑자적자전환여부")))
    for label, (cur, prev, py) in items.items():
        rows.append(_tr(_td(label, rowspan=2), _td("당해실적"), _td(cur), _td(prev), _td("1.0"), _td("-"),
                        _td(py), _td("2.0"), _td("-")))
        rows.append(_tr(_td("누계실적"), _td("9,999"), _td("-"), _td("-"), _td("-"), _td("8,888"),
                        _td("3.0"), _td("-")))
    rows.append(_tr(_td("-", 2), *[_td("-")] * 7))
    rows.append(_tr(_td("2. 정보제공내역", 2, 5), _td("정보제공자", 2), _td("IR", 5)))
    return f'<table border="1" id="{tid}"> <tbody> ' + " ".join(rows) + " </tbody> </table>"


def _doc(*tables: str, title: str = "금호타이어/연결재무제표기준영업(잠정)실적(공정공시)/"
                                     "(2026.10.29)연결재무제표기준영업(잠정)실적(공정공시)") -> str:
    return (f'<html>\n <head> \n  <meta content="text/html; charset=euc-kr" http-equiv="Content-Type"> \n'
            f"  <title>{title}</title> \n </head> \n <body> \n  <div class=\"xforms\"> "
            + " ".join(tables) + " </div> </body>\n</html>")


def _kumho(**kw: object) -> str:
    return _doc(_period_table(("2026-07-01", "2026-09-30"), ("2025-07-01", "2025-09-30")),
                _result_table(KUMHO, **kw))  # type: ignore[arg-type]


def _parse(text: str) -> dict[str, str | None] | None:
    return pd_.prelim_row(RNO, f"{RNO}.xml", text, "2026-11-01T04:00:00")


def test_유가_서식_당해_당기값을_원_단위로_읽는다() -> None:
    r = _parse(_kumho())
    assert r is not None and r["status"] == "ok" and r["error"] is None
    assert (r["fs_div"], r["period_from"], r["period_to"], r["unit_raw"]) == ("CFS", "20260701", "20260930", "백만원")
    assert (r["revenue_krw"], r["op_profit_krw"], r["pretax_krw"], r["net_income_krw"], r["ni_owner_krw"]) == (
        "1237395000000", "130521000000", "12791000000", "13064000000", "6096000000")
    assert (r["revenue_py_krw"], r["op_profit_py_krw"], r["net_income_py_krw"]) == (
        "1113746000000", "108507000000", "91548000000")
    assert (r["py_from"], r["py_to"]) == ("20250701", "20250930")
    assert (r["n_items"], r["unknown_items"]) == ("5", "[]")


def test_코스닥_서식은_단위가_구분_칸_안에_있고_억원이면_1억을_곱한다() -> None:
    items = {"매출액": ("15,755", "13,940", "10,289"), "영업이익": ("5,253.5", "4,523", "3,014")}
    r = _parse(_doc(_period_table(("2026-07-01", "2026-09-30"), ("2025-07-01", "2025-09-30"), "XFormD1_Form0_Table1"),
                    _result_table(items, unit="억원", unit_in_header=True, tid="XFormD1_Form0_RepeatTable1")))
    assert r is not None and r["status"] == "ok" and r["unit_raw"] == "억원"
    assert (r["revenue_krw"], r["op_profit_krw"], r["net_income_krw"]) == ("1575500000000", "525350000000", None)


def test_별도_서식은_OFS_이고_지배기업_행이_없다() -> None:
    items = {k: v for k, v in KUMHO.items() if k != "지배기업 소유주지분 순이익"}
    r = _parse(_doc(_period_table(("2026-07-01", "2026-09-30"), ("2025-07-01", "2025-09-30")),
                    _result_table(items, consolidated=False), title="라온피플/영업(잠정)실적(공정공시)/…"))
    assert r is not None and r["status"] == "ok"
    assert (r["fs_div"], r["ni_owner_krw"], r["n_items"]) == ("OFS", None, "4")


def test_정정_공시는_정정표를_건너뛰고_바로잡은_서식을_읽는다() -> None:
    change = ('<table id="XFormD8_Form0_Table1"> <tbody> ' + _tr(_td("정정일자"), _td("2026-10-26"))
              + ' </tbody> </table> <table id="XFormD8_Form0_RepeatTable0"> <tbody> '
              + _tr(_td("정정항목"), _td("정정전"), _td("정정후"))
              + _tr(_td("- 매출액(당해실적)"), _td("15,000"), _td("15,755")) + " </tbody> </table>")
    items = {"매출액": ("15,755", "13,940", "10,289"), "영업이익": ("5,253", "4,523", "3,014")}
    r = _parse(_doc(change, _period_table(("2026-07-01", "2026-09-30"), ("2025-07-01", "2025-09-30")),
                    _result_table(items, unit="억원")))
    assert r is not None and r["status"] == "ok"
    assert (r["revenue_krw"], r["op_profit_krw"]) == ("1575500000000", "525300000000")


def test_월_실적은_기간을_그대로_남기고_빈칸은_NULL() -> None:
    items = {"매출액": ("13,403", "14,055", "13,382"), "영업이익": ("-", "-", "-"), "당기순이익": ("-", "-", "-")}
    r = _parse(_doc(_period_table(("2026-08-01", "2026-08-31"), ("2025-08-01", "2025-08-31")),
                    _result_table(items, consolidated=False)))
    assert r is not None and r["status"] == "ok"
    assert (r["period_from"], r["period_to"]) == ("20260801", "20260831")      # 분기 여부는 equity 가 가린다
    assert (r["revenue_krw"], r["op_profit_krw"], r["net_income_krw"]) == ("13403000000", None, None)


def test_음수는_부호를_살린다() -> None:
    items = {"매출액": ("8,000", "7,000", "6,000"), "당기순이익": ("-3,286", "-9,440", "906")}
    r = _parse(_doc(_period_table(("2026-07-01", "2026-09-30"), ("2025-07-01", "2025-09-30")), _result_table(items)))
    assert r is not None and r["status"] == "ok"
    assert (r["net_income_krw"], r["net_income_py_krw"]) == ("-3286000000", "906000000")


def test_모르는_단위는_실패로_두고_값을_넣지_않는다() -> None:
    r = _parse(_kumho(unit="천달러"))
    assert r is not None and r["status"] == "failed" and "unit" in (r["error"] or "")
    assert r["revenue_krw"] is None and r["op_profit_krw"] is None and r["fs_div"] == "CFS"


def test_읽을_수_없는_숫자가_하나라도_있으면_행_전체를_실패로() -> None:
    items = dict(KUMHO, 영업이익=("△130,521", "182,238", "108,507"))
    r = _parse(_doc(_period_table(("2026-07-01", "2026-09-30"), ("2025-07-01", "2025-09-30")), _result_table(items)))
    assert r is not None and r["status"] == "failed" and "영업이익" in (r["error"] or "")
    assert r["revenue_krw"] is None


def test_실적기간_표가_없으면_실패() -> None:
    r = _parse(_doc(_result_table(KUMHO)))
    assert r is not None and r["status"] == "failed" and "period" in (r["error"] or "")
    assert r["period_from"] is None and r["revenue_krw"] is None


def test_실적내용_표가_둘이면_실패() -> None:
    p = _period_table(("2026-07-01", "2026-09-30"), ("2025-07-01", "2025-09-30"))
    r = _parse(_doc(p, _result_table(KUMHO), _result_table(KUMHO, tid="XFormD1_Form0_RepeatTable1")))
    assert r is not None and r["status"] == "failed" and "result" in (r["error"] or "")


def test_모르는_항목_이름은_따로_남긴다() -> None:
    items = {"매출액": ("8,000", "7,000", "6,000"), "영업이익(손실)": ("100", "90", "80")}
    r = _parse(_doc(_period_table(("2026-07-01", "2026-09-30"), ("2025-07-01", "2025-09-30")), _result_table(items)))
    assert r is not None and r["status"] == "ok"
    assert (r["op_profit_krw"], r["n_items"], r["unknown_items"]) == (None, "1", '["영업이익(손실)"]')


def test_잠정실적이_아닌_html_은_None() -> None:
    other = "<html>\n <head><title>동화기업/주식분할결정</title></head><body><table></table></body></html>"
    assert _parse(other) is None


def test_parse_zip_은_html_meta_행과_함께_잠정_행을_낸다() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{RNO}.xml", _kumho().encode())
    out = pd_.parse_zip(RNO, buf.getvalue(), "2026-11-01T04:00:00", V)
    assert [m["parse_mode"] for m in out.meta] == ["html"] and out.section == [] and out.correction == []
    assert len(out.prelim) == 1 and out.prelim[0]["rcept_no"] == RNO
    assert out.prelim[0]["member_name"] == f"{RNO}.xml" and out.prelim[0]["fetched_at"] == "2026-11-01T04:00:00"
    assert out.prelim[0]["revenue_krw"] == "1237395000000"
