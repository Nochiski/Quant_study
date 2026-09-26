import type { StrategyDocument } from "../../../shared/api";
import type { DocumentState } from "./document-state";

/**
 * 업그레이드를 제안해야 하는 backend 구조 진단 코드.
 *
 * - `structure.unsupported_schema_version`: 문서가 스스로 지원하지 않는 버전이라고 적었다.
 * - `structure.legacy_shape`: 버전 줄은 현재 버전인데 본문이 1.0 문법이다(P1-05). 이 경우도
 *   사용자가 할 일은 업그레이드라 배너를 같이 띄운다. 어떤 문법이 1.0인지는 backend가 판정하고
 *   frontend는 코드만 본다(은퇴 버전 문자열을 갖지 않는다는 기존 규칙 그대로).
 */
const UPGRADE_SUGGESTING_CODES = new Set([
  "structure.unsupported_schema_version",
  "structure.legacy_shape",
]);

/** 열린 revision 중 배너 판정에 필요한 봉투 필드만 받는다(생성 타입에서 파생, 복제 아님). */
export type StoredRevisionMeta = Pick<
  StrategyDocument,
  "revision" | "generated" | "requires_upgrade"
>;

/**
 * 업그레이드 배너가 무엇을 제안할지(WORKFLOW P2-02).
 *
 * - `upgradeable`: backend compile이 현재 텍스트의 schema 버전을 지원하지 않는다고 답했다.
 *   `POST /strategy-documents/upgrade`로 현재 버전 텍스트를 받아 편집기에 넣는다. 변환할 수 없는
 *   버전이면 backend가 `strategy_document.not_upgradeable` 422로 거부하고 배너가 그 문구를 보인다
 *   (frontend는 어떤 버전이 은퇴 버전인지 알지 않는다 — Phase 2 감사 DEFECT-P2X-002).
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
 * 판정은 backend 진단으로만 한다(지원 버전·은퇴 버전·1.0 문법 판정 모두 backend가 소유). 현재
 * 텍스트가 무슨 버전이든 backend가 "지원하지 않음" 또는 "1.0 문법"이라 답했으면 업그레이드를
 * 제안하고, 변환 가능 여부는 업그레이드 endpoint가 판정한다.
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
