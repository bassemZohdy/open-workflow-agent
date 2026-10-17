"""Finite real OpenAPI descriptions shared by transport fixtures."""

from __future__ import annotations


def operation_document(
    server: str,
    *,
    operation_id: str = "lookup",
    path: str = "/openapi",
    parameters: tuple[tuple[str, str, str], ...] = (),
) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Deterministic protocol fixture", "version": "1.0.0"},
        "servers": [{"url": server}],
        "paths": {
            path: {
                "get": {
                    "operationId": operation_id,
                    "parameters": [
                        {
                            "name": name,
                            "in": location,
                            "required": location == "path",
                            "schema": {"type": kind},
                        }
                        for name, location, kind in parameters
                    ],
                    "responses": {"200": {"description": "Controlled result"}},
                }
            }
        },
    }
