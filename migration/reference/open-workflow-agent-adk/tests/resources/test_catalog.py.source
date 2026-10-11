from pathlib import Path

import pytest
import respx
from httpx import Response

from openworkflow_adk import load, run_workflow
from openworkflow_adk.resources.catalog import CatalogFunctionRegistry


def _document(endpoint: str | Path, call: str = "greet:1.0.0@shared"):
    endpoint_value = endpoint.as_uri() if isinstance(endpoint, Path) else endpoint
    return load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "catalog",
                "version": "1.0.0",
            },
            "use": {"catalogs": {"shared": {"endpoint": endpoint_value}}},
            "do": [{"invoke": {"call": call}}],
        }
    )


@pytest.mark.asyncio
async def test_catalog_function_is_loaded_from_spec_directory(tmp_path: Path) -> None:
    function = tmp_path / "functions" / "greet" / "1.0.0" / "function.yaml"
    function.parent.mkdir(parents=True)
    function.write_text("set:\n  greeting: '\"hello\"'\n")

    events = await run_workflow(_document(tmp_path), catalog_base_dir=str(tmp_path))

    assert any(
        event.actions and event.actions.state_delta.get("greeting") == "hello" for event in events
    )


@pytest.mark.asyncio
@respx.mock
async def test_catalog_github_endpoint_is_resolved_to_raw_resource() -> None:
    route = respx.get(
        "https://raw.githubusercontent.com/example/catalog/refs/heads/main/"
        "functions/greet/1.0.0/function.yaml"
    ).mock(return_value=Response(200, text="set:\n  greeting: '\"hello\"'\n"))

    events = await run_workflow(_document("https://github.com/example/catalog/tree/main"))

    assert route.called
    assert any(
        event.actions and event.actions.state_delta.get("greeting") == "hello" for event in events
    )


@pytest.mark.asyncio
async def test_catalog_reference_requires_a_declared_catalog(tmp_path: Path) -> None:
    document = _document(tmp_path, call="greet:1.0.0@missing")

    with pytest.raises(KeyError, match="catalog 'missing'"):
        await run_workflow(document)


@pytest.mark.asyncio
async def test_catalog_registry_caches_validated_task(tmp_path: Path) -> None:
    function = tmp_path / "functions" / "greet" / "1.0.0" / "function.yaml"
    function.parent.mkdir(parents=True)
    function.write_text("set:\n  greeting: '\"hello\"'\n")
    registry = CatalogFunctionRegistry()
    document = _document(tmp_path)

    first = await registry.load(document, "greet", "1.0.0", "shared", base_dir=tmp_path)
    second = await registry.load(document, "greet", "1.0.0", "shared", base_dir=tmp_path)

    assert first == second
    assert first["set"] == {"greeting": '"hello"'}
