# Third-party source attribution

The runtime depends on [jq.py 1.12.0](https://github.com/mwilliamson/jq.py/tree/1.12.0),
licensed under BSD-2-Clause. Its wheels bundle [jq 1.8.2](https://github.com/jqlang/jq/tree/jq-1.8.2)
(MIT with bundled component notices) and Oniguruma (BSD-2-Clause).
Those dependency distributions retain their own licenses; no jq source is
copied into this repository. All dependency environments pin the same binding.

The canonical runtime keeps its existing MIT license. Material imported from
`bassemZohdy/open-workflow-agent-adk` at revision
`48db0cf5e1a1d3202cbbec6021736e1d89e94cd0` retains Apache-2.0:

- Exact snapshots: [migration/reference/open-workflow-agent-adk/LICENSE](migration/reference/open-workflow-agent-adk/LICENSE).
- Adapted fixtures: [tests/contract/fixtures/migrated-adk/LICENSE](tests/contract/fixtures/migrated-adk/LICENSE). Modified fixtures identify their source and changes.

No separate NOTICE file exists at that revision. Original source headers and
license bytes are preserved. Frozen snapshots are not runtime modules. No
generic-agent-lang source is copied. The approved specs-agent-platform reference publication also retains Apache-2.0: [license](migration/reference/specs-agent-platform/LICENSE), revision `a90b25a8c0d3518e1c0bd311f42621869b94a0a8`. All 109 tracked files remain exact snapshots; no separate NOTICE exists in that source tree.
