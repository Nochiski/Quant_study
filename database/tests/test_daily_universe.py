"""daily.universe — 키움 마스터 기반 요청 유니버스와 유예 규칙. 플랜 P1 Task 1.1 / R5."""
import json
import sqlite3

from daily import universe as uni


def _kw(tmp_path, master_rows, foreign_rows=()):
    """master_rows: (snap_date, code, upSizeName[, marketName]) — marketName 생략 시 '거래소'."""
    con = sqlite3.connect(tmp_path / "kiwoom.db")
    con.execute("CREATE TABLE ka10099_stock_master (snap_date TEXT, code TEXT, upSizeName TEXT, marketName TEXT)")
    con.executemany("INSERT INTO ka10099_stock_master VALUES (?,?,?,?)",
                    [(r + ("거래소",))[:4] for r in master_rows])
    con.execute("CREATE TABLE ka10008_foreign_holdings (ticker TEXT, dt TEXT)")
    con.executemany("INSERT INTO ka10008_foreign_holdings VALUES (?,?)", foreign_rows)
    con.commit()
    return con


def test_kiwoom_common_uses_latest_snapshot_size_class_or_new_listing(tmp_path):
    con = _kw(tmp_path, [("20260908", "005930", "대형주"), ("20260909", "005930", "대형주"),
                         ("20260909", "386380", "", "코스닥"),      # 신규 상장: 규모구분 아직 없음 → 포함
                         ("20260909", "005935", "", "거래소"),      # 우선주(끝자리 5) → 제외
                         ("20260909", "0238P0", "", "ETF"),         # ETF → 제외
                         ("20260909", "610111", "", "ETN")])        # ETN → 제외
    snap = uni.kiwoom_common(con)
    assert snap.snap_date == "20260909"
    assert snap.tickers == ("005930", "386380")


def test_first_run_seeds_from_tickers_txt_and_logs_difference(tmp_path):
    con = _kw(tmp_path, [("20260909", "005930", "대형주"), ("20260909", "386380", "", "코스닥")])
    seed = tmp_path / "tickers.txt"
    seed.write_text("005930\n000660\n", encoding="utf-8")       # 000660 은 마스터에 upSizeName 공백
    state_path = tmp_path / "universe_kw.json"
    req = uni.requested(con, state_path=state_path, seed_path=seed, grace_days=5)
    assert req.tickers == ("000660", "005930", "386380")         # 시드 ∪ 오늘
    assert req.seeded_only == ("000660",)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["asof"] == "20260909" and state["n_requested"] == 3     # ledger_health 의 비율 분모
    assert "000660" in state["grace"]


def test_grace_expires_after_grace_days_and_reports_missing_tail(tmp_path):
    con = _kw(tmp_path, [("20260909", "005930", "대형주")],
              foreign_rows=[("000660", "20260901")])              # 000660 마지막 수집 09-01
    state_path = tmp_path / "universe_kw.json"
    # 두 종목 다 유예 5거래일을 채웠다. 000660 은 ka10008.max(dt) >= last_seen 로 꼬리까지 받았고
    # 000001 은 아무 데이터도 못 받았다 — 그래도 제외한다. 키움은 폐지 종목에 0행을 주므로 더 기다려도
    # 꼬리는 오지 않고(검수 B F-1·F-4: 4종목이 영구 고착돼 하루 16콜 낭비), 대신 tail_missing 으로 알린다.
    state_path.write_text(json.dumps({"asof": "20260908", "grace": {
        "000660": {"last_seen": "20260901", "missing_days": 4},
        "000001": {"last_seen": "20260901", "missing_days": 4}}}), encoding="utf-8")
    req = uni.requested(con, state_path=state_path, seed_path=None, grace_days=5)
    assert "000660" not in req.tickers and "000001" not in req.tickers
    assert req.dropped == ("000001", "000660")
    assert req.tail_missing == ("000001",)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["grace"] == {}


def test_ticker_gone_from_master_enters_grace_with_last_seen(tmp_path):
    # 검수 B F-3: 마스터에서 사라진 종목을 유예에 넣는 경로가 첫 실행 시드에만 있었다.
    con = _kw(tmp_path, [("20260908", "005930", "대형주"), ("20260908", "000660", "중형주"),
                         ("20260909", "005930", "대형주")])
    state_path = tmp_path / "universe_kw.json"
    state_path.write_text(json.dumps({"asof": "20260908", "n_requested": 2, "grace": {}}), encoding="utf-8")
    req = uni.requested(con, state_path=state_path, seed_path=None, grace_days=5)
    assert req.tickers == ("000660", "005930")                   # 사라진 날에도 요청한다
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["grace"]["000660"] == {"last_seen": "20260908", "missing_days": 1}


def test_same_day_second_call_does_not_advance_grace(tmp_path):
    # 검수 B F-4: kw_daily 와 kis_daily 가 같은 상태 파일을 하루 두 번 읽는다 — 거래일 단위로만 센다.
    con = _kw(tmp_path, [("20260909", "005930", "대형주")])
    state_path = tmp_path / "universe_kw.json"
    state_path.write_text(json.dumps({"asof": "20260909", "n_requested": 2, "grace": {
        "000660": {"last_seen": "20260908", "missing_days": 2}}}), encoding="utf-8")
    req = uni.requested(con, state_path=state_path, seed_path=None, grace_days=5)
    assert "000660" in req.tickers
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["grace"]["000660"]["missing_days"] == 2
