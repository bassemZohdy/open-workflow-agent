import assert from "node:assert/strict";
import test from "node:test";

import { buildNativeSmokeGraph } from "../src/native-smoke.js";

test("pinned LangGraph builds and invokes a deterministic native graph", async () => {
  const graph = buildNativeSmokeGraph();
  const result = await graph.invoke({ value: 41 });

  assert.deepEqual(result, { value: 42 });
});
