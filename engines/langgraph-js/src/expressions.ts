import { loadJq } from "jq-wasm";

/**
 * jq 1.8.2 evaluation of workflow-owned expressions, mirroring core's
 * `open_workflow_agent.expressions` for the literal-plus-expression profile.
 * No variable bindings are supplied, so ambient variables are rejected up front.
 */

export class ExpressionFailure extends Error {
  constructor(message: string, readonly reason?: string) {
    super(message);
    this.name = "ExpressionFailure";
  }
}

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Jq = Awaited<ReturnType<typeof loadJq>>;
export type Variables = Readonly<Record<string, Json>>;

const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;

// Identical text to core's _PRELUDE/_POSTLUDE: the line count is observable through $__loc__.
const PRELUDE = `
def env: error("environment access is disabled");
def input: error("external input is disabled");
def inputs: error("external input is disabled");
def debug: error("expression diagnostics are disabled");
def debug($messages): error("expression diagnostics are disabled");
def stderr: error("expression diagnostics are disabled");
def modulemeta: error("expression modules are disabled");
def halt: error("expression process control is disabled");
def halt_error: error("expression process control is disabled");
def halt_error($code): error("expression process control is disabled");
def now: error("ambient time access is disabled");
{} as $ENV | (
`;
const POSTLUDE = `
) | if any(..; type == "number" and (isnan or isinfinite))
    then error("non-finite expression output") else . end
`;

// Variables core binds from the workflow state; this profile binds none.
const UNBOUND_AMBIENT = new Set([
  "$workflow", "$runtime", "$context", "$event", "$resume_input", "$task", "$input", "$output",
  "$secrets", "$authorization", "$error",
]);

let handle: Jq | undefined;

export async function loadExpressions(): Promise<void> {
  handle ??= await loadJq();
}

function jq(): Jq {
  if (handle === undefined) throw new ExpressionFailure("jq expression evaluation failed");
  return handle;
}

export function validateExpressionData(value: unknown, depth = 0, maxDepth = 64): asserts value is Json {
  const invalid = () => new ExpressionFailure(
    "expression data must be finite JSON with safe integers and bounded nesting", "invalid_json",
  );
  if (depth > maxDepth) throw invalid();
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (!Number.isFinite(value) || (Number.isInteger(value) && Math.abs(value) > MAX_SAFE_INTEGER)) throw invalid();
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) validateExpressionData(item, depth + 1, maxDepth);
    return;
  }
  if (typeof value === "object") {
    for (const item of Object.values(value as Record<string, unknown>)) validateExpressionData(item, depth + 1, maxDepth);
    return;
  }
  throw invalid();
}

function expressionString(value: Json): string {
  validateExpressionData(value);
  return typeof value === "string" ? value : JSON.stringify(value);
}

function stringEnd(text: string, start: number): number {
  let index = start + 1;
  while (index < text.length) {
    if (text[index] === "\\") {
      if (index + 1 < text.length && text[index + 1] === "(") index = groupEnd(text, index + 1, "(", ")") + 1;
      else index += 2;
    } else if (text[index] === '"') return index;
    else index += 1;
  }
  throw new ExpressionFailure("unterminated expression string");
}

function commentEnd(text: string, start: number): number {
  for (;;) {
    const newline = text.indexOf("\n", start);
    if (newline < 0) return text.length;
    const line = text.slice(start, newline).replace(/\r+$/, "");
    const backslashes = line.length - line.replace(/\\+$/, "").length;
    start = newline + 1;
    if (backslashes % 2 === 0) return start;
  }
}

function groupEnd(text: string, start: number, opening: string, closing: string): number {
  let depth = 1;
  let index = start + 1;
  while (index < text.length) {
    const character = text[index];
    if (character === '"') {
      index = stringEnd(text, index) + 1;
      continue;
    }
    if (character === "#") {
      index = commentEnd(text, index);
      continue;
    }
    if (character === opening) depth += 1;
    else if (character === closing) {
      depth -= 1;
      if (depth === 0) return index;
    }
    index += 1;
  }
  throw new ExpressionFailure("unterminated runtime expression");
}

function segments(text: string): [boolean, string][] {
  const result: [boolean, string][] = [];
  let index = 0;
  while (index < text.length) {
    const start = text.indexOf("${", index);
    if (start < 0) {
      result.push([false, text.slice(index)]);
      break;
    }
    if (start > index) result.push([false, text.slice(index, start)]);
    const end = groupEnd(text, start + 1, "{", "}");
    result.push([true, text.slice(start + 2, end)]);
    index = end + 1;
  }
  return result;
}

