"""Keep canonical source/image delivery separate from predecessor installs."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_distribution_identity_and_no_shim_decision_match_real_package_metadata():
    policy = json.loads((ROOT / "resources/python-distribution-policy.json").read_text())
    canonical = policy["canonical_adk"]
    project = ROOT / canonical["project"]
    metadata = tomllib.loads((project / "pyproject.toml").read_text())
    assert metadata["project"]["name"] == canonical["distribution"]
    packages = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == ["src/" + canonical["import"]]
    assert not canonical["compatibility_shims"]
    assert not (project / "src" / policy["predecessor"]["import"]).exists()
    scripts = metadata["project"].get("scripts", {})
    assert not set(scripts) & set(policy["predecessor"]["commands"])
    predecessor = ROOT / policy["predecessor"]["reference"] / "pyproject.toml.source"
    archived = tomllib.loads(predecessor.read_text())
    assert archived["project"]["name"] == canonical["distribution"]
    assert set(policy["predecessor"]["commands"]) <= set(archived["project"]["scripts"])
    root_metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert root_metadata["project"]["scripts"]["owa"] == "open_workflow_agent.cli:main"


def test_current_delivery_policy_has_no_package_index_publisher():
    policy = json.loads((ROOT / "resources/python-distribution-policy.json").read_text())
    assert policy["package_index_publication"] == "disabled"
    assert policy["delivery"] == ["pinned_source_checkout", "qualified_oci_images"]
    assert policy["consumer_migration"] == "separate_environment_and_explicit_workflow_port"
    publishers = re.compile(r"(?:uv\s+publish|twine\s+upload|pypa/gh-action-pypi-publish)", re.I)
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        assert not publishers.search(workflow.read_text()), workflow.name
