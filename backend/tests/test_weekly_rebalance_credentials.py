"""S07 서버 키 읽기: `.env`에서 정한 세 이름만 읽는다. 값은 모두 가짜다."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountUnavailableError,
    NoticeError,
)
from strategy_workbench.bootstrap.weekly_rebalance.facade.command import (
    ENV_FILE_ENV,
    KEY_PAIR_ENV,
    TELEGRAM_CHAT_ID_ENV,
    TELEGRAM_ENV_FILE_ENV,
    TELEGRAM_TOKEN_VAR_ENV,
    load_credentials,
    telegram_notice,
)

ENV = """# 실전 키도 같은 파일에 있다
KIWOOM_APP_KEY=real-app-key-should-not-be-read
KIWOOM_SECRET_KEY=real-secret-should-not-be-read
PAPER_TRADING_KIWOOM_APP_KEY_1=fake-app-1-0123456789
PAPER_TRADING_KIWOOM_SECRET_KEY_1=fake-secret-1-0123456789
PAPER_TRADING_KIWOOM_APP_KEY_2="fake-app-2-0123456789"
export PAPER_TRADING_KIWOOM_SECRET_KEY_2='fake-secret-2-0123456789'
PAPER_TRADING_KIWOOM_ACCOUNT_2=5012345678
BOT_TOKEN=123456:fake-bot-token
OTHER_BOT_TOKEN=999999:other-fake-token
"""


def test_env_file_reads_only_the_chosen_paper_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(ENV)
    monkeypatch.setenv(ENV_FILE_ENV, str(env))
    credentials = load_credentials(tmp_path / "repo")
    assert (credentials.app_key, credentials.secret_key, credentials.account_number) == (
        "fake-app-2-0123456789",
        "fake-secret-2-0123456789",
        "5012345678",
    )
    assert "fake" not in repr(credentials)


def test_missing_names_are_reported_without_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(ENV)
    monkeypatch.setenv(ENV_FILE_ENV, str(env))
    monkeypatch.setenv(KEY_PAIR_ENV, "1")  # 1번 쌍에는 계좌번호가 없다
    with pytest.raises(AccountUnavailableError) as raised:
        load_credentials(tmp_path / "repo")
    assert "PAPER_TRADING_KIWOOM_ACCOUNT_1" in str(raised.value)
    assert "fake-app-1" not in str(raised.value)
    monkeypatch.setenv(KEY_PAIR_ENV, "2; rm")
    with pytest.raises(AccountUnavailableError):
        load_credentials(tmp_path / "repo")


def test_telegram_token_comes_from_the_named_variable_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(ENV)
    with pytest.raises(NoticeError) as raised:
        telegram_notice()  # 설정이 없으면 보내지 않는다
    assert TELEGRAM_CHAT_ID_ENV in str(raised.value)
    monkeypatch.setenv(TELEGRAM_ENV_FILE_ENV, str(env))
    monkeypatch.setenv(TELEGRAM_TOKEN_VAR_ENV, "BOT_TOKEN")
    monkeypatch.setenv(TELEGRAM_CHAT_ID_ENV, "-1000000000001")
    notice = telegram_notice()
    assert "fake-bot-token" not in repr(notice)
    monkeypatch.setenv(TELEGRAM_TOKEN_VAR_ENV, "MISSING_TOKEN")
    with pytest.raises(NoticeError) as raised:
        telegram_notice()
    assert "MISSING_TOKEN" in str(raised.value) and "fake" not in str(raised.value)
