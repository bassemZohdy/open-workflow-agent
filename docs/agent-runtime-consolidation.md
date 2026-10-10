# Agent-runtime consolidation: bounded preparation

Baseline: OWA `15bcfba17b4327f927d0729c03c968718738f6db`; OSA `178039de5b4060d9a7a8b593fcbb479ee57eecb7`. These are pinned review baselines,
not a requirement to install old images. OWA remains the portable workflow
runtime; OSA remains managed agent definitions/catalog/lifecycle. This guide
does not activate a new compiler, engine, provider, protocol or release track.

## Source revisions and preservation

| Predecessor | Source revision | Files preserved at origin | Retirement blocker |
|---|---|---:|---|
| generic-agent-lang | `00dafe26f8ddc72f191ea585547972e892a7afff` | 102 | No detected license grant, unsafe approval prototype, API/role/model compatibility |
| generic-agent-adk | `9fb772d270f44bdf189e257fb912d798624ab629` | 122 | Native ADK graph/policy/evaluation and Live/persistence/consumer behavior |
| open-workflow-agent-adk | `48db0cf5e1a1d3202cbbec6021736e1d89e94cd0` | 284 | Distribution collision, CLI/import/registry/expression compatibility and existing PRs |

All original tracked file hashes/modes are recorded in each predecessor's
`docs/migration/source-manifest.json` proposal. Complete Git histories, remote
branches/tags/PR refs, local refs and the standalone local stash were preserved
and independently restored/ref-compared/fsck-checked. No original worktree was
updated. All three public predecessors remain active; no runtime code/fixture
copy, package rename, live installation, release or archive occurred.

No direct predecessor SDK import was found in the canonical OWA/OSA source
trees. A bounded scan of captured portfolio sources found only predecessor
self references and the canonical distribution's own references. External
packages/images/services and actual installations remain unknown.

## Preserve behavior before replacing implementations

| Legacy preset | Candidate OWA expression | Required reconciliation before migration |
|---|---|---|
| assistant | Generated one-task `agent:1.0.0@default` workflow | Provider/model selection, message/session identity, tool allowlists and response shape |
| pipeline | Explicit ordered `do` tasks | Per-role instructions/models/tools, prior-stage context, output keys and step limits |
| multi_perspective | `fork` followed by an explicit synthesis task | Stable branch outputs, joins, synthesis instruction/model and partial failures |
| refine_until_good | Bounded `for`/`switch` or subworkflow | Quality predicate, loop budget, retry versus refinement semantics and termination |
| expert_dispatch | Explicit `switch`/catalog calls | Route parsing, default/unknown route, fixed roster and per-role restrictions |
| team_coordinator | Explicit subworkflows/catalog operations | Dynamic delegation, worker ownership, recursion and cancellation; no native graph equivalence claimed |
| plan_and_execute | Explicit planner result plus bounded workflow steps | Plan validation, iteration budget, executor context and forbidden operations |
| approval_gate | Durable approval service plus `emit`/`listen` and approved-only branch | Protected operator identity, pending/unknown/rejected/expired refusal, restart/replay, idempotency and zero side effects before approval |


The existing [portable fixture suite](../tests/contract/test_contract.py),
[tools contract](../tests/contract/test_tools.py),
[sandbox contract](../tests/contract/test_sandbox_contract.py), and
[durable approval replay test](../tests/contract/test_durable_approvals.py)
are useful target evidence, not proof that predecessor presets/APIs are ported.
Do not call similar preset names or parsed YAML equivalent runtime behavior.

The generic ADK compiler constructs native Workflow/FunctionNode/JoinNode
graphs, whereas current OWA executes the common immutable plan inside engine
envelopes. Native lowering is not introduced by this preparation. Retain those
fixtures with their original Apache provenance until an accepted translation or
explicit evaluation/reference scope exists.

## Approval exclusion

generic-agent-lang's `HumanInLoopStrategy` defaults a missing status to approved
and routes unknown states to completion; `request_approval` only returns text.
Neither implementation may become a real canonical approval mechanism.
Use the existing [protected approval service](api.md#approvals-human-in-the-loop)
and prove pause, operator binding, denial/expiry, restart/replay and side-effect
idempotency on both engines. A prompt or state field is not authorization.
The source approval files stay at origin with an explicit exclusion disposition.

## Distribution and license gates

Standalone Apache-2.0 `open-workflow-agent-adk` v0.2.1 imports `openworkflow_adk`;
the canonical MIT engine of that distribution name v0.2.0 imports
`open_workflow_agent_adk`. Choose a distinctly named compatibility distribution
preserving the old API/CLIs or an intentionally breaking API retirement, then
settle unambiguous package/image ownership and consumer instructions. There is
no package rename/release in this stage. Standalone PR15 still requires its
Dependency Review setting to be resolved; no setting or check is bypassed.

Keep Apache license/provenance with later eligible ports. generic-agent-lang
has no detected license grant, so source/fixture copying waits for rights and
license disposition. This guide creates no new grant and copies no runtime code.

## Phases, stop criteria and rollback

1. **Prepared:** pinned source indices, honest approval warning, full-ref backups
   and candidate mappings. Draft documentation PRs are reviewable; source remains
   active and canonical executable behavior is unchanged.
2. **Pending:** choose legacy CLI/Live/native graph support scope and package
   ownership; identify actual consumer/storage coordinates and rights.
3. **Pending:** selectively port eligible fixtures/adapters under existing public
   contracts. Accept each only after equivalent outputs/errors, role/model/tool
   restrictions, auth, replay and safe approval behavior reconcile on both engines.
   No broad redesign or new optional product track is implied.
4. **Pending:** cut over actual consumers with an immutable rollback coordinate
   and data reconciliation plan. Reversible archive requires all unique content,
   collaboration/refs, consumer/write dependencies and migration gates satisfied.
   Permanent deletion is excluded. Archive alone is not a consumer cutover.

Stop on an unclassified unique file, unresolved rights, missing fixture parity,
unknown consumer write/storage dependency or failing required check. No fake
approval or skipped security check can close a gate. Revert these documentation
commits to roll back this stage; installed runtimes and live data are unchanged.
For later live cutover, verify database/write reconciliation as well as restoring
the old image/package; an old snapshot alone cannot account for new writes.
