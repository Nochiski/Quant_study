import { cleanup, render, screen } from "@testing-library/react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { useCommittedRef } from "..";

/**
 * commit과 ref 갱신 사이의 틈(Phase 5 backlog 20·21). 상태를 act 밖의 promise로 바꾸고, `MutationObserver`가
 * DOM 변화를 본 microtask — passive effect가 돌기 전 — 에 두 ref를 읽는다. `useEffect`로 비춘 ref(대조군)는
 * 옛 값이고, `useCommittedRef`는 화면과 같은 값이다.
 */

afterEach(cleanup);

const Probe = ({
  open,
  refs,
}: {
  open: Promise<void>;
  refs: { committed: RefObject<number>; lagging: RefObject<number> }[];
}) => {
  const [value, setValue] = useState(0);
  useEffect(() => {
    void open.then(() => setValue(1));
  }, [open]);
  const committed = useCommittedRef(value);
  const lagging = useRef(value);
  useEffect(() => {
    lagging.current = value;
  }, [value]);
  useEffect(() => {
    refs.push({ committed, lagging });
  }, [committed, lagging, refs]);
  return <output data-value={value}>{value}</output>;
};

describe("useCommittedRef", () => {
  it("reads the committed value in the same tick the DOM shows it, where a useEffect mirror still lags", async () => {
    let open!: () => void;
    const gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    const refs: { committed: RefObject<number>; lagging: RefObject<number> }[] = [];
    render(<Probe open={gate} refs={refs} />);
    const output = await screen.findByText("0");
    const observed = new Promise<{ committed: number; lagging: number }>((resolve) => {
      const observer = new MutationObserver(() => {
        if (output.getAttribute("data-value") !== "1") return;
        observer.disconnect();
        const latest = refs[refs.length - 1]!;
        resolve({ committed: latest.committed.current, lagging: latest.lagging.current });
      });
      observer.observe(output, { attributes: true, attributeFilter: ["data-value"] });
    });
    open();
    const seen = await observed;
    expect(seen.committed).toBe(1);
    // 대조군: passive effect는 아직 돌지 않았다 — 이 틈이 backlog 20·21의 원인이다.
    expect(seen.lagging).toBe(0);
  });
});
