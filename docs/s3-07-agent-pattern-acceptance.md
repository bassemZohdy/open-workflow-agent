# S3-07 agent pattern scope and required gates

S3-07 remains unchecked. All eight independently authored recipe families and
named deployment role bindings are staged source. Repository Ruff and formatting
passed across 513 files; strict typing passed across 132 source files. All 12
recipe files passed the unmodified bundled official schema (zero schema/lint
errors) and canonical admission with exactly configured role references. These
static checks do not qualify behavioral, native, database or publication gates. The original assistant and
pipeline recipes retain their 1.0.0 contracts; separate named-role recipes add
provider/model, instruction, tool and budget separation. No unlicensed
predecessor implementation or old SDK API is a conformance oracle.

## Execution contract

| Step | Executor | Guard and failure behavior |
| --- | --- | --- |
| Respond, draft, review, research, synthesize, evaluate | Common agent catalog role | Original caller intersected with the role policy and tool allowlist; bounded model/tool rounds and output |
| Route an expert | Standard OWS switch | Validated research/finance enum; no model-supplied target or expression evaluation |
| Refine | Standard OWS for loop | Three attempts, explicit boolean quality and feedback; exhaustion faults |
| Coordinate workers | Standard OWS fork and run.workflow | Two deployment-registered, awaited child workflows; ordered results, original lineage and child budgets |
| Execute a plan | Standard OWS for loop and fixed switch | Strict plan schema, one to three actions, one selected expert per action; no recursion |
| Request and wait for approval | Standard OWS emit/listen with existing protected service | Original request ID, expiry, named assignee and reassignment/escalation targets; zero action calls before approval |

All role calls have a 30-second task timeout, child calls 40 seconds, the approval
listen 90 seconds and each new recipe a 120-second workflow timeout. There are no
blind retries of uncertain effects. A role is a deployment binding, never a new
principal. Model-generated options cannot choose another provider, credential,
role, tool allowlist or budget. Credentials remain named deployment references;
actual selected credential values must stay out of public metadata and archives.

Only exact configured role/version catalog references may pass admission. The
original admitted sanitized role descriptor and current allowlist must agree
before execution/recovery. Default agent and LLM behavior remains available
without named roles. Agent roles cannot delegate child workflows; their child
budget is zero. Coordinator children are explicit OWS calls and retain the
existing root child lifetime, budget and cancellation rules.

## Required validation

- Validate every recipe against the unmodified bundled OWS 1.0.3 schema and the
  canonical capability/expression profile. Check public references and original
  assistant/pipeline compatibility.
- Run identical deterministic role model fixtures on actual ADK and LangGraph
  for all eight families, ordered out-of-order fork completions, original input,
  fixed routes, malformed/oversized plans, exhaustion and zero early approval
  actions. Each child and selected plan action must have the expected cardinality.
- Verify caller/role policy intersection, forbidden model-generated tool/provider
  selectors, role quotas and actual known credential reference redaction on common
  SQLite and actual PostgreSQL. No paid API or universal taint claim is needed.
- Kill a producer and recover through a different actual native process on SQLite
  and actual PostgreSQL. Preserve original input, clock, authority, role binding,
  child lineage and known outcomes; changed role/model/tool bindings fail closed
  before business dispatch or lease mutation.
- Run the full relevant contract/adapter suites, core/CI checks, strict typing,
  formatting, documentation links, fresh builds and required hosted Windows,
  database, security, CTK and image gates before updating completion.

Skipped optional/native/database profiles do not qualify a required gate. A finite
recipe does not claim dynamic team API parity, recursively generated workflows,
provider-specific agent memory behavior, hard subprocess isolation, or any
unimplemented native extension. Acceptance evidence will be added only after the
coherent source batch is frozen and the required validations finish.
