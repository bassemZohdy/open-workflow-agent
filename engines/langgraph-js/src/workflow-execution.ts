import { Annotation, END, START, StateGraph } from "@langchain/langgraph";

import { admitExpressions, evaluateExpression, ExpressionFailure, hasExpression, loadExpressions } from "./expressions.js";
import { loadWorkflowDefinition } from "./workflow-definition.js";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type SwitchCase = Readonly<{ name: string; when?: Json; next: string; endsWorkflow: boolean }>;
type Task = Readonly<{
  reference: string;
  key: string;
  name: string;
  next: string;
  endsWorkflow: boolean;
  value?: Json;
  dynamic?: boolean;
  cases?: readonly SwitchCase[];
  fallback?: SwitchCase;
  problem?: Readonly<Record<string, Json>>;
  children?: readonly Task[];
  attempt?: readonly Task[];
  branches?: readonly (readonly Task[])[];
  handler?: Readonly<{
    filter: Readonly<Record<string, Json>>;
    children: readonly Task[];
    next?: string;
    endsWorkflow?: boolean;
  }>;
}>;

const MAX_TASKS = 256;
const MAX_STEPS = 512;
const MAX_SCOPE_DEPTH = 8;
const MAX_VALUE_BYTES = 1_048_576;
const State = Annotation.Root({
  data: Annotation<Json>,
  next: Annotation<string>,
  ended: Annotation<boolean>,
});

export class WorkflowExecutionError extends Error {
  constructor(
    readonly code: "unsupported_workflow_feature" | "workflow_semantic_error" | "workflow_execution_error" | "expression_error",
    message: string,
    readonly reference: string,
    readonly problem?: Readonly<Record<string, Json>>,
    /** Values a `catch` filter sees for errors that are catchable but not OWS `raise` problems. */
    readonly catchView?: Readonly<Record<string, Json>>,
    /** Extra projected details for admission errors that carry more than a reference. */
    private readonly admission?: Readonly<Record<string, Json>>,
  ) {
    super(message);
    this.name = "WorkflowExecutionError";
    if (Buffer.byteLength(reference, "utf8") > 8192) this.reference = "/";
  }

  asDict() {
    return {
      code: this.code, message: this.message,
      details: this.problem === undefined ? { reference: this.reference, ...structuredClone(this.admission ?? {}) }
        : { ...structuredClone(this.problem), instance: this.reference },
    };
  }
}

function reject(reference: string): never {
  throw new WorkflowExecutionError(
    "unsupported_workflow_feature", "feature is outside the JavaScript literal execution profile", reference,
  );
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function jsonValue(value: unknown, reference: string, depth = 0, literal = false): asserts value is Json {
  if (depth > 64) reject(reference);
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    if (literal && value.includes("${")) reject(reference);
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) reject(reference);
    return;
  }
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== Array.prototype || Reflect.ownKeys(value).length !== value.length + 1) reject(reference);
    for (let index = 0; index < value.length; index++) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
      if (descriptor === undefined || !Object.hasOwn(descriptor, "value") || !descriptor.enumerable) reject(reference);
      jsonValue(descriptor.value, reference, depth + 1, literal);
    }
    return;
  }
  if (!object(value) || (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null)) reject(reference);
  if (Reflect.ownKeys(value).some((key) => typeof key !== "string")) reject(reference);
  for (const descriptor of Object.values(Object.getOwnPropertyDescriptors(value))) {
    if (!Object.hasOwn(descriptor, "value") || !descriptor.enumerable) reject(reference);
    jsonValue(descriptor.value, reference, depth + 1, literal);
  }
}

function bounded(value: unknown, reference: string, literal = false): Json {
  jsonValue(value, reference, 0, literal);
  if (Buffer.byteLength(JSON.stringify(value), "utf8") > MAX_VALUE_BYTES) reject(reference);
  return structuredClone(value);
}

function freeze<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

function pointer(value: string): string {
  return value.replaceAll("~", "~0").replaceAll("/", "~1");
}

const FILTER_KEYS = ["type", "status", "instance", "title", "detail"] as const;

function compileFilter(catchBody: Record<string, unknown>, reference: string): Readonly<Record<string, Json>> {
  if (catchBody.errors === undefined) return {};
  const errors = catchBody.errors;
  if (!object(errors) || Object.keys(errors).length !== 1 || !object(errors.with)) reject(`${reference}/errors`);
  const filter = errors.with;
  const keys = Object.keys(filter);
  if (keys.length === 0 || keys.some((key) => !(FILTER_KEYS as readonly string[]).includes(key))) reject(`${reference}/errors/with`);
  for (const key of keys) {
    const value = filter[key];
    const valid = key === "status" ? typeof value === "number" && Number.isSafeInteger(value) : typeof value === "string";
    if (!valid) reject(`${reference}/errors/with/${key}`);
  }
  return bounded(filter, `${reference}/errors/with`, true) as Record<string, Json>;
}

