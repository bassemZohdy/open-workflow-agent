# Portable Profile CTK subset

These Gherkin files are selected from the upstream Open Workflow CTK at commit
`2dd2c84170d5f3e05d58e913e9ca298dcf8d543a` (the repository head used during
integration). The adapter executes the upstream `do`, `set`, `switch`, and
`for` scenarios through both engine packages and compares their declared
outputs and task-order assertions.

This is an applicable Portable Profile subset, not a claim of full Open
Workflow conformance. CTK scenarios requiring emit, flow, HTTP fixtures,
OpenAPI, or other capabilities outside the current profile remain excluded
until their runtime contracts are implemented.
