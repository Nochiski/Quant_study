"""모델 레지스트리 (플랜 `docs/plans/2026-09-24-v3-merge.md` M2 W1-c · T2.1).

`config/models/*.toml` 한 파일 = 모델 하나(`ModelSpec`). TOML 인 이유: 서버 venv 에 PyYAML 이 없고
`tomllib` 은 표준 라이브러리다(09-29 오케스트레이터). 적재 때 `ModelSpec.from_dict`(모르는 키 거절)
→ `validate()` 를 거쳐, 오류가 하나라도 있으면 파일 이름과 오류 목록 전부를 담아 실패한다 —
잘못된 설정으로 점수를 조용히 내지 않는다.

기본 디렉터리는 패키지 기준 `database/config/models`(`src/model/` 의 두 단계 위)다.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from model.contracts import ModelSpec

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "models"


def load(config_dir: Path | None = None) -> dict[str, ModelSpec]:
    """디렉터리의 `*.toml` 전부 → {spec_id: ModelSpec}(파일 이름순)."""
    root = CONFIG_DIR if config_dir is None else Path(config_dir)
    specs: dict[str, ModelSpec] = {}
    where: dict[str, str] = {}
    for path in sorted(root.glob("*.toml")):
        try:
            with path.open("rb") as fh:
                spec = ModelSpec.from_dict(tomllib.load(fh))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"{path.name}: 레지스트리 항목을 읽지 못했다 — {e}") from e
        errs = spec.validate()
        if errs:
            raise ValueError(f"{path.name}: {spec.spec_id} 검증 실패 {len(errs)}건 — {errs}")
        if spec.spec_id in specs:
            raise ValueError(f"{path.name}: spec_id {spec.spec_id} 중복({where[spec.spec_id]})")
        specs[spec.spec_id] = spec
        where[spec.spec_id] = path.name
    return specs


def get(spec_id: str, config_dir: Path | None = None) -> ModelSpec:
    """`model_id@version` 하나. 없으면 KeyError(등록된 목록을 함께 보인다)."""
    specs = load(config_dir)
    if spec_id not in specs:
        raise KeyError(f"레지스트리에 {spec_id!r} 없음 — 등록: {sorted(specs)}")
    return specs[spec_id]


def all_specs(config_dir: Path | None = None) -> tuple[ModelSpec, ...]:
    """등록된 모델 전부(spec_id 순)."""
    specs = load(config_dir)
    return tuple(specs[k] for k in sorted(specs))
