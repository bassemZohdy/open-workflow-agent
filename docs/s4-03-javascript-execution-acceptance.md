# S4-03 experimental JavaScript literal execution

The frozen `5310f5a` CI run passes the JavaScript package job and the required
13-case comparison across actual ADK, LangGraph Python and Node. This verifies
the literal component's named outcome projection only. Full engine identity,
durability, expressions, calls and the broader S4-03 scope remain open.

Current integration: see the [main checkpoint](validation-history/main-integration-2026-10-25.md)
for current results, the final hosted-fixture repair and remaining gates.
The full task stays open. Earlier counts and pending statements below are
retained development history, not additional current acceptance claims.

Status: authored and locally tested; S4-03 remains open. This is an execution
component in the isolated private Node package, not a production engine adapter
or a public invocation API. The official OWS 1.0.3 schema and its pin are unchanged.

`executeLiteralWorkflow` loads a complete canonical definition, admits every
task, freezes a typed private program and builds actual LangGraph nodes. Its
finite scope is literal `set`, nested `do`, and standard `continue`, scope-local
named jumps, local `exit` and whole-workflow `end`. All operations remain in
native graphs. The component performs no executable, catalog, provider or
protocol operation and creates no competing sandbox path. Missing workflow
source generates the canonical one-task call to `agent:1.0.0@default`; admission
rejects it because catalog calls are not implemented by this profile.

Admission rejects expressions, transforms, schemas, other task types and native
extension declarations before graph construction. Invalid/duplicate names and
foreign transition targets reject. Runtime JSON is limited to 1 MiB, depth 64
and safe JavaScript integers; host objects, accessors, sparse arrays, non-finite
values and symbol properties reject. Task names are bounded to 256 UTF-8 bytes,
diagnostic references to 8 KiB, total tasks to 256, nested scope depth to eight,
and shared execution steps to 512. SDK exceptions use a fixed sanitized common
error; no native graph/program object is returned. The result contains only
status and output. No plan wire format is introduced.

`literalExecutionCapabilities()` explicitly reports experimental status and
the absence of public invocation identity, persistence, recovery, expressions,
calls and native extensions. Python's public invocation/fingerprint contract is
not implemented by this component. General JS workflow compilation/execution,
durability, common services, jq semantics, identity and outcome parity remain
required before it can become an engine adapter. Shared fixture overlap does not
promote any full proposed criterion or schema-constraint row.

Nine exact fixtures are consumed by Node and the Python adapter contract suite:
literal object/string data, sequence, named jump, continue, nested sequence,
nested exit, nested end and prototype-shaped task/data names. Windows Node
24.16.0 with the pinned npm 12.0.2 and LangGraph JS 1.4.21 passes 43 package tests,
including whole-definition rejection before native graph construction, bounded
loops/values/tasks/depth and non-execution of host accessors. The production npm
dependency audit reports zero vulnerabilities. Actual independent ADK and
LangGraph Windows SDKs each pass all nine shared cases (eight-case selection
plus the final prototype case); root fallback checks remain separate evidence.

The optional `Dockerfile.tests` installs pinned dependencies and compiles at
build time. CI runs it as an arbitrary UID with read-only root, no network and
finite memory/CPU/PID controls. It is not a production image or release target.
The frozen `b9b72ee20d7184a91fe7f92860f14048707df80c` test image passes all
43 cases on Linux under those controls, with no skips. Its source input manifest
contains 1,151 files. Coherent hosted qualification of this component remains
pending. Local reports are retained under `tmp/javascript-sandbox-20261025/`.

## Literal structured faults candidate — 2026-10-25

The next candidate admits literal inline `raise.error` objects. Actual native
nodes throw the common `workflow_execution_error` with the Python contract's
detail/title/type message precedence and canonical JSON Pointer `instance`.
Nested scopes propagate the same public error. The immutable private program
and error projections contain no SDK object; a caller can mutate its returned
projection without changing the original admitted error. Definition-wide
admission still precedes native construction, including unreachable later tasks.
Named error references and any expressions remain unsupported.

Four exact fault fixtures now join the nine success fixtures. Windows Node
passes all 48 package tests; each independent actual Python SDK passes all
13 shared cases. The initial two Node failures exposed invalid fixture
assumptions: a URI was used where the schema describes a JSON Pointer, and a
named error reference was placed outside its required `raise.error` envelope.
The fixtures now use their valid official forms; the schema and validators are
unchanged. Python's annotation-only default accepts the former URI format,
while the pinned JS validator rejects it. This known format-validation
difference remains an S4-04 parity gap, with no full criterion promoted.
Current-source Docker and coherent hosted qualification remain required.

