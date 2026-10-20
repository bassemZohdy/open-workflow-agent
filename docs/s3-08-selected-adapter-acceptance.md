# S3-08 selected ADK patterns and required gates

S3-08 remains unchecked. This source batch independently adapts selected
compiler, provider, memory and broker behavior descriptions from the frozen
Apache-2.0 ADK reference at
`48db0cf5e1a1d3202cbbec6021736e1d89e94cd0`. The preserved reference and its
license/provenance remain under `migration/reference/`; they are not runtime code
or a conformance oracle. No unlicensed generic-agent source is copied.

## Finite selected scope

| Area | Adaptation | Limits and remaining gates |
| --- | --- | --- |
| Compiler | Original typed plan, scopes/references/transforms and actual adapter-native orchestration | Ordinary descriptive ADK/plugin metadata stays inert; executable capability gates remain. Required versioned native metadata belongs to S4-01. |
| Provider | Existing common Model SPI, fake/LiteLLM, finite neutral tool requests and optional original bounds | Text/null compatibility, strict object arguments, bounded deadline/data/tool count, no transparent provider retry. Unknown completion preserves its pending outer intent. Real vendor/delegation qualification is not inferred. |
| Memory | Optional common-connection bounded principal memory | Quotas and live original owner share the mutation transaction; SQL bounds precede ranking. Legacy storage stays unchanged. No implicit migration or per-tool exactly-once claim. |
| Broker | Bounded CloudEvent codec and explicitly selected embedded ingress | Exact payload/null/extensions, independent receipt in the inbox transaction, ACK after physical proof. Remote brokers and stronger transport-selection recovery profile remain S4-10 gates. |

`model.bounds` is optional; absent bounds preserve prior serialized declarations,
including nested role descriptors. Configured bounds must apply to the selected
fake/injected model as well as the LiteLLM adapter. The original deadline includes
preparation, permission and credential work; caller cancellation propagates. A
known local failure differs from an unknown provider send outcome. Only strict
finite tool DTOs may reach existing tool loops; direct LLM calls return tool
requests without executing them. Literal echoes from actual selected credential
references remain guarded, with no universal taint claim.

`memory.bounds` is optional and disabled by default. Its enabled profile uses the
actual common invocation connection, original authenticated principal namespace,
current running snapshot and live database lease. Conflicting custom memory
storage is rejected. Text, metadata, queries, rows, results and namespace bytes
have finite bounds. Optional retention records absolute expiry only when explicitly
configured; search stays read-only and private authorized `memory.prune` advances
one due page without refreshing expiry or inventing a replay identity. Default
retention never evicts operator data. Known enclosing agent receipts do not repeat
memory changes; an uncertain enclosing action must remain unresolved.

The new embedded ingress is a deployment composition choice. Its finite profile
requires an explicit event time so duplicate admission cannot regenerate a
clock; the general codec still preserves an absent optional time. Scope comes
from trusted current authority, never user role/scope/correlation labels. The
independent admission receipt contains bounded identities and digests, not another
copy of the body or credentials. Admission failure gives zero ACK. This receipt
alone does not pin a new transport dependency in an existing native listen
selection; native proof corruption protection requires a versioned atomic
selection profile and is not advertised by this adaptation.

## Required validation

Run one coherent frozen-source batch with no paid APIs: strict typing/lint/format,
public schemas/capability admission, documentation/provenance and fresh builds;
common core/CI suites; identical actual ADK/LangGraph fixtures on SQLite and actual
PostgreSQL; separate OS-stopped producer/recovery processes with original inputs,
clocks, authority and known/uncertain outcomes. Provider tests must cover finite
DTOs, selected secret permissions/echoes, preparation versus actual-send timeout,
cancellation, opaque model injection and no retry of an unknown completion.
Memory tests must include two real connections racing the last quota slot,
namespace isolation, database-clock expiry/takeover, cancellation/rollback,
corruption and bounded multi-page explicit retention. Broker tests must include
exact envelope round trips, committed receipt re-read, duplicate/mismatched input,
ACK failure/rollback and known inbox/listen/model/outbox process boundaries.
The newly authored conflicting-duplicate case uses actual native engine fixtures
and both database selections; it rejects changed data for an already committed
event identity, proves the first envelope remains unchanged, then retries the
original event and requires the same admission receipt and valid selection proof.
It is unrun and does not qualify the broker profile.

Keep all earlier source/identity/approval/sandbox/recovery gates. Required hosted
Windows, actual database, security, CTK and image gates still apply before task
completion. Skipped database/native profiles or fake peers do not qualify remote
vendor, Redis/Kafka/RabbitMQ/NATS or independent deployment acceptance. Source
changes and targeted passes advance no inventory completion row.
