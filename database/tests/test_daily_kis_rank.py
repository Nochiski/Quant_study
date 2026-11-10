"""daily.kis_rank — KIS 관심종목 등록 상위 · HTS 조회 상위 스냅숏(N-66).

콜은 전부 `api.kis` monkeypatch 다 — 이 파일은 네트워크를 쓰지 않는다. 응답은 칸 이름 · 행 수만 실제와 같은
합성 값이다(가짜 종목코드 · 건수). KIS 고객 이용약관 제5조 ③(시세 제3자 제공 금지) 때문에 실제 응답을
저장소에 넣지 않는다.
"""
import datetime as dt
import importlib
import sqlite3
from pathlib import Path

import pytest

from daily import calendar as trading_calendar
from daily import kis_daily, kis_rank

_FAKE_ENV = ("KRX_API_KEY=krx\nKRX_ID=id\nKRX_PW=pw\n"
             "KIS_APP_KEY=appkey\nKIS_APP_SECRET=secret\n")
KST = dt.timezone(dt.timedelta(hours=9))
DAY = dt.date(2026, 11, 11)                       # 수요일
CAL = trading_calendar.Calendar(holidays=frozenset({"20261112"}), source="kis_cache")
TI_URL = "/uapi/domestic-stock/v1/ranking/top-interest-stock"
HT_URL = "/uapi/domestic-stock/v1/ranking/hts-top-view"


@pytest.fixture
def api_mod(tmp_path, monkeypatch):
    """`api` 모듈. 토큰 캐시 경로를 tmp 로 돌려 실제 캐시를 지우지 않게 한다."""
    env = tmp_path / "fake.env"
    env.write_text(_FAKE_ENV, encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(env))
    api = importlib.import_module("api")
    monkeypatch.setattr(api, "_KIS_CACHE", str(tmp_path / "kis_token.json"))
    monkeypatch.setattr(kis_daily.time, "sleep", lambda _s: None)   # 재시도 백오프 제거
    return api


def _kst(hhmm: str, day: dt.date = DAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.fromisoformat(hhmm), tzinfo=KST)


def _interest_rows(scope: str, n: int = 30) -> list[dict[str, str]]:
    return [{"mrkt_div_cls_name": "코스피", "mksc_shrn_iscd": f"9{scope}{i:02d}", "hts_kor_isnm": f"가짜{i}",
             "stck_prpr": "1000", "prdy_vrss": "10", "prdy_vrss_sign": "2", "prdy_ctrt": "1.00",
             "acml_vol": "100", "acml_tr_pbmn": "100000", "askp": "1001", "bidp": "999",
             "data_rank": str(i + 1), "inter_issu_reg_csnu": str(100000 - i)} for i in range(n)]


def _hts_rows(n: int = 20) -> list[dict[str, str]]:
    return [{"mrkt_div_cls_code": "J", "mksc_shrn_iscd": f"8000{i:02d}"} for i in range(n)]


def _fake_kis(seen: list[tuple[str, str]], interest=_interest_rows, hts=_hts_rows):
    """`api.kis` 대역 — TR · 범위마다 합성 응답. seen 에 (url, 범위) 를 남긴다."""
    def kis(url: str, tr_id: str, params: dict[str, str]) -> dict[str, object]:
        if url == TI_URL:
            scope = params["fid_input_iscd"]
            seen.append((url, scope))
            return {"rt_cd": "0", "msg_cd": "MCA00000", "output": interest(scope)}
        seen.append((url, "all"))
        return {"rt_cd": "0", "msg_cd": "MCA00000", "output1": hts()}
    return kis


def _clock():
    t = [dt.datetime(2026, 11, 11, 2, 0, 0, tzinfo=dt.UTC)]

    def now() -> dt.datetime:
        t[0] += dt.timedelta(seconds=1)
        return t[0]
    return now


