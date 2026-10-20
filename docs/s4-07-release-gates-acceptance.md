# S4-07 release gates and publication evidence

Status: workflow guards are authored and unvalidated as a combined source
batch. S4-07 remains open. Publication follows successful main-push CI and
requires successful PostgreSQL, External Sandbox, Security, and Docs runs for
the same commit. Missing runs remain pending until timeout; failed runs stop
publication. Agent Framework remains an optional evaluation adapter and is not
a production image or release gate.

Security and Docs run on every main push so an exact-head result is available
to the gate. Their pull-request triggers remain scoped to their normal
validation needs. A documentation-only main commit does not start publication
by itself because Release is triggered by successful CI.

Before publication, the Release workflow checks that root, core, ADK, LangGraph,
sandbox-controller, and Kubernetes sandbox-controller package versions match.
Tests read the package metadata and assert that every published package is
covered by the workflow check. All eight runtime/controller image and
architecture pairs must pass vulnerability preflight before any push; the
workflow retains SBOM, provenance, and attestation steps. Stale-main checks
before and after acceptance, plus serialized release concurrency, prevent an
older commit from publishing current tags. Formal GitHub release creation
waits for both runtime and controller publication.

Final acceptance must validate the Release workflow, exact-head gate selectors,
all required hosted workflows, package/image metadata, and rollback records on
one unchanged source commit. This record does not claim a release, production
deployment, or successful qualification for the current source. It does not
change repository rulesets, environment protection, support policy, or public
isolation guarantees.