function catchMatches(filter: Readonly<Record<string, Json>>, error: WorkflowExecutionError): boolean {
  const problem = error.catchView ?? error.problem ?? {};
  const message = problem.detail || problem.title || problem.type;
  const values: Record<string, Json | undefined> = {
    type: problem.type, status: problem.status, instance: error.reference,
    title: problem.title ?? error.code, detail: problem.detail ?? String(message),
  };
  return Object.entries(filter).every(([key, expected]) => values[key] === expected);
}

function admitSetExpressions(value: Json, reference: string): void {
  try {
    admitExpressions(value);
  } catch (error) {
    if (!(error instanceof ExpressionFailure)) throw error;
    if (error.reason === "unsupported_profile") reject(reference);
    throw new WorkflowExecutionError("expression_error", error.message, reference);
  }
}

const EXPRESSION_ERROR_TYPE = "https://serverlessworkflow.io/spec/1.0.0/errors/expression";

function evaluateSet(task: Task, data: Json, value: Json = task.value!): Json {
  try {
    const result = evaluateExpression(value, data);
    if (Buffer.byteLength(JSON.stringify(result), "utf8") > MAX_VALUE_BYTES) {
      throw new WorkflowExecutionError("workflow_execution_error", "expression result exceeds the value limit", task.reference);
    }
    return result;
  } catch (error) {
    if (!(error instanceof ExpressionFailure)) throw error;
    throw new WorkflowExecutionError(
      "expression_error", error.message, task.reference,
      error.reason === undefined ? {} : { reason: error.reason },
      { type: EXPRESSION_ERROR_TYPE, status: 400, title: "expression_error", detail: error.message },
    );
  }
}

/** First matching `when` wins; the default applies only when none matches; jq truthiness. */
function selectCase(task: Task, data: Json): SwitchCase | undefined {
  for (const item of task.cases ?? []) {
    const outcome = hasExpression(item.when!) ? evaluateSet(task, data, item.when) : item.when;
    if (outcome !== null && outcome !== false) return item;
  }
  return task.fallback;
}

