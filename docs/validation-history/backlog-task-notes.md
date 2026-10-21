# Detailed backlog acceptance notes

Task scope and completion remain authoritative in [TODO.md](../../TODO.md). Earlier detailed notes are retained with relocated local links and UTF-8 encoding.

## S2-03

- [x] **S2-03** (L) — Prove native checkpoint recovery and reconcile native state with common invocation/side-effect records.
  JSON resume-value preservation is verified at `f2943c6`; original-input and
  terminal-result recovery at `6840605`, and journal storage at `d197fa1`.
  Leaf-call action receipts and corrected dependency validation are verified at
  `fc53574`; conditional invocation writes passed all gates at `6954db8`.
  Initial native/input-binding admission passed all gates at `b9a2b31`;
  eight forced process-stop cases per SDK/common bridge passed SQLite/PostgreSQL
  acceptance at `ef29f23`. Private expiring ownership storage at `75fbc0d`
  passed all gates at `cdd6e17`. Runtime admission and atomic common/native
  checkpoint fencing at `b2dfc5c` pass local, Windows, native and PostgreSQL
  suites; both main CI image restart gates fail immediate resume while the
  stopped owner's lease is live. Expiry-aware image acceptance and action/fork
  generation guards passed all seven gates at `2acf632`, including both actual
  forced-stop runtime images. Root/nested LangGraph corruption checks at
  `556da47` pass local native/shared/CTK, Windows and image regression at
  `fc54600`; its PostgreSQL corruption fixture fails before changing SDK state.
  The corrected fixture and ADK all-input-event validation at `005ab5b` pass
  local checks and full ADK regression; all seven gates pass at `4058895`, with
  127 configured LangGraph and 108 ADK recovery cases. Boolean terminal controls
  contradicting native routing are rejected at `e61c6a5`; full local LangGraph
  regression passes; `6af95cf` routing/PostgreSQL/push image gates pass, while
  PR CI fails its ADK storage benchmark. Source `9755e96` binds native progress
  to common receipts and drains ADK SQLite reads through the existing worker.
  Shared root/nested value/null/fault and waiting-fork receipt checks pass on both
  principal SDKs. Full frozen core/LangGraph/optional regression passes; final
  ADK native/shared/CTK passes 808/366/three tracked gaps and its benchmark passes.
  Missing-receipt assertions fail on prior source on both SDKs. All seven
  `c276f40` gates pass, including both main CI force-stop images and PostgreSQL
  159 LangGraph/134 ADK recovery cases. Source `968ec8f` binds completed scope
  checkpoints to later native progress. Deleted or consistently changed child
  checkpoints reject recovery; a cancelled child without its first checkpoint
  remains valid. Focus passes 65/117 and full LangGraph native/shared/CTK passes
  850/357/three tracked gaps. All seven `ac478e1` jobs succeed, but their
  PostgreSQL filter deselects all nine completed-scope cases; those cases were
  unqualified at that revision. Source `3df61f6` corrects the selector and checks
  actual collection.
  Common sandbox receipts preserve known script/shell/container outcomes across
  native recovery and stop changed requests or uncertain intents before another
  dispatch. Frozen full core passes 1,583/546/three tracked gaps with 91.50%
  coverage; ADK 844/420, LangGraph 886/393 and optional Agent Framework
  1,097/592 each retain three tracked gaps. Focus passes 49/36 on each principal
  SDK. All seven `c7b70ad` jobs pass; PostgreSQL actually executes all nine scope
  cases and eighteen sandbox cases per adapter, with 186 LangGraph/152 ADK
  shared recovery passes and both runtime images. Source `5506277` additionally
  records event and synchronous child outcomes before native progress; both
  focused suites pass 70/64. Full frozen core passes 1,625/586
  with 91.58% coverage; ADK 884/460, LangGraph 926/433 and optional
  Agent Framework 1,157/652,
  each with three tracked gaps. A completed child's missing common invocation
  record is accepted at that source in actual SDK probes. All seven `33cd41f`
  jobs subsequently pass, including twenty configured event/child cases per
  adapter and both PostgreSQL/main CI images. Source `e39a7c0` binds terminal
  child records/input to prepared requests and admits an action identity index
  atomically with intents. Pre-admission checks reconcile associated receipts
  and descendant actions even before native task progress saves a receipt link.
  Missing/changed child records, input, execution references, descendant receipts
  and index metadata now reject before parent mutation or dispatch. Focus passes
  77/74 per principal SDK; storage/selection checks pass 52/9. Full frozen
  core passes 1,664/621/three tracked gaps with 91.67% coverage;
  ADK 918/494, LangGraph 960/467, optional Agent Framework 1,208/703, each with three gaps.
  At `29c681d`, PR CI and five other configured runs pass, including all
  seventeen PostgreSQL child-dependency cases per adapter and atomic rollback.
  Push CI is cancelled at its Windows time limit after two process-stop failures;
  it is not an all-seven-success artifact. The worker now requires a committed
  terminal marker, uses a five-second test lease and retains production settings;
  the Windows job is bounded at twenty minutes. Process-stop focus passes 8/24
  per SDK. Final acceptance at `ddd194e` passes all seven configured runs,
  both Windows suites, both main and PostgreSQL runtime images and both native
  benchmarks. All ten scoped [recovery requirements](../s2-03-recovery-acceptance.md)
  are verified; later-stage features retain their owners.
  See [acceptance evidence](../../PROJECT.md#s2-03-checkpoint-recovery--2026-10-12-verified).

## S2-04

- [x] **S2-04** (M) — Complete retry predicates/backoff/jitter, task/workflow timeouts, deadlines and exception handling.
  Error-variable/status binding and common provider error replay are corrected
  at `23b9d35`; native/portable/recovery focus passes 95/29 per SDK, and the
  final error fixture passes 19/15 per environment. All seven `b6aef8c` gates
  qualify that block, including five PostgreSQL provider cases per SDK.
  Runtime timing at `f2b5c02` passes 85/39 timing/recovery and 93/26 lifecycle/
  fork checks per SDK. Jitter and duration fields now operate during a run.
  All seven `cbb06b2` gates qualify runtime timing, with eleven PostgreSQL
  timing cases per SDK. Original workflow/task deadline anchors at `17d4197`
  pass 49/61 focus and 124/167 broader recovery checks per SDK; eight actual
  deadline cases cover expiry/remaining time and missing/changed records.
  `fdd771e` fails four fixture gates and is not all-seven success. Source
  `a0ee9c0` completes saved retry decisions, standard/scoped errors and fixture
  corrections. Actual process stops pass 3/9 per SDK; full frozen core passes
  1,732/728/three gaps at 91.61%, ADK 985/579, LangGraph 1,027/552 and optional
  Agent Framework 1,277/820, each with three gaps. Current source builds/types
  pass. Fresh configured acceptance and the final
  [finite audit](../s2-04-retry-timeout-acceptance.md) remain open. A further
  Windows descriptor success-path fixture timeout at `3fb1041` is corrected
  by test-only `b7d8674`; catalog checks pass 41 per principal/root and the
  complete local Windows root check passes 460/12. Fresh qualification remains
  required. Final `3fb1041` has five successful runs/two Windows failures.
  Native deadline follow-up `29eeb3b` explicitly asserts the original remaining
  budget (20/0 seconds); native ADK catalog/deadline passes 49/24 and LangGraph
  deadlines 8/24. All production bytes match the verified base. See
  [verified acceptance](../../PROJECT.md#s2-04-retry-timeout-and-exception-work--2026-10-12-verified).
  Final exact-source `6f8e95d` passes all seven configured runs and all thirteen
  finite requirements. PostgreSQL passes 5 provider, 11 timing, 8 deadline,
  5 retry-schedule and 3 actual process-stop cases per principal, plus atomic
  policy rollback; both Windows stages, main/PG image restarts, native benchmarks
  and all six zero-advisory reports pass. Earlier failures remain historical.
  S2-05 is next; 13 of 37 backlog tasks are verified.
