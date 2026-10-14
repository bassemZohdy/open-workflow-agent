# S3-03 operation adapters: finite acceptance plan

Status: authored, unqualified. S3-03 remains unchecked. The first frozen
behavioral batch failed as recorded below; no native/backend qualification,
hosted CI or inventory-completion claim is made. Authored finite gRPC and embedded background executable
profiles still require their independent S3-03 gates. AsyncAPI network activation
and broader function/sandbox responsibilities remain pending.

Latest hosted checkpoint `f9cb76f` failed the PostgreSQL LangGraph operation
adapter job on the selected-adapter `inbox-before-ack` recovery case (220
passed, 158 skipped, 1 failed). Review found a fixture race: after durable
inbox commit, the blocked ACK callback yielded the event loop, allowing
LangGraph to advance before the parent stopped the producer. The fixture now
holds that loop at the intended pre-ACK boundary; local Windows ADK/SQLite and
LangGraph/SQLite selections pass. Hosted requalification must confirm whether
this fully explains the PostgreSQL mismatch. PostgreSQL and the Windows
40-minute operation-adapter lane still need hosted requalification; S3-03
remains unqualified.

The public DSL remains the unmodified Open Workflow 1.0.3 schema. Core owns the
common adapter; ADK and LangGraph use their own existing native checkpoints.

## HTTP and pure OpenAPI profile

| Construct | Authored finite behavior | Qualification still required |
| --- | --- | --- |
| HTTP output | Exact bytes become ASCII base64 for `raw`; JSON/text and response projections retain existing semantics, including explicit null. | Empty/binary/UTF-8/null and all output modes in common/shared suites. |
| HTTP redirect | OWS status acceptance (`200–299`, or `200–399` when true) does not imply an automatic request to Location. | Known status errors, no redirected credential/destination access, bounded transport regression. |
| OpenAPI version | Exact OAS `3.0.3` and `3.2.0`, finite selected-operation profile. Other versions reject; this is not full parser/conformance support. | Both versions, valid and negative schemas, document provenance. |
| Description | Bounded strict UTF-8 JSON/YAML; public GET has no inherited business auth or client certificate. No aliases, anchors, arbitrary tags, duplicate keys, nonfinite values, opaque SSLContext or custom TLS transport. | Deterministic negative controls and actual description/business listener. |
| Bounds | Description/canonical data at most 1 MiB, 10,000 nodes, depth32; up to32 selected local references; up to64 parameters per scope and8 security alternatives. | Exact edge/overflow cases and total original deadline/cancellation. |
| References | Selected local JSON Pointer closure; malformed, external and recursive selected schema references reject. Unused references are not fetched. | Local reference binding and independent immutable corruption evidence. |
| Operation | Unique case-sensitive operationId; operation/path/root server precedence; relative server and OAS3.2 `$self` base; finite declared server-variable defaults/enums. | Exact captured method/path/endpoint and duplicate/unsafe routing rejection. |
| Parameters | Path/header `simple`; query/cookie `form`; scalar and flat array/object encoding; scalar cookies; explicit required/unknown/type constraints; path overrides. Ambiguous names across locations reject. | Slash/reserved-value encoding, structural delimiters, array/object/null, no unsafe header values. |
| Schemas | Bounded selected primitive/object/array JSON Schema validation; OAS3.0.3 nullable and boolean exclusive bounds translated internally. Unsupported schema keywords, composition, regex, formats and recursive models reject. | Finite typed cases for both versions; no claim of arbitrary JSON Schema support. |
| Responses | Declared matching media/JSON schemas validated before projection; other accepted HTTP statuses retain common projection. Text/plain and binary projection need no declared response schema; unsupported selected codec/schema profiles reject. Invalid dispatched JSON replies remain uncertain. | Typed/null/duplicate/mismatched replies and known status faults. |
| Request body | Rejected before business dispatch until an authoritative OWS parameter-to-requestBody mapping is reviewed. No invented public `with.body`, method or server override. | Remains explicitly pending; no pass-through JSON-body substitution. |
| Security | Named deployment bearer/API-key/mTLS or explicit OAuth client-credential binding; exact actual prepared endpoint grant/private tool binding; declared security requirements must match current profile. | Actual mTLS and missing/revoked/wrong endpoint/scope checks before secret resolution. |
| Business transport | Same common HttpClient, original remaining deadline, logical operation key, TLS/DNS/host/byte bounds, known validated HTTP status faults. Timeout/connection/oversize failure after dispatch is uncertain, with no implicit business replay. | Root workflow uncertainty integration and real process-stop/recovery evidence. |

