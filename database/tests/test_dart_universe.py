"""dart_universe — 유니버스 = KRX 전기간 ∪ 키움 일별 마스터(현역). 신규 상장사가 들어온다(DEFECT-B05). 플랜 P1 Task 1.6."""
import importlib
import io
import sqlite3
import sys
import zipfile


def _corpcode_zip(entries):
    xml = "<result>" + "".join(
        f"<list><corp_code>{cc}</corp_code><corp_name>{nm}</corp_name><stock_code>{sc}</stock_code></list>"
        for cc, nm, sc in entries) + "</result>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def _load(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("KRX_API_KEY=k\nKRX_ID=i\nKRX_PW=p\n", encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(env))
    monkeypatch.setenv("QL_HOME", str(tmp_path))
    (tmp_path / "data" / "raw").mkdir(parents=True)
    for m in ("api", "dart_universe"):
        sys.modules.pop(m, None)
    return importlib.import_module("dart_universe")


def test_new_listing_from_kiwoom_master_enters_universe(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch)
    krx = sqlite3.connect(tmp_path / "data" / "raw" / "krx.db")
    for t in ("krx_stk_isu_base_info", "krx_ksq_isu_base_info"):
        krx.execute(f"CREATE TABLE {t} (ISU_SRT_CD TEXT, bas_dd_req TEXT)")
    krx.executemany("INSERT INTO krx_stk_isu_base_info VALUES (?,?)",
                    [("005930", "20100104"), ("005930", "20260820"), ("082640", "20100104"), ("082640", "20200630")])
    krx.commit(); krx.close()
    kw = sqlite3.connect(tmp_path / "data" / "raw" / "kiwoom.db")
    kw.execute("CREATE TABLE ka10099_stock_master (snap_date TEXT, code TEXT, upSizeName TEXT)")
    kw.executemany("INSERT INTO ka10099_stock_master VALUES (?,?,?)",
                   [("20260909", "005930", "대형주"), ("20260904", "386380", "소형주"), ("20260909", "386380", "소형주")])
    kw.commit(); kw.close()
    monkeypatch.setattr(m.api, "dart", lambda path, **kw_: _corpcode_zip(
        [("00126380", "삼성전자", "005930"), ("01999999", "스카이랩스", "386380"), ("00100001", "동양생명", "082640")]))
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path / "corps.txt")])
    m.main()
    rows = {ln.split("\t")[0]: ln.split("\t")[1:] for ln in (tmp_path / "corps.txt").read_text().splitlines()}
    assert rows["01999999"] == ["2025", "2026"]          # 신규 상장(09-04) — 상장 직전연도 1년 유예
    assert rows["00126380"] == ["2015", "2026"]          # KRX 2010~ ∪ 키움 2026
    assert rows["00100001"] == ["2015", "2020"]          # 폐지: KRX 구간만, last_year 2020


def test_missing_kiwoom_db_falls_back_to_krx_only(tmp_path, monkeypatch):
    m = _load(tmp_path, monkeypatch)
    krx = sqlite3.connect(tmp_path / "data" / "raw" / "krx.db")
    for t in ("krx_stk_isu_base_info", "krx_ksq_isu_base_info"):
        krx.execute(f"CREATE TABLE {t} (ISU_SRT_CD TEXT, bas_dd_req TEXT)")
    krx.execute("INSERT INTO krx_stk_isu_base_info VALUES ('005930','20260820')")
    krx.commit(); krx.close()
    monkeypatch.setattr(m.api, "dart", lambda path, **kw_: _corpcode_zip([("00126380", "삼성전자", "005930")]))
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path / "corps.txt"), "--kw", str(tmp_path / "nope.db")])
    m.main()
    assert (tmp_path / "corps.txt").read_text().startswith("00126380\t")
