from openworkflow_adk.schema import SCHEMA_VERSION, load_schema


def test_vendored_schema_loads() -> None:
    schema = load_schema()

    assert SCHEMA_VERSION == "1.0.3"
    assert isinstance(schema, dict)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
