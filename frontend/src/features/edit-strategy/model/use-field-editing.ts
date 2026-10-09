/**
 * 필드·목록 항목 편집의 상태 훅(WORKFLOW P4-02). Form 행·Graph 편집기·파이프라인 카드가 같은 확정·삭제 경로를
 * 쓴다 — 연산은 `form-transactions.ts` 가 만들고, 여기는 적용과 안내 상태만 든다.
 */
import { useState } from "react";

import { t } from "../../../shared/config";
import type { DocumentReference } from "./document-references";
import type { FormField, FormListItem } from "./form-projection";
import {
  fieldOperation,
  placeholderOf,
  removalBlockers,
  removeItemOperation,
  type InvalidDraft,
  type ObjectSection,
} from "./form-transactions";
import type { Scalar, SourceOperation } from "./source-transactions";
import type { SourceTransactions } from "./use-source-transactions";

/** Form 컨트롤의 feedback owner. Graph(`graph`)·스니펫(`snippet`)과 슬롯을 나눈다(Phase 4 감사 R2). */
export const FORM_OWNER = "form";
/** 컨트롤이 포커스를 가진 채 적용한다: 편집기로 포커스를 옮기면 컨트롤 blur가 같은 값을 다시 확정한다. */
export const NO_FOCUS = { focusEditor: false } as const;

/**
 * 필드 확정을 가로채는 계획(Graph의 `node_id` rename처럼 한 필드가 여러 위치를 바꿀 때). null이면 기본
 * `fieldOperation`, `invalid`면 그 사유(`form.invalid.<사유>`)를 안내하고 적용하지 않는다.
 */
export type CommitInvalidReason =
  | "duplicateNodeId"
  | "emptyNodeId"
  | "missingNode"
  | "duplicateIdentity"
  | "emptyIdentity";
export type CommitPlanner = (
  field: FormField,
  value: Scalar,
) =>
  | SourceOperation
  | readonly SourceOperation[]
  | { invalid: CommitInvalidReason }
  | null;

/**
 * 필드 값 확정과 무효 입력 안내. Form 행·Graph 편집기·파이프라인 카드의 확정 경로는 이 훅 하나다:
 * `planCommit` 이 가로채지 않으면 `fieldOperation(section, field, value)` 를 `transactions.apply` 로 넘긴다.
 * 적용 여부를 컨트롤에 돌려준다: 실패한 확정의 재시도 판정은 컨트롤 로컬이다(P4-02 리뷰 009/012 — 공유
 * feedback 슬롯은 다른 필드가 덮고, label은 목록 항목끼리 겹친다). `label` 은 feedback 문구에 들어간다.
 * `control` 은 `FieldControl` 에 그대로 펼치는 묶음이다(placeholder 규칙 `placeholderOf` 포함).
 */
export const useFieldCommit = ({
  section,
  field,
  transactions,
  owner = FORM_OWNER,
  label = field.key,
  planCommit,
}: {
  section: ObjectSection;
  field: FormField;
  transactions: SourceTransactions;
  owner?: string;
  label?: string;
  planCommit?: CommitPlanner;
}) => {
  const [invalid, setInvalid] = useState<string | null>(null);
  const onCommit = (value: Scalar): boolean => {
    setInvalid(null);
    const planned = planCommit?.(field, value) ?? null;
    if (planned !== null && "invalid" in planned) {
      setInvalid(t(`form.invalid.${planned.invalid}`));
      return false;
    }
    return transactions.apply(
      planned ?? fieldOperation(section, field, value),
      label,
      owner,
      NO_FOCUS,
    );
  };
  return {
    invalid,
    control: {
      placeholderValue: placeholderOf(section, field),
      onCommit,
      onValid: () => setInvalid(null),
      onInvalid: (reason: InvalidDraft) =>
        setInvalid(t(`form.invalid.${reason}`)),
    },
  };
};

/**
 * 목록 항목 삭제와 거부 판정. 다른 곳이 참조하면 지우지 않고 그 참조 목록을 돌려준다 — 문장은 화면이 만든다
 * (WORKFLOW P4-03 결정 3: Form 은 pointer 그대로, 캔버스는 스키마 사실로 자리 이름). 거부는 그 판정을 낸
 * 문서(tree)에만 붙는다 — 문서가 바뀌면(재색인 포함) 렌더 중 파생으로 사라진다. React key 가 pointer(인덱스)라
 * 인스턴스가 다른 항목에 재사용될 수 있다(리뷰 P2-2).
 */
export const useItemRemoval = (
  item: FormListItem,
  tree: unknown,
  transactions: SourceTransactions,
  owner: string = FORM_OWNER,
  label: string = item.summary,
) => {
  const [blockers, setBlockers] = useState<{
    tree: unknown;
    references: DocumentReference[];
  } | null>(null);
  const blocked =
    blockers !== null && blockers.tree === tree ? blockers.references : null;
  const remove = (): void => {
    const references = removalBlockers(tree, item);
    if (references.length > 0) {
      setBlockers({ tree, references });
      return;
    }
    setBlockers(null);
    transactions.apply(removeItemOperation(item), label, owner, NO_FOCUS);
  };
  return { blocked, remove };
};
