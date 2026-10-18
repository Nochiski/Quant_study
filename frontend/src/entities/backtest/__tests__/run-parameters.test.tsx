import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import type { BacktestRunSpec } from "../../../shared/api";
import { useRunParameters } from "../model/use-run-parameters";
import { RunParameterSelection } from "../ui/run-parameter-selection";

const URL = "http://localhost:8000/api/v1/backtests/:runId/request";
const request = (): BacktestRunSpec => ({ strategy_source: { kind: "saved_revision", strategy_id: "strategy-a", revision: 2, expected_spec_hash: "hash-a" }, parameter_values: { scale: 0, mode: "fast", enabled: false } });
const server = setupServer(http.get(URL, () => HttpResponse.json(request())));
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { cleanup(); server.resetHandlers(); });
afterAll(() => server.close());

const Harness = ({ runId }: { runId?: string }) => {
  const selection = useRunParameters(runId, { strategyId: "strategy-a", revision: 2, specHash: "hash-a" });
  return <><RunParameterSelection selection={selection} clear={() => undefined} /><button disabled={!selection.ready}>백테스트</button></>;
};
const mount = (runId?: string) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const element = (id?: string) => <QueryClientProvider client={client}><Harness runId={id} /></QueryClientProvider>;
  return { ...render(element(runId)), element };
};

describe("선택 실행의 파라미터 출처", () => {
  it("선택이 없으면 요청하지 않고 기존 기본값 경로를 유지한다", () => {
    let reads = 0;
    server.use(http.get(URL, () => { reads++; return HttpResponse.json(request()); }));
    mount();
    expect(screen.getByRole("button", { name: "백테스트" })).toBeEnabled();
    expect(screen.queryByRole("region")).toBeNull();
    expect(reads).toBe(0);
  });
  it("서버의 0·false·문자열을 보존하고 펼친 출처에서 읽는다", async () => {
    mount("run-a");
    await waitFor(() => expect(screen.getByRole("button", { name: "백테스트" })).toBeEnabled());
    expect(screen.getByText("0", { selector: "dd" })).not.toBeVisible();
    expect(screen.getByRole("button", { name: "문서 기본값으로 돌아가기" })).toBeVisible();
    await userEvent.setup().click(screen.getByLabelText("선택값 출처: 적용 범위와 선택 출처", { selector: "summary", exact: true }));
    expect(screen.getByText("0", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("false", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("fast", { selector: "dd" })).toBeVisible();
    expect(screen.getByText(/현재 문서와 현재 실행 환경에 적용합니다/u)).toBeVisible();
  });
  it.each(["strategy", "revision", "hash", "inline"])("%s 불일치는 기본값으로 대체하지 않는다", async (kind) => {
    const body = request();
    if (body.strategy_source?.kind !== "saved_revision") throw new Error("리비전 fixture 오류");
    if (kind === "strategy") body.strategy_source.strategy_id = "other";
    if (kind === "revision") body.strategy_source.revision = 3;
    if (kind === "hash") body.strategy_source.expected_spec_hash = "other";
    if (kind === "inline") body.strategy_source = undefined;
    server.use(http.get(URL, () => HttpResponse.json(body)));
    mount("run-a");
    expect(await screen.findByRole("alert")).toHaveTextContent("리비전이 일치하지 않습니다");
    expect(screen.getByRole("button", { name: "백테스트" })).toBeDisabled();
  });
  it("요청 실패는 차단하고 명시적 다시 조회로 복구한다", async () => {
    let reads = 0;
    server.use(http.get(URL, () => ++reads === 1 ? HttpResponse.json({}, { status: 503 }) : HttpResponse.json(request())));
    mount("run-a");
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "백테스트" })).toBeDisabled();
    await userEvent.setup().click(screen.getByRole("button", { name: "선택한 실행 다시 조회" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "백테스트" })).toBeEnabled());
    expect(reads).toBe(2);
  });
  it("느린 다른 칸의 요청이 도착해도 현재 선택값을 덮지 않는다", async () => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    let requested = false;
    let returned = false;
    server.use(http.get(URL, async ({ params }) => {
      if (params.runId === "slow") { requested = true; await pending; returned = true; }
      return HttpResponse.json({ ...request(), parameter_values: { scale: params.runId === "slow" ? 1 : 2 } });
    }));
    const view = mount("slow");
    await waitFor(() => expect(requested).toBe(true));
    expect(screen.getByRole("button", { name: "백테스트" })).toBeDisabled();
    view.rerender(view.element("current"));
    await waitFor(() => expect(screen.getByRole("button", { name: "백테스트" })).toBeEnabled());
    await userEvent.setup().click(screen.getByLabelText("선택값 출처: 적용 범위와 선택 출처", { selector: "summary", exact: true }));
    expect(screen.getByText("2", { selector: "dd" })).toBeVisible();
    release();
    await waitFor(() => expect(returned).toBe(true));
    expect(screen.queryByText("1", { selector: "dd" })).toBeNull();
    expect(screen.getByText("2", { selector: "dd" })).toBeVisible();
  });
});
