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