function tokens(text: string): [string, number][] {
  const found: [string, number][] = [];
  let index = 0;
  while (index < text.length) {
    if (text[index] === '"') {
      const end = stringEnd(text, index);
      let current = index + 1;
      while (current < end) {
        if (text[current] === "\\") {
          if (current + 1 < end && text[current + 1] === "(") {
            const closing = groupEnd(text, current + 1, "(", ")");
            for (const [token, offset] of tokens(text.slice(current + 2, closing))) found.push([token, current + 2 + offset]);
            current = closing + 1;
          } else current += 2;
        } else current += 1;
      }
      index = end + 1;
    } else if (text[index] === "#") index = commentEnd(text, index);
    else {
      const match = /^\$?[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(index));
      if (match) {
        found.push([match[0], index]);
        index += match[0].length;
      } else index += 1;
    }
  }
  return found;
}

/** Bindings are prepended on the first program line, so the prelude's line numbers are unchanged. */
function program(source: string, bindings = ""): string {
  return bindings + PRELUDE + source + "\n" + POSTLUDE;
}

function bindingsFor(source: string, variables: Variables): string {
  const used = new Set(tokens(source).filter(([token]) => token.startsWith("$")).map(([token]) => token.slice(1)));
  let text = "";
  for (const [name, value] of Object.entries(variables)) {
    if (!used.has(name)) continue;
    // Core validates each binding with the same bounds; descriptor wrappers get a little extra depth.
    validateExpressionData(value, 0, name === "workflow" || name === "task" ? 68 : 64);
    text += `${JSON.stringify(value)} as $${name} | `;
  }
  return text;
}

/** Admission: reject denied constructs, then compile (without running) via an uncalled definition. */
function admit(source: string, scope: readonly string[]): void {
  for (const [token, position] of tokens(source)) {
    if (token === "$ENV") throw new ExpressionFailure("ambient environment access is not enabled");
    if (UNBOUND_AMBIENT.has(token) && !scope.includes(token.slice(1))) throw new ExpressionFailure("expression variables are not enabled in this profile", "unsupported_profile");
    if (token === "include" || token === "import") {
      const before = source.slice(0, position).trimEnd();
      const after = source.slice(position + token.length).trimStart();
      if (!before.endsWith(".") && !after.startsWith(":")) throw new ExpressionFailure("expression modules are not enabled");
    }
  }
  try {
    // jq drops uncalled definitions before resolving names; a branch that never runs forces
    // full compilation (undefined variables and functions) without executing the expression.
    const declared = scope.map((name) => `null as $${name} | `).join("");
    jq().json(null, `def __owa_admission: ${program(source, declared)};\nif false then __owa_admission else null end`);
  } catch {
    throw new ExpressionFailure("invalid jq expression");
  }
}

/** Admit every `${ }` segment in a value tree; keys are never evaluated. */
export function admitExpressions(value: unknown, scope: readonly string[] = []): void {
  if (Array.isArray(value)) value.forEach((item) => admitExpressions(item, scope));
  else if (value !== null && typeof value === "object") Object.values(value).forEach((item) => admitExpressions(item, scope));
  else if (typeof value === "string" && value.includes("${")) {
    for (const [executable, source] of segments(value)) if (executable) admit(source, scope);
  }
}

export function hasExpression(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasExpression);
  if (value !== null && typeof value === "object") return Object.values(value).some(hasExpression);
  return typeof value === "string" && value.includes("${");
}

function evaluateAtom(source: string, data: Json, variables: Variables): Json {
  validateExpressionData(data);
  let results: unknown[];
  try {
    results = jq().json(structuredClone(data), program(source, bindingsFor(source, variables)));
  } catch {
    throw new ExpressionFailure("jq expression evaluation failed");
  }
  if (results.length !== 1) throw new ExpressionFailure("jq expression must produce exactly one JSON value");
  validateExpressionData(results[0]);
  return results[0] as Json;
}

export function evaluateExpression(expression: Json, data: Json, variables: Variables = {}): Json {
  validateExpressionData(data);
  if (Array.isArray(expression)) return expression.map((item) => evaluateExpression(item, data, variables));
  if (expression !== null && typeof expression === "object") {
    return Object.fromEntries(Object.entries(expression).map(([key, item]) => [key, evaluateExpression(item, data, variables)]));
  }
  if (typeof expression !== "string") {
    validateExpressionData(expression);
    return structuredClone(expression);
  }
  if (!expression.includes("${")) return expression;
  const parts = segments(expression);
  const expressions = parts.filter(([executable]) => executable).map(([, source]) => source);
  const literals = parts.filter(([executable]) => !executable).map(([, source]) => source);
  if (expressions.length === 1 && literals.join("").trim() === "") return evaluateAtom(expressions[0], data, variables);
  return parts.map(([executable, source]) => (executable ? expressionString(evaluateAtom(source, data, variables)) : source)).join("");
}
