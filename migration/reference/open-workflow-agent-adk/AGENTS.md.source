# AGENTS.md

Guidance for AI coding agents. `CLAUDE.md` holds the architectural baseline
(spec version, ADK mapping, config-layering precedence, stack) — read it first;
this file only adds what it doesn't already cover.

## Status: v0.2.1 delivered; tracked backlog verified complete

The v1 workflow runtime, production hardening, tests, Docker support, CI, and
release packaging are implemented. The tracked backlog in [`TODO.md`](TODO.md)
has been completed and archived; future protocol adapters are recorded there
as explicitly uncommitted work.

## Python is auto-formatted on every edit

`.claude/settings.json` wires a PostToolUse hook that runs
`uvx ruff format` + `uvx ruff check --fix` on every `.py` Write/Edit.
- Don't hand-format; the hook rewrites the file behind your edit.
- Re-read a file after editing if you need its final shape.
- Hook errors are swallowed (`|| true`) — ruff findings that can't be
  autofixed won't surface unless you run `uvx ruff check` yourself.

Ruff config (`pyproject.toml`): line-length 100, target py310, rules `E,F,I,UP`.

The hook is written for a Unix shell (`jq`, `tr`, `case…esac`); on Windows it must
run through Git Bash or WSL. If the hook is silent or `ruff` is not found, verify
that the shell environment exposes those utilities.

## Tooling

`uv`/`uvx` is the expected runner (the format hook depends on it). Use the
checked-in lockfile for reproducible environments and consult `pyproject.toml`
for the supported Python versions and optional dependency groups.

## Before extending the OpenWorkflow schema

Run the `spec-drift-check` skill first — the project intentionally diverges
from upstream (baseline v1.0.3), so verify upstream hasn't moved before adding
task or agent-characteristics semantics.
