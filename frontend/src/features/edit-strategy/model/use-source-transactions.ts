import { useCallback, useRef, useState } from "react";

import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import type { DocumentState } from "./document-state";
import type { SnippetEditFailure } from "./canonical-snippets";
import {
  planSourceOperation,
  type PlanFailure,
  type PlannedEdit,
  type SourceOperation,
} from "./source-transactions";

export type TransactionFailure =
  | PlanFailure
  | SnippetEditFailure
  | "composing"
  | "editor-unavailable"
  | "editor-inactive";

/** 트랜잭션을 잠그는 이유(우선순위: 문서 형식 → 편집기 비활성 → IME → 구문/버전 → 편집기 준비). */
export type TransactionDisabledReason =
  "json" | "inactive" | "composing" | "syntax" | "editor";

export type TransactionFeedback =
  | { status: "idle" }
  | { status: "applied"; owner: string; label: string }
  | {
      status: "error";
      owner: string;
      label: string;
      reason: TransactionFailure;
    };

/** 편집기 현재 텍스트·선택으로 편집 하나를 계획하는 함수. 기본은 `planSourceOperation`이다. */
export type SourcePlanner = (editor: {
  text: string;
  selection: { from: number; to: number };
}) =>
  | { status: "ok"; edit: PlannedEdit }
  | { status: "error"; reason: TransactionFailure };

/** 적용 뒤 편집기 처리. Form처럼 다른 컨트롤이 포커스를 가진 소비자는 `focusEditor: false`로 둔다. */
export type ApplyOptions = {
  /** 적용 뒤 편집기에 포커스를 준다(기본 true; 스니펫). 포커스 이동이 다른 컨트롤의 blur를 부르면 안 될 때 false. */
  focusEditor?: boolean;
};

export type SourceTransactions = {
  /**
   * 원시 연산 하나를 편집기 현재 텍스트에 계획·적용한다. 적용했으면 true. 실패하면 텍스트는 그대로고
   * feedback만 바뀌며 false — 호출자가 feedback 슬롯(인스턴스당 하나)을 다시 읽지 않아도 결과를 안다.
   */
  apply: (
    op: SourceOperation,
    label: string,
    owner?: string,
    options?: ApplyOptions,
  ) => boolean;
  /** 커서 문맥이 필요한 편집(스니펫)처럼 연산 하나로 표현되지 않는 계획을 같은 경로로 적용한다. 반환은 `apply`와 같다. */
  run: (
    planner: SourcePlanner,
    label: string,
    owner?: string,
    options?: ApplyOptions,
  ) => boolean;
  /** 마지막 결과(소비자 무관). `owner`로 어느 소비자(form·snippet·graph)의 것인지 구분한다(Phase 3 감사 R2). */
  feedback: TransactionFeedback;
  /**
   * `owner`의 마지막 결과. 슬롯은 owner별이라 스니펫 결과가 Form의 "반영됨"을 지우지 않는다(Phase 4 감사
   * R2: Graph가 세 번째 소비자로 들어오기 전에 분리). 소비자는 자기 owner 슬롯만 읽는다.
   */
  feedbackFor: (owner: string) => TransactionFeedback;
  onEditorReady: (editor: CodeEditorHandle | null) => void;
  /** yaml 문서이고, 편집기가 활성·준비됐고, IME 조합 중이 아니며, 현재 텍스트의 parse가 ok인 상태. */
  enabled: boolean;
  /** `enabled`가 거짓인 이유. UI가 같은 사실을 다시 계산하지 않는다(Phase 3 감사 R3). */
  disabled: TransactionDisabledReason | null;
};

type ScopedFeedback = {
  documentEpoch: number;
  scope: unknown;
  value: TransactionFeedback;
};

const IDLE: TransactionFeedback = { status: "idle" };

/**
 * source 트랜잭션을 편집기 handle에 적용하는 훅(WORKFLOW P3-02, spec D5). page가 한 번 만들어
 * 스니펫·Form·Graph가 공유한다(P4-04). 적용은 `CodeEditorHandle.replaceRange` 한 번(history 격리)이고,
 * 편집기 change가 reducer `edit`로 흘러 parse·compile이 이어진다.
 *
 * 계획은 항상 **편집기의 현재 텍스트**(`getText`)로 세운다: reducer의 `state.source`는 debounce 전에
 * 잠시 뒤처질 수 있고, 트랜잭션은 사용자가 보는 텍스트에 적용되어야 한다. 그래서 `planSourceOperation`이
 * 문서를 두 번 parse한다(계획 + preflight). 연산 한 번에 붙는 비용이라 호출자는 keystroke마다가 아니라
 * 확정(blur·Enter·버튼)마다 호출한다.
 *
 * feedback은 `documentEpoch`(다른 문서를 열면 사라진다)와 호출자가 주는 `scope` 값(같은 문서 안에서
 * 기능이 꺼졌다 켜지는 전이)에 묶인다.
 *
 * `enabled`/`disabled`는 UI 비활성화용 요약값이다. `apply`/`run`은 구문·버전 조건으로는 막지 않고
 * 편집기의 live 텍스트를 다시 parse하므로, reducer의 parse가 stale이거나 실패했어도 편집기 텍스트가
 * 유효하면 적용된다(fail-closed는 `planSourceOperation`의 preflight가 한다). Form·Graph는 `enabled`가
 * 참일 때만 연산을 넘긴다(stale pointer는 preflight가 못 잡는다). `editorActive`는 "편집기 handle이
 * 살아 있는가"다: Form/Graph view가 활성이어도 hidden 편집기가 살아 있으면 참이어야 한다(Phase 3 감사 R1).
 */
