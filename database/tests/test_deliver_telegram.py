"""텔레그램 발송(`deliver.telegram`) — 가짜 전송만 쓴다(실제 발송 없음).

핵심: 토큰·채팅 ID 가 반환값·로그·예외 문자열 어디에도 나오지 않는다.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from deliver import telegram as tg

TOKEN, CHAT = "987654:TOKEN-very-secret_XYZ", "-1001234567890"


@pytest.fixture
def env(tmp_path: Path) -> Path:
    p = tmp_path / "kael.env"
    p.write_text("# 주석\n"
                 f"export BOT_TOKEN='{TOKEN}'\n"
                 f'CHAT_ID_AIPLAYGROUND="{CHAT}"\n'
                 "CHAT_ID_LOG=-100555\n"
                 "OPENAI_API_KEY=sk-should-not-be-read\n", encoding="utf-8")
    return p


@pytest.fixture
def doc(tmp_path: Path) -> Path:
    p = tmp_path / "model_scores_20260925_morning.xlsx"
    p.write_bytes(b"PK\x03\x04 fake xlsx")
    return p


class Fake:
    def __init__(self, resp: dict[str, object] | None = None, exc: Exception | None = None):
        self.calls: list[tuple[str, dict[str, str], str, bytes]] = []
        self.resp = resp if resp is not None else {"ok": True, "result": {}}
        self.exc = exc

    def __call__(self, url: str, fields, name: str, data: bytes):
        self.calls.append((url, dict(fields), name, data))
        if self.exc is not None:
            raise self.exc
        return self.resp


def _clean(*texts: object) -> None:
    for t in texts:
        assert TOKEN not in str(t) and CHAT not in str(t), t


def test_read_secrets_only_wanted_keys(env: Path) -> None:
    got = tg.read_secrets(env, ("BOT_TOKEN", "CHAT_ID_AIPLAYGROUND"))
    assert got == {"BOT_TOKEN": TOKEN, "CHAT_ID_AIPLAYGROUND": CHAT}
    assert tg.read_secrets(env.parent / "none.env", ("BOT_TOKEN",)) == {}


def test_env_path_resolution(monkeypatch: pytest.MonkeyPatch, env: Path) -> None:
    monkeypatch.setenv("QL_ENV", str(env))
    assert tg.env_path() == env
    assert tg.env_path("/x/y.env") == Path("/x/y.env")
    monkeypatch.delenv("QL_ENV")
    assert tg.env_path() == Path.home() / "kael-system-v3" / ".env"


def test_send_document_success(env: Path, doc: Path, caplog: pytest.LogCaptureFixture) -> None:
    fake = Fake()
    with caplog.at_level(logging.DEBUG, logger="deliver.telegram"):
        res = tg.send_document(doc, "캡션", env_file=env, transport=fake)
    assert res == {"ok": True, "description": "sent"}
    (url, fields, name, data), = fake.calls
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    assert fields == {"chat_id": CHAT, "caption": "캡션"}
    assert name == doc.name and data == doc.read_bytes()
    _clean(res, caplog.text)
    assert "CHAT_ID_AIPLAYGROUND" in caplog.text


def test_env_var_is_used_and_other_chat_key(monkeypatch: pytest.MonkeyPatch, env: Path,
                                            doc: Path) -> None:
    monkeypatch.setenv("QL_ENV", str(env))
    fake = Fake()
    res = tg.send_document(doc, "c", chat_key="CHAT_ID_LOG", transport=fake)
    assert res["ok"] and fake.calls[0][1]["chat_id"] == "-100555"


def test_transport_exception_is_redacted(env: Path, doc: Path,
                                         caplog: pytest.LogCaptureFixture) -> None:
    exc = ConnectionError(f"failed https://api.telegram.org/bot{TOKEN}/sendDocument chat={CHAT}")
    with caplog.at_level(logging.DEBUG, logger="deliver.telegram"):
        res = tg.send_document(doc, "c", env_file=env, transport=Fake(exc=exc))
    assert res["ok"] is False
    assert "ConnectionError" in str(res["description"]) and tg.REDACTED in str(res["description"])
    _clean(res, caplog.text)


def test_api_error_description_is_redacted(env: Path, doc: Path) -> None:
    fake = Fake({"ok": False, "description": f"Bad Request: chat {CHAT} not found"})
    res = tg.send_document(doc, "c", env_file=env, transport=fake)
    assert res["ok"] is False and "Bad Request" in str(res["description"])
    _clean(res)


def test_missing_secrets_or_file_do_not_send(tmp_path: Path, doc: Path) -> None:
    fake = Fake()
    empty = tmp_path / "empty.env"
    empty.write_text("BOT_TOKEN=\n", encoding="utf-8")
    res = tg.send_document(doc, "c", env_file=empty, transport=fake)
    assert res["ok"] is False and "BOT_TOKEN" in str(res["description"])
    res = tg.send_document(tmp_path / "nope.xlsx", "c", env_file=empty, transport=fake)
    assert res["ok"] is False and "파일 없음" in str(res["description"])
    assert fake.calls == []


def test_dry_run_checks_presence_without_network(env: Path, doc: Path, tmp_path: Path,
                                                 caplog: pytest.LogCaptureFixture) -> None:
    fake = Fake()
    with caplog.at_level(logging.DEBUG, logger="deliver.telegram"):
        res = tg.send_document(doc, "c", env_file=env, transport=fake, dry_run=True)
        res2 = tg.send_document(doc, "c", env_file=tmp_path / "none.env", transport=fake,
                                dry_run=True)
    assert fake.calls == []
    assert res["ok"] is True and "BOT_TOKEN 있음" in str(res["description"])
    assert res2["ok"] is False and "없음" in str(res2["description"])
    _clean(res, res2, caplog.text)


def test_caption_is_truncated_to_telegram_limit(env: Path, doc: Path) -> None:
    fake = Fake()
    tg.send_document(doc, "가" * 2000, env_file=env, transport=fake)
    assert len(fake.calls[0][1]["caption"]) == tg.CAPTION_MAX


def test_captions_fixed_format() -> None:
    top = [(1, "005930", "삼성전자"), (2, "000660", "SK하이닉스"), (3, "035420", "")]
    c = tg.caption_daily("2026-09-25", "morning", "v4_rank@0.1", top, 317, 603)
    assert c.splitlines() == ["[모델 점수] 2026-09-25 morning · v4_rank@0.1",
                              "상위5: 1.삼성전자 · 2.SK하이닉스 · 3.035420",
                              "순위 317 · 제외 286 · 모집단 603"]
    w = tg.caption_weekly("2026-W39", "2026-09-25", "v4_rank@0.1", top, 30, 5, 5, 4)
    assert w.splitlines() == ["[주간 후보] 2026-W39 (기준 2026-09-25) · v4_rank@0.1",
                              "상위5: 1.삼성전자 · 2.SK하이닉스 · 3.035420",
                              "후보 30 · 신규 5 · 이탈 5 · 거래일 판 4/5"]
    assert tg.caption_daily("d", "b", "s", [], 0, 0).splitlines()[1] == "상위5: -"


def test_caption_daily_correction_line() -> None:
    """Q9 — 정정 발송(n ≥ 1)만 끝에 '정정 n · 판 id · 생성 시각' 줄을 단다.
    첫 발송(n = 0)은 그대로."""
    top = [(1, "005930", "삼성전자")]
    base = tg.caption_daily("2026-09-25", "morning", "scope@1.0", top, 317, 603)
    first = tg.caption_daily("2026-09-25", "morning", "scope@1.0", top, 317, 603,
                             correction=0, build_id="m_x", generated_at="2026-09-25T00:43:12Z")
    assert first == base
    c = tg.caption_daily("2026-09-25", "morning", "scope@1.0", top, 317, 603,
                         correction=2, build_id="m_x", generated_at="2026-09-25T00:43:12Z")
    assert c.splitlines() == [*base.splitlines(), "정정 2 · 판 m_x · 생성 2026-09-25T00:43:12Z"]


def test_multipart_body_shape() -> None:
    body, ctype = tg._multipart({"chat_id": "1", "caption": "한글"}, "a.xlsx", b"DATA")
    boundary = ctype.split("boundary=")[1]
    assert ctype.startswith("multipart/form-data; boundary=")
    assert body.count(f"--{boundary}".encode()) == 4
    assert b'name="document"; filename="a.xlsx"' in body
    assert "한글".encode() in body and b"DATA" in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_redact_longest_first() -> None:
    assert tg.redact(f"x {TOKEN} y {CHAT}", (CHAT, TOKEN)) == f"x {tg.REDACTED} y {tg.REDACTED}"
    assert tg.redact("plain", ("",)) == "plain"
