"""Engine-neutral error contract."""

from __future__ import annotations

from typing import Any


class OwaError(Exception):
    """Base error exposed by the runtime boundary."""

    code = "owa_error"
    status_code = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ConfigurationError(OwaError):
    code = "configuration_error"
    status_code = 400


class WorkflowSchemaError(OwaError):
    code = "workflow_schema_error"
    status_code = 400


class WorkflowSemanticError(OwaError):
    code = "workflow_semantic_error"
    status_code = 400


class UnsupportedWorkflowFeature(OwaError):
    code = "unsupported_workflow_feature"
    status_code = 422


class ExpressionError(OwaError):
    code = "expression_error"
    status_code = 400


class ModelError(OwaError):
    code = "model_error"


class AgentError(OwaError):
    code = "agent_error"


class KnowledgeError(OwaError):
    code = "knowledge_error"


class MemoryError(OwaError):
    code = "memory_error"


class ToolError(OwaError):
    code = "tool_error"


class WorkflowExecutionError(OwaError):
    code = "workflow_execution_error"


class WorkflowDefinitionChanged(OwaError):
    code = "workflow_definition_changed"
    status_code = 409
