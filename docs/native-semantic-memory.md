# Local semantic memory

The opt-in `langgraph.memory.semantic@1.0.0` source profile adds semantic search
to the [configured native memory boundary](native-memory.md). It uses the
packaged MiniLM CPU model and the native SDK's SQLite vector/pgvector stores.
There is no external embedding API, runtime model download or startup package
installation. SQLite remains the embedded default. PostgreSQL additionally
requires a deployment with the `vector` extension available to its runtime
schema; ordinary PostgreSQL without that extension cannot advertise this profile.
This source profile is not yet in a published image. S4-02 remains open.

Mount [semantic-memory.yaml](../examples/semantic-memory.yaml) as the runtime
configuration and [the workflow](../examples/semantic-memory-workflow.yaml) at
its configured path. Supply `OWA_SEMANTIC_MEMORY_TOKEN` through deployment secrets
and retain `/data`. Invoke with `{}` and the configured bearer token. The
workflow stores two notes and returns the espresso note as the closest match
for coffee. Each result contains only `key`, `value` and a finite cosine `score`.
User/session labels, graph threads and checkpoints do not select the namespace.

## Explicit model and index contract

Add `semantic` to a native binding with these settings:

| Setting | Default | Supported boundary |
| --- | --- | --- |
| `profile` | `local-minilm-v1` | Exact profile; other models/providers are rejected |
| `field` | `text` | One top-level field; bounded ASCII identifier, 1–64 characters |
| `max_text_bytes` | 4,096 | Nonempty UTF-8 text, at most 16,384 bytes |
| `timeout_seconds` | 10 | Positive finite inference/admission deadline, at most 30 seconds |
| `artifact_sha256` | resolved from installed artifacts | Optional exact deployment pin |

The model is `sentence-transformers/all-MiniLM-L6-v2`, using 384 finite,
normalized dimensions, cosine distance and exact search. Index records and
queries bind the actual model/tokenizer files and installed inference library
versions, rather than trusting a model revision label. The loader hashes the
active local snapshot before and after admission, restricts its resolved paths,
and bounds file count, directory entries and bytes. CPU inference uses one model
thread; bindings in one installation share the model and inference slot.

Admission rejects missing/corrupt local artifacts and a mismatched explicit pin
before opening stores or starting background dispatch. The resolved identity is
included in common mutation/query evidence, provider storage identity, the
original recovery profile and a durable native index binding. A missing, changed
or oversized native binding record invalidates capability and cached reuse.

Use a new binding ID and empty collection when adopting this profile or changing
model/index identity. Existing unindexed records are not silently reinterpreted
or backfilled. A changed model/library fingerprint requires an explicit future
migration or a new binding/collection; it cannot resume the old index under a new
meaning. The SDK vector migration formats stay native. The pinned PostgreSQL
async setup needs an adapter correction to honor its existing conditional skip
of the approximate-index migration for exact search. No SDK SQL is replaced by
an application vector engine.

## Versioned workflow calls

Calls on an indexed binding must require both `langgraph.memory@1.0.0` and
`langgraph.memory.semantic@1.0.0` in the standard OWS metadata declaration.
This uses the official 1.0.3 schema unchanged. Missing declarations and unknown
arguments fail admission; ADK and minimal installations reject the capability.

Writes retain `memory.put:1.0.0@<binding>` and require an object containing the
configured nonempty text field. Get/delete/sweep retain their existing versions.
`memory.search:1.0.0@<binding>` remains filtered key-order search.
Semantic calls explicitly use `memory.search:1.1.0@<binding>`:

```json
{"query":"coffee drink","filter":{"kind":"note"},"limit":5}
```

`query` is required bounded text. `filter` and `limit` retain the bounded scalar
equality and deployment limits of filtered search. Operators, offsets, caller
vectors, field/model selectors, namespaces and provider locations are rejected.
Both stores apply exact namespace, strict scalar filters and expiry before their
candidate limits. Ranking uses native cosine scores with key order for equal
scores. PostgreSQL exact search avoids an approximate-index access path even
when another index exists. Returned scores clamp floating-point roundoff to
[-1, 1]. The entire projected result must fit the binding's record-byte bound.

Every call checks original principal authority. A first committed observation
retains its value, scores and absence across later writes, expiry and restart;
cached replay does not run the model or search again. Knowledge, portable agent
memory tools, session state and checkpoints keep their separate contracts.

## Writes and interrupted execution

Inference finishes before the owned write transaction starts. The pinned SDK
then writes the value and precomputed vector in the same fenced transaction as
the native marker. It never calls a model while holding that write transaction.
Delete and bounded expiry cleanup remove their native vectors as part of the
same transaction. Recovery uses retained common evidence and native markers;
an old interrupted write cannot overwrite a newer value or embedding.

Dispatched operations without proof pause and use the existing authenticated
[write-resolution controls](native-memory.md#resolve-a-paused-write). Completion
requires native proof; explicit abandonment records an unknown outcome and
never repeats SDK SQL. Permissions, immutable decisions and terminal invocation
limits are unchanged.

Inference deadlines cancel the caller's wait but cannot kill native CPU work.
Cancellation retains the slot and drains the worker before returning, including
repeated cancellation, so it cannot create an unbounded background inference
queue. Drain time may exceed the nominal deadline. These controls are not a
hard process-memory or isolation boundary. Record/namespace limits still refer
to logical serialized values; vector storage has a fixed 384-dimension cost per
indexed record under the same live-record ceiling.

The [acceptance record](s4-02-native-memory-acceptance.md) distinguishes the
finite qualified profile from broader T51/T68, JavaScript parity, other models,
online reindexing and the remaining S4-02 features. No research row or whole
task is completed merely by this source profile.
