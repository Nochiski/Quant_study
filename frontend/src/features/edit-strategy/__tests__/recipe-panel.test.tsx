import { useState } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  DatasetFieldProfile,
  OperatorDefinition,
} from "../../../shared/api";
import { parseSource } from "../../../shared/lib/yaml12";
import { readBackendFixture } from "../../../shared/testing/backend-fixtures";
import type { JsonSchema } from "../model/schema-navigator";
import { planSourceOperations } from "../model/source-transactions";
import type { SourceTransactions } from "../model/use-source-transactions";
import { RecipePanel } from "../ui/recipe-panel";

const schema = JSON.parse(
  readBackendFixture("strategy_documents/runtime-schema.json"),
) as JsonSchema;
const operators = (
  JSON.parse(
    readBackendFixture("strategy_documents/operator-catalog.json"),
  ) as { operators: OperatorDefinition[] }
).operators;
const blank =
  'schema_version: "1.2"\ntitle: 레시피\nfactors:\n  - factor_id: alpha\n    label: 팩터\n    graph:\n      nodes: []\n      output_node_id: ""\n';
const catalogs = {
  equityFields: [
    { field_id: "price.adj_close", label: "수정 종가" },
    { field_id: "price.volume", label: "거래량" },
  ] as DatasetFieldProfile[],
};
afterEach(cleanup);
const setup = (
  initial = blank,
  extra: {
    blocked?: boolean;
    diagnostics?: React.ComponentProps<typeof RecipePanel>["diagnostics"];
    definitions?: OperatorDefinition[];
  } = {},
) => {
  let latest = initial;
  const onBack = vi.fn();
  const onAdvanced = vi.fn();
  function Harness() {
    const [source, setSource] = useState(initial);
    const [path, setPath] = useState<string | undefined>("/factors/0/graph");
    const parsed = parseSource(source, "yaml");
    if (parsed.status !== "ok") throw new Error("원문 파싱 실패");
    const transactions: SourceTransactions = {
      apply: (operation) => {
        const plan = planSourceOperations(
          source,
          "yaml",
          Array.isArray(operation) ? operation : [operation],
        );
        if (plan.status !== "ok") throw new Error(plan.reason);
        latest = plan.edit.nextSource;
        setSource(latest);
        return true;
      },
      run: vi.fn(),
      feedback: { status: "idle" },
      feedbackFor: () => ({ status: "idle" }),
      onEditorReady: vi.fn(),
      enabled: !extra.blocked,
      disabled: extra.blocked ? "syntax" : null,
      settling: false,
    };
    return (
      <RecipePanel
        tree={parsed.tree}
        schema={schema}
        transactions={transactions}
        catalogs={catalogs}
        operators={{
          status: "ready",
          definitions: extra.definitions ?? operators,
        }}
        factorIndex={0}
        plans={{ status: "blocked", reason: "pending" }}
        diagnostics={extra.diagnostics ?? []}
        selectedPointer={path}
        onSelectPointer={setPath}
        onBack={onBack}
        onAdvanced={onAdvanced}
      />
    );
  }
  render(<Harness />);
  return { source: () => latest, onBack, onAdvanced, user: userEvent.setup() };
};
const addHead = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(
    screen.getByRole("button", { name: "데이터 필드 노드 추가" }),
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "데이터 필드 1" }),
    "price.adj_close",
  );
  await user.click(screen.getByRole("button", { name: "단계 반영" }));
};

