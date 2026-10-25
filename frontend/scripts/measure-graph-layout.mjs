/** P6-01: 설치된 ELK의 배치 시간과 Vite API/worker 분할 크기를 재현한다. */
import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import { createRequire } from "node:module";
import { gzipSync } from "node:zlib";
import { build } from "vite";
import ELK from "elkjs/lib/elk.bundled.js";

const require = createRequire(import.meta.url);
const api = require.resolve("elkjs/lib/elk-api.js").replaceAll("\\", "/");
const worker = require
  .resolve("elkjs/lib/elk-worker.min.js")
  .replaceAll("\\", "/");
const virtual = "virtual:elk-spike";
const bundle = await build({
  configFile: false,
  logLevel: "silent",
  plugins: [
    {
      name: "elk-spike-entry",
      resolveId: (id) => (id === virtual ? `\0${virtual}` : undefined),
      load: (id) =>
        id === `\0${virtual}`
          ? `
      globalThis.createElkSpike = async () => {
        const [{default: ELK}, {default: url}] = await Promise.all([
          import(${JSON.stringify(api)}), import(${JSON.stringify(worker + "?url")})
        ]);
        return new ELK({workerFactory: () => new Worker(url)});
      };
    `
          : undefined,
    },
  ],
  build: {
    write: false,
    assetsInlineLimit: 0,
    rolldownOptions: { input: virtual },
  },
});
const outputs = (Array.isArray(bundle) ? bundle : [bundle]).flatMap(
  (part) => part.output,
);
const chunks = outputs.map((part) => {
  const bytes = Buffer.from(part.type === "chunk" ? part.code : part.source);
  return {
    name: part.fileName,
    bytes: bytes.length,
    gzipBytes: gzipSync(bytes).length,
  };
});
assert(
  chunks.some(
    (chunk) => chunk.name.includes("elk-worker.min") && chunk.bytes > 100000,
  ),
);
assert(chunks.some((chunk) => chunk.name.includes("elk-api")));

const graph = (count, shape) => ({
  id: "root",
  layoutOptions: { "elk.algorithm": "layered", "elk.direction": "RIGHT" },
  children: Array.from({ length: count }, (_, index) => ({
    id: `node-${index}`,
    width: 200,
    height: 96,
    layoutOptions: { "elk.portConstraints": "FIXED_SIDE" },
    ports: [
      {
        id: `in-${index}`,
        width: 8,
        height: 8,
        layoutOptions: { "elk.port.side": "WEST" },
      },
      ...(shape === "join"
        ? [
            {
              id: `second-${index}`,
              width: 8,
              height: 8,
              layoutOptions: { "elk.port.side": "WEST" },
            },
          ]
        : []),
      {
        id: `out-${index}`,
        width: 8,
        height: 8,
        layoutOptions: { "elk.port.side": "EAST" },
      },
    ],
  })),
  edges: [
    ...Array.from({ length: count - 1 }, (_, index) => ({
      id: `edge-${index}`,
      sources: [`out-${shape === "branch" ? Math.floor(index / 2) : index}`],
      targets: [`in-${index + 1}`],
    })),
    ...(shape === "join"
      ? Array.from({ length: count - 2 }, (_, index) => ({
          id: `join-${index}`,
          sources: [`out-${index}`],
          targets: [`second-${index + 2}`],
        }))
      : []),
  ],
});
const elk = new ELK();
const samples = [];
for (const count of [10, 50, 200]) {
  for (const shape of ["chain", "branch", "join"]) {
    const source = graph(count, shape);
    const before = JSON.stringify(source);
    const durations = [];
    // 첫 실행을 따로 기록하고, 같은 인스턴스의 반복 20회 p50/p95를 잰다.
    for (let iteration = 0; iteration <= 20; iteration++) {
      const start = performance.now();
      const placed = await elk.layout(structuredClone(source));
      const elapsed = performance.now() - start;
      assert.equal(placed.children.length, count);
      assert(
        placed.children.every(
          (node) => Number.isFinite(node.x) && Number.isFinite(node.y),
        ),
      );
      assert(placed.edges.every((edge) => edge.sections?.length > 0));
      durations.push(elapsed);
    }
    assert.equal(JSON.stringify(source), before);
    const [cold, ...warm] = durations;
    warm.sort((a, b) => a - b);
    samples.push({
      count,
      shape,
      firstMs: cold,
      p50Ms: warm[9],
      p95Ms: warm[18],
    });
  }
}
// Node bundled는 실제 Worker를 만들지 않는다. terminateWorker는 브라우저 Worker 경로에만 적용한다.
console.log(
  JSON.stringify(
    {
      node: process.version,
      platform: process.platform,
      elk: require("elkjs/package.json").version,
      iterations: 20,
      chunks,
      samples,
      scope:
        "Node algorithm time; Vite production bytes. Browser worker/interaction latency is measured in P6-02.",
    },
    null,
    2,
  ),
);
