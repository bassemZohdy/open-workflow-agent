# S3-02 finite A2A invocation and interoperability acceptance

Status: implementation in progress; S3-02 remains unchecked. S2-09's finite
recovery policy and S3-01's finite Python MCP profile are verified. The A2A
implementation has broad passing local and exact-source hosted test selections,
but the eight acceptance gates below still require evidence-by-evidence
reconciliation; suite totals alone do not complete those gates. A2A01-A2A08
remain open wherever their cross-method, authority, failure, backend or recovery
controls have not been directly evidenced.

## Contract and authority

Open Workflow 1.0.3 remains the public DSL; its official schema is unchanged.
Every invocation executes a workflow, including the generated one-task default
agent workflow. Workflow A2A calls and configured native agent tools use one
typed common service. SDK 1.2.2 is the selected review/pinning candidate for
stable release 1.0.1 and wire 1.0; package installation does not qualify a method,
binding, discovery flow or native interoperability.

A2A Tasks project common invocation state. Task ID is the invocation ID; context
ID remains the common session identity. Common ownership, original-definition
archive, receipts, deadlines, approvals and child cancellation stay authoritative.
No A2A checkpoint store, second execution engine or native state transplant is
introduced. Skills are deployment-owned workflow bindings. A caller cannot
select arbitrary files, catalogs, engines, schemas or sandbox targets.

Inbound support remains optional and disabled by default. JSON-RPC and HTTP+JSON
are the finite target bindings; broader transports require separate admission
and acceptance. Required message/Part/role/ID/context fields and finite bounds
must validate before creating or resuming an invocation. Known null and
structured artifacts remain distinct from absent output. Cross-principal and
unknown Task access remain indistinguishable. Terminal retrieval is read-only.

Async acknowledgment follows durable admission of the accepted initial
delivery. Existing-task returnImmediately and streaming input resume are
explicitly unsupported in this finite profile; blocking protected resume remains
available. Durable resume consumption needs a separate authoritative wait-revision
gate and is not inferred from the initial queue. Original input and resume input
remain separate. A private
delivery record may retain immutable request/source/skill/principal authority
and bounded claim state; dispatch uses the existing common initial/resume path
and execution fencing. An acknowledged pre-native initial delivery does not
authorize public resume to invent a missing checkpoint. Host drain stops local
workers without converting observation or shutdown into business cancellation.

Outgoing card/interface selection and method models must use reviewed official
semantics and the common bounded HTTP/authentication service. Discovery does not
grant credential forwarding to a different origin. Exact response correlation,
known remote errors and dispatch phase determine whether the common action has
a known result or remains pending uncertainty. Message ID and operation headers
do not establish universal peer deduplication. No automatic resend of a possibly
accepted SendMessage, hidden page fetch, indefinite reconnect or remote rollback
is authorized.

Outbound SDK calls issue one business request and never automatically retry.
After dispatch, a lost initial or partial stream response is uncertain and is
not replayed. Reconnection is an explicit caller action using
`SubscribeToTask`/`tasks/resubscribe` with a known task ID; it does not repeat
`SendStreamingMessage` or imply durable resubscription leases.

Streaming, when advertised, is a bounded protocol projection with exact event
alternatives, task/context correlation, total deadline, byte/event/queue limits
and cleanup. Cross-worker completion must observe authoritative common state;
a process-local subscriber alone is insufficient. Disconnecting an observer
does not cancel execution. Timer/child progress must be distinguished from
approval or other input-required waits using authoritative common evidence.

## Required finite gates

| ID | Required evidence |
| --- | --- |
| A2A01 | Official SDK client→OWA and OWA common client→official SDK peer on actual loopback sockets for every advertised binding/method. Reviewed card/interface/version precedence, typed parameters/results/errors, exact IDs, modern shapes and rejected legacy or unsupported installs. |
| A2A02 | Identical actual ADK/LangGraph workflow calls and configured native agent-tool invocation through the common service, deterministic provider/tool choices and generated default workflow. Direct registry calls alone do not qualify native tool use. |
| A2A03 | Synchronous and returnImmediately completion, protected input-required resume, get/cancel, null/structured artifacts, deployed skill policy and context/owner checks. Malformed or mismatched input rejects before invocation/input/receiver mutation; unknown and cross-principal errors match. |
| A2A04 | Real timer/child/approval waits and authoritative cancellation/deadline/END projection. Observer disconnect does not cancel; first terminal outcomes and uncertain external actions survive late native results and host drain. |
| A2A05 | Actual producer PID stop after async ACK before native setup, after claim before dispatch, and after remote acceptance before receipt/native completion; separate recovery PID on SQLite and actual PostgreSQL. Original invocation/session/principal/source/input/skill/action identity, one remote task and bounded model/action counts remain. Initial delivery competing workers and stale generations are fenced. Async resume delivery remains unsupported until its separate durable consumption gate qualifies. |
| A2A06 | Existing Tasks resume archived original v1 under a v2 deployment while current skill authority still applies. Missing/corrupt archive/native/receipt/owner evidence rejects before unsafe lease/native/receiver writes. Terminal retrieval remains immutable and network-free. |
| A2A07 | Actual TLS and named bearer/API-key/finite OAuth/mTLS controls. Card-selected origin, redirect, resource/audience and credential policy rejects before preparation/business send when unauthorized. No resolved secrets in card/Task/capabilities/errors/logs/ordinary receipts. Existing inbound bearer scope is preserved; no delegated identity forwarding. |
| A2A08 | Bounded streaming/resubscription, initial Task and ordered status/artifact alternatives, cross-worker final state, backpressure/size/malformed/lost-response cleanup and precise optional-method rejection. Durable Task/receipt expiry is explicitly outside this finite profile; no expiry capability is advertised. Push, delegated grants and unqualified transports cannot become capabilities from SDK availability. |

