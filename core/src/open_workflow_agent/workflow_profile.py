"""Reviewed OWS provenance; no runtime lookup or floating schema selection."""

from typing import Any

WORKFLOW_DSL = "1.0.3"
WORKFLOW_PROFILE = "OWA Portable Profile v1"
SCHEMA_REVISION = "2dd2c84170d5f3e05d58e913e9ca298dcf8d543a"
SCHEMA_SHA256 = "704ef5e91c5d823167dd8751794edb1dd1a6f9a3bdf9bfd389bf9c6b23ae3816"
RELEASE_REVISION = "9b5b1da29e9d4fff2358580241e11aab22704a16"


def workflow_profile() -> dict[str, Any]:
    return {
        "dsl": WORKFLOW_DSL,
        "releaseStatus": "prerelease",
        "releaseRevision": RELEASE_REVISION,
        "schemaRevision": SCHEMA_REVISION,
        "schemaSha256": SCHEMA_SHA256,
        "semanticProfile": WORKFLOW_PROFILE,
        "fullConformance": False,
    }
