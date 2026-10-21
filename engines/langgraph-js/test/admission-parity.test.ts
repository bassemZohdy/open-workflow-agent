import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  loadWorkflowDefinition,
  WorkflowDefinitionError,
  type WorkflowDefinition,
} from "../src/workflow-definition.js";

type Case = {
  id: string;
  source: string;
  outcome: "accept" | "reject";
  expected?: WorkflowDefinition;
  category?: ErrorCategory;
};

type ErrorCategory =
  | "invalid_document"
  | "non_json_value"
  | "root_not_object"
  | "schema_invalid"
  | "unsupported_version";

const fixtureUrl = new URL(
  "../../../../tests/fixtures/workflow-definition-admission.json",
  import.meta.url,
);
const cases = JSON.parse(await readFile(fixtureUrl, "utf8")) as Case[];

function category(error: WorkflowDefinitionError): ErrorCategory {
  const message = error.message;
  if (
    message === "workflow document could not be parsed" ||
    message === "workflow mapping keys must be strings"
  ) {
    return "invalid_document";
  }
  if (
    message === "workflow document contains a YAML timestamp scalar" ||
    message === "workflow values must be finite JSON data"
  ) {
    return "non_json_value";
  }
  if (message === "workflow root must be an object") return "root_not_object";
  if (message.startsWith("schema validation failed at ")) return "schema_invalid";
  if (message === "only Open Workflow DSL 1.0.3 is supported") return "unsupported_version";
  throw new Error(`unclassified workflow admission error: ${message}`);
}

for (const fixture of cases) {
  test(`TypeScript definition admission matches shared fixture: ${fixture.id}`, async () => {
    if (fixture.outcome === "accept") {
      assert.deepEqual(await loadWorkflowDefinition(fixture.source), fixture.expected);
      return;
    }

    await assert.rejects(loadWorkflowDefinition(fixture.source), (error: unknown) => {
      assert.ok(error instanceof WorkflowDefinitionError);
      assert.equal(category(error), fixture.category);
      return true;
    });
  });
}
