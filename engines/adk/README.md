# open-workflow-agent-adk

Google ADK engine adapter for the Open Workflow Agent. Wraps the shared immutable workflow plan in an ADK-native Runner/FunctionNode execution envelope, with ADK-native tool bindings and persistence while keeping public identity, capability reporting, and observable behavior identical to the common contract. Task-level plan-to-ADK compilation is not yet claimed.

See the [repository root](https://github.com/BassemZohdy/open-workflow-agent) for documentation.

## Resume semantics

The current ADK envelope uses a dynamic `FunctionNode` with
`rerun_on_resume=True`. A process restart therefore replays the common plan
from its durable public invocation metadata; it does not claim task-boundary
continuation without replay. The common executor derives each side-effect
operation id from the invocation id and canonical task reference and passes it
as `Idempotency-Key`/`X-OWA-Operation-ID`. External side-effecting callers must
deduplicate that key. The ADK native and operator-matrix tests verify stable
keys across interruption, retry, timeout, and restart/resume paths.
