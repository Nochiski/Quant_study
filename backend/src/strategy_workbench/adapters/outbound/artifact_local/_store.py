from __future__ import annotations

import hashlib
import logging
import math
import shutil
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from pydantic import BeforeValidator, TypeAdapter, ValidationError

from strategy_workbench.application.backtest_run.facade.ports import (
    ArtifactCommit,
    BacktestArtifactUnreadableError,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult
from strategy_workbench.domain.canonical.facade.encoding import canonical_payload_json

logger = logging.getLogger(__name__)

# `_json_value` 가 쓴 `result.json` 을 되읽는 디코더. HTTP 가 요청 본문을 읽을 때와 같은 dataclass
# 검증(pydantic)이라 `__post_init__` 불변식을 다시 탄다.
# v2에만 있던 비용 사본은 읽기 경계에서 기존 불일치 검사를 유지한다. 새 모델·저장본·소비자는
# 이 목록을 사용하지 않는다. 체크섬은 이 검증에 앞서 원래 파일 바이트 그대로 확인한다.
_LEGACY_FLAT_COST_FIELDS = ("fee_bps", "slippage_bps", "participation_rate")


def _validate_legacy_manifest_costs(value: object) -> object:
    if not isinstance(value, dict):
        return value
    manifest = value.get("manifest")
    if not isinstance(manifest, dict):
        return value
    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        return value
    divergent = {
        name: (manifest[name], environment[name])
        for name in _LEGACY_FLAT_COST_FIELDS
        if name in manifest and name in environment and manifest[name] != environment[name]
    }
    if divergent:
        raise ValueError(
            "manifest records two execution cost models for one run — "
            f"run_id={manifest.get('run_id')} divergent={divergent}"
        )
    return value


_RESULT = TypeAdapter(
    Annotated[BacktestRunResult, BeforeValidator(_validate_legacy_manifest_costs)]
)


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def remove_orphan_staging(self) -> None:
        """커밋 도중 프로세스가 죽어 남은 staging(`.*.tmp`)을 모두 지운다.

        루트 하나를 서버 프로세스 하나가 쓴다는 전제라, 그 서버의 composition root 가 부팅 때 한 번
        부른다(연구 기록 DB 와 같다). 생성자에서 지우지 않는다 — 같은 루트를 여는 다른 프로세스가
        도는 run 의 staging 을 지우게 된다(run-worker spec D4). 못 지워도(Windows 잠금 등) 부팅은
        막지 않는다 — 남은 staging 은 run 이 읽지 않는다.
        """
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
            payload = canonical_payload_json(_json_value(result)).encode("utf-8")
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

    def discard_unfinished(self, run_id: str) -> None:
        """끝나지 못한 run 하나의 staging(`.{run_id}.*.tmp`)과 결과 디렉터리를 지운다(멱등).

        다른 프로세스(run worker)가 쓰던 run 을, 그 프로세스가 끝난 뒤 부모가 치울 때 쓴다
        (run-worker spec D5). 프로세스가 살아 있는 동안 부르지 않는다 — rename·열린 파일과 경합한다.
        """
        target = self._run_dir(run_id)
        for staging in self._root.glob(f".{run_id}.*.tmp"):
            shutil.rmtree(staging)
            logger.warning("unfinished run artifact staging removed — run_id=%s", run_id)
        if target.exists():
            shutil.rmtree(target)
            logger.warning("unfinished run artifact removed — run_id=%s", run_id)

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
