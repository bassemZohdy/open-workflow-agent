from openworkflow_adk import import_airflow, import_argo


def test_import_airflow_bash_tasks() -> None:
    document = import_airflow({"tasks": [{"task_id": "extract", "bash_command": "echo data"}]})
    assert document.do[0].name == "extract"
    assert document.do[0].task.run["shell"]["arguments"][-1] == "echo data"


def test_import_argo_container_templates() -> None:
    document = import_argo(
        {
            "spec": {
                "templates": [
                    {"name": "extract", "container": {"command": ["echo"], "args": ["data"]}}
                ]
            }
        }
    )
    assert document.do[0].name == "extract"
