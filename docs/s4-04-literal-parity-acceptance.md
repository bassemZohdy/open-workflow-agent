# S4-04 finite native outcome comparison candidate

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
