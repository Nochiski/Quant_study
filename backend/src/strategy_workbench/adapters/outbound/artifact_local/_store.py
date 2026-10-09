from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from strategy_workbench.application.backtest_run.facade.ports import (
    ArtifactCommit,
    BacktestArtifactUnreadableError,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult

logger = logging.getLogger(__name__)

# `_json_value` 가 쓴 `result.json` 을 되읽는 디코더. HTTP 가 요청 본문을 읽을 때와 같은 dataclass
# 검증(pydantic)이라 `__post_init__` 불변식을 다시 탄다.
_RESULT = TypeAdapter(BacktestRunResult)


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        # 커밋 도중 프로세스가 죽으면 staging 이 남는다. 부팅 때 여기서 한 번 치운다 — 루트 하나를
        # 서버 프로세스 하나가 쓴다는 전제다(연구 기록 DB 와 같다). 못 지워도(Windows 잠금 등)
        # 부팅은 막지 않는다 — 남은 staging 은 run 이 읽지 않는다.
        for staging in self._root.glob(".*.tmp"):
            try:
                shutil.rmtree(staging)
            except OSError:
                logger.exception("orphan run artifact staging not removed — name=%s", staging.name)
            else:
                logger.warning("orphan run artifact staging removed — name=%s", staging.name)

    def commit(self, result: BacktestRunResult) -> ArtifactCommit:
        target = self._run_dir(result.manifest.run_id)
        if target.exists():
            raise FileExistsError(f"run artifact already exists: {result.manifest.run_id}")
        staging = (self._root / f".{result.manifest.run_id}.{uuid4().hex}.tmp").resolve()
        if staging.parent != self._root:
            raise ValueError("staging path escapes artifact root")
        staging.mkdir()
        try:
            payload = json.dumps(
                _json_value(result),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            result_path = staging / "result.json"
            with result_path.open("xb") as stream:
                stream.write(payload)
                stream.flush()
            staging.replace(target)
        except BaseException:
            if staging.exists() and staging.parent == self._root:
                shutil.rmtree(staging)
            raise
        return ArtifactCommit(sha256=hashlib.sha256(payload).hexdigest())

    def load(self, run_id: str, *, sha256: str) -> BacktestRunResult:
        try:
            payload = (self._run_dir(run_id) / "result.json").read_bytes()
        except FileNotFoundError as error:
            raise BacktestArtifactUnreadableError(
                f"run result file is missing — run_id={run_id}"
            ) from error
        if hashlib.sha256(payload).hexdigest() != sha256:
            raise BacktestArtifactUnreadableError(
                f"run result file does not match its recorded sha256 — run_id={run_id}"
            )
        try:
            return _RESULT.validate_json(payload)
        except ValidationError as error:
            # 해시가 맞는데 못 읽으면 결과 모델이 파일을 쓴 뒤 바뀐 것이다. 서버 경로·파일 원문은
            # 싣지 않는다.
            first = error.errors()[0]
            raise BacktestArtifactUnreadableError(
                f"run result file does not decode — run_id={run_id} "
                f"errors={error.error_count()} first={first['loc']}: {first['msg']}"
            ) from error

    def discard(self, run_id: str) -> None:
        """Delete one exact committed run after application-level cancellation wins."""

        target = self._run_dir(run_id)
        if target.exists():
            shutil.rmtree(target)

    def _run_dir(self, run_id: str) -> Path:
        target = (self._root / run_id).resolve()
        if target.parent != self._root:
            raise ValueError("run id escapes artifact root")
        return target


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("artifact values must be finite or null")
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported artifact value: {type(value).__name__}")
