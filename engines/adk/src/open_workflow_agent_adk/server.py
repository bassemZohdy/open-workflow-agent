from open_workflow_agent.api import create_app
from open_workflow_agent.config import RuntimeConfig

from . import AdkWorkflowEngine

app = create_app(config=RuntimeConfig.from_file(), engine=AdkWorkflowEngine())
