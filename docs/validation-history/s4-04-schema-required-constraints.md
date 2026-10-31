# S4-04 `required` schema-constraint execution

Recorded 2026-11-01 on `main` at `bbff175` plus the change that adds
`tests/contract/test_schema_required_constraints.py`. This is a local development
run; it is not hosted CI and not a conformance result.

<a id="schema-required-run"></a>

## Schema required run

Command (root environment, Python 3.12.11, jsonschema 4.26.0):

```
uv run --locked pytest tests/contract/test_schema_required_constraints.py tests/ci/test_consolidation_tracking.py
```

For each of the 89 extracted `required` rows the test resolves the enclosing
subschema of the packaged OWS 1.0.3 schema, generates a valid instance, and checks
that deleting every listed property makes the instance invalid under Python's
Draft 2020-12 validator. A row is credited only if both the valid instance and every
single-property rejection are demonstrated, so the credit set is derived rather than
asserted.

Result: 67 rows credited (`executed` in
[the coverage bridge](../../resources/compatibility/coverage-bridge.json)); 22 rows
could not be credited because no valid instance of the enclosing subschema could be
generated, and they stay `unmapped` with that reason recorded. No row was found
where a listed property could be removed without rejection.

## Not claimed

- No row is `verified`; `executed` only means the named selector ran.
- This does not exercise runtime admission, the JavaScript validator or any proposed
  multi-runtime test, and a generated instance is not a realistic workflow.
- The other 707 schema rows and all 443 proposed tests remain unmapped.

## Uncredited rows

- `SC0097` `/$defs/callTask/oneOf/0/required`
- `SC0121` `/$defs/callTask/oneOf/1/required`
- `SC0145` `/$defs/callTask/oneOf/2/required`
- `SC0171` `/$defs/callTask/oneOf/3/required`
- `SC0189` `/$defs/callTask/oneOf/4/required`
- `SC0208` `/$defs/callTask/oneOf/5/required`
- `SC0248` `/$defs/callTask/oneOf/5/allOf/1/properties/with/required`
- `SC0251` `/$defs/callTask/oneOf/6/required`
- `SC0261` `/$defs/forkTask/required`
- `SC0272` `/$defs/doTask/required`
- `SC0277` `/$defs/emitTask/required`
- `SC0285` `/$defs/emitTask/allOf/1/properties/emit/properties/event/properties/with/required`
- `SC0289` `/$defs/forTask/required`
- `SC0304` `/$defs/listenTask/required`
- `SC0317` `/$defs/raiseTask/required`
- `SC0328` `/$defs/runTask/required`
- `SC0394` `/$defs/setTask/required`
- `SC0404` `/$defs/switchTask/required`
- `SC0421` `/$defs/tryTask/required`
- `SC0438` `/$defs/waitTask/required`
- `SC0630` `/$defs/eventConsumptionStrategy/oneOf/2/required`
- `SC0641` `/$defs/eventFilter/required`
