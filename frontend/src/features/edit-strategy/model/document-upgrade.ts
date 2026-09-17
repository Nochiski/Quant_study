import type { DocumentState } from "./document-state";

/** schema 1.0 문서를 1.1로 다시 쓰는 backend 변환의 입력 버전(spec D2·D3). */
export const LEGACY_SCHEMA_VERSION = "1.0";
const UNSUPPORTED_VERSION_CODE = "structure.unsupported_schema_version";

/** 열린 revision 중 배너 판정에 필요한 봉투 정보만 받는다(문서 응답 전체를 복제하지 않음). */
export type StoredRevisionMeta = {
  revision: number;
  generated: boolean;
  requires_upgrade: boolean;
};

/**
 * 업그레이드 배너가 무엇을 제안할지(WORKFLOW P2-02).
 *
 * - `upgradeable`: 현재 텍스트가 `schema_version: "1.0"`이고 backend compile이 지원하지 않는
 *   버전이라고 답했다. `POST /strategy-documents/upgrade`로 1.1 텍스트를 받아 편집기에 넣는다.
 * - `frozen-generated`: legacy JSON 동결 row. generated source는 이미 1.1이므로 업그레이드
 *   endpoint를 부르지 않고 새 revision 저장만 제안한다(P1-03 리뷰 잔여 위험 1).
 * - `none`: 배너 없음.
 */
export type UpgradeAvailability =
  { kind: "none" } | { kind: "upgradeable" } | { kind: "frozen-generated" };

const currentTreeSchemaVersion = (state: DocumentState): unknown => {
  if (
    state.parse === null ||
    state.parsedVersion !== state.sourceVersion ||
    state.parse.status !== "ok"
  )
    return undefined;
  return state.parse.tree.schema_version;
};

const compileRejectsSchemaVersion = (state: DocumentState): boolean =>
  state.compiled !== null &&
  state.compiledVersion === state.sourceVersion &&
  state.compiled.diagnostics.some(
    (diagnostic) => diagnostic.code === UNSUPPORTED_VERSION_CODE,
  );

/**
 * 판정은 backend 진단(지원 버전은 backend가 소유)과 현재 parse tree의 `schema_version` 값으로만
 * 한다. frontend는 어떤 버전이 지원되는지 알지 못하며, 1.0 문자열은 변환 입력 계약일 뿐이다.
 */
export const decideDocumentUpgrade = (
  state: DocumentState,
  stored: StoredRevisionMeta | null,
): UpgradeAvailability => {
  if (
    stored !== null &&
    stored.requires_upgrade &&
    stored.generated &&
    state.baseRevision === stored.revision
  )
    return { kind: "frozen-generated" };
  if (
    compileRejectsSchemaVersion(state) &&
    currentTreeSchemaVersion(state) === LEGACY_SCHEMA_VERSION
  )
    return { kind: "upgradeable" };
  return { kind: "none" };
};
