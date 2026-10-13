import { expect, it } from "vitest";
import { initialDocumentState } from "../model/document-state";
import { projectDraftDiff } from "../model/diff-projection";
it("잘못된 원문도 정확한 텍스트 변경을 보존한다", () => {
  const state = {
    ...initialDocumentState("yaml", 'title: "broken\n'),
    savedSource: "title: old\n",
  };
  expect(projectDraftDiff(state)).toMatchObject({
    sourceBase: "saved-revision",
    source: { added: 1, removed: 1 },
  });
});
it("새 문서는 빈 원문과 비교한다", () => {
  expect(
    projectDraftDiff(initialDocumentState("yaml", "title: new\n")),
  ).toMatchObject({
    sourceBase: "empty-draft",
    source: { added: 2, removed: 0 },
  });
});
