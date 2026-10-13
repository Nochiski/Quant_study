import { describe, expect, it, vi } from "vitest";
import { createCompileObserver } from "./compile-observer";

const deferred = () => {
  let resolve!: (value: { spec_hash: string | null }) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<{ spec_hash: string | null }>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};

describe("컴파일 응답 관찰의 요청 소유권", () => {
  it("이미 대체된 응답의 본문은 읽지 않는다", async () => {
    const observer = createCompileObserver<object>();
    const old = {};
    observer.requested(old);
    observer.requested({});
    const read = vi.fn();
    await observer.received(old, read);
    expect(read).not.toHaveBeenCalled();
    expect(observer.specHash()).toBeNull();
  });

  it("본문 대기 중 대체된 요청의 취소는 새 hash와 UI 대기를 실패시키지 않는다", async () => {
    const observer = createCompileObserver<object>();
    const old = {},
      current = {};
    const pending = deferred();
    observer.requested(old);
    const received = observer.received(old, () => pending.promise);
    observer.requested(current);
    await observer.received(current, async () => ({ spec_hash: "current" }));
    pending.reject(
      new Error("Network.getResponseBody: No data found for resource"),
    );
    await expect(received).resolves.toBeUndefined();
    expect(observer.specHash()).toBe("current");
  });

  it("이전 응답의 늦은 성공이 최신 hash를 덮지 않는다", async () => {
    const observer = createCompileObserver<object>();
    const old = {},
      current = {};
    const pending = deferred();
    observer.requested(old);
    const received = observer.received(old, () => pending.promise);
    observer.requested(current);
    await observer.received(current, async () => ({ spec_hash: "current" }));
    pending.resolve({ spec_hash: "obsolete" });
    await received;
    expect(observer.specHash()).toBe("current");
  });

  it("현재 요청의 잘못된 JSON은 이벤트 밖의 hash 단언에서 그대로 실패한다", async () => {
    const observer = createCompileObserver<object>();
    const current = {};
    observer.requested(current);
    const invalid = new SyntaxError("invalid compile JSON");
    await expect(
      observer.received(current, async () => {
        throw invalid;
      }),
    ).resolves.toBeUndefined();
    expect(() => observer.specHash()).toThrow(invalid);
    const next = {};
    observer.requested(next);
    expect(observer.specHash()).toBeNull();
    await observer.received(next, async () => ({ spec_hash: "next" }));
    expect(observer.specHash()).toBe("next");
  });
});
