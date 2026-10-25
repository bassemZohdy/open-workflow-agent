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
`d72781f` and `f66cd23` qualifications verify eleven finite scopes. The
[backlog](../TODO.md) now has **31 checked and six open tasks**: three ready and
three user-kept conditional tracks. These counts are accepted task scopes,
not full protocol or inventory conformance.

| Scope | Remaining work or evidence |
| --- | --- |
| S4-02 | Native memory, streams, state updates, replay and time travel; retain the finite channel/Command/Send and fork-recovery evidence. |
| S4-03 | Full JavaScript engine identity, durability, expressions, calls and services; the literal component is narrower. |
| S4-04 | Full CTK/proposed/schema/parity mapping and execution; all 443 proposed and 774 schema rows remain unverified. |
| S4-09–S4-11 | Framework production, external event consumer/transport and delegation/multi-tenancy remain conditional. |

The [main integration record](validation-history/main-integration-2026-10-25.md)
separates the completed task baseline from current partial hosted qualification
and the final process-fixture repair. Next work is S4-02 native memory.
Repository variables and branch protection were not changed. Automatic validation
and publication remain separately opt-in.

## Progress-audit actions

The development loop now keeps one feature active, runs focused checks before
one coherent full qualification, and freezes runtime source during recovery
tests. The owned fork wait/end fix has deterministic regression coverage; its
optional-store test profiles are explicit. Earlier failures remain in the
[native acceptance record](s4-02-native-channels-acceptance.md#owned-waitend-and-optional-store-follow-up--2026-10-25).

Both principal Dockerfiles now declare source/version labels after filesystem
preparation. Previously changing `VCS_REF` invalidated the OS upgrade layer:
the `bc87e0d` build spent about 119 seconds repeating it. Security updates,
version/source labels and publication gates remain required. At source
`b37e49fc668672cfbca9492479bf4bb2f431f041`, actual label-only rebuilds reused
that layer on both engines and took **10.77 seconds (ADK) / 11.95 seconds
(LangGraph)**. The initial builds took about 288 seconds. These measurements
compare a label-only probe with its initial build; they do not promise the same
speed for application/dependency changes. Both source-labeled images pass the
packaged JavaScript checks and retain version `0.2.0` and the full source SHA.
The probe images deliberately use revision `unknown` and are not release
artifacts. Reports are under `tmp/javascript-sandbox-20261025/b37e49f/`.

Reuse independent test dependency images only after checking their lock and
package metadata match; record the actual source mount and dependency image
separately. Keep source mounts read-only during recovery checks, and serialize
environment synchronization. The final source still needs its own hosted and
packaged-image gates. This avoids reinstalling identical tools for each edit
without describing an older image as a newly built runtime.

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
