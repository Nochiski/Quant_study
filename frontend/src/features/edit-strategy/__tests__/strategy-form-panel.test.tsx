import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { CodeEditorHandle } from "../../../shared/ui/code-editor";
import {
  initialDocumentState,
  type DocumentDiagnostic,
  type DocumentState,
} from "../model/document-state";
import { projectForm } from "../model/form-projection";
import { draftOf } from "../model/form-transactions";
import type { JsonSchema } from "../model/schema-navigator";
import {
  useSourceTransactions,
  type SourceTransactions,
} from "../model/use-source-transactions";
import { StrategyFormPanel } from "../ui/strategy-form-panel";

afterEach(cleanup);

const SCHEMA = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const MINIMAL = readBackendFixture(
  "strategy_documents/quality_momentum.minimal.yaml",
);

const parsedState = (source: string): DocumentState => {
  const base = initialDocumentState("yaml", source);
  return {
    ...base,
    parse: parseSource(source, "yaml"),
    parsedVersion: base.sourceVersion,
  };
};

const stubTransactions = (
  overrides: Partial<SourceTransactions> = {},
): SourceTransactions => ({
  apply: vi.fn(),
  run: vi.fn(),
  feedback: { status: "idle" },
  // 패널은 자기 owner 슬롯만 읽는다(P5-01): 스텁은 `feedback`이 form 것이면 그것을 돌려준다.
  feedbackFor: (owner) => {
    const feedback = overrides.feedback ?? { status: "idle" };
    return feedback.status !== "idle" && feedback.owner === owner
      ? feedback
      : { status: "idle" };
  },
  onEditorReady: vi.fn(),
  enabled: true,
  disabled: null,
  settling: false,
  ...overrides,
});

const NO_CATALOGS = { equityFields: null, factors: null };

const renderPanel = (
  source: string,
  transactions: SourceTransactions,
  diagnostics: DocumentDiagnostic[] = [],
) => {
  const state = parsedState(source);
  const projection = projectForm(SCHEMA, state.parse, diagnostics);
  render(
    <StrategyFormPanel
      projection={projection}
      transactions={transactions}
      catalogs={NO_CATALOGS}
    />,
  );
  return projection;
};

/**
 * 섹션 fieldset(legend = `▾ <섹션 이름> <섹션 키> [· 힌트]`). P1-03부터 라벨이 사람 말 이름을
 * 먼저 보이므로 앞이 낱말 문자가 아닌 자리에서 키를 찾는다.
 */
const section = (name: string) =>
  within(screen.getByRole("group", { name: new RegExp(`(^|[^\\w])${name}`) }));

/**
 * 컨트롤의 접근성 이름은 `<이름> <key>[ · <unit>]`이다. `_`는 낱말 문자라 `selection_count`가
 * `short_selection_count`에 걸리지 않는다.
 */
const named = (key: string) => ({
  name: new RegExp(`(^|[^\\w])${key}(\\s|$)`),
});