function compileScope(
  value: unknown, reference: string, budget: { tasks: number }, depth = 0, offset = 0,
): readonly Task[] {
  if (!Array.isArray(value) || value.length === 0 || depth > MAX_SCOPE_DEPTH) reject(reference);
  const entries: { name: string; body: Record<string, unknown>; reference: string; key: string }[] = [];
  const names = new Map<string, string>();
  for (const [index, entry] of value.entries()) {
    if (!object(entry) || Object.keys(entry).length !== 1) reject(reference);
    const [name, body] = Object.entries(entry)[0];
    if (name.length === 0 || Buffer.byteLength(name, "utf8") > 256) reject(`${reference}/${index + offset}`);
    const taskReference = `${reference}/${index + offset}/${pointer(name)}`;
    if (names.has(name)) {
      throw new WorkflowExecutionError("workflow_semantic_error", "duplicate task name in scope", taskReference);
    }
    if (!object(body) || ++budget.tasks > MAX_TASKS) reject(taskReference);
    const key = `task_${index}`;
    names.set(name, key);
    entries.push({ name, body, reference: taskReference, key });
  }
  return freeze(entries.map((entry, index): Task => {
    const { name, body, reference: taskReference, key } = entry;
    if (Object.keys(body).some((field) => !["set", "do", "raise", "try", "catch", "fork", "switch", "then", "metadata"].includes(field))) reject(taskReference);
    if (["set", "do", "raise", "try", "fork", "switch"].filter((kind) => Object.hasOwn(body, kind)).length !== 1) reject(taskReference);
    if (Object.hasOwn(body, "catch") !== Object.hasOwn(body, "try")) reject(taskReference);
    const resolve = (transition: unknown, at: string, withTarget = false): { next: string; ends: boolean } => {
      if (typeof transition !== "string") reject(at);
      if (transition === "exit" || transition === "end") return { next: END, ends: transition === "end" };
      if (transition === "continue") return { next: entries[index + 1]?.key ?? END, ends: false };
      const target = names.get(transition);
      if (target === undefined) {
        throw new WorkflowExecutionError(
          "workflow_semantic_error", "transition target must belong to the current scope", at,
          undefined, undefined, withTarget ? { target: transition } : undefined,
        );
      }
      return { next: target, ends: false };
    };
    const { next, ends } = resolve(body.then ?? "continue", `${taskReference}/then`);
    let operation: Pick<Task, "value" | "dynamic" | "children" | "problem" | "attempt" | "handler" | "branches" | "cases" | "fallback">;
    if (Object.hasOwn(body, "set")) {
      const value = bounded(body.set, `${taskReference}/set`);
      const dynamic = hasExpression(value);
      if (dynamic) admitSetExpressions(value, `${taskReference}/set`);
      operation = { value, dynamic };
    }
    else if (Object.hasOwn(body, "do")) operation = { children: compileScope(body.do, `${taskReference}/do`, budget, depth + 1) };
    else if (Object.hasOwn(body, "switch")) {
      const at = `${taskReference}/switch`;
      if (!Array.isArray(body.switch) || body.switch.length === 0) reject(at);
      const seen = new Set<string>();
      const cases: SwitchCase[] = [];
      let fallback: SwitchCase | undefined;
      for (const [slot, entry] of body.switch.entries()) {
        if (!object(entry) || Object.keys(entry).length !== 1) reject(`${at}/${slot}`);
        const [caseName, caseBody] = Object.entries(entry)[0];
        const caseReference = `${at}/${slot}/${pointer(caseName)}`;
        if (caseName.length === 0 || Buffer.byteLength(caseName, "utf8") > 256 || !object(caseBody)) reject(caseReference);
        if (seen.has(caseName)) throw new WorkflowExecutionError("workflow_semantic_error", "duplicate switch case name", caseReference);
        seen.add(caseName);
        if (Object.keys(caseBody).some((field) => field !== "when" && field !== "then")
          || (caseBody.when !== undefined && typeof caseBody.when !== "string")) reject(caseReference);
        const when = caseBody.when === undefined ? undefined : bounded(caseBody.when, `${caseReference}/when`);
        if (when !== undefined && hasExpression(when)) admitSetExpressions(when, `${caseReference}/when`);
        const target = resolve(caseBody.then, `${caseReference}/then`, true);
        const item: SwitchCase = { name: caseName, ...(when === undefined ? {} : { when }), next: target.next, endsWorkflow: target.ends };
        if (when === undefined) {
          if (fallback !== undefined) throw new WorkflowExecutionError("workflow_semantic_error", "switch must have at most one default case", at);
          fallback = item;
        } else cases.push(item);
      }
      operation = { cases, ...(fallback === undefined ? {} : { fallback }) };
    } else if (Object.hasOwn(body, "fork")) {
      const at = `${taskReference}/fork`;
      const fork = body.fork;
      if (!object(fork) || !Array.isArray(fork.branches) || fork.branches.length === 0
        || Object.keys(fork).some((field) => field !== "branches" && field !== "compete")
        || (fork.compete !== undefined && fork.compete !== false)) reject(at);
      operation = {
        branches: fork.branches.map((branch: unknown, slot: number) => {
          // A branch is one named task; its transitions would be outside the proven profile.
          if (object(branch) && Object.values(branch).some((task) => object(task) && Object.hasOwn(task, "then"))) reject(`${at}/branches/${slot}`);
          return compileScope([branch], `${at}/branches`, budget, depth + 1, slot);
        }),
      };
    } else if (Object.hasOwn(body, "try")) {
      const handlerBody = body.catch;
      const at = `${taskReference}/catch`;
      if (!object(handlerBody) || !Object.hasOwn(handlerBody, "do")
        || Object.keys(handlerBody).some((field) => !["errors", "as", "do", "then"].includes(field))
        || (handlerBody.as !== undefined && typeof handlerBody.as !== "string")
        || (handlerBody.then !== undefined && body.then !== undefined)) reject(at);
      const after = handlerBody.then === undefined ? undefined : resolve(handlerBody.then, `${at}/then`);
      operation = {
        attempt: compileScope(body.try, `${taskReference}/try`, budget, depth + 1),
        handler: {
          filter: compileFilter(handlerBody, at),
          children: compileScope(handlerBody.do, `${at}/do`, budget, depth + 1),
          ...(after === undefined ? {} : { next: after.next, endsWorkflow: after.ends }),
        },
      };
    } else {
      if (!object(body.raise) || Object.keys(body.raise).length !== 1 || !object(body.raise.error)) reject(`${taskReference}/raise`);
      operation = { problem: bounded(body.raise.error, `${taskReference}/raise/error`, true) as Record<string, Json> };
    }
    return {
      name, key, reference: taskReference, next, endsWorkflow: ends,
      ...operation,
    };
  }));
}

