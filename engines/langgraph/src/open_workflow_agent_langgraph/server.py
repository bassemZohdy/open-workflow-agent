from open_workflow_agent.api import create_app
from open_workflow_agent.config import RuntimeConfig

from . import LangGraphWorkflowEngine

app = create_app(config=RuntimeConfig.from_file(), engine=LangGraphWorkflowEngine())


def main() -> None:
    import uvicorn

    config = RuntimeConfig.from_file()
    uvicorn.run(app, host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()
