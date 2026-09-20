import type { StrategyDocument } from "../../../shared/api";
import type { DocumentState } from "./document-state";

const UNSUPPORTED_VERSION_CODE = "structure.unsupported_schema_version";

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

const compileRejectsSchemaVersion = (state: DocumentState): boolean =>
  state.compiled !== null &&
  state.compiledVersion === state.sourceVersion &&
  state.compiled.diagnostics.some(
    (diagnostic) => diagnostic.code === UNSUPPORTED_VERSION_CODE,
  );

/**
 * 판정은 backend 진단으로만 한다(지원 버전·은퇴 버전 모두 backend가 소유). 현재 텍스트가 무슨
 * 버전이든 backend가 "지원하지 않음"이라 답했으면 업그레이드를 제안하고, 변환 가능 여부는 업그레이드
 * endpoint가 판정한다.
 */
export const decideDocumentUpgrade = (
  state: DocumentState,
  stored: StoredRevisionMeta | null,
): UpgradeAvailability => {
  // 현재 텍스트가 우선한다: legacy 동결 row를 열었더라도 편집기에 은퇴 버전 텍스트를 넣었으면
  // 업그레이드가 유일한 진행 경로다(P2-02 리뷰 P2-001).
  if (compileRejectsSchemaVersion(state)) return { kind: "upgradeable" };
  if (
    stored !== null &&
    stored.requires_upgrade &&
    stored.generated &&
    state.baseRevision === stored.revision
  )
    return { kind: "frozen-generated" };
  return { kind: "none" };
};
