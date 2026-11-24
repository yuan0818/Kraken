FROM docker.io/library/python:3.12.15-slim
RUN pip install --no-cache-dir uv==0.13.4
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/node-agent apps/node-agent
COPY apps/control-plane/pyproject.toml apps/control-plane/pyproject.toml
COPY packages packages
RUN uv sync --frozen --package platform-node-agent --no-dev
RUN useradd --uid 10001 --create-home platform && mkdir /state && chown platform:platform /state
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 NODE_STATE_DIR=/state
USER platform
WORKDIR /state
CMD ["python", "-m", "node_agent.transport.client"]
