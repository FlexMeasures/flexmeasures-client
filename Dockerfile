ARG UV_MAJOR_VERSION=0.10
ARG PYTHON_VERSION=3.12
ARG DEBIAN_VERSION=trixie

# Build the virtual environment using UV
FROM ghcr.io/astral-sh/uv:${UV_MAJOR_VERSION}-python${PYTHON_VERSION}-${DEBIAN_VERSION}-slim AS builder

# Redeclare ARG after FROM to make it available in this stage
ARG UV_COMPILE_BYTECODE=1

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV UV_COMPILE_BYTECODE=${UV_COMPILE_BYTECODE}
ENV UV_LINK_MODE=copy

# Install git for hatch-vcs version detection
RUN apt-get update && apt-get install -y --no-install-recommends \
    git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Sync dependencies without installing the project itself (creates .venv)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --all-extras --no-install-project

# Ensure subsequent commands use the virtual environment
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Copy application code (including .git for version detection)
COPY pyproject.toml uv.lock README.rst ./
COPY src/ ./src
COPY .git ./.git

# Install FlexMeasures itself in the virtual environment
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-extras

# Use a separate runtime image to run the code
FROM python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION} AS runtime

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Copy virtual environment from builder
COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}

# Copy application code
COPY --from=builder /app/src ./src

# Set environment variables to optimize Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1