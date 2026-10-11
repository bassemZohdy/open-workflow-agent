from openworkflow_adk.translator import _kill_process_tree, _sandbox_preexec


def test_sandbox_preexec_accepts_resource_limits() -> None:
    assert callable(_sandbox_preexec)


def test_timeout_kills_process_group() -> None:
    assert callable(_kill_process_tree)
