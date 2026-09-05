import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const css = readFileSync(
  resolve(process.cwd(), "src/app/styles/tokens.css"),
  "utf8",
);

const block = (selector: RegExp): string => {
  const match = css.match(selector);
  if (!match?.[1]) throw new Error(`theme selector not found: ${selector}`);
  return match[1];
};

const tokens = (source: string): Map<string, string> =>
  new Map(
    [...source.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map((match) => [
      match[1]!,
      match[2]!.trim(),
    ]),
  );

const channel = (hex: string): number => {
  const value = Number.parseInt(hex, 16) / 255;
  return value <= 0.04045
    ? value / 12.92
    : Math.pow((value + 0.055) / 1.055, 2.4);
};

const luminance = (hex: string): number => {
  const match = hex.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (!match)
    throw new Error(`expected an opaque six-digit colour, received ${hex}`);
  return (
    channel(match[1]!) * 0.2126 +
    channel(match[2]!) * 0.7152 +
    channel(match[3]!) * 0.0722
  );
};

const contrast = (left: string, right: string): number => {
  const a = luminance(left);
  const b = luminance(right);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
};

const light = tokens(block(/:root\s*\{([^}]*)\}/s));
const dark = tokens(block(/:root\[data-theme="dark"\]\s*\{([^}]*)\}/s));

const textPairs = [
  ["--text", "--surface-panel"],
  ["--text-muted", "--surface-panel"],
  ["--text-subtle", "--surface-panel"],
  ["--accent", "--surface-panel"],
  ["--text-on-accent", "--accent"],
  ["--status-ok", "--status-ok-soft"],
  ["--status-warn", "--status-warn-soft"],
  ["--status-error", "--status-error-soft"],
  ["--status-info", "--status-info-soft"],
  ["--syntax-key", "--surface-panel"],
  ["--syntax-string", "--surface-panel"],
  ["--syntax-number", "--surface-panel"],
  ["--syntax-literal", "--surface-panel"],
  ["--syntax-comment", "--surface-panel"],
  ["--syntax-punctuation", "--surface-panel"],
] as const;

describe("semantic theme token contract", () => {
  it.each([
    ["light", light],
    ["dark", dark],
  ] as const)("keeps %s text pairs at WCAG AA contrast", (_name, theme) => {
    for (const [foreground, background] of textPairs) {
      const ink = theme.get(foreground);
      const surface = theme.get(background);
      expect(ink, foreground).toBeDefined();
      expect(surface, background).toBeDefined();
      expect(
        contrast(ink!, surface!),
        `${foreground} on ${background}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each([
    ["light", light],
    ["dark", dark],
  ] as const)(
    "keeps %s chart strokes distinguishable from the canvas",
    (_name, theme) => {
      const canvas = theme.get("--surface-canvas");
      expect(canvas).toBeDefined();
      for (let index = 1; index <= 6; index += 1) {
        const series = theme.get(`--chart-series-${index}`);
        expect(series).toBeDefined();
        expect(
          contrast(series!, canvas!),
          `--chart-series-${index} on --surface-canvas`,
        ).toBeGreaterThanOrEqual(3);
      }
    },
  );
});
