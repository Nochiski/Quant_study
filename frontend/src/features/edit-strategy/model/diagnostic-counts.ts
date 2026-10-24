/**
 * backend 진단의 오류 수. 안내 단계 상태와 목록 항목 카드의 오류 톤이 같은 셈을 쓴다 — 한쪽만 경고를 세거나
 * 항목 자신의 진단을 빠뜨리면 단계는 완료인데 카드는 빨간 어긋남이 생긴다.
 */
import type { DocumentDiagnostic } from "./document-state";
import type { FormListItem, FormListSection, FormSection } from "./form-projection";

export const errorCount = (diagnostics: readonly DocumentDiagnostic[]): number =>
  diagnostics.filter((diagnostic) => diagnostic.severity === "error").length;

/** 목록 항목 하나: 항목 자신의 진단과 항목 칸의 진단. */
export const itemErrorCount = (item: FormListItem): number =>
  errorCount(item.diagnostics) +
  item.fields.reduce((sum, field) => sum + errorCount(field.diagnostics), 0);

/** 목록 하나: 목록 자신의 진단과 모든 항목. */
export const listErrorCount = (list: FormListSection): number =>
  errorCount(list.diagnostics) +
  list.items.reduce((sum, item) => sum + itemErrorCount(item), 0);

const errorsOf = (diagnostics: readonly DocumentDiagnostic[]): DocumentDiagnostic[] =>
  diagnostics.filter((diagnostic) => diagnostic.severity === "error");

const listErrors = (list: FormListSection): DocumentDiagnostic[] => [
  ...errorsOf(list.diagnostics),
  ...list.items.flatMap((item) => [
    ...errorsOf(item.diagnostics),
    ...item.fields.flatMap((field) => errorsOf(field.diagnostics)),
  ]),
];

/** 섹션 하나의 오류 진단: object 섹션이면 섹션 자신·칸·중첩 목록, 목록 섹션이면 목록 전체. */
export const sectionErrors = (section: FormSection): DocumentDiagnostic[] =>
  section.kind === "list"
    ? listErrors(section)
    : [
        ...errorsOf(section.diagnostics),
        ...section.fields.flatMap((field) => errorsOf(field.diagnostics)),
        ...section.lists.flatMap(listErrors),
      ];

/** 칸 여럿의 오류 진단. */
export const fieldErrors = (fields: readonly { diagnostics: readonly DocumentDiagnostic[] }[]) =>
  fields.flatMap((field) => errorsOf(field.diagnostics));
