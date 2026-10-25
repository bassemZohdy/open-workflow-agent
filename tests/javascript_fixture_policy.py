"""Require the actual selected Node 24 interpreter for JavaScript acceptance."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def javascript_path() -> str:
    executable = shutil.which("node")
    if executable is None:
        if os.getenv("OWA_TEST_REQUIRED_JAVASCRIPT") == "1":
            pytest.fail("required Node 24 interpreter is unavailable")
        pytest.skip("Node 24 interpreter is not selected in this environment")
    version = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, timeout=10, check=True
    ).stdout.strip()
    if not version.startswith("v24."):
        if os.getenv("OWA_TEST_REQUIRED_JAVASCRIPT") == "1":
            pytest.fail("required Node 24 interpreter has a different major version")
        pytest.skip("this environment does not select the qualified Node 24 profile")
    return str(Path(executable).parent)
