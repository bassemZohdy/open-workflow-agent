from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_rbac_probe_accepts_expected_oc_denials() -> None:
    script = (ROOT / "tests" / "ci" / "openshift_sandbox_acceptance.sh").read_text(encoding="utf-8")

    assert "local actual_status=0" in script
    assert "|| actual_status=$?" in script
    assert 'if [[ "$actual_status" -ne 0 && "$actual" != "no" ]]; then' in script