describe("StrategyFormPanel controls", () => {
  it("commits number and text inputs on blur or Enter only when changed, and cancels on Escape", async () => {
    const user = userEvent.setup();
    const transactions = stubTransactions();
    renderPanel(MINIMAL, transactions);
    const weight = section("risk").getByRole(
      "spinbutton",
      named("max_name_weight"),
    );
    expect(weight).toHaveValue(0.05);

    await user.click(weight);
    await user.tab(); // 변경 없음 → 트랜잭션 없음
    expect(transactions.apply).not.toHaveBeenCalled();

    await user.clear(weight);
    await user.type(weight, "0.1");
    await user.tab();
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "replace-scalar", pointer: "/risk/max_name_weight", value: 0.1 },
      "max_name_weight",
      "form",
      { focusEditor: false },
    );

    const fee = section("execution").getByRole("spinbutton", named("fee_bps"));
    await user.clear(fee);
    await user.type(fee, "20{Enter}");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "replace-scalar", pointer: "/execution/fee_bps", value: 20 },
      "fee_bps",
      "form",
      { focusEditor: false },
    );

    const title = section("전략 문서").getByRole("textbox", named("title"));
    await user.clear(title);
    await user.type(title, "버림{Escape}");
    expect(title).toHaveValue("퀄리티 모멘텀");
    expect(transactions.apply).toHaveBeenCalledTimes(2);
  });

  it("rejects invalid drafts locally without a transaction", async () => {
    const user = userEvent.setup();
    const transactions = stubTransactions();
    renderPanel(MINIMAL, transactions);
    const count = section("portfolio").getByRole(
      "spinbutton",
      named("selection_count"),
    );
    await user.clear(count);
    await user.type(count, "2.5");
    await user.tab();
    expect(screen.getByRole("alert")).toHaveTextContent("정수를 입력하세요");
    expect(transactions.apply).not.toHaveBeenCalled();
  });

  it("applies enum, boolean and reset changes immediately, inserting omitted sections in one operation", async () => {
    const user = userEvent.setup();
    const transactions = stubTransactions();
    renderPanel(MINIMAL, transactions);
    await user.selectOptions(
      section("portfolio").getByRole("combobox", named("side")),
      "long_short",
    );
    expect(transactions.apply).toHaveBeenLastCalledWith(
      {
        kind: "insert-key",
        parentPointer: "/portfolio",
        key: "side",
        value: "long_short",
      },
      "side",
      "form",
      { focusEditor: false },
    );
    await user.click(
      section("risk").getByRole("checkbox", named("sector_neutral")),
    );
    expect(transactions.apply).toHaveBeenLastCalledWith(
      {
        kind: "insert-key",
        parentPointer: "/risk",
        key: "sector_neutral",
        value: true,
      },
      "sector_neutral",
      "form",
      { focusEditor: false },
    );
    // signal 섹션은 문서에 없다 → 루트에 섹션째 삽입(트랜잭션 한 번).
    const threshold = section("signal").getByRole(
      "spinbutton",
      named("score_threshold"),
    );
    await user.type(threshold, "0.3{Enter}");
    expect(transactions.apply).toHaveBeenLastCalledWith(
      {
        kind: "insert-key",
        parentPointer: "",
        key: "signal",
        value: { score_threshold: 0.3 },
      },
      "score_threshold",
      "form",
      { focusEditor: false },
    );
    await user.click(
      section("risk").getByRole("button", {
        name: "max_name_weight · 기본값으로",
      }),
    );
    expect(transactions.apply).toHaveBeenLastCalledWith(
      { kind: "remove", pointer: "/risk/max_name_weight" },
      "max_name_weight",
      "form",
      { focusEditor: false },
    );
    // 미작성 nullable 필드는 이미 null → "설정 안 함" 버튼이 없다.
    expect(
      section("signal").queryByRole("button", {
        name: "regime_field_id · 설정 안 함",
      }),
    ).toBeNull();
  });

  it("locks every control with the reason when transactions are disabled", () => {
    const transactions = stubTransactions({
      enabled: false,
      disabled: "composing",
    });
    const state = { ...parsedState(MINIMAL), composing: true };
    render(
      <StrategyFormPanel
        projection={projectForm(SCHEMA, state.parse, [])}
        transactions={transactions}
        catalogs={NO_CATALOGS}
      />,
    );
    expect(screen.getByText("IME 입력 중")).toBeInTheDocument();
    expect(
      section("risk").getByRole("spinbutton", named("max_name_weight")),
    ).toBeDisabled();
  });

  it("shows diagnostics badges, inapplicable hints, default placeholders and feedback", () => {
    const transactions = stubTransactions({
      feedback: {
        status: "error",
        owner: "form",
        label: "fee_bps",
        reason: "parse",
      },
    });
    const projection = renderPanel(MINIMAL, transactions, [
      {
        code: "strategy.risk.weight",
        kind: "semantic",
        severity: "error",
        pointer: "/risk/max_name_weight",
        message: "너무 큽니다",
        range: null,
      },
    ]);
    expect(section("risk").getByText("오류 1")).toHaveAttribute(
      "title",
      "너무 큽니다",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "YAML 1.2로 읽히지 않아",
    );
    // selection_method 기본값(top_n) 때문에 percentile은 읽히지 않는 필드다.
    const portfolio = projection.sections.find((s) => s.key === "portfolio");
    if (portfolio === undefined || portfolio.kind !== "object")
      throw new Error("portfolio");
    const percentile = portfolio.fields.find(
      (f) => f.key === "selection_percentile",
    )!;
    expect(percentile).toMatchObject({ written: false, applicable: false });
    expect(
      section("portfolio").getByRole(
        "spinbutton",
        named("selection_percentile"),
      ),
    ).toHaveAttribute("placeholder", draftOf(percentile.defaultValue));
    expect(
      section("portfolio").getAllByText(
        "현재 모드에서는 읽히지 않는 필드입니다",
      ).length,
    ).toBeGreaterThan(0);
    expect(section("data").getByText("기본값 KRX")).toBeInTheDocument();
  });
});

