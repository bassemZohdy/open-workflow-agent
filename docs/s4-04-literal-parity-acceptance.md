# S4-04 finite native outcome comparison candidate

The final frozen `5310f5a` CI run passes all 22 jobs, including required
three-runtime outcome comparison job `121177010650`. Its 13 actual native
outcomes have matched source, exact SDK/runtime versions and fixture/schema
bytes. Full S4-04 and all 443 proposed/774 schema criteria remain unverified.
The separate PostgreSQL run is partial because of the subsequently repaired
process fixture; a full green merged-source qualification is not claimed.

Current integration: see the [main checkpoint](validation-history/main-integration-2026-10-25.md)
for current results, the final hosted-fixture repair and remaining gates.
The full task stays open. Earlier counts and pending statements below are
retained development history, not additional current acceptance claims.

Status: authored; full S4-04 remains open. This candidate compares the 13 exact
shared literal success/fault fixtures across actual ADK, LangGraph Python and
LangGraph JS. It does not run all proposed tests, establish public identity or
durability parity, or promote any of the 443 proposed or 774 schema criteria.

Each independent native environment captures actual public outcome projections:
completed output or the common structured error. Captures retain the full
source commit, exact SDK version, runtime version, both fixture byte hashes and
official schema byte hash. Python capture checks SDK availability, restricts
fixture operations to the literal profile before execution and uses deterministic
models with zero model calls. JavaScript executes actual native graphs using
the same canonical fixture definitions. No private plan, native checkpoint,
business credential or model-provider call is introduced by the capture tool.

The comparison requires all three distinct native reports, one source commit,
the source-pinned SDK versions, Python 3.12/Node 24, identical fixture/schema
hashes and every expected success/fault case. Canonical finite JSON comparison
preserves type differences such as boolean versus integer. Missing, duplicate,
mixed, altered, private/extra-field and non-finite evidence rejects. Report files
are bounded to 4 MiB; errors do not reproduce untrusted artifact data. A failed
comparison leaves an existing output file unchanged.

The existing optional CI switch still controls execution. Native capture runs
inside each existing independent SDK job; JavaScript capture runs after its
package tests. A required aggregate job downloads the three reports from the
same CI run and retains a small comparison artifact with explicit false values
for full conformance, public identity and durability parity. Source metadata is
supplied by the checkout's trusted workflow context; the JSON reports are
diagnostics, not standalone authenticated attestations or execution authority.
Existing release gates continue to require overall CI success.

Synthetic validator/CLI tests cover the report boundary and do not prove a
native runtime. Actual capture and coherent hosted qualification remain pending.
The source research rows and coverage bridge remain unchanged and unverified.

The source-label guard now checks the actual Git checkout in both development
capture tools before fixture execution or output. Git runs with fixed arguments,
a filtered environment and a five-second bound; it is development evidence
collection, not a workflow execution path. An initial manually mislabelled local
Node report is retained as invalid evidence and will not be used as acceptance.
Python reads every shared source explicitly as UTF-8; the local Windows default
is cp1252, so locale-dependent decoding would change the Unicode fixture.
The boundary/CLI/Unicode selection passes 24 tests, and the Node package passes
49 tests including rejection of an unverified checkout without replacing an
existing report. Actual native captures remain required.

The first actual Windows capture cohort at
`50c257b94d6a1dba9ba32bc051c604141f16c63a` matches all 13 outcomes on ADK 2.7.1,
LangGraph Python 1.2.11 and LangGraph JS 1.4.21. Its raw files remain under
`tmp/javascript-sandbox-20261025/50c257b/`. That cohort used the same Windows
fixture bytes across all three producers; its raw hashes differ from the Git
blob bytes because of CRLF checkout conversion. The next candidate pins these
fixtures to LF on every platform, requires a clean checkout, and will regenerate
portable capture evidence. Expected ADK error-node tracebacks are quieted only
inside declared-fault development captures; outcome verification is unchanged.

The corrected portable Windows cohort at
`7d1a37b0b405a9a251049433ff8f918485db2911` independently captures and matches
all 13 outcomes on those three pinned SDKs. Fixture bytes match the Git blobs;
the reports and aggregate are retained under
`tmp/javascript-sandbox-20261025/7d1a37b/`. This qualifies the local literal
comparison only. Hosted Linux execution and the broader S4-04 criteria remain
pending.

## Case set grown to 64 (local, 2026-11-01)

The shared fixture set now has 64 cases (45 success, 19 fault) after adding literal
`try`/`catch`, `fork` and jq `set` expression coverage (see
[the S4-03 scope](s4-03-javascript-execution-acceptance.md#literal-trycatch-local-development-result)).
The CI aggregate test now expects 64 outcomes. The earlier hosted comparison covers
only the original 13; the 64-case comparison has not been run on hosted CI, and no
proposed-test or schema-constraint bridge row is promoted.
