/**
 * backend 진단 중 오류만 모은다. 안내 단계 상태와 목록 항목 카드의 오류 톤이 같은 모음을 쓴다 — 한쪽만 경고를
 * 세거나 항목 자신의 진단을 빠뜨리면 단계는 완료인데 카드는 빨간 어긋남이 생긴다. 수는 모음의 길이다.
 */
import type { DocumentDiagnostic } from "./document-state";
import type { FormListItem, FormListSection, FormSection } from "./form-projection";

type DiagnosticOwner = { readonly diagnostics: readonly DocumentDiagnostic[] };

export const errorsOf = (diagnostics: readonly DocumentDiagnostic[]): DocumentDiagnostic[] =>
  diagnostics.filter((diagnostic) => diagnostic.severity === "error");

/** 칸 여럿(또는 진단을 가진 무엇이든 여럿)의 오류. */
export const fieldErrors = (owners: readonly DiagnosticOwner[]): DocumentDiagnostic[] =>
  owners.flatMap((owner) => errorsOf(owner.diagnostics));

/** 목록 항목 하나: 항목 자신의 진단과 항목 칸의 진단. */
export const itemErrors = (item: FormListItem): DocumentDiagnostic[] => [
  ...errorsOf(item.diagnostics),
  ...fieldErrors(item.fields),
];

/** 목록 하나: 목록 자신의 진단과 모든 항목. */
export const listErrors = (list: FormListSection): DocumentDiagnostic[] => [
  ...errorsOf(list.diagnostics),
  ...list.items.flatMap(itemErrors),
];

/** 섹션 하나의 오류: object 섹션이면 섹션 자신·칸·중첩 목록, 목록 섹션이면 목록 전체. */
export const sectionErrors = (section: FormSection): DocumentDiagnostic[] =>
  section.kind === "list"
    ? listErrors(section)
    : [
        ...errorsOf(section.diagnostics),
        ...fieldErrors(section.fields),
        ...section.lists.flatMap(listErrors),
      ];

export const itemErrorCount = (item: FormListItem): number => itemErrors(item).length;
