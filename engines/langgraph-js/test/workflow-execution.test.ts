import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { StateGraph } from "@langchain/langgraph";
import { executeLiteralWorkflow, literalExecutionCapabilities, WorkflowExecutionError } from "../src/workflow-execution.js";

const cases = JSON.parse(await readFile(new URL("../../../../tests/fixtures/javascript-literal-execution.json", import.meta.url), "utf8")) as {
  id: string; tasks: unknown[]; input: unknown; expected: unknown;
}[];
const faults = JSON.parse(await readFile(new URL("../../../../tests/fixtures/javascript-literal-faults.json", import.meta.url), "utf8")) as {
  id: string; tasks: unknown[]; expected: unknown;
}[];

function workflow(tasks: unknown[], extras = {}) {
  return JSON.stringify({ document: { dsl: "1.0.3", namespace: "tests", name: "literal", version: "1.0.0" }, do: tasks, ...extras });
}

test("capture rejects an unverified checkout and preserves an existing report", async () => {
  const prefix = join(tmpdir(), "owa-literal-capture-");
  const directory = await mkdtemp(prefix);
  assert.ok(resolve(directory).startsWith(resolve(prefix)));
  try {
    const output = join(directory, "report.json");
    await writeFile(output, "original report", "utf8");
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => ["PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"].includes(key.toUpperCase())));
    const result = spawnSync(process.execPath, [fileURLToPath(new URL("./export-literal-outcomes.js", import.meta.url)), "--source-commit", "a".repeat(40), "--output", output], { env, encoding: "utf8", timeout: 10_000, maxBuffer: 4096 });
    assert.equal(result.status, 2);
    assert.equal(result.stderr, "literal outcome evidence rejected\n");
    assert.equal(await readFile(output, "utf8"), "original report");
  } finally {
    assert.ok(resolve(directory).startsWith(resolve(prefix)));
    await rm(directory, { recursive: true, force: true });
  }
});

for (const fixture of cases) {
  test(`actual JavaScript native execution matches shared literal fixture: ${fixture.id}`, async () => {
    const input = structuredClone(fixture.input);
    const result = await executeLiteralWorkflow(workflow(fixture.tasks), input);
    assert.deepEqual(result, { status: "completed", output: fixture.expected });
    assert.deepEqual(input, fixture.input);
    assert.deepEqual(Object.keys(result).sort(), ["output", "status"]);
  });
}

for (const fixture of faults) {
  test(`actual JavaScript native fault matches shared literal fixture: ${fixture.id}`, async () => {
    await assert.rejects(executeLiteralWorkflow(workflow(fixture.tasks), { private: "not-error-data" }), (error: unknown) => {
      assert.ok(error instanceof WorkflowExecutionError);
      assert.deepEqual(error.asDict(), fixture.expected);
      Object.assign(error.asDict().details, { title: "mutated caller projection" });
      assert.deepEqual(error.asDict(), fixture.expected);
      assert.ok(!JSON.stringify(error.asDict()).includes("not-error-data"));
      return true;
    });
  });
}

test("raise admission rejects references and expressions before native construction", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(StateGraph.prototype, "compile")!;
  Object.defineProperty(StateGraph.prototype, "compile", { ...descriptor, value() { throw new Error("must-not-compile"); } });
  try {
    for (const raised of [{ error: "declaredError" }, { error: { status: 400, type: "https://errors.test/", detail: "${ .secret }" } }]) {
      await assert.rejects(executeLiteralWorkflow(workflow([{ fail: { raise: raised } }]), {}), (error: unknown) => {
        assert.ok(error instanceof WorkflowExecutionError);
        assert.equal(error.code, "unsupported_workflow_feature");
        return true;
      });
    }
    const problem = { error: { status: 400, type: "https://errors.test/", title: "must-not-run" } };
    await assert.rejects(executeLiteralWorkflow(workflow([{ fail: { raise: problem } }, { late: { call: "agent:1.0.0@default" } }]), {}), (error: unknown) => {
      assert.ok(error instanceof WorkflowExecutionError);
      assert.equal(error.code, "unsupported_workflow_feature");
      assert.ok(!error.message.includes("must-not-run"));
      return true;
    });
  } finally {
    Object.defineProperty(StateGraph.prototype, "compile", descriptor);
  }
});

