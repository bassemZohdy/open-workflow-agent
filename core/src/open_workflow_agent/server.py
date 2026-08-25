"""ASGI entry point for local and container execution."""

from .api import build_app_from_environment

app = build_app_from_environment()
