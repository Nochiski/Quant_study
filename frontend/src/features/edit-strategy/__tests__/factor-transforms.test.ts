import { describe, expect, test } from "vitest";

import type { FactorGraph } from "../../../entities/factor";
import {
  appendFactorTransform,
  factorTransformChain,
  quickTransforms,
  removeLastFactorTransform,
  type QuickTransform,
} from "../model/factor-transforms";

const source: FactorGraph = {
  nodes: [{ kind: "field", node_id: "close", field_id: "price.close" }],
  output_node_id: "close",
  missing_policy: "drop",
};

describe("Quick ↔ Advanced ↔ StrategySpec lossless property", () => {
  const sequences: QuickTransform[][] = [
    [],
    ...quickTransforms.map((item) => [item]),
    ...quickTransforms.flatMap((first) =>
      quickTransforms.map((second) => [first, second]),
    ),
  ];

  test.each(sequences)(
    "preserves and reverses transform chain %j",
    (...sequence) => {
      const quickGraph = sequence.reduce(appendFactorTransform, source);
      const advancedGraph = JSON.parse(
        JSON.stringify(quickGraph),
      ) as FactorGraph;

      expect(factorTransformChain(advancedGraph)).toEqual(sequence);

      const restored = sequence.reduce(
        (graph) => removeLastFactorTransform(graph),
        advancedGraph,
      );
      expect(restored).toEqual(source);
    },
  );
});
