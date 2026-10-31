# S4-04 schema-constraint mapping worklist

Updated 2026-11-01. This is planning input for S4-04, not evidence or a
conformance claim. The table below describes the original extraction of 774 rows in
[the coverage bridge](../resources/compatibility/coverage-bridge.json). Since it was
written, 628 rows are executed in isolation with Python only (67 `required`, see
[its run record](validation-history/s4-04-schema-required-constraints.md); 561 closed-object,
additionalProperties, default, enum, const, bound, type, pattern, composition and `$ref` rows, see
[the value run record](validation-history/s4-04-schema-value-constraints.md)); the
other 146 remain `unmapped` and none is verified.

The composition (`oneOf`, `anyOf`, `allOf`, `not`) and `$ref` rows executed so
far prove only that the keyword is operative; the fuller per-branch proofs in the
table below remain open.

## Composition of the 774 extracted constraints

| Keyword | Rows | Kind of proof needed |
| --- | ---: | --- |
| `type` | 294 | Wrong-type value in the enclosing property is rejected. |
| `$ref` | 139 | Structural indirection; credit only through the constraints it reaches. |
| `required` | 89 | Omit each required property in its operation context and expect rejection. |
| `unevaluatedProperties` | 76 | Unknown property in the enclosing definition is rejected. |
| `oneOf` | 38 | Zero and multiple matching branches are rejected, each branch accepted. |
| `additionalProperties` | 30 | Unknown property is rejected where declared closed. |
| `allOf` | 20 | Every conjunct is enforced. |
| `default` | 18 | Annotation only; verify defaults are applied or deliberately not applied. |
| `enum` | 15 | Out-of-set value rejected, every member accepted. |
| `pattern`, `minProperties`, `format`, `const`, `anyOf`, `maxProperties`, `then`, `if`, `not`, `minLength`, `$id`, `$schema`, `minimum`, `maximum`, `minItems`, `else` | 55 | Boundary and negative case per row. |

Largest enclosing definitions: `callTask` 164, `runTask` 66, root 61,
`authenticationPolicy` 50, `oauth2AuthenticationProperties` 26, `retryPolicy` 26,
`eventProperties` 19, `error` 18, `switchTask` 17, `tryTask` 17, `forTask` 15,
`eventConsumptionStrategy` 15 (48 definitions in total).

## Suggested order

1. **Closed-object and required rows** (`required`, `unevaluatedProperties`,
   `additionalProperties`, `minProperties`, `maxProperties`: about 206 rows).
   Generate one negative fixture per row from the official schema in its exact
   enclosing operation, so each row has an independent selector.
2. **Value-domain rows** (`enum`, `const`, `pattern`, `minimum`, `maximum`,
   `minLength`, `minItems`: about 36 rows) with a boundary pair each.
3. **Composition rows** (`oneOf`, `anyOf`, `allOf`, `if`/`then`/`else`, `not`:
   about 71 rows), which need a case per branch.
4. **`type` rows last**; many are implied by the above and need context-specific
   wrong-type values rather than a generic one.
5. `$ref`, `default`, `$id`, `$schema` and `format` rows need decisions on what
   counts as evidence before any selector is credited.

## Rules for promoting a row

- Use an existing selector in the bridge's required form; the integrity check
  rejects skipped, `skipif` or `xfail` functions.
- The fixture must exercise the constraint in its enclosing operation or
  property, not a generic schema-validation test.
- Retain run evidence before moving a row beyond `mapped_not_executed`.
- Python and JavaScript/TypeScript validation differ today; record the
  format-validation difference instead of hiding it.
- Do not modify the official schema.