## Literal `try`/`catch` (local development result)

The Node component now executes `try` with a literal `catch`. The profile was
derived from actual ADK and LangGraph Python behavior on the same inputs, which
agreed on every probe, and 16 new shared fixtures (13 success, 3 fault; 29 in
total with the earlier 13) are consumed byte-for-byte by Node and both Python
engines.

Supported: `catch.errors.with` filters over `type`, `status`, `instance`, `title`
and `detail` (all must match by equality; `title` falls back to the error code and
`detail` to the message, as in Python), a required `catch.do`, `catch.then`
(`continue`, `exit`, `end` or a scope-local name), nested `try` and a handler
fault propagating with its own instance. Unmatched errors propagate unchanged.

Rejected before any native graph is constructed: `catch.when`, `catch.exceptWhen`,
`catch.retry`, a `catch` without `do` (the partially updated data it would expose
is not matched), an empty `catch`, `catch.then` together with a task-level `then`
(precedence not proven), unknown filter keys or wrongly typed filter values, and
expressions. The official schema separately rejects a `try` without `catch` and
malformed `as`/`with` values. Only errors raised by literal `raise` are catchable;
the step-limit bound is deliberately not. Because expressions are unsupported,
the data a handler would see after a partial failure is not observable here.

Local evidence: Node 66/66 passes; the shared-fixture contract selection passes on
ADK and LangGraph with the literal-parity checks (297 passed in the combined CI
selection). This is not hosted qualification, does not promote any proposed
criterion or schema-constraint row, and S4-03 remains open.

## Literal non-competing `fork` (local development result)

`fork` with literal branches executes in Node. Behavior was probed on actual ADK
and LangGraph Python, which agreed on every case, and nine more shared fixtures
(eight success, one fault; 38 in total) are consumed identically by Node and both
Python engines.

Supported: `fork.branches` where each branch is one named task (`set`, `do`,
`raise`, `try` or a nested `fork`), optionally with an explicit `compete: false`.
The output is the array of branch outputs in branch order; a `do` branch yields its
last output. Each branch receives the fork task's input, runs as its own native
graph, and a branch fault keeps the branch task's own instance
(for example `/do/0/par/fork/branches/1/b`) and is catchable by an enclosing `try`.
The shared step and task bounds apply across all branches.

Rejected before any native graph is constructed: `compete: true` (the winner is
timing-dependent), any other `fork` field, a `then` on a branch task (branch-local
transitions are not proven), non-literal values and unsupported task types.
When several branches fault, the one reported is the first in branch order; Python's
choice for that case is not probed, so it is not claimed as matching.

Local evidence: Node 76/76 passes; the shared-fixture contract and CI selection
passes on ADK and LangGraph (315 passed). This is not hosted qualification and
promotes no proposed-test or schema-constraint row; S4-03 remains open.

## jq expressions in `set` values (local development result)

The Node component evaluates `${ }` runtime expressions in `set` values with jq 1.8.2
(`jq-wasm` 3.0.0-jq-1.8.2, pinned exactly; the owner approved this dependency on
2026-11-01). The port in `engines/langgraph-js/src/expressions.ts` mirrors core's
evaluator: identical prelude and postlude text (so `$__loc__` line numbers match),
`env`, `input`, `debug`, `halt`, `now` and `$ENV` disabled, modules rejected,
exactly one JSON result, finite output, safe integers and bounded depth, value-only
evaluation (keys stay literal), whole-value expressions keeping their JSON type and
mixed text interpolating compact JSON.

Behavior was probed on actual ADK and LangGraph Python, which agreed on every case,
and 26 more shared fixtures (15 success, 11 fault; 64 in total) are consumed
identically by Node and both Python engines. Expression failures use the common
`expression_error` code with `{instance}` details at run time. They are catchable
like Python's: a `catch` filter sees `type` `.../errors/expression`, `status` 400 and
`title` `expression_error`. Admission errors (invalid jq, undefined variables,
unterminated expressions, `$ENV`, modules) are raised before any graph is built with
`{reference}` details; Python raises the same messages from `compile_workflow`, which
the shared fault harness cannot express, so they are covered by Node tests that
quote core's values.

