FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY README.md ./

RUN useradd --create-home --uid 10001 workflow \
    && chown -R workflow:workflow /app
USER workflow

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["owf-adk", "--version"]

ENTRYPOINT ["owf-adk"]
CMD ["--help"]
