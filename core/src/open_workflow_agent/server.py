"""ASGI entry point for local and container execution."""

from .api import build_app_from_environment
from .config import RuntimeConfig

app = build_app_from_environment()


def main() -> None:
    import uvicorn

    config = RuntimeConfig.from_file()
    uvicorn.run(app, host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()
