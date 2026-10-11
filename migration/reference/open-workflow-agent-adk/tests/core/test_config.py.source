import pytest

from openworkflow_adk.config import environment_config, resolve_agent_characteristics


def test_environment_config_supports_nested_values() -> None:
    config = environment_config(
        {
            "WORKFLOW_AGENT__MODEL": "env-model",
            "WORKFLOW_AGENT__GENERATE_CONTENT_CONFIG__temperature": "0.2",
        }
    )

    assert config == {
        "agent": {
            "model": "env-model",
            "generate_content_config": {"temperature": 0.2},
        }
    }


def test_precedence_is_environment_then_task_then_defaults() -> None:
    result = resolve_agent_characteristics(
        {"model": "task-model", "generate_content_config": {"top_p": 0.8}},
        {
            "model": "default-model",
            "instruction": "default",
            "generate_content_config": {"temperature": 0.1},
        },
        {
            "WORKFLOW_AGENT__MODEL": "env-model",
            "WORKFLOW_AGENT__GENERATE_CONTENT_CONFIG__temperature": "0.4",
        },
    )

    assert result.model == "env-model"
    assert result.instruction == "default"
    assert result.generate_content_config == {"temperature": 0.4, "top_p": 0.8}


@pytest.mark.parametrize(
    ("task", "defaults", "environ", "expected_model"),
    [
        (None, {"model": "default"}, {}, "default"),
        ({"model": "task"}, {"model": "default"}, {}, "task"),
        ({"model": "task"}, {"model": "default"}, {"WORKFLOW_AGENT__MODEL": "env"}, "env"),
        ({"model": "task"}, None, {"WORKFLOW_AGENT__MODEL": "env"}, "env"),
    ],
)
def test_each_configuration_layer_can_supply_the_model(
    task, defaults, environ, expected_model
) -> None:
    assert resolve_agent_characteristics(task, defaults, environ).model == expected_model


def test_named_model_reference_is_resolved_to_spec_model() -> None:
    result = resolve_agent_characteristics(
        {"model": {"use": "flash"}, "instruction": "task"},
        models={"flash": {"model": "gemini-2.5-flash"}},
    )
    assert result.model == "gemini-2.5-flash"


def test_environment_can_override_named_model_reference() -> None:
    result = resolve_agent_characteristics(
        {"model": {"use": "flash"}, "instruction": "task"},
        environ={"WORKFLOW_AGENT__MODEL": "env-model"},
        models={"flash": {"model": "gemini-2.5-flash"}},
    )
    assert result.model == "env-model"


def test_environment_can_redirect_named_model_reference() -> None:
    result = resolve_agent_characteristics(
        {"model": {"use": "flash"}, "instruction": "task"},
        environ={"WORKFLOW_AGENT__MODEL": '{"use": "pro"}'},
        models={
            "flash": {"model": "gemini-2.5-flash"},
            "pro": {"model": "gemini-2.5-pro"},
        },
    )
    assert result.model == "gemini-2.5-pro"


def test_document_defaults_supply_missing_agent_fields() -> None:
    result = resolve_agent_characteristics(
        {"instruction": "task"},
        defaults={"model": "default-model", "description": "default desc"},
    )
    assert result.model == "default-model"
    assert result.instruction == "task"
    assert result.description == "default desc"


def test_task_overrides_document_default_model() -> None:
    result = resolve_agent_characteristics(
        {"model": "task-model"},
        defaults={"model": "default-model"},
    )
    assert result.model == "task-model"


def test_environment_overrides_document_default_model() -> None:
    result = resolve_agent_characteristics(
        None,
        defaults={"model": "default-model"},
        environ={"WORKFLOW_AGENT__MODEL": "env-model"},
    )
    assert result.model == "env-model"
