# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

This repo builds a containerized (Docker) implementation that translates [OpenWorkflow Specification](https://open-workflow-specification.org/) documents (YAML/JSON) into runnable agentic components using Google's [ADK](https://github.com/google/adk-python) (`google-adk`, Python 3.10+, `pip install google-adk`).

The core idea: OpenWorkflow's native `do` task list (`call`, `run`, `switch`, `fork`, `try`/`catch`, `wait`, `emit`/`listen`, etc.) is extended with embedded agent-characteristics configuration per task (model, instructions, tools, agent vs. deterministic node), so a standard OpenWorkflow file can declare which tasks are LLM agents and how they're built. ADK-specific config lives in OpenWorkflow-compatible metadata containers:

- Task-level agent config: `task.metadata.adk.agent`
- Task-level self-heal config: `task.metadata.adk.self_heal`
- Project-level registries: `document.metadata.adk.models`, `document.metadata.adk.providers`, `document.metadata.adk.memories`

Tasks without agent config fall back to project-wide defaults.

- Spec baseline: OpenWorkflow v1.0.3 — schema at `https://open-workflow-specification.org/schemas/1.0.3/workflow.yaml` (and `.json`). Document structure: top-level `document` (dsl version, namespace, name, version) + `do` (the task list). Since this project *extends* the spec, expect divergence over time — see the `spec-drift-check` skill.
- Runtime mapping: ADK's `Agent` (instructions/tools/model for one agentic unit) and `Workflow` (graph-based orchestration) classes are the translation target for OpenWorkflow's `do` tasks.

## Configuration

Config resolution follows a layered, Spring-Boot-style precedence (highest wins):

1. Environment variables (externalized, container-friendly — e.g. `WORKFLOW_AGENT__MODEL`, using `__` as the nesting separator)
2. Per-task agent config embedded in the workflow YAML/JSON (`task.metadata.adk.agent`)
3. Project-wide default values and named registries (`document.metadata.adk`) (baked-in fallback when a task omits agent config)

When adding new configurable agent characteristics, wire all three layers rather than hardcoding a single source.

## Stack

- Language: Python (google-adk requires 3.10+)
- Deployment target: Docker container
