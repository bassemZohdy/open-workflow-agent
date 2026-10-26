"""Original-principal state views, independent of portable lifecycle diagnostics."""

from native_stream_test_support import authority
from native_stream_test_support import configuration as stream_configuration
from native_stream_test_support import workflow as stream_workflow
from open_workflow_agent.config import NativeStateConfig
from open_workflow_agent.workflow import compile_workflow


def configuration(*, denied=False, **bounds):
    config = stream_configuration()
    config.execution_authorization.id = "native-state-tests"
    if not denied:
        config.execution_authorization.policy.rules[0].actions.append("state.inspect")
    config.observability.native_streams = None
    config.native_state = NativeStateConfig(**bounds)
    return config


def workflow(*, required=True, declared=True, waiting=False):
    source = stream_workflow(waiting=waiting).source
    source["document"]["name"] = "native-state-test"
    source["document"].pop("metadata", None)
    if declared:
        source["document"]["metadata"] = {
            "owa": {
                "nativeExtensions": [
                    {
                        "name": "langgraph.state.read",
                        "version": "1.0.0",
                        "required": required,
                    }
                ]
            }
        }
    return compile_workflow(source)


__all__ = ["authority", "configuration", "workflow"]
