import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

/**
 * Test-only access to fixtures that live once, in the backend tree (parser ADR D3). Walks up
 * from the working directory so tests pass from `frontend/` and from the repository root.
 */
export const backendFixturePath = (relative: string): string => {
  const wanted = `backend/tests/fixtures/${relative}`;
  let dir = process.cwd();
  for (;;) {
    const candidate = resolve(dir, wanted);
    if (existsSync(candidate)) return candidate;
    const parent = dirname(dir);
    if (parent === dir)
      throw new Error(
        `fixture not found — from=${process.cwd()} want=${wanted}`,
      );
    dir = parent;
  }
};

export const readBackendFixture = (relative: string): string =>
  readFileSync(backendFixturePath(relative), "utf8");
