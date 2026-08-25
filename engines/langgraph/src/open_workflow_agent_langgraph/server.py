from open_workflow_agent.api import create_app
from open_workflow_agent.config import RuntimeConfig

from . import LangGraphWorkflowEngine

app = create_app(config=RuntimeConfig.from_file(), engine=LangGraphWorkflowEngine())
