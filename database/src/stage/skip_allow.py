"""게이트 SKIP 허용표 — 모델이 쓰는 원천에서 표 밖 SKIP 은 실패다(K1-7a).

근거: 로드맵 §8 K1-7('모델이 쓰는 표의 SKIP 은 허용 목록 밖 0') ·
DECISIONS §6-6('SKIP = 통과' 결함) · N-42 Q4(정지 조건 '필수 검사 SKIP = 실패' 일괄 승인).
허용표의 정본은 이 파일 하나다(P4). 층마다 여기서 읽는다.

  equity         `equity.gates.run_all` — equity 표 전부
                 (빌드와 재판정 `gate` 가 같은 함수를 쓴다)
  stage          `factor_inputs.build` 가 직접 읽는 stage 표(`STAGE_SOURCES` + 장 마감 판 T 행 원천
                 `stg_flow_postclose_kiwoom`)의 판 기록 — fi 입력 가드. stage 층 전체(68표)는
                 범위 밖이다
  factor_inputs  `factor_inputs.gates.run_all`
  model          `model.gates.run_all` — 기록형 게이트는 `model.gates.RECORD_ONLY`

판정: SKIP 의 (층, 게이트, 사유[, 표]) 가 아래 `ALLOW` 에 없으면
  · 폐기형 게이트 → FAIL. 그 판을 올리지 않는다(층의 원래 FAIL 처리 그대로).
  · 기록형 게이트 → 경고. PASS + metrics.warn 이라 판은 올린다
    (model `status_of` 가 'warn' 으로 적는다).
게이트가 남긴 detail·metrics 는 그대로 두고 metrics 에 `skip_reason`·`skip_not_allowed` 를 더한다.

사유 코드는 detail 의 첫 낱말에서 ':' 앞이다. equity 는 폐쇄 어휘(`equity.gates.SKIP_REASONS`)
가 detail 전체이고, stage·fi·model 은 'no_fixtures — 설명' · 'ledger_unavailable:kiwoom' 처럼
첫 낱말이 코드다.

범위 밖(다른 PR·후속): 원장 `ledger_health` 의 필수 검사 SKIP(K1-7b) · stage 건전성 C1~C6 ·
equity `catalog`(EG11·EG5c·EG3_firm_mktcap)·`contract`(EGC) — fi·모델이 읽지 않는 산출이다.

허용 항목을 더할 때는 '왜 이 SKIP 이 정상인지' 한 줄을 함께 적는다(빈 근거는 테스트가 막는다).
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from .gates import GateResult, GateStatus

LAYERS = ("stage", "equity", "factor_inputs", "model")
ANY_GATE = "*"          # 그 층 모든 게이트(upstream_failed 처럼 게이트를 가리지 않는 사유)


@dataclass(frozen=True)
class Allow:
    layer: str
    gate: str                        # 게이트 이름. ANY_GATE 면 그 층 모든 게이트
    reason: str                      # 사유 코드(`reason_code`)
    why: str                         # 이 SKIP 이 정상인 이유 한 줄
    tables: tuple[str, ...] = ()     # 비면 그 층 모든 표. model 은 spec_id


# 처음 내용(10-10) = 코드·테스트에서 찾은 '정상 운영에서 나는 SKIP' + 로컬 equity 사본
# (10-03 아침 판 30표) 실측. 서버 판의 실제 목록은 `scripts/gate_skips.py` 로 확인한다.
# 10-10 리뷰: EG21 no_coverage(opinion_daily 한정)를 지웠다 — 서버 `gate_skips.py --last 10` 에서
# 마지막 발생이 m_20261003T004557_542182Z 이고, 그 뒤 판은 base_date 세션이 23 을 넘어 나지 않는다.
ALLOW: tuple[Allow, ...] = (
    # ── equity ───────────────────────────────────────────────────────────────
    Allow("equity", ANY_GATE, "upstream_failed",
          "앞 게이트가 이미 FAIL 이라 판은 폐기된다 — 판정을 바꾸지 않는다(GATES §7-1)"),
    Allow("equity", "EG1", "declaration_table",
          "선언표(universe_policy·dataset_profile·factor_readiness)는 "
          "행수 등식이 정의되지 않는다(GATES §0-2)"),
    Allow("equity", "EG2", "dimension_table",
          "차원 표는 available_date 가 없어 PIT 불변식의 대상이 아니다(GATES §0-2)"),
    Allow("equity", "EG13", "dimension_table",
          "차원 표는 available_date 가 없다(EG2 와 같은 근거)"),
    Allow("equity", "EG5a", "no_previous_build",
          "첫 빌드 — 비교할 직전 판이 없다"),
    Allow("equity", "EG5a", "inputs_changed",
          "일일 빌드는 매번 새 stage 판을 고정해 늘 이 사유다 — "
          "결정성은 같은 스냅샷 반복 빌드 해시(K1-5·DESIGN P42)가 본다"),
    Allow("equity", "EG5a", "rules_changed",
          "규칙 판본을 올린 첫 빌드는 같은 입력이어도 산출이 달라지는 것이 정상이다"),
    # ── stage(fi 가 직접 읽는 WISE 4표) ──────────────────────────────────────
    Allow("stage", "G4", "no_fixtures",
          "stage 골든은 unit_scale 열만 필수다(STAGE_DESIGN §9) — "
          "WISE 4표는 unit_scale 열·픽스처가 없다(값 대조 공백은 후속)"),
    Allow("stage", "G5", "no_baseline",
          "첫 빌드 — 직전 판의 G1 계수가 없다"),
    Allow("stage", "G9", "no_cross_check",
          "교차 소스 선언(cross_check)이 없는 표 — 원천이 WISE 하나다"),
    # ── stage(장 마감 판 T 행 원천 — 컷오버 PR-5, `data/model_db/stage` 의 이 표 한정) ──────
    Allow("stage", "G4", "golden_inherited",
          "첫 수집(10-14) 전엔 원장에 행이 없어 자기 골든을 둘 수 없다 — unit_scale 13열은 같은 "
          "규칙·같은 원천 TR(ka10060) 의 stg_flow_daily_kiwoom 골든이 지킨다(PR-2 golden_from)",
          ("stg_flow_postclose_kiwoom",)),
    Allow("stage", "G6", "write_mode=first_write_wins",
          "원장이 INSERT OR IGNORE(첫 관측 유지)라 같은 키의 나중 값이 없다 — 판 사이 덮어쓰기 "
          "검사의 대상이 아니다(daily.postclose.insert_first)",
          ("stg_flow_postclose_kiwoom",)),
    Allow("stage", "G8", "not_blob",
          "원장이 JSON blob 이 아니라 열 단위 표(ka10060_investor_flows)다 — blob 보존 등식이 "
          "정의되지 않는다",
          ("stg_flow_postclose_kiwoom",)),
    # ── factor_inputs ────────────────────────────────────────────────────────
    Allow("factor_inputs", ANY_GATE, "upstream_failed",
          "FG0 이 이미 FAIL 이라 판은 폐기된다 — 판정을 바꾸지 않는다"),
    # ── model ────────────────────────────────────────────────────────────────
    Allow("model", ANY_GATE, "upstream_failed",
          "MG0 이 이미 FAIL 이라 판은 폐기된다 — 판정을 바꾸지 않는다"),
    Allow("model", "MG5", "no_previous",
          "같은 spec·basis 의 첫 판 — 비교할 직전 성공 판이 없다(기록형)"),
)


def reason_code(detail: str) -> str:
    """SKIP detail 의 사유 코드 — 첫 낱말에서 ':' 앞."""
    words = detail.split(maxsplit=1)
    return words[0].split(":", 1)[0] if words else ""


def _check_layer(layer: str) -> None:
    if layer not in LAYERS:
        raise ValueError(f"skip_allow layer 는 {LAYERS} 중 하나: got={layer!r}")


def find(layer: str, gate: str, reason: str, table: str | None = None) -> Allow | None:
    """(층, 게이트, 사유, 표) 를 덮는 허용 항목. 없으면 None."""
    _check_layer(layer)
    for a in ALLOW:
        if (a.layer == layer and a.gate in (gate, ANY_GATE) and a.reason == reason
                and (not a.tables or table in a.tables)):
            return a
    return None


def judge(layer: str, g: GateResult, *, table: str | None = None,
          record_only: Collection[str] = ()) -> GateResult:
    """게이트 결과 하나의 층 판정. 허용표 밖 SKIP 만 바꾸고 나머지는 그대로 돌려준다."""
    _check_layer(layer)
    if g.status is not GateStatus.SKIP:
        return g
    code = reason_code(g.detail)
    if find(layer, g.name, code, table) is not None:
        return g
    metrics: dict[str, object] = {**g.metrics, "skip_reason": code, "skip_not_allowed": True}
    detail = (f"skip_not_allowed({code}) — 허용표(stage/skip_allow.py) 밖 SKIP 은 "
              f"통과가 아니다: {g.detail}")
    if g.name in record_only:
        return GateResult(g.name, GateStatus.PASS, f"warn: {detail}", {**metrics, "warn": True})
    return GateResult(g.name, GateStatus.FAIL, detail, metrics)


def recorded_skips(recorded: object) -> list[tuple[str, str]]:
    """판 기록(MANIFEST·_meta·_runs 의 gates — 목록 또는 {이름: 기록})의 SKIP →
    `(게이트, 사유 코드)`. 기록이 없거나 모양이 다르면 빈 목록."""
    if isinstance(recorded, Mapping):
        items = [{"name": k, **v} for k, v in recorded.items() if isinstance(v, Mapping)]
    elif isinstance(recorded, list):
        items = [g for g in recorded if isinstance(g, Mapping)]
    else:
        return []
    return [(str(g.get("name")), reason_code(str(g.get("detail") or ""))) for g in items
            if g.get("status") == GateStatus.SKIP.value]


def violations(layer: str, recorded: object, *, table: str | None = None) -> list[str]:
    """판 기록에서 허용표 밖 SKIP 을 `게이트:사유` 로 모은다."""
    _check_layer(layer)
    return [f"{name}:{code}" for name, code in recorded_skips(recorded)
            if find(layer, name, code, table) is None]
