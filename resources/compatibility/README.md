# Full compatibility tracker

[feature-tracker.json](feature-tracker.json) retains the reviewed inventory: 575 OWS-to-LangGraph rows, 83 reverse rows, 443 proposed tests, 774 schema constraints, 83 extension decisions and 52 configuration entries. Each feature links to pending tasks in [TODO.md](../../TODO.md).

The inventory records source analysis and proposed translations, not completed implementation or validated full conformance. Python and JS status are independent. Original research fields remain intact; `repository_tracking` and each tracking row's `todo_ids`, `repository_status` and `evidence_of_completion` record repository delivery separately. Revalidate upstream versions before implementation; this inventory is dated 2026-10-11 and covers more than the current shipped profile.

Effort S/M/L is relative work size, not a calendar promise. Do not delete a row merely because it is deferred or cannot have identical framework internals. Record the limitation, any business-behavior representation, extension boundary, and evidence. Native API existence does not prove semantic compatibility. Never promise arbitrary host-code/object serialization or exact distributed side-effect execution through YAML alone.

[implementation-audit.json](implementation-audit.json) records current active
behavior separately for all 658 IDs, with qualified source/test evidence groups
and remaining repair owners. [The audit report](../../docs/current-implementation-audit.md)
explains partial, rejected, inert, mismatched and private native outcomes.
`current_implementation_audit` links each delivery row to that evidence;
original research fields and all full proposed acceptance criteria remain
unchanged. A qualified subset is not a full-feature or conformance claim.