test("try/catch outside the literal profile rejects before constructing a native graph", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(StateGraph.prototype, "compile")!;
  Object.defineProperty(StateGraph.prototype, "compile", { ...descriptor, value() { throw new Error("must-not-compile"); } });
  const raise = { fail: { raise: { error: { status: 400, type: "https://errors.test/a" } } } };
  const handler = [{ recover: { set: { ok: true } } }];
  try {
    // The official schema rejects these before the literal profile is consulted.
    for (const body of [
      { try: [raise] },
      { catch: { do: handler } },
      { try: [raise], catch: { do: handler, as: 7 } },
      { try: [raise], catch: { errors: { with: {} }, do: handler } },
      { try: [raise], catch: { errors: { with: { status: "400" } }, do: handler } },
    ]) {
      await assert.rejects(executeLiteralWorkflow(workflow([{ guard: body }]), {}), (error: unknown) => {
        assert.ok(error instanceof Error && error.name === "WorkflowDefinitionError");
        assert.ok(!error.message.includes("must-not-compile"));
        return true;
      });
    }
    // Schema-valid shapes outside the profile reject before graph construction.
    for (const body of [
      { try: [raise], catch: {} },
      { try: [raise], catch: { errors: { with: { status: 400 } } } },
      { try: [raise], catch: { do: handler, when: "${ true }" } },
      { try: [raise], catch: { do: handler, exceptWhen: "${ false }" } },
      { try: [raise], catch: { do: handler, retry: { limit: { attempt: { count: 2 } } } } },
      { try: [raise], catch: { do: handler, then: "end" }, then: "end" },
      { try: [raise], catch: { errors: { with: { code: "x" } }, do: handler } },
      { try: [raise], catch: { errors: { with: { type: "${ .x }" } }, do: handler } },
    ]) {
      await assert.rejects(executeLiteralWorkflow(workflow([{ guard: body }]), {}), (error: unknown) => {
        assert.ok(error instanceof WorkflowExecutionError);
        assert.equal(error.code, "unsupported_workflow_feature");
        assert.ok(!error.message.includes("must-not-compile"));
        return true;
      });
    }
    await assert.rejects(
      executeLiteralWorkflow(workflow([{ guard: { try: [raise], catch: { do: handler, then: "missing" } } }]), {}),
      (error: unknown) => error instanceof WorkflowExecutionError && error.code === "workflow_semantic_error",
    );
  } finally {
    Object.defineProperty(StateGraph.prototype, "compile", descriptor);
  }
});

test("fork outside the literal profile rejects before constructing a native graph", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(StateGraph.prototype, "compile")!;
  Object.defineProperty(StateGraph.prototype, "compile", { ...descriptor, value() { throw new Error("must-not-compile"); } });
  const branch = { a: { set: { v: 1 } } };
  try {
    for (const fork of [
      { compete: true, branches: [branch] },
      { branches: [{ a: { set: { v: 1 }, then: "end" } }] },
      { branches: [{ a: { set: { v: 1 }, then: "continue" } }] },
      { branches: [{ a: { call: "agent:1.0.0@default" } }] },
      { branches: [{ a: { raise: { error: { status: 400, type: "${ .x }" } } } }] },
    ]) {
      await assert.rejects(executeLiteralWorkflow(workflow([{ par: { fork } }]), {}), (error: unknown) => {
        assert.ok(error instanceof WorkflowExecutionError);
        assert.equal(error.code, "unsupported_workflow_feature");
        assert.ok(!error.message.includes("must-not-compile"));
        return true;
      });
    }
  } finally {
    Object.defineProperty(StateGraph.prototype, "compile", descriptor);
  }
});

test("the bundled jq is exactly the version core pins", async () => {
  const { version } = await import("jq-wasm");
  assert.equal(await version(), "jq-1.8.2");
});