def _q(db: Path, sql: str, args: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


# ── 슬롯 판정 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("now", "slot"), [
    (_kst("08:29"), None),                                      # 첫 슬롯 전
    (_kst("08:30"), "2026-11-11T08:30"),
    (_kst("12:44"), "2026-11-11T12:30"),
    (_kst("20:59"), "2026-11-11T20:30"),                        # 마지막 슬롯 창 끝
    (_kst("21:00"), None),
    # 크론은 UTC 다 — 08:30 KST 는 전날 23:30 UTC. 날짜를 UTC 로 보면 화요일 슬롯을 월요일로 판정한다
    (dt.datetime(2026, 11, 10, 23, 30, tzinfo=dt.UTC), "2026-11-11T08:30"),
    # 일요일 23:30 UTC = 월요일 08:30 KST — 휴장을 UTC 날짜로 보면 매주 월요일 첫 슬롯을 잃는다
    (dt.datetime(2026, 11, 15, 23, 30, tzinfo=dt.UTC), "2026-11-16T08:30"),
])
def test_slot_is_the_kst_grid_slot_containing_now(now: dt.datetime, slot: str | None) -> None:
    got = kis_rank.pick_slot(now, CAL)
    assert got.slot_kst == slot
    assert got.status is (kis_rank.SlotStatus.OK if slot else kis_rank.SlotStatus.OUTSIDE)


@pytest.mark.parametrize("now", [
    _kst("10:00", dt.date(2026, 11, 12)),
    dt.datetime(2026, 11, 11, 23, 30, tzinfo=dt.UTC),          # UTC 로는 거래일(11-11) — KST 로는 휴장 11-12 08:30
])
def test_holiday_is_skipped_by_kst_date(now: dt.datetime) -> None:
    got = kis_rank.pick_slot(now, CAL)
    assert got.status is kis_rank.SlotStatus.HOLIDAY
    assert got.slot_kst is None


# ── 정상 수집 · 다시 돌리기 ──────────────────────────────────────────────────

def test_slot_stores_every_row_with_received_time(api_mod, monkeypatch, tmp_path) -> None:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis(seen))
    db = tmp_path / "kis_rank.db"
    res = kis_rank.run(db, _kst("11:00"), CAL, clock=_clock())
    assert res.status is kis_rank.SlotStatus.OK and res.rc == 0
    assert seen == [(TI_URL, "0001"), (TI_URL, "1001"), (TI_URL, "0000"), (HT_URL, "all")]
    assert _q(db, "SELECT scope, COUNT(*) FROM kis_top_interest WHERE slot_kst='2026-11-11T11:00' "
                  "GROUP BY scope ORDER BY scope") == [("0000", 30), ("0001", 30), ("1001", 30)]
    assert _q(db, "SELECT COUNT(*) FROM kis_hts_top_view") == [(20,)]
    assert _q(db, "SELECT COUNT(*) FROM kis_top_interest WHERE received_utc IS NULL") == [(0,)]
    assert _q(db, "SELECT inter_issu_reg_csnu FROM kis_top_interest WHERE scope='0001' AND pos=0") \
        == [("100000",)]
    runs = _q(db, "SELECT endpoint, scope, verdict, n_rows, received_utc IS NOT NULL FROM kis_rank_runs "
                  "ORDER BY endpoint, scope")
    assert runs == [("hts_top_view", "all", "ok", 20, 1), ("top_interest", "0000", "ok", 30, 1),
                    ("top_interest", "0001", "ok", 30, 1), ("top_interest", "1001", "ok", 30, 1)]


def test_rerun_of_a_received_slot_makes_no_call(api_mod, monkeypatch, tmp_path) -> None:
    """이미 받은 슬롯을 다시 부르면 첫 관측(받은 시각)이 늦은 값으로 바뀐다 — 부르지 않는다."""
    db = tmp_path / "kis_rank.db"
    monkeypatch.setattr(api_mod, "kis", _fake_kis([]))
    kis_rank.run(db, _kst("11:00"), CAL, clock=_clock())
    first = _q(db, "SELECT received_utc FROM kis_rank_runs ORDER BY endpoint, scope")
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis(seen))
    res = kis_rank.run(db, _kst("11:10"), CAL, clock=_clock())
    assert res.rc == 0
    assert seen == []
    assert _q(db, "SELECT received_utc FROM kis_rank_runs ORDER BY endpoint, scope") == first


