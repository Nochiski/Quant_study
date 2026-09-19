import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemePreferenceProvider } from "../../../shared/lib/theme";
import { StrategyIde } from "..";

/**
 * 단축키 게이트 경합(Phase 5 backlog 20). 버튼의 `disabled`는 commit에서 바로 바뀌지만 `window` keydown 리스너가
 * passive effect(`useEffect`)로 교체되면 그 사이 틈이 생겨 단축키가 옛 닫힌 값(비활성)으로 버려졌다. 이 테스트는
 * 게이트를 act 밖의 promise(프로덕션과 같은 스케줄: passive effect는 commit과 별개의 매크로태스크)로 열고,
 * `MutationObserver`가 `disabled` 제거를 본 microtask — passive effect가 돌기 전 — 에 즉시 단축키를 보낸다.
 * layout effect면 commit 안에서 이미 새 리스너다(#155 리뷰: red/green을 가르는 것은 이 타이밍 하나다).
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const GateOpensLater = ({
  onRunBacktest,
  onSave,
  open,
}: {
  onRunBacktest: () => void;
  onSave: () => void;
  open: Promise<void>;
}) => {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    // compile 응답이 도착하듯 act 밖의 promise에서 게이트를 연다.
    void open.then(() => setEnabled(true));
  }, [open]);
  return (
    <ThemePreferenceProvider>
      <StrategyIde
        title="새 전략"
        versionLabel="v1"
        editor={<textarea aria-label="source" />}
        onRunBacktest={onRunBacktest}
        runDisabled={!enabled}
        onSave={onSave}
        saveDisabled={!enabled}
        // 페이지가 넣는 버튼처럼 같은 게이트로 `disabled`를 그린다 — 테스트는 이 버튼이 켜지는 순간을 관찰한다.
        editorActions={
          <>
            <button type="button" disabled={!enabled}>
              리비전 저장
            </button>
            <button type="button" disabled={!enabled}>
              백테스트
            </button>
          </>
        }
      />
    </ThemePreferenceProvider>
  );
};

const button = async (name: string): Promise<HTMLButtonElement> =>
  (await screen.findByRole("button", { name })) as HTMLButtonElement;

/** `disabled`가 사라진 바로 그 microtask에 단축키를 보낸다(passive effect가 돌기 전). */
const pressWhenEnabled = (
  target: HTMLButtonElement,
  keys: Parameters<typeof fireEvent.keyDown>[1],
): Promise<void> =>
  new Promise((resolve) => {
    const observer = new MutationObserver(() => {
      if (target.disabled) return;
      observer.disconnect();
      fireEvent.keyDown(window, keys);
      resolve();
    });
    observer.observe(target, { attributes: true, attributeFilter: ["disabled"] });
  });

describe("StrategyIde shortcut gate (backlog 20)", () => {
  it("fires Ctrl+Shift+Enter and Ctrl+S in the very tick the buttons become enabled", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    const onRunBacktest = vi.fn();
    const onSave = vi.fn();
    let open!: () => void;
    const gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    render(<GateOpensLater onRunBacktest={onRunBacktest} onSave={onSave} open={gate} />);
    const run = await button("백테스트");
    const save = await button("리비전 저장");
    expect(run.disabled).toBe(true);
    const pressed = Promise.all([
      pressWhenEnabled(run, { key: "Enter", ctrlKey: true, shiftKey: true }),
      pressWhenEnabled(save, { key: "s", ctrlKey: true }),
    ]);
    open();
    await pressed;
    expect(onRunBacktest).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
