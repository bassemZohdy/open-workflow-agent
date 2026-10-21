import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { Validator } from "@cfworker/json-schema";
import { isAlias, isMap, isNode, isPair, isScalar, isSeq, parse, parseDocument } from "yaml";
import type { Node as YamlNode, Scalar as YamlScalar } from "yaml";

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

function collectAnchors(node: YamlNode | null, anchors: Map<string, YamlNode>, depth = 0): void {
  if (node === null) return;
  if (depth > 64) {
    throw new WorkflowDefinitionError("workflow must be acyclic JSON data with depth at most 64");
  }
  if (isAlias(node)) return;
  const anchor = (node as YamlNode & { anchor?: string }).anchor;
  if (anchor) anchors.set(anchor, node);
  if (isMap(node)) {
    for (const item of node.items) {
      if (isPair(item)) {
        if (isNode(item.key)) collectAnchors(item.key, anchors, depth + 1);
        if (isNode(item.value)) collectAnchors(item.value, anchors, depth + 1);
      }
    }
  } else if (isSeq(node)) {
    for (const item of node.items) {
      if (isNode(item)) collectAnchors(item, anchors, depth + 1);
    }
  }
}

function containsPlainTimestamp(
  node: YamlNode | null,
  anchors: Map<string, YamlNode>,
  ancestors = new Set<YamlNode>(),
  visited = new Set<YamlNode>(),
  depth = 0,
): boolean {
  if (node === null) return false;
  if (depth > 64) {
    throw new WorkflowDefinitionError("workflow must be acyclic JSON data with depth at most 64");
  }
  if (isAlias(node)) {
    const target = anchors.get(node.source);
    if (target === undefined) return false;
    if (ancestors.has(target)) {
      throw new WorkflowDefinitionError("workflow document could not be parsed");
    }
    return visited.has(target)
      ? false
      : containsPlainTimestamp(target, anchors, ancestors, visited, depth + 1);
  }
  if (visited.has(node)) return false;
  if (ancestors.has(node)) {
    throw new WorkflowDefinitionError("workflow document could not be parsed");
  }
  const active = new Set(ancestors);
  active.add(node);
  if (isScalar(node)) {
    const scalar = node as YamlScalar.Parsed;
    const timestamp = scalar.type === "PLAIN" && typeof scalar.value === "string" && (
      /^\d{4}-\d{1,2}-\d{1,2}$/.test(scalar.source) ||
      /^\d{4}-\d{1,2}-\d{1,2}(?:[Tt]|[ \t]+)\d{1,2}:\d{2}:\d{2}(?:\.\d*)?(?:[ \t]*(?:Z|[-+]\d{1,2}(?::?\d{2})?))?$/.test(
        scalar.source,
      )
    );
    visited.add(node);
    return timestamp;
  }
  let found = false;
  if (isMap(node)) {
    for (const item of node.items) {
      if (
        isPair(item) &&
        ((isNode(item.key) && containsPlainTimestamp(item.key, anchors, active, visited, depth + 1)) ||
          (isNode(item.value) && containsPlainTimestamp(item.value, anchors, active, visited, depth + 1)))
      ) {
        found = true;
        break;
      }
    }
  }
  if (!found && isSeq(node)) {
    for (const item of node.items) {
      if (isNode(item) && containsPlainTimestamp(item, anchors, active, visited, depth + 1)) {
        found = true;
        break;
      }
    }
  }
  visited.add(node);
  return found;
}

function assertFiniteJsonValue(value: unknown, ancestors = new Set<object>(), depth = 0): void {
  if (depth > 64) {
    throw new WorkflowDefinitionError("workflow must be acyclic JSON data with depth at most 64");
  }
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (Number.isFinite(value)) return;
    throw new WorkflowDefinitionError("workflow values must be finite JSON data");
  }
  if (typeof value !== "object") {
    throw new WorkflowDefinitionError("workflow values must be finite JSON data");
  }
  if (ancestors.has(value)) {
    throw new WorkflowDefinitionError("workflow must be acyclic JSON data with depth at most 64");
  }

  const active = new Set(ancestors);
  active.add(value);
  if (Array.isArray(value)) {
    for (const item of value) assertFiniteJsonValue(item, active, depth + 1);
    return;
  }
  if (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) {
    throw new WorkflowDefinitionError("workflow values must be finite JSON data");
  }
  for (const [key, item] of Object.entries(value)) {
    if (typeof key !== "string") {
      throw new WorkflowDefinitionError("workflow mapping keys must be strings");
    }
    assertFiniteJsonValue(item, active, depth + 1);
  }
}

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

  let yamlDocument: ReturnType<typeof parseDocument>;
  try {
    yamlDocument = parseDocument(source);
  } catch {
    throw new WorkflowDefinitionError("workflow document could not be parsed");
  }
  if (yamlDocument.errors.length > 0) {
    throw new WorkflowDefinitionError("workflow document could not be parsed");
  }
  const anchors = new Map<string, YamlNode>();
  collectAnchors(yamlDocument.contents, anchors);
  if (containsPlainTimestamp(yamlDocument.contents, anchors)) {
    throw new WorkflowDefinitionError("workflow document contains a YAML timestamp scalar");
  }

  let definition: unknown;
  try {
    definition = yamlDocument.toJS();
  } catch {
    throw new WorkflowDefinitionError("workflow document could not be parsed");
  }
  assertFiniteJsonValue(definition);
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
