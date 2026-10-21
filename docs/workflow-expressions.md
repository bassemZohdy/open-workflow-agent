# Workflow expression semantics

Core evaluates workflow-owned expressions with [jq.py 1.12.0](https://pypi.org/project/jq/1.12.0/),
whose distributed wheels embed jq 1.8.2. Root, standalone core and all three
adapter locks use that exact binding version. Evaluation stays in core and
does not create an engine-specific execution path. The unchanged OWS 1.0.3
schema and [reviewed semantic profile](workflow-admission.md) remain authoritative.

In default `strict` mode, only `${ ... }` expressions are executable. Bare
`.value`, `$input`, `true` and JSON-looking strings remain strings. A complete
wrapped expression returns its JSON type; expressions embedded in text produce
strings, with objects, arrays, booleans and null formatted as compact JSON.
Template recognition handles nested objects, jq strings/interpolation and
continued comments. jq syntax uses double-quoted strings, not Python quoting.

In `loose` mode, bare strings also attempt jq evaluation. Compilation or
evaluation failures preserve the original string, as required by the pinned
DSL. Non-portable input and unsafe native configuration still fail. The mode
belongs to each invocation, including its nested scopes; concurrent workflows
cannot change one another's evaluator. In either mode, input-derived strings
are never recursively reinterpreted as expression source. Calls and child
workflows with no explicit input definition receive copied data directly.

The [jq manual](https://jqlang.org/manual/v1.8/) defines operators, filters,
functions, variables, arithmetic, selection, reductions and JSON construction.
Conditions use jq truthiness: only false and null are false. A workflow
expression must yield exactly **one** JSON result. Zero or multiple results
raise `expression_error` instead of silently truncating; use an array
constructor (`[.items[]]`) to collect a stream. Runtime data and results must
be finite JSON, with nesting at most 64 levels and integers between
`-(2^53-1)` and `2^53-1`. Non-finite jq output is rejected before the binding
can convert NaN to null or infinity to a finite number. Evaluation copies
inputs and bindings; assignments cannot mutate caller-owned data.

Strict expression slots are compiled during admission, with scoped loop and
catch bindings. Invalid syntax and unknown variables fail before invocation
or effects and retain the task pointer. Literal schemas, metadata and script
source are not expression slots. Sandboxed arguments, stdin and environment
values use the same evaluator and JSON string conversion. Credentials remain
deployment-owned references. The [data pipeline](workflow-data-flow.md)
defines phase-specific input/output/context and runtime/workflow/task
descriptor arguments. Unsupported authorization/secrets arguments fail
compilation until their deployment/security contracts are implemented.

Ambient environment access (`env`, `$ENV`), input streams, diagnostic writes,
process control, module imports/metadata and wall-clock `now` are disabled.
Native diagnostics and their traceback state are discarded before public
errors are created, so expression constants and user data are not error
messages. Native iterators are released before their program on every result
and failure path. The binding automatically loads a home `.jq` location and
cannot disable it through its API; core rejects an existing or inaccessible
location before calling the compiler, using libjq's HOME/Windows fallback
rules without changing process environment variables. Deployment homes must
remain free of ambient `.jq` files/directories.

This is an in-process evaluator for trusted workflow definitions. It does
not enforce a CPU/memory isolation boundary or preempt an unbounded jq
computation; task timeout cancellation cannot interrupt native computation.
The disabled ambient operations are profile restrictions, not container or VM
isolation. Module-enabled jq, alternative languages and broader conformance
remain unsupported. JS implementation and later-stage data-flow/recovery
semantics are not claimed by this milestone.

Evidence: [core jq fixtures](../tests/core/test_expressions.py),
[shared native adapter outcomes](../tests/contract/test_jq_portability.py),
the existing contract suite, the Windows CI expression/admission job and
adapted pinned CTK selection. Native LangGraph prepares its SQLite/WAL store
through its own saver before concurrent first writers; a native barrier test
verifies that this does not serialize workflow execution. Proposed
inventory tests remain separate from executed repository fixtures.
