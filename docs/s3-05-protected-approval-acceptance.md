# S3-05 protected approvals: authored acceptance plan

Status: source authored, unqualified. S3-05 remains unchecked. The PostgreSQL
approval timestamp precision migration is implemented, and the f9 PostgreSQL
protected-approval cases passed. The same checkpoint's Windows timer lane found
a process-recovery fixture race before the durable approval barrier. The local
fixture now allows a five-second ownership-renewal window and waits for natural
database-clock lease expiry. The formerly failing hosted Windows `request-row`
case passed at `8183776`; broader S3-05 qualification remains open. See the
[checkpoint evidence](validation-history/pending-batch-results.md#protected-approval-recovery-lease-boundary-at-f9cb76f).
This finite plan makes no new capability or task-completion claim.

The finite, default-disabled `owa/protected-approval/v1` business profile is
carried in standard `emit.event.with.data.approvalPolicy`. It does not introduce
a workflow task or native metadata extension. Deployment-owned named bearer
profiles bind unique typed principals; caller-selected operator labels confer
no identity. Actor credentials use the protected approval header alongside the
existing server authorization boundary. Secrets remain references and are not
retained in workflow files, policy rows, audits or public errors.

Original admission captures the request event, owner scope, raw invocation input,
definition fingerprint, assignee, allowed reassignment/escalation targets and
absolute RFC 3339 UTC expiry. Optional response schemas use the existing bounded
common schema validation. Literal declarations fail admission when disabled,
unbound or invalid; dynamic event data is validated transactionally at runtime.
It is not advertised as statically classifiable.

Independent policy/index/request evidence and immutable revisioned audits bind
actual actor identity and each full action. Pending assignments change only by
authorized expected-revision compare-and-swap. The first approved/rejected/expired
terminal outcome remains immutable; exact replay verifies actor, operation key,
revision and value. Protected reads cannot trigger the legacy expiry side effect.
Terminal projection and scoped durable inbox publication commit atomically.

The bounded common expiry observer preserves original due times, isolates invalid
candidates without repairing them, and advances a fair keyset cursor so poisoned
pages cannot starve later valid work. At most one bounded wrap occurs per tick;
diagnostics retain fixed categories in a bounded history. It starts and drains
with engine lifecycle, with no post-shutdown store use. Private proof history
requires at most 10,000 indexed identities/requests, 8 MiB aggregate index payload
and 64 KiB per identity. Oversize or corrupt evidence rejects before ownership.

Independent proofs are checked before ordinary ownership acquisition, timer claim
and native replay, including when native dependency metadata omits an admission.
SQL routing/projection columns must match retained request and terminal audits.
Unconsumed protected terminal inbox evidence remains retained without relying on
an outbox or listener pin. Known terminal events replay without a second business
call; absent/corrupt proof cannot downgrade into the legacy approval profile.

Authored inventories, not executed counts: common protected store/API cases 30
across SQLite/PostgreSQL, expiry dispatcher 15, pure recovery/static admission 5,
shared actual native/backend cases 56 and actual producer-stop/process cases 32.
The 88 native/process cases span the two principal SDKs and both databases, with
22 per principal/backend. Producer-stop cases retain approved, rejected and
expired decisions and persist reassignment/escalation before process termination.
After restart they replay the original assignment operation and require an
unchanged revisioned record and audit before proving that only the recovered
assignee can decide. They require original expiry/input, genuine native
checkpoints, actual separate process identities, exactly one observable business
commit and no paid API. Windows and PostgreSQL selectors are added to existing
mandatory jobs; their selectors, outer limits and image dependencies are
retained.

The actual-process recovery worker also replays each approved, rejected and
expired terminal operation after restart using its original actor, operation
key, revision and value. It requires the durable record and complete approval
snapshot (including revisioned audit and inbox evidence) to remain unchanged.
This adds coverage to the existing engine/backend matrix; it is authored and
unqualified until the full batch passes.

Qualification requires a coherent unchanged-source batch, relevant complete suites,
actual principal SQLite/PostgreSQL and process evidence, platform gates, fresh
package/image/security/provenance evidence, API snapshot validation and source-linked
hosted reports. Prior profile successes, source review and authored counts do not
qualify these additions. S3-06 delegated authorization and broader native metadata
features remain separately owned backlog work.
