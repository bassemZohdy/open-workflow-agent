# Open Workflow 1.0.3 Schema Source

The normative schema is maintained by the Open Workflow Specification project:

<https://github.com/open-workflow-specification/specification/blob/2dd2c84170d5f3e05d58e913e9ca298dcf8d543a/schema/workflow.yaml>

This repository vendors the unmodified schema from upstream commit `2dd2c84170d5f3e05d58e913e9ca298dcf8d543a`.

Vendored file SHA-256:

`704EF5E91C5D823167DD8751794EDB1DD1A6F9A3BDF9BFD389BF9C6B23AE3816`

The standalone `core` distribution carries a byte-identical package copy so it remains runnable outside the monorepo.

Release status reviewed 2026-10-11: upstream `v1.0.3` is a prerelease at
`9b5b1da29e9d4fff2358580241e11aab22704a16`. The vendored schema is a later,
unmodified upstream snapshot, not that tag's schema. The existing DSL pin is
an explicit exception to the stable-release policy. No schema was changed in
the S1-01 review. Runtime schema overrides must match the digest above.

The runtime applies this schema first and then applies a separate OWA Portable Profile capability gate. It must not be described as full Open Workflow conformance until the applicable CTK scenarios pass.
