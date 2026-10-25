# S4-03 experimental JavaScript literal execution

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
Linux/container and coherent hosted qualification of this component remain
pending. Local reports are retained under `tmp/javascript-sandbox-20261025/`.
