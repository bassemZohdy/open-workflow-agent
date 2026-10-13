# S3-06 identity and security acceptance

Status: authored; behavioral, native, platform, database and publication qualification pending.
Project Definition sections 11 and 22–25 govern this work. TODO.md retains the
unchecked task. Source-only audits identify gaps; they do not establish behavioral
failure or delivery. The preceding combined batch at `ebc2694` is tested separately.

## Scope and trust boundaries

Authenticated deployment identity must remain distinct from caller correlation
labels, user_id, session_id, Agent Card descriptions, model output and workflow
input. Original principal claims and action policy must reach common runtime,
agent tools, workflow calls, children, schedules and recovery without credentials
in durable state. Existing protocol grants, original admission evidence, native
checkpoint ownership, protected approvals and sandbox resource controls remain
required.

Internal subprocess controls remain a controlled execution boundary. Scoped Python
catalog services are a programming boundary for trusted managed code. Neither is
hard isolation. Enterprise federation, delegated-user token exchange, consent and
cross-domain trust remain conditional S4-11 work; this task invents no identity
protocol and modifies no public OWS schema.

## Review baseline and evidence

Protected assets are deployment credentials, original authenticated authority,
workflow inputs and results, scoped memory, and durable decision evidence. The
selected implementation baseline is the repository contract at `ebc2694` and
public OWS 1.0.3; this record performs no external compliance assessment. Controls
below have source-review evidence and planned acceptance, not new passing results.
No human security acceptance is inferred from authoring or the implementation request.

Source findings and failed prior-batch results are preserved in
[the pending batch history](validation-history/pending-batch-results.md).
Agents own common authority evidence, API/credential boundaries, and runtime/native
propagation respectively; the coordinated batch must qualify their interactions.
Residual limits include trusted host storage and managed Python code, literal-only
credential detection, actual platform sandbox controls, and conditional delegated
identity. Existing independent admission and original-profile checks remain required.

## Concrete threats and proposed controls

| Threat | Observed source gap | Control and required evidence |
| --- | --- | --- |
| S306-01 spoofing | Inbound bearer parsing accepts malformed shapes or raises on Unicode. | One bounded exact parser; malformed and duplicate headers reject before storage, lease, native or business mutation. |
| S306-02 authority loss | Ingress reduces authenticated claims to a principal label. | Immutable private original authority; independent policy evidence verified before action and recovery; spoofed labels cannot grant roles. |
| S306-03 foreign object access | Schedule read/cancel lacks the invocation ownership lookup. | Owner check with identical sanitized foreign/missing result, before read or cancellation. |
| S306-04 privilege escalation | Tools and administrative calls lack common action/resource policy. | Default-disabled deployment profile; exact decisions precede model, tool, secret resolution and administrative mutation; activated unknown authority fails closed. |
| S306-05 context leakage | Catalog metadata exposes private callbacks; tool/memory context is global or absent. | Narrow immutable metadata and task-local scopes with reset; actual concurrent same-correlation principals cannot share authority or protected memory. |
| S306-06 credential disclosure | HTTP errors/headers and approved sandbox secret stdout can reach durable outputs. | Ephemeral credential-aware common response boundary; actual canary echo tests across journals, native state, model, logs and public projections. Literal redaction does not imply general information-flow or encoded-secret detection. |
| S306-07 repudiation or replay | Lifecycle evidence lacks authenticated action decision provenance. | Bounded transactional audit/policy evidence; independent corruption, rollback, stale-owner and revocation witnesses; no uncertain-action resend. |

## Authored fixture inventory

AST-only authoring counts: 207 new core nodes (125 authority/proof, 68 API/credential/
metadata and 14 memory/context), plus 80 shared native/process nodes: 13 shared and
seven actual process cases per ADK/LangGraph and SQLite/PostgreSQL combination.
These are unrun authoring counts, not collected or passed tests. Existing regressions
remain required alongside this finite grid.

ADK now derives a private SDK user key from original verified principal identity and
public user correlation, preserving public fields and anonymous legacy keys. The
namespace revision is pinned for recovery. Scripts use controlled UTF-8 standard
streams. Docker projects against its already resolved credentials. Kubernetes
requests containing Pod Secret references suppress stdout/stderr because the runtime
has no access to those Secret values; known exit status and duration remain intact.
This adds no Secret-read RBAC or hard-isolation claim. Changed backend behavior
requires its applicable deployment acceptance before qualification.

## Required qualification

Author a coherent source batch before behavior testing. Use deterministic providers
and local non-deduplicating peers, with no paid APIs. Exercise actual shared ADK and
LangGraph fixtures on SQLite and PostgreSQL, plus distinct producer/recovery
processes and original admitted clocks, inputs, authority and operation keys.

Acceptance includes strict API/A2A authentication, exact roles/scopes/audience and
resource decisions, administrative and schedule ownership, child/schedule/resume
propagation, task-local cleanup after errors/cancellation, isolated memory, bounded
independent audit proof, deployment changes, direct credential echoes, and known
versus uncertain outcomes. Retain all preceding version, lifecycle, SDK, source,
background and approval regressions. Build/provenance, type/lint/documentation,
required hosted platform/database checks and exact-source evidence remain gates.

Full inventory and delegated-authorization rows remain individually pending.
Partial tests, source authoring and existing non-echoing secret tests do not qualify
this profile. No blocker or task deferral has been established.
