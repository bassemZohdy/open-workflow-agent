# Workflow control flow

The common Python executor implements scope-aware task routing against the
[pinned OWS reference](https://github.com/open-workflow-specification/specification/blob/2dd2c84170d5f3e05d58e913e9ca298dcf8d543a/dsl-reference.md#flow-directive).
The schema remains unchanged. Task declaration order is the default sequence.
An explicit name must resolve in the task's current scope; missing names and
cross-scope jumps fail admission before effects. Backward jumps are supported.
Cancellation remains cooperative; jq itself retains its documented
[in-process limits](workflow-expressions.md).

`continue` advances in the current sequence. `exit` finishes that sequence
and returns to its parent. In a loop body it finishes that iteration; later
iterations can still run. At the root it completes the workflow. `end`
concludes the entire invocation: later tasks/iterations stop, active fork
siblings are cancelled and awaited, and the finishing branch and its ancestors
complete. This is graceful workflow completion rather than invocation
cancellation. Cancelled sibling tasks emit `TaskCancelled` with reason
`workflow_end`; the workflow emits `WorkflowCompleted`.

Switches evaluate predicates in declaration order and use the first match.
An unconditional case is a default, independent of its position; it is used
only if no predicate matches. Multiple defaults and duplicate case names
are rejected. A selected case's directive overrides the switch task's `then`,
including an explicit `continue`. An explicit catch-path directive also takes
precedence over the try task's normal completion directive.

Loops evaluate their collection once. It must be an array, including an
empty array; strings, objects, numbers, booleans and null fail with the common
validation contract before body effects. Item and index variables are local
to the iteration. Each body consumes the preceding iteration's output and
carries successful context exports forward. `while` is checked before each
iteration against the loop task's transformed input, with current context
and item/index bindings;
false/null stops iteration. With no completed iteration the original loop
input passes through; otherwise the last iteration's output is the raw loop
output. Existing jq truthiness applies to task and switch conditions.

Task descriptors, lifecycle events, errors and operation ids use original
definition JSON pointers. Names escape `~` and `/` per JSON Pointer. Fork
branches use `/fork/branches/<index>/<name>` without a synthetic sequence
index. The corrected fork/name references can change operation ids relative
to the earlier bridge. Compatibility for upgrading historical in-flight
records belongs to S2-09 and is not qualified by this milestone.

The optional Agent Framework bridge and LangGraph Functional API compatibility
helper reserve native
`None` as a control/no-input signal. They encode public JSON null using a
private bytes marker and decode it before common execution. That marker
cannot be admitted JSON input; direct attempts to pass it fail core
validation. Existing non-null Functional API checkpoint input shapes are retained.
Engine invocations now use [native task graphs](native-task-execution.md),
where JSON null is ordinary snapshot data. Native persistence/control objects
remain private.

Evidence is in [shared scope/flow fixtures](../tests/contract/test_scoped_flow.py),
the existing lifecycle suite and the adapted CTK flow/switch/loop selection.
Ordered fork aggregation, reducers, durable competitive winners, child
workflow recursion/background operation and full recovery/migration remain
separate later-stage work. Native LangGraph task lowering is recorded
separately in the task-execution acceptance record. JS and full conformance are not
claimed.
