# Natural-language workflow generation

`generate_workflow(prompt, generator=...)` accepts an application-provided LLM
or text generator, requests a JSON workflow, validates it with the OpenWorkflow
loader, and compiles it before returning. Generation is injectable so offline
tests can use a deterministic function and production deployments can select
their own provider.

The generated result is never treated as executable until both schema
validation and translation succeed; malformed or uncompilable output raises
`WorkflowGenerationError`.
