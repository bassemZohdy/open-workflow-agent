/** Development-only native outcome capture; no public engine or plan API. */
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import { executeLiteralWorkflow, WorkflowExecutionError } from "../src/workflow-execution.js";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const fixtures = ["tests/fixtures/javascript-literal-execution.json", "tests/fixtures/javascript-literal-faults.json"];
const schema = "resources/open-workflow/1.0.3/workflow.yaml";
const hash = (value: Uint8Array) => createHash("sha256").update(value).digest("hex");

async function main() {
  const { values } = parseArgs({ options: { "source-commit": { type: "string" }, output: { type: "string" } }, strict: true });
  if (values["source-commit"] === undefined || !/^[0-9a-f]{40}$/.test(values["source-commit"]) || values.output === undefined) throw new Error("invalid capture selection");
  const hashes: Record<string, string> = {};
  const records: Record<string, unknown>[] = [];
  for (const [index, filename] of fixtures.entries()) {
    const bytes = await readFile(resolve(root, filename));
    hashes[filename] = hash(bytes);
    const cases = JSON.parse(bytes.toString("utf8")) as { id: string; tasks: unknown[]; input?: unknown }[];
    for (const fixture of cases) {
      const source = JSON.stringify({ document: { dsl: "1.0.3", namespace: "tests", name: fixture.id, version: "1.0.0" }, do: fixture.tasks });
      try {
        const result = await executeLiteralWorkflow(source, index === 0 ? fixture.input : { private: "not-error-data" });
        records.push({ id: fixture.id, status: result.status, output: result.output });
      } catch (error) {
        if (!(error instanceof WorkflowExecutionError)) throw new Error("native literal capture failed");
        records.push({ id: fixture.id, status: "faulted", error: error.asDict() });
      }
    }
  }
  const sdk = JSON.parse(await readFile(new URL("../../node_modules/@langchain/langgraph/package.json", import.meta.url), "utf8")) as { version: string };
  const report = {
    format: "owa/literal-outcomes/v1", source_commit: values["source-commit"],
    engine: "langgraph-js", sdk_version: sdk.version, runtime_version: process.versions.node,
    native: true, fixture_sha256: hashes, schema_sha256: hash(await readFile(resolve(root, schema))),
    records: records.sort((left, right) => String(left.id) < String(right.id) ? -1 : String(left.id) > String(right.id) ? 1 : 0),
  };
  const payload = JSON.stringify(report, null, 2) + "\n";
  if (Buffer.byteLength(payload, "utf8") > 4_194_304) throw new Error("native literal capture exceeded its bound");
  await mkdir(dirname(values.output), { recursive: true });
  await writeFile(values.output, payload, "utf8");
}

main().catch(() => { process.stderr.write("literal outcome evidence rejected\n"); process.exitCode = 2; });
