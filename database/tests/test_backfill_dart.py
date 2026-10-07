"""backfill_dart.call — 네트워크 예외 재시도(배포 묶음 3 T15, N-23 ③ 동결 예외).

`api.dart` 만 가짜로 바꾸고(네트워크 경계) 콜 로그는 실제 sqlite 에 남긴다.
대기(`time.sleep`)는 기록만 한다.
"""
import os
import sqlite3
import tempfile

import pytest

# backfill_dart 는 import 시점에 `api` 를 통해 kael .env 를 읽는다(api.py:20-30).
# CI 에는 .env 가 없으므로 임시 .env 를 만들어 QL_ENV 로 가리킨다 — test_sweep_disclosure 와
# 같은 방식. 키 값은 더미다.
if not os.environ.get("QL_ENV"):
    _env = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")  # noqa: SIM115  # reason: 파일을 닫은 뒤 경로를 계속 써야 한다
    _env.write("KRX_API_KEY=krx\nKRX_ID=id\nKRX_PW=pw\nDART_API_KEY_2=k2\n")
    _env.close()
    os.environ["QL_ENV"] = _env.name

import backfill_dart as bf  # reason: 위 QL_ENV 설정이 import 보다 먼저여야 한다

# 운영 스키마(backfill_dart.main 의 dart_call_log)와 같은 열
_CALL_LOG = ("CREATE TABLE dart_call_log (endpoint TEXT NOT NULL, corp_code TEXT, bsns_year TEXT, "
             "reprt_code TEXT, fs_div TEXT, status TEXT NOT NULL, n_rows INTEGER NOT NULL, "
             "ts TEXT NOT NULL, key_id TEXT NOT NULL DEFAULT 'kael')")
_OK = {"status": "000", "list": [{"rcept_no": "20260829000123", "corp_code": "00126380"}]}


def _call(monkeypatch: pytest.MonkeyPatch, replies: list[object]):
    """replies 를 차례로 돌려주는(예외면 던지는) 가짜 api.dart 로 tsstkAqDecsn 한 유닛을 부른다.

    반환: (call 결과, api 호출 수, 콜 로그 status 목록, 대기 초 목록)
    """
    sent: list[str] = []
    waits: list[float] = []

    def fake_dart(ep: str, key: str | None = None, **params: object) -> object:
        sent.append(ep)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(bf.api, "dart", fake_dart)
    monkeypatch.setattr(bf.time, "sleep", waits.append)
    con = sqlite3.connect(":memory:")
    con.execute(_CALL_LOG)
    got = bf.call(con, "tsstkAqDecsn", "00126380", "k2", "dummy-key")
    log = [r[0] for r in con.execute("SELECT status FROM dart_call_log ORDER BY rowid")]
    return got, len(sent), log, waits


def test_network_exception_is_retried_until_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """G1 — 08-29T06:40:16 tsstkAqDecsn 재현: 예외 2번 뒤 000 이면 3번째 시도에서 받는다.

    옛 코드는 첫 시도에서 포기했다(재시도 0회). 예외 경로의 상태는 응답 형태 신호가 붙어
    'exc/resp_NoneType' 이 되는데(j=None → normalize_rows) `st != "exc"` 로 검사했기 때문이다.
    """
    replies: list[object] = [bf.api.DartError("ConnectionError — 가짜"),
                             bf.api.DartError("ReadTimeout — 가짜"), _OK]
    (rows, verdict, status), n_sent, log, waits = _call(monkeypatch, replies)
    assert n_sent == 3
    assert (verdict, status, len(rows)) == ("ok", "000", 1)
    assert log == ["exc/resp_NoneType", "exc/resp_NoneType", "000"]
    # 재시도 간격은 기존 상수 그대로 — 콜마다 PACE, 재시도 전 RETRY_BASE × 2^attempt
    assert waits == [bf.PACE, bf.RETRY_BASE, bf.PACE, bf.RETRY_BASE * 2, bf.PACE]


def test_network_exception_gives_up_after_retry_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """예외가 계속되면 기존 재시도 횟수(RETRY_MAX)만큼 더 부르고 error 로 돌려준다.

    ingest_log 에는 지금처럼 error 로 남는다(호출부가 verdict 를 적는다).
    """
    replies: list[object] = [bf.api.DartError("ConnectionError — 가짜")
                             for _ in range(bf.RETRY_MAX + 1)]
    (rows, verdict, status), n_sent, log, _ = _call(monkeypatch, replies)
    assert n_sent == bf.RETRY_MAX + 1
    assert (rows, verdict, status) == ([], "error", "exc/resp_NoneType")
    assert log == ["exc/resp_NoneType"] * (bf.RETRY_MAX + 1)


def test_server_error_statuses_are_retried_until_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """800·900(DART 서버 일시 오류)은 지금처럼 재시도한다 — 고친 술어의 다른 반쪽(v == "retry")."""
    replies: list[object] = [{"status": "800"}, {"status": "900"}, _OK]
    (rows, verdict, status), n_sent, log, waits = _call(monkeypatch, replies)
    assert n_sent == 3
    assert (verdict, status, len(rows)) == ("ok", "000", 1)
    assert log == ["800", "900", "000"]
    assert waits == [bf.PACE, bf.RETRY_BASE, bf.PACE, bf.RETRY_BASE * 2, bf.PACE]


@pytest.mark.parametrize("reply", [{"status": "013"}, {"status": "100"}, {"status": 0}])
def test_other_statuses_are_not_retried(monkeypatch: pytest.MonkeyPatch, reply: dict) -> None:
    """예외가 아닌 응답은 지금처럼 한 번에 끝난다(무자료·요청 오류·문자열이 아닌 status)."""
    (_, verdict, status), n_sent, _, _ = _call(monkeypatch, [reply])
    assert n_sent == 1
    assert status == reply["status"]
    assert verdict == bf.VERDICT.get(reply["status"], "unknown")
