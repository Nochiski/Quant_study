import type { StrategyDocument, UpgradedDocument } from "../../../shared/api";
import { t, type MessageKey } from "../../../shared/config";
import type { DocumentState } from "./document-state";

/** 업그레이드 응답 warning 코드(OpenAPI enum, P2-09). 생성 타입에서 파생한다(복제 아님). */
export type UpgradeWarningCode = UpgradedDocument["warnings"][number]["code"];

/**
 * warning 코드별 제목(P3-01). 문장은 backend `message`가 한글로 완성해 보내므로 여기는 코드가 무엇을
 * 뜻하는지 한 줄 제목만 둔다. `Record`라 생성 enum 에 코드가 늘면 typecheck 가 번역 누락을 막는다.
 * 배너에 그리는 것은 P3-02 다(WORKFLOW P3-02 "P2-09 가 남긴 배너 소비 항목").
 */
const UPGRADE_WARNING_TITLES: Record<UpgradeWarningCode, MessageKey> = {
  "strategy_document.upgrade_missing_policy_conflict":
    "upgrade.warning.strategy_document.upgrade_missing_policy_conflict",
  "strategy_document.upgrade_weighting_rule_changed":
    "upgrade.warning.strategy_document.upgrade_weighting_rule_changed",
  "strategy_document.upgrade_environment_unavailable":
    "upgrade.warning.strategy_document.upgrade_environment_unavailable",
};

export const upgradeWarningTitle = (code: UpgradeWarningCode): string =>
  t(UPGRADE_WARNING_TITLES[code]);

/**
 * 업그레이드를 제안해야 하는 backend 구조 진단 코드.
 *
 * - `structure.unsupported_schema_version`: 버전 줄이 은퇴 버전이고 업그레이더가 받아 줄 문서다(backend
 *   `upgrade_refusal`이 판정).
 *
 * 업그레이더가 거절할 문서는 배너를 띄우지 않는다. 배너를 띄우면 누를 때마다 실패하는 버튼이
 * 된다(P1-05 DEFECT-P105-001과 같은 모순). 진단 문장이 제자리에서 고칠 방법을 말한다.
 * - `structure.legacy_shape`: 버전 줄은 현재 버전인데 본문에 1.0 문법이 섞였다. backend는 문서가 선언한
 *   버전을 믿어 업그레이드하지 않는다(lang2 Phase 2 감사 NB-1).
 * - `structure.not_upgradeable_schema_version`: backend `upgrade_refusal`이 거절한 문서다(#267 DEFECT-2).
 */
const UPGRADE_SUGGESTING_CODES = new Set([
  "structure.unsupported_schema_version",
]);

/** 열린 revision 중 배너 판정에 필요한 봉투 필드만 받는다(생성 타입에서 파생, 복제 아님). */
export type StoredRevisionMeta = Pick<
  StrategyDocument,
  "revision" | "generated" | "requires_upgrade"
>;

/**
 * 업그레이드 배너가 무엇을 제안할지(WORKFLOW P2-02).
 *
 * - `upgradeable`: backend compile이 현재 텍스트를 업그레이드할 수 있는 은퇴 버전이라고 답했다.
 *   `POST /strategy-documents/upgrade`로 현재 버전 텍스트를 받아 편집기에 넣는다. 그래도 거부되면
 *   (`strategy_document.not_upgradeable` 등 422) 배너가 그 문구를 보인다(frontend는 어떤 버전이 은퇴
 *   버전인지 알지 않는다 — Phase 2 감사 DEFECT-P2X-002).
 * - `frozen-generated`: legacy JSON 동결 row. generated source는 이미 현재 버전이므로 업그레이드
 *   endpoint를 부르지 않고 새 revision 저장만 제안한다(P1-03 리뷰 잔여 위험 1).
 * - `none`: 배너 없음.
 */
export type UpgradeAvailability =
  { kind: "none" } | { kind: "upgradeable" } | { kind: "frozen-generated" };

const compileSuggestsUpgrade = (state: DocumentState): boolean =>
  state.compiled !== null &&
  state.compiledVersion === state.sourceVersion &&
  state.compiled.diagnostics.some((diagnostic) =>
    UPGRADE_SUGGESTING_CODES.has(diagnostic.code),
  );

/**
 * 판정은 backend 진단 코드로만 한다(지원 버전·은퇴 버전·1.0 문법·업그레이드 가능 여부 판정 모두
 * backend가 소유). 텍스트의 버전 문자열은 보지 않는다.
 */
export const decideDocumentUpgrade = (
  state: DocumentState,
  stored: StoredRevisionMeta | null,
): UpgradeAvailability => {
  // 현재 텍스트가 우선한다: legacy 동결 row를 열었더라도 편집기에 은퇴 버전 텍스트를 넣었으면
  // 업그레이드가 유일한 진행 경로다(P2-02 리뷰 P2-001).
  if (compileSuggestsUpgrade(state)) return { kind: "upgradeable" };
  if (
    stored !== null &&
    stored.requires_upgrade &&
    stored.generated &&
    state.baseRevision === stored.revision
  )
    return { kind: "frozen-generated" };
  return { kind: "none" };
};
