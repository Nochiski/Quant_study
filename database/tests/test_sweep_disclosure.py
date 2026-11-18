"""sweep_disclosure — 창 대조·재스윕 판정(순수 함수). 검수 D H3(2026-09-10) 후속."""
import os
import sqlite3
import tempfile
from datetime import UTC, date, datetime

# sweep_disclosure 는 import 시점에 `api` 를 통해 kael .env 를 읽는다(api.py:35). CI 에는 .env 가 없으므로
# 임시 .env 를 만들어 QL_ENV 로 가리킨다 — test_api_dart_keys 와 같은 방식. 키 값은 더미다.
if not os.environ.get("QL_ENV"):
    _env = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")  # noqa: SIM115  # reason: 파일을 닫은 뒤 경로를 계속 써야 한다
    _env.write("KRX_API_KEY=krx\nKRX_ID=id\nKRX_PW=pw\nDART_API_KEY_2=k2\n")
    _env.close()
    os.environ["QL_ENV"] = _env.name

import sweep_disclosure as sd  # reason: 위 QL_ENV 설정이 import 보다 먼저여야 한다
from daily import dart_list


def test_reconcile_accepts_ledger_excess_as_source_side_deletion():
    # 09-10 실측: total_count 44,156 = 수신 44,156 < 적재 고유 44,162. 초과 6건은 DART 가 지운 공시라
    # 원장에만 남은 것 — "받은 것을 다 적었나" 는 참이므로 완료로 적고 초과분만 기록한다.
    r = sd.reconcile(total_count=44156, n_recv=44156, n_db=44162)
    assert r.ok and r.excess == 6


def test_reconcile_still_flags_paging_loss_and_store_loss():
    assert not sd.reconcile(total_count=100, n_recv=99, n_db=99).ok       # 페이징 누락
    assert not sd.reconcile(total_count=100, n_recv=100, n_db=98).ok      # 적재 유실
    assert sd.reconcile(total_count=100, n_recv=100, n_db=100).excess == 0


def test_window_is_reswept_while_inside_lookback():
    assert sd.is_resweep("20260930", today="20261005", lookback_days=30)      # 분기 끝난 지 5일
    assert not sd.is_resweep("20260930", today="20261105", lookback_days=30)  # 36일 — 종결
    assert sd.is_resweep("20261231", today="20261005", lookback_days=0)       # 진행 중 창은 항상
    assert not sd.is_resweep("20260630", today="20261005", lookback_days=30)


# ── 스윕 기록을 읽는 쪽(daily.dart_list) 과의 계약 ───────────────────────────────
def test_list_sweep_reader_uses_the_sweeps_own_unit_name_states_and_window_id():
    """`daily.dart_list` 는 스윕이 `ingest_log` 에 남긴 기록으로 "공시 목록을 다 받았는가"를 판정한다 — 유닛
    이름 · 통과 상태 · 창 id 가 스윕과 어긋나면 기록을 못 찾아 매일 실패하거나, 멈춘 스윕을 통과시킨다."""
    assert dart_list.SWEEP_NAME == sd.NAME
    assert set(dart_list.RECONCILED) == {*sd.DONE, "open"}
    for day in (date(2026, 1, 1), date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 8),
                date(2026, 11, 17), date(2026, 12, 31)):
        q_start = date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
        assert sd.windows(q_start, sd.q_end(day)) == [dart_list.quarter_window(day.strftime("%Y%m%d"))]


def _ingest_log(tmp_path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "dart.db")
    con.execute("""CREATE TABLE ingest_log(
      name TEXT NOT NULL, corp_code TEXT NOT NULL,
      bsns_year TEXT NOT NULL DEFAULT '', reprt_code TEXT NOT NULL DEFAULT '',
      fs_div TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL, n_rows INTEGER NOT NULL, note TEXT, ts TEXT NOT NULL,
      PRIMARY KEY (name, corp_code, bsns_year, reprt_code, fs_div))""")
    return con


