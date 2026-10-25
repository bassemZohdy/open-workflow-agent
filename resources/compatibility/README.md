# Full compatibility tracker

[feature-tracker.json](feature-tracker.json) retains the reviewed inventory: 575 OWS-to-LangGraph rows plus 83 reverse rows (658 total), 443 proposed tests, 774 schema constraints, 83 extension decisions and 52 configuration entries. Each feature links to pending tasks in [TODO.md](../../TODO.md).

The inventory records source analysis and proposed translations, not completed implementation or validated full conformance. Python and JS status are independent. Original research fields remain intact; `repository_tracking` records the original 2026-10-11 baseline mapping and explicitly does not certify implementation. Its task rows and each feature's `todo_ids`, `repository_status` and `evidence_of_completion` record later repository delivery separately. Revalidate upstream versions before implementation; this inventory is dated 2026-10-11 and covers more than the current shipped profile.

Effort S/M/L is relative work size, not a calendar promise. Do not delete a row merely because it is deferred or cannot have identical framework internals. Record the limitation, any business-behavior representation, extension boundary, and evidence. Native API existence does not prove semantic compatibility. Never promise arbitrary host-code/object serialization or exact distributed side-effect execution through YAML alone.

[implementation-audit.json](implementation-audit.json) records current active
behavior separately for all 658 IDs, with qualified source/test evidence groups
and remaining repair owners. [The audit report](../../docs/current-implementation-audit.md)
explains partial, rejected, inert, mismatched and private native outcomes.
`current_implementation_audit` links each delivery row to that evidence;
original research fields and all full proposed acceptance criteria remain
unchanged. A qualified subset is not a full-feature or conformance claim.

[coverage-bridge.json](coverage-bridge.json) gives every proposed-test ID and
every extracted schema-constraint row a stable inventory entry. Its current
entries are explicitly unmapped and unexecuted: shared fixtures and the
portable CTK can overlap with proposed criteria, but are not credited unless
an exact runnable selector is mapped. The CI integrity check requires every
inventory item to remain present and rejects an `executed` or `verified` claim
unless it names an existing pytest function and retained evidence anchor. This
bridge is a coverage guard and migration scaffold; it does not run the 443
proposed tests, validate all 774 constraints, expand the selected CTK subset,
or claim Python/JavaScript parity or full conformance.

The selector guard also rejects pytest functions marked `skip`, `skipif`, or
`xfail`, including module-level skip markers, so an unexecuted/expected-failure
case cannot be promoted by selector existence alone. This syntax check does not
prove that an active test asserts every proposed criterion; reviewers still
need criterion-specific selectors and retained run evidence before changing a
bridge status. CTK artifacts establish only the named Portable Profile subset.

Later task `progress_record` links describe candidate native Command routing,
bounded literal JavaScript execution and durable diagnostic history. They do
not rewrite the dated implementation audit or promote full inventory/proposed
criteria merely because focused tests pass.

The bridge now records the current mapping blockers and CI checks their counts
against the row statuses. The proposed test rows require their full stated
multi-runtime scope, including Python and JS/TS comparisons where listed; no
selector is credited for only one engine or a shared fixture. Schema mappings
likewise require evidence for the precise constraint in its enclosing
operation/property context, not just a general schema-validation test. These
counts describe the current audit, not a claim that the work is impossible;
update the blocker summary as exact selectors and evidence are added.
The bridge integrity check also preserves each extracted constraint's
verification guidance alongside its pointer, keyword, value, and source
reference. This keeps the enclosing-operation/property fixture requirement
from silently disappearing during inventory updates; it does not validate the
constraint against the runtime schema or count it as executed.

The bridge integrity, selector-marker, and retained-guidance checks passed in
the corrected consolidated local suite. All proposed-test and schema rows
remain unmapped and unexecuted; these local guard checks do not count as CTK,
schema, parity, or cross-engine coverage. Exact-source hosted qualification is
still pending.

The [literal outcome comparison candidate](../../docs/s4-04-literal-parity-acceptance.md)
adds source-matched actual ADK/LangGraph Python/Node captures for 13 shared
success/fault fixtures. Its required CI comparison is narrower than the proposed
multi-runtime criteria. It leaves every proposed/schema bridge row unverified;
native capture and complete source qualification remain pending.