def test_rerun_calls_only_the_failed_target(api_mod, monkeypatch, tmp_path) -> None:
    db = tmp_path / "kis_rank.db"

    def flaky(url: str, tr_id: str, params: dict[str, str]) -> dict[str, object]:
        if url == HT_URL:
            return {"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 서비스"}
        return _fake_kis([])(url, tr_id, params)
    monkeypatch.setattr(api_mod, "kis", flaky)
    assert kis_rank.run(db, _kst("11:00"), CAL, clock=_clock()).rc == 5
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis(seen))
    assert kis_rank.run(db, _kst("11:05"), CAL, clock=_clock()).rc == 0
    assert seen == [(HT_URL, "all")]
    assert _q(db, "SELECT verdict, rows_json FROM kis_rank_runs WHERE endpoint='hts_top_view'") == [("ok", None)]


# ── 칸 · 행 수 이상은 crit — 조용히 NULL 로 넣지 않고, 받은 행은 버리지 않는다 ─────────

@pytest.mark.parametrize("bad", [
    pytest.param(lambda scope: [{k: v for k, v in r.items() if k != "inter_issu_reg_csnu"}
                                for r in _interest_rows(scope)], id="missing-column"),
    pytest.param(lambda scope: _interest_rows(scope, 29), id="29-rows"),
    pytest.param(lambda scope: [{**r, "new_fld": "1"} for r in _interest_rows(scope)], id="unknown-column"),
    pytest.param(lambda scope: [], id="empty"),                 # rt_cd 0 인데 행 0 — 실패가 아니라 형태 이상
])
def test_shape_change_is_crit_and_keeps_the_rows(api_mod, monkeypatch, tmp_path, bad) -> None:
    monkeypatch.setattr(api_mod, "kis", _fake_kis([], interest=bad))
    db = tmp_path / "kis_rank.db"
    res = kis_rank.run(db, _kst("11:00"), CAL, clock=_clock())
    assert res.status is kis_rank.SlotStatus.SHAPE and res.rc == 2
    verdicts = _q(db, "SELECT DISTINCT verdict FROM kis_rank_runs WHERE endpoint='top_interest'")
    assert verdicts == [("shape",)]
    assert _q(db, "SELECT COUNT(*) FROM kis_rank_runs WHERE endpoint='top_interest' AND rows_json IS NULL") \
        == [(0,)]
    n = _q(db, "SELECT COUNT(*) FROM kis_top_interest WHERE scope='0001'")[0][0]
    assert n == len(bad("0001"))


def test_rerun_of_a_shape_slot_does_not_mix_a_second_response(api_mod, monkeypatch, tmp_path) -> None:
    """칸 이상이어도 응답은 받았다 — 다시 부르면 첫 응답 29행에 둘째 응답 30번째 행이 섞인다."""
    db = tmp_path / "kis_rank.db"
    monkeypatch.setattr(api_mod, "kis", _fake_kis([], interest=lambda scope: _interest_rows(scope, 29)))
    kis_rank.run(db, _kst("11:00"), CAL, clock=_clock())
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis(seen))
    res = kis_rank.run(db, _kst("11:05"), CAL, clock=_clock())
    assert seen == []
    assert res.rc == 0
    assert _q(db, "SELECT COUNT(*) FROM kis_top_interest WHERE scope='0001'") == [(29,)]



def test_non_dict_rows_hidden_behind_a_full_count_are_shape(api_mod, monkeypatch, tmp_path) -> None:
    """dict 행은 30개 그대로인데 배열에 다른 것이 섞였다 — 행 수만 보면 ok 로 통과한다."""
    monkeypatch.setattr(api_mod, "kis", _fake_kis([], interest=lambda scope: [*_interest_rows(scope), "x"]))
    res = kis_rank.run(tmp_path / "kis_rank.db", _kst("11:00"), CAL, clock=_clock())
    assert res.status is kis_rank.SlotStatus.SHAPE and res.rc == 2


