# Open Workflow Agent

This repository is being initialized as a configuration-driven, model-agnostic agent and workflow platform with interchangeable ADK and LangGraph engines.

`Project Definition.md` is the authoritative project specification. The current tree is scaffolding only; implementation should follow its milestone order, beginning with framework-neutral core contracts.

Planned areas are `core/`, `engines/`, `runtime-catalog/`, `resources/`, `docker/`, `examples/`, and layered `tests/`. ADK and LangGraph will be packaged as separate images while sharing the same public configuration and portable workflow contract.