## Private immutable selection and integration gates

`OpenApiDescription` retains original canonical document JSON, retrieval URI and
SHA-256. `OpenApiOperationSelection` uses `owa/openapi-operation/v1` and exact
derived fields; `from_value` re-resolves the captured document and requires equality.
It stores credential references only and excludes its transient remaining timeout.
`prepare_request` constructs the actual encoded endpoint/query/headers from original
parameters. Business credential grants apply to that actual endpoint, not the
unexpanded route template. A description or securityScheme grants no credential
authority.

The independent durable-admission owner must atomically bind the real call identity,
original input/principal/definition/transport/source/budget to completed
`policy:openapi-operation:v1` receipt, decision row and index before business
dispatch. Existing known receipts must retain this independent proof and never
refetch mutable descriptions. Missing/changed proof, stale/lost generation and
interrupted publication fail closed. Pending foreign-owner intents are never
adopted. Native checkpoint skips must verify the policy dependency independently.

RuntimeServices/ToolRegistry and imported-catalog forwarding are separate root-owned
integration gates. Catalog transformation/trust must be preserved when forwarding
private selections; mutable discovery without genuine admission context is not a
qualified configured-tool recovery profile. Reusable OWS authentication remains
its existing pending capability, distinct from named deployment security profiles.

Required shared evidence covers actual ADK/LangGraph with SQLite/actual PostgreSQL,
changed/deleted descriptions after reopen, value/null/fault known-receipt replay,
distinct loop keys, original task deadlines, concurrent/stale workers and genuine
OS stops before dispatch/after business commit/after receipt. Core tests and authored
tables cannot substitute for those native/process/backend witnesses. Fixture counts
will be pinned only after final source authoring and collection, then passing actual
logs must show no mandatory skips/errors. Existing CTK/security/build/image/source
hash and all six hosted acceptance gates remain independent.

## Reviewed sources

Authored OpenAPI witnesses, pending collection and execution:

- `tests/core/test_openapi_client.py`: 66 manually enumerated cases, including
  host-drain cancellation propagating after the response stream closes. Ordinary
  post-dispatch timeout/connection/oversize errors remain uncertain.
- `tests/contract/test_openapi_calls.py`: 19 cases per principal/backend, 76 in
  the authored ADK/LangGraph and SQLite/PostgreSQL grid. Includes registered
  compatibility imports retaining the original parent lease/context and native
  observer, immutable captured imports replaying with deleted catalog/description,
  and uncertain imported actions bypassing catch/retry. The compatibility path
  has an inner common action receipt; no separate outer receipt is assumed.
- `tests/contract/test_openapi_process_recovery.py`: 12 cases per principal/backend,
  48 in the authored grid. An actual verified HTTPS/mTLS peer commits a durable
  operation ledger; a separate producer is killed before dispatch, after business
  commit, after value/null/fault receipt, or after terminal finalization. Recovery
  uses another PID, actual native history and natural lease expiry. Changed/deleted
  descriptions must not be fetched again; known outcomes retain their original
  policy/business proofs. A committed action without a receipt stays pending and
  is never sent again. This finite proof does not grant generic receiver
  reconciliation or an exactly-once guarantee.

These are source inventories, not passing test counts. Backend/native presence,
original identity/input/principal/deadline, zero extra metadata/business requests,
cleanup, and mandatory no-skip execution remain required before qualification.

