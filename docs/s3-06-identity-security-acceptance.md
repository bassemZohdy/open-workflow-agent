# S3-06 identity and security acceptance

Status: authored; behavioral, native, platform, database and publication
qualification pending. New descriptor regressions reject unpaired Unicode in
principal identity/role/scope/audience and named security-profile references as
bounded-reference `ValueError`s; they await the final batch.
Security profile names now use the same 256-byte bounded-reference rule and
reject DEL and unpaired-surrogate values before a profile can be selected. The
focused configuration regressions are authored and unrun; full S3-06
qualification remains pending.
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

| Threat | Required control | Evidence still required |
| --- | --- | --- |
| S306-01 spoofing | Parse bearer credentials with a bounded exact parser; reject malformed and duplicate headers before storage, lease, native or business mutation. | Actual malformed/duplicate/Unicode ingress cases and unchanged-state witnesses on required platforms. |
| S306-02 authority loss | Preserve immutable private original authority; verify independent policy evidence before action and recovery; never grant roles from spoofed labels. | Cross-engine/backend propagation through tools, children, schedules and resume, including forged-label rejection. |
| S306-03 foreign object access | Check invocation ownership before schedule reads or cancellation; return the same sanitized result for foreign and missing IDs. | Actual authenticated foreign/missing-object reads and cancellation with no mutation. |
| S306-04 privilege escalation | Keep the deployment profile disabled by default; require exact decisions before model, tool, secret resolution and administrative mutation; fail closed on unknown authority. | Activation, denial, revocation and stale-policy recovery witnesses with no unauthorized side effect. |
| S306-05 context leakage | Expose narrow immutable catalog metadata and task-local scopes with reset; isolate principals even when correlation IDs match. | Concurrent same-correlation principals, protected-memory isolation, and cleanup after errors/cancellation. |
| S306-06 credential disclosure | Use an ephemeral credential-aware common response boundary and suppress sensitive sandbox output where configured. Literal redaction does not claim general information-flow or encoded-secret detection. | Actual canary echoes across journals, native state, model, logs and public projections, plus deployment acceptance. |
| S306-07 repudiation or replay | Retain bounded transactional audit/policy evidence and prevent uncertain-action resend. | Independent corruption, rollback, stale-owner and revocation witnesses, including recovery after process stop. |

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
