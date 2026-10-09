import { cleanup, render, screen, waitFor } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createBrowserHistory,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { DirtyLeaveGuard } from "../ui/dirty-leave-guard";

/**
 * 이탈 가드 경합(Phase 5 backlog 21). dirty가 act 밖의 갱신(편집 → reducer)으로 켜진 뒤 그 commit을 본 microtask에
 * 바로 다른 경로로 이동한다 — 가드가 passive effect로 dirty를 비추거나 `disabled`로 등록을 껐다 켜면 이 이동은
 * 경고 없이 통과해 편집이 버려진다. 가드는 이동을 막고 대화상자를 띄워야 한다.
 */

afterEach(cleanup);

const Editor = ({ open }: { open: Promise<void> }) => {
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    void open.then(() => setDirty(true));
  }, [open]);
  return (
    <>
      <output data-dirty={dirty}>{dirty ? "dirty" : "clean"}</output>
      <DirtyLeaveGuard dirty={dirty} />
    </>
  );
};

const mount = (open: Promise<void>, history = createMemoryHistory({ initialEntries: ["/"] })) => {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const editorRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <Editor open={open} />,
  });
  const awayRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/away",
    component: () => <p>away</p>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([editorRoute, awayRoute]),
    history,
  });
  render(<RouterProvider router={router} />);
  return router;
};

describe("DirtyLeaveGuard (backlog 21)", () => {
  it("blocks a navigation issued in the very tick the draft became dirty", async () => {
    let open!: () => void;
    const gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    const router = mount(gate);
    const marker = await screen.findByText("clean");
    const navigated = new Promise<void>((resolve) => {
      const observer = new MutationObserver(() => {
        if (marker.getAttribute("data-dirty") !== "true") return;
        observer.disconnect();
        router.history.push("/away");
        resolve();
      });
      observer.observe(marker, { attributes: true, attributeFilter: ["data-dirty"] });
    });
    open();
    await navigated;
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("저장하지 않은 변경이 있습니다");
    expect(router.state.location.pathname).toBe("/");
    expect(screen.queryByText("away")).toBeNull();
  });

  it("arms the browser unload warning only while dirty (blocker stays registered, judgement is in the callback)", async () => {
    // 브라우저 history만 `beforeunload`를 건다(#157 리뷰 P2-4). blocker를 항상 등록하므로 clean일 때 경고가 뜨면 퇴행이다.
    window.history.replaceState(null, "", "/");
    let open!: () => void;
    const gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    mount(gate, createBrowserHistory());
    await screen.findByText("clean");
    const unload = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(unload()).toBe(false);
    open();
    await screen.findByText("dirty");
    expect(unload()).toBe(true);
  });

  it("lets a clean draft leave without a dialog", async () => {
    const router = mount(new Promise<void>(() => {}));
    await screen.findByText("clean");
    router.history.push("/away");
    await waitFor(() => expect(router.state.location.pathname).toBe("/away"));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});