The unchanged official OWS 1.0.3 inline A2A schema includes the seven supported
methods, including `tasks/list` and `agent/getAuthenticatedExtendedCard`.
Its `parameters` property is optional, but an object must contain at least one
property. Empty SDK parameters therefore omit the DSL property rather than
supplying `{}`. The shared native grid retains seven inline methods and one
configured-tool call; neither internal normalization nor the SDK expands the
public schema or enables its separately gated push operations.

## Current evidence checkpoint (not task qualification)

At source `af4acd61c200787fb68248b2d180ba69efb8c837`, all six hosted workflows
passed. The Windows common A2A selection reported 156 passed; the Windows native
lifecycle selection reported 26 A2A contract cases passed; PostgreSQL ADK and
LangGraph selections each reported 26 passed and 26 backend-inapplicable skips.
The core A2A-focused local selection reported 332 passed and 39 skipped. The
isolated SQLite A2A contract profile reported 26 passed for each principal
engine. These establish substantial test execution on the stated source, not
completion of A2A01-A2A08: the gates also require controls to be tied to exact
witnesses, and unsupported durable input resume, push, delegated identity,
transport and cross-language scope remains excluded. Exact-source run IDs and
limits are in
the [validation history](validation-history/pending-batch-results.md#exact-source-qualification-af4acd6).

The documentation-only follow-up at `4014874629cf1200cadb0e0dc8b474d8002a16c0`
passed Docs, Security, External Sandbox and Agent Framework. Fifteen CI jobs
passed, but the Windows timer lane was canceled at its 45-minute job limit, so
the aggregate CI workflow did not pass. PostgreSQL LangGraph also failed one
unrelated S3-03 selected-adapter recovery case. Neither outcome is evidence for
or against the A2A gates; both are recorded in the [validation history](validation-history/pending-batch-results.md#follow-up-at-4014874).

| Gate | Current evidence state | Remaining proof before this gate closes |
| --- | --- | --- |
| A2A01 | Partial: `test_each_method_uses_actual_v1_sdk_envelopes_identity_and_single_business_dispatch` covers all seven advertised methods over ASGI and actual loopback TCP, asserting fixture-defined typed result fields, IDs and one business dispatch per call. The official SDK client test covers the independent client socket path. New `test_correlated_typed_rpc_error_is_known_for_every_advertised_method` and `test_legacy_or_unsupported_card_interface_rejects_every_method_before_dispatch` add seven-method error and legacy-version/unsupported-binding matrices; the grouped 31-case selection passed locally. Existing tests cover invocation-version rejection and card interface precedence. | Complete the remaining response-shape and card/version cross-products on exact source and qualify the full outbound/inbound matrix in hosted CI. |
| A2A02 | Finite Python profile directly evidenced by `test_native_workflow_operations_return_only_typed_sdk_results` and `test_native_configured_agent_tool_calls_the_same_official_peer`; both principal SQLite selections passed 26 cases each. | Keep the claim to these tested engines, methods, deterministic tools and generated default workflow. |
| A2A03 | Partial: async completion, task access, null artifacts, malformed input and skill policy have direct tests. `test_return_immediately_returns_task_and_completes` now checks acknowledged Task/invocation identity, supplied context identity, structured output and exactly one model call on both bindings. `test_resuming_send_restarts_persisted_waiting_task` preserves Task/context identity, original input and exactly one resume. `test_authenticated_a2a_task_access_is_bound_to_creator` confirms unknown and foreign-owner errors match for Get, Cancel, Subscribe and resume on both bindings, without owner-state mutation or model work. Protected-wait bypass and malformed-resume rejection also have both-binding witnesses. | Close the remaining completion, ownership and backend cross-products with exact-source evidence. |
| A2A04 | Partial: timer, child value/fault, cancellation, deadline, host-drain late native values/faults and stream-disconnect projections have direct both-binding witnesses. New `test_protected_approval_terminal_outcome_survives_a2a_recovery` covers a decision written by a second RuntimeServices worker and a due protected approval reconciled after expiry-dispatcher restart; both cases verify immutable terminal reads, persisted approval outcome, rejected terminal resume and zero model calls. Its focused cases passed locally. | Complete the remaining cross-worker terminal observation and uncertainty/replay interactions, then qualify the combined projection suite on hosted backends. |
| A2A05 | Partial: `test_sdk_async_ack_survives_real_stop_with_original_definition_and_clock` covers both engines, SQLite/PostgreSQL and three process-stop phases; recovery checks original identity/clock, one receiver commit and zero model calls. The local ADK/LangGraph SQLite selections each pass 6 cases, including retained skill identity. Invalid skill/archive/queue evidence rejects without writes. `test_old_initial_delivery_process_cannot_write_after_takeover` covers SQLite and actual PostgreSQL with distinct producer/recovery PIDs; the PostgreSQL witness passed at `36544b5`, with generation advance, one receiver commit and unchanged persisted snapshot after stale-owner rejection. New `test_competing_initial_delivery_process_cannot_dispatch_while_owner_is_live` passes on SQLite with separate PIDs, no takeover, unchanged state for the rejected competitor and exactly one eventual receiver commit; PostgreSQL is skipped locally without its configured URL. | Qualify both process-race witnesses on the final exact source and extend broader competing-dispatch acceptance. Async resume remains unsupported. |
| A2A06 | Partial: `test_public_resume_rejects_changed_archive_before_ownership_or_native_mutation` covers missing/digest-corrupt archive and invocation binding through JSON-RPC and HTTP+JSON, preserving common/native/receiver/model snapshots. ADK/SQLite and LangGraph/SQLite each pass 12 selected cases locally. The owner-authority matrix expects common fail-closed `task_not_resumeable`; all eight cases skip locally because isolated engine SDK environments and PostgreSQL are unavailable. `test_a2a_resume_rejects_corrupt_native_checkpoint_before_mutation` collects for LangGraph SQLite/PostgreSQL and both A2A bindings; all four cases skip locally for missing SDK/database prerequisites. `test_a2a_resume_rejects_damaged_receiver_receipt_before_mutation` adds absent/malformed persisted receipt evidence across bindings, engines and backends; all 16 cases skip locally for the same prerequisites and require hosted qualification. Archived workflow authority, immutable card decisions, terminal projection and known-receipt recovery have adjacent witnesses. | Run owner-authority, native-checkpoint and receiver-receipt witnesses in isolated ADK/LangGraph environments and PostgreSQL CI. Verify repeated terminal reads are immutable and network-free. Complete the engine/backend matrix for corruption rejection before lease/native/receiver writes. |
| A2A07 | Partial: local validation passed the actual-HTTPS SDK matrix for all seven advertised methods with named bearer/API-key, OAuth and mTLS credentials. Bearer, API-key and OAuth calls use the endpoint selected from the fetched AgentCard, and discovery carries no business credential. Card/tool endpoint mismatch and unbound OAuth resource policy also reject all seven methods before dispatch. Hosted requalification is pending. | Complete the remaining card-selected origin/resource-policy and secret-surface cross-products across the required backend matrix; delegated identity stays excluded. |
| A2A08 | Partial: inbound and outbound streaming have bounded identity, explicit resubscription, cross-worker completion, first/partial TCP-loss, disconnect, timeout, limits and terminal-cleanup witnesses. The outbound SDK stream failure matrix includes malformed JSON data, an unsupported SSE event name and malformed field framing; all eight focused cases pass after the malformed-JSON classification fix, with final grouped verification pending. `test_stream_disconnect_during_host_drain_keeps_working_projection` additionally passes on both inbound bindings and confirms observer disconnect does not publish a terminal event or mutate the working projection. `test_actual_tcp_subscribe_partial_response_loss_is_uncertain_without_replay`, `test_jsonrpc_rejects_unimplemented_optional_v1_methods_before_work` and `test_http_json_optional_operations_reject_before_workflow_dispatch` pass locally; the last covers all six unimplemented HTTP+JSON v1 operations before invocation. Durable Task/receipt expiry is explicitly excluded from this finite S3-02 profile by the 2026-10-20 scope decision; the existing 24-hour EventStore inbox retention remains separate, and no expiry capability is advertised. Complete malformed-event and optional-method binding/backend matrices. Push and delegated grants remain excluded. |

The A2A02 result is finite Python evidence, not closure of S3-02. The other
rows remain partial until each listed evidence gap is directly traced and the
combined validation source passes. Gate names, test symbols and exclusions are
kept explicit so the large suite totals do not imply broader protocol support.

Representative executed witnesses for the partial gates include:

- **A2A01:** `tests/core/test_a2a_sdk_http.py::test_each_method_uses_actual_v1_sdk_envelopes_identity_and_single_business_dispatch`, `::test_card_selection_uses_first_supported_interface_and_ignores_server_endpoint` and `::test_card_selection_rejects_when_no_interface_matches_finite_profile`; `tests/contract/test_a2a_sdk_inbound.py::test_official_sdk_client_discovers_sends_and_reads_native_task` covers the independent SDK client on both inbound bindings.
- **A2A03:** `tests/core/test_a2a_async.py::test_return_immediately_returns_task_and_completes`, `::test_resuming_send_restarts_persisted_waiting_task` and `::test_a2a_resume_cannot_bypass_protected_approval_wait`; `tests/core/test_a2a_task_routes.py::test_authenticated_a2a_task_access_is_bound_to_creator` checks unknown/foreign response equality across Get, Cancel, Subscribe and resume on JSON-RPC and HTTP+JSON; `tests/core/test_a2a_inbound_v1.py::test_invalid_v1_send_rejects_before_invocation_or_model`, `::test_resume_context_mismatch_has_no_delivery_or_input_mutation`, `::test_malformed_resume_rejects_before_invocation_or_delivery_mutation` (both bindings passed locally), `::test_unknown_and_foreign_a2a_tasks_share_sanitized_not_found_without_callbacks` and `::test_completed_null_is_an_explicit_artifact`.
- **A2A04:** `tests/core/test_a2a_task_routes.py::test_actual_workflow_deadline_projects_stable_read_only_task` drives the configured top-level workflow timeout to a persisted fault by advancing the engine's deterministic absolute clock, then checks repeated GetTask identity/error stability, unchanged persisted payload, and zero model calls on JSON-RPC and HTTP+JSON (2 focused cases passed). The adjacent `::test_end_and_deadline_task_reads_are_authoritative_and_read_only` separately seeds END and deadline-fault records (4 focused cases passed). `tests/core/test_a2a_async.py::test_host_drain_keeps_inflight_task_authoritative_in_later_reads` verifies an in-flight model-backed task remains working and unchanged through repeated GetTask reads on both bindings after engine shutdown, including a late provider result after suppressed cancellation (4 cases passed; cross-worker native late-result and stream projection remain untested). Other witnesses: `tests/core/test_a2a_inbound_v1.py::test_real_timer_wait_projects_authoritative_terminal_task_after_get_task` and `::test_real_retained_timer_is_automatic_work_without_mutating_wait_evidence`; `tests/core/test_a2a_child_projection.py::test_get_task_projects_committed_terminal_nested_child_outcome_immutably` (4 focused cases passed); `tests/core/test_a2a_async.py::test_subscription_reads_another_services_terminal_commit_without_local_event` and `::test_approval_wait_projects_input_required_and_completes`; `tests/core/test_a2a_streaming.py::test_stream_disconnect_does_not_cancel_invocation`; `tests/contract/test_a2a_workflow_calls.py::test_accepted_remote_task_with_lost_correlation_stays_pending_without_catch_replay`.
- **A2A05:** `tests/contract/test_a2a_initial_delivery.py::test_sdk_async_ack_survives_real_stop_with_original_definition_and_clock` and `::test_stopped_delivery_invalid_authority_or_evidence_rejects_before_lease_or_native_mutation`; the recovery worker asserts the persisted delivery retains the original skill ID. `::test_stale_initial_delivery_generation_cannot_write_after_takeover` covers an in-process fence, while `tests/contract/test_a2a_initial_delivery_process_race.py::test_old_initial_delivery_process_cannot_write_after_takeover` passes across two SQLite processes and the actual PostgreSQL witness passed at `36544b5`. The new `::test_competing_initial_delivery_process_cannot_dispatch_while_owner_is_live` covers live ownership on SQLite/PostgreSQL with distinct owner and competitor PIDs; its grouped execution remains pending. Broader competing-dispatch acceptance remains open. `tests/contract/test_a2a_workflow_calls.py::test_original_known_a2a_receipt_survives_native_restart_without_second_task`.
- **A2A06:** `tests/contract/test_workflow_version_recovery.py::test_public_resume_rejects_changed_archive_before_ownership_or_native_mutation` covers REST plus JSON-RPC and HTTP+JSON A2A resume for missing/digest-corrupt archive and invocation-binding evidence; the two principal engines each pass 12 SQLite selections locally. `::test_a2a_resume_rejects_changed_owner_authority_before_mutation`, `tests/contract/test_a2a_resume_native_corruption.py::test_a2a_resume_rejects_corrupt_native_checkpoint_before_mutation` and `tests/contract/test_a2a_resume_receiver_receipt_corruption.py::test_a2a_resume_rejects_damaged_receiver_receipt_before_mutation` add owner/native/receipt evidence rejection across bindings and backends; all isolated actual-native selections collected but skipped in the root environment. The owner-authority assertion matches common fail-closed `task_not_resumeable`. `::test_a2a_cannot_grant_an_unadvertised_archived_workflow_default_skill_authority`, `tests/contract/test_a2a_workflow_calls.py::test_known_receipt_recovery_never_refetches_mutable_agent_card` and `tests/core/test_a2a_card_admission.py::test_changed_request_or_deployment_binding_cannot_replace_original_decision` cover adjacent authority and receipt controls.
- **A2A07:** `tests/core/test_a2a_sdk_http.py::test_actual_tls_a2a_call_uses_named_mtls_credential[method]`, `::test_actual_tls_a2a_call_uses_named_http_credential[kind-method]` and `::test_actual_tls_a2a_call_uses_named_oauth_credential_for_card_endpoint[method]` passed locally across the seven advertised methods and four named TLS credential profiles. Bearer, API-key and OAuth cases use the fetched AgentCard-selected endpoint and assert public discovery has no business credential. `::test_card_cannot_redirect_protected_tool_credentials_to_another_endpoint[method]` and `::test_oauth_card_requires_exact_resource_endpoint_before_issuer_dispatch[method]` reject all seven methods before network or issuer dispatch when endpoint/resource authority is missing. The complete SDK HTTP test file passed 133 cases locally; hosted requalification remains pending. `::test_public_card_fetch_has_no_inherited_business_credentials_and_uses_pinned_selection`, `::test_exact_deployment_credential_grant_reaches_selected_sdk_peer` and `tests/core/test_secret_safety.py::test_a2a_surfaces_never_expose_the_resolved_secret` cover adjacent policy and secret surfaces.
- **A2A08:** `tests/core/test_a2a_streaming.py::test_resubscribe_streams_existing_task_and_rejects_unknown` and `::test_stream_disconnect_does_not_cancel_invocation`; `tests/core/test_a2a_inbound_v1.py::test_a2a_stream_event_limit_keeps_initial_snapshot_and_closes`, `::test_a2a_stream_backpressure_closes_without_leaking_subscription`, `::test_a2a_stream_timeout_closes_after_snapshot`, `::test_a2a_stream_of_terminal_task_contains_only_snapshot_and_closes`, `::test_subscription_reads_another_services_terminal_commit_without_local_event` and `::test_malformed_streaming_message_rejects_without_invocation_or_subscription`; `tests/core/test_a2a_sdk_http.py::test_each_method_uses_actual_v1_sdk_envelopes_identity_and_single_business_dispatch`, `::test_actual_tcp_streaming_lost_response_is_uncertain_without_reconnect`, `::test_actual_tcp_streaming_partial_response_loss_is_uncertain_without_reconnect`, `::test_actual_tcp_subscribe_partial_response_loss_is_uncertain_without_replay`, `::test_stream_limits_identity_and_completion_are_checked_without_reconnect` (including malformed JSON, unsupported SSE event and malformed framing) and `::test_subscribe_rejects_foreign_first_task_even_when_later_events_match_it` witness one-shot dispatch, uncertain loss and explicit resubscription; `tests/core/test_a2a.py::test_jsonrpc_rejects_unimplemented_optional_v1_methods_before_work` and `tests/core/test_a2a_http_json_optional_methods.py::test_http_json_optional_operations_reject_before_workflow_dispatch` witness precise rejection for all six unimplemented HTTP+JSON v1 operations before workflow dispatch. Durable Task/receipt expiry stays outside this finite profile and is not advertised; remaining binding/backend matrices remain open.

These named tests demonstrate individual branches. The remaining table entries
identify the cross-products, backend, process-stop and failure interactions that
still need direct evidence before any partial gate advances.

The authored inventory currently includes common initial-delivery controls
(26 SQLite/PostgreSQL nodes), actual native workflow/tool calls (64), official
SDK inbound socket cases (16), actual process-stop initial delivery (24), and
inbound field/context/cross-worker/SDK availability controls (22). These are
declared counts, not executed evidence. Outbound common SDK HTTP/card/credential controls add 84 declared nodes;
immutable card admission adds 53 declared nodes (26 for each backend and
one official SDK portable-workflow case). Common native SQLite inspection
requires its 14 Windows/Linux controls. The process grid covers ACK before SDK setup, persisted native
projection, and known business receipt before native commit, plus revoked
skill/archive/queue corruption rejection. The initial delivery preserves v1
source/input/identity/clock under v2 and has no paid model calls.

The current exact-source checkpoint and its passing counts are recorded below.
The latest grouped follow-up is pushed as [`bc5f551`](validation-history/pending-batch-results.md#exact-source-follow-up-bc5f551);
its hosted gates are pending. The prior `a77678a` and `18123a6` hosted failures
and their corrections remain documented in the validation history.
SDK version 1.2.2 is required before enabled inbound exposure; sanitized
capabilities retain configured enabled separately from effective SDK
availability and do not claim full conformance. S3-02 stays open until the
remaining gate gaps are directly evidenced and the grouped change batch passes
its focused regressions, independent suites, builds and hosted gates. Mandatory
actual principal/backend cases cannot qualify through foreign SDK or database
skips. Retain failed attempts and finite supervisor/source hashes.

## Independent gates and publication

Push callbacks remain S4-10 delivery/security work. Delegated identity, token
exchange and consent remain S3-06/S4-11. gRPC remains S3-03 unless separately
reviewed and qualified. JavaScript/TypeScript and full T67 interoperability stay
S4-03/S4-04; Python/native evidence cannot close them. T32/T33 receive only
explicitly proven finite evidence. Full A2A/OWS conformance, arbitrary version
migration, remote rollback and general exactly-once guarantees are not claimed.

Retain predecessor LC184, VM68, MCP64, common security/platform controls, seven
audits, two native benchmarks, 26 canonical CTK feature hashes and four runtime
image gates. All six hosted workflows must qualify the final combined source.
Update TODO/PROJECT and only proven tracker/audit subsets after acceptance;
proposed inventory tests and independent gaps remain visible and unverified.

The S3-02 implementation baseline was authored from
`325458af130cccc8d9d899dc09614f6e55cc48f5`; that provenance is not A2A
acceptance. With S2-09 and S3-01 prerequisites now verified, S3-02 is
actionable. Continue with the grouped peer/native/backend gates, preserve the
bounded semantics above, then run the frozen focused and full validation
profiles. No blocker is recorded.


## Retained first combined batch

Frozen source `ad441432` remained unchanged throughout the first batch. Root
reported 12 failed / 203 passed / 26 skipped; actual ADK reported 12 failed /
28 passed / 75 skipped; actual LangGraph reported 12 failed / 28 passed /
76 skipped; optional Agent Framework reported 81 passed / 316 skipped. Five
independent wheel/sdist builds passed. These results are retained under
`tmp/s3-02-first-batch` and do not qualify the source or any skipped principal.

Failures exposed fixture setup mismatches (invented configuration field,
nonexistent child dispatcher start hook, incomplete modern messages, empty
optional parameters) and stale typed-error/structured-Task assertions. They also
exposed a real terminal queue settlement early-return defect, now corrected.
The correction retains owner/input/identity assertions and adds stop-window
coverage within the existing counts. The full corrected batch remains pending;
S3-02 and its feature criteria stay unchecked and unqualified.


## Retained second combined batch

Frozen source `296c60e` remained unchanged throughout the second batch. Root
reported 2 failed / 424 passed / 79 skipped; actual ADK and actual LangGraph
each reported 13 failed / 112 passed / 232 skipped; optional Agent Framework
reported 81 passed / 316 skipped. Five independent wheel/sdist builds passed.
Full proof is retained under `tmp/s3-02-second-batch`; no skipped SDK/backend
profile or successful build substitutes for runtime qualification.

The remaining failures identified a real authenticated AgentCard compatibility
defect (legacy bearer security fields), an incorrect SDK subscription method in
the peer fixture, shared-helper import placement, and older version-recovery
fixtures with incomplete modern requests or stale artifact assertions. The
correction publishes strict SDK-parsed v1 `httpAuthSecurityScheme` and
`securityRequirements` while retaining the bearer scheme identifier and excluding
secrets. The existing positive card test now parses the complete response through
the official SDK. The stream fixture uses its actual `subscribe` SPI. Counts
remain unchanged; the complete correction batch and full qualification remain
pending, with S3-02 and its feature criteria unchecked.

## Retained third correction focus

Frozen source `386b390` remained unchanged throughout the third focused batch.
Root reported 285 passed / 39 skipped; actual ADK reported 2 failed / 39 passed /
123 skipped; actual LangGraph reported 3 failed / 38 passed / 123 skipped. Five
independent wheel/sdist builds passed. Exact logs and source snapshots are retained
under `tmp/s3-02-third-batch`. These focused results do not qualify skipped profiles
or substitute for the required full/native/backend/hosted acceptance.

The actual SDK streaming peer exposed a runtime wire defect: JSON-RPC SSE data
contained bare streamed results instead of correlated `{jsonrpc,id,result}`
envelopes. The correction wraps every frame before bounded byte accounting and
forwards the validated original request ID; HTTP+JSON keeps bare streamed results.
Existing common fixtures now validate the exact envelope fields, version and ID
type/value before inspecting any result, including live ASGI and disconnect readers.
The remaining producer fixtures identified a worker receipt-serialization mismatch
and the selected LangGraph partial-native stop boundary. Their correction retains
the same required identities, outcomes, native-history and no-repeat-dispatch
assertions. Fixture counts remain unchanged. S3-02 and its criteria remain unchecked;
the complete corrected batch and final qualification are pending.

The fourth frozen correction at `0c5eec0` passed 55 common wire controls
(13 backend skips), all 10 actual SDK/socket/process-stop cases per principal
(30 foreign/backend skips each), and all five independent builds. The 13 new
LangGraph initializer corruption/control cases failed at fixture creation because
a required `user_id` was omitted, before archive or SDK setup; that fixture field
is corrected without changing runtime code or assertions. Exact failed and passing
history is retained in `tmp/s3-02-fourth-batch`. Final full suites, actual PostgreSQL,
platform, image, security, CTK and exact-source hosted acceptance remain required.

### Bounded full-suite history at 072fa41 (not qualification)

Four independent local full suites retained an unchanged 433-input source
snapshot and continued making progress until their explicit 3,000-second
Windows bound. Each exited124 with timed_out=true; none is a passing full
suite or coverage gate. Five standalone/root/engine builds passed on that source.
The original full logs, start/result records and source manifest are retained in
`tmp/s3-02-full-bounded-072fa41`; no timeout or earlier failure is promoted.

The catalog mock did not provide the pinned SDK's correlated JSON-RPC2.0 typed
Task result. Its correction retains the inline/imported translation comparison,
adds exact request version and unique IDs, echoes the request ID and returns the
typed Task directly. Final LangGraph log review also exposed the common HTTP
regression in preexisting recovery fixtures: preparing the client's own named
bearer binding discarded its explicit verified private CA. Positive preparation
failed TLS before native-corruption controls could execute. A bounded standalone
reproduction and saved producer/query-only artifacts confirmed that shared cause.

The grouped correction preserves a client's selected CA only for its inherited
or exact same authentication object, while an explicitly different selected
profile still replaces global trust and client identity. Six new direct/pinned
real TLS witnesses cover retained bearer trust and rejection of foreign-profile
CA inheritance. The common TLS suite now declares10 cases. Windows native routing
uses verbose rows so all13 initializer controls can be independently audited.
Corrected scoped checks, fresh builds and all exact-source hosted full-suite,
coverage, PostgreSQL, Windows, image, audit, CTK and benchmark gates remain
mandatory before S2-09/S3-01/S3-02 closure or publication qualification.

### Grouped trust correction at 39b1024 (scoped history only)

The corrected real TLS grid passes all10 cases in independent common/principal
and optional Framework environments. Actual ADK and LangGraph each pass35
selected checks (69 explicit foreign/backend skips), including every applicable
11 native corruption and12 OS-stop recovery case that previously failed before
TLS admission. Optional Framework passes13 selected cases. Five fresh builds
pass, and every supervisor records zero source changes. These scoped passes do
not turn the earlier bounded full suites into green full/coverage evidence.

The common root check retains411 passes/26 skips and one timing-dependent MCP
fixture failure. Its original30ms whole-operation budget expired before the
shared dispatch marker, so the runtime correctly returned a known pre-send
ToolError. The fixture now keeps a finite1s original budget, explicitly waits
for response-body entry, and then requires post-send uncertainty, exactly one
request and actual stream closure. The body remains indefinitely stalled; no
clock is reset and no runtime classification or retry rule is relaxed. Final
fixture checks, source-matched builds and hosted full acceptance remain pending.
Logs and immutable source/result records are retained in
`tmp/s3-02-postfull-39b1024`; earlier failure history remains distinct.

### Transport fixture phase review at e63d096 (not a green root batch)

Independent ADK/LangGraph fixture checks pass98 cases each and optional
Framework passes99; all common TLS10 and MCP86 witnesses execute. Five builds
pass on that source with zero changed inputs. Root retains411 passes/26 skips
and one60ms issuer/resource phase assertion failure: issuer handling consumed
the budget before any resource request, which is a valid pre-send timeout.
The saved root log is retained in `tmp/s3-02-final-fixture-e63d096`.

The grouped fixture correction uses explicit issuer/resource/body entry events
and modest finite original budgets, with permanently stalled mocked receivers.
The combined issuer/resource witness additionally inspects the actual resource
request's remaining read timeout after a known issuer delay; resetting a new
full resource budget still fails. Post-send MCP/A2A witnesses require one
actual dispatch and stream cleanup. Metadata rejection fixtures no longer rely
on a sub-scheduler-sized timeout unrelated to their admission purpose. No
runtime deadline/classification or known/uncertain replay semantics changed.
Full hosted qualification and final common fixture validation remain required.

### Hosted f020d6e failure review and grouped corrections (unqualified)

The final scoped common checks passed237 root,228 ADK,228 LangGraph and229
optional Framework cases, and five fresh package builds passed with unchanged
source inputs. The pushed checkpoint passed Docs, Security, external sandbox,
both standard runtime images, and both Linux native lifecycle selections except
the four LangGraph SDK inbound PostgreSQL-labeled cases. These scoped results do
not qualify the failed aggregate workflows or close any pending task.

PR CI root and principal shared suites exhausted their eight-minute commands at
22%,46% and51%. Optional Framework exhausted its ten-minute job at36%; its
PostgreSQL selection exhausted five minutes before the final OS-stop case.
Windows timing exceeded its job budget and also retained two deadline recovery
failures; Windows ownership retained one first-invoke waiting timeout. PostgreSQL
common retained29 failures. Principal PostgreSQL recovery selections retained
assertion failures before their14-minute bounds; their aborts omit final failure
stacks and cannot establish exclusive causes. Original logs and artifacts are
retained in the primary checkout's `tmp/ci-f020d6e-*` evidence.

The grouped fixture corrections isolate content archives and retire only
fixture-owned queues/schedules after negative snapshots, supply genuine leased
original parent/child admissions before pending effects, observe archive-first
timer recovery rather than expecting network compilation, and suppress global
timer polling only in fixtures proving explicit invoke/resume boundaries.
Automatic-delivery acceptance retains real pollers. The child deadline OS worker
now reopens the original32/1000 deployment quotas; its former64/100000 recovery
policy was correctly rejected by the retained profile. Separate common controls
require changed quotas to fail without mutation. Worker failures retain common
reason details. SDK inbound PostgreSQL selection now uses its actual configured
database and asserts the common/native storage destination before network I/O.

Finite aggregate CI budgets now cover the measured complete selections while
preserving every fixture, original per-case deadline, independent backend/job,
90% root coverage, image/audit, CTK and benchmark gate. These edits still require
one frozen grouped behavioral run and fresh complete hosted qualification. No
cancelled, timed-out, mislabeled-backend or earlier-source result is counted as
acceptance, and no task is deferred while actionable corrections remain.

### Consolidated local batch — 2026-10-17 (not qualified)

The latest full local root suite (on the working tree before `36544b5`)
completed in 3h30m with 4,616 passed, 3,007 skipped, 28 failed, 13 setup errors
and 125 warnings. The coverage gate passed, but the suite was not green. The 13
setup errors reproduce at the Windows native SQLite real super-journal barrier
fixture. The 28 failures include Windows process/sandbox timing and workflow
lifecycle cases. Focused A2A approval-wait and nested-child projection cases
pass in isolation. The malformed SSE JSON parser now maps parse failures to
`invalid_stream`; its eight-case focused matrix passes. No A2A gate is complete.
See the [full batch record](validation-history/pending-batch-results.md#latest-working-tree-root-batch-2026-10-17-not-green)
for evidence and source limits.

Pushed exact source `d7a3acf` passed PostgreSQL, External Sandbox, Security and
Docs. Main CI and Agent Framework CI failed recovery-admission shutdown
assertions because the common read-only resume preflight runs before lease
acquisition; pushed source `5f11067` tracks and cancels that admission phase,
and its focused ADK/LangGraph cases pass. The same CI run exposed a Windows
`heartbeat.json` read-open permission failure and 13 setup errors from the
native SQLite hot-super-journal fixture. A bounded read-open retry is locally
covered by IPC tests, but the original Windows deadline scenario is not yet
requalified. Real Task deadline transition and host-drain projection witnesses
now pass both A2A bindings locally. The native SQLite fixture now uses a pinned APSW VFS barrier at super-journal deletion. The focused Windows batch passed; corrected hosted qualification is pending. Durable Task/receipt expiry still needs a product contract.
Exact workflow links and limits are in the
[source qualification record](validation-history/pending-batch-results.md#exact-source-qualification-d7a3acf),
[the `5f11067` follow-up](validation-history/pending-batch-results.md#exact-source-qualification-5f11067)
and [current source `bc5f551`](validation-history/pending-batch-results.md#exact-source-follow-up-bc5f551).
