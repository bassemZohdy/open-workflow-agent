from open_workflow_agent.api import create_app
from open_workflow_agent.config import RuntimeConfig

from . import AdkWorkflowEngine

app = create_app(config=RuntimeConfig.from_file(), engine=AdkWorkflowEngine())


def main() -> None:
    import uvicorn

    config = RuntimeConfig.from_file()
    uvicorn.run(app, host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()