async function runScope(tasks: readonly Task[], input: Json, budget: { steps: number }): Promise<{ data: Json; ended: boolean }> {
  const builder = new StateGraph<typeof State.spec, typeof State.State, typeof State.Update, string>(State);
  for (const task of tasks) {
    builder.addNode(task.key, async (state) => {
      if (++budget.steps > MAX_STEPS) {
        throw new WorkflowExecutionError("workflow_execution_error", "workflow step limit exceeded", task.reference);
      }
      if (task.problem !== undefined) {
        const message = task.problem.detail || task.problem.title || task.problem.type;
        throw new WorkflowExecutionError("workflow_execution_error", String(message), task.reference, task.problem);
      }
      let next = task.next;
      let endsWorkflow = task.endsWorkflow;
      let result: { data: Json; ended: boolean };
      if (task.attempt !== undefined) {
        try {
          result = await runScope(task.attempt, structuredClone(state.data), budget);
        } catch (error) {
          const handler = task.handler!;
          if (!(error instanceof WorkflowExecutionError) || (error.problem === undefined && error.catchView === undefined) || !catchMatches(handler.filter, error)) throw error;
          result = await runScope(handler.children, structuredClone(state.data), budget);
          if (handler.next !== undefined) {
            next = handler.next;
            endsWorkflow = handler.endsWorkflow === true;
          }
        }
      } else if (task.cases !== undefined || task.fallback !== undefined) {
        const chosen = selectCase(task, state.data);
        if (chosen !== undefined) {
          next = chosen.next;
          endsWorkflow = chosen.endsWorkflow;
        }
        result = { data: structuredClone(state.data), ended: false };
      } else if (task.branches !== undefined) {
        const settled = await Promise.allSettled(task.branches.map((branch) => runScope(branch, structuredClone(state.data), budget)));
        const failed = settled.find((outcome): outcome is PromiseRejectedResult => outcome.status === "rejected");
        if (failed !== undefined) throw failed.reason;
        result = { data: settled.map((outcome) => (outcome as PromiseFulfilledResult<{ data: Json }>).value.data), ended: false };
      } else {
        result = task.children !== undefined
          ? await runScope(task.children, structuredClone(state.data), budget)
          : { data: task.dynamic ? evaluateSet(task, state.data) : structuredClone(task.value!), ended: false };
      }
      const ended = result.ended || endsWorkflow;
      return { data: result.data, ended, next: ended ? END : next };
    });
    builder.addConditionalEdges(task.key, (state) => state.next,
      Object.fromEntries([...tasks.map((item) => item.key), END].map((key) => [key, key])));
  }
  builder.addEdge(START, tasks[0].key);
  const result = await builder.compile().invoke({ data: structuredClone(input), next: END, ended: false },
    { recursionLimit: MAX_STEPS + 2 });
  return { data: result.data, ended: result.ended };
}

const DEFAULT_WORKFLOW = {
  document: { dsl: "1.0.3", namespace: "open-workflow-agent", name: "default-agent", version: "1.0.0" },
  do: [{ respond: { call: "agent:1.0.0@default" } }],
};

export function literalExecutionCapabilities() {
  return {
    engine: "langgraph-js", experimental: true, dsl: "1.0.3",
    tasks: ["set", "do", "raise", "try", "fork", "switch"], transitions: ["continue", "exit", "end", "scope_task_name"],
    expressions: true, expressionScope: ["set"], jq: "1.8.2", catalogCalls: false, nativeExtensions: [],
    persistence: false, recovery: false, publicInvocationIdentity: false,
    maxTasks: MAX_TASKS, maxSteps: MAX_STEPS, maxScopeDepth: MAX_SCOPE_DEPTH, maxValueBytes: MAX_VALUE_BYTES,
  } as const;
}

/** Experimental literal-only execution component; no public plan or durable handle. */
export async function executeLiteralWorkflow(
  source: string | undefined, input: unknown,
): Promise<{ status: "completed"; output: Json }> {
  await loadExpressions();
  const definition = await loadWorkflowDefinition(source ?? JSON.stringify(DEFAULT_WORKFLOW));
  // Admission of the complete definition precedes graph construction and execution.
  if (Object.keys(definition).some((field) => !["document", "do"].includes(field))) reject("/");
  const document = definition.document as Record<string, unknown>;
  const metadata = object(document.metadata) ? document.metadata : undefined;
  const owa = metadata !== undefined && object(metadata.owa) ? metadata.owa : undefined;
  if (owa?.nativeExtensions !== undefined || owa?.nativeExtensionData !== undefined) reject("/document/metadata/owa");
  const tasks = compileScope(definition.do, "/do", { tasks: 0 });
  const selectedInput = bounded(input, "/input");
  try {
    const result = await runScope(tasks, selectedInput, { steps: 0 });
    return { status: "completed", output: structuredClone(result.data) };
  } catch (error) {
    if (error instanceof WorkflowExecutionError) throw error;
    throw new WorkflowExecutionError("workflow_execution_error", "native JavaScript workflow execution failed", "/do");
  }
}
