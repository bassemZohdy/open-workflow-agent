"""Fetch and verify the vendored OpenWorkflow schema baseline."""

from pathlib import Path
from urllib.request import urlopen

VERSION = "1.0.3"
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "openworkflow_adk" / "schema" / "vendor" / VERSION
BASE_URL = f"https://open-workflow-specification.org/schemas/{VERSION}/workflow"


def fetch(extension: str) -> None:
    target = TARGET / f"workflow.{extension}"
    with urlopen(f"{BASE_URL}.{extension}") as response:  # noqa: S310 - fixed official host
        target.write_bytes(response.read())
    print(f"wrote {target}")


if __name__ == "__main__":
    TARGET.mkdir(parents=True, exist_ok=True)
    fetch("yaml")
    fetch("json")