describe("StrategyFormPanel review follow-up (P4-02 1차)", () => {
  it("renders const rows without editing controls or reset buttons, and nested lists as list sections (DEFECT-P402-001/004, P4-05)", () => {
    const source = `${MINIMAL}eligibility:\n  rules:\n    - field_id: liquidity.adv\n      operator: gte\n      value: 1\n`;
    renderPanel(source, stubTransactions());
    const eligibility = section("eligibility");
    // `rules`는 더 이상 "기본값으로" 버튼이 있는 link 행이 아니라 항목 추가·삭제가 있는 목록 섹션이다.
    expect(eligibility.queryByRole("button", { name: "rules · 기본값으로" })).toBeNull();
    expect(
      eligibility.getByRole("button", { name: "rules · 항목 추가" }),
    ).toBeInTheDocument();
    expect(
      eligibility.getByRole("button", { name: "liquidity.adv · 삭제" }),
    ).toBeInTheDocument();
    const root = section("전략 문서");
    expect(root.getByText("1.1")).toHaveAttribute("aria-labelledby");
    expect(root.queryByRole("textbox", named("schema_version"))).toBeNull();
    expect(root.queryByRole("button", { name: /schema_version ·/ })).toBeNull();
  });

  it("lets a failed confirmation be retried with the same value and clears stale invalid hints (DEFECT-P402-002/003)", async () => {
    const user = userEvent.setup();
    const state = parsedState(MINIMAL);
    const projection = projectForm(SCHEMA, state.parse, []);
    const Harness = ({ failed }: { failed: boolean }) => (
      <StrategyFormPanel
        projection={projection}
        transactions={stubTransactions({
          apply,
          feedback: failed
            ? {
                status: "error",
                owner: "form",
                label: "max_name_weight",
                reason: "not-found",
              }
            : { status: "idle" },
        })}
        catalogs={NO_CATALOGS}
      />
    );
    // 첫 확정은 실패(apply가 false), 그 뒤는 성공.
    const apply = vi.fn().mockReturnValueOnce(false);
    const { rerender } = render(<Harness failed={false} />);
    const weight = section("risk").getByRole(
      "spinbutton",
      named("max_name_weight"),
    );
    await user.clear(weight);
    await user.type(weight, "0.1{Enter}");
    expect(apply).toHaveBeenCalledTimes(1);
    rerender(<Harness failed />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "문서에서 위치를 찾지 못했습니다",
    );
    // 실패한 값은 blur로 다시 계획하지 않는다(P4-02 리뷰 011).
    await user.tab();
    expect(apply).toHaveBeenCalledTimes(1);
    // 다른 필드의 성공이 feedback을 덮어도 Enter 재확정은 필드 로컬 판정으로 살아 있다(009/012).
    rerender(<Harness failed={false} />);
    await user.click(weight);
    await user.keyboard("{Enter}");
    expect(apply).toHaveBeenCalledTimes(2);
    // 성공한 뒤 같은 값 Enter는 다시 확정하지 않는다.
    await user.keyboard("{Enter}");
    expect(apply).toHaveBeenCalledTimes(2);

    // 무효 입력 안내는 값을 되돌리면 사라진다.
    const count = section("portfolio").getByRole(
      "spinbutton",
      named("selection_count"),
    );
    await user.clear(count);
    await user.type(count, "2.5");
    await user.tab();
    expect(
      screen
        .getAllByRole("alert")
        .some((el) => el.textContent?.includes("정수를 입력하세요")),
    ).toBe(true);
    await user.clear(count);
    await user.type(count, "20");
    await user.tab();
    expect(screen.queryByText("정수를 입력하세요")).toBeNull();
  });

  it("shows the x-default-from sibling value as the placeholder of an omitted field", () => {
    const source = MINIMAL.replace('    label: "모멘텀"\n', "");
    const state = parsedState(source);
    const projection = projectForm(SCHEMA, state.parse, []);
    const factors = projection.sections.find((s) => s.key === "factors");
    if (factors === undefined || factors.kind !== "list")
      throw new Error("factors");
    const label = factors.items[0]!.fields.find((f) => f.key === "label")!;
    expect(label).toMatchObject({ written: false, defaultFrom: "factor_id" });
    // 패널의 placeholder 규칙은 FormFieldRow 단위이므로 object 섹션과 같은 경로를 쓰는 항목 필드는 P4-03이 그린다.
  });

  it("translates every planner failure into a recovery message (DEFECT-P402-008)", () => {
    for (const reason of [
      "not-scalar",
      "not-mapping",
      "not-sequence",
    ] as const) {
      cleanup();
      renderPanel(
        MINIMAL,
        stubTransactions({
          feedback: {
            status: "error",
            owner: "form",
            label: "fee_bps",
            reason,
          },
        }),
      );
      expect(screen.getByRole("alert")).toHaveTextContent(
        "source를 확인하세요",
      );
    }
  });
});

