# Development cleanup review — 2026-10-25

The initial cleanup was a repository review. Later runtime repairs and their
validation are linked below. Existing local test/documentation edits were
preserved; the official schema, dependency locks and feature-completion states
are unchanged.

## Cleanup

- Gate every validation job behind manual dispatch or `OWA_AUTOMATIC_CI=true`.
  Keep all test selections, schedules and dependency gates available for reuse.
- Gate automatic publication separately behind `OWA_AUTOMATIC_RELEASES=true`,
  also requiring automatic CI to be enabled so skipped checks cannot qualify a
  release. Preserve successful-main-push and exact-source acceptance requirements.
- Add manual dispatch to Docs, matching the other validation workflows.
- Ignore mutation output. The staged `src` layout is deliberate, not an obsolete
  runtime path. Leave the temporary source copy visible until it is removed as
  documented: ignoring `/src/` also excludes nested packages from Hatch builds.
- Document local feedback commands and how to restore automatic validation.

## What remains missing

The initial review counted 20 checked tasks and 17 open tasks. The later
`d72781f` qualification closes eight finite scopes; the authoritative
[backlog](../TODO.md) now has 28 checked tasks and nine open tasks (six ready,
three conditional).
Task counts describe accepted scopes, not full protocol conformance. Work already
authored but awaiting qualification must not be mistaken for missing source code.

| Scope | Remaining work or evidence |
| --- | --- |
| S3-03, next ordered task | Finish operation-adapter acceptance: HTTP/OpenAPI/gRPC, functions and background-operation platform/backend recovery. Retain and diagnose intermittent native resume failures. |
| S3-04–S3-06 | Qualify executable sandbox portability, protected-approval recovery/queue limits and identity/security boundaries across required adapters and backends. |
| S3-07–S3-08 | Qualify all eight agent patterns and selected migrated adapters; consolidate unrun or partially run regressions. |
| S4-01–S4-02 | Complete native extension coverage and advanced channels/reducers, Command/Send, streams, updates, replay and time travel. Current channel slices are narrower than this scope. |
| S4-03 | Implement JavaScript workflow compilation/execution and independently prove parity. Its schema loader/native scaffold is not an OWS execution adapter. |
| S4-04 | Map and execute official CTK/proposed coverage: the bridge retains 443 proposed tests and 774 schema constraints as unmapped/unexecuted. Integrity checks are not conformance evidence. |
| S4-05–S4-07 | Complete durable history/tracing/provenance, benchmark qualification and versioned release/rollback acceptance. Local success does not replace skipped or pending hosted/backend gates. |
| S4-08 | The later canonical/no-shim, separate-environment migration decision and disabled index-publication policy are now recorded. Full-source qualification remains required; future index publication still needs verified ownership/version/consumer evidence. |
| S4-09–S4-11 | Conditional work: Agent Framework production hardening, external event delivery and multi-tenancy/delegated identity need concrete requirements before activation. |

The existing working-tree records describe a 5,004-pass local batch, with 3,206
skips and 90.54% core coverage. Those are historical records, not results from
this cleanup. Fresh exact-source qualification remains necessary. Continue
S3-03 first, using its [acceptance plan](s3-03-operation-adapters-acceptance.md);
do not restart completed milestones or mark conditional work as a blocker.

Automation settings only affect GitHub after these changes are pushed. Repository
variables and branch protection were not changed remotely, and existing runs
were not cancelled.

## Cleanup validation

- The broader `tests/ci` suite passed: 182 tests.
- The final automation, validation-workflow and release-workflow selection
  passed: 22 tests, including the new development automation guards.
- Repository Ruff checks, new-test formatting and relative Markdown links passed.
- No engine/runtime implementation changed; native engine, PostgreSQL and
  container acceptance were not rerun for this cleanup.

Follow-up on 2026-10-25: the `/src/` ignore added during cleanup produced an
empty standalone core package. It was removed during S3-03 package validation,
and the build verifier now rejects metadata-only wheels. See the
[operation-adapter follow-up](s3-03-operation-adapters-acceptance.md#local-reference-correction--2026-10-25)
for the fresh validation scope; the cleanup results above remain historical.

Further cleanup removed 29 generated Python/lint/type/test cache directories and
shortened the current project summary while preserving historical evidence and
all task states. The old coverage database is archived with a cleanup manifest
under `tmp/development-cleanup-20261025/`. Dependency environments, source and
frozen migration material are retained. Local adapter installs containing the
earlier metadata-only wheel were
rebuilt in their independent environments. The production Docker build now
reuses downloaded embedding assets after application-source edits. Runtime
reference/admission repairs and their new validation are tracked in the
[encoded-reference follow-up](s3-03-operation-adapters-acceptance.md#encoded-reference-and-host-admission-follow-up--2026-10-25),
separately from the automation-only checks above.

Temporary test containers and the disposable cache-probe image are removed.
The loopback-bound PostgreSQL fixture and useful development images remain
available. Final Docker reports, source hashes and image identities are retained
under `tmp/reference-qualification-20261025/`; no remote publication was performed.
