"""Deterministic original-principal native streaming configuration."""

from open_workflow_agent.config import RuntimeConfig
from open_workflow_agent.execution_authority import ExecutionAuthority
from open_workflow_agent.security import static_principal
from open_workflow_agent.workflow import compile_workflow, generate_default_workflow


def configuration(*, denied=False, **bounds):
    actions = ["invocations.create", "invocations.resume", "catalog.call", "model.complete"]
    if not denied:
        actions.append("invocations.stream")
    return RuntimeConfig.model_validate(
        {
            "server": {"api_security_profile": "actor-alice"},
            "security": {
                "profiles": {
                    "actor-" + actor: {
                        "type": "bearer",
                        "principal": actor,
                        "roles": ["observer"],
                        "scopes": ["observe"],
                        "audience": ["runtime"],
                        "token": {"from_env": "OWA_NATIVE_STREAM_TEST_" + actor.upper()},
                    }
                    for actor in ("alice", "bob")
                }
            },
            "execution_authorization": {
                "enabled": True,
                "id": "native-stream-tests",
                "version": "1",
                "policy": {
                    "rules": [
                        {
                            "actions": actions,
                            "resources": ["*"],
                            "roles": ["observer"],
                            "scopes": ["observe"],
                            "audience": ["runtime"],
                        }
                    ]
                },
            },
            "observability": {"native_streams": bounds},
        }
    )


def authority(services, actor):
    profile = "actor-" + actor
    return ExecutionAuthority.capture(
        static_principal(services.config.security.profile(profile)), security_profile=profile
    )


def workflow(*, required=True, declared=True, waiting=False):
    definition = generate_default_workflow()
    definition["document"]["name"] = "native-stream-test"
    if declared:
        definition["document"]["metadata"] = {
            "owa": {
                "nativeExtensions": [
                    {
                        "name": "langgraph.streams",
                        "version": "1.0.0",
                        "required": required,
                    }
                ]
            }
        }
    definition["do"] = [
        {"first": {"set": {"value": "${ .value }"}}},
        {"answer": {"call": "llm:1.0.0@default"}},
        *([{"pause": {"wait": {"seconds": 300}}}] if waiting else []),
        {"last": {"set": {"done": True}}},
    ]
    return compile_workflow(definition)
