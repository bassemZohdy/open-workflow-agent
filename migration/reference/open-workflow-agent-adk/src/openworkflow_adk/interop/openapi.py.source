"""OpenAPI spec generation for a served OpenWorkflow document."""

from __future__ import annotations

from typing import Any

from openworkflow_adk.models import OpenWorkflowDocument


def generate_openapi(
    document: OpenWorkflowDocument, base_url: str = "http://localhost:8080"
) -> dict[str, Any]:
    """Return an OpenAPI 3.1.0 spec describing the workflow's HTTP surface.

    The spec covers the health, run, run/stream, and metrics endpoints exposed by
    :func:`openworkflow_adk.server.create_app`.
    """
    safe_name = "".join(char if char.isalnum() else "-" for char in document.document.name)
    title = f"owf-adk: {document.document.name}"
    return {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": document.document.version or "1.0.0",
            "description": f"OpenWorkflow '{document.document.name}' served as an ADK workflow.",
        },
        "servers": [{"url": base_url, "description": "Local workflow server"}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": f"{safe_name}-health",
                    "summary": "Health check",
                    "responses": {
                        "200": {
                            "description": "Server is healthy",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "workflow": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/metrics": {
                "get": {
                    "operationId": f"{safe_name}-metrics",
                    "summary": "Prometheus-compatible workflow metrics",
                    "responses": {
                        "200": {
                            "description": "Prometheus exposition format",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
            "/run": {
                "post": {
                    "operationId": f"{safe_name}-run",
                    "summary": "Run the workflow",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/RunRequest",
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Workflow completed",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/RunResponse",
                                    }
                                }
                            },
                        },
                        "500": {
                            "description": "Workflow execution failed",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ErrorResponse",
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/run/stream": {
                "post": {
                    "operationId": f"{safe_name}-run-stream",
                    "summary": "Run the workflow and stream events",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/RunRequest",
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Server-sent event stream",
                            "content": {
                                "text/event-stream": {
                                    "schema": {"type": "string"},
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "RunRequest": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "object", "default": {}},
                        "session_id": {"type": "string", "nullable": True},
                        "user_id": {"type": "string", "default": "workflow-user"},
                    },
                },
                "RunResponse": {
                    "type": "object",
                    "properties": {
                        "workflow": {"type": "string"},
                        "events": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Event"},
                        },
                    },
                },
                "Event": {
                    "type": "object",
                    "properties": {
                        "author": {"type": "string", "nullable": True},
                        "output": {},
                        "error": {"type": "string", "nullable": True},
                    },
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "detail": {"type": "string"},
                    },
                },
            }
        },
    }


def export_openapi(document: OpenWorkflowDocument, base_url: str = "http://localhost:8080") -> str:
    """Return the OpenAPI spec as a JSON string."""
    import json

    return json.dumps(generate_openapi(document, base_url=base_url), indent=2)