describe("StrategyFormPanel re-edit right after a commit (audit DEFECT-P5X-001)", () => {
  it("does not restore the committed value into a draft the user already changed", async () => {
    const user = userEvent.setup();
    const apply = vi.fn(() => true);
    const view = (source: string) => (
      <StrategyFormPanel
        projection={projectForm(SCHEMA, parsedState(source).parse, [])}
        transactions={stubTransactions({ apply })}
        catalogs={NO_CATALOGS}
      />
    );
    const { rerender } = render(view(MINIMAL));
    const weight = section("risk").getByRole("spinbutton", named("max_name_weight"));
    await user.clear(weight);
    await user.type(weight, "0.1{Enter}");
    expect(apply).toHaveBeenCalledTimes(1);
    // 확정 직후(parse 도착 전) 사용자가 값을 지우고 새 값을 치기 시작한다.
    await user.clear(weight);
    await user.type(weight, "0.2");
    // parse가 도착해 projection이 0.1로 바뀐다 — 사용자가 고친 draft는 되돌리지 않는다.
    rerender(view(MINIMAL.replace("max_name_weight: 0.05", "max_name_weight: 0.1")));
    expect(weight).toHaveValue(0.2);
    await user.keyboard("{Enter}");
    expect(apply).toHaveBeenLastCalledWith(
      { kind: "replace-scalar", pointer: "/risk/max_name_weight", value: 0.2 },
      "max_name_weight",
      "form",
      { focusEditor: false },
    );
    // 확정한 입력은 다시 pristine이라 다음 projection 값으로 정렬된다.
    rerender(view(MINIMAL.replace("max_name_weight: 0.05", "max_name_weight: 0.3")));
    expect(weight).toHaveValue(0.3);
    // 한 번도 손대지 않은 다른 입력도 외부 변경(스니펫·소스 편집기·undo)을 따라간다(`draft === seen` 경로).
    const fee = section("execution").getByRole("spinbutton", named("fee_bps"));
    expect(fee).toHaveValue(15);
    rerender(view(MINIMAL.replace("fee_bps: 15.0", "fee_bps: 20.0")));
    expect(fee).toHaveValue(20);
    // 예전에 확정했던 문자열을 경유해 치는 중에도(0.2까지 쳤을 때 외부 변경 도착) 되돌리지 않는다.
    await user.clear(weight);
    await user.type(weight, "0.2");
    rerender(view(MINIMAL.replace("max_name_weight: 0.05", "max_name_weight: 0.4")));
    expect(weight).toHaveValue(0.2);
  });
});