def test_list_sweep_reader_judges_the_record_the_sweep_marks(tmp_path):
    """스윕의 `mark()` 가 실제로 적은 행을 읽는다: 대조를 통과한 창(open · ok)은 통과, 한 번도 통과하지 못하고
    멈춘 창(partial) · 대조가 어긋난 창(mismatch)은 실패, 기록이 없는 창도 실패. 건수(여기선 1건)는 판정과 무관하다."""
    con = _ingest_log(tmp_path)
    day = "20200102"                                    # 과거 날짜 — mark() 는 지금 시각으로 적는다
    assert not dart_list.list_sweep(con, day).ok        # 기록 없음
    window = dart_list.quarter_window(day)
    sd.mark(con, *window, "partial", 1, 1, 1, "000")
    assert not dart_list.list_sweep(con, day).ok        # 첫 스윕이 도중에 멈췄다 — 통과한 적이 없다
    sd.mark(con, *window, "open", 1, 1, 1, "000")
    got = dart_list.list_sweep(con, day)
    assert got.ok and (got.status, got.total_count) == ("open", 1)
    sd.mark(con, *window, "mismatch", 1, 1, 1, "000")
    assert not dart_list.list_sweep(con, day).ok
    sd.mark(con, *window, "ok", 1, 1, 1, "000")
    assert dart_list.list_sweep(con, day).ok
    assert not dart_list.list_sweep(con, day, since_ts="2999-01-01T00:00:00").ok    # 그 뒤에 돈 스윕이 없다
    con.close()


class _Clock:
    """`sweep_disclosure.mark` 가 읽는 시계 — 기록 시각을 테스트가 정한다."""

    def __init__(self) -> None:
        self.at = "2026-11-17T09:10:37"

    def now(self, tz: object = None) -> datetime:
        return datetime.strptime(self.at, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


def test_a_sweep_that_stops_midway_keeps_the_time_the_window_last_reconciled(tmp_path, monkeypatch):
    """스윕은 쪽마다 창 기록을 partial 로 덮는다. 06:00 재수집의 스윕이 도중에 멈추면(키 소진 · 응답 오류 ·
    강제 종료) 전날 18:05 에 대조를 통과한 사실까지 지워져 08:10 원장 건전성 `dart.disclosure.rows` 가 실패하고
    확정판을 짓지 않는다. 마지막으로 대조를 통과한 시각은 partial 로 덮여도 남아야 한다 — 다만 그 시각은
    멈춘 런보다 앞이므로, 그 런 자신의 `filings` 관문(이번 스윕 시작 이후를 요구)은 여전히 실패다."""
    clock = _Clock()
    monkeypatch.setattr(sd, "datetime", clock)
    con = _ingest_log(tmp_path)
    day = "20261117"
    window = dart_list.quarter_window(day)
    sd.mark(con, *window, "open", 24357, 24357, 244, "000")       # 11-17 18:10 KST — 대조 통과
    clock.at = "2026-11-17T21:42:05"                              # 11-18 06:42 KST — 3쪽째에서 멈춤
    sd.mark(con, *window, "partial", 300, 24364, 244, "000")

    got = dart_list.list_sweep(con, day)                          # 08:10 원장 건전성(기준 = D 00:00 KST)
    assert got.ok and got.status == "partial"
    assert got.reconciled_ts == "2026-11-17T09:10:37"
    assert not dart_list.list_sweep(con, day, since_ts="2026-11-17T21:41:16").ok   # 멈춘 런의 filings 관문
    assert not dart_list.list_sweep(con, "20261118").ok           # 다음 날(D+1) 것으로는 통과하지 않는다
    con.close()


def test_a_mismatch_forgets_the_earlier_reconciliation(tmp_path, monkeypatch):
    """대조가 어긋난 창(mismatch — 페이징 누락 · 적재 유실)은 앞선 통과를 이어받지 않는다. 그 뒤 런이 쪽을 도는
    동안의 partial 도 통과가 아니다 — 다시 대조를 통과해야 한다."""
    clock = _Clock()
    monkeypatch.setattr(sd, "datetime", clock)
    con = _ingest_log(tmp_path)
    day = "20261117"
    window = dart_list.quarter_window(day)
    sd.mark(con, *window, "open", 24357, 24357, 244, "000")
    clock.at = "2026-11-17T21:45:36"
    sd.mark(con, *window, "mismatch", 24300, 24364, 244, "000")
    assert not dart_list.list_sweep(con, day).ok
    clock.at = "2026-11-18T03:30:40"
    sd.mark(con, *window, "partial", 100, 24380, 244, "000")
    got = dart_list.list_sweep(con, day)
    assert not got.ok and got.reconciled_ts is None
    clock.at = "2026-11-18T03:37:12"
    sd.mark(con, *window, "open", 24380, 24380, 244, "000")
    assert dart_list.list_sweep(con, day).ok
    con.close()