def test_quota_after_backoff_is_warn_and_tries_every_target(api_mod, monkeypatch, tmp_path) -> None:
    seen: list[str] = []

    def kis(url: str, tr_id: str, params: dict[str, str]) -> dict[str, object]:
        seen.append(url)
        return {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다"}
    monkeypatch.setattr(api_mod, "kis", kis)
    db = tmp_path / "kis_rank.db"
    res = kis_rank.run(db, _kst("11:00"), CAL, clock=_clock())
    assert res.status is kis_rank.SlotStatus.FAILED and res.rc == 5
    assert _q(db, "SELECT DISTINCT verdict FROM kis_rank_runs") == [("quota",)]
    assert len(_q(db, "SELECT 1 FROM kis_rank_runs")) == 4     # 유량은 토큰과 달리 나머지 TR 도 부른다

# ── 토큰 ─────────────────────────────────────────────────────────────────────

def test_token_expiry_once_is_reissued_and_stored(api_mod, monkeypatch, tmp_path) -> None:
    calls = [0]
    ok = _fake_kis([])

    def kis(url: str, tr_id: str, params: dict[str, str]) -> dict[str, object]:
        calls[0] += 1
        if calls[0] == 1:
            return {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token"}
        return ok(url, tr_id, params)
    monkeypatch.setattr(api_mod, "kis", kis)
    res = kis_rank.run(tmp_path / "kis_rank.db", _kst("11:00"), CAL, clock=_clock())
    assert res.rc == 0
    assert calls[0] == 5                         # 첫 콜 만료 → 재발급 뒤 다시 + 나머지 3


def test_token_still_expired_stops_the_slot_as_warn(api_mod, monkeypatch, tmp_path) -> None:
    calls = [0]

    def kis(url: str, tr_id: str, params: dict[str, str]) -> dict[str, object]:
        calls[0] += 1
        return {"rt_cd": "1", "msg_cd": "EGW00121", "msg1": "기간이 만료된 token"}
    monkeypatch.setattr(api_mod, "kis", kis)
    db = tmp_path / "kis_rank.db"
    res = kis_rank.run(db, _kst("11:00"), CAL, clock=_clock())
    assert res.status is kis_rank.SlotStatus.FAILED and res.rc == 5
    assert calls[0] == 2                         # 재발급 1회 뒤 멈춘다 — 나머지 TR 을 두드리지 않는다
    assert _q(db, "SELECT verdict, msg_cd FROM kis_rank_runs") == [("token", "EGW00121")]


# ── 하루 요약(워치독) ─────────────────────────────────────────────────────────

def _seed_runs(db: Path, ok_hts: int, ok_interest: int, broken_scope_slots: int = 0) -> None:
    """그날 앞쪽 슬롯부터 ok 줄을 넣는다. broken_scope_slots 개 슬롯은 관심 범위 하나가 fail."""
    con = sqlite3.connect(db)
    kis_rank.ensure_schema(con)
    for i, t in enumerate(kis_rank.SLOTS):
        slot = f"{DAY.isoformat()}T{t:%H:%M}"
        rows = []
        if i < ok_hts:
            rows.append(("hts_top_view", "all", "ok"))
        if i < ok_interest:
            for scope in ("0001", "1001", "0000"):
                bad = i < broken_scope_slots and scope == "1001"
                rows.append(("top_interest", scope, "fail" if bad else "ok"))
        con.executemany("INSERT INTO kis_rank_runs (slot_kst, endpoint, scope, started_utc, n_calls, verdict) "
                        "VALUES (?, ?, ?, '2026-11-11T00:00:00Z', 1, ?)", [(slot, *r) for r in rows])
    con.commit()
    con.close()


@pytest.mark.parametrize(("ok_hts", "ok_interest", "broken", "status"), [
    (23, 25, 0, "ok"),                           # 23/25 까지 통과
    (22, 25, 0, "warn"),
    (25, 25, 3, "warn"),                         # 관심은 범위 3개가 다 ok 여야 그 슬롯이 ok — 25-3 = 22
    (25, 0, 0, "crit"),                          # 어느 TR 이든 0 이면 crit
])
def test_day_summary_counts_ok_slots_per_endpoint(tmp_path, ok_hts, ok_interest, broken, status) -> None:
    db = tmp_path / "kis_rank.db"
    _seed_runs(db, ok_hts, ok_interest, broken)
    s = kis_rank.summarize(db, DAY)
    assert s.status.value == status
    assert s.ok_hts == ok_hts and s.ok_interest == ok_interest - broken


def test_day_summary_without_the_file_is_crit(tmp_path) -> None:
    s = kis_rank.summarize(tmp_path / "kis_rank.db", DAY)
    assert s.status is kis_rank.DayStatus.CRIT
    assert not (tmp_path / "kis_rank.db").exists()          # 요약은 파일을 만들지 않는다