describe("StrategyFormPanel 라벨 어휘 (P1-03)", () => {
  it("이름과 스키마 키를 띄어 읽는다", () => {
    renderPanel(MINIMAL, stubTransactions());

    // accname 계산은 인라인 요소의 결과를 각각 trim한다. 구분 공백이 `<span>` 안에 있으면
    // "이름key"로 붙어 읽히므로 형제 text node여야 한다(P1-03 리뷰).
    expect(
      section("risk").getByRole("spinbutton", {
        name: /^종목별 최대 목표 비중 한도 max_name_weight/,
      }),
    ).toBeInTheDocument();
    expect(
      section("전략 문서").getByRole("textbox", { name: /^전략 이름 title$/ }),
    ).toBeInTheDocument();
  });

  it("단위 접미사도 키와 띄어 읽는다", () => {
    renderPanel(MINIMAL, stubTransactions());

    expect(
      section("execution").getByRole("spinbutton", {
        name: "체결 금액에 적용할 수수료 가정 fee_bps · bp",
      }),
    ).toBeInTheDocument();
  });

  it("이름이 없는 필드는 키만 보인다", () => {
    renderPanel(MINIMAL, stubTransactions());

    // 섹션 제목은 스키마 키가 없으면 이름만 남는다(루트 스칼라 섹션 = 문서 자신).
    expect(
      screen.getByRole("button", { name: "전략 문서", expanded: true }),
    ).toBeInTheDocument();
  });
});

describe("StrategyFormPanel section collapse (P4-04)", () => {
  it("toggles aria-expanded and hides the section body while keeping it in the DOM", async () => {
    const user = userEvent.setup();
    renderPanel(MINIMAL, stubTransactions());
    const toggle = section("risk").getByRole("button", {
      name: /risk$/,
      expanded: true,
    });
    const weight = () =>
      section("risk").queryByRole("spinbutton", named("max_name_weight"));
    expect(weight()).not.toBeNull();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // 접힌 본문은 `hidden`이라 접근성 트리에서 빠지지만 DOM 노드는 남는다(입력 상태 유지).
    expect(weight()).toBeNull();
    expect(
      section("risk").getByRole("spinbutton", {
        ...named("max_name_weight"),
        hidden: true,
      }),
    ).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(weight()).not.toBeNull();
  });
});

