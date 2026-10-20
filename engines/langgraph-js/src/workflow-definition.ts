import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { Validator } from "@cfworker/json-schema";
import { parse } from "yaml";

export const OPEN_WORKFLOW_DSL = "1.0.3";
export const MAX_WORKFLOW_SOURCE_BYTES = 1_048_576;
export const OFFICIAL_SCHEMA_SHA256 =
  "704ef5e91c5d823167dd8751794edb1dd1a6f9a3bdf9bfd389bf9c6b23ae3816";

export type WorkflowDefinition = Record<string, unknown>;
type JsonSchema = Record<string, unknown>;

export class WorkflowDefinitionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkflowDefinitionError";
  }
}

const defaultSchemaPath = fileURLToPath(
  new URL("../../../../resources/open-workflow/1.0.3/workflow.yaml", import.meta.url),
);

async function loadOfficialSchema(schemaPath: string): Promise<JsonSchema> {
  let raw: string;
  try {
    raw = await readFile(schemaPath, "utf8");
  } catch {
    throw new WorkflowDefinitionError("official Open Workflow 1.0.3 schema is unavailable");
  }

  const digest = createHash("sha256").update(raw, "utf8").digest("hex");
  if (digest !== OFFICIAL_SCHEMA_SHA256) {
    throw new WorkflowDefinitionError("official Open Workflow 1.0.3 schema does not match its SHA-256 pin");
  }

  let schema: unknown;
  try {
    schema = parse(raw);
  } catch {
    throw new WorkflowDefinitionError("official Open Workflow 1.0.3 schema could not be parsed");
  }
  if (
    typeof schema !== "object" ||
    schema === null ||
    Array.isArray(schema) ||
    (schema as JsonSchema)["$id"] !== "https://open-workflow-specification.org/schemas/1.0.3/workflow.yaml" ||
    (schema as JsonSchema)["$schema"] !== "https://json-schema.org/draft/2020-12/schema"
  ) {
    throw new WorkflowDefinitionError("pinned Open Workflow schema has an unexpected identity or dialect");
  }
  return schema as JsonSchema;
}

/** Parse and validate a YAML or JSON document against the pinned official OWS 1.0.3 schema. */
export async function loadWorkflowDefinition(
  source: string,
  schemaPath = defaultSchemaPath,
): Promise<WorkflowDefinition> {
  if (Buffer.byteLength(source, "utf8") > MAX_WORKFLOW_SOURCE_BYTES) {
    throw new WorkflowDefinitionError("workflow document exceeds the 1 MiB source limit");
  }

  let definition: unknown;
  try {
    definition = parse(source);
  } catch {
    throw new WorkflowDefinitionError("workflow document could not be parsed");
  }
  if (typeof definition !== "object" || definition === null || Array.isArray(definition)) {
    throw new WorkflowDefinitionError("workflow root must be an object");
  }

  const schema = await loadOfficialSchema(schemaPath);
  const result = new Validator(schema, "2020-12").validate(definition);
  if (!result.valid) {
    const issue = result.errors?.[0];
    const path = issue?.instanceLocation || "root";
    const message = issue?.error || "definition does not satisfy the official schema";
    throw new WorkflowDefinitionError(`schema validation failed at ${path}: ${message}`);
  }

  const document = (definition as WorkflowDefinition)["document"];
  if (
    typeof document !== "object" ||
    document === null ||
    Array.isArray(document) ||
    (document as Record<string, unknown>)["dsl"] !== OPEN_WORKFLOW_DSL
  ) {
    throw new WorkflowDefinitionError("only Open Workflow DSL 1.0.3 is supported");
  }
  return definition as WorkflowDefinition;
}