Not supported and rejected as outside the profile: variables core binds from
workflow state (`$workflow`, `$runtime`, `$context`, `$event`, `$input`, `$output`,
`$secrets` and similar), and expressions anywhere except `set` values (`raise`,
`catch` filters and transitions stay literal). `for`, `switch` and conditions are
still unimplemented.

Known limits: jq has no time or memory limit here, so a pathological expression such
as unbounded recursion blocks the Node event loop; results are limited to the shared
1 MiB value bound. The Node check of the bundled jq version guards dependency drift.

Local evidence: Node 105/105 passes; the shared-fixture contract and CI selection
passes on ADK and LangGraph (367 passed); `npm audit --omit=dev` reports no
vulnerabilities. This is not hosted qualification and promotes no proposed-test or
schema-constraint row; S4-03 remains open.

## `switch` (local development result)

`switch` executes in Node with jq `when` conditions. Behavior was probed on actual ADK
and LangGraph Python, which agreed on every case, and 23 more shared fixtures (19
success, 4 fault; 87 in total) are consumed identically by Node and both Python engines.

Semantics, as observed: `when` cases are evaluated in listed order and the first with a
jq-truthy result (anything except `null` and `false`, so `0` and a literal string such
as `"false"` are true) is selected; later `when` expressions are never evaluated. The
default case (no `when`) is taken only if no `when` case matches, wherever it is listed.
The data is unchanged. The selected case's `then` applies: a named task, `exit`, `end`,
or `continue` meaning the natural next task. The task-level `then` applies only when no
case is selected, and an explicit `continue` on a case or default does not use it. A
`when` failure is an `expression_error` at the switch task and is catchable.

Admission errors are raised before any graph is built with the same codes, messages
and details as core: a duplicate case name, an unknown target (with `target` detail),
more than one default, and invalid or denied `when` expressions (reference
`.../switch/N/case/when`). Rejected as outside the profile: workflow-state variables in
`when`, as for `set`. Backward jumps work and are bounded by the shared step budget.

Local evidence: Node 129/129 passes; the shared-fixture contract and CI selection passes
on ADK and LangGraph. This is not hosted qualification and promotes no proposed-test or
schema-constraint row; S4-03 remains open (`for`, calls, durability and public identity
are still unimplemented).

## `for` loops (local development result)

`for` executes in Node with jq `in` and `while` expressions and per-iteration variable
bindings. Behavior was probed on actual ADK and LangGraph Python, which agreed on every
case, and 24 more shared fixtures (16 success, 8 fault; 111 in total) are consumed
identically by Node and both Python engines.

Semantics, as observed: `in` must evaluate to an array (an object, number, null or
string, including literal text such as `"[1,2]"`, is a `workflow_semantic_error`
"for.in must evaluate to an array" at the loop task). The data threads through the
iterations and the result is the last iteration's data; an empty array leaves the data
unchanged. The item and index variables default to `$item` and `$index`, can be renamed
with `each` and `at`, exist only inside `while` and the body, shadow outer loops of the
same name, and are not visible to `in` or after the loop. `then: exit` in the body ends
only that iteration, a named jump is local to the body and restarts each iteration, and
`then: end` ends the whole workflow. Body failures keep the body task's instance and
are catchable.

`while` is a guard evaluated before each iteration with the loop variables bound. Its
`.` is the data the loop task received and does not change between iterations, so a
condition such as `.n < 2` over data the body updates never stops the loop. This is
observed core behavior, probably not the specification's intent; Node reproduces it so
the engines agree, and it is recorded as a fixture
(`for-while-sees-the-data-the-loop-received`) so neither side can drift silently.

Admission errors carry core's codes, messages and references: invalid or reserved
binding names (`ENV`, non-identifiers) at `.../for/each` or `.../for/at`, equal item and
index names at the loop task, and invalid `in`, `while` and body expressions at
`.../for/in`, `.../while` and `.../set`. Not probed, so not claimed: the catch-filter
values (`type`, `status`) that the "for.in must evaluate to an array" error presents,
and loops combined with `fork` or `try` beyond the fixtures. Workflow-state variables
remain rejected as outside the profile.

Local evidence: Node 155/155 passes; the shared-fixture contract and CI selection passes
on ADK and LangGraph. This is not hosted qualification and promotes no proposed-test or
schema-constraint row; S4-03 remains open (calls, durability and public identity are
still unimplemented).

