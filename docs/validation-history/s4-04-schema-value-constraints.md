# S4-04 value, bound, type, composition and closed-object schema-constraint execution

Recorded 2026-11-01 on `main` at `b7b76da` plus the change that extends
`tests/contract/test_schema_value_constraints.py` with `pattern`, composition and
`$ref` rows. This is a local development run, not hosted CI and not a conformance
result.

<a id="schema-value-run"></a>

## Schema value run

Command (root environment, Python 3.12.11, jsonschema 4.26.0):

```
uv run --locked pytest tests/contract/test_schema_value_constraints.py tests/contract/test_schema_required_constraints.py tests/ci/test_consolidation_tracking.py
```

For each extracted row the test builds a copy of the enclosing subschema in the
packaged OWS 1.0.3 schema with that single keyword removed, and credits the row only
when a probe value is accepted by the copy and rejected by the real subschema. This
shows the keyword is operative: it alone causes the rejection. Probes: an added unknown
property for `unevaluatedProperties: false`; a non-member for `enum`; a different value
for `const`; a value of another JSON type for `type`; a non-matching string for
`pattern`; one step beyond the limit for `minimum`, `maximum`, `minLength`, `minItems`,
`minProperties` and `maxProperties`; and, for `oneOf`, `anyOf`, `allOf`, `not` and
`$ref`, scalars, an unknown object and single-property edits of a generated valid
instance. In-bounds values must also be accepted where the probe defines them. The
credit set is derived at collection time and checked against the bridge, not asserted
by hand.

| Keyword | Credited | Rows |
| --- | ---: | ---: |
| `$ref` | 139 | 139 |
| `additionalProperties` | 0 | 30 |
| `allOf` | 2 | 20 |
| `anyOf` | 4 | 5 |
| `const` | 7 | 7 |
| `enum` | 15 | 15 |
| `maxProperties` | 0 | 3 |
| `maximum` | 1 | 1 |
| `minItems` | 0 | 1 |
| `minLength` | 2 | 2 |
| `minProperties` | 8 | 8 |
| `minimum` | 1 | 1 |
| `not` | 1 | 2 |
| `oneOf` | 38 | 38 |
| `pattern` | 9 | 9 |
| `type` | 237 | 294 |
| `unevaluatedProperties` | 53 | 76 |

## Not claimed

- No row is `verified`; `executed` only means the named selector ran.
- Python's validator on the packaged schema only: not runtime admission, not the
  JavaScript validator, not any proposed multi-runtime test.
- Composition rows (`oneOf`, `anyOf`, `allOf`, `not`) show the combinator is operative.
  They do not show that every branch is accepted or that `oneOf` branches are mutually
  exclusive.
- `$ref` rows show the reference is operative (its target rejects a probe the
  unreferenced subschema accepts). The target's own constraints are separate rows.
- `additionalProperties` credits nothing: none of its 30 rows is `false`, so the
  closed-object probe does not apply, and the open (`true`) and schema-valued forms
  need a different proof.
- Four `type` rows are properties named `type` and two have extracted pointers that do
  not resolve in the schema tree; they are extraction artifacts, not type constraints.
- The remaining uncredited rows could not be isolated or generated and stay `unmapped`
  with the reason recorded in the bridge.

## Uncredited rows

- `additionalProperties`: `SC0018`, `SC0020`, `SC0026`, `SC0029`, `SC0035`, `SC0038`, `SC0041`, `SC0046`, `SC0049`, `SC0066`, `SC0080`, `SC0140`, `SC0155`, `SC0160`, `SC0163`, `SC0180`, `SC0202`, `SC0220`, `SC0227`, `SC0235`, `SC0239`, `SC0258`, `SC0286`, `SC0363`, `SC0380`, `SC0390`, `SC0401`, `SC0413`, `SC0615`, `SC0636`
- `allOf`: `SC0099`, `SC0122`, `SC0146`, `SC0172`, `SC0190`, `SC0209`, `SC0252`, `SC0262`, `SC0273`, `SC0279`, `SC0291`, `SC0306`, `SC0319`, `SC0330`, `SC0396`, `SC0406`, `SC0423`, `SC0440`
- `anyOf`: `SC0613`
- `maxProperties`: `SC0034`, `SC0065`, `SC0412`
- `minItems`: `SC0409`
- `not`: `SC0255`
- `type`: `SC0096`, `SC0101`, `SC0103`, `SC0108`, `SC0119`, `SC0124`, `SC0143`, `SC0148`, `SC0164`, `SC0169`, `SC0174`, `SC0182`, `SC0187`, `SC0192`, `SC0197`, `SC0206`, `SC0211`, `SC0216`, `SC0223`, `SC0246`, `SC0249`, `SC0259`, `SC0270`, `SC0276`, `SC0288`, `SC0297`, `SC0303`, `SC0311`, `SC0316`, `SC0327`, `SC0332`, `SC0336`, `SC0352`, `SC0356`, `SC0393`, `SC0403`, `SC0420`, `SC0437`, `SC0444`, `SC0460`, `SC0485`, `SC0489`, `SC0503`, `SC0512`, `SC0519`, `SC0523`, `SC0539`, `SC0556`, `SC0574`, `SC0579`, `SC0602`, `SC0616`, `SC0644`, `SC0678`, `SC0699`, `SC0720`, `SC0756`
- `unevaluatedProperties`: `SC0081`, `SC0098`, `SC0118`, `SC0120`, `SC0144`, `SC0170`, `SC0188`, `SC0207`, `SC0250`, `SC0260`, `SC0271`, `SC0278`, `SC0290`, `SC0305`, `SC0318`, `SC0329`, `SC0395`, `SC0405`, `SC0422`, `SC0439`, `SC0486`, `SC0504`, `SC0632`
