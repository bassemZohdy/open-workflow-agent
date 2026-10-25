import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

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

test("unsupported late tasks reject before constructing a native graph", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(StateGraph.prototype, "compile")!;
  Object.defineProperty(StateGraph.prototype, "compile", { ...descriptor, value() { throw new Error("must-not-compile"); } });
  try {
    for (const tasks of [
      [{ first: { set: { ok: true } } }, { unsupported: { call: "agent:1.0.0@default" } }],
      [{ expression: { set: { value: "${ .secret }" } } }],
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