export const useSourceTransactions = (
  state: DocumentState,
  editorActive = true,
  scope: unknown = null,
): SourceTransactions => {
  const editor = useRef<CodeEditorHandle | null>(null);
  const [editorReady, setEditorReady] = useState(false);
  const [scoped, setScoped] = useState<ScopedFeedback | null>(null);
  const [scopedByOwner, setScopedByOwner] = useState<
    Record<string, ScopedFeedback>
  >({});
  const onEditorReady = useCallback((next: CodeEditorHandle | null): void => {
    editor.current = next;
    setEditorReady(next !== null);
  }, []);
  const parseCurrent =
    state.parse !== null &&
    state.parse.status === "ok" &&
    state.parsedVersion === state.sourceVersion;
  const disabled: TransactionDisabledReason | null =
    state.format !== "yaml"
      ? "json"
      : !editorActive
        ? "inactive"
        : state.composing
          ? "composing"
          : !parseCurrent
            ? "syntax"
            : !editorReady
              ? "editor"
              : null;
  const enabled = disabled === null;
  const setFeedback = useCallback(
    (value: TransactionFeedback): void => {
      const next = { documentEpoch: state.documentEpoch, scope, value };
      setScoped(next);
      if (value.status !== "idle")
        setScopedByOwner((current) => ({ ...current, [value.owner]: next }));
    },
    [scope, state.documentEpoch],
  );

  const run = useCallback(
    (
      planner: SourcePlanner,
      label: string,
      owner = "default",
      options: ApplyOptions = {},
    ): boolean => {
      if (state.format !== "yaml") {
        setFeedback({ status: "error", owner, label, reason: "yaml-only" });
        return false;
      }
      if (!editorActive) {
        setFeedback({
          status: "error",
          owner,
          label,
          reason: "editor-inactive",
        });
        return false;
      }
      const current = editor.current;
      if (current === null) {
        setFeedback({
          status: "error",
          owner,
          label,
          reason: "editor-unavailable",
        });
        return false;
      }
      if (state.composing) {
        setFeedback({ status: "error", owner, label, reason: "composing" });
        return false;
      }
      const result = planner({
        text: current.getText(),
        selection: current.getSelection(),
      });
      if (result.status === "error") {
        setFeedback({ status: "error", owner, label, reason: result.reason });
        if (options.focusEditor !== false) current.focus();
        return false;
      }
      const { edit } = result;
      current.replaceRange(edit.from, edit.to, edit.insert, edit.selection);
      current.scrollTo(edit.selection.from);
      if (options.focusEditor !== false) current.focus();
      setFeedback({ status: "applied", owner, label });
      return true;
    },
    [editorActive, setFeedback, state.composing, state.format],
  );

  const apply = useCallback(
    (
      op: SourceOperation,
      label: string,
      owner = "default",
      options: ApplyOptions = {},
    ): boolean =>
      run(
        ({ text }) => planSourceOperation(text, "yaml", op),
        label,
        owner,
        options,
      ),
    [run],
  );

  // 값으로 비교한다: memo identity에 기대면 React가 memo를 버릴 때 피드백이 조용히 사라진다(감사 DEFECT-P3X-004).
  const live = (entry: ScopedFeedback | null | undefined): TransactionFeedback =>
    entry !== null &&
    entry !== undefined &&
    entry.documentEpoch === state.documentEpoch &&
    Object.is(entry.scope, scope)
      ? entry.value
      : IDLE;
  const feedback = live(scoped);
  const feedbackFor = useCallback(
    (owner: string): TransactionFeedback => live(scopedByOwner[owner]),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `live`는 렌더마다 새로 만들지만 같은 값(state·scope)만 닫는다.
    [scopedByOwner, state.documentEpoch, scope],
  );

  return {
    apply,
    run,
    feedback,
    feedbackFor,
    onEditorReady,
    enabled,
    disabled,
  };
};