describe("StrategyFormPanel reset on the last written field (audit R4)", () => {
  it("collapses the section to `{}` and drops the standalone comment inside it, keeping the key line comment", async () => {
    const user = userEvent.setup();
    const source = `${MINIMAL}`.replace(
      "risk:\n  max_name_weight: 0.05\n",
      "risk: # 리스크 메모\n  # 안쪽 설명\n  max_name_weight: 0.05\n",
    );
    let text = source;
    const editor: CodeEditorHandle = {
      getText: () => text,
      replaceRange: vi.fn((from: number, to: number, insert: string) => {
        text = `${text.slice(0, from)}${insert}${text.slice(to)}`;
      }),
      getSelection: () => ({ from: 0, to: 0 }),
      setSelection: vi.fn(),
      offsetToPosition: vi.fn(() => ({ line: 0, column: 0 })),
      positionToOffset: vi.fn(() => 0),
      scrollTo: vi.fn(),
      focus: vi.fn(),
      loadText: vi.fn(),
      undo: vi.fn(() => false),
      redo: vi.fn(() => false),
      historyDepth: vi.fn(() => ({ undo: 0, redo: 0 })),
      getHistoryState: vi.fn(() => null),
      restoreHistoryState: vi.fn(),
    };
    const Harness = () => {
      const [state] = useState(() => parsedState(source));
      const transactions = useSourceTransactions(state);
      return (
        <>
          <button
            type="button"
            onClick={() => transactions.onEditorReady(editor)}
          >
            attach
          </button>
          <StrategyFormPanel
            projection={projectForm(SCHEMA, state.parse, [])}
            transactions={transactions}
            catalogs={NO_CATALOGS}
          />
        </>
      );
    };
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "attach" }));
    await user.click(
      section("risk").getByRole("button", {
        name: "max_name_weight · 기본값으로",
      }),
    );
    // 의도된 동작(WORKFLOW P3-01 알려진 제한): 부모가 접히면 안의 독립 주석은 사라지고 undo 한 번으로 돌아온다.
    expect(text).toContain("risk: # 리스크 메모\n  {}\n");
    expect(text).not.toContain("# 안쪽 설명");
  });
});

describe("StrategyFormPanel with the real transaction hook", () => {
  it("edits the editor text through one replaceRange whose result parses to the changed value", async () => {
    const user = userEvent.setup();
    let text = MINIMAL;
    const editor: CodeEditorHandle = {
      getText: () => text,
      replaceRange: vi.fn((from: number, to: number, insert: string) => {
        text = `${text.slice(0, from)}${insert}${text.slice(to)}`;
      }),
      getSelection: () => ({ from: 0, to: 0 }),
      setSelection: vi.fn(),
      offsetToPosition: vi.fn(() => ({ line: 0, column: 0 })),
      positionToOffset: vi.fn(() => 0),
      scrollTo: vi.fn(),
      focus: vi.fn(),
      loadText: vi.fn(),
      undo: vi.fn(() => false),
      redo: vi.fn(() => false),
      historyDepth: vi.fn(() => ({ undo: 0, redo: 0 })),
      getHistoryState: vi.fn(() => null),
      restoreHistoryState: vi.fn(),
    };
    const Harness = () => {
      const [state, setState] = useState(() => parsedState(MINIMAL));
      const transactions = useSourceTransactions(state);
      return (
        <>
          <button
            type="button"
            onClick={() => transactions.onEditorReady(editor)}
          >
            attach
          </button>
          <button type="button" onClick={() => setState(parsedState(text))}>
            resync
          </button>
          <StrategyFormPanel
            projection={projectForm(SCHEMA, state.parse, [])}
            transactions={transactions}
            catalogs={NO_CATALOGS}
          />
        </>
      );
    };
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "attach" }));
    const weight = section("risk").getByRole(
      "spinbutton",
      named("max_name_weight"),
    );
    await user.clear(weight);
    await user.type(weight, "0.1{Enter}");
    expect(editor.replaceRange).toHaveBeenCalledTimes(1);
    expect(text).toContain("risk:\n  max_name_weight: 0.1\n");
    expect(text.replace("0.1", "0.05")).toBe(MINIMAL);
    expect(screen.getByRole("status")).toHaveTextContent(
      "max_name_weight 반영됨",
    );

    await act(async () => {
      await user.click(screen.getByRole("button", { name: "resync" }));
    });
    expect(
      section("risk").getByRole("spinbutton", named("max_name_weight")),
    ).toHaveValue(0.1);
  });
});
