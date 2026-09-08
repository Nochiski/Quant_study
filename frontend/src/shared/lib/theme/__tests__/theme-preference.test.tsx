import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  THEME_PREFERENCE_KEY,
  ThemePreferenceProvider,
  readThemePreference,
  useThemePreference,
} from "..";

afterEach(() => {
  cleanup();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
});

const Consumer = () => {
  const theme = useThemePreference();
  return (
    <>
      <output>{`${theme.preference}:${theme.resolved}`}</output>
      <button type="button" onClick={() => theme.setPreference("dark")}>
        dark
      </button>
    </>
  );
};

describe("theme preference", () => {
  it("fails malformed, wrong-version and non-string storage to system", () => {
    for (const raw of [
      "not-json",
      '{"version":2,"preference":"dark"}',
      '{"version":1,"preference":null}',
      '{"version":1,"preference":{"toString":"dark"}}',
    ]) {
      const storage = { getItem: () => raw, setItem: vi.fn() };
      expect(readThemePreference(storage)).toBe("system");
    }
    expect(
      readThemePreference({
        getItem: () => {
          throw new DOMException("denied");
        },
        setItem: vi.fn(),
      }),
    ).toBe("system");
  });

  it("resolves system changes and persists an explicit choice", async () => {
    let listener: ((event: MediaQueryListEvent) => void) | undefined;
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: false,
        media: "(prefers-color-scheme: dark)",
        addEventListener: (
          _: string,
          next: (event: MediaQueryListEvent) => void,
        ) => {
          listener = next;
        },
        removeEventListener: vi.fn(),
      })),
    );
    const user = userEvent.setup();
    render(
      <ThemePreferenceProvider>
        <Consumer />
      </ThemePreferenceProvider>,
    );

    expect(screen.getByText("system:light")).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    act(() => listener?.({ matches: true } as MediaQueryListEvent));
    expect(await screen.findByText("system:dark")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "dark" }));
    expect(localStorage.getItem(THEME_PREFERENCE_KEY)).toBe(
      '{"version":1,"preference":"dark"}',
    );
    expect(screen.getByText("dark:dark")).toBeInTheDocument();
  });
});
