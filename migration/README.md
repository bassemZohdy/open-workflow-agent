# Consolidated predecessor material

`open-workflow-agent` is the canonical implementation repository.

- `reference/open-workflow-agent-adk/` preserves all 284 tracked files at the pinned revision, with the original Apache-2.0 license and exact source bytes. The `.source` suffix keeps old Python modules, tests, configuration and CI files out of active execution and discovery. Read the original path from `source-manifest.json`.
- `generic-agent-lang` is preserved at its original repository and indexed here. It has no detected license grant; its code is not copied. Independently written portable recipes are in `resources/patterns/`.
- `specs-agent-platform` is private. Its locally reviewed compiler material is not published here until private-to-public transfer is approved.

Use [the staged backlog](../TODO.md) to turn selected references into tested canonical implementations. Do not install the reference tree or advertise it as an integrated engine. Old expression engines, direct execution paths, history/checkpoint implementations and package identities must not replace canonical contracts automatically.

Apache reference provenance: `bassemZohdy/open-workflow-agent-adk`, revision `48db0cf5e1a1d3202cbbec6021736e1d89e94cd0`. The source has no separate NOTICE file at this revision. Its license and source headers remain intact. Future modified ports need their own change notices and applicable attribution.

Archive status is pending. Do not remove origin repositories or original refs. The manifest records the reviewed revision, file hashes and preserved refs; it does not claim that external consumers have migrated.
