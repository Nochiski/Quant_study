"""daily.universe — 키움 마스터 기반 요청 유니버스와 유예 규칙. 플랜 P1 Task 1.1 / R5."""
import json
import sqlite3

from daily import universe as uni


def _kw(tmp_path, master_rows, foreign_rows=()):
    con = sqlite3.connect(tmp_path / "kiwoom.db")
    con.execute("CREATE TABLE ka10099_stock_master (snap_date TEXT, code TEXT, upSizeName TEXT)")
    con.executemany("INSERT INTO ka10099_stock_master VALUES (?,?,?)", master_rows)
    con.execute("CREATE TABLE ka10008_foreign_holdings (ticker TEXT, dt TEXT)")
    con.executemany("INSERT INTO ka10008_foreign_holdings VALUES (?,?)", foreign_rows)
    con.commit()
    return con


def test_kiwoom_common_uses_latest_snapshot_and_size_class(tmp_path):
    con = _kw(tmp_path, [("20260908", "005930", "대형주"), ("20260909", "005930", "대형주"),
                         ("20260909", "386380", "소형주"), ("20260909", "005935", ""),  # 우선주
                         ("20260909", "0238P0", "")])                                  # ETF
    snap = uni.kiwoom_common(con)
    assert snap.snap_date == "20260909"
    assert snap.tickers == ("005930", "386380")


def test_first_run_seeds_from_tickers_txt_and_logs_difference(tmp_path):
    con = _kw(tmp_path, [("20260909", "005930", "대형주"), ("20260909", "386380", "소형주")])
    seed = tmp_path / "tickers.txt"
    seed.write_text("005930\n000660\n", encoding="utf-8")       # 000660 은 마스터에 upSizeName 공백
    state_path = tmp_path / "universe_kw.json"
    req = uni.requested(con, state_path=state_path, seed_path=seed, grace_days=5)
    assert req.tickers == ("000660", "005930", "386380")         # 시드 ∪ 오늘
    assert req.seeded_only == ("000660",)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["asof"] == "20260909"
    assert "000660" in state["grace"]


def test_grace_expires_only_after_last_data_received(tmp_path):
    con = _kw(tmp_path, [("20260909", "005930", "대형주")],
              foreign_rows=[("000660", "20260901")])              # 000660 마지막 수집 09-01
    state_path = tmp_path / "universe_kw.json"
    # 000660 은 09-01 에 마스터에서 마지막으로 보였고 유예 5거래일이 이미 지났지만
    # ka10008.max(dt)=0901 >= last_seen=0901 이라 "마지막 거래일 데이터 확보" 조건 충족 → 제외
    state_path.write_text(json.dumps({"asof": "20260908", "grace": {
        "000660": {"last_seen": "20260901", "missing_days": 5},
        "000001": {"last_seen": "20260901", "missing_days": 5}}}), encoding="utf-8")
    req = uni.requested(con, state_path=state_path, seed_path=None, grace_days=5)
    assert "000660" not in req.tickers                            # 제외됨
    assert "000001" in req.tickers                                # 데이터를 아직 못 받았으면 유예 연장
    assert req.dropped == ("000660",)