test("expression admission mirrors core: errors are raised before any native graph is built", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(StateGraph.prototype, "compile")!;
  Object.defineProperty(StateGraph.prototype, "compile", { ...descriptor, value() { throw new Error("must-not-compile"); } });
  try {
    // Messages and details are the ones core's compile_workflow raises for the same inputs.
    for (const [expression, message] of [
      ["${ .a + }", "invalid jq expression"],
      ["${ $nope }", "invalid jq expression"],
      ["${ .a", "unterminated runtime expression"],
      ["${ $ENV }", "ambient environment access is not enabled"],
      ["${ import \"x\" as x; 1 }", "expression modules are not enabled"],
    ] as const) {
      await assert.rejects(executeLiteralWorkflow(workflow([{ a: { set: expression } }]), {}), (error: unknown) => {
        assert.ok(error instanceof WorkflowExecutionError);
        assert.equal(error.code, "expression_error");
        assert.equal(error.message, message);
        assert.deepEqual(error.asDict().details, { reference: "/do/0/a/set" });
        return true;
      });
    }
    // Variables core binds from workflow state are not bound here, so they reject as outside the profile.
    for (const name of ["$workflow", "$context", "$input", "$secrets", "$runtime"]) {
      await assert.rejects(executeLiteralWorkflow(workflow([{ a: { set: `\${ ${name} }` } }]), {}), (error: unknown) => {
        assert.ok(error instanceof WorkflowExecutionError);
        assert.equal(error.code, "unsupported_workflow_feature");
        return true;
      });
    }
  } finally {
    Object.defineProperty(StateGraph.prototype, "compile", descriptor);
  }
});

test("expressions evaluate values only, keys stay literal, and local jq bindings work", async () => {
  const result = await executeLiteralWorkflow(
    workflow([{ a: { set: { "${ .k }": "v", local: "${ . as $x | $x.k + 1 }" } } }]), { k: 1 },
  );
  assert.deepEqual(result.output, { "${ .k }": "v", local: 2 });
});

test("switch admission mirrors core: errors are raised before any native graph is built", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(StateGraph.prototype, "compile")!;
  Object.defineProperty(StateGraph.prototype, "compile", { ...descriptor, value() { throw new Error("must-not-compile"); } });
  const targets = [{ negT: { set: { r: "neg" } } }, { posT: { set: { r: "pos" } } }];
  const route = (cases: unknown[]) => workflow([{ route: { switch: cases } }, ...targets]);
  try {
    // Codes, messages and details are the ones core's compile_workflow raises for the same inputs.
    for (const [cases, code, message, details] of [
      [[{ a: { when: "${ true }", then: "posT" } }, { a: { then: "negT" } }], "workflow_semantic_error", "duplicate switch case name", { reference: "/do/0/route/switch/1/a" }],
      [[{ a: { when: "${ true }", then: "nowhere" } }], "workflow_semantic_error", "transition target must belong to the current scope", { reference: "/do/0/route/switch/0/a/then", target: "nowhere" }],
      [[{ a: { then: "posT" } }, { b: { then: "negT" } }], "workflow_semantic_error", "switch must have at most one default case", { reference: "/do/0/route/switch" }],
      [[{ a: { when: "${ .x + }", then: "continue" } }], "expression_error", "invalid jq expression", { reference: "/do/0/route/switch/0/a/when" }],
      [[{ a: { when: "${ $ENV }", then: "continue" } }], "expression_error", "ambient environment access is not enabled", { reference: "/do/0/route/switch/0/a/when" }],
    ] as const) {
      await assert.rejects(executeLiteralWorkflow(route([...cases]), {}), (error: unknown) => {
        assert.ok(error instanceof WorkflowExecutionError);
        assert.equal(error.code, code);
        assert.equal(error.message, message);
        assert.deepEqual(error.asDict().details, details);
        return true;
      });
    }
    // Schema-valid shapes outside the profile reject as unsupported.
    for (const cases of [
      [{ a: { when: "${ $workflow }", then: "continue" } }],
    ]) {
      await assert.rejects(executeLiteralWorkflow(route(cases), {}), (error: unknown) => {
        assert.ok(error instanceof WorkflowExecutionError);
        assert.equal(error.code, "unsupported_workflow_feature");
        return true;
      });
    }
  } finally {
    Object.defineProperty(StateGraph.prototype, "compile", descriptor);
  }
});

