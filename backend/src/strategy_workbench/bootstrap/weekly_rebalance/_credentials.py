"""키움 모의 키와 주문지 알림 봇 토큰을 어디서 읽을지 정한다(S07).

- 기본(맥): 설정 화면이 저장한 사용자 설정 폴더의 `secrets.json`.
- 서버: `STRATEGY_WORKBENCH_KIWOOM_ENV_FILE`이 있으면 그 `.env`에서
  `PAPER_TRADING_KIWOOM_{APP_KEY,SECRET_KEY,ACCOUNT}_<쌍>` 세 이름만 읽는다(쌍 번호는
  `STRATEGY_WORKBENCH_KIWOOM_KEY_PAIR`, 기본 2). 다른 이름은 읽지 않는다. 그 파일에 실전 키가
  함께 있어도 이 코드의 주소는 모의 서버 하나라 실전 키는 키움이 8030으로 거절한다
  (2026-10-21 사용자 결정).
- 주문지 알림: `STRATEGY_WORKBENCH_TELEGRAM_ENV_FILE`의 `.env`에서
  `STRATEGY_WORKBENCH_TELEGRAM_TOKEN_VAR`가 가리키는 이름 하나만 읽고, 보낼 곳은
  `STRATEGY_WORKBENCH_TELEGRAM_CHAT_ID`다.
값은 어떤 메시지에도 싣지 않는다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from strategy_workbench.adapters.outbound.kiwoom_mock.facade.broker import KiwoomCredentials
from strategy_workbench.adapters.outbound.secrets_local.facade.env import read_env_values
from strategy_workbench.adapters.outbound.telegram_notice.facade.sender import TelegramNotice
from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    AccountUnavailableError,
    NoticeError,
)
from strategy_workbench.bootstrap.facade.secrets import saved_kiwoom_credentials

ENV_FILE_ENV = "STRATEGY_WORKBENCH_KIWOOM_ENV_FILE"
KEY_PAIR_ENV = "STRATEGY_WORKBENCH_KIWOOM_KEY_PAIR"
DEFAULT_KEY_PAIR = "2"
_NAME_PREFIX = "PAPER_TRADING_KIWOOM_"
TELEGRAM_ENV_FILE_ENV = "STRATEGY_WORKBENCH_TELEGRAM_ENV_FILE"
TELEGRAM_TOKEN_VAR_ENV = "STRATEGY_WORKBENCH_TELEGRAM_TOKEN_VAR"
TELEGRAM_CHAT_ID_ENV = "STRATEGY_WORKBENCH_TELEGRAM_CHAT_ID"


def load_credentials() -> KiwoomCredentials:
    env_file = os.environ.get(ENV_FILE_ENV)
    if env_file:
        return env_file_credentials(
            Path(env_file).expanduser(), os.environ.get(KEY_PAIR_ENV, DEFAULT_KEY_PAIR)
        )
    saved = saved_kiwoom_credentials()
    if saved is None:
        raise AccountUnavailableError("saved kiwoom mock keys not found — save them in settings")
    return saved


def env_file_credentials(path: Path, pair: str) -> KiwoomCredentials:
    if not re.fullmatch(r"\d{1,2}", pair):
        raise AccountUnavailableError(f"invalid key pair number — pair={pair!r}")
    wanted = {
        f"{_NAME_PREFIX}APP_KEY_{pair}": "app_key",
        f"{_NAME_PREFIX}SECRET_KEY_{pair}": "secret_key",
        f"{_NAME_PREFIX}ACCOUNT_{pair}": "account_number",
    }
    try:
        found = read_env_values(path, wanted)
    except OSError as error:
        raise AccountUnavailableError(
            f"cannot read the kiwoom env file — file={path.name} error={type(error).__name__}"
        ) from error
    values = {wanted[name]: value for name, value in found.items()}
    missing = sorted(name for name, field in wanted.items() if not values.get(field))
    if missing:
        raise AccountUnavailableError(f"kiwoom mock keys missing in env file — names={missing}")
    try:
        return KiwoomCredentials(**values)
    except ValueError as error:
        raise AccountUnavailableError(f"invalid kiwoom mock keys in env file — {error}") from error


def telegram_notice() -> TelegramNotice:
    names = (TELEGRAM_ENV_FILE_ENV, TELEGRAM_TOKEN_VAR_ENV, TELEGRAM_CHAT_ID_ENV)
    env_file, token_var, chat_id = (os.environ.get(name, "") for name in names)
    unset = [name for name in names if not os.environ.get(name)]
    if unset:
        raise NoticeError(f"telegram notice is not configured — unset={unset}")
    path = Path(env_file).expanduser()
    try:
        token = read_env_values(path, (token_var,)).get(token_var, "")
    except OSError as error:
        raise NoticeError(
            f"cannot read the telegram env file — file={path.name} error={type(error).__name__}"
        ) from error
    if not token:
        raise NoticeError(f"telegram token missing in env file — name={token_var}")
    return TelegramNotice(token, chat_id)
