import pytest

from openworkflow_adk import WorkflowValidationError, load


def test_loads_yaml_task_list_and_agent_extension() -> None:
    document = load(
        """
        document:
          dsl: 1.0.3
          namespace: demo
          name: hello
          version: 1.0.0
        do:
          - waitForIt:
              wait:
                seconds: 1
              metadata:
                adk:
                  agent:
                    model: gemini-2.5-flash
                    instruction: Say hello.
        """
    )

    assert document.do[0].name == "waitForIt"
    agent_config = document.do[0].task.effective_agent()
    assert agent_config is not None
    assert agent_config.model == "gemini-2.5-flash"


def test_invalid_document_has_structured_path() -> None:
    with pytest.raises(WorkflowValidationError) as raised:
        load({"document": {}, "do": []})

    assert raised.value.errors
    assert any("document" in error["path"] for error in raised.value.errors)


def test_invalid_agent_extension_has_task_path() -> None:
    with pytest.raises(WorkflowValidationError) as raised:
        load(
            {
                "document": {"dsl": "1.0.3", "namespace": "demo", "name": "x", "version": "1.0.0"},
                "do": [
                    {
                        "task": {
                            "wait": {"seconds": 1},
                            "metadata": {"adk": {"agent": {"unknown": True}}},
                        }
                    }
                ],
            }
        )

    assert any("metadata.adk.agent.unknown" in error["path"] for error in raised.value.errors)


def test_loader_accepts_additive_patch_dsl_version() -> None:
    document = load(
        {
            "document": {
                "dsl": "1.0.4",
                "namespace": "demo",
                "name": "patch-compatible",
                "version": "1.0.0",
            },
            "do": [{"finish": {"set": {"value": 1}}}],
        }
    )

    assert document.document.dsl == "1.0.4"


def test_legacy_agent_task_key_is_rejected_with_migration_hint() -> None:
    with pytest.raises(WorkflowValidationError) as raised:
        load(
            {
                "document": {"dsl": "1.0.3", "namespace": "demo", "name": "x", "version": "1.0.0"},
                "do": [{"task": {"wait": {"seconds": 1}, "agent": {"model": "x"}}}],
            }
        )

    assert any("legacy 'agent'" in error["message"] for error in raised.value.errors)


def test_legacy_use_models_registry_is_rejected_with_migration_hint() -> None:
    with pytest.raises(WorkflowValidationError) as raised:
        load(
            {
                "document": {"dsl": "1.0.3", "namespace": "demo", "name": "x", "version": "1.0.0"},
                "use": {"models": {"fast": {"model": "x"}}},
                "do": [{"finish": {"set": {"value": 1}}}],
            }
        )

    assert any("legacy 'use.models'" in error["message"] for error in raised.value.errors)


def test_non_dict_metadata_does_not_crash_validators() -> None:
    """C19.10: loader validators must not assume metadata is a dict."""
    with pytest.raises(WorkflowValidationError) as raised:
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "x",
                    "version": "1.0.0",
                    "metadata": "not-a-dict",
                },
                "do": [
                    {
                        "task": {
                            "wait": {"seconds": 1},
                            "metadata": ["also", "not", "a", "dict"],
                        }
                    }
                ],
            }
        )

    # The failure should come from upstream schema validation, not an AttributeError
    # in our custom reference validators.
    assert raised.value.errors


def test_sub_agent_model_reference_is_validated() -> None:
    """C19.22: loader validates model references on sub-agents recursively."""
    with pytest.raises(WorkflowValidationError) as raised:
        load(
            {
                "document": {
                    "dsl": "1.0.3",
                    "namespace": "demo",
                    "name": "sub",
                    "version": "1.0.0",
                    "metadata": {
                        "adk": {
                            "models": {"flash": {"model": "gemini-2.5-flash"}},
                        }
                    },
                },
                "do": [
                    {
                        "parent": {
                            "wait": {"seconds": 1},
                            "metadata": {
                                "adk": {
                                    "agent": {
                                        "model": "gemini-2.5-flash",
                                        "instruction": "hi",
                                        "sub_agents": [
                                            {
                                                "model": {"use": "missing-model"},
                                                "instruction": "sub",
                                            }
                                        ],
                                    }
                                }
                            },
                        }
                    }
                ],
            }
        )

    assert any(
        "missing-model" in error["message"] and "sub_agents" in error["path"]
        for error in raised.value.errors
    )


def test_sub_agent_model_reference_resolves() -> None:
    """C19.22: valid sub-agent model references load without error."""
    document = load(
        {
            "document": {
                "dsl": "1.0.3",
                "namespace": "demo",
                "name": "sub",
                "version": "1.0.0",
                "metadata": {
                    "adk": {
                        "models": {"flash": {"model": "gemini-2.5-flash"}},
                    }
                },
            },
            "do": [
                {
                    "parent": {
                        "wait": {"seconds": 1},
                        "metadata": {
                            "adk": {
                                "agent": {
                                    "model": {"use": "flash"},
                                    "instruction": "hi",
                                    "sub_agents": [
                                        {
                                            "model": {"use": "flash"},
                                            "instruction": "sub",
                                        }
                                    ],
                                }
                            }
                        },
                    }
                }
            ],
        }
    )

    agent = document.do[0].task.effective_agent()
    assert agent is not None
    assert agent.sub_agents[0].model.use == "flash"
