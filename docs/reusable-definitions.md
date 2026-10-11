# Reusable definitions and resource identity

The unmodified Open Workflow 1.0.3 schema admits workflow-owned reusable
functions, errors, retry policies and timeouts. Source definitions retain
named references. Core derives separate immutable execution definitions;
these remain internal. Source copies and derived JSON copies cannot mutate
the plan. Named references must resolve during admission, before invocation
or catalog fetches. Unused functions are validated too.

`use.functions` contains ordinary OWS tasks, including nested task scopes.
A call evaluates its own input transformation and `with` parameters first.
Those parameters become the reusable task's raw input; omitting `with`
passes the caller's transformed input. The reusable task then executes the
common input/body/output/export pipeline. Its context export is available
to the caller's output/export pipeline. Scope exit returns from the function;
workflow end retains its workflow-wide behavior. Caller and reusable task
descriptors retain their original definition pointers, including escaped
names. Native checkpoint scopes distinguish individual call sites; operation
identities also distinguish calls sharing the same reusable definition.

Function bodies cannot shadow standard protocol calls or versioned catalog
calls. Recursive reusable functions fail admission. Function depth is limited
to 32 and derived expansion to 10,000 tasks; capabilities report both guards.
Recursive child workflows remain a separate S2-07 track. Arbitrary host/Python
imports and arbitrary URI function imports are disabled. Authentication,
secrets and extensions remain rejected until their security/extension stages.

Named errors resolve from `use.errors`; their expressions run in the raising
task's scope, and `instance` identifies the actual raising task. Named
`catch.retry` and task/workflow timeouts resolve from `use.retries` and
`use.timeouts`. Resolution does not qualify the later complete retry,
deadline or recovery work in S2-03/S2-04.

Inline JSON Schema uses Draft 2020-12. Fragment pointers, escaped/percent
encoded tokens, anchors, dynamic anchors and nested inline resource identifiers
are checked for a complete network-free closure during admission. Missing or
non-schema targets fail admission. Runtime validation has no remote retriever.
Fields named `$ref` in schema `const`/examples/default data remain ordinary
data. External schema resources/references and other dialects are rejected.

Deployment-trusted external catalogs still require HTTPS, allowed hosts,
bounded responses/timeouts, TLS verification, no redirects and deployment
credential references. Catalog versions are exact SemVer 2.0 pins, including
prerelease/build identifiers; ranges, floating versions and malformed pins are
rejected. Resolution inspects actual task call slots, including reusable task
bodies, without treating application text as imports.

The asynchronous workflow and sandbox compilers capture each resolved resource's
immutable content, URI and digest in the internal plan. Workflow fingerprints
include that closure. Compiling another workflow cannot replace a captured
function body; execution rechecks current deployment trust/integrity policy.
Synchronous compilation without resolution retains its existing catalog
registration contract. Catalog imports currently allow supported protocol
`call`/`with` bodies only; task modifiers, executable code, nested imports and
unimplemented protocols fail explicitly. Their wider adapter work remains
S3-03. Changed imported content produces a different fingerprint; automatic
in-flight content migration remains S2-09.

The local workflow registry selects `latest` by SemVer precedence, independent
of registration order. Build metadata does not affect precedence, so ties
require an explicit version. A registered namespace/name/version cannot be
overwritten with different executable content. A2A skill name selection still
requires a unique registered name.

Validation lives in the shared reusable-definition, catalog-binding and
schema-reference-closure fixtures, local catalog tests and native LangGraph
restart tests. Independent JS, proposed inventory tests and full OWS
conformance remain unqualified.