[Pinned OWS 1.0.3 DSL reference](https://raw.githubusercontent.com/open-workflow-specification/specification/v1.0.3/dsl-reference.md)
defines HTTP/OpenAPI raw and redirect behavior and the unchanged OpenAPI arguments.
[OAS 3.0.3](https://spec.openapis.org/oas/v3.0.3.html) and
[OAS 3.2.0](https://spec.openapis.org/oas/v3.2.0.html) define the reviewed finite
operation/server/parameter/security rules. OAS3.2.1 is an explicit future review,
not silently admitted by this finite implementation. Vendored OWS schema provenance
remains in `resources/open-workflow/1.0.3/SCHEMA_SOURCE.md`.

## Pure AsyncAPI description profile

Status: authored, unqualified, transport inactive. `asyncapi_client.py` performs
no description retrieval, broker connection, credential resolution, message send,
poll, offset commit or acknowledgment. S3-03 remains unchecked. Existing workflow
admission continues to reject `call: asyncapi`; these private descriptors do not
enable it. The unchanged OWS `document`, `channel`/`operation`, `server`, `protocol`,
`message`/`subscription` and `authentication` fields remain the public contract.

`AsyncApiDescription` captures strict JSON or YAML and original document URI.
Compact JSON preserves source object order: OWS chooses the first eligible server,
so sorting server keys would alter routing. The exact captured bytes receive a
SHA-256 digest. Frozen `AsyncApiOperationSelection` retains this original source,
version, selector and derived channel/address/server/message/schema/binding/security
strings. Its `owa/asyncapi-operation/v1` reconstruction re-resolves the source and
requires equality. No private selector or broker configuration enters user JSON.

| Area | Authored finite behavior | Required qualification or excluded behavior |
| --- | --- | --- |
| Versions | Exact `2.6.0`, `3.0.0`, `3.1.0`; version2 uses channel publish/subscribe; version3 uses operation send/receive with a matching caller mode. | Other versions reject. This is selected-operation parsing, not full AsyncAPI conformance or inversion of another application's API. |
| Description bounds | 1 MiB raw/canonical document, 10,000 nodes, depth32; strict UTF-8/JSON values; duplicate/nonstring keys, nonfinite numbers, YAML tags/aliases/anchors reject. | Common HTTP metadata fetch, deployment trust and durable operation admission remain separate work. |
| References | Selected local JSON Pointer closure, at most64 references; escaped pointer segments supported. | Missing, malformed, recursive or external selected references reject. Unused definitions are not fetched. |
| Routing | Version-specific channel/message references, channel server subset, explicit server or first eligible original declaration, protocol filter, declared server-variable defaults/enums. Static version3 channel-parameter defaults are supported. | Unknown/unsafe variables, dynamic/ambiguous channel routing, unsupported protocols and foreign messages reject. A URI or description grants no network/credential authority. |
| Messages | At most16 candidate JSON messages; finite Draft7 scalar/object/array constraints and local schema references; headers are objects; the message must match exactly one candidate. | Avro/Protobuf codecs, schema composition/regex/formats, traits and request/reply interactions reject. Tests must prove unique/ambiguous schemas and type/null behavior. |
| Correlation | Declared `$message.header` or `$message.payload` JSON Pointer; typed nonempty string ID; an inbound supplied correlationId must agree. | No ambient expression evaluation or generated correlation identity. Broker delivery identity/deduplication is a separate gate. |
| Bindings | HTTP0.3.0 and Kafka0.5.0 selected declarations parsed; HTTP method/query/header schemas, Kafka topic/group/client/key schema and bounded topic metadata retained. | No automatic HTTP POST, schema-registry lookup, topic creation, producer/consumer configuration or broker qualification. Unreviewed binding versions/fields reject. |
| Security | Declared server and operation alternatives remain independent; named v2 schemes and local v3 scheme references, bounded client-credential OAuth descriptions and finite HTTP/Kafka mechanism declarations. | No secret resolution or credentials in descriptions; inline credential fields and unsupported flows reject. Parsing a mechanism does not enable its authentication implementation. |
| Subscription descriptor | Original filtered consumption amount/while/until, filter, foreach and duration retained; while is after a filtered message, until before. Fixed day/time and integer-unit durations remain original inputs. | No consumer loop or second execution engine. Original absolute deadline, foreach native checkpoints, durable registration/inbox and offset ACK semantics remain unimplemented here. Calendar/unresolved duration profiles reject. |

`tests/core/test_asyncapi_client.py` authors description/order/immutability,
JSON/YAML, selected references, messages/correlation, server/security/binding,
consumption-position and rejection witnesses. They are unexecuted at this authoring
stage and cannot substitute for actual broker, native, SQLite/PostgreSQL,
cross-worker, process-stop or CI proof.

External broker activation remains conditional on the concrete consumer/transport/
deployment requirements in `Project Definition.md` section36 and
`docs/event-integration-readiness.md`, with independent EVENT/S4-10 gates. A future
Kafka network boundary must enforce the actual advertised broker destinations and
credential authority; a configuration flag or a bootstrap-host allowlist alone
does not prove that. Durable publication needs actual delivery acknowledgment;
subscription needs durable inbox admission before a fenced explicit offset ACK,
crash redelivery/deduplication and the existing native foreach executor. Source
parsing, a producer's enqueue return and process-local events prove none of these.

Reviewed official sources are [AsyncAPI2.6.0](https://raw.githubusercontent.com/asyncapi/spec/v2.6.0/spec/asyncapi.md),
[AsyncAPI3.0.0](https://raw.githubusercontent.com/asyncapi/spec/v3.0.0/spec/asyncapi.md)
and [AsyncAPI3.1.0](https://raw.githubusercontent.com/asyncapi/spec/v3.1.0/spec/asyncapi.md).
The declared binding profiles follow immutable source
[HTTP0.3.0](https://raw.githubusercontent.com/asyncapi/bindings/1f418fe7077096cc8f25d0c6518df617f7c303f3/http/README.md)
and [Kafka0.5.0](https://raw.githubusercontent.com/asyncapi/bindings/1f418fe7077096cc8f25d0c6518df617f7c303f3/kafka/README.md).
The [OWS1.0.3 AsyncAPI call reference](https://raw.githubusercontent.com/open-workflow-specification/specification/v1.0.3/dsl-reference.md)
defines the unchanged selectors and filtered consumption timing.

## Awaited executable return selection (authored, unqualified)

The unchanged [OWS1.0.3 Run and processResult reference](https://raw.githubusercontent.com/open-workflow-specification/specification/v1.0.3/dsl-reference.md#run)
requires default `stdout`, explicit `stderr`/`code`/`all`/`none`, and `all`
with the public `code`, `stdout` and `stderr` fields. The existing private
execution receipt retains the complete bounded SandboxExecutionResult before
projection; return selection cannot erase outcome evidence or trigger a new
execution on replay. Explicit `none` projects JSON null, and exit code zero
remains an integer. Non-zero exits retain the existing common error contract;
selecting `code` does not silently convert an execution failure into success.

The common sandbox executor and admission now author these awaited modes.
Script/shell/container backend opt-ins, exact image policies, workspace,
resource/output/time bounds and cancellation remain prerequisites. Invalid
return modes reject before dispatch. Executable `await:false` still rejects
until the independent job/store/supervisor lifetime profile is implemented and
qualified. This change does not activate background work or weaken isolation.

`test_sandbox_contract.py` authors 48 actual native SDK/internal script/shell
mode/reusable-function cases, including nonempty stderr, plus existing retry
and lifecycle checks. `test_sandbox_effect_recovery.py` adds 20 principal/backend
mode cases with actual native persistence and common outcome retention across
interruption before native commit; its fake backend proves common receipt
semantics, not a physical restart or selected container execution. Existing
container fake contracts now expect default stdout. Eleven admission witnesses
check all declared modes, deployment rejection, background rejection and invalid
selectors. These fixtures remain unexecuted during authoring. Actual backend,
process-stop, platform, packaging and hosted qualification remain outstanding.

OpenAPI's `operationId` is solely its description selector. Both standalone
protocol calls and workflow calls now keep it separate from business logical
operation identity. The authored standalone identity witness and shared loop
witness require distinct calls/occurrences to use distinct action keys while
retaining an explicitly stamped/recovered key. No test execution is claimed.

## Selected container field admission (authored, unqualified)

The restricted Docker controller's existing fixed pull policy is `never`; the
Kubernetes controller constructs pods with `IfNotPresent`. Explicit OWS
`pullPolicy` now must match the selected controller's implemented policy, so
`always` and other mismatching explicit policies reject before action admission.
The historical Docker profile remains limited to deployment-approved preloaded
images; this does not qualify general registry pulling or default-policy behavior
for an uncached image. Neither controller qualifies arbitrary container lifetime
settings: explicit `lifetime` now rejects instead of being discarded by the
request builder. The Kubernetes backend does not implement stdin, so present
stdin (including empty or dynamic values) rejects during workflow admission;
Docker's existing stdin boundary retains separate acceptance.

Thirty source-level compiler witnesses cover all three official policy values,
both selected controllers, inline/reusable functions, declared lifetime modes,
input-source immutability and Kubernetes stdin. They launch no controller and
remain unexecuted. Existing DEPLOY-1/Docker acceptance is not proof of new
lifetime, pulling, mappings, language or background profiles. Public schema and
controller execution APIs remain unchanged. Real selected-backend proof and the
full proposed T40/T41 inventories stay separately required.


## Authored embedded background executable profile, unqualified

The explicit deployment switch is `sandbox.background.enabled`, false by default.
Activation requires an enabled internal sandbox and a persistent common SQLite
file. PostgreSQL and external/container background activation reject before engine
ownership, native execution or process launch. PostgreSQL job-ledger fixtures prove
only the common store; they do not qualify a PostgreSQL supervisor.

Executable `run.await: false` atomically retains a completed logical run acceptance
and independent `policy:sandbox-job-admission:v1` receipt under the real original
invocation lease. Both receipts enter the engine's native dependency lane. Its
output is exactly the transformed task input, including explicit nulls; no job ID,
exit/result projection or future completion is substituted. Admissions retain the
original archived source/raw input/principal, transformed request/input, deployment
profile and original task/root absolute budget. Missing/changed/index-orphaned
proofs and foreign pending intents reject; no ambiguous submit is retried.
The read-only continuation preflight bounds per-invocation job proof enumeration
at 10,000 rows and rejects overflow; it never silently skips later evidence.

A common observer uses an independent fenced job lease and the common manager to
contact the bundled authenticated supervisor. Parent completion, cancellation,
END or runtime drain does not implicitly cancel an independent job. Runtime start
and stop only start/stop this observer. Explicit job controls are separately
immutable; actual known exit and control ACK are not interchangeable. A verified
original-deadline outcome has its own independent receipt so an earlier cancel
intent can remain immutable without relabeling the actual deadline exit.

This is a controlled internal execution boundary, not hard isolation. A living
supervisor enforces the original absolute wall-time budget; supervisor loss remains
uncertain and cannot authorize PID adoption, outcome fabrication or relaunch.
POSIX supervisor loss does not prove orphan wall-time termination. Durable
background environment values currently require deployment-approved secret
references; synchronous literal environment semantics are unchanged. No resolved
secrets are persisted in ordinary admission, receipt or supervisor bootstrap.

Authored evidence pending collection and execution includes common SQLite/actual
PostgreSQL atomic ledger/control/corruption fixtures, embedded supervisor fixtures,
and `tests/contract/test_background_job_recovery.py`: four real native OS-stop
boundaries per ADK/LangGraph principal (receipt, parent completion, cancellation,
and runtime drain), plus eight actual native nonterminal proof failures per
principal before submit: missing job/admission/action/indices/archive/input and
changed current deployment profile. These require unchanged parent owner generation
and native snapshot, no supervisor launch and no business commit. Those workers
publish actual producer/supervisor/child/recovery
PIDs, kill the producer through its real OS handle, preserve original deadline and
receipt identities, and require exactly one real business commit. No behavioral
pass, native/backend qualification or task checkbox is claimed by authoring.

## Authored unary gRPC profile, unqualified

The default `protocols.grpc_bindings` registry is empty and `call: grpc` remains
disabled unless an explicit deployment profile passes admission. The authored
client pins `grpcio==1.84.0` and `protobuf==7.36.1`; installed SDK versions and
physical binary/source fingerprints are retained in the recovery profile. The
reviewed gRPC source is commit
`3252a89f10d8e92997862167ca7d095ecda85973`, the immutable v1.84.0 release.
Core owns `grpc.aio` unary transport; adapters retain existing native tasks and
checkpoints and do not construct another execution or subprocess path.

A named deployment binding contains original UTF-8 proto text, its SHA-256,
a bounded compiled FileDescriptorSet and its SHA-256, declared document URI,
destination/service/method, limits, and an exact credential endpoint grant.
Raw descriptors are bounded to 1 MiB and at most128 registry entries. Registry
validation occurs before invocation ownership or business dispatch. No runtime
protoc, descriptor reflection, arbitrary generated-code imports, untrusted paths,
remote descriptor fetch or automatic registration is used.

The finite closed descriptor/ProtoJSON profile validates fields, oneofs, enums,
bytes, int64 values, request/response limits and the selected service/method.
Streaming RPCs and unsupported descriptor constructs reject before connection.
Root google.protobuf Value/ListValue response codecs reject at descriptor
admission; a declared response message may contain supported nested Value/null
data. Whole-list/root-null projection is not silently lost after dispatch.
Channel construction preserves explicit DNS destination policy, TLS authority and
certificate verification, exact prepared authentication grants, stable logical
operation metadata and the original remaining deadline. Transparent retry,
proxy and service-config-driven retry are disabled.

A validated declared remote application-status trailer provides a known fault.
A client-generated/transport failure after sending instead raises the private
uncertain-outcome control: the common action stays pending and workflow catch or
retry cannot resend it. Host interruption propagates cancellation while closing
the call. Known value/null/fault receipts retain original policy/source/input,
clock and native dependency proofs and recover without a second business call.
Activated profile drift or damaged admission fails before lease/native/receiver
mutation. Captured imported workflows retain their original common service and
owner context; mutable catalog content is not substituted during replay.

Authored inventories, not passing test counts:

- `tests/core/test_grpc_client.py`: 71 authored client/registry/type/security cases.
- `tests/contract/test_grpc_calls.py`: 17 per principal/backend, 68 across actual
  ADK/LangGraph and SQLite/PostgreSQL, including imported workflows, null/value/
  fault recovery, logical loop identity, deadline, uncertainty and ownership.
- `tests/contract/test_grpc_process_recovery.py`: 5 per principal/backend, 20
  across the same grid. An independent pinned grpc.aio peer retains a no-dedup
  business ledger. Actual producer termination and a different recovery PID
  preserve original v1 source/input/action key/absolute deadline/native session.
  Committed calls lacking a receipt remain pending without resend; known value/
  fault outcomes recover across the native-commit gap without another call.
  Missing archived binding and changed descriptors fail before mutation.
- `tests/core/test_internal_sandbox_jobs.py`: 21 authored supervisor/IPC/platform
  cases authored separately, including a real independently living supervisor,
  real exit codes, manager drain, original deadline/late-cancel race, stale
  controls, resource uncertainty, supervisor loss and no ambiguous relaunch.

Actual native SDK availability, persistent SQLite/actual PostgreSQL selection,
zero unexpected receiver/model calls, complete cleanup, source-frozen full-suite,
packaging/image/security and hosted evidence remain mandatory gates. These
profiles remain unchecked and unqualified; no broad gRPC conformance, streaming,
generic reconciliation, external supervisor or PostgreSQL background lifetime is
claimed.

[Pinned gRPC AsyncIO channel implementation](https://github.com/grpc/grpc/blob/3252a89f10d8e92997862167ca7d095ecda85973/src/python/grpcio/grpc/aio/_channel.py),
[official gRPC v1.84.0 release](https://github.com/grpc/grpc/releases/tag/v1.84.0),
and [ProtoJSON rules](https://protobuf.dev/programming-guides/json/) define the
reviewed client and encoding boundaries.

## Review corrections before the first behavioral batch

Independent source review found and corrected unsafe OpenAPI authority/framing
headers, HTTPX dot-segment route normalization, orphan business action indexes,
configured endpoint forwarding and original timeout propagation. Description
selection grants no permission to override the actual wire authority or endpoint.
The reviewed OWS1.0.3 OpenAPI table defines named parameters but no requestBody
mapping; required requestBody profiles remain explicitly rejected pending a
reviewed mapping, rather than receiving an invented public argument.

gRPC now rejects permissive protobuf byte/enum coercions in scalar, repeated and
map values, and rejects packed Any in the selected descriptor closure. Pure
evaluated-request validation precedes the business intent; dispatch repeats it.
The client's own named authentication retains its configured CA, while a foreign
profile cannot inherit that trust. IPC publication failures remove only the
owned temporary file and preserve original frames and one-time bootstrap history.

Background identity/dependency serialization normalizes only private dataclass
occurrence arrays, retaining strict validation of untrusted task input. Actual
manager/backend request validation checks transformed input sizes, operation,
runtime, executable availability and approved references before job acceptance.
Transient queue-scan failures retain bounded diagnostic categories and leave the
observer polling; an uncertain submission remains inspect-only.

The embedded supervisor retains read-only ordinary inspection. Physical launch,
Windows suspended-process resume and explicit control termination use a bounded
write reservation on the already-selected existing SQLite database, with no
adoption, bootstrap or evidence updates. Guards recheck the current generation,
DB clock, original source/input/deadline, submit nonce and control. Rejected
control does not terminate an independent live job; autonomous original deadline
remains effective after observer expiry. New gated real-process/SQLite tests
require these properties. The internal process boundary remains controlled,
and does not provide container/VM hard isolation.

All corrections and their authored tests remain unqualified until the coherent
source is frozen and every required behavioral, native, process, platform,
database, package, image, security and publication gate finishes successfully.
No source review, inventory count, parser or fake backend proves those gates.

[Pinned OWS1.0.3 OpenAPI call contract](https://github.com/open-workflow-specification/specification/blob/v1.0.3/dsl-reference.md#openapi-call)
is the public argument authority.

## First frozen batch and grouped corrections

Source `f2220334c90e0d853fb348817fea5cbb3f08b218` retained 979 unchanged tracked
inputs throughout its four local profiles. Root ended with 44 failures, 716
passes, 140 skips and two setup/teardown errors; ADK and LangGraph each ended
with 20 failures, 115 passes and 246 skips; optional Agent Framework ended with
59 passes and 358 skips. Five independent fresh builds passed. These results
do not qualify any unfinished profile. Original logs, commands, source manifest
and exit records are retained under `tmp/operation-batch-f222033-*` locally.

The coherent correction set preserves those failures. AsyncAPI positive fixtures
now avoid accidental YAML anchors, and oversize cases keep their actual sizes
with bounded parameter identifiers. Pure job fixtures use an available executable;
Kubernetes admission fixtures declare the network-policy prerequisite they are
testing. A genuine SQL NULL control remains NULL instead of being classified as
malformed; cancellation, malformed and oversize controls retain independent
read-only rejection witnesses. Corruptible shared PostgreSQL definition fixtures
have unique public source identities rather than damaging another fixture's
archive. Native checkpoint snapshots retain bytes through a typed SHA/length
representation and preserve before/after equality checks.

OpenAPI fixtures use valid public function arguments and an actual JSON null
response. Unknown dispatched outcomes retain structured pending assertions.
Shutdown after failed initialization is safe and has dedicated coverage. The
repository-owned CTK A2A listener now emits a correlated pinned typed Task wire
response; its expected public result follows that response. Upstream CTK material
and provenance remain unchanged, and fresh current-source CTK evidence is still
required. The corrected batch must finish before any passing claim replaces this
failed history.

The exact-source `67aa724` hosted checkpoint also failed: root had three failing
cases (one secret-safety A2A request missing its required message ID and two CTK
wire fixtures), both shared principal suites each had one legacy-scalar event
worker startup failure, PostgreSQL ADK had that same failure, PostgreSQL LangGraph
had that failure plus 16 manual invocation-state cases, optional Framework had
three CTK failures, and Windows timing had the legacy worker failure. Both standard
images, PostgreSQL common store, both PostgreSQL lifecycle suites, optional
Framework PostgreSQL recovery, other Windows profiles, audits, docs and external
controller checks passed; the aggregate checkpoint remains failed.

The legacy producer accidentally imported a current test helper that imports
TimerDispatcher, absent from its intentionally archived runtime. A local standard
library atomic writer removes that dependency; mode-specific sanitized diagnostics
retain original deadlines and checkpoints. Manual invocation-state tests opt into
the existing explicit delivery fixture so automatic dispatch cannot consume earlier
shared-database timers with a different test's model. Premature terminal execution
now reports bounded safe categories within the original ten-second fixture gate.
The secret-safety positive request supplies the pinned required message ID and
asserts a successful response before its original no-secret checks. All changes
require the next frozen behavioral and hosted batch.

The correction batch at `9570557316014542fe10bfbd2361f70e2968df05` froze
980 inputs. Root ended with 22 failures, 755 passes and 140 skips; optional
Framework ended with three failures, 251 passes and 429 skips. Both retained
unchanged-source evidence. Principal native profiles are still running; no
aggregate success is claimed. Five fresh independent builds passed again.

The next grouped source fixes add the missing process-limit prerequisite in the
pure Kubernetes policy fixture and finish the repository-owned A2A wire fixture
migration to `SendMessage` and version-1 text Parts. Actual supervisor handling
now distinguishes an early asyncio timer wake from a signaled control request,
preserving the same original autonomous deadline and independent process. Windows
supervisor-loss cleanup accepts stale-PID error 87 only after a fresh limited
process query proves absence or actual exit; live-process and unrelated errors
still fail. Real early-wake continuity and live-PID negative witnesses are authored.
These corrections remain unqualified until the next coherent batch.
