# S4-04 value, bound and closed-object schema-constraint execution

Recorded 2026-11-01 on `main` at `760b139` plus the change that adds
`tests/contract/test_schema_value_constraints.py`. This is a local development run,
not hosted CI and not a conformance result.

<a id="schema-value-run"></a>

## Schema value run

Command (root environment, Python 3.12.11, jsonschema 4.26.0):

```
uv run --locked pytest tests/contract/test_schema_value_constraints.py tests/contract/test_schema_required_constraints.py tests/ci/test_consolidation_tracking.py
```

For each extracted row the test builds a copy of the enclosing subschema in the
packaged OWS 1.0.3 schema with that single keyword removed, and credits the row only
when a probe value is accepted by the copy and rejected by the real subschema. This
shows the keyword itself is the cause of the rejection. Probes: an added unknown
property for `unevaluatedProperties: false`; a non-member for `enum`; a different value
for `const`; one step beyond the limit for `minimum`, `maximum`, `minLength`,
`minItems`, `minProperties` and `maxProperties`. In-bounds values must also be accepted
where the probe defines them. The credit set is derived at collection time and checked
against the bridge, not asserted by hand.

| Keyword | Credited | Rows |
| --- | ---: | ---: |
| `additionalProperties` | 0 | 30 |
| `const` | 7 | 7 |
| `enum` | 15 | 15 |
| `maxProperties` | 0 | 3 |
| `maximum` | 1 | 1 |
| `minItems` | 0 | 1 |
| `minLength` | 2 | 2 |
| `minProperties` | 8 | 8 |
| `minimum` | 1 | 1 |
| `unevaluatedProperties` | 53 | 76 |

## Not claimed

- No row is `verified`; `executed` only means the named selector ran.
- Python's validator on the packaged schema only: not runtime admission, not the
  JavaScript validator, not any proposed multi-runtime test.
- `additionalProperties` credits nothing: none of its 30 rows is `false`, so the
  closed-object probe does not apply, and the open (`true`) and schema-valued forms
  need a different proof.
- The remaining uncredited rows could not be isolated or generated and stay `unmapped`
  with the reason recorded in the bridge.

## Uncredited rows

- `additionalProperties`: `SC0018`, `SC0020`, `SC0026`, `SC0029`, `SC0035`, `SC0038`, `SC0041`, `SC0046`, `SC0049`, `SC0066`, `SC0080`, `SC0140`, `SC0155`, `SC0160`, `SC0163`, `SC0180`, `SC0202`, `SC0220`, `SC0227`, `SC0235`, `SC0239`, `SC0258`, `SC0286`, `SC0363`, `SC0380`, `SC0390`, `SC0401`, `SC0413`, `SC0615`, `SC0636`
- `maxProperties`: `SC0034`, `SC0065`, `SC0412`
- `minItems`: `SC0409`
- `unevaluatedProperties`: `SC0081`, `SC0098`, `SC0118`, `SC0120`, `SC0144`, `SC0170`, `SC0188`, `SC0207`, `SC0250`, `SC0260`, `SC0271`, `SC0278`, `SC0290`, `SC0305`, `SC0318`, `SC0329`, `SC0395`, `SC0405`, `SC0422`, `SC0439`, `SC0486`, `SC0504`, `SC0632`
