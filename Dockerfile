FROM python:3.12-slim

# Install git (needed for editable installs and git_status integration tool)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only the package manifests and source first — layer cache:
# if pyproject.toml and praxis/ are unchanged, pip install is skipped.
COPY pyproject.toml ./
COPY praxis/ ./praxis/

RUN pip install --no-cache-dir ".[all]"

# Copy the rest of the repo (README, demo/, wiki/, systemd/, etc.)
COPY . .

# Workspace is mounted at runtime — separate from the immutable app code.
ENV PRAXIS_WORKSPACE_ROOT=/workspace
ENV PRAXIS_MEMORY_ROOT=/workspace/.praxis/memory

# /workspace is mutable user state (tasks, wiki, staging, memory).
# Mount a named Docker volume or host bind-mount here.
VOLUME /workspace

# MCP Gateway port
EXPOSE 8765

# Default: start the MCP HTTP/SSE gateway.
# Override with e.g. ["python", "-m", "praxis", "--queue"] for the queue processor.
CMD ["python", "-m", "praxis", "--mcp"]