test("for admission mirrors core: bindings, scopes and errors are checked before any native graph is built", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(StateGraph.prototype, "compile")!;
  Object.defineProperty(StateGraph.prototype, "compile", { ...descriptor, value() { throw new Error("must-not-compile"); } });
  const body = [{ step: { set: { v: 1 } } }];
  const loop = (spec: unknown, extra: Record<string, unknown> = {}, steps: unknown[] = body) =>
    workflow([{ loop: { for: spec, do: steps, ...extra } }]);
  try {
    // Codes, messages and references are the ones core's compile_workflow raises for the same inputs.
    for (const [source, code, message, reference] of [
      [loop({ each: "ENV", in: "${ .items }" }), "workflow_semantic_error", "invalid or reserved expression binding", "/do/0/loop/for/each"],
      [loop({ each: "x", in: "${ .items }", at: "ENV" }), "workflow_semantic_error", "invalid or reserved expression binding", "/do/0/loop/for/at"],
      [loop({ each: "1x", in: "${ .items }" }), "workflow_semantic_error", "invalid or reserved expression binding", "/do/0/loop/for/each"],
      [loop({ each: "x", in: "${ .items }", at: "x" }), "workflow_semantic_error", "loop item and index bindings must differ", "/do/0/loop"],
      [loop({ each: "x", in: "${ .items + }" }), "expression_error", "invalid jq expression", "/do/0/loop/for/in"],
      [loop({ each: "x", in: "${ .items }" }, { while: "${ .a + }" }), "expression_error", "invalid jq expression", "/do/0/loop/while"],
      [loop({ each: "n", in: "${ .items }" }, {}, [{ step: { set: { v: "${ $item }" } } }]), "expression_error", "invalid jq expression", "/do/0/loop/do/0/step/set"],
      [loop({ each: "n", in: "${ [$n] }" }), "expression_error", "invalid jq expression", "/do/0/loop/for/in"],
      [workflow([{ loop: { for: { each: "n", in: "${ .items }" }, do: body } }, { after: { set: { v: "${ $n }" } } }]), "expression_error", "invalid jq expression", "/do/1/after/set"],
    ] as const) {
      await assert.rejects(executeLiteralWorkflow(source, { items: [1] }), (error: unknown) => {
        assert.ok(error instanceof WorkflowExecutionError);
        assert.equal(error.code, code);
        assert.equal(error.message, message);
        assert.deepEqual(error.asDict().details, { reference });
        return true;
      });
    }
    // Workflow-state variables stay unbound here, even inside a loop body.
    await assert.rejects(
      executeLiteralWorkflow(loop({ each: "x", in: "${ .items }" }, {}, [{ step: { set: { v: "${ $workflow }" } } }]), { items: [1] }),
      (error: unknown) => error instanceof WorkflowExecutionError && error.code === "unsupported_workflow_feature",
    );
  } finally {
    Object.defineProperty(StateGraph.prototype, "compile", descriptor);
  }
});

test("loop variables are bound by name, shadow outer loops and are limited to their scope", async () => {
  const result = await executeLiteralWorkflow(workflow([{
    outer: { for: { each: "x", in: "${ .xs }" }, do: [{ inner: { for: { each: "x", in: "${ .ys }" }, do: [
      { acc: { set: { xs: "${ .xs }", ys: "${ .ys }", seen: "${ (.seen // []) + [$x] }" } } },
    ] } }] },
  }]), { xs: [1, 2], ys: [10, 20] });
  // The inner `x` shadows the outer one, so only the inner values are recorded.
  assert.deepEqual(result.output, { xs: [1, 2], ys: [10, 20], seen: [10, 20, 10, 20] });
});

