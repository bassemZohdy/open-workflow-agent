import { Annotation, END, START, StateGraph } from "@langchain/langgraph";

import { loadWorkflowDefinition } from "./workflow-definition.js";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Task = Readonly<{
  reference: string;
  key: string;
  name: string;
  next: string;
  endsWorkflow: boolean;
  value?: Json;
  children?: readonly Task[];
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
    readonly code: "unsupported_workflow_feature" | "workflow_semantic_error" | "workflow_execution_error",
    message: string,
    readonly reference: string,
  ) {
    super(message);
    this.name = "WorkflowExecutionError";
    if (Buffer.byteLength(reference, "utf8") > 8192) this.reference = "/";
  }

  asDict() {
    return { code: this.code, message: this.message, details: { reference: this.reference } };
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

function compileScope(value: unknown, reference: string, budget: { tasks: number }, depth = 0): readonly Task[] {
  if (!Array.isArray(value) || value.length === 0 || depth > MAX_SCOPE_DEPTH) reject(reference);
  const entries: { name: string; body: Record<string, unknown>; reference: string; key: string }[] = [];
  const names = new Map<string, string>();
  for (const [index, entry] of value.entries()) {
    if (!object(entry) || Object.keys(entry).length !== 1) reject(reference);
    const [name, body] = Object.entries(entry)[0];
    if (name.length === 0 || Buffer.byteLength(name, "utf8") > 256) reject(`${reference}/${index}`);
    const taskReference = `${reference}/${index}/${pointer(name)}`;
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
    if (Object.keys(body).some((field) => !["set", "do", "then", "metadata"].includes(field))) reject(taskReference);
    if (Object.hasOwn(body, "set") === Object.hasOwn(body, "do")) reject(taskReference);
    const transition = body.then ?? "continue";
    if (typeof transition !== "string") reject(`${taskReference}/then`);
    let next = entries[index + 1]?.key ?? END;
    if (transition === "exit" || transition === "end") next = END;
    else if (transition !== "continue") {
      const target = names.get(transition);
      if (target === undefined) {
        throw new WorkflowExecutionError("workflow_semantic_error", "transition target must belong to the current scope", `${taskReference}/then`);
      }
      next = target;
    }
    return {
      name, key, reference: taskReference, next, endsWorkflow: transition === "end",
      ...(Object.hasOwn(body, "set") ? { value: bounded(body.set, `${taskReference}/set`, true) }
        : { children: compileScope(body.do, `${taskReference}/do`, budget, depth + 1) }),
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
      const result = task.children !== undefined
        ? await runScope(task.children, structuredClone(state.data), budget)
        : { data: structuredClone(task.value!), ended: false };
      const ended = result.ended || task.endsWorkflow;
      return { data: result.data, ended, next: ended ? END : task.next };
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
    tasks: ["set", "do"], transitions: ["continue", "exit", "end", "scope_task_name"],
    expressions: false, catalogCalls: false, nativeExtensions: [],
    persistence: false, recovery: false, publicInvocationIdentity: false,
    maxTasks: MAX_TASKS, maxSteps: MAX_STEPS, maxScopeDepth: MAX_SCOPE_DEPTH, maxValueBytes: MAX_VALUE_BYTES,
  } as const;
}

/** Experimental literal-only execution component; no public plan or durable handle. */
export async function executeLiteralWorkflow(
  source: string | undefined, input: unknown,
): Promise<{ status: "completed"; output: Json }> {
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