describe("레시피 단계 패널", () => {
  it("빈 그래프에서 필드를 물어 추가하고 기존 컨트롤로 설정·이동·삭제한다", async () => {
    const { user, source } = setup();
    await addHead(user);
    await user.click(
      screen.getByRole("button", { name: "기간 평균 노드 추가" }),
    );
    const mean = screen.getByRole("article", { name: "2. 기간 평균" });
    const window = within(mean).getByRole("spinbutton", { name: /집계 기간/ });
    await user.clear(window);
    await user.type(window, "20");
    await user.tab();
    expect(source()).toContain("window: 20");
    await user.click(
      screen.getByRole("button", { name: "부호 뒤집기 노드 추가" }),
    );
    const negate = screen.getByRole("article", { name: "3. 부호 뒤집기" });
    await user.click(within(negate).getByRole("button", { name: "위로" }));
    expect(source().indexOf("node_id: negate")).toBeLessThan(
      source().indexOf("node_id: mean"),
    );
    await user.click(
      within(screen.getByRole("article", { name: "2. 부호 뒤집기" })).getByRole(
        "button",
        { name: "단계 삭제" },
      ),
    );
    expect(source()).not.toContain("node_id: negate");
    expect(source()).toContain("input_node_id: adj_close");
    expect(
      screen.getByRole("region", { name: "팩터 레시피" }).textContent,
    ).not.toMatch(/node_id|field_id|kind:/);
  });
  it("다중 입력은 취소하면 원문을 안 바꾸고 선택한 슬롯과 새 잎을 한 번에 넣는다", async () => {
    const { user, source } = setup();
    await addHead(user);
    const before = source();
    await user.click(screen.getByRole("button", { name: "나누기 노드 추가" }));
    await user.click(screen.getByRole("button", { name: "취소" }));
    expect(source()).toBe(before);
    await user.click(screen.getByRole("button", { name: "나누기 노드 추가" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "데이터 필드 1" }),
      "price.volume",
    );
    await user.click(screen.getByRole("button", { name: "단계 반영" }));
    expect(source()).toContain("left_node_id: adj_close");
    expect(source()).toContain("right_node_id: volume");
  });
  it("선택 단계 다음에 삽입하고 연산 교체도 모델을 통해 처리한다", async () => {
    const { user, source } = setup();
    await addHead(user);
    await user.click(
      screen.getByRole("button", { name: "기간 평균 노드 추가" }),
    );
    await user.click(screen.getByRole("button", { name: "1. 데이터 필드" }));
    await user.click(
      screen.getByRole("button", { name: "부호 뒤집기 노드 추가" }),
    );
    expect(source()).toContain("input_node_id: negate");
    await user.click(
      within(screen.getByRole("article", { name: "2. 부호 뒤집기" })).getByRole(
        "button",
        { name: "연산 교체" },
      ),
    );
    await user.click(
      screen.getByRole("button", { name: "기간 평균 노드 추가" }),
    );
    expect(source()).not.toContain("node_id: negate");
    expect(source()).toContain("node_id: mean_2");
  });
  it("미지원 연산자를 숨기고 backend 진단은 해당 단계에 보인다", async () => {
    const fixture = readBackendFixture(
      "strategy_documents/ideas/momentum_12_1.yaml",
    );
    setup(fixture, {
      definitions: operators.map((entry) =>
        entry.operator === "negate"
          ? { ...entry, availability: "unsupported" }
          : entry,
      ),
      diagnostics: [
        {
          code: "factor.test",
          kind: "semantic",
          range: null,
          severity: "error",
          message: "검증용 단계 오류",
          pointer: "/factors/0/graph/nodes/1",
        },
      ],
    });
    expect(
      screen.queryByRole("button", { name: "부호 뒤집기 노드 추가" }),
    ).toBeNull();
    expect(
      within(screen.getByRole("article", { name: /2\./ })).getByText(
        "검증용 단계 오류",
      ),
    ).toBeVisible();
  });
  it("분기 그래프는 단계 UI 없이 고급 전환을 제공하고 잠긴 문서는 수정하지 않는다", async () => {
    const fixture = readBackendFixture(
      "strategy_documents/ideas/ma20_breakout.yaml",
    ).replace("left_node_id: adj_close_2", "left_node_id: adj_close");
    const { onAdvanced } = setup(fixture);
    expect(screen.queryByRole("article")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "고급으로" }));
    expect(onAdvanced).toHaveBeenCalledOnce();
    cleanup();
    const { source } = setup(blank, { blocked: true });
    expect(
      screen.getByRole("button", { name: "데이터 필드 노드 추가" }),
    ).toBeDisabled();
    expect(source()).toBe(blank);
  });
});