test("unsupported late tasks reject before constructing a native graph", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(StateGraph.prototype, "compile")!;
  Object.defineProperty(StateGraph.prototype, "compile", { ...descriptor, value() { throw new Error("must-not-compile"); } });
  try {
    for (const tasks of [
      [{ first: { set: { ok: true } } }, { unsupported: { call: "agent:1.0.0@default" } }],
      [{ expression: { raise: { error: { status: 400, type: "https://errors.test/a", title: "${ .secret }" } } } }],
      [{ output: { set: { ok: true }, output: { as: "${ . }" } } }],
      [{ timeout: { set: { ok: true }, timeout: { after: { seconds: 1 } } } }],
    ]) {
      await assert.rejects(executeLiteralWorkflow(workflow(tasks), {}), (error: unknown) => {
        assert.ok(error instanceof WorkflowExecutionError);
        assert.equal(error.code, "unsupported_workflow_feature");
        assert.ok(!error.message.includes("must-not-compile"));
        return true;
      });
    }
    await assert.rejects(executeLiteralWorkflow(undefined, {}), (error: unknown) => {
      assert.ok(error instanceof WorkflowExecutionError);
      assert.equal(error.reference, "/do/0/respond");
      return true;
    });
  } finally {
    Object.defineProperty(StateGraph.prototype, "compile", descriptor);
  }
});

test("invalid transitions, duplicate names, unsupported numbers and cyclic input fail closed", async () => {
  for (const tasks of [
    [{ first: { set: { ok: true }, then: "missing" } }],
    [{ same: { set: { one: true } } }, { same: { set: { two: true } } }],
  ]) {
    await assert.rejects(executeLiteralWorkflow(workflow(tasks), {}), (error: unknown) => {
      assert.ok(error instanceof WorkflowExecutionError);
      assert.equal(error.code, "workflow_semantic_error");
      return true;
    });
  }
  const cyclic: Record<string, unknown> = {}; cyclic.self = cyclic;
  for (const input of [cyclic, { unsafe: Number.MAX_SAFE_INTEGER + 1 }, { nonfinite: Infinity }, { object: new Date() }]) {
    await assert.rejects(executeLiteralWorkflow(workflow([{ value: { set: { ok: true } } }]), input), WorkflowExecutionError);
  }
});

test("a native routing loop is finite and returns a sanitized common error", async () => {
  await assert.rejects(executeLiteralWorkflow(workflow([{ loop: { set: { ok: true }, then: "loop" } }]), {}), (error: unknown) => {
    assert.ok(error instanceof WorkflowExecutionError);
    assert.equal(error.code, "workflow_execution_error");
    assert.equal(error.message, "workflow step limit exceeded");
    return true;
  });
  const capabilities = literalExecutionCapabilities();
  assert.equal(capabilities.experimental, true);
  assert.equal(capabilities.persistence, false);
  assert.equal(capabilities.publicInvocationIdentity, false);
});

test("value/task bounds reject before execution and host accessors are never evaluated", async () => {
  const source = workflow([{ value: { set: { ok: true } } }]);
  const accessor = Object.defineProperty({}, "private", { enumerable: true, get() { assert.fail("must not invoke a host getter"); } });
  const indexed = [1];
  Object.defineProperty(indexed, "0", { get() { assert.fail("must not invoke an array getter"); } });
  for (const input of [accessor, indexed, Array(1), { [Symbol("private")]: "hidden" }, "x".repeat(1_048_577)]) {
    await assert.rejects(executeLiteralWorkflow(source, input), WorkflowExecutionError);
  }
  await assert.rejects(executeLiteralWorkflow(workflow(Array.from({ length: 257 }, (_, index) => ({ [`task_${index}`]: { set: { ok: true } } }))), {}), WorkflowExecutionError);
  let nested: unknown[] = [{ value: { set: { ok: true } } }];
  for (let depth = 0; depth < 9; depth++) nested = [{ scope: { do: nested } }];
  await assert.rejects(executeLiteralWorkflow(workflow(nested), {}), WorkflowExecutionError);
  const error = new WorkflowExecutionError("workflow_execution_error", "controlled", "/" + "x".repeat(9000));
  assert.deepEqual(error.asDict(), { code: "workflow_execution_error", message: "controlled", details: { reference: "/" } });
});
