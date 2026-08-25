# Portable Profile CTK subset

These Gherkin files are selected from the upstream Open Workflow CTK at commit
`2dd2c84170d5f3e05d58e913e9ca298dcf8d543a` (the repository head used during
integration). The adapter executes the upstream `do`, `set`, `switch`, `for`,
`branch`, and `raise` scenarios through both engine packages and compares
their declared outputs, fault details, property-count assertions, and
task-order assertions.

This is an applicable Portable Profile subset, not a claim of full Open
Workflow conformance. CTK scenarios requiring emit, flow, HTTP fixtures,
OpenAPI, or other capabilities outside the current profile remain excluded
until their runtime contracts are implemented.

The selected subset passed for both engines in GitHub Actions run
[`32816720537`](https://github.com/bassemZohdy/open-workflow-agent/actions/runs/32816720537). Each engine job uploads test output plus provenance containing
the repository commit, this pinned upstream CTK commit, and SHA-256 hashes for
the selected scenario files. Further expansion is deferred to later backlog
items and must remain limited to scenarios supported by the declared Portable
Profile.
