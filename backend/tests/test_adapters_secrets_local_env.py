"""`.env`에서 이름 몇 개의 값만 읽기(S07 서버 키·봇 토큰, 2026-10-22 리뷰 #605). 가짜 값만 쓴다."""

from __future__ import annotations

from pathlib import Path

from strategy_workbench.adapters.outbound.secrets_local.facade.env import read_env_values


def test_reads_only_the_named_values_with_quotes_and_inline_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# 주석 줄",
                "export FAKE_APP_KEY=fake-app-0123",
                "FAKE_SECRET=\"fake 'secret' 4567\"  # 따옴표 뒤 주석",
                "FAKE_ACCOUNT=5012345678 # 줄 끝 주석",
                "FAKE_TOKEN='abc#def'",
                "OTHER_REAL_KEY=must-not-be-read",
                "FAKE_EMPTY=",
            ]
        ),
        encoding="utf-8",
    )
    names = {"FAKE_APP_KEY", "FAKE_SECRET", "FAKE_ACCOUNT", "FAKE_TOKEN", "FAKE_EMPTY", "MISSING"}
    assert read_env_values(env, names) == {
        "FAKE_APP_KEY": "fake-app-0123",
        "FAKE_SECRET": "fake 'secret' 4567",
        "FAKE_ACCOUNT": "5012345678",
        "FAKE_TOKEN": "abc#def",
        "FAKE_EMPTY": "",
    }


def test_server_env_keeps_the_old_meaning_quotes_only_no_escapes(tmp_path: Path) -> None:
    """재리뷰 6: S07 서버 `.env`는 예전처럼 감싼 따옴표만 벗기고 이스케이프를 풀지 않는다.

    BOM은 떼어 첫 줄 이름을 알아본다. 줄은 `\\n`·`\\r\\n`으로만 나눈다.
    """
    env = tmp_path / ".env"
    env.write_bytes(
        b"\xef\xbb\xbfFAKE_APP_KEY=fake-app-0123\r\n"
        b'FAKE_SECRET="a\\"b"\r\n'
        b'FAKE_TOKEN="x\\ny"\r\n'
        b"FAKE_NOTE=one\x0btwo\n"
    )
    assert read_env_values(env, {"FAKE_APP_KEY", "FAKE_SECRET", "FAKE_TOKEN", "FAKE_NOTE"}) == {
        "FAKE_APP_KEY": "fake-app-0123",
        "FAKE_SECRET": "a\\",
        "FAKE_TOKEN": "x\\ny",
        "FAKE_NOTE": "one\x0btwo",
    }
