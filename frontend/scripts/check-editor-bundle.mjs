import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gzipSync } from "node:zlib";

const budgetBytes = 200 * 1024;
const assets = resolve(process.cwd(), "dist", "assets");
const candidates = readdirSync(assets).filter(
  (name) => name.startsWith("code-editor-view-") && name.endsWith(".js"),
);

if (candidates.length !== 1) {
  throw new Error(
    `expected one lazy code-editor-view chunk, found ${candidates.length}: ${candidates.join(", ")}`,
  );
}

const filename = candidates[0];
const gzipBytes = gzipSync(readFileSync(resolve(assets, filename))).byteLength;
const gzipKiB = (gzipBytes / 1024).toFixed(2);
console.log(
  `editor chunk ${filename}: ${gzipKiB} KiB gzip (budget 200.00 KiB)`,
);
if (gzipBytes > budgetBytes) {
  throw new Error(`editor chunk exceeds budget: ${gzipKiB} KiB > 200.00 KiB`);
}
