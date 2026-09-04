"""ASGI entry point for local and container execution."""

from .api import build_app_from_environment
from .config import RuntimeConfig
from .logging_config import configure_logging

app = build_app_from_environment()


def main() -> None:
    import uvicorn

    config = RuntimeConfig.from_file()

    # Configure logging based on observability settings
    configure_logging(
        log_level=config.observability.log_level,
        structured=config.observability.structured_logging,
    )

    uvicorn.run(app, host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()
