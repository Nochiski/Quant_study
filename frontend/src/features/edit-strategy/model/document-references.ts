/**
 * 문서 안 id 참조 탐색(WORKFLOW P4-03·D7 삭제 가드). 순수 함수이며 스키마를 모른다: 참조 키는
 * `<namespace>_id` 또는 `*_<namespace>_id`(`input_node_id`, `output_node_id`)이고, 값이 `id`와 같은
 * 곳의 pointer를 돌려준다. "참조가 있는가"만 답하고 유효성(사이클·타입)은 계속 backend validation이
 * 판정한다.
 */
import { escapePointerSegment } from "../../../shared/lib/yaml12";

export type DocumentReference = {
  /** 참조를 담은 필드의 pointer(`/factors/1/graph/nodes/0/factor_id`). */
  pointer: string;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export type FindReferencesOptions = {
  /**
   * 탐색을 이 pointer 아래로 좁힌다. 네임스페이스가 스코프를 가지면(`graph.nodes`의 `node`: 다른 팩터가
   * 같은 `node_id`를 써도 합법) 호출자가 그 스코프(`/factors/N/graph`)를 넘겨야 한다 — 전역 탐색은
   * 다른 그래프의 정의·출력을 참조로 오탐한다(Phase 4 감사 DEFECT-P4X-002). 생략하면 문서 전체.
   * `definingPointer`는 `within` 안에 있어야 한다 — 밖이면 정의 자리가 제외되지 않아 참조로 센다(리뷰 P2-6).
   */
  within?: string;
};

/**
 * `id`를 참조하는 필드 pointer 목록. `definingPointer`(정의하는 항목, 예: `/factors/0`) 아래는
 * 제외하고, 같은 정의 배열의 다른 항목이 가진 정의 키(`/factors/1/factor_id`처럼 배열 항목 바로 아래의
 * `<namespace>_id`)도 참조가 아니라 정의로 보아 제외한다. `options.within`이 있으면 그 subtree만 본다.
 */
export const findReferences = (
  tree: unknown,
  namespace: string,
  id: string,
  definingPointer = "",
  options: FindReferencesOptions = {},
): DocumentReference[] => {
  const key = `${namespace}_id`;
  const suffix = `_${key}`;
  const definingArray = definingPointer.replace(/\/\d+$/, "");
  const siblingDefinition = new RegExp(
    `^${definingArray.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/\\d+/${key}$`,
  );
  const found: DocumentReference[] = [];
  const excluded = (pointer: string): boolean =>
    definingPointer !== "" &&
    (pointer === definingPointer || pointer.startsWith(`${definingPointer}/`));
  const visit = (value: unknown, pointer: string): void => {
    if (excluded(pointer)) return;
    if (Array.isArray(value)) {
      value.forEach((item, index) => visit(item, `${pointer}/${index}`));
      return;
    }
    if (!isRecord(value)) return;
    for (const [name, child] of Object.entries(value)) {
      const childPointer = `${pointer}/${escapePointerSegment(name)}`;
      const references = name === key || name.endsWith(suffix);
      if (
        references &&
        child === id &&
        !excluded(childPointer) &&
        !(definingPointer !== "" && siblingDefinition.test(childPointer))
      )
        found.push({ pointer: childPointer });
      visit(child, childPointer);
    }
  };
  const within = options.within ?? "";
  visit(valueAtPointer(tree, within), within);
  return found;
};

const valueAtPointer = (tree: unknown, pointer: string): unknown => {
  let current: unknown = tree;
  for (const segment of pointer.split("/").slice(1)) {
    const key = segment.replaceAll("~1", "/").replaceAll("~0", "~");
    if (Array.isArray(current)) current = current[Number(key)];
    else if (isRecord(current)) current = current[key];
    else return undefined;
  }
  return current;
};
