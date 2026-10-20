import assert from "node:assert/strict";
import test from "node:test";

import {
  loadWorkflowDefinition,
  WorkflowDefinitionError,
} from "../src/workflow-definition.js";

const minimalDefinition = `
document:
  dsl: 1.0.3
  namespace: example
  name: js-loader
  version: 1.0.0
do:
  - respond:
      call: agent:1.0.0@default
`;

test("loads a minimal YAML definition against the pinned official OWS schema", async () => {
  const definition = await loadWorkflowDefinition(minimalDefinition);

  assert.deepEqual(definition["document"], {
    dsl: "1.0.3",
    namespace: "example",
    name: "js-loader",
    version: "1.0.0",
  });
});

test("rejects schema-invalid definitions without executing them", async () => {
  await assert.rejects(
    loadWorkflowDefinition("document: { dsl: '1.0.3', namespace: x, name: y, version: '1.0.0' }\n"),
    (error: unknown) => {
      assert.ok(error instanceof WorkflowDefinitionError);
      assert.match(error.message, /^schema validation failed at /);
      return true;
    },
  );
});

test("rejects valid-looking documents that select a different OWS DSL version", async () => {
  await assert.rejects(
    loadWorkflowDefinition(minimalDefinition.replace("dsl: 1.0.3", "dsl: 1.0.2")),
    /only Open Workflow DSL 1\.0\.3 is supported/,
  );
});

test("rejects malformed YAML and non-object roots", async () => {
  await assert.rejects(loadWorkflowDefinition("document: ["), /could not be parsed/);
  await assert.rejects(loadWorkflowDefinition("- workflow"), /workflow root must be an object/);
});

test("does not expose malformed YAML source excerpts in parse errors", async () => {
  const sentinel = "malformed-workflow-secret-7f8c";
  await assert.rejects(
    loadWorkflowDefinition(`document: "${sentinel}`),
    (error: unknown) => {
      assert.ok(error instanceof WorkflowDefinitionError);
      assert.equal(error.message, "workflow document could not be parsed");
      assert.equal(error.message.includes(sentinel), false);
      return true;
    },
  );
});

test("rejects a schema file that differs from the reviewed official digest", async () => {
  await assert.rejects(
    loadWorkflowDefinition(minimalDefinition, "not-the-official-schema.yaml"),
    /official Open Workflow 1\.0\.3 schema is unavailable/,
  );
});
